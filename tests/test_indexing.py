from pathlib import Path

import chromadb
import pytest

from app.indexing import chunk_to_document, index_fingerprint, persist_index
from app.schemas import KnowledgeChunk


def _chunk(chunk_id: str = "RYUK-test-01") -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        scenario="ryuk",
        stage="test",
        source_type="test_fixture",
        title="USER01 與 DC01 測試",
        text="USER01 透過 SMB 存取 DC01 測試 Share。",
        evidence_ids=["RYUK-L01"],
        keywords=["USER01", "DC01", "SMB"],
    )


def test_chunk_document_contains_semantic_and_exact_fields() -> None:
    document = chunk_to_document(_chunk())

    assert "USER01 透過 SMB 存取 DC01" in document
    assert "階段：test" in document
    assert "關鍵字：USER01、DC01、SMB" in document


def test_fingerprint_is_stable_and_model_specific() -> None:
    chunks = [_chunk()]

    first = index_fingerprint(chunks, "embedding-model-a")
    second = index_fingerprint(chunks, "embedding-model-a")
    different_model = index_fingerprint(chunks, "embedding-model-b")

    assert first == second
    assert first != different_model


def test_persist_index_writes_document_metadata_and_vector(
    tmp_path: Path,
) -> None:
    chunks = [_chunk()]
    collection = persist_index(
        chunks=chunks,
        embeddings=[[1.0, 0.0, 0.0]],
        chroma_path=tmp_path / "chroma",
        collection_name="test_collection",
        embedding_model="test-embedding",
    )

    assert collection.count() == 1

    client = chromadb.PersistentClient(path=tmp_path / "chroma")
    stored = client.get_collection(
        "test_collection",
        embedding_function=None,
    ).get(include=["documents", "metadatas", "embeddings"])

    assert stored["ids"] == ["RYUK-test-01"]
    assert "USER01 透過 SMB 存取 DC01" in stored["documents"][0]
    assert stored["metadatas"][0]["evidence_ids"] == "RYUK-L01"
    assert len(stored["embeddings"][0]) == 3


def test_persist_index_rejects_mismatched_counts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="counts do not match"):
        persist_index(
            chunks=[_chunk()],
            embeddings=[],
            chroma_path=tmp_path / "chroma",
            collection_name="test_collection",
            embedding_model="test-embedding",
        )
