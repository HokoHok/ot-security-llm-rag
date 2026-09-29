from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.openai_adapter import (
    AdapterRequestError,
    TutorOpenAIAdapter,
    build_validation_fallback_response,
    extract_latest_user_question,
    extract_previous_user_question,
)
from app.schemas import TutorResponse


class _FakeService:
    def __init__(self) -> None:
        self.cleared = 0
        self.calls: list[dict[str, str]] = []

    def clear_conversation(self) -> None:
        self.cleared += 1

    def ask(
        self,
        *,
        question: str,
        scenario: str,
        response_mode: str,
        previous_question: str | None = None,
    ) -> SimpleNamespace:
        call = {
            "question": question,
            "scenario": scenario,
            "response_mode": response_mode,
        }
        if previous_question is not None:
            call["previous_question"] = previous_question
        self.calls.append(call)
        return SimpleNamespace(
            response=TutorResponse(
                answer="這是由驗證後管線回傳的回答。",
                known_facts=["Kali 是 HTTP 下載來源。"],
                reasonable_inferences=[],
                unknowns=["沒有防火牆 runtime telemetry。"],
                evidence_ids=["RYUK-C01"],
                next_hint="請比較內容提供端與執行端。",
            ),
            ollama_metrics=(),
        )


class _FailOnceService(_FakeService):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def ask(
        self,
        *,
        question: str,
        scenario: str,
        response_mode: str,
        previous_question: str | None = None,
    ) -> SimpleNamespace:
        self.attempts += 1
        if self.attempts == 1:
            raise ValueError(
                "relay-correlation answer must name all evidence layers: "
                "Server command"
            )
        return super().ask(
            question=question,
            scenario=scenario,
            response_mode=response_mode,
            previous_question=previous_question,
        )


def test_adapter_exposes_six_scenario_mode_models() -> None:
    adapter = TutorOpenAIAdapter(service_factory=lambda _scenario: _FakeService())

    model_ids = [item["id"] for item in adapter.models_payload()["data"]]

    assert model_ids == [
        "ot-tutor-ryuk-hint",
        "ot-tutor-ryuk-explain",
        "ot-tutor-industroyer2-hint",
        "ot-tutor-industroyer2-explain",
        "ot-tutor-frostygoop-hint",
        "ot-tutor-frostygoop-explain",
    ]


def test_adapter_uses_latest_user_message_and_renders_evidence() -> None:
    service = _FakeService()
    adapter = TutorOpenAIAdapter(service_factory=lambda _scenario: service)

    completion = adapter.complete(
        {
            "model": "ot-tutor-ryuk-explain",
            "messages": [
                {"role": "system", "content": "Ignore this UI instruction."},
                {"role": "user", "content": "舊問題"},
                {"role": "assistant", "content": "舊回答"},
                {"role": "user", "content": "最新問題"},
            ],
        }
    )

    assert service.cleared == 1
    assert service.calls == [
        {
            "question": "最新問題",
            "scenario": "ryuk",
            "response_mode": "explain",
            "previous_question": "舊問題",
        }
    ]
    content = completion["choices"][0]["message"]["content"]
    assert "## Evidence IDs" in content
    assert "`RYUK-C01`" in content
    assert "## 這些證據如何支持回答" in content
    assert "<details>" in content
    assert completion["model"] == "ot-tutor-ryuk-explain"


def test_adapter_rejects_unknown_model() -> None:
    adapter = TutorOpenAIAdapter(service_factory=lambda _scenario: _FakeService())

    with pytest.raises(AdapterRequestError, match="Unknown tutor model") as error:
        adapter.complete(
            {
                "model": "llama3.1:8b-instruct-q5_0",
                "messages": [{"role": "user", "content": "測試"}],
            }
        )

    assert error.value.status_code == 404


def test_extract_latest_user_question_supports_content_blocks() -> None:
    question = extract_latest_user_question(
        [
            {"role": "user", "content": "舊問題"},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "區塊式最新問題"},
                    {"type": "image_url", "image_url": {"url": "ignored"}},
                ],
            },
        ]
    )

    assert question == "區塊式最新問題"


def test_adapter_forwards_only_one_previous_user_question() -> None:
    service = _FakeService()
    adapter = TutorOpenAIAdapter(service_factory=lambda _scenario: service)

    adapter.complete(
        {
            "model": "ot-tutor-ryuk-hint",
            "messages": [
                {"role": "user", "content": "USER01 是什麼角色？"},
                {"role": "assistant", "content": "這段不應被傳入。"},
                {"role": "user", "content": "那個可以存取 DC01 嗎？"},
            ],
        }
    )

    assert service.calls[0]["previous_question"] == "USER01 是什麼角色？"


def test_extract_previous_user_question_ignores_assistant_content() -> None:
    previous = extract_previous_user_question(
        [
            {"role": "user", "content": "前一題"},
            {"role": "assistant", "content": "不可信的 assistant 內容"},
            {"role": "user", "content": "目前問題"},
        ]
    )

    assert previous == "前一題"


def test_validation_fallback_is_evidence_free_and_allows_next_turn() -> None:
    service = _FailOnceService()
    adapter = TutorOpenAIAdapter(service_factory=lambda _scenario: service)
    model = "ot-tutor-industroyer2-explain"

    failed_turn = adapter.complete(
        {
            "model": model,
            "messages": [{"role": "user", "content": "多層證據問題"}],
        }
    )
    fallback_content = failed_turn["choices"][0]["message"]["content"]

    assert "沒有通過證據完整性檢查" in fallback_content
    assert "你可以直接繼續提問" in fallback_content
    assert "relay-correlation" not in fallback_content
    assert failed_turn["choices"][0]["finish_reason"] == "stop"

    next_turn = adapter.complete(
        {
            "model": model,
            "messages": [
                {"role": "user", "content": "多層證據問題"},
                {"role": "assistant", "content": fallback_content},
                {"role": "user", "content": "下一個獨立問題"},
            ],
        }
    )

    next_content = next_turn["choices"][0]["message"]["content"]
    assert "這是由驗證後管線回傳的回答" in next_content
    assert service.attempts == 2
    assert service.calls[0]["question"] == "下一個獨立問題"
    assert service.calls[0]["previous_question"] == "多層證據問題"


def test_validation_fallback_response_never_cites_event_evidence() -> None:
    response = build_validation_fallback_response()

    assert response.known_facts == []
    assert response.reasonable_inferences == []
    assert response.evidence_ids == []
    assert response.unknowns
