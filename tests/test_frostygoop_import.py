import json
from pathlib import Path

import pytest

from app.frostygoop_import import convert_frostygoop_normalized
from app.ingestion import load_chunks, load_evidence_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMALIZED_ROOT = PROJECT_ROOT / "scenarios" / "frostygoop" / "input" / "normalized"


def test_normalized_frostygoop_output_converts_to_traceable_rag_files(
    tmp_path: Path,
) -> None:
    result = convert_frostygoop_normalized(
        normalized_root=NORMALIZED_ROOT,
        output_dir=tmp_path,
    )

    chunks = load_chunks(result.chunks_path)
    catalog = load_evidence_catalog(result.evidence_catalog_path)
    catalog_ids = {record.evidence_id for record in catalog.evidence}

    assert result.normalizer_version == "frostygoop-normalizer-0.1.0"
    assert result.chunk_count == 14
    assert result.evidence_count == 14
    assert {chunk.scenario for chunk in chunks} == {"frostygoop"}
    assert {chunk.evidence_ids[0] for chunk in chunks} == catalog_ids
    assert all(record.source_path.startswith("scenarios/frostygoop/") for record in catalog.evidence)
    assert all((PROJECT_ROOT / record.source_path).is_file() for record in catalog.evidence)
    assert all("normalizer=frostygoop-normalizer-0.1.0" in record.locator for record in catalog.evidence)
    assert any(chunk.chunk_id.endswith("pcap-follow-up-03") for chunk in chunks)
    assert any("not production TCP/502" in chunk.text for chunk in chunks)
    assert any("not claimed as observed real FrostyGoop behavior" in chunk.text for chunk in chunks)


def test_importer_rejects_missing_normalized_companion_file(tmp_path: Path) -> None:
    normalized_root = tmp_path / "normalized"
    normalized_root.mkdir()
    (normalized_root / "case_index.json").write_text(
        json.dumps(
            {
                "normalizer_version": "frostygoop-normalizer-0.1.0",
                "scenario": "frostygoop",
                "cases": [
                    {
                        "case_id": "frostygoop_missing_001",
                        "classification": "safe_test",
                        "safety_role": "safe test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Required normalized file does not exist"):
        convert_frostygoop_normalized(
            normalized_root=normalized_root,
            output_dir=tmp_path / "output",
        )
