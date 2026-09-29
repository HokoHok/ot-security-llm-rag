import json
from pathlib import Path

import pytest

from app.industroyer2_import import convert_industroyer2_normalized
from app.ingestion import load_chunks, load_evidence_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMALIZED_ROOT = (
    PROJECT_ROOT / "scenarios" / "industroyer2" / "input" / "normalized"
)


def test_normalized_industroyer2_output_converts_to_traceable_rag_files(
    tmp_path: Path,
) -> None:
    result = convert_industroyer2_normalized(
        normalized_root=NORMALIZED_ROOT,
        output_dir=tmp_path,
    )

    chunks = load_chunks(result.chunks_path)
    catalog = load_evidence_catalog(result.evidence_catalog_path)
    catalog_ids = {record.evidence_id for record in catalog.evidence}

    assert result.normalizer_version == "ind2-normalizer-0.1.5"
    assert result.chunk_count == 26
    assert result.evidence_count == 26
    assert {chunk.scenario for chunk in chunks} == {"industroyer2"}
    assert {chunk.evidence_ids[0] for chunk in chunks} == catalog_ids
    assert all(record.source_path.startswith("scenarios/industroyer2/") for record in catalog.evidence)
    assert all((PROJECT_ROOT / record.source_path).is_file() for record in catalog.evidence)
    assert all("normalizer=ind2-normalizer-0.1.5" in record.locator for record in catalog.evidence)
    assert any("via_eng_relay_success" in chunk.text for chunk in chunks)
    assert any(chunk.stage == "data_quality" for chunk in chunks)
    direct_comparison = next(
        chunk
        for chunk in chunks
        if chunk.chunk_id == "IND2-ve-002-comparison-note-02"
    )
    assert {"Kali", "正常 ENG", "OT-SERVER01"} <= set(
        direct_comparison.keywords
    )


def test_importer_rejects_case_index_when_a_companion_file_is_missing(
    tmp_path: Path,
) -> None:
    normalized_root = tmp_path / "normalized"
    normalized_root.mkdir()
    (normalized_root / "case_index.json").write_text(
        json.dumps(
            {
                "normalizer_version": "ind2-normalizer-0.1.5",
                "cases": [
                    {
                        "case_id": "ind2_missing_001",
                        "primary_case_type": "normal_eng_maintenance",
                        "tags": ["normal_eng_maintenance"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Required normalized file does not exist"):
        convert_industroyer2_normalized(
            normalized_root=normalized_root,
            output_dir=tmp_path / "output",
        )
