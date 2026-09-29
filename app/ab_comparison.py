from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from app.generation import (
    ChatProvider,
    OllamaCallMetrics,
    ResponseMode,
    allowed_evidence_ids,
    build_response_schema,
    build_retrieved_context,
    generate_tutor_response,
)
from app.retrieval import RetrievedChunk
from app.schemas import TutorResponse


@dataclass(frozen=True)
class ABComparison:
    """One preliminary same-model comparison using shared student context."""

    scenario: str
    question: str
    student_context_path: Path
    student_context: str
    baseline_response: TutorResponse
    rag_response: TutorResponse
    retrieved: tuple[RetrievedChunk, ...]
    ollama_metrics: tuple[OllamaCallMetrics, ...]


def load_ab_student_context(project_root: Path, scenario: str) -> tuple[Path, str]:
    """Load the compact, shared A/B context for one supported scenario."""

    normalized = scenario.strip().casefold()
    path = (
        project_root
        / "student_materials"
        / normalized
        / "llm_context.md"
    )
    try:
        content = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ValueError(
            "No A/B student context is available for "
            f"scenario {normalized!r}: {path}"
        ) from exc
    if not content:
        raise ValueError(f"A/B student context is empty: {path}")
    return path, content


def build_ab_messages(
    *,
    scenario: str = "industroyer2",
    question: str,
    student_context: str,
    retrieved: Sequence[RetrievedChunk] = (),
    response_mode: ResponseMode = "explain",
) -> list[dict[str, str]]:
    """Build an intentionally compact, symmetric A/B comparison prompt.

    The shared student context is supplied to both conditions.  Only the RAG
    condition receives ``retrieved`` evidence.  This helper is deliberately
    separate from the production tutor prompt: it is an evaluation aid, not a
    second student-facing pipeline.
    """

    if response_mode not in {"hint", "explain"}:
        raise ValueError("response_mode must be 'hint' or 'explain'")
    if not question.strip():
        raise ValueError("Question must not be empty")

    evidence_ids = allowed_evidence_ids(retrieved)
    has_rag = bool(retrieved)
    evidence_instruction = (
        "你另外取得了本題的 RAG 證據。事件層的 known_facts 必須由這些證據"
        "直接支持；只能引用 ALLOWED_EVIDENCE_IDS 中的 ID。"
        if has_rag
        else "你沒有 RAG 證據。不得捏造本次事件細節；evidence_ids 必須是空陣列。"
    )
    detail_instruction = (
        "answer 以兩到三句自然、具教學性的繁體中文說明。"
        if response_mode == "explain"
        else "answer 保持一到兩句，先引導學生檢查關鍵證據。"
    )
    retrieved_context = (
        build_retrieved_context(retrieved, scenario=scenario)
        if has_rag
        else "（本組沒有檢索證據。）"
    )
    allowed_ids_text = ", ".join(evidence_ids) if evidence_ids else "（無）"
    system_prompt = (
        "你是 OT 資安事件還原教學助理。以臺灣繁體中文回覆，並只輸出符合"
        "指定 Schema 的 JSON。學生背景可用於理解 Lab 角色與安全範圍，"
        "但不應被擴寫成未被記錄的事件事實。將已知事實、合理推論與證據不足"
        "分開；缺少觀測時使用『現有資料無法確認』，不可改寫成『沒有發生』。"
        "拒絕真實 OT 控制、破壞性或惡意操作要求，改為安全、可逆的 Lab 分析。"
    )
    user_prompt = f"""學生背景：
{student_context}

學生問題：
{question}

本組規則：
{evidence_instruction}
{detail_instruction}

ALLOWED_EVIDENCE_IDS：{allowed_ids_text}

RAG 證據：
{retrieved_context}
"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _validate_comparison_response(
    payload: object,
    allowed_ids: Sequence[str],
) -> TutorResponse:
    """Validate the shared schema and enforce the condition's citation scope."""

    response = TutorResponse.model_validate(payload)
    invalid_ids = [
        evidence_id
        for evidence_id in response.evidence_ids
        if evidence_id not in allowed_ids
    ]
    if invalid_ids:
        raise ValueError(
            "A/B comparison response cited IDs outside its allowed set: "
            + ", ".join(invalid_ids)
        )
    if not allowed_ids and response.evidence_ids:
        raise ValueError("Baseline comparison response must not cite evidence IDs")
    return response


def run_ab_comparison(
    *,
    scenario: str,
    question: str,
    student_context_path: Path,
    student_context: str,
    retrieved: Sequence[RetrievedChunk],
    chat_provider: ChatProvider,
    response_mode: ResponseMode = "explain",
) -> ABComparison:
    """Run the baseline and RAG conditions once each with the same model."""

    baseline_messages = build_ab_messages(
        scenario=scenario,
        question=question,
        student_context=student_context,
        response_mode=response_mode,
    )
    baseline_schema = build_response_schema((), response_mode=response_mode)
    baseline_payload = chat_provider.generate_json(
        messages=baseline_messages,
        response_schema=baseline_schema,
    )
    baseline_response = _validate_comparison_response(baseline_payload, ())

    # B deliberately reuses the production generator, including its schema,
    # evidence, semantic, and question-alignment validation.  The student
    # context is appended to the same production prompt used by the UI.
    rag_response = generate_tutor_response(
        question=question,
        retrieved=retrieved,
        chat_provider=chat_provider,
        require_unknowns=False,
        response_mode=response_mode,
        scenario=scenario,
        student_context=student_context,
    )

    metrics_history = tuple(getattr(chat_provider, "metrics_history", ()))
    return ABComparison(
        scenario=scenario,
        question=question,
        student_context_path=student_context_path,
        student_context=student_context,
        baseline_response=baseline_response,
        rag_response=rag_response,
        retrieved=tuple(retrieved),
        ollama_metrics=metrics_history,
    )


def format_ab_comparison(comparison: ABComparison) -> str:
    """Return a terminal-friendly, human-reviewable A/B comparison."""

    lines = [
        "=" * 72,
        f"[A/B QUESTION] {comparison.question}",
        f"[SCENARIO] {comparison.scenario}",
        f"[SHARED CONTEXT] {comparison.student_context_path}",
        "=" * 72,
        "[A] 情境文件模式（無 RAG）",
        json.dumps(
            comparison.baseline_response.model_dump(),
            ensure_ascii=False,
            indent=2,
        ),
        "-" * 72,
        "[B] 情境文件 + RAG 模式",
        json.dumps(
            comparison.rag_response.model_dump(),
            ensure_ascii=False,
            indent=2,
        ),
        "-" * 72,
        "[B RETRIEVED]",
    ]
    for rank, result in enumerate(comparison.retrieved, start=1):
        lines.append(
            f"{rank}. {result.chunk_id} | "
            f"evidence_ids={', '.join(result.evidence_ids)}"
        )
        lines.append(f"   {result.metadata.get('title', '')}")
    for metrics in comparison.ollama_metrics:
        lines.append(
            "[OLLAMA] "
            f"call={metrics.request_number}, "
            f"done_reason={metrics.done_reason or 'unknown'}, "
            f"prompt_tokens={metrics.prompt_eval_count or 'n/a'}, "
            f"output_tokens={metrics.eval_count or 'n/a'}"
        )
    return "\n".join(lines)


__all__ = [
    "ABComparison",
    "build_ab_messages",
    "format_ab_comparison",
    "load_ab_student_context",
    "run_ab_comparison",
]
