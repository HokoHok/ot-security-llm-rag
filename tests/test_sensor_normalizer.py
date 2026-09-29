import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RYUK_DIR = PROJECT_ROOT / "scenarios" / "ryuk"
NORMALIZER_PATH = PROJECT_ROOT / "scripts" / "sensor" / "normalize_ryuk.py"


def load_normalizer() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "sensor_normalizer",
        NORMALIZER_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_imported_summary() -> dict[str, object]:
    path = RYUK_DIR / "input" / "normalized" / "ryuk_incident_summary.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_imported_user01_endpoint_summary_is_verified() -> None:
    summary = load_imported_summary()
    endpoint = summary["user01_endpoint_evidence"]

    assert endpoint["execution_corroborated"] is True
    assert endpoint["powershell"]["event_4104_count"] == 6
    assert endpoint["process_creation"]["event_count"] == 7
    assert endpoint["manifest"]["verified"] is True
    assert summary["data_quality"] == {"status": "ok", "warnings": []}


def test_sensor_manifest_verification_detects_hash_mismatch(
    tmp_path: Path,
) -> None:
    normalizer = load_normalizer()
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text('{"safe": true}', encoding="utf-8")
    manifest = [
        {
            "file": evidence_file.name,
            "sha256": hashlib.sha256(evidence_file.read_bytes()).hexdigest(),
        }
    ]
    warnings: list[str] = []

    verified = normalizer.verify_manifest(tmp_path, manifest, warnings)
    assert verified["verified"] is True
    assert warnings == []

    evidence_file.write_text('{"safe": false}', encoding="utf-8")
    warnings = []
    mismatched = normalizer.verify_manifest(tmp_path, manifest, warnings)

    assert mismatched["verified"] is False
    assert warnings == ["USER01 manifest hash mismatch: evidence.json"]


def test_empty_post_execution_smb_state_keeps_historical_access_unknown() -> None:
    summary = load_imported_summary()
    endpoint = summary["user01_endpoint_evidence"]

    assert endpoint["smb_current_state"] == {
        "connection_count": 0,
        "mapping_count": 0,
        "historical_access_proven": False,
    }
    assert any(
        "does not disprove" in limitation
        for limitation in endpoint["limitations"]
    )


def test_student_facing_normalized_outputs_do_not_name_a_dev_version() -> None:
    summary_path = (
        RYUK_DIR / "input" / "normalized" / "ryuk_incident_summary.json"
    )
    brief_path = RYUK_DIR / "input" / "normalized" / "ryuk_ai_brief.md"

    assert "Version D" not in summary_path.read_text(encoding="utf-8")
    assert "Version D" not in brief_path.read_text(encoding="utf-8")
