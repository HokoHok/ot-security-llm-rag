from __future__ import annotations

from typing import Any

from app.ab_comparison import (
    build_ab_messages,
    load_ab_student_context,
    run_ab_comparison,
)
from app.config import PROJECT_ROOT
from app.retrieval import RetrievedChunk


class _SequenceProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_json(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"messages": messages, "schema": response_schema})
        if len(self.calls) == 1:
            return {
                "answer": "情境文件只足以說明需要比對多端紀錄。",
                "known_facts": ["ENG01 是工程工作站。"],
                "reasonable_inferences": [],
                "unknowns": ["目前沒有本題的動態事件證據。"],
                "evidence_ids": [],
                "next_hint": "比較 Server 與 ENG 的觀測位置。",
            }
        return {
            "answer": "RAG 證據指出來源 IP 本身不足以判定最初發起者。",
            "known_facts": ["Server source IP 單獨不足以區分兩種情境。"],
            "reasonable_inferences": [],
            "unknowns": [],
            "evidence_ids": ["IND2-N20"],
            "next_hint": "再比較 Kali action 與 ENG Relay log。",
        }


def _retrieved() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id="IND2-test-01",
            document="Server source IP alone cannot distinguish normal ENG from relay.",
            metadata={
                "title": "來源 IP 的證據界線",
                "stage": "analysis",
                "source_type": "normalized_analysis",
                "evidence_ids": "IND2-N20",
            },
            rrf_score=0.1,
            dense_rank=1,
            sparse_rank=1,
        )
    ]


def test_industroyer2_ab_context_is_available() -> None:
    path, content = load_ab_student_context(PROJECT_ROOT, "industroyer2")

    assert path.name == "llm_context.md"
    assert "OT-SERVER01" in content


def test_all_scenarios_have_nonempty_ab_context() -> None:
    for scenario in ("ryuk", "industroyer2", "frostygoop"):
        path, content = load_ab_student_context(PROJECT_ROOT, scenario)

        assert path.name == "llm_context.md"
        assert content


def test_ab_messages_only_add_retrieved_evidence_to_rag_condition() -> None:
    _, context = load_ab_student_context(PROJECT_ROOT, "industroyer2")
    baseline = build_ab_messages(question="測試問題", student_context=context)
    rag = build_ab_messages(
        question="測試問題",
        student_context=context,
        retrieved=_retrieved(),
    )

    assert context in baseline[1]["content"]
    assert context in rag[1]["content"]
    assert "IND2-N20" not in baseline[1]["content"]
    assert "IND2-N20" in rag[1]["content"]


def test_ab_comparison_keeps_baseline_uncited_and_rag_cited() -> None:
    path, context = load_ab_student_context(PROJECT_ROOT, "industroyer2")
    provider = _SequenceProvider()

    comparison = run_ab_comparison(
        scenario="industroyer2",
        question="為什麼來源 IP 不足以判定正常維護？",
        student_context_path=path,
        student_context=context,
        retrieved=_retrieved(),
        chat_provider=provider,
    )

    assert comparison.baseline_response.evidence_ids == []
    assert comparison.rag_response.evidence_ids == ["IND2-N20"]
    assert len(provider.calls) == 2
