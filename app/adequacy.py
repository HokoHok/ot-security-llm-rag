from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal, Sequence

from app.retrieval import RetrievedChunk
from app.scenario_profiles import get_scenario_profile
from app.schemas import TutorResponse

AdequacyStatus = Literal["sufficient", "uncertain", "insufficient"]


@dataclass(frozen=True)
class EvidenceAdequacy:
    """Deterministic evidence-support decision for one retrieval result.

    ``sufficient`` means that the retrieved context can support a bounded
    response.  It may still require an ``unknowns`` item when the context
    explicitly documents that a requested detail was not collected.

    Raw dense and sparse scores are intentionally retained for diagnostics.
    RRF is only a rank-fusion score and is never used as an adequacy threshold.
    """

    status: AdequacyStatus
    require_unknowns: bool
    reasons: tuple[str, ...]
    best_dense_distance: float | None
    best_sparse_score: float | None


@dataclass(frozen=True)
class _FacetRule:
    name: str
    question_patterns: tuple[str, ...]
    support_patterns: tuple[str, ...]
    limitation_patterns: tuple[str, ...]


_EVIDENCE_ID_PATTERN = re.compile(
    r"(?<![A-Z0-9])[A-Z][A-Z0-9]*-[A-Z][0-9]{2,3}(?![A-Z0-9])",
    re.IGNORECASE,
)

# These rules cover explicit, testable fields for which a host-name match alone
# must not be treated as an answer.  They are deliberately small and auditable;
# semantic topicality is handled separately by dense retrieval.
_FACET_RULES = (
    _FacetRule(
        name="bios",
        question_patterns=(r"\bbios\b", r"韌體版本", r"firmware version"),
        support_patterns=(r"\bbios\b", r"韌體版本", r"firmware version"),
        limitation_patterns=(
            r"(?:缺少|沒有|未提供|無法確認).{0,20}(?:bios|韌體)",
        ),
    ),
    _FacetRule(
        name="cve_or_vulnerability",
        question_patterns=(
            r"\bcve(?:-\d{4}-\d+)?\b",
            r"哪(?:一|個|種).{0,8}漏洞",
            r"漏洞(?:名稱|編號)",
        ),
        support_patterns=(r"\bcve-\d{4}-\d+\b",),
        limitation_patterns=(
            r"(?:沒有|未|無).{0,16}(?:記載|指出|證明).{0,16}漏洞",
            r"(?:漏洞|cve).{0,16}(?:不明|未知|無法確認|證據不足)",
        ),
    ),
    _FacetRule(
        name="credential_acquisition",
        question_patterns=(
            r"(?:偷到|竊取|窃取|取得).{0,16}(?:密碼|密码|憑證|credential)",
            r"(?:密碼|密码|憑證|credential).{0,16}(?:偷到|竊取|窃取|取得)",
        ),
        support_patterns=(
            r"(?:觀測到|紀錄顯示|記錄顯示).{0,16}(?:credential theft|密碼竊取)",
        ),
        limitation_patterns=(
            r"demo assumption",
            r"(?:否定|沒有|未).{0,20}(?:credential theft|password dumping|密碼竊取)",
            r"不能回答密碼如何被偷",
        ),
    ),
    _FacetRule(
        name="exact_time_or_session_duration",
        question_patterns=(
            r"幾點(?:幾分)?",
            r"精確.{0,6}時間",
            r"時間戳",
            r"何時.{0,12}(?:建立|連線|執行)",
            r"(?:session|連線).{0,12}(?:多久|持續時間|時長)",
        ),
        support_patterns=(
            r"\b20\d{2}-\d{2}-\d{2}[tT ][0-2]\d:[0-5]\d",
            r"\b[0-2]?\d:[0-5]\d(?::[0-5]\d)?\b",
        ),
        limitation_patterns=(
            r"(?:缺少|沒有|未提供).{0,24}(?:時間|時間戳|session|連線)",
            r"(?:事後|執行後).{0,20}(?:snapshot|快照|狀態)",
            r"(?:不能|無法).{0,24}(?:歷史連線|先前存取|session)",
        ),
    ),
    _FacetRule(
        name="packet_or_firewall_telemetry",
        question_patterns=(
            r"封包",
            r"\bpcap\b",
            r"\bpacket(?:s)?\b",
            r"防火牆.{0,8}(?:事件|紀錄|記錄|log)",
            r"每一個.{0,8}路由",
        ),
        support_patterns=(
            r"source_type.{0,4}(?:packet_capture|firewall_log)",
            r"(?:封包|pcap|firewall log).{0,16}(?:觀測到|記錄顯示|紀錄顯示)",
            r"pcap files are present",
            r"recorded file hashes/sizes",
        ),
        limitation_patterns=(
            r"(?:缺少|沒有|未提供|無).{0,24}(?:封包|pcap|firewall log|防火牆)",
            r"(?:封包|pcap|firewall log|防火牆).{0,24}(?:證據不足|無法確認)",
            r"tshark is not installed.{0,60}did not decode",
            r"pcap.{0,80}did not decode iec-104 fields",
        ),
    ),
    _FacetRule(
        name="password_value",
        question_patterns=(
            r"原始密碼",
            r"密碼內容",
            r"密碼是什麼",
            r"password value",
            r"plaintext password",
        ),
        # Raw credentials are outside this teaching dataset's safe scope.
        support_patterns=(),
        limitation_patterns=(
            r"(?:不能|無法).{0,20}(?:回答|確認).{0,16}密碼",
            r"(?:沒有|未).{0,20}(?:密碼|password).{0,12}(?:內容|值|collection)",
            r"(?:否定|沒有|未).{0,20}(?:credential theft|password dumping|密碼竊取)",
        ),
    ),
    _FacetRule(
        name="business_loss_or_downtime",
        question_patterns=(
            r"營業損失",
            r"營運損失",
            r"損失金額",
            r"停機時間",
            r"\bbusiness (?:loss|impact)\b",
            r"\bdowntime\b",
        ),
        support_patterns=(
            r"(?:nt\$|twd|usd|新臺幣|新台幣|美元)\s*[0-9,]+",
            r"(?:停機|downtime).{0,16}\d+(?:\.\d+)?\s*(?:分鐘|小時|天|minutes?|hours?|days?)",
        ),
        limitation_patterns=(
            r"(?:缺少|沒有|未提供|無法確認).{0,24}(?:營業損失|營運損失|損失金額|停機時間|downtime)",
        ),
    ),
)


def _finite_metric(result: RetrievedChunk, name: str) -> float | None:
    """Read a raw metric from a new field or legacy metadata."""

    value = getattr(result, name, None)
    if value is None:
        value = result.metadata.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _best_raw_scores(
    retrieved: Sequence[RetrievedChunk],
) -> tuple[float | None, float | None]:
    dense_values = [
        value
        for result in retrieved
        if (value := _finite_metric(result, "dense_distance")) is not None
    ]
    sparse_values = [
        value
        for result in retrieved
        if (value := _finite_metric(result, "sparse_score")) is not None
    ]
    return (
        min(dense_values) if dense_values else None,
        max(sparse_values) if sparse_values else None,
    )


def _context_text(retrieved: Sequence[RetrievedChunk]) -> str:
    blocks: list[str] = []
    for result in retrieved:
        blocks.append(result.document)
        for field in ("title", "keywords", "source_type", "evidence_ids"):
            value = result.metadata.get(field)
            if value is not None:
                blocks.append(str(value))
    return "\n".join(blocks).casefold()


def _matches_any(text: str, patterns: Sequence[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _requested_facets(question: str) -> tuple[_FacetRule, ...]:
    normalized = question.casefold()
    return tuple(
        rule
        for rule in _FACET_RULES
        if _matches_any(normalized, rule.question_patterns)
    )


def _is_industroyer2_relay_path_question(question: str, context: str) -> bool:
    """Identify a host-path question that merely happens to say ``封包``.

    A request to distinguish Kali direct delivery from a via-ENG Relay hand-off
    is answerable from cross-host summaries and Relay state.  It is different
    from requesting decoded PCAP fields, packet contents, or firewall details.
    """

    normalized = question.casefold()
    asks_path = (
        "kali" in normalized
        and "relay" in normalized
        and any(
            marker in normalized
            for marker in (
                "via-eng",
                "via eng",
                "direct",
                "直接",
                "經由",
                "透過",
            )
        )
    )
    recorded_handoff = (
        "kali requested a command through eng relay" in context
        and "eng01 sent the iec-104 command to ot-server01" in context
    )
    asks_packet_detail = any(
        marker in normalized
        for marker in ("asdu", "type id", "ioa", "欄位", "內容", "payload")
    )
    return asks_path and recorded_handoff and not asks_packet_detail


def _is_industroyer2_route_or_firewall_detail_question(
    question: str,
) -> bool:
    """Identify a request for telemetry the IND2 corpus does not contain.

    The normalized evidence records a bounded Relay hand-off, not each network
    hop or a firewall allow/deny decision.  Those are different questions and
    must not be inferred from the hand-off summary.
    """

    normalized = question.casefold()
    asks_endpoints = "kali" in normalized and "ot-server01" in normalized
    asks_route = any(
        marker in normalized
        for marker in (
            "route",
            "routing",
            "every hop",
            "\u6bcf\u4e00\u8df3",
            "\u8def\u7531",
        )
    )
    asks_firewall = any(
        marker in normalized
        for marker in (
            "firewall",
            "allow rule",
            "\u9632\u706b\u7246",
            "\u653e\u884c",
        )
    )
    return asks_endpoints and (asks_route or asks_firewall)


def _is_frostygoop_lab_pcap_context(question: str, context: str) -> bool:
    """Recognize FrostyGoop's normalized, explicitly bounded PCAP evidence.

    Unlike an unparsed packet-file question, this scenario's normalized records
    explicitly correlate a safe Lab Task JSON with decoded localhost Modbus
    requests and state that capture does not prove device acceptance.  Let the
    model explain that bounded fact while requiring an ``unknowns`` entry.
    """

    normalized = question.casefold()
    return (
        "frosty-n" in context
        and "pcap" in normalized
        and ("modbus" in context or "tcp/1502" in context)
    )


def _is_frostygoop_impact_boundary_question(question: str, context: str) -> bool:
    """Recognize requests that must retain the Lab acceptance/impact boundary."""

    normalized = question.casefold()
    asks_impact = any(
        marker in normalized
        for marker in (
            "device acceptance",
            "physical impact",
            "plc",
            "\u5be6\u9ad4\u8a2d\u5099\u5f71\u97ff",
            "\u5be6\u9ad4\u5f71\u97ff",
            "\u7269\u7406\u5f71\u97ff",
            "設備接受",
            "實體影響",
            "實體設備影響",
            "物理影響",
            "製程",
            "寫進設備",
        )
    )
    return (
        asks_impact
        and "frosty-n" in context
        and (
            "does not prove device acceptance" in context
            or "not claimed as observed real frostygoop behavior" in context
        )
    )


def assess_evidence_adequacy(
    question: str,
    retrieved: Sequence[RetrievedChunk],
    *,
    dense_strong_max_distance: float = 0.65,
    dense_weak_min_distance: float = 0.70,
    sparse_strong_min_score: float = 3.5,
) -> EvidenceAdequacy:
    """Classify whether retrieved context can support a bounded answer.

    The default score bands are provisional values for the current Ryuk
    corpus and EmbeddingGemma index.  Callers should keep them configurable
    and recalibrate them whenever the embedding model or index fingerprint
    changes.
    """

    if not question.strip():
        raise ValueError("Question must not be empty")
    if dense_strong_max_distance < 0 or dense_weak_min_distance < 0:
        raise ValueError("Dense distance thresholds must not be negative")
    if dense_strong_max_distance >= dense_weak_min_distance:
        raise ValueError(
            "dense_strong_max_distance must be lower than "
            "dense_weak_min_distance"
        )
    if sparse_strong_min_score < 0:
        raise ValueError("Sparse score threshold must not be negative")

    best_dense, best_sparse = _best_raw_scores(retrieved)
    requested_ids = {
        match.upper() for match in _EVIDENCE_ID_PATTERN.findall(question)
    }
    retrieved_ids = {
        evidence_id.upper()
        for result in retrieved
        for evidence_id in result.evidence_ids
    }

    if requested_ids:
        if requested_ids <= retrieved_ids:
            return EvidenceAdequacy(
                status="sufficient",
                require_unknowns=False,
                reasons=("exact_evidence_id_hit",),
                best_dense_distance=best_dense,
                best_sparse_score=best_sparse,
            )
        return EvidenceAdequacy(
            status="insufficient",
            require_unknowns=True,
            reasons=("requested_evidence_id_not_found",),
            best_dense_distance=best_dense,
            best_sparse_score=best_sparse,
        )

    if not retrieved:
        return EvidenceAdequacy(
            status="insufficient",
            require_unknowns=True,
            reasons=("no_retrieved_chunks",),
            best_dense_distance=None,
            best_sparse_score=None,
        )

    context = _context_text(retrieved)
    if _is_industroyer2_route_or_firewall_detail_question(question):
        return EvidenceAdequacy(
            status="insufficient",
            require_unknowns=True,
            reasons=("requested_facet_not_supported:route_or_firewall_path",),
            best_dense_distance=best_dense,
            best_sparse_score=best_sparse,
        )
    # This scenario explicitly contains a Lab-only device-acceptance boundary.
    # Check it before generic requested-facet rules so a question about physical
    # impact receives the bounded, evidence-grounded unknown response rather
    # than a generic "no information" redirect.
    if (
        _is_frostygoop_impact_boundary_question(question, context)
        and not _is_frostygoop_lab_pcap_context(question, context)
    ):
        return EvidenceAdequacy(
            status="sufficient",
            require_unknowns=True,
            reasons=("explicit_context_limitation:frostygoop_lab_impact_boundary",),
            best_dense_distance=best_dense,
            best_sparse_score=best_sparse,
        )
    requested_facets = _requested_facets(question)
    unsupported_facets: list[str] = []
    explicitly_limited_facets: list[str] = []
    for rule in requested_facets:
        if (
            rule.name == "packet_or_firewall_telemetry"
            and _is_industroyer2_relay_path_question(question, context)
        ):
            continue
        if (
            rule.name == "packet_or_firewall_telemetry"
            and _is_frostygoop_lab_pcap_context(question, context)
        ):
            explicitly_limited_facets.append(rule.name)
            continue
        if _matches_any(context, rule.limitation_patterns):
            explicitly_limited_facets.append(rule.name)
        elif not rule.support_patterns or not _matches_any(
            context,
            rule.support_patterns,
        ):
            unsupported_facets.append(rule.name)

    if unsupported_facets:
        return EvidenceAdequacy(
            status="insufficient",
            require_unknowns=True,
            reasons=tuple(
                f"requested_facet_not_supported:{name}"
                for name in unsupported_facets
            ),
            best_dense_distance=best_dense,
            best_sparse_score=best_sparse,
        )

    if explicitly_limited_facets:
        return EvidenceAdequacy(
            status="sufficient",
            require_unknowns=True,
            reasons=tuple(
                f"explicit_context_limitation:{name}"
                for name in explicitly_limited_facets
            ),
            best_dense_distance=best_dense,
            best_sparse_score=best_sparse,
        )

    # A strong semantic match is independently sufficient.  Requiring a
    # keyword overlap here would incorrectly reject natural paraphrases.
    if best_dense is not None and best_dense <= dense_strong_max_distance:
        return EvidenceAdequacy(
            status="sufficient",
            require_unknowns=False,
            reasons=("strong_dense_semantic_match",),
            best_dense_distance=best_dense,
            best_sparse_score=best_sparse,
        )

    if best_sparse is not None and best_sparse >= sparse_strong_min_score:
        return EvidenceAdequacy(
            status="sufficient",
            require_unknowns=False,
            reasons=("strong_sparse_match",),
            best_dense_distance=best_dense,
            best_sparse_score=best_sparse,
        )

    weak_dense = (
        best_dense is not None and best_dense >= dense_weak_min_distance
    )
    weak_sparse = best_sparse is None or best_sparse <= 0
    if weak_dense and weak_sparse:
        return EvidenceAdequacy(
            status="insufficient",
            require_unknowns=True,
            reasons=("weak_dense_and_sparse_signals",),
            best_dense_distance=best_dense,
            best_sparse_score=best_sparse,
        )

    return EvidenceAdequacy(
        status="uncertain",
        require_unknowns=True,
        reasons=("mixed_or_unavailable_retrieval_signals",),
        best_dense_distance=best_dense,
        best_sparse_score=best_sparse,
    )


def build_insufficient_evidence_response(
    scenario: str = "ryuk",
    question: str = "",
) -> TutorResponse:
    """Return a stable response without asking Llama to fill missing facts."""

    profile = get_scenario_profile(scenario)
    if (
        profile.key == "industroyer2"
        and _is_industroyer2_route_or_firewall_detail_question(question)
    ):
        return TutorResponse(
            answer=(
                "現有 Industroyer2-like 情境資料沒有 Kali 到 OT-SERVER01 "
                "每一跳路由或防火牆放行紀錄，因此不能重建完整網路路徑。"
            ),
            known_facts=[],
            reasonable_inferences=[],
            unknowns=[
                "現有資料只能支持已記錄的 direct 或 via-ENG Relay 事件關係，"
                "無法確認每一跳路由與防火牆決策。"
            ],
            evidence_ids=[],
            next_hint=(
                "請改以比較 direct case 與 via-ENG Relay case 的來源 IP、"
                "Relay 紀錄及 Server command 證據。"
            ),
        )
    return TutorResponse(
        answer=f"目前的 {profile.display_name} 情境證據不足，無法確認這項資訊。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=["現有檢索內容未提供足以回答此問題的直接證據。"],
        evidence_ids=[],
        next_hint=(
            f"請將問題聚焦於 {profile.suggested_topics}。"
        ),
    )


__all__ = [
    "AdequacyStatus",
    "EvidenceAdequacy",
    "assess_evidence_adequacy",
    "build_insufficient_evidence_response",
]
