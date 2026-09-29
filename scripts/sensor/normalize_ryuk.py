from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any


EXPECTED_RAW_FILES = (
    "collection_metadata.json",
    "kali_http_access.log",
    "detection_stage2_user_v4.json",
    "stage4_impact_summary.json",
    "stage4_user_execution_impact_log.txt",
    "stage4_discovery_log.txt",
    "stage4_credential_exposure_simulation_log.txt",
    "stage4_scheduled_task_deployment_simulation_log.txt",
    "stage4_service_recovery_dryrun_log.txt",
    "restore_version_d_result.json",
)
EXPECTED_USER01_FILES = (
    "user01_execution_context.json",
    "user01_manifest.json",
    "user01_powershell_events.json",
    "user01_powershell_summary.json",
    "user01_process_collection_status.json",
    "user01_process_events.json",
    "user01_process_summary.json",
    "user01_smb_state.json",
)


def load_json(
    raw_dir: Path,
    name: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Load one raw JSON object and report missing or invalid evidence."""

    path = raw_dir / name
    if not path.exists():
        warnings.append(f"Missing raw JSON: {name}")
        return {}
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"Invalid raw JSON {name}: {exc}")
        return {}
    if not isinstance(value, dict):
        warnings.append(f"Raw JSON root must be an object: {name}")
        return {}
    return value


def load_text(raw_dir: Path, name: str, warnings: list[str]) -> str:
    """Load one raw text file and report read errors."""

    path = raw_dir / name
    if not path.exists():
        warnings.append(f"Missing raw text: {name}")
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        warnings.append(f"Could not read raw text {name}: {exc}")
        return ""


def load_json_array(
    raw_dir: Path,
    name: str,
    warnings: list[str],
) -> list[Any]:
    """Load one raw JSON array and report missing or invalid evidence."""

    path = raw_dir / name
    if not path.exists():
        warnings.append(f"Missing raw JSON: {name}")
        return []
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"Invalid raw JSON {name}: {exc}")
        return []
    if not isinstance(value, list):
        warnings.append(f"Raw JSON root must be an array: {name}")
        return []
    return value


def verify_manifest(
    evidence_dir: Path,
    manifest: list[Any],
    warnings: list[str],
) -> dict[str, Any]:
    """Verify copied USER01 files against their exported SHA-256 manifest."""

    checked = 0
    matched = 0
    for item in manifest:
        if not isinstance(item, dict):
            warnings.append("USER01 manifest contains a non-object record.")
            continue
        name = item.get("file")
        expected_hash = item.get("sha256")
        if not isinstance(name, str) or not isinstance(expected_hash, str):
            warnings.append("USER01 manifest record is missing file or sha256.")
            continue
        path = evidence_dir / name
        checked += 1
        if not path.is_file():
            warnings.append(f"USER01 manifest file is missing: {name}")
            continue
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash.casefold() != expected_hash.casefold():
            warnings.append(f"USER01 manifest hash mismatch: {name}")
            continue
        matched += 1

    return {
        "records": len(manifest),
        "files_checked": checked,
        "hashes_matched": matched,
        "verified": bool(manifest) and checked == matched,
    }


def verify_kali_fetch(
    raw_dir: Path,
    metadata: dict[str, Any],
    warnings: list[str],
) -> tuple[bool, dict[str, Any]]:
    """Verify collection from explicit curl metadata and the saved file."""

    path = raw_dir / "kali_http_access.log"
    file_exists = path.is_file()
    actual_bytes = path.stat().st_size if file_exists else 0
    fetch = metadata.get("kali_http_log_fetch")

    if not isinstance(fetch, dict):
        warnings.append(
            "Collection metadata has no kali_http_log_fetch result; "
            "the current-run Kali log cannot be verified."
        )
        return False, {
            "declared_success": None,
            "verified_success": False,
            "curl_exit_code": None,
            "http_status": None,
            "reported_bytes": None,
            "actual_bytes": actual_bytes,
            "file_exists": file_exists,
        }

    declared_success = fetch.get("success") is True
    curl_exit_code = fetch.get("curl_exit_code")
    http_status = str(fetch.get("http_status", ""))
    reported_bytes = fetch.get("bytes")
    nonempty = file_exists and actual_bytes > 0
    verified_success = (
        declared_success
        and curl_exit_code == 0
        and http_status == "200"
        and nonempty
    )

    if declared_success and not verified_success:
        warnings.append(
            "Collector declared Kali HTTP fetch success, but curl metadata "
            "or the saved file did not verify it."
        )
    if not declared_success:
        warnings.append(
            "Collector did not report a successful Kali HTTP log fetch "
            "for this collection run."
        )
        if file_exists:
            warnings.append(
                "A Kali HTTP log exists, but current collection metadata "
                "does not confirm that it belongs to this collection run."
            )
    if isinstance(reported_bytes, int) and reported_bytes != actual_bytes:
        warnings.append(
            "Kali HTTP log byte count differs from collection metadata: "
            f"reported={reported_bytes}, actual={actual_bytes}."
        )

    return verified_success, {
        "declared_success": declared_success,
        "verified_success": verified_success,
        "curl_exit_code": curl_exit_code,
        "http_status": http_status or None,
        "reported_bytes": reported_bytes,
        "actual_bytes": actual_bytes,
        "file_exists": file_exists,
        "attempted_at": fetch.get("attempted_at"),
    }


def build_summary(base: Path) -> dict[str, Any]:
    """Normalize the current Ryuk collection into one evidence-aware summary."""

    raw = base / "raw"
    warnings: list[str] = []

    metadata = load_json(raw, "collection_metadata.json", warnings)
    detection = load_json(raw, "detection_stage2_user_v4.json", warnings)
    impact = load_json(raw, "stage4_impact_summary.json", warnings)
    restore = load_json(raw, "restore_version_d_result.json", warnings)
    if metadata.get("dc01_share_available") is False:
        warnings.append(
            "DC01 evidence source was unavailable during this collection run."
        )

    sensor_fetched, kali_collection = verify_kali_fetch(
        raw,
        metadata,
        warnings,
    )
    kali_log = (
        load_text(raw, "kali_http_access.log", warnings)
        if sensor_fetched
        else ""
    )

    page_hits = len(
        re.findall(
            r'\S+ .*"GET / HTTP/1\.1" (?:200|304)',
            kali_log,
        )
    )
    payload_hits = len(
        re.findall(
            (
                r'\S+ .*"GET /invoice_update'
                r'(?:_v[0-9]+)?\.ps1 HTTP/1\.1" 200'
            ),
            kali_log,
        )
    )

    raw_files_present = {
        name: (raw / name).is_file() for name in EXPECTED_RAW_FILES
    }

    user01_collection = metadata.get("user01_evidence")
    if not isinstance(user01_collection, dict):
        user01_collection = {}
        warnings.append(
            "Collection metadata has no USER01 endpoint evidence result."
        )
    user01_run_id = str(user01_collection.get("run_id", "")).strip()
    user01_dir = raw / "user01" / user01_run_id
    user01_files_present = {
        name: bool(user01_run_id and (user01_dir / name).is_file())
        for name in EXPECTED_USER01_FILES
    }
    if user01_collection.get("success") is not True:
        warnings.append(
            "Collector did not report a complete USER01 evidence run."
        )

    user01_context: dict[str, Any] = {}
    powershell_summary: dict[str, Any] = {}
    process_status: dict[str, Any] = {}
    process_summary: dict[str, Any] = {}
    smb_state: dict[str, Any] = {}
    manifest: list[Any] = []
    if user01_run_id:
        user01_context = load_json(
            user01_dir,
            "user01_execution_context.json",
            warnings,
        )
        powershell_summary = load_json(
            user01_dir,
            "user01_powershell_summary.json",
            warnings,
        )
        process_status = load_json(
            user01_dir,
            "user01_process_collection_status.json",
            warnings,
        )
        process_summary = load_json(
            user01_dir,
            "user01_process_summary.json",
            warnings,
        )
        smb_state = load_json(
            user01_dir,
            "user01_smb_state.json",
            warnings,
        )
        manifest = load_json_array(
            user01_dir,
            "user01_manifest.json",
            warnings,
        )

    manifest_verification = verify_manifest(
        user01_dir,
        manifest,
        warnings,
    )
    powershell_markers = powershell_summary.get("markers")
    if not isinstance(powershell_markers, dict):
        powershell_markers = {}
    powershell_verified = (
        powershell_summary.get("source_host") == "USER01"
        and powershell_summary.get("event_4104_count", 0) > 0
        and powershell_markers.get("payload_name_found") is True
        and powershell_markers.get("dc01_share_found") is True
        and powershell_markers.get("impact_folder_found") is True
    )
    process_verified = (
        process_status.get("success") is True
        and process_summary.get("source_host") == "USER01"
        and process_summary.get("event_count", 0) > 0
        and process_summary.get("powershell_found") is True
        and process_summary.get("payload_name_found") is True
        and process_summary.get("employee01_found") is True
        and process_summary.get("command_line_available") is True
    )
    context_verified = (
        user01_context.get("run_id") == user01_run_id
        and user01_context.get("source_host") == "USER01"
        and str(user01_context.get("source_user", "")).casefold()
        == "ryuk\\employee01"
    )
    if user01_run_id and not context_verified:
        warnings.append("USER01 execution context did not verify.")
    if user01_run_id and not powershell_verified:
        warnings.append("USER01 PowerShell summary did not verify.")
    if user01_run_id and not process_verified:
        warnings.append("USER01 Security 4688 summary did not verify.")

    smb_connections = smb_state.get("connections", [])
    smb_mappings = smb_state.get("mappings", [])
    if not isinstance(smb_connections, list):
        smb_connections = []
    if not isinstance(smb_mappings, list):
        smb_mappings = []
    detection_file_checks = {
        "discovery_log_found": "stage4_discovery_log.txt",
        "credential_exposure_simulation_log_found":
            "stage4_credential_exposure_simulation_log.txt",
        "scheduled_task_deployment_dryrun_log_found":
            "stage4_scheduled_task_deployment_simulation_log.txt",
        "service_recovery_dryrun_log_found":
            "stage4_service_recovery_dryrun_log.txt",
        "stage4_impact_summary_found": "stage4_impact_summary.json",
        "stage4_user_execution_log_found":
            "stage4_user_execution_impact_log.txt",
    }
    for field, filename in detection_file_checks.items():
        declared = detection.get(field)
        present = raw_files_present[filename]
        if isinstance(declared, bool) and declared != present:
            warnings.append(
                f"Detection field {field}={declared} conflicts with "
                f"collected file presence for {filename}={present}."
            )

    summary: dict[str, Any] = {
        "scenario": "Ryuk-like safe incident simulation",
        "normalization_schema_version": "1.1",
        "summary_type": "AI incident summary",
        "generated_by": "SENSOR01",
        "generated_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "source_hosts": {
            "delivery_server": "Kali",
            "execution_endpoint": "USER01",
            "detector_and_file_server": "DC01",
            "collector": "SENSOR01",
        },
        "target_asset": detection.get(
            "target_path",
            "C:\\RyukShare\\attack_from_user_v4",
        ),
        "user_context": detection.get(
            "expected_user",
            impact.get("user", "RYUK\\employee01"),
        ),
        "risk": detection.get("risk", "Unknown"),
        "initial_access_evidence": {
            "kali_download_page_hits_from_user01": page_hits,
            "payload_download_hits_from_user01": payload_hits,
            "sensor_fetched_kali_http_log": sensor_fetched,
        },
        "user01_endpoint_evidence": {
            "run_id": user01_run_id or None,
            "collector_declared_success":
                user01_collection.get("success") is True,
            "source_host": user01_context.get("source_host"),
            "source_user": user01_context.get("source_user"),
            "payload_sha256": user01_context.get("payload_sha256"),
            "context_verified": context_verified,
            "powershell": {
                "verified": powershell_verified,
                "total_events": powershell_summary.get("total_events"),
                "event_4103_count":
                    powershell_summary.get("event_4103_count"),
                "event_4104_count":
                    powershell_summary.get("event_4104_count"),
                "markers": powershell_markers,
            },
            "process_creation": {
                "verified": process_verified,
                "event_id": process_summary.get("event_id"),
                "event_count": process_summary.get("event_count"),
                "powershell_found":
                    process_summary.get("powershell_found"),
                "payload_name_found":
                    process_summary.get("payload_name_found"),
                "employee01_found":
                    process_summary.get("employee01_found"),
                "command_line_available":
                    process_summary.get("command_line_available"),
            },
            "smb_current_state": {
                "connection_count": len(smb_connections),
                "mapping_count": len(smb_mappings),
                "historical_access_proven": False,
            },
            "manifest": manifest_verification,
            "execution_corroborated": (
                context_verified
                and powershell_verified
                and process_verified
                and manifest_verification["verified"]
            ),
            "limitations": [
                (
                    "SMB state was queried after execution and contained no "
                    "active connection or mapping; this does not disprove "
                    "earlier USER01-to-DC01 SMB access."
                ),
                (
                    "Historical SMB access still requires DC01 server audit "
                    "or network telemetry."
                ),
            ],
        },
        "impact_evidence": {
            "ryk_file_count": detection.get("ryk_file_count"),
            "ryuk_readme_count": detection.get("ryuk_readme_count"),
            "caesar_marker_file_count": detection.get(
                "caesar_marker_file_count"
            ),
            "affected_file_type_count": detection.get(
                "affected_file_type_count"
            ),
        },
        "kill_chain_evidence": {
            field: detection.get(field) for field in detection_file_checks
        },
        "attack_stages": [
            "Initial Access",
            "User Execution",
            "Discovery",
            "Credential Exposure Assumption",
            "Valid Account Abuse",
            "Ryuk-like Impact",
            "Detection",
            "Evidence Collection",
        ],
        "detection_reasons": [
            str(reason).replace(
                "Version D",
                "the Ryuk-like safe simulation",
            )
            for reason in detection.get("reasons", [])
        ],
        "recommended_actions": [
            "Isolate USER01 from the lab network",
            "Review Kali HTTP access log for download evidence",
            "Review DC01 detection JSON and affected RyukShare folder",
            "Check the RYUK\\employee01 account activity",
            (
                "Restore affected test files using the validated safe "
                "restore procedure if recovery demo is required"
            ),
        ],
        "restore_status": {
            "restore_result_found": bool(restore),
            "restored_file_count": restore.get("restored_file_count"),
            "failed_file_count": restore.get("failed_file_count"),
        },
        "collection_evidence": {
            "collection_time": metadata.get("collection_time"),
            "dc01_share_available": metadata.get("dc01_share_available"),
            "kali_http_log": kali_collection,
            "raw_files_present": raw_files_present,
            "user01_run_id": user01_run_id or None,
            "user01_files_present": user01_files_present,
        },
        "data_quality": {
            "status": "ok" if not warnings else "warning",
            "warnings": warnings,
        },
        "safety_note": (
            "Safe Ryuk-like lab emulation only. No real Ryuk malware, "
            "real credential theft, persistence, service stop, or "
            "destructive recovery deletion was used."
        ),
    }
    return summary


def write_outputs(base: Path, summary: dict[str, Any]) -> tuple[Path, Path]:
    """Write normalized JSON and a human-readable Markdown brief."""

    normalized = base / "normalized"
    normalized.mkdir(parents=True, exist_ok=True)
    out_json = normalized / "ryuk_incident_summary.json"
    out_md = normalized / "ryuk_ai_brief.md"

    with out_json.open("w", encoding="utf-8") as file:
        json.dump(summary, file, indent=2, ensure_ascii=False)

    with out_md.open("w", encoding="utf-8") as file:
        file.write("# Ryuk-like Safe Incident Brief\n\n")
        file.write(f"- Scenario: {summary['scenario']}\n")
        file.write(f"- Risk: {summary['risk']}\n")
        file.write(f"- User Context: {summary['user_context']}\n")
        file.write(f"- Target Asset: {summary['target_asset']}\n\n")

        file.write("## Initial Access Evidence\n")
        for key, value in summary["initial_access_evidence"].items():
            file.write(f"- {key}: {value}\n")

        file.write("\n## USER01 Endpoint Evidence\n")
        user01 = summary["user01_endpoint_evidence"]
        file.write(f"- run_id: {user01['run_id']}\n")
        file.write(
            "- execution_corroborated: "
            f"{user01['execution_corroborated']}\n"
        )
        file.write(
            "- powershell_4104_count: "
            f"{user01['powershell']['event_4104_count']}\n"
        )
        file.write(
            "- process_4688_count: "
            f"{user01['process_creation']['event_count']}\n"
        )
        file.write(
            "- smb_historical_access_proven: "
            f"{user01['smb_current_state']['historical_access_proven']}\n"
        )

        file.write("\n## Impact Evidence\n")
        for key, value in summary["impact_evidence"].items():
            file.write(f"- {key}: {value}\n")

        file.write("\n## Kill Chain Evidence\n")
        for key, value in summary["kill_chain_evidence"].items():
            file.write(f"- {key}: {value}\n")

        file.write("\n## Detection Reasons\n")
        for reason in summary["detection_reasons"]:
            file.write(f"- {reason}\n")

        file.write("\n## Recommended Actions\n")
        for action in summary["recommended_actions"]:
            file.write(f"- {action}\n")

        file.write("\n## Collection Evidence\n")
        kali = summary["collection_evidence"]["kali_http_log"]
        file.write(
            f"- kali_http_log_verified: {kali['verified_success']}\n"
        )
        file.write(f"- kali_http_status: {kali['http_status']}\n")
        file.write(f"- kali_http_log_bytes: {kali['actual_bytes']}\n")

        file.write("\n## Data Quality\n")
        file.write(f"- status: {summary['data_quality']['status']}\n")
        warnings = summary["data_quality"]["warnings"]
        if warnings:
            for warning in warnings:
                file.write(f"- warning: {warning}\n")
        else:
            file.write("- warnings: None\n")

        file.write("\n## Safety Note\n")
        file.write(summary["safety_note"] + "\n")

    return out_json, out_md


def main() -> None:
    base = Path(
        os.environ.get(
            "RYUK_BASE_DIR",
            str(Path.home() / "incident-data" / "ryuk"),
        )
    )
    summary = build_summary(base)
    out_json, out_md = write_outputs(base, summary)
    print(f"[OK] Wrote {out_json}")
    print(f"[OK] Wrote {out_md}")
    for warning in summary["data_quality"]["warnings"]:
        print(f"[WARN] {warning}")


if __name__ == "__main__":
    main()
