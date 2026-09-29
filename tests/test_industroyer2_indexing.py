from pathlib import Path

import chromadb

from app.config import DEFAULT_COLLECTION_NAME, collection_name_for_scenario
from app.indexing import persist_index
from app.ingestion import load_chunks
from app.retrieval import HybridRetriever
from app.schemas import KnowledgeChunk


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RYUK_DIR = PROJECT_ROOT / "scenarios" / "ryuk"
IND2_DIR = PROJECT_ROOT / "scenarios" / "industroyer2"


class Industroyer2QueryEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


def _industroyer2_embeddings(
    chunks: list[KnowledgeChunk],
) -> list[list[float]]:
    embeddings: list[list[float]] = []
    for chunk in chunks:
        if (
            chunk.stage == "analysis"
            and "via_eng_relay_success" in chunk.text
        ):
            embeddings.append([1.0, 0.0, 0.0])
        elif (
            chunk.stage == "analysis"
            and "normal_eng_maintenance" in chunk.text
        ):
            embeddings.append([0.98, 0.02, 0.0])
        else:
            embeddings.append([0.0, 1.0, 0.0])
    return embeddings


def test_scenario_collection_names_keep_ryuk_and_industroyer2_separate() -> None:
    assert (
        collection_name_for_scenario(DEFAULT_COLLECTION_NAME, "ryuk")
        == DEFAULT_COLLECTION_NAME
    )
    assert collection_name_for_scenario(
        DEFAULT_COLLECTION_NAME,
        "industroyer2",
    ) == "ot_security_chunks_v1_industroyer2"


def test_industroyer2_collection_is_separate_and_retrieves_comparison_evidence(
    tmp_path: Path,
) -> None:
    ryuk_chunk = load_chunks(RYUK_DIR / "chunks.json")[0]
    ind2_chunks = load_chunks(IND2_DIR / "chunks.json")
    chroma_path = tmp_path / "chroma"
    ryuk_collection_name = collection_name_for_scenario(
        DEFAULT_COLLECTION_NAME,
        "ryuk",
    )
    ind2_collection_name = collection_name_for_scenario(
        DEFAULT_COLLECTION_NAME,
        "industroyer2",
    )

    persist_index(
        chunks=[ryuk_chunk],
        embeddings=[[0.0, 0.0, 1.0]],
        chroma_path=chroma_path,
        collection_name=ryuk_collection_name,
        embedding_model="stub-embedding",
    )
    ind2_collection = persist_index(
        chunks=ind2_chunks,
        embeddings=_industroyer2_embeddings(ind2_chunks),
        chroma_path=chroma_path,
        collection_name=ind2_collection_name,
        embedding_model="stub-embedding",
    )

    client = chromadb.PersistentClient(path=chroma_path)
    assert client.get_collection(ryuk_collection_name).count() == 1
    assert ind2_collection.count() == 26

    retriever = HybridRetriever(
        collection=ind2_collection,
        embedder=Industroyer2QueryEmbedder(),
        candidate_k=26,
    )
    results = retriever.retrieve(
        question="為什麼只看 Server 的來源 IP，無法判斷正常維護或 Kali 經由 ENG Relay？",
        scenario="industroyer2",
        top_k=4,
    )

    result_text = "\n".join(result.document for result in results)
    assert "normal_eng_maintenance" in result_text
    assert "via_eng_relay_success" in result_text


def test_industroyer2_evidence_id_is_pinned_into_retrieval_results(
    tmp_path: Path,
) -> None:
    chunks = load_chunks(IND2_DIR / "chunks.json")
    collection = persist_index(
        chunks=chunks,
        embeddings=_industroyer2_embeddings(chunks),
        chroma_path=tmp_path / "chroma",
        collection_name="ind2_evidence_pinning_test",
        embedding_model="stub-embedding",
    )
    retriever = HybridRetriever(
        collection=collection,
        embedder=Industroyer2QueryEmbedder(),
        candidate_k=26,
    )

    results = retriever.retrieve(
        question="IND2-N20 支持什麼結論？",
        scenario="industroyer2",
        top_k=1,
    )

    assert results[0].evidence_ids == ["IND2-N20"]
