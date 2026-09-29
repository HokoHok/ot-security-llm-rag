from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

from app.adequacy import (
    assess_evidence_adequacy,
    build_insufficient_evidence_response,
)
from app.generation import ChatProvider, generate_tutor_response
from app.retrieval import RetrievedChunk, tokenize
from app.schemas import GroundTruth, GroundTruthCase, TutorResponse

ANSWER_ALIGNMENT_THRESHOLD = 0.14
KNOWN_FACT_MATCH_THRESHOLD = 0.34
INFERENCE_MATCH_THRESHOLD = 0.34
UNKNOWN_MATCH_THRESHOLD = 0.12
FORBIDDEN_CLAIM_THRESHOLD = 0.58


class Retriever(Protocol):
    """Minimal retrieval interface used by the Ground Truth evaluator."""

    def retrieve(
        self,
        question: str,
        scenario: str,
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Return ranked chunks for one canonical question."""


EvaluationProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class ForbiddenClaimHit:
    """One rule-based potential match to a prohibited claim."""

    forbidden_claim: str
    generated_claim: str
    similarity: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "forbidden_claim": self.forbidden_claim,
            "generated_claim": self.generated_claim,
            "similarity": round(self.similarity, 4),
        }


@dataclass(frozen=True)
class CaseEvaluation:
    """Evaluation result for one Ground Truth question."""

    question_id: str
    topic: str
    question: str
    retrieved_chunk_ids: list[str]
    retrieved_evidence_ids: list[str]
    required_evidence_ids: list[str]
    missing_required_evidence_ids: list[str]
    retrieval_recall: float
    first_required_rank: int | None
    reciprocal_rank: float
    top1_required_hit: bool
    evidence_precision: float
    extra_evidence_ids: list[str]
    adequacy_status: str
    adequacy_requires_unknowns: bool
    adequacy_reasons: list[str]
    retrieval_pass: bool
    answer_pass: bool | None = None
    answer_alignment_pass: bool | None = None
    answer_similarity: float | None = None
    citation_pass: bool | None = None
    known_facts_pass: bool | None = None
    unknowns_pass: bool | None = None
    known_fact_coverage: float | None = None
    inference_coverage: float | None = None
    unknown_coverage: float | None = None
    forbidden_claim_hits: tuple[ForbiddenClaimHit, ...] = ()
    generation_error: str | None = None
    response: TutorResponse | None = None

    @property
    def passed(self) -> bool:
        if not self.retrieval_pass:
            return False
        return self.answer_pass is not False

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "topic": self.topic,
            "question": self.question,
            "passed": self.passed,
            "retrieval": {
                "passed": self.retrieval_pass,
                "recall": round(self.retrieval_recall, 4),
                "first_required_rank": self.first_required_rank,
                "reciprocal_rank": round(self.reciprocal_rank, 4),
                "top1_required_hit": self.top1_required_hit,
                "evidence_precision": round(
                    self.evidence_precision,
                    4,
                ),
                "extra_evidence_ids": self.extra_evidence_ids,
                "adequacy": {
                    "status": self.adequacy_status,
                    "requires_unknowns": self.adequacy_requires_unknowns,
                    "reasons": self.adequacy_reasons,
                },
                "retrieved_chunk_ids": self.retrieved_chunk_ids,
                "retrieved_evidence_ids": self.retrieved_evidence_ids,
                "required_evidence_ids": self.required_evidence_ids,
                "missing_required_evidence_ids": (
                    self.missing_required_evidence_ids
                ),
            },
            "answer": {
                "passed": self.answer_pass,
                "answer_alignment_pass": self.answer_alignment_pass,
                "answer_similarity": _rounded_optional(
                    self.answer_similarity
                ),
                "citation_pass": self.citation_pass,
                "known_facts_pass": self.known_facts_pass,
                "unknowns_pass": self.unknowns_pass,
                "known_fact_coverage": _rounded_optional(
                    self.known_fact_coverage
                ),
                "inference_coverage": _rounded_optional(
                    self.inference_coverage
                ),
                "unknown_coverage": _rounded_optional(
                    self.unknown_coverage
                ),
                "forbidden_claim_hits": [
                    hit.to_dict() for hit in self.forbidden_claim_hits
                ],
                "generation_error": self.generation_error,
                "response": (
                    self.response.model_dump()
                    if self.response is not None
                    else None
                ),
            },
        }


@dataclass(frozen=True)
class EvaluationReport:
    """Batch result for all selected Ground Truth cases."""

    scenario: str
    mode: str
    top_k: int
    cases: tuple[CaseEvaluation, ...]

    @property
    def passed_cases(self) -> int:
        return sum(case.passed for case in self.cases)

    @property
    def overall_pass(self) -> bool:
        return bool(self.cases) and self.passed_cases == len(self.cases)

    @property
    def mean_retrieval_recall(self) -> float:
        if not self.cases:
            return 0.0
        return sum(case.retrieval_recall for case in self.cases) / len(
            self.cases
        )

    @property
    def mean_reciprocal_rank(self) -> float:
        if not self.cases:
            return 0.0
        return sum(case.reciprocal_rank for case in self.cases) / len(
            self.cases
        )

    @property
    def top1_hit_rate(self) -> float:
        if not self.cases:
            return 0.0
        return sum(case.top1_required_hit for case in self.cases) / len(
            self.cases
        )

    @property
    def mean_evidence_precision(self) -> float:
        if not self.cases:
            return 0.0
        return sum(case.evidence_precision for case in self.cases) / len(
            self.cases
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "mode": self.mode,
            "top_k": self.top_k,
            "thresholds": {
                "answer_alignment": ANSWER_ALIGNMENT_THRESHOLD,
                "known_fact_match": KNOWN_FACT_MATCH_THRESHOLD,
                "inference_match": INFERENCE_MATCH_THRESHOLD,
                "unknown_match": UNKNOWN_MATCH_THRESHOLD,
                "forbidden_claim": FORBIDDEN_CLAIM_THRESHOLD,
            },
            "summary": {
                "overall_pass": self.overall_pass,
                "total_cases": len(self.cases),
                "passed_cases": self.passed_cases,
                "failed_cases": len(self.cases) - self.passed_cases,
                "mean_retrieval_recall": round(
                    self.mean_retrieval_recall,
                    4,
                ),
                "mean_reciprocal_rank": round(
                    self.mean_reciprocal_rank,
                    4,
                ),
                "top1_hit_rate": round(self.top1_hit_rate, 4),
                "mean_evidence_precision": round(
                    self.mean_evidence_precision,
                    4,
                ),
            },
            "cases": [case.to_dict() for case in self.cases],
        }


_NEGATION_OR_UNCERTAINTY_MARKERS = (
    "無法確認",
    "不能確認",
    "未能確認",
    "沒有證據",
    "無法證明",
    "不能證明",
    "不足以",
    "不代表",
    "不攜帶",
    "不會出現",
    "不由",
    "並未",
    "未送出",
    "沒有送出",
    "被拒絕",
    "未建立",
    "沒有建立",
    "沒有執行",
    "未執行",
    "不是",
    "不是真實",
    "僅為",
    "只是",
)
_UNCERTAINTY_MARKERS = (
    "無法確認",
    "不能確認",
    "未能確認",
    "證據不足",
    "不確定",
)
_SPACE_PATTERN = re.compile(r"\s+")


def _rounded_optional(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _formatted_optional(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "n/a"


def _formatted_check(value: bool | None) -> str:
    if value is None:
        return "n/a"
    return "PASS" if value else "FAIL"


def _unique_evidence_ids(retrieved: list[RetrievedChunk]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for result in retrieved:
        for evidence_id in result.evidence_ids:
            if evidence_id not in seen:
                ordered.append(evidence_id)
                seen.add(evidence_id)
    return ordered


def _normalized_text(text: str) -> str:
    return _SPACE_PATTERN.sub("", text).lower()


def _text_similarity(reference: str, candidate: str) -> float:
    reference_tokens = set(tokenize(reference))
    candidate_tokens = set(tokenize(candidate))
    if reference_tokens and candidate_tokens:
        overlap = len(reference_tokens & candidate_tokens)
        precision = overlap / len(candidate_tokens)
        recall = overlap / len(reference_tokens)
        token_f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
    else:
        token_f1 = 0.0

    character_ratio = SequenceMatcher(
        None,
        _normalized_text(reference),
        _normalized_text(candidate),
    ).ratio()
    return max(token_f1, character_ratio)


def _coverage(
    references: list[str],
    candidates: list[str],
    threshold: float,
) -> float:
    if not references:
        return 1.0
    if not candidates:
        return 0.0
    matched = sum(
        max(_text_similarity(reference, candidate) for candidate in candidates)
        >= threshold
        for reference in references
    )
    return matched / len(references)


def _is_negated_or_uncertain(text: str) -> bool:
    normalized = _normalized_text(text)
    return any(
        marker in normalized
        for marker in _NEGATION_OR_UNCERTAINTY_MARKERS
    )


def _is_uncertain(text: str) -> bool:
    normalized = _normalized_text(text)
    return any(marker in normalized for marker in _UNCERTAINTY_MARKERS)


def _is_source_ip_baseline_without_attribution(
    forbidden_claim: str,
    candidate: str,
) -> bool:
    """Check whether a source-IP baseline is reported without attribution.

    Similarity alone cannot distinguish a harmless baseline observation from
    the prohibited conclusion that the same source IP proves attribution.
    Keep this guard narrow so the evaluator remains conservative for other
    forbidden claims.
    """

    normalized_forbidden = _normalized_text(forbidden_claim)
    if "source_ip=10.10.60.20" not in normalized_forbidden:
        return False
    if not any(
        marker in normalized_forbidden
        for marker in ("單獨可確定", "足以證明", "可確定", "一定證明")
    ):
        return False

    normalized_candidate = _normalized_text(candidate)
    return not any(
        marker in normalized_candidate
        for marker in ("單獨", "足以", "可確定", "能確定", "一定", "證明")
    )


def find_forbidden_claim_hits(
    forbidden_claims: list[str],
    response: TutorResponse,
    threshold: float = FORBIDDEN_CLAIM_THRESHOLD,
) -> tuple[ForbiddenClaimHit, ...]:
    """Find likely affirmative prohibited claims using deterministic similarity.

    This deliberately excludes ``unknowns`` and negated/uncertain claims.
    Matches are review signals rather than a complete semantic proof.
    """

    candidates = [
        response.answer,
        *response.known_facts,
        *response.reasonable_inferences,
    ]
    hits: list[ForbiddenClaimHit] = []
    for candidate in candidates:
        if _is_negated_or_uncertain(candidate):
            continue
        for forbidden_claim in forbidden_claims:
            if _is_source_ip_baseline_without_attribution(
                forbidden_claim,
                candidate,
            ):
                continue
            similarity = _text_similarity(forbidden_claim, candidate)
            if similarity >= threshold:
                hits.append(
                    ForbiddenClaimHit(
                        forbidden_claim=forbidden_claim,
                        generated_claim=candidate,
                        similarity=similarity,
                    )
                )
    return tuple(hits)


def evaluate_retrieval(
    case: GroundTruthCase,
    retrieved: list[RetrievedChunk],
) -> CaseEvaluation:
    """Evaluate exact required-evidence coverage for one question."""

    retrieved_ids = _unique_evidence_ids(retrieved)
    retrieved_set = set(retrieved_ids)
    missing = [
        evidence_id
        for evidence_id in case.required_evidence_ids
        if evidence_id not in retrieved_set
    ]
    recall = (
        (len(case.required_evidence_ids) - len(missing))
        / len(case.required_evidence_ids)
    )
    required_set = set(case.required_evidence_ids)
    first_required_rank = next(
        (
            rank
            for rank, result in enumerate(retrieved, start=1)
            if required_set & set(result.evidence_ids)
        ),
        None,
    )
    reciprocal_rank = (
        1.0 / first_required_rank
        if first_required_rank is not None
        else 0.0
    )
    top1_required_hit = first_required_rank == 1
    relevant_retrieved = retrieved_set & required_set
    evidence_precision = (
        len(relevant_retrieved) / len(retrieved_set)
        if retrieved_set
        else 0.0
    )
    extra_evidence_ids = [
        evidence_id
        for evidence_id in retrieved_ids
        if evidence_id not in required_set
    ]
    adequacy = assess_evidence_adequacy(case.canonical_question, retrieved)
    return CaseEvaluation(
        question_id=case.question_id,
        topic=case.topic,
        question=case.canonical_question,
        retrieved_chunk_ids=[result.chunk_id for result in retrieved],
        retrieved_evidence_ids=retrieved_ids,
        required_evidence_ids=list(case.required_evidence_ids),
        missing_required_evidence_ids=missing,
        retrieval_recall=recall,
        first_required_rank=first_required_rank,
        reciprocal_rank=reciprocal_rank,
        top1_required_hit=top1_required_hit,
        evidence_precision=evidence_precision,
        extra_evidence_ids=extra_evidence_ids,
        adequacy_status=adequacy.status,
        adequacy_requires_unknowns=adequacy.require_unknowns,
        adequacy_reasons=list(adequacy.reasons),
        retrieval_pass=not missing,
    )


def evaluate_answer(
    case: GroundTruthCase,
    retrieval_result: CaseEvaluation,
    response: TutorResponse,
) -> CaseEvaluation:
    """Add deterministic answer checks to one retrieval evaluation."""

    citation_pass = set(response.evidence_ids) <= set(
        retrieval_result.retrieved_evidence_ids
    )
    answer_similarity = (
        _text_similarity(case.expected_answer, response.answer)
        if case.expected_answer is not None
        else None
    )
    answer_alignment_pass = (
        answer_similarity >= ANSWER_ALIGNMENT_THRESHOLD
        if answer_similarity is not None
        else True
    )
    if (
        case.expected_answer is not None
        and _is_uncertain(response.answer)
        and not _is_uncertain(case.expected_answer)
    ):
        answer_alignment_pass = False
    known_coverage = _coverage(
        case.expected_known_facts,
        response.known_facts,
        threshold=KNOWN_FACT_MATCH_THRESHOLD,
    )
    known_facts_pass = (
        not case.expected_known_facts or known_coverage > 0.0
    )
    inference_coverage = _coverage(
        case.acceptable_inferences,
        response.reasonable_inferences,
        threshold=INFERENCE_MATCH_THRESHOLD,
    )
    unknown_coverage = _coverage(
        case.expected_unknowns,
        response.unknowns,
        threshold=UNKNOWN_MATCH_THRESHOLD,
    )
    unknowns_pass = (
        unknown_coverage == 1.0
        if case.expected_unknowns
        else not response.unknowns
    )
    forbidden_hits = find_forbidden_claim_hits(
        case.forbidden_claims,
        response,
    )
    answer_pass = (
        citation_pass
        and answer_alignment_pass
        and known_facts_pass
        and unknowns_pass
        and not forbidden_hits
    )

    return CaseEvaluation(
        **{
            **retrieval_result.__dict__,
            "answer_pass": answer_pass,
            "answer_alignment_pass": answer_alignment_pass,
            "answer_similarity": answer_similarity,
            "citation_pass": citation_pass,
            "known_facts_pass": known_facts_pass,
            "unknowns_pass": unknowns_pass,
            "known_fact_coverage": known_coverage,
            "inference_coverage": inference_coverage,
            "unknown_coverage": unknown_coverage,
            "forbidden_claim_hits": forbidden_hits,
            "response": response,
        }
    )


def _generation_failure(
    retrieval_result: CaseEvaluation,
    error: Exception,
) -> CaseEvaluation:
    return CaseEvaluation(
        **{
            **retrieval_result.__dict__,
            "answer_pass": False,
            "generation_error": str(error),
        }
    )


def evaluate_ground_truth(
    ground_truth: GroundTruth,
    retriever: Retriever,
    top_k: int,
    chat_provider: ChatProvider | None = None,
    progress: EvaluationProgressCallback | None = None,
) -> EvaluationReport:
    """Evaluate all Ground Truth cases, optionally including live generation."""

    if top_k < 1:
        raise ValueError("top_k must be at least 1")

    notify = progress or (lambda _message: None)
    results: list[CaseEvaluation] = []
    total_cases = len(ground_truth.cases)
    for index, case in enumerate(ground_truth.cases, start=1):
        prefix = f"[EVALUATE {index}/{total_cases}] {case.question_id}"
        notify(f"{prefix} 正在檢索…")
        retrieved = retriever.retrieve(
            question=case.canonical_question,
            scenario=ground_truth.scenario,
            top_k=top_k,
        )
        retrieval_result = evaluate_retrieval(case, retrieved)
        if chat_provider is None:
            results.append(retrieval_result)
            status = "PASS" if retrieval_result.passed else "FAIL"
            notify(f"{prefix} 檢索完成：{status}")
            continue

        notify(f"{prefix} 正在呼叫 Llama…")
        try:
            response = (
                build_insufficient_evidence_response(
                    scenario=ground_truth.scenario,
                )
                if retrieval_result.adequacy_status == "insufficient"
                else generate_tutor_response(
                    question=case.canonical_question,
                    retrieved=retrieved,
                    chat_provider=chat_provider,
                    require_unknowns=(
                        retrieval_result.adequacy_requires_unknowns
                    ),
                    scenario=ground_truth.scenario,
                )
            )
        except (RuntimeError, ValueError) as exc:
            failure = _generation_failure(retrieval_result, exc)
            results.append(failure)
            notify(f"{prefix} 生成失敗：{exc}")
            continue
        evaluated = evaluate_answer(case, retrieval_result, response)
        results.append(evaluated)
        status = "PASS" if evaluated.passed else "FAIL"
        notify(f"{prefix} 回答評估完成：{status}")

    return EvaluationReport(
        scenario=ground_truth.scenario,
        mode=(
            "retrieval_and_generation"
            if chat_provider is not None
            else "retrieval_only"
        ),
        top_k=top_k,
        cases=tuple(results),
    )


def format_evaluation_report(report: EvaluationReport) -> str:
    """Format a concise Traditional-Chinese console report."""

    lines = [
        "[GROUND TRUTH EVALUATION]",
        f"scenario: {report.scenario}",
        f"mode: {report.mode}",
        f"top_k: {report.top_k}",
        "",
    ]
    for case in report.cases:
        status = "PASS" if case.passed else "FAIL"
        covered = len(case.required_evidence_ids) - len(
            case.missing_required_evidence_ids
        )
        lines.extend(
            [
                f"[{status}] {case.question_id} {case.topic}",
                f"  問題：{case.question}",
                (
                    "  必要證據："
                    f"{covered}/{len(case.required_evidence_ids)} "
                    f"(recall={case.retrieval_recall:.2f}, "
                    f"first_rank={case.first_required_rank or 'n/a'}, "
                    f"precision={case.evidence_precision:.2f})"
                ),
                "  chunks：" + ", ".join(case.retrieved_chunk_ids),
            ]
        )
        if case.missing_required_evidence_ids:
            lines.append(
                "  缺少：" + ", ".join(case.missing_required_evidence_ids)
            )
        if case.answer_pass is not None:
            lines.append(
                "  回答檢查："
                f"answer={_formatted_check(case.answer_alignment_pass)}, "
                f"citation={_formatted_check(case.citation_pass)}, "
                f"known_facts={_formatted_check(case.known_facts_pass)}, "
                f"unknowns={_formatted_check(case.unknowns_pass)}, "
                f"forbidden_hits={len(case.forbidden_claim_hits)}"
            )
            lines.append(
                "  分類覆蓋（參考）："
                f"answer={_formatted_optional(case.answer_similarity)}, "
                f"known={_formatted_optional(case.known_fact_coverage)}, "
                f"inference={_formatted_optional(case.inference_coverage)}, "
                f"unknown={_formatted_optional(case.unknown_coverage)}"
            )
        if case.generation_error:
            lines.append(f"  生成錯誤：{case.generation_error}")
        for hit in case.forbidden_claim_hits:
            lines.append(
                "  禁止主張疑似命中："
                f"{hit.generated_claim} "
                f"(score={hit.similarity:.2f})"
            )
        lines.append("")

    overall = "PASS" if report.overall_pass else "FAIL"
    lines.extend(
        [
            "[SUMMARY]",
            f"overall: {overall}",
            f"passed: {report.passed_cases}/{len(report.cases)}",
            f"mean retrieval recall: {report.mean_retrieval_recall:.2f}",
            f"mean reciprocal rank: {report.mean_reciprocal_rank:.2f}",
            f"top-1 required hit rate: {report.top1_hit_rate:.2f}",
            f"mean evidence precision: {report.mean_evidence_precision:.2f}",
        ]
    )
    return "\n".join(lines)


def write_evaluation_report(
    report: EvaluationReport,
    path: Path,
) -> Path:
    """Write a UTF-8 JSON report and return its resolved path."""

    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return resolved
