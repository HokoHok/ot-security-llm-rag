from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.adequacy import EvidenceAdequacy
from app.followups import (
    DerivedQuestion,
    DerivedQuestionSet,
    FollowupGenerationTrace,
)
from app.manual_evaluation import (
    atomic_write_json,
    run_manual_evaluation,
)
from app.retrieval import RetrievedChunk
from app.schemas import TutorResponse
from app.service import TutorSession


class _Metric:
    def __init__(self, request_number: int) -> None:
        self.request_number = request_number

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_number": self.request_number,
            "done_reason": "stop",
            "prompt_eval_count": 20,
            "eval_count": 10,
            "total_duration_seconds": 1.0,
            "load_duration_seconds": 0.1,
            "prompt_eval_duration_seconds": 0.2,
            "eval_duration_seconds": 0.7,
            "output_tokens_per_second": 10 / 0.7,
            "output_may_be_truncated": False,
        }


class _Provider:
    model = "stub-model"

    def __init__(self) -> None:
        self.request_count = 0
        self.metrics_history: list[_Metric] = []

    def record_call(self) -> None:
        self.request_count += 1
        self.metrics_history.append(_Metric(self.request_count))


class _Service:
    top_k = 4
    response_cache_size = 0

    def __init__(self, fail_questions: set[str] | None = None) -> None:
        self.chat_provider = _Provider()
        self.fail_questions = fail_questions or set()
        self.questions: list[str] = []

    def ask(
        self,
        question: str,
        scenario: str,
        response_mode: str,
        progress: Any = None,
    ) -> TutorSession:
        self.questions.append(question)
        if progress is not None:
            progress("stub retrieval")
        if question in self.fail_questions:
            raise RuntimeError("stub failure")
        self.chat_provider.record_call()
        evidence_id = "RYUK-U01"
        response = TutorResponse(
            answer="這是測試回答。",
            known_facts=["USER01 是測試端點。"],
            reasonable_inferences=[],
            unknowns=[],
            evidence_ids=[evidence_id],
            next_hint="請繼續比較證據。",
        )
        retrieved = [
            RetrievedChunk(
                chunk_id="RYUK-test-01",
                document="測試文件",
                metadata={
                    "title": "測試證據",
                    "evidence_ids": evidence_id,
                },
                rrf_score=0.1,
                dense_rank=1,
                sparse_rank=1,
                dense_distance=0.2,
                sparse_score=3.0,
            )
        ]
        return TutorSession(
            question=question,
            scenario=scenario,
            retrieved=retrieved,
            response=response,
            retrieval_seconds=0.1,
            generation_seconds=0.7,
            generation_attempts=1,
            ollama_metrics=(),
            question_scope="in_scope",
            evidence_adequacy=EvidenceAdequacy(
                status="sufficient",
                require_unknowns=False,
                reasons=("stub",),
                best_dense_distance=0.2,
                best_sparse_score=3.0,
            ),
            response_mode=response_mode,  # type: ignore[arg-type]
        )


def _dataset(path: Path) -> Path:
    questions = []
    for index in range(1, 26):
        turns = [f"第 {index} 題測試問題是什麼？"]
        if index in {24, 25}:
            turns.append(f"第 {index} 題的獨立追問是什麼？")
        questions.append(
            {
                "question_id": f"MT-{index:02d}",
                "category": "multi_turn" if index in {24, 25} else "normal",
                "turns": turns,
                "response_mode": "explain",
            }
        )
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "scenario": "ryuk",
                "purpose": "test",
                "questions": questions,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _stub_followups(**kwargs: Any) -> FollowupGenerationTrace:
    kwargs["chat_provider"].record_call()
    return FollowupGenerationTrace(
        result=DerivedQuestionSet(
            questions=[
                DerivedQuestion(
                    kind="clarify",
                    question="請釐清這項已知事實的證據？",
                ),
                DerivedQuestion(
                    kind="challenge",
                    question="哪些限制使這項結論不能擴大？",
                ),
                DerivedQuestion(
                    kind="investigate",
                    question="下一步還應蒐集哪一類證據？",
                ),
            ]
        ),
        source="llama",
        attempts=1,
    )


def test_atomic_write_json_replaces_complete_document(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "result.json"

    atomic_write_json(target, {"version": 1})
    atomic_write_json(target, {"version": 2, "text": "繁體中文"})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "version": 2,
        "text": "繁體中文",
    }
    assert list(target.parent.glob("*.tmp")) == []


def test_batch_runs_one_base_case_and_three_derived_questions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_path = _dataset(tmp_path / "manual.json")
    run_dir = tmp_path / "run"
    service = _Service()
    monkeypatch.setattr(
        "app.manual_evaluation.generate_derived_questions_with_trace",
        _stub_followups,
    )

    report = run_manual_evaluation(
        dataset_path=dataset_path,
        run_dir=run_dir,
        service=service,  # type: ignore[arg-type]
        limit=1,
        progress=lambda _message: None,
    )

    assert report["summary"]["status"] == "completed"
    assert report["summary"]["planned"] == {
        "base_cases": 1,
        "base_turns": 1,
        "derived_questions": 3,
        "logical_questions": 4,
        "total_tutor_turns": 4,
        "followup_generation_batches": 1,
    }
    assert report["summary"]["completed_question_turns"] == 4
    assert report["summary"]["ollama_call_count"] == 5
    assert report["summary"]["done_reason_counts"] == {"stop": 5}
    assert len(report["question_results"]) == 4
    assert (run_dir / "checkpoint.json").exists()
    assert (run_dir / "report.json").exists()


def test_multi_turn_case_is_counted_as_independent_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_path = _dataset(tmp_path / "manual.json")
    service = _Service()
    monkeypatch.setattr(
        "app.manual_evaluation.generate_derived_questions_with_trace",
        _stub_followups,
    )

    report = run_manual_evaluation(
        dataset_path=dataset_path,
        run_dir=tmp_path / "run",
        service=service,  # type: ignore[arg-type]
        case_ids={"MT-24"},
        progress=lambda _message: None,
    )

    assert report["summary"]["planned"]["base_turns"] == 2
    assert report["summary"]["planned"]["logical_questions"] == 4
    assert report["summary"]["planned"]["total_tutor_turns"] == 5
    assert all(
        result["history_passed_to_model"] is False
        for result in report["question_results"]
    )


def test_resume_skips_successful_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_path = _dataset(tmp_path / "manual.json")
    run_dir = tmp_path / "run"
    monkeypatch.setattr(
        "app.manual_evaluation.generate_derived_questions_with_trace",
        _stub_followups,
    )
    first_service = _Service()
    run_manual_evaluation(
        dataset_path=dataset_path,
        run_dir=run_dir,
        service=first_service,  # type: ignore[arg-type]
        limit=1,
        progress=lambda _message: None,
    )
    second_service = _Service()

    report = run_manual_evaluation(
        dataset_path=dataset_path,
        run_dir=run_dir,
        service=second_service,  # type: ignore[arg-type]
        limit=1,
        resume=True,
        progress=lambda _message: None,
    )

    assert second_service.questions == []
    assert second_service.chat_provider.request_count == 0
    assert report["summary"]["completed_question_turns"] == 4


def test_question_failure_is_saved_and_later_cases_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_path = _dataset(tmp_path / "manual.json")
    failed_question = "第 1 題測試問題是什麼？"
    service = _Service(fail_questions={failed_question})
    monkeypatch.setattr(
        "app.manual_evaluation.generate_derived_questions_with_trace",
        _stub_followups,
    )

    report = run_manual_evaluation(
        dataset_path=dataset_path,
        run_dir=tmp_path / "run",
        service=service,  # type: ignore[arg-type]
        limit=2,
        progress=lambda _message: None,
    )

    assert report["summary"]["status"] == "completed_with_errors"
    assert report["summary"]["failed_question_turns"] == 1
    assert report["summary"]["materialized_derived_questions"] == 3
    assert "第 2 題測試問題是什麼？" in service.questions
    assert any(
        result["spec"]["record_id"] == "MT-02-D3"
        for result in report["question_results"]
    )
    assert len(report["summary"]["errors"]) == 2


def test_runner_rejects_enabled_response_cache(
    tmp_path: Path,
) -> None:
    dataset_path = _dataset(tmp_path / "manual.json")
    service = _Service()
    service.response_cache_size = 32

    with pytest.raises(ValueError, match="response_cache_size=0"):
        run_manual_evaluation(
            dataset_path=dataset_path,
            run_dir=tmp_path / "run",
            service=service,  # type: ignore[arg-type]
            limit=1,
            progress=lambda _message: None,
        )
