from pathlib import Path

import pytest
from chromadb.errors import NotFoundError

from app.indexing import persist_index
from app.ingestion import load_chunks, load_evidence_catalog
from app.retrieval import (
    HybridRetriever,
    _ensure_industroyer2_context_coverage,
    _ensure_ryuk_simulation_boundary_coverage,
    expand_scenario_query,
    reciprocal_rank_fusion,
    tokenize,
)
from app.schemas import KnowledgeChunk

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RYUK_DIR = PROJECT_ROOT / "scenarios" / "ryuk"


class StubEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class QuerySensitiveEmbedder:
    """Make an unknown evidence ID visibly alter ranking if not normalized."""

    def __init__(self) -> None:
        self.inputs: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.inputs.extend(texts)
        return [
            [0.0, 1.0, 0.0]
            if "RYUK-X99" in text.upper()
            else [1.0, 0.0, 0.0]
            for text in texts
        ]


def test_missing_chroma_collection_has_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingCollectionClient:
        def get_collection(self, *_args, **_kwargs):
            raise NotFoundError("collection does not exist")

    monkeypatch.setattr(
        "app.retrieval.chromadb.PersistentClient",
        lambda **_kwargs: MissingCollectionClient(),
    )

    with pytest.raises(RuntimeError, match="Build the scenario index"):
        HybridRetriever.from_persistent_chroma(
            chroma_path="test-chroma",
            collection_name="missing-collection",
            embedder=StubEmbedder(),
        )


def _build_evidence_pinning_retriever(
    tmp_path: Path,
    *,
    embedder: StubEmbedder | QuerySensitiveEmbedder | None = None,
) -> HybridRetriever:
    chunks = [
        KnowledgeChunk(
            chunk_id="RYUK-pin-baseline",
            scenario="ryuk",
            stage="overview",
            source_type="lab_configuration",
            title="Baseline relationship",
            text="General event relationship used as the dense-search baseline.",
            evidence_ids=["RYUK-C01"],
            keywords=["baseline"],
        ),
        KnowledgeChunk(
            chunk_id="RYUK-pin-u02",
            scenario="ryuk",
            stage="execution",
            source_type="observed_endpoint_log_summary",
            title="PowerShell event summary",
            text="USER01 PowerShell Operational event summary.",
            evidence_ids=["RYUK-U02"],
            keywords=["4103", "4104"],
        ),
        KnowledgeChunk(
            chunk_id="RYUK-pin-u03",
            scenario="ryuk",
            stage="execution",
            source_type="observed_endpoint_log_summary",
            title="Process event summary",
            text="USER01 Security process-creation event summary.",
            evidence_ids=["RYUK-U03"],
            keywords=["4688"],
        ),
    ]
    collection = persist_index(
        chunks=chunks,
        embeddings=[
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        chroma_path=tmp_path / "evidence-pinning-chroma",
        collection_name="evidence_pinning_test",
        embedding_model="stub-embedding",
    )
    return HybridRetriever(
        collection=collection,
        embedder=embedder or StubEmbedder(),
        candidate_k=1,
    )


def test_tokenize_preserves_chinese_and_technical_identifiers() -> None:
    tokens = tokenize("USER01 透過 SMB 存取 DC01 的 .RYK")

    assert "user01" in tokens
    assert "smb" in tokens
    assert "dc01" in tokens
    assert ".ryk" in tokens
    assert any("存取" in token for token in tokens)


def test_industroyer2_query_expansion_adds_only_retrieval_vocabulary() -> None:
    question = "看到 .20 來源位址，為什麼不能當作正常維護？"

    expanded = expand_scenario_query(question, "industroyer2")

    assert question in expanded
    assert "source_ip=10.10.60.20" in expanded
    assert "via_eng_relay_success" in expanded
    assert "via ENG Relay" in expanded
    assert expand_scenario_query(question, "ryuk") == question


def test_industroyer2_relay_correlation_keeps_data_quality_context() -> None:
    ids = ["summary", "comparison", "relay", "quality"]
    documents = [
        "Kali requested a command through ENG Relay.",
        "Comparison note for Relay traffic.",
        "accepted=True iec104_sent=True outcome=eng_relay_successful_iec104_send.",
        "Server/PCAP source IP alone cannot distinguish normal ENG operation from Kali via ENG relay.",
    ]
    candidates = [
        ("comparison", 0.04),
        ("relay", 0.03),
        ("summary", 0.02),
        ("quality", 0.01),
    ]

    refined = _ensure_industroyer2_context_coverage(
        question="Kali 透過 ENG Relay 成功影響 OT-SERVER01，要對照哪幾端資料？",
        scenario="industroyer2",
        top_k=3,
        selected=candidates[:3],
        candidates=candidates,
        ids=ids,
        documents=documents,
        sparse_indexes=[0, 1, 2, 3],
    )

    assert {chunk_id for chunk_id, _score in refined} == {
        "summary",
        "relay",
        "quality",
    }


def test_industroyer2_relay_path_keeps_handoff_and_send_record() -> None:
    ids = ["baseline", "quality", "summary", "relay-send"]
    documents = [
        "Normal ENG traffic establishes the authorized baseline.",
        "PCAP metadata is available.",
        (
            "Kali requested a command through ENG Relay. ENG01 sent the "
            "IEC-104 command to OT-SERVER01."
        ),
        "accepted=True iec104_sent=True outcome=eng_relay_successful_iec104_send.",
    ]
    candidates = [
        ("baseline", 0.04),
        ("quality", 0.03),
        ("summary", 0.02),
        ("relay-send", 0.01),
    ]

    refined = _ensure_industroyer2_context_coverage(
        question=(
            "Kali via-ENG Relay 的封包會經由 ENG01 發送，還是 Kali 直接送到 "
            "OT-SERVER01？"
        ),
        scenario="industroyer2",
        top_k=2,
        selected=candidates[:2],
        candidates=candidates,
        ids=ids,
        documents=documents,
        sparse_indexes=[0, 1, 2, 3],
    )

    assert {chunk_id for chunk_id, _score in refined} == {
        "summary",
        "relay-send",
    }


def test_industroyer2_source_ip_question_keeps_via_relay_comparison() -> None:
    ids = ["baseline", "server", "summary", "relay", "via-relay"]
    documents = [
        "Normal ENG traffic establishes the authorized baseline.",
        "Server command record.",
        "Normal ENG executive summary.",
        "ENG Relay event record.",
        "Via-ENG relay success can look similar to normal ENG traffic from the Server/PCAP perspective.",
    ]
    candidates = [
        ("baseline", 0.05),
        ("server", 0.04),
        ("summary", 0.03),
        ("relay", 0.02),
        ("via-relay", 0.01),
    ]

    refined = _ensure_industroyer2_context_coverage(
        question="Server 收到 .20 的 IEC-104，就能確定是正常 ENG 工程師指令嗎？",
        scenario="industroyer2",
        top_k=4,
        selected=candidates[:4],
        candidates=candidates,
        ids=ids,
        documents=documents,
        sparse_indexes=[0, 1, 2, 3, 4],
    )

    assert "baseline" in {chunk_id for chunk_id, _score in refined}
    assert "via-relay" in {chunk_id for chunk_id, _score in refined}


def test_rrf_rewards_items_present_in_both_rankings() -> None:
    fused = reciprocal_rank_fusion(
        dense_ids=["dense-only", "shared"],
        sparse_ids=["shared", "sparse-only"],
    )

    assert fused[0][0] == "shared"


def test_retrieves_user01_dc01_relationship_without_ollama(
    tmp_path: Path,
) -> None:
    chunks = load_chunks(RYUK_DIR / "chunks.json")
    preferred = {
        "RYUK-topology-01": [1.0, 0.0, 0.0],
        "RYUK-execution-impact-01": [0.98, 0.02, 0.0],
        "RYUK-delivery-01": [0.90, 0.10, 0.0],
        "RYUK-impact-01": [0.80, 0.20, 0.0],
    }
    embeddings = [
        preferred.get(chunk.chunk_id, [0.0, 1.0, 0.0])
        for chunk in chunks
    ]
    collection = persist_index(
        chunks=chunks,
        embeddings=embeddings,
        chroma_path=tmp_path / "chroma",
        collection_name="retrieval_test",
        embedding_model="stub-embedding",
    )
    retriever = HybridRetriever(
        collection=collection,
        embedder=StubEmbedder(),
    )

    results = retriever.retrieve(
        question="為什麼 Kali 不是直接修改 DC01 Share，實際執行端是誰？",
        scenario="ryuk",
        top_k=4,
    )
    result_ids = {result.chunk_id for result in results}

    assert "RYUK-topology-01" in result_ids
    assert "RYUK-execution-impact-01" in result_ids

    catalog = load_evidence_catalog(RYUK_DIR / "evidence_catalog.json")
    valid_evidence_ids = {
        record.evidence_id
        for record in catalog.evidence
    }
    returned_evidence_ids = {
        evidence_id
        for result in results
        for evidence_id in result.evidence_ids
    }
    assert returned_evidence_ids <= valid_evidence_ids


def test_bm25_can_retrieve_an_evidence_id_stored_in_metadata(
    tmp_path: Path,
) -> None:
    chunks = [
        KnowledgeChunk(
            chunk_id="RYUK-test-evidence",
            scenario="ryuk",
            stage="execution",
            source_type="observed_endpoint_logs",
            title="USER01 process creation",
            text="Security log records process creation on USER01.",
            evidence_ids=["RYUK-U03"],
            keywords=["USER01", "4688"],
        ),
        KnowledgeChunk(
            chunk_id="RYUK-test-other",
            scenario="ryuk",
            stage="impact",
            source_type="observed_log",
            title="Other evidence",
            text="Unrelated impact artifact.",
            evidence_ids=["RYUK-L03"],
            keywords=["impact"],
        ),
    ]
    collection = persist_index(
        chunks=chunks,
        embeddings=[[0.9, 0.1, 0.0], [1.0, 0.0, 0.0]],
        chroma_path=tmp_path / "evidence-id-chroma",
        collection_name="evidence_id_test",
        embedding_model="stub-embedding",
    )
    retriever = HybridRetriever(
        collection=collection,
        embedder=StubEmbedder(),
        candidate_k=2,
    )

    result = retriever.retrieve(
        question="RYUK-U03 能支持什麼結論？",
        scenario="ryuk",
        top_k=1,
    )

    assert result[0].chunk_id == "RYUK-test-evidence"


def test_source_type_filter_isolates_general_reference_chunks(
    tmp_path: Path,
) -> None:
    chunks = [
        KnowledgeChunk(
            chunk_id="IND2-event-01",
            scenario="industroyer2",
            stage="execution",
            source_type="normalized_summary",
            title="Lab Relay record",
            text="ENG Relay recorded a safe simulation event.",
            evidence_ids=["IND2-N01"],
            keywords=["Relay"],
        ),
        KnowledgeChunk(
            chunk_id="OT-iec104-overview-01",
            scenario="industroyer2",
            stage="general_concept",
            source_type="public_reference",
            title="IEC-104 overview",
            text="IEC-104 is a telecontrol protocol using TCP/IP transport profiles.",
            evidence_ids=["OT-G01"],
            keywords=["IEC-104", "SCADA", "TCP/IP"],
        ),
    ]
    collection = persist_index(
        chunks=chunks,
        embeddings=[[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        chroma_path=tmp_path / "source-filter-chroma",
        collection_name="source_filter_test",
        embedding_model="stub-embedding",
    )
    retriever = HybridRetriever(
        collection=collection,
        embedder=StubEmbedder(),
    )

    concepts = retriever.retrieve(
        question="IEC-104 是什麼？",
        scenario="industroyer2",
        top_k=2,
        source_types=("public_reference", "teaching_convention"),
    )
    events = retriever.retrieve(
        question="Relay 發生什麼事？",
        scenario="industroyer2",
        top_k=2,
        excluded_source_types=("public_reference", "teaching_convention"),
    )

    assert [result.chunk_id for result in concepts] == [
        "OT-iec104-overview-01"
    ]
    assert [result.chunk_id for result in events] == ["IND2-event-01"]


def test_explicit_multiple_evidence_ids_are_pinned_into_top_k(
    tmp_path: Path,
) -> None:
    retriever = _build_evidence_pinning_retriever(tmp_path)

    results = retriever.retrieve(
        question="請比較 RYUK-U02 與 RYUK-U03 分別記錄了什麼？",
        scenario="ryuk",
        top_k=2,
    )

    assert {result.chunk_id for result in results} == {
        "RYUK-pin-u02",
        "RYUK-pin-u03",
    }
    returned_evidence_ids = {
        evidence_id
        for result in results
        for evidence_id in result.evidence_ids
    }
    assert {"RYUK-U02", "RYUK-U03"} <= returned_evidence_ids


def test_unknown_evidence_id_does_not_change_retrieval(
    tmp_path: Path,
) -> None:
    embedder = QuerySensitiveEmbedder()
    retriever = _build_evidence_pinning_retriever(
        tmp_path,
        embedder=embedder,
    )

    baseline = retriever.retrieve(
        question="請說明一般事件關係",
        scenario="ryuk",
        top_k=2,
    )
    with_unknown_id = retriever.retrieve(
        question="請說明一般事件關係 RYUK-X99",
        scenario="ryuk",
        top_k=2,
    )

    assert embedder.inputs == ["請說明一般事件關係", "請說明一般事件關係"]
    assert [result.chunk_id for result in with_unknown_id] == [
        result.chunk_id for result in baseline
    ]
    assert all(
        "RYUK-X99" not in result.evidence_ids
        for result in with_unknown_id
    )


def test_evidence_pinning_uses_question_order_when_top_k_is_too_small(
    tmp_path: Path,
) -> None:
    retriever = _build_evidence_pinning_retriever(tmp_path)

    u03_first = retriever.retrieve(
        question="先看 RYUK-U03，再比較 RYUK-U02。",
        scenario="ryuk",
        top_k=1,
    )
    u02_first = retriever.retrieve(
        question="先看 RYUK-U02，再比較 RYUK-U03。",
        scenario="ryuk",
        top_k=1,
    )

    assert u03_first[0].chunk_id == "RYUK-pin-u03"
    assert u02_first[0].chunk_id == "RYUK-pin-u02"


def test_ryuk_simulation_boundary_keeps_stage_and_impact_evidence() -> None:
    ids = [
        "RYUK-simulation-boundaries-01",
        "RYUK-impact-01",
        "RYUK-other-01",
        "RYUK-other-02",
    ]
    documents = [
        "Credential exposure is an assumption. Scheduled task and service recovery are dry run.",
        "Caesar shift is a safe transform, not real Ryuk encryption.",
        "Unrelated collection metadata.",
        "Unrelated topology information.",
    ]
    candidates = [
        ("RYUK-other-01", 0.04),
        ("RYUK-simulation-boundaries-01", 0.03),
        ("RYUK-other-02", 0.02),
        ("RYUK-impact-01", 0.01),
    ]

    refined = _ensure_ryuk_simulation_boundary_coverage(
        question="哪些 kill chain 階段是真的觀測，哪些只是安全模擬？",
        scenario="ryuk",
        top_k=2,
        selected=candidates[:2],
        candidates=candidates,
        ids=ids,
        documents=documents,
        sparse_indexes=[0, 1, 2, 3],
    )

    assert {chunk_id for chunk_id, _score in refined} == {
        "RYUK-simulation-boundaries-01",
        "RYUK-impact-01",
    }
