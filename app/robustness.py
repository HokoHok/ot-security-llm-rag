from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.adequacy import (
    EvidenceAdequacy,
    assess_evidence_adequacy,
    build_insufficient_evidence_response,
)
from app.evaluation import ForbiddenClaimHit, find_forbidden_claim_hits
from app.generation import ChatProvider, ResponseMode, generate_tutor_response
from app.retrieval import RetrievedChunk
from app.schemas import RobustnessDataset, RobustnessQuestion, TutorResponse
from app.scope import (
    build_scope_response,
    classify_question_scope,
    normalize_question_identifiers,
)


class Retriever(Protocol):
    """Minimal retrieval interface used by the robustness evaluator."""

    def retrieve(
        self,
        question: str,
        scenario: str,
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Return ranked chunks for one question."""


RobustnessProgressCallback = Callable[[str], None]


def _unique_evidence_ids(retrieved: list[RetrievedChunk]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for result in retrieved:
        for evidence_id in result.evidence_ids:
            if evidence_id not in seen:
                ordered.append(evidence_id)
                seen.add(evidence_id)
    return ordered


@dataclass(frozen=True)
class RobustnessCaseResult:
    """Deterministic checks for one robustness question."""

    question_id: str
    category: str
    question: str
    expected_scope: str
    detected_scope: str
    scope_pass: bool
    retrieved_chunk_ids: list[str]
    retrieved_evidence_ids: list[str]
    required_evidence_ids: list[str]
    missing_required_evidence_ids: list[str]
    retrieval_pass: bool
    evidence_recall: float
    first_required_rank: int | None
    reciprocal_rank: float
    top1_required_hit: bool | None
    evidence_precision: float | None
    extra_evidence_ids: list[str]
    adequacy_status: str | None
    adequacy_requires_unknowns: bool | None
    adequacy_reasons: list[str]
    expected_unknowns: bool
    required_answer_terms: list[str]
    missing_answer_terms: list[str] | None = None
    answer_terms_pass: bool | None = None
    unknowns_pass: bool | None = None
    citation_pass: bool | None = None
    forbidden_claim_hits: tuple[ForbiddenClaimHit, ...] = ()
    answer_pass: bool | None = None
    generation_error: str | None = None
    response: TutorResponse | None = None

    @property
    def passed(self) -> bool:
        if not self.scope_pass or not self.retrieval_pass:
            return False
        return self.answer_pass is not False

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "category": self.category,
            "question": self.question,
            "passed": self.passed,
            "scope": {
                "passed": self.scope_pass,
                "expected": self.expected_scope,
                "detected": self.detected_scope,
            },
            "retrieval": {
                "passed": self.retrieval_pass,
                "retrieved_chunk_ids": self.retrieved_chunk_ids,
                "retrieved_evidence_ids": self.retrieved_evidence_ids,
                "required_evidence_ids": self.required_evidence_ids,
                "missing_required_evidence_ids": (
                    self.missing_required_evidence_ids
                ),
                "evidence_recall": round(self.evidence_recall, 4),
                "first_required_rank": self.first_required_rank,
                "reciprocal_rank": round(self.reciprocal_rank, 4),
                "top1_required_hit": self.top1_required_hit,
                "evidence_precision": (
                    round(self.evidence_precision, 4)
                    if self.evidence_precision is not None
                    else None
                ),
                "extra_evidence_ids": self.extra_evidence_ids,
                "adequacy": {
                    "status": self.adequacy_status,
                    "requires_unknowns": self.adequacy_requires_unknowns,
                    "reasons": self.adequacy_reasons,
                },
            },
            "answer": {
                "passed": self.answer_pass,
                "expected_unknowns": self.expected_unknowns,
                "required_answer_terms": self.required_answer_terms,
                "missing_answer_terms": self.missing_answer_terms or [],
                "answer_terms_pass": self.answer_terms_pass,
                "unknowns_pass": self.unknowns_pass,
                "citation_pass": self.citation_pass,
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
class RobustnessReport:
    """Batch result for a selected robustness question set."""

    scenario: str
    mode: str
    top_k: int
    cases: tuple[RobustnessCaseResult, ...]

    @property
    def passed_cases(self) -> int:
        return sum(case.passed for case in self.cases)

    @property
    def overall_pass(self) -> bool:
        return bool(self.cases) and self.passed_cases == len(self.cases)

    def category_summary(self) -> dict[str, dict[str, int]]:
        totals = Counter(case.category for case in self.cases)
        passed = Counter(case.category for case in self.cases if case.passed)
        return {
            category: {
                "passed": passed[category],
                "total": total,
                "failed": total - passed[category],
            }
            for category, total in sorted(totals.items())
        }

    @property
    def ranked_cases(self) -> tuple[RobustnessCaseResult, ...]:
        """Return cases whose retrieval target contains evidence IDs."""

        return tuple(case for case in self.cases if case.required_evidence_ids)

    @property
    def mean_reciprocal_rank(self) -> float:
        cases = self.ranked_cases
        if not cases:
            return 0.0
        return sum(case.reciprocal_rank for case in cases) / len(cases)

    @property
    def top1_hit_rate(self) -> float:
        cases = self.ranked_cases
        if not cases:
            return 0.0
        return sum(case.top1_required_hit is True for case in cases) / len(
            cases
        )

    @property
    def mean_evidence_precision(self) -> float:
        values = [
            case.evidence_precision
            for case in self.ranked_cases
            if case.evidence_precision is not None
        ]
        return sum(values) / len(values) if values else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "mode": self.mode,
            "top_k": self.top_k,
            "summary": {
                "overall_pass": self.overall_pass,
                "total_cases": len(self.cases),
                "passed_cases": self.passed_cases,
                "failed_cases": len(self.cases) - self.passed_cases,
                "by_category": self.category_summary(),
                "mean_reciprocal_rank": round(
                    self.mean_reciprocal_rank,
                    4,
                ),
                "top1_required_hit_rate": round(
                    self.top1_hit_rate,
                    4,
                ),
                "mean_evidence_precision": round(
                    self.mean_evidence_precision,
                    4,
                ),
            },
            "cases": [case.to_dict() for case in self.cases],
        }


def _evaluate_retrieval(
    question: RobustnessQuestion,
    retrieved: list[RetrievedChunk],
    detected_scope: str,
    adequacy: EvidenceAdequacy | None,
) -> RobustnessCaseResult:
    evidence_ids = _unique_evidence_ids(retrieved)
    evidence_set = set(evidence_ids)
    missing = [
        evidence_id
        for evidence_id in question.required_evidence_ids
        if evidence_id not in evidence_set
    ]
    scope_pass = detected_scope == question.expected_scope
    required_set = set(question.required_evidence_ids)
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
    evidence_recall = (
        (len(required_set) - len(missing)) / len(required_set)
        if required_set
        else 1.0
    )
    evidence_precision = (
        len(evidence_set & required_set) / len(evidence_set)
        if required_set and evidence_set
        else None
    )
    extra_evidence_ids = [
        evidence_id
        for evidence_id in evidence_ids
        if evidence_id not in required_set
    ]
    retrieval_pass = (
        not retrieved and not question.required_evidence_ids
        if question.expected_scope != "in_scope"
        else not missing
    )
    return RobustnessCaseResult(
        question_id=question.question_id,
        category=question.category,
        question=question.question,
        expected_scope=question.expected_scope,
        detected_scope=detected_scope,
        scope_pass=scope_pass,
        retrieved_chunk_ids=[result.chunk_id for result in retrieved],
        retrieved_evidence_ids=evidence_ids,
        required_evidence_ids=list(question.required_evidence_ids),
        missing_required_evidence_ids=missing,
        retrieval_pass=retrieval_pass,
        evidence_recall=evidence_recall,
        first_required_rank=first_required_rank,
        reciprocal_rank=reciprocal_rank,
        top1_required_hit=(
            first_required_rank == 1 if required_set else None
        ),
        evidence_precision=evidence_precision,
        extra_evidence_ids=extra_evidence_ids,
        adequacy_status=adequacy.status if adequacy is not None else None,
        adequacy_requires_unknowns=(
            adequacy.require_unknowns if adequacy is not None else None
        ),
        adequacy_reasons=(
            list(adequacy.reasons) if adequacy is not None else []
        ),
        expected_unknowns=question.expected_unknowns,
        required_answer_terms=list(question.required_answer_terms),
        missing_answer_terms=[],
    )


def _evaluate_answer(
    question: RobustnessQuestion,
    result: RobustnessCaseResult,
    response: TutorResponse,
) -> RobustnessCaseResult:
    unknowns_pass = bool(response.unknowns) == question.expected_unknowns
    normalized_answer = response.answer.casefold()
    missing_answer_terms = [
        term
        for term in question.required_answer_terms
        if term.casefold() not in normalized_answer
    ]
    answer_terms_pass = not missing_answer_terms
    citation_pass = set(response.evidence_ids) <= set(
        result.retrieved_evidence_ids
    )
    forbidden_hits = find_forbidden_claim_hits(
        question.forbidden_claims,
        response,
    )
    answer_pass = (
        unknowns_pass
        and answer_terms_pass
        and citation_pass
        and not forbidden_hits
    )
    return RobustnessCaseResult(
        **{
            **result.__dict__,
            "unknowns_pass": unknowns_pass,
            "missing_answer_terms": missing_answer_terms,
            "answer_terms_pass": answer_terms_pass,
            "citation_pass": citation_pass,
            "forbidden_claim_hits": forbidden_hits,
            "answer_pass": answer_pass,
            "response": response,
        }
    )


def evaluate_robustness(
    dataset: RobustnessDataset,
    retriever: Retriever,
    top_k: int,
    chat_provider: ChatProvider | None = None,
    progress: RobustnessProgressCallback | None = None,
    response_mode: ResponseMode = "hint",
) -> RobustnessReport:
    """Evaluate scope routing, retrieval, and optional live generation."""

    if top_k < 1:
        raise ValueError("top_k must be at least 1")

    notify = progress or (lambda _message: None)
    results: list[RobustnessCaseResult] = []
    total = len(dataset.questions)
    for index, question in enumerate(dataset.questions, start=1):
        prefix = f"[ROBUSTNESS {index}/{total}] {question.question_id}"
        processing_question = normalize_question_identifiers(
            question.question
        )
        detected_scope = classify_question_scope(
            processing_question,
            scenario=dataset.scenario,
        )
        if detected_scope != "in_scope":
            retrieved: list[RetrievedChunk] = []
            notify(f"{prefix} scope={detected_scope}，略過檢索")
        else:
            notify(f"{prefix} 正在檢索")
            retrieved = retriever.retrieve(
                question=processing_question,
                scenario=dataset.scenario,
                top_k=top_k,
            )
        adequacy = (
            assess_evidence_adequacy(processing_question, retrieved)
            if detected_scope == "in_scope"
            else None
        )

        result = _evaluate_retrieval(
            question,
            retrieved,
            detected_scope,
            adequacy,
        )
        if chat_provider is None:
            results.append(result)
            continue

        try:
            response = (
                build_scope_response(detected_scope, scenario=dataset.scenario)
                if detected_scope != "in_scope"
                else build_insufficient_evidence_response(
                    scenario=dataset.scenario,
                    question=processing_question,
                )
                if adequacy is not None
                and adequacy.status == "insufficient"
                else generate_tutor_response(
                    question=processing_question,
                    retrieved=retrieved,
                    chat_provider=chat_provider,
                    require_unknowns=(
                        adequacy.require_unknowns
                        if adequacy is not None
                        else False
                    ),
                    scenario=dataset.scenario,
                    response_mode=response_mode,
                )
            )
            result = _evaluate_answer(question, result, response)
        except (RuntimeError, ValueError) as exc:
            result = RobustnessCaseResult(
                **{
                    **result.__dict__,
                    "answer_pass": False,
                    "generation_error": str(exc),
                }
            )
        results.append(result)

    return RobustnessReport(
        scenario=dataset.scenario,
        mode=(
            "retrieval_and_generation"
            if chat_provider is not None
            else "retrieval_only"
        ),
        top_k=top_k,
        cases=tuple(results),
    )


def format_robustness_report(report: RobustnessReport) -> str:
    """Format a concise Traditional-Chinese console report."""

    lines = [
        f"[{report.scenario.upper()} ROBUSTNESS EVALUATION]",
        f"mode: {report.mode}",
        f"top_k: {report.top_k}",
        "",
    ]
    for case in report.cases:
        status = "PASS" if case.passed else "FAIL"
        missing = ", ".join(case.missing_required_evidence_ids) or "無"
        missing_terms = (
            ", ".join(case.missing_answer_terms or []) or "無"
            if case.answer_pass is not None
            else "n/a"
        )
        lines.append(
            f"[{status}] {case.question_id} {case.category} | "
            f"scope={case.detected_scope} | missing_evidence={missing} | "
            f"first_rank={case.first_required_rank or 'n/a'} | "
            "precision="
            f"{case.evidence_precision if case.evidence_precision is not None else 'n/a'} | "
            f"missing_answer_terms={missing_terms}"
        )
        if case.generation_error:
            lines.append(f"  生成錯誤：{case.generation_error}")

    lines.extend(["", "[CATEGORY SUMMARY]"])
    for category, summary in report.category_summary().items():
        lines.append(
            f"{category}: {summary['passed']}/{summary['total']} passed"
        )
    overall = "PASS" if report.overall_pass else "FAIL"
    lines.extend(
        [
            "",
            f"overall: {overall}",
            f"passed: {report.passed_cases}/{len(report.cases)}",
            f"mean reciprocal rank: {report.mean_reciprocal_rank:.2f}",
            f"top-1 required hit rate: {report.top1_hit_rate:.2f}",
            f"mean evidence precision: {report.mean_evidence_precision:.2f}",
        ]
    )
    return "\n".join(lines)


def write_robustness_report(
    report: RobustnessReport,
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
