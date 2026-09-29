from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMALIZER_PATH = PROJECT_ROOT / "scripts" / "sensor" / "normalize_frostygoop.py"


def _load_normalizer() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "frostygoop_normalizer",
        NORMALIZER_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_input(root: Path) -> None:
    json_root = root / "json-analysis"
    traffic_root = root / "traffic"
    json_root.mkdir(parents=True)
    traffic_root.mkdir()
    payloads = {
        "task_test_reconstructed_v1.json": {
            "Iplist": ["127.0.0.1"],
            "Tasks": [{"Code": 3, "Address": 53760, "Count": 123}],
        },
        "task_test_fc06_lab.json": {
            "Iplist": ["127.0.0.1"],
            "Tasks": [{"Code": 6, "Address": 53760, "Value": 2222}],
        },
        "task_test_fc16_lab.json": {
            "Iplist": ["127.0.0.1"],
            "Tasks": [
                {
                    "Code": 16,
                    "Address": 53760,
                    "Count": 3,
                    "Value": [3000, 3001, 3002],
                }
            ],
        },
    }
    for filename, payload in payloads.items():
        (json_root / filename).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
    for filename in (
        "frostygoop_fc03_reconstructed.pcapng",
        "frostygoop_fc06_lab.pcapng",
        "frostygoop_fc16_lab.pcapng",
    ):
        (traffic_root / filename).write_bytes(b"safe-pcap-fixture")


def _tshark_output(payload_hex: str) -> str:
    return f"1\t127.0.0.1\t127.0.0.1\t40000\t1502\t{payload_hex}\n"


def test_normalizer_writes_three_safe_cases_and_rag_records(tmp_path: Path) -> None:
    normalizer = _load_normalizer()
    input_root = tmp_path / "input"
    output_root = tmp_path / "normalized"
    _write_input(input_root)
    outputs = {
        "frostygoop_fc03_reconstructed.pcapng": _tshark_output(
            "0001000000060103d200007b"
        ),
        "frostygoop_fc06_lab.pcapng": _tshark_output(
            "0001000000060106d20008ae"
        ),
        "frostygoop_fc16_lab.pcapng": _tshark_output(
            "00010000000d0110d2000003060bb80bb90bba"
        ),
    }

    index = normalizer.normalize_frostygoop(
        input_root,
        output_root,
        tshark_runner=lambda pcap_path, _port: outputs[pcap_path.name],
    )

    assert index["normalizer_version"] == "frostygoop-normalizer-0.1.0"
    assert [entry["case_id"] for entry in index["cases"]] == [
        "frostygoop_fc03_read_001",
        "frostygoop_fc06_write_001",
        "frostygoop_fc16_write_001",
    ]
    summary = json.loads(
        (output_root / "frostygoop_fc16_write_001" / "incident_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["task_pcap_match"] is True
    assert summary["pcap_observation"]["values"] == [3000, 3001, 3002]
    assert summary["behavior_detection"] == {
        "operation": "write_multiple_registers",
        "behavior": "WRITE",
        "risk": "HIGH",
        "detector_result": "ALERT",
        "message": "Safe multiple-register write overlaps the Lab watched register range.",
    }
    assert any("not claimed as observed real FrostyGoop behavior" in warning for warning in summary["data_quality"]["warnings"])
    records = [
        json.loads(line)
        for line in (output_root / "frostygoop_fc06_write_001" / "rag_chunks.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert [record["chunk_type"] for record in records] == [
        "case_summary",
        "task_pcap_correlation",
        "behavior_detection",
        "data_quality",
    ]


def test_normalizer_marks_task_pcap_mismatch_without_inventing_success(
    tmp_path: Path,
) -> None:
    normalizer = _load_normalizer()
    input_root = tmp_path / "input"
    output_root = tmp_path / "normalized"
    _write_input(input_root)
    fc03_only = _tshark_output("0001000000060103d200007b")

    normalizer.normalize_frostygoop(
        input_root,
        output_root,
        tshark_runner=lambda _pcap_path, _port: fc03_only,
    )

    quality = json.loads(
        (output_root / "frostygoop_fc06_write_001" / "data_quality.json").read_text(
            encoding="utf-8"
        )
    )
    assert quality["matching_request_count"] == 0
    assert "Decoded PCAP request does not match the corresponding task JSON." in quality["warnings"]


def test_normalizer_retains_safe_follow_up_read_after_write(tmp_path: Path) -> None:
    normalizer = _load_normalizer()
    input_root = tmp_path / "input"
    output_root = tmp_path / "normalized"
    _write_input(input_root)
    outputs = {
        "frostygoop_fc03_reconstructed.pcapng": _tshark_output(
            "0001000000060103d200007b"
        ),
        "frostygoop_fc06_lab.pcapng": (
            _tshark_output("0001000000060106d20008ae")
            + "2\t127.0.0.1\t127.0.0.1\t40000\t1502\t"
            "0002000000060103d2000001\n"
        ),
        "frostygoop_fc16_lab.pcapng": _tshark_output(
            "00010000000d0110d2000003060bb80bb90bba"
        ),
    }

    normalizer.normalize_frostygoop(
        input_root,
        output_root,
        tshark_runner=lambda pcap_path, _port: outputs[pcap_path.name],
    )

    summary = json.loads(
        (output_root / "frostygoop_fc06_write_001" / "incident_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["task_pcap_match"] is True
    assert summary["pcap_follow_up_observations"][0]["function_code"] == 3
    records = [
        json.loads(line)
        for line in (output_root / "frostygoop_fc06_write_001" / "rag_chunks.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert "pcap_follow_up" in [record["chunk_type"] for record in records]
