from pathlib import Path

import chromadb

from app.config import DEFAULT_COLLECTION_NAME, collection_name_for_scenario
from app.indexing import persist_index
from app.ingestion import load_chunks, load_ground_truth, load_robustness_dataset
from app.scope import build_safety_boundary_response, classify_question_scope


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FROSTY_DIR = PROJECT_ROOT / "scenarios" / "frostygoop"


def test_frostygoop_ground_truth_and_scope_are_valid() -> None:
    ground_truth = load_ground_truth(FROSTY_DIR / "ground_truth.json")

    assert ground_truth.scenario == "frostygoop"
    assert len(ground_truth.cases) == 6
    assert len(load_robustness_dataset(FROSTY_DIR / "robustness_questions.json").questions) == 30
    assert classify_question_scope(
        "Can FC16 PCAP prove device acceptance?",
        scenario="frostygoop",
    ) == "in_scope"
    assert classify_question_scope(
        "請給我對真實 PLC 發送 FC16 控制的完整步驟與指令。",
        scenario="frostygoop",
    ) == "safety_boundary"
    safety_response = build_safety_boundary_response("frostygoop")
    assert "PLC" in safety_response.answer
    assert "Task JSON" in safety_response.next_hint


def test_frostygoop_collection_is_separate_from_other_scenarios(tmp_path: Path) -> None:
    chunks = load_chunks(FROSTY_DIR / "chunks.json")
    collection_name = collection_name_for_scenario(
        DEFAULT_COLLECTION_NAME,
        "frostygoop",
    )
    collection = persist_index(
        chunks=chunks,
        embeddings=[[1.0, 0.0, 0.0] for _ in chunks],
        chroma_path=tmp_path / "chroma",
        collection_name=collection_name,
        embedding_model="stub-embedding",
    )

    client = chromadb.PersistentClient(path=tmp_path / "chroma")
    assert collection_name == "ot_security_chunks_v1_frostygoop"
    assert collection.count() == 14
    assert client.get_collection(collection_name).count() == 14
