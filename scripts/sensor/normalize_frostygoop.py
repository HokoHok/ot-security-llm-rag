"""Normalize safe FrostyGoop report-derived Modbus Lab evidence for RAG.

The normalizer reads only pre-existing task JSON files and PCAP captures.  It
never executes a Modbus client, connects to a device, or opens the samples
directory.  FC06 and FC16 are explicitly labelled as safe capability demos,
not observed FrostyGoop behavior.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
from typing import Any, Callable, Sequence


NORMALIZER_VERSION = "frostygoop-normalizer-0.1.0"
SCENARIO = "frostygoop"
DEFAULT_PORT = 1502
WATCH_START = 53760
WATCH_END = 53882


@dataclass(frozen=True)
class CaseDefinition:
    """One safe Lab capture and its expected task configuration."""

    case_id: str
    classification: str
    task_filename: str
    pcap_filename: str
    expected_function_code: int
    safety_role: str


CASES: tuple[CaseDefinition, ...] = (
    CaseDefinition(
        case_id="frostygoop_fc03_read_001",
        classification="report_derived_fc03_read_baseline",
        task_filename="task_test_reconstructed_v1.json",
        pcap_filename="frostygoop_fc03_reconstructed.pcapng",
        expected_function_code=3,
        safety_role="report-derived read-only baseline",
    ),
    CaseDefinition(
        case_id="frostygoop_fc06_write_001",
        classification="safe_fc06_single_register_write",
        task_filename="task_test_fc06_lab.json",
        pcap_filename="frostygoop_fc06_lab.pcapng",
        expected_function_code=6,
        safety_role="safe Lab capability demonstration",
    ),
    CaseDefinition(
        case_id="frostygoop_fc16_write_001",
        classification="safe_fc16_multiple_register_write",
        task_filename="task_test_fc16_lab.json",
        pcap_filename="frostygoop_fc16_lab.pcapng",
        expected_function_code=16,
        safety_role="safe Lab capability demonstration",
    ),
)

TsharkRunner = Callable[[Path, int], str]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"Required task JSON does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: "
            f"{exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON value must be an object")
    return value


def _required_int(value: Any, field_name: str, path: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path}: {field_name} must be an integer")
    return value


def _task_from_json(path: Path) -> dict[str, Any]:
    document = _read_json(path)
    tasks = document.get("Tasks")
    if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
        raise ValueError(f"{path}: Tasks must contain exactly one task object")
    task = tasks[0]
    code = _required_int(task.get("Code"), "Tasks[0].Code", path)
    address = _required_int(task.get("Address"), "Tasks[0].Address", path)
    if code == 3:
        count = _required_int(task.get("Count"), "Tasks[0].Count", path)
        if count < 1:
            raise ValueError(f"{path}: Tasks[0].Count must be positive")
        return {"function_code": code, "address": address, "count": count}
    if code == 6:
        return {
            "function_code": code,
            "address": address,
            "value": _required_int(task.get("Value"), "Tasks[0].Value", path),
        }
    if code == 16:
        count = _required_int(task.get("Count"), "Tasks[0].Count", path)
        values = task.get("Value")
        if (
            count < 1
            or not isinstance(values, list)
            or len(values) != count
            or any(isinstance(value, bool) or not isinstance(value, int) for value in values)
        ):
            raise ValueError(
                f"{path}: FC16 requires Count matching a non-empty integer Value list"
            )
        return {
            "function_code": code,
            "address": address,
            "count": count,
            "values": values,
        }
    raise ValueError(f"{path}: unsupported Modbus function code {code}")


def _default_tshark_runner(pcap_path: Path, port: int) -> str:
    """Return only client-to-server TCP payload fields from an existing PCAP."""

    tshark = shutil.which("tshark")
    if tshark is None:
        raise ValueError(
            "tshark was not found. Run this normalizer on the FrostyGoop Lab VM "
            "or provide a system PATH containing tshark."
        )
    command = [
        tshark,
        "-n",
        "-r",
        str(pcap_path),
        "-Y",
        f"tcp.dstport=={port} && tcp.len>0",
        "-T",
        "fields",
        "-E",
        "separator=\t",
        "-e",
        "frame.number",
        "-e",
        "ip.src",
        "-e",
        "ip.dst",
        "-e",
        "tcp.srcport",
        "-e",
        "tcp.dstport",
        "-e",
        "tcp.payload",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or "tshark did not return a readable result"
        raise ValueError(f"Could not parse {pcap_path} with tshark: {message}") from exc
    return completed.stdout


def _parse_payload(payload_hex: str) -> dict[str, Any] | None:
    try:
        payload = bytes.fromhex(payload_hex)
    except ValueError:
        return None
    if len(payload) < 12:
        return None
    transaction_id, protocol_id, _length = struct.unpack(
        ">HHH", payload[:6]
    )
    if protocol_id != 0:
        return None
    unit_id = payload[6]
    function_code = payload[7]
    address = struct.unpack(">H", payload[8:10])[0]
    observation: dict[str, Any] = {
        "transaction_id": transaction_id,
        "unit_id": unit_id,
        "function_code": function_code,
        "address": address,
    }
    if function_code in {3, 6}:
        observation["count"] = struct.unpack(">H", payload[10:12])[0]
        if function_code == 6:
            observation["value"] = observation.pop("count")
    elif function_code == 16:
        if len(payload) < 13:
            return None
        count = struct.unpack(">H", payload[10:12])[0]
        byte_count = payload[12]
        values_raw = payload[13 : 13 + byte_count]
        if len(values_raw) != byte_count or byte_count != count * 2:
            return None
        observation["count"] = count
        observation["values"] = [
            struct.unpack(">H", values_raw[offset : offset + 2])[0]
            for offset in range(0, byte_count, 2)
        ]
    else:
        return None
    return observation


def _parse_tshark_output(output: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 6 or not fields[-1].strip():
            continue
        parsed = _parse_payload(fields[-1].strip())
        if parsed is None:
            continue
        try:
            parsed["frame_number"] = int(fields[0])
            parsed["source_ip"] = fields[1].strip()
            parsed["destination_ip"] = fields[2].strip()
            parsed["source_port"] = int(fields[3])
            parsed["destination_port"] = int(fields[4])
        except ValueError:
            continue
        observations.append(parsed)
    return observations


def _task_matches_observation(task: dict[str, Any], observation: dict[str, Any]) -> bool:
    if task["function_code"] != observation["function_code"]:
        return False
    if task["address"] != observation["address"]:
        return False
    function_code = task["function_code"]
    if function_code == 3:
        return task["count"] == observation.get("count")
    if function_code == 6:
        return task["value"] == observation.get("value")
    return task["count"] == observation.get("count") and task["values"] == observation.get("values")


def _behavior(function_code: int, address: int, count: int) -> dict[str, Any]:
    end_address = address + count - 1
    range_hit = address <= WATCH_END and end_address >= WATCH_START
    if function_code == 3:
        return {
            "operation": "read_holding_registers",
            "behavior": "READ",
            "risk": "LOW",
            "detector_result": "NOTICE" if range_hit else "OK",
            "message": (
                "Read activity overlaps the Lab watched register range."
                if range_hit
                else "No watched-range overlap."
            ),
        }
    if function_code == 6:
        return {
            "operation": "write_single_register",
            "behavior": "WRITE",
            "risk": "MEDIUM",
            "detector_result": "ALERT" if range_hit else "OK",
            "message": (
                "Safe single-register write overlaps the Lab watched register range."
                if range_hit
                else "No watched-range overlap."
            ),
        }
    return {
        "operation": "write_multiple_registers",
        "behavior": "WRITE",
        "risk": "HIGH",
        "detector_result": "ALERT" if range_hit else "OK",
        "message": (
            "Safe multiple-register write overlaps the Lab watched register range."
            if range_hit
            else "No watched-range overlap."
        ),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: Sequence[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def normalize_frostygoop(
    input_root: Path,
    output_root: Path,
    *,
    port: int = DEFAULT_PORT,
    tshark_runner: TsharkRunner = _default_tshark_runner,
) -> dict[str, Any]:
    """Create stable normalized evidence from existing safe FrostyGoop Lab files."""

    if port < 1 or port > 65535:
        raise ValueError("port must be between 1 and 65535")
    input_root = input_root.resolve()
    output_root = output_root.resolve()
    json_root = input_root / "json-analysis"
    traffic_root = input_root / "traffic"
    generated_at = datetime.now(timezone.utc).isoformat()
    case_entries: list[dict[str, Any]] = []

    for definition in CASES:
        task_path = json_root / definition.task_filename
        pcap_path = traffic_root / definition.pcap_filename
        if not pcap_path.is_file():
            raise ValueError(f"Required PCAP does not exist: {pcap_path}")
        task = _task_from_json(task_path)
        if task["function_code"] != definition.expected_function_code:
            raise ValueError(
                f"{task_path}: expected FC{definition.expected_function_code}, "
                f"found FC{task['function_code']}"
            )
        observations = _parse_tshark_output(tshark_runner(pcap_path, port))
        matching = [
            observation
            for observation in observations
            if _task_matches_observation(task, observation)
        ]
        follow_up_observations = [
            observation
            for observation in observations
            if observation not in matching
        ]
        warnings: list[str] = [
            "Report-derived safe reconstruction; no real FrostyGoop sample, PLC, RTU, or physical process is involved.",
            f"Modbus port {port} is a localhost Lab port, not production TCP/502.",
            "PCAP proves the captured Lab request, not device acceptance or physical impact.",
        ]
        if definition.expected_function_code in {6, 16}:
            warnings.append(
                "FC06/FC16 are safe Lab capability demonstrations and are not claimed as observed real FrostyGoop behavior."
            )
        if not observations:
            warnings.append("No supported client-to-server Modbus request was decoded from the PCAP.")
        elif not matching:
            warnings.append("Decoded PCAP request does not match the corresponding task JSON.")
        if follow_up_observations:
            warnings.append(
                "PCAP contains additional supported Modbus requests; they are retained as follow-up observations rather than discarded."
            )

        primary_observation = matching[0] if matching else (observations[0] if observations else None)
        count = int(task.get("count", 1))
        behavior = _behavior(task["function_code"], task["address"], count)
        files = {
            "task_json": {
                "path": str(task_path),
                "sha256": _sha256(task_path),
                "size": task_path.stat().st_size,
            },
            "pcap": {
                "path": str(pcap_path),
                "sha256": _sha256(pcap_path),
                "size": pcap_path.stat().st_size,
            },
        }
        summary = {
            "schema_version": "1.0",
            "normalizer_version": NORMALIZER_VERSION,
            "scenario": SCENARIO,
            "case_id": definition.case_id,
            "classification": definition.classification,
            "safety_role": definition.safety_role,
            "generated_at": generated_at,
            "lab": {
                "transport": "Modbus TCP",
                "lab_endpoint": "127.0.0.1",
                "lab_port": port,
                "watch_range": {"start": WATCH_START, "end": WATCH_END},
            },
            "task": task,
            "pcap_observation": primary_observation,
            "pcap_follow_up_observations": follow_up_observations,
            "task_pcap_match": bool(matching),
            "behavior_detection": behavior,
            "source_files": files,
            "data_quality": {"status": "warning", "warnings": warnings},
        }
        timeline_events: list[dict[str, Any]] = [
            {
                "sequence": 1,
                "event_type": "task_configuration_loaded",
                "detail": task,
                "source": definition.task_filename,
            },
            {
                "sequence": 2,
                "event_type": "pcap_request_decoded",
                "detail": primary_observation,
                "source": definition.pcap_filename,
            },
        ]
        for ordinal, observation in enumerate(follow_up_observations, start=3):
            timeline_events.append(
                {
                    "sequence": ordinal,
                    "event_type": "pcap_follow_up_request_decoded",
                    "detail": observation,
                    "source": definition.pcap_filename,
                }
            )
        timeline_events.append(
            {
                "sequence": len(timeline_events) + 1,
                "event_type": "behavior_detector_result",
                "detail": behavior,
                "source": "deterministic_normalizer",
            }
        )
        timeline = {
            "schema_version": "1.0",
            "normalizer_version": NORMALIZER_VERSION,
            "scenario": SCENARIO,
            "case_id": definition.case_id,
            "events": timeline_events,
        }
        data_quality = {
            "schema_version": "1.0",
            "normalizer_version": NORMALIZER_VERSION,
            "scenario": SCENARIO,
            "case_id": definition.case_id,
            "status": "warning",
            "warnings": warnings,
            "decoded_request_count": len(observations),
            "matching_request_count": len(matching),
        }
        records = [
            {
                "case_id": definition.case_id,
                "chunk_type": "case_summary",
                "text": (
                    f"FrostyGoop report-derived safe reconstruction; {definition.safety_role}. "
                    f"The Lab decoded Modbus FC{task['function_code']} on localhost TCP/{port}."
                ),
                "metadata": {"classification": definition.classification},
            },
            {
                "case_id": definition.case_id,
                "chunk_type": "task_pcap_correlation",
                "text": (
                    f"Task JSON requests FC{task['function_code']} at register {task['address']}; "
                    f"the decoded PCAP match is {bool(matching)}. "
                    f"Additional decoded request count is {len(follow_up_observations)}. "
                    f"The capture is a localhost Lab request and does not prove device acceptance."
                ),
                "metadata": {
                    "task": task,
                    "pcap_observation": primary_observation,
                    "pcap_follow_up_observations": follow_up_observations,
                },
            },
            {
                "case_id": definition.case_id,
                "chunk_type": "behavior_detection",
                "text": (
                    f"Behavior detector classified FC{task['function_code']} as {behavior['behavior']} "
                    f"with Lab risk {behavior['risk']} and result {behavior['detector_result']}. "
                    f"{behavior['message']}"
                ),
                "metadata": behavior,
            },
            {
                "case_id": definition.case_id,
                "chunk_type": "data_quality",
                "text": " | ".join(warnings),
                "metadata": {"warnings": warnings},
            },
        ]
        if follow_up_observations:
            records.insert(
                2,
                {
                    "case_id": definition.case_id,
                    "chunk_type": "pcap_follow_up",
                    "text": (
                        f"The PCAP contains {len(follow_up_observations)} additional supported "
                        "Modbus request(s) after the task-correlated request. These are retained "
                        "as Lab follow-up observations and do not prove device acceptance."
                    ),
                    "metadata": {"observations": follow_up_observations},
                },
            )
        case_dir = output_root / definition.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        _write_json(case_dir / "incident_summary.json", summary)
        _write_json(case_dir / "timeline.json", timeline)
        _write_json(case_dir / "data_quality.json", data_quality)
        _write_jsonl(case_dir / "rag_chunks.jsonl", records)
        case_entries.append(
            {
                "case_id": definition.case_id,
                "classification": definition.classification,
                "safety_role": definition.safety_role,
                "decoded_request_count": len(observations),
                "matching_request_count": len(matching),
            }
        )

    index = {
        "schema_version": "1.0",
        "normalizer_version": NORMALIZER_VERSION,
        "scenario": SCENARIO,
        "generated_at": generated_at,
        "cases": case_entries,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    _write_json(output_root / "case_index.json", index)
    return index


def main(argv: Sequence[str] | None = None) -> None:
    """Run the normalizer against an existing safe FrostyGoop Lab directory."""

    parser = argparse.ArgumentParser(
        description="Normalize safe FrostyGoop report-derived Lab evidence"
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path.home() / "frostygoop-analysis",
        help="Existing Lab directory containing json-analysis/ and traffic/",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path.home() / "frostygoop-analysis" / "normalized",
        help="Directory where normalized JSON and JSONL files are written",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Safe local Modbus Lab TCP port (default: 1502)",
    )
    args = parser.parse_args(argv)
    result = normalize_frostygoop(
        args.input_root,
        args.output_root,
        port=args.port,
    )
    print(f"[OK] Normalizer: {result['normalizer_version']}")
    print(f"[OK] Cases: {len(result['cases'])}")
    print(f"[OK] Output: {args.output_root.resolve()}")


if __name__ == "__main__":
    main()
