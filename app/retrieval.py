from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import chromadb
import jieba
from chromadb.errors import NotFoundError
from rank_bm25 import BM25Okapi

jieba.setLogLevel(logging.WARNING)

_EVIDENCE_ID_PATTERN = re.compile(
    r"(?<![A-Z0-9])[A-Z][A-Z0-9]*-[A-Z][0-9]{2,3}(?![A-Z0-9])",
    re.IGNORECASE,
)


class EmbeddingProvider(Protocol):
    """Minimal embedding interface used by retrieval and test doubles."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector for each input text."""


@dataclass(frozen=True)
class RetrievedChunk:
    """One fused retrieval result with traceable ranking details."""

    chunk_id: str
    document: str
    metadata: dict[str, Any]
    rrf_score: float
    dense_rank: int | None
    sparse_rank: int | None
    dense_distance: float | None = None
    sparse_score: float | None = None

    @property
    def evidence_ids(self) -> list[str]:
        raw_value = self.metadata.get("evidence_ids", "")
        if not isinstance(raw_value, str):
            return []
        return [
            evidence_id.strip()
            for evidence_id in raw_value.split(",")
            if evidence_id.strip()
        ]


def _retrieval_where(
    scenario: str,
    source_types: Sequence[str] | None,
    excluded_source_types: Sequence[str] | None,
) -> dict[str, Any]:
    """Build the Chroma filter for one isolated knowledge route."""

    conditions: list[dict[str, Any]] = [{"scenario": scenario}]
    if source_types:
        conditions.append({"source_type": {"$in": list(source_types)}})
    if excluded_source_types:
        conditions.append(
            {"source_type": {"$nin": list(excluded_source_types)}}
        )
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def tokenize(text: str) -> list[str]:
    """Tokenize Taiwan Traditional Chinese and preserve technical identifiers."""

    normalized = text.lower()
    jieba_tokens = [
        token.strip()
        for token in jieba.lcut(normalized)
        if token.strip() and not re.fullmatch(r"\W+", token)
    ]
    technical_tokens = re.findall(r"[a-z0-9_.:/\\-]+", normalized)
    return jieba_tokens + technical_tokens


def _metadata_evidence_ids(metadata: dict[str, Any]) -> frozenset[str]:
    """Return normalized evidence IDs stored in one Chroma metadata record."""

    raw_value = metadata.get("evidence_ids", "")
    if not isinstance(raw_value, str):
        return frozenset()
    return frozenset(
        value.strip().upper()
        for value in raw_value.split(",")
        if value.strip()
    )


def _requested_evidence_ids(question: str) -> tuple[str, ...]:
    """Extract unique scenario evidence IDs in first-occurrence order."""

    requested: list[str] = []
    seen: set[str] = set()
    for match in _EVIDENCE_ID_PATTERN.finditer(question):
        evidence_id = match.group(0).upper()
        if evidence_id not in seen:
            requested.append(evidence_id)
            seen.add(evidence_id)
    return tuple(requested)


def normalize_retrieval_query(
    question: str,
    valid_evidence_ids: Sequence[str],
) -> str:
    """Remove unknown evidence references before dense and sparse retrieval.

    The original question still drives evidence pinning and remains available to
    the caller for generation.  This normalization only prevents a syntactically
    valid but nonexistent ID, such as ``RYUK-X99``, from changing retrieval ranks.
    """

    valid_ids = {evidence_id.upper() for evidence_id in valid_evidence_ids}

    def replace_unknown(match: re.Match[str]) -> str:
        return match.group(0) if match.group(0).upper() in valid_ids else ""

    normalized = _EVIDENCE_ID_PATTERN.sub(replace_unknown, question)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or "未提供可檢索的事件問題"


def expand_scenario_query(question: str, scenario: str) -> str:
    """Add safe scenario vocabulary for natural-language retrieval aliases.

    Expansion is intentionally retrieval-only: it does not add claims to the
    Llama prompt and cannot create evidence IDs.  The rules map recurring
    student wording to identifiers already present in normalized Industroyer2
    evidence, so dense and BM25 retrieval can find the relevant chunk.
    """

    if scenario.strip().casefold() != "industroyer2":
        return question

    normalized = question.casefold()
    additions: list[str] = []

    def has_any(*terms: str) -> bool:
        return any(term in normalized for term in terms)

    # A short path question such as "Kali via-ENG Relay ... or direct?" can
    # omit the older correlation vocabulary below.  Expand it to the recorded
    # Relay-success fields, rather than letting a host-name-only match decide
    # whether the event evidence is adequate.
    if has_any("via-eng", "via eng") and has_any(
        "kali",
        "direct",
        "through",
        "經由",
        "透過",
        "發送",
        "送到",
    ):
        additions.extend(
            (
                "via_eng_relay_success",
                "Kali requested a command through ENG Relay",
                "ENG01 sent the IEC-104 command to OT-SERVER01",
                "accepted=True",
                "iec104_sent=True",
                "source_ip=10.10.60.20",
            )
        )

    if has_any("maintenance ticket", "maintenance_ticket", "維護單", "維護票"):
        additions.extend(
            (
                "maintenance_ticket",
                "ENG 本機 local evidence",
                "normal ENG",
                "normal_eng_maintenance authorized baseline",
                "source_ip=10.10.60.20 TCP/2404",
                "IEC-104 packet",
                "Server state",
            )
        )

    if has_any("source_ip", "10.10.60.20", ".20", "來源位址", "來源 ip"):
        additions.extend(
            (
                "source_ip=10.10.60.20",
                "normal ENG",
                "via ENG Relay",
                "via_eng_relay_success",
                "Kali action",
                "normal_eng_maintenance authorized baseline",
                "Server source IP alone cannot distinguish",
            )
        )

    if has_any("direct", "直接送", "直接操作") and has_any(
        "kali", "iec-104", "iec104", "ot-server01", "正常 eng", "relay", "中繼", "中继"
    ):
        additions.extend(
            (
                "direct_iec104_attack",
                "source_ip=10.10.60.10",
                "Direct attack is distinguishable at Server PCAP",
                "OT-SERVER01",
                "TCP/2404",
            )
        )

    if has_any("relay", "中繼", "中继") and has_any(
        "成功", "送出", "送到", "影響", "影响", "對照", "对照", "哪幾", "哪些"
    ):
        additions.extend(
            (
                "via_eng_relay_success",
                "Kali action",
                "ENG Relay",
                "accepted=True",
                "iec104_sent=True",
                "Server command",
                "PCAP metadata",
                "data_quality",
                "tshark not installed",
                "Server PCAP source IP alone cannot distinguish normal ENG operation from Kali via ENG relay",
            )
        )

    if has_any("caddywiper", "lockout") and has_any("relay", "中繼", "中继"):
        additions.extend(
            (
                "blocked_by_safe_caddywiper",
                "accepted=False",
                "iec104_sent=False",
                "ENG Relay",
            )
        )

    if has_any("pcap", "tshark", "asdu", "type id", "ioa", "封包欄位", "封包字段"):
        additions.extend(
            (
                "data_quality",
                "tshark not installed",
                "PCAP file size SHA-256",
                "IEC-104 fields not decoded",
            )
        )

    if has_any("時間戳", "时间戳", "timestamp", "時鐘", "时钟", "因果順序", "因果顺序"):
        additions.extend(
            (
                "data_quality",
                "cross-host clock offset",
                "timestamps",
                "do not infer causality from absolute time alone",
            )
        )

    if has_any("路由", "路由", "防火牆", "防火墙", "每一跳"):
        additions.extend(
            (
                "data_quality",
                "PCAP limitation",
                "ind2_vE_002",
                "tshark not installed",
                "cross-host clock offset",
                "cross-host evidence",
            )
        )

    if has_any("工廠", "工厂", "停機", "停机", "營運", "运营", "損失", "损失"):
        additions.extend(
            (
                "Industroyer2-like safe simulation",
                "direct_iec104_attack",
                "ind2_vE_002",
            )
        )

    if not additions:
        return question
    unique_additions = list(dict.fromkeys(additions))
    return f"{question}\n檢索同義詞：{'；'.join(unique_additions)}"


def _pin_requested_evidence_chunks(
    *,
    question: str,
    top_k: int,
    fused: Sequence[tuple[str, float]],
    ids: Sequence[str],
    metadatas: Sequence[dict[str, Any]],
    sparse_indexes: Sequence[int],
) -> list[tuple[str, float]]:
    """Ensure explicit, valid evidence references survive Top-K truncation.

    For every requested ID that actually exists in the scenario collection, the
    best matching chunk is selected according to the existing fused order, then
    the full BM25 order, and finally ``chunk_id``.  The selected chunks replace
    only the lowest-ranked non-pinned results.

    If ``top_k`` cannot hold every distinct selected chunk, evidence IDs are
    considered in first-occurrence order in the question.  One chunk may satisfy
    multiple requested IDs.  Unknown IDs are ignored and therefore cannot alter
    the original fused result.
    """

    requested_ids = _requested_evidence_ids(question)
    if not requested_ids:
        return list(fused[:top_k])

    evidence_by_chunk = {
        chunk_id: _metadata_evidence_ids(metadata)
        for chunk_id, metadata in zip(ids, metadatas, strict=True)
    }
    valid_requested_ids = [
        evidence_id
        for evidence_id in requested_ids
        if any(
            evidence_id in chunk_evidence_ids
            for chunk_evidence_ids in evidence_by_chunk.values()
        )
    ]
    if not valid_requested_ids:
        return list(fused[:top_k])

    fused_rank = {
        chunk_id: rank
        for rank, (chunk_id, _) in enumerate(fused, start=1)
    }
    sparse_rank = {
        ids[index]: rank
        for rank, index in enumerate(sparse_indexes, start=1)
    }
    missing_rank = len(ids) + len(fused) + 1

    pinned_ids: list[str] = []
    for evidence_id in valid_requested_ids:
        matching_chunks = [
            chunk_id
            for chunk_id in ids
            if evidence_id in evidence_by_chunk[chunk_id]
        ]
        best_chunk_id = min(
            matching_chunks,
            key=lambda chunk_id: (
                fused_rank.get(chunk_id, missing_rank),
                sparse_rank.get(chunk_id, missing_rank),
                chunk_id,
            ),
        )
        if best_chunk_id not in pinned_ids:
            pinned_ids.append(best_chunk_id)

    # A finite Top-K cannot always represent every distinct requested evidence
    # record.  First textual occurrence is the documented deterministic tie-break.
    pinned_ids = pinned_ids[:top_k]
    selected_ids = set(pinned_ids)
    for chunk_id, _ in fused:
        if len(selected_ids) >= top_k:
            break
        selected_ids.add(chunk_id)

    score_by_id = dict(fused)
    pinned_order = {
        chunk_id: order
        for order, chunk_id in enumerate(pinned_ids)
    }
    ordered_ids = sorted(
        selected_ids,
        key=lambda chunk_id: (
            fused_rank.get(chunk_id, missing_rank),
            pinned_order.get(chunk_id, missing_rank),
            chunk_id,
        ),
    )
    return [
        (chunk_id, score_by_id.get(chunk_id, 0.0))
        for chunk_id in ordered_ids
    ]


def _ensure_industroyer2_context_coverage(
    *,
    question: str,
    scenario: str,
    top_k: int,
    selected: Sequence[tuple[str, float]],
    candidates: Sequence[tuple[str, float]],
    ids: Sequence[str],
    documents: Sequence[str],
    sparse_indexes: Sequence[int],
) -> list[tuple[str, float]]:
    """Reserve one complementary evidence type for two correlation questions.

    This is a small diversity re-ranker, not evidence-ID pinning.  It recognizes
    two recurring Industroyer2 teaching intents and selects documents by their
    recorded semantic content: a Relay-correlation question needs one data
    quality boundary, while a source-IP attribution question needs a via-Relay
    comparison.  The primary Relay or normal-ENG evidence remains protected.
    """

    if scenario.casefold() != "industroyer2" or top_k < 2:
        return list(selected)

    normalized = question.casefold()
    document_by_id = {
        chunk_id: document.casefold()
        for chunk_id, document in zip(ids, documents, strict=True)
    }
    candidate_rank = {
        chunk_id: rank
        for rank, (chunk_id, _score) in enumerate(candidates, start=1)
    }
    sparse_rank = {
        ids[index]: rank
        for rank, index in enumerate(sparse_indexes, start=1)
    }
    missing_rank = len(ids) + len(candidates) + 1
    score_by_id = dict(candidates)
    selected_ids = [chunk_id for chunk_id, _score in selected]

    def choose_best(predicate: Any) -> str | None:
        matching = [
            chunk_id
            for chunk_id, document in document_by_id.items()
            if predicate(document)
        ]
        if not matching:
            return None
        return min(
            matching,
            key=lambda chunk_id: (
                candidate_rank.get(chunk_id, missing_rank),
                sparse_rank.get(chunk_id, missing_rank),
                chunk_id,
            ),
        )

    def ensure(
        desired_id: str | None,
        protected: Any,
    ) -> None:
        if desired_id is None or desired_id in selected_ids:
            return
        removable = [
            chunk_id
            for chunk_id in selected_ids
            if not protected(document_by_id[chunk_id])
        ]
        if not removable:
            return
        replace_id = max(
            removable,
            key=lambda chunk_id: (
                candidate_rank.get(chunk_id, missing_rank),
                sparse_rank.get(chunk_id, missing_rank),
                chunk_id,
            ),
        )
        selected_ids[selected_ids.index(replace_id)] = desired_id

    asks_relay_correlation = (
        "kali" in normalized
        and "relay" in normalized
        and any(marker in normalized for marker in ("成功", "送出", "影響", "影响"))
        and any(marker in normalized for marker in ("哪幾", "哪些", "對照", "对照", "資料", "资料"))
    )
    if asks_relay_correlation:
        ensure(
            choose_best(
                lambda document: "server/pcap source ip alone cannot distinguish"
                in document
            ),
            lambda document: (
                "kali requested a command through eng relay" in document
                or ("accepted=true" in document and "iec104_sent=true" in document)
            ),
        )

    asks_source_ip_attribution = (
        any(marker in normalized for marker in ("10.10.60.20", ".20", "source_ip"))
        and any(marker in normalized for marker in ("正常", "normal", "工程師", "工程师"))
        and any(marker in normalized for marker in ("確定", "确定", "判定", "證明", "证明"))
    )
    if asks_source_ip_attribution:
        ensure(
            choose_best(
                lambda document: "via-eng relay success can look similar to normal eng traffic"
                in document
            ),
            lambda document: "authorized baseline" in document,
        )

    # Keep both ends of the recorded hand-off for questions about whether a
    # via-ENG request is forwarded by ENG01 or sent directly by Kali.  The
    # executive summary establishes the host path; the Relay record confirms
    # that the send result was successful.  This is intentionally based on
    # document content, not a fixed chunk or evidence ID.
    asks_relay_path = (
        "kali" in normalized
        and "relay" in normalized
        and any(
            marker in normalized
            for marker in (
                "via-eng",
                "via eng",
                "through",
                "direct",
                "直接",
            )
        )
    )
    if asks_relay_path:
        relay_path_summary = choose_best(
            lambda document: (
                "kali requested a command through eng relay" in document
                and "eng01 sent the iec-104 command to ot-server01" in document
            )
        )
        relay_send_record = choose_best(
            lambda document: (
                "accepted=true" in document and "iec104_sent=true" in document
            )
        )
        ensure(
            relay_path_summary,
            lambda document: "accepted=true" in document
            and "iec104_sent=true" in document,
        )
        ensure(
            relay_send_record,
            lambda document: (
                "kali requested a command through eng relay" in document
                and "eng01 sent the iec-104 command to ot-server01" in document
            ),
        )

    return sorted(
        (
            (chunk_id, score_by_id.get(chunk_id, 0.0))
            for chunk_id in selected_ids
        ),
        key=lambda item: (
            candidate_rank.get(item[0], missing_rank),
            sparse_rank.get(item[0], missing_rank),
            item[0],
        ),
    )


def _ensure_ryuk_simulation_boundary_coverage(
    *,
    question: str,
    scenario: str,
    top_k: int,
    selected: Sequence[tuple[str, float]],
    candidates: Sequence[tuple[str, float]],
    ids: Sequence[str],
    documents: Sequence[str],
    sparse_indexes: Sequence[int],
) -> list[tuple[str, float]]:
    """Keep impact and stage-boundary evidence together for one Ryuk question."""

    normalized = question.casefold()
    asks_stage_boundary = (
        scenario.casefold() == "ryuk"
        and top_k >= 2
        and "kill chain" in normalized
        and any(
            marker in normalized
            for marker in ("觀測", "安全模擬", "dry run", "實際")
        )
    )
    if not asks_stage_boundary:
        return list(selected)

    document_by_id = {
        chunk_id: document.casefold()
        for chunk_id, document in zip(ids, documents, strict=True)
    }
    candidate_rank = {
        chunk_id: rank
        for rank, (chunk_id, _score) in enumerate(candidates, start=1)
    }
    sparse_rank = {
        ids[index]: rank
        for rank, index in enumerate(sparse_indexes, start=1)
    }
    missing_rank = len(ids) + len(candidates) + 1
    score_by_id = dict(candidates)
    selected_ids = [chunk_id for chunk_id, _score in selected]

    def choose_best(predicate: Any) -> str | None:
        matching = [
            chunk_id
            for chunk_id, document in document_by_id.items()
            if predicate(document)
        ]
        if not matching:
            return None
        return min(
            matching,
            key=lambda chunk_id: (
                candidate_rank.get(chunk_id, missing_rank),
                sparse_rank.get(chunk_id, missing_rank),
                chunk_id,
            ),
        )

    def ensure(desired_id: str | None, protected: Any) -> None:
        if desired_id is None or desired_id in selected_ids:
            return
        removable = [
            chunk_id
            for chunk_id in selected_ids
            if not protected(document_by_id[chunk_id])
        ]
        if not removable:
            return
        replace_id = max(
            removable,
            key=lambda chunk_id: (
                candidate_rank.get(chunk_id, missing_rank),
                sparse_rank.get(chunk_id, missing_rank),
                chunk_id,
            ),
        )
        selected_ids[selected_ids.index(replace_id)] = desired_id

    stage_boundary = choose_best(
        lambda document: (
            ("credential exposure" in document or "憑證" in document)
            and ("scheduled task" in document or "排程工作" in document)
            and "dry run" in document
        )
    )
    safe_impact = choose_best(
        lambda document: (
            "caesar shift" in document
            and ("not real ryuk" in document or "不是真實 ryuk" in document)
            and not (
                "credential exposure" in document
                and "scheduled task" in document
                and "dry run" in document
            )
        )
    )
    ensure(
        stage_boundary,
        lambda document: (
            ("credential exposure" in document or "憑證" in document)
            and "dry run" in document
        ),
    )
    ensure(
        safe_impact,
        lambda document: (
            ("credential exposure" in document or "憑證" in document)
            and "dry run" in document
        ),
    )
    return sorted(
        (
            (chunk_id, score_by_id.get(chunk_id, 0.0))
            for chunk_id in selected_ids
        ),
        key=lambda item: (
            candidate_rank.get(item[0], missing_rank),
            sparse_rank.get(item[0], missing_rank),
            item[0],
        ),
    )


def reciprocal_rank_fusion(
    dense_ids: Sequence[str],
    sparse_ids: Sequence[str],
    dense_weight: float = 0.7,
    sparse_weight: float = 0.3,
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse dense and sparse rankings with deterministic weighted RRF."""

    if k < 1:
        raise ValueError("RRF k must be at least 1")
    if dense_weight < 0 or sparse_weight < 0:
        raise ValueError("RRF weights must not be negative")
    if dense_weight == 0 and sparse_weight == 0:
        raise ValueError("At least one RRF weight must be positive")

    scores: dict[str, float] = defaultdict(float)
    best_rank: dict[str, int] = {}

    for rank, chunk_id in enumerate(dense_ids, start=1):
        scores[chunk_id] += dense_weight / (k + rank)
        best_rank[chunk_id] = min(best_rank.get(chunk_id, rank), rank)

    for rank, chunk_id in enumerate(sparse_ids, start=1):
        scores[chunk_id] += sparse_weight / (k + rank)
        best_rank[chunk_id] = min(best_rank.get(chunk_id, rank), rank)

    return sorted(
        scores.items(),
        key=lambda item: (-item[1], best_rank[item[0]], item[0]),
    )


class HybridRetriever:
    """Retrieve scenario chunks using Chroma dense search, BM25, and RRF."""

    def __init__(
        self,
        collection: Any,
        embedder: EmbeddingProvider,
        dense_weight: float = 0.7,
        sparse_weight: float = 0.3,
        candidate_k: int = 12,
        rrf_k: int = 60,
    ) -> None:
        if candidate_k < 1:
            raise ValueError("candidate_k must be at least 1")
        self.collection = collection
        self.embedder = embedder
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight
        self.candidate_k = candidate_k
        self.rrf_k = rrf_k

    @classmethod
    def from_persistent_chroma(
        cls,
        chroma_path: str,
        collection_name: str,
        embedder: EmbeddingProvider,
        dense_weight: float = 0.7,
        sparse_weight: float = 0.3,
        candidate_k: int = 12,
        rrf_k: int = 60,
    ) -> HybridRetriever:
        client = chromadb.PersistentClient(path=chroma_path)
        try:
            collection = client.get_collection(
                collection_name,
                embedding_function=None,
            )
        except NotFoundError as exc:
            raise RuntimeError(
                f"ChromaDB collection {collection_name!r} was not found at "
                f"{chroma_path!r}. Build the scenario index before running "
                "retrieve or ask."
            ) from exc
        return cls(
            collection=collection,
            embedder=embedder,
            dense_weight=dense_weight,
            sparse_weight=sparse_weight,
            candidate_k=candidate_k,
            rrf_k=rrf_k,
        )

    def retrieve(
        self,
        question: str,
        scenario: str,
        top_k: int = 4,
        source_types: Sequence[str] | None = None,
        excluded_source_types: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        if not question.strip():
            raise ValueError("Question must not be empty")
        if not scenario.strip():
            raise ValueError("Scenario must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if source_types and excluded_source_types:
            overlap = set(source_types) & set(excluded_source_types)
            if overlap:
                raise ValueError(
                    "source_types and excluded_source_types overlap: "
                    + ", ".join(sorted(overlap))
                )

        where = _retrieval_where(
            scenario,
            source_types,
            excluded_source_types,
        )

        stored = self.collection.get(
            where=where,
            include=["documents", "metadatas"],
        )
        ids: list[str] = stored["ids"]
        if not ids:
            raise ValueError(f"No indexed chunks found for scenario {scenario!r}")

        documents: list[str] = stored["documents"]
        metadatas: list[dict[str, Any]] = [
            dict(metadata)
            for metadata in stored["metadatas"]
        ]
        candidate_count = min(self.candidate_k, len(ids))

        valid_evidence_ids = sorted(
            {
                evidence_id
                for metadata in metadatas
                for evidence_id in _metadata_evidence_ids(metadata)
            }
        )
        retrieval_question = normalize_retrieval_query(
            question,
            valid_evidence_ids,
        )
        retrieval_question = expand_scenario_query(
            retrieval_question,
            scenario,
        )

        query_embeddings = self.embedder.embed([retrieval_question])
        if len(query_embeddings) != 1:
            raise RuntimeError("Embedding provider did not return one query vector")

        dense = self.collection.query(
            query_embeddings=query_embeddings,
            where=where,
            n_results=candidate_count,
            include=["distances"],
        )
        dense_ids: list[str] = dense["ids"][0]
        dense_distances: list[float] = dense["distances"][0]

        sparse_documents = [
            f"{document}\n{metadata.get('evidence_ids', '')}"
            for document, metadata in zip(
                documents,
                metadatas,
                strict=True,
            )
        ]
        tokenized_documents = [
            tokenize(document) for document in sparse_documents
        ]
        bm25 = BM25Okapi(tokenized_documents)
        sparse_scores = bm25.get_scores(tokenize(retrieval_question))
        sparse_indexes = sorted(
            range(len(ids)),
            key=lambda index: (-float(sparse_scores[index]), ids[index]),
        )
        sparse_ids = [
            ids[index]
            for index in sparse_indexes
            if float(sparse_scores[index]) > 0
        ][:candidate_count]

        fused_candidates = reciprocal_rank_fusion(
            dense_ids=dense_ids,
            sparse_ids=sparse_ids,
            dense_weight=self.dense_weight,
            sparse_weight=self.sparse_weight,
            k=self.rrf_k,
        )
        fused = _pin_requested_evidence_chunks(
            question=question,
            top_k=top_k,
            fused=fused_candidates,
            ids=ids,
            metadatas=metadatas,
            sparse_indexes=sparse_indexes,
        )
        fused = _ensure_industroyer2_context_coverage(
            question=question,
            scenario=scenario,
            top_k=top_k,
            selected=fused,
            candidates=fused_candidates,
            ids=ids,
            documents=documents,
            sparse_indexes=sparse_indexes,
        )
        fused = _ensure_ryuk_simulation_boundary_coverage(
            question=question,
            scenario=scenario,
            top_k=top_k,
            selected=fused,
            candidates=fused_candidates,
            ids=ids,
            documents=documents,
            sparse_indexes=sparse_indexes,
        )
        dense_ranks = {
            chunk_id: rank
            for rank, chunk_id in enumerate(dense_ids, start=1)
        }
        sparse_ranks = {
            chunk_id: rank
            for rank, chunk_id in enumerate(sparse_ids, start=1)
        }
        dense_distance_by_id = {
            chunk_id: float(distance)
            for chunk_id, distance in zip(
                dense_ids,
                dense_distances,
                strict=True,
            )
        }
        sparse_score_by_id = {
            chunk_id: float(sparse_scores[index])
            for index, chunk_id in enumerate(ids)
        }
        stored_by_id = {
            chunk_id: {
                "document": documents[index],
                "metadata": metadatas[index],
            }
            for index, chunk_id in enumerate(ids)
        }

        results: list[RetrievedChunk] = []
        for chunk_id, rrf_score in fused[: min(top_k, len(fused))]:
            item = stored_by_id[chunk_id]
            results.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    document=item["document"],
                    metadata=item["metadata"],
                    rrf_score=rrf_score,
                    dense_rank=dense_ranks.get(chunk_id),
                    sparse_rank=sparse_ranks.get(chunk_id),
                    dense_distance=dense_distance_by_id.get(chunk_id),
                    sparse_score=sparse_score_by_id.get(chunk_id),
                )
            )
        return results
