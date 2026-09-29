from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from time import perf_counter
from typing import Any, Literal

from app.config import Settings, load_settings
from app.followups import (
    FollowupGenerationTrace,
    generate_derived_questions_with_trace,
)
from app.ingestion import load_manual_test_dataset
from app.schemas import ManualTestCase, ManualTestDataset, TutorResponse
from app.service import TutorService, TutorSession

ProgressCallback = Callable[[str], None]
RunStatus = Literal["running", "completed", "completed_with_errors"]


@dataclass(frozen=True)
class QuestionSpec:
    """One independently submitted tutor question in a live batch."""

    record_id: str
    case_id: str
    category: str
    kind: Literal["base", "derived"]
    question: str
    response_mode: Literal["hint", "explain"]
    turn_index: int | None = None
    derivative_index: int | None = None
    derivative_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def atomic_write_json(path: Path, payload: Any) -> Path:
    """Durably replace one JSON file without exposing a partial document."""

    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(
        f".{resolved.name}.{os.getpid()}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, resolved)
    finally:
        if temporary.exists():
            temporary.unlink()
    return resolved


def create_manual_tutor_service(
    settings: Settings | None = None,
) -> TutorService:
    """Create one cache-free service whose Ollama client is reused by a run."""

    resolved = settings or load_settings()
    return TutorService.from_settings(
        replace(resolved, response_cache_size=0)
    )


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _dataset_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_file(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _selected_cases(
    dataset: ManualTestDataset,
    case_ids: set[str] | None,
    limit: int | None,
) -> list[ManualTestCase]:
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    known = {case.question_id for case in dataset.questions}
    requested = set(case_ids or ())
    missing = sorted(requested - known)
    if missing:
        raise ValueError(
            "Unknown manual question ID(s): " + ", ".join(missing)
        )
    selected = [
        case
        for case in dataset.questions
        if not requested or case.question_id in requested
    ]
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        raise ValueError("No manual questions matched the selection")
    return selected


def _base_specs(cases: Sequence[ManualTestCase]) -> list[QuestionSpec]:
    specs: list[QuestionSpec] = []
    for case in cases:
        for turn_index, question in enumerate(case.turns, start=1):
            specs.append(
                QuestionSpec(
                    record_id=f"{case.question_id}-T{turn_index}",
                    case_id=case.question_id,
                    category=case.category,
                    kind="base",
                    question=question,
                    response_mode=case.response_mode,
                    turn_index=turn_index,
                )
            )
    return specs


def _metrics_snapshot(provider: Any) -> tuple[int | None, int]:
    request_count = getattr(provider, "request_count", None)
    history = getattr(provider, "metrics_history", ())
    return (
        request_count if isinstance(request_count, int) else None,
        len(history) if isinstance(history, Sequence) else 0,
    )


def _metric_dict(metric: Any) -> dict[str, Any]:
    if hasattr(metric, "to_dict"):
        value = metric.to_dict()
        if isinstance(value, dict):
            return value
    if hasattr(metric, "__dataclass_fields__"):
        return asdict(metric)
    return {"value": str(metric)}


def _metrics_since(
    provider: Any,
    snapshot: tuple[int | None, int],
) -> tuple[list[dict[str, Any]], int]:
    request_before, history_before = snapshot
    history = getattr(provider, "metrics_history", ())
    metrics = (
        [_metric_dict(item) for item in history[history_before:]]
        if isinstance(history, Sequence)
        else []
    )
    request_after = getattr(provider, "request_count", None)
    request_delta = (
        request_after - request_before
        if isinstance(request_before, int)
        and isinstance(request_after, int)
        else len(metrics)
    )
    return metrics, max(request_delta, len(metrics), 0)


def _retrieval_dict(session: TutorSession) -> dict[str, Any]:
    evidence_ids: list[str] = []
    items: list[dict[str, Any]] = []
    for rank, result in enumerate(session.retrieved, start=1):
        for evidence_id in result.evidence_ids:
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
        items.append(
            {
                "rank": rank,
                "chunk_id": result.chunk_id,
                "title": result.metadata.get("title", ""),
                "evidence_ids": result.evidence_ids,
                "rrf_score": result.rrf_score,
                "dense_rank": result.dense_rank,
                "sparse_rank": result.sparse_rank,
                "dense_distance": result.dense_distance,
                "sparse_score": result.sparse_score,
            }
        )
    return {
        "seconds": session.retrieval_seconds,
        "chunk_ids": [item["chunk_id"] for item in items],
        "evidence_ids": evidence_ids,
        "items": items,
    }


def _adequacy_dict(session: TutorSession) -> dict[str, Any] | None:
    adequacy = session.evidence_adequacy
    if adequacy is None:
        return None
    return {
        "status": adequacy.status,
        "require_unknowns": adequacy.require_unknowns,
        "reasons": list(adequacy.reasons),
        "best_dense_distance": adequacy.best_dense_distance,
        "best_sparse_score": adequacy.best_sparse_score,
    }


def _generation_dict(
    *,
    session: TutorSession | None,
    metrics: list[dict[str, Any]],
    request_delta: int,
    seconds: float,
) -> dict[str, Any]:
    attempts = session.generation_attempts if session is not None else None
    return {
        "seconds": seconds,
        "attempts": attempts,
        "ollama_call_count": request_delta,
        "ollama_metrics": metrics,
        "done_reasons": [
            item.get("done_reason") for item in metrics
            if item.get("done_reason") is not None
        ],
        "prompt_tokens": sum(
            item.get("prompt_eval_count") or 0 for item in metrics
        ),
        "output_tokens": sum(
            item.get("eval_count") or 0 for item in metrics
        ),
        "output_may_be_truncated": any(
            bool(item.get("output_may_be_truncated")) for item in metrics
        ),
    }


def execute_question(
    service: TutorService,
    spec: QuestionSpec,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Execute one question and always return a serializable result record."""

    notify = progress or (lambda _message: None)
    provider = service.chat_provider
    snapshot = _metrics_snapshot(provider)
    started_at = _now()
    started = perf_counter()
    last_progress: list[str] = []

    def relay(message: str) -> None:
        last_progress.append(message)
        notify(f"[{spec.record_id}] {message}")

    session: TutorSession | None = None
    try:
        session = service.ask(
            question=spec.question,
            scenario=getattr(service, "manual_scenario", "ryuk"),
            response_mode=spec.response_mode,
            progress=relay,
        )
        wall_seconds = perf_counter() - started
        metrics, request_delta = _metrics_since(provider, snapshot)
        return {
            "schema_version": "1.0",
            "status": "ok",
            "spec": spec.to_dict(),
            "started_at": started_at,
            "completed_at": _now(),
            "wall_seconds": wall_seconds,
            "history_passed_to_model": False,
            "question_scope": session.question_scope,
            "evidence_adequacy": _adequacy_dict(session),
            "retrieval": _retrieval_dict(session),
            "generation": _generation_dict(
                session=session,
                metrics=metrics,
                request_delta=request_delta,
                seconds=session.generation_seconds,
            ),
            "cache_hit": session.cache_hit,
            "response": session.response.model_dump(),
            "progress_messages": last_progress,
            "error": None,
        }
    except Exception as exc:
        wall_seconds = perf_counter() - started
        metrics, request_delta = _metrics_since(provider, snapshot)
        return {
            "schema_version": "1.0",
            "status": "error",
            "spec": spec.to_dict(),
            "started_at": started_at,
            "completed_at": _now(),
            "wall_seconds": wall_seconds,
            "history_passed_to_model": False,
            "question_scope": None,
            "evidence_adequacy": None,
            "retrieval": None,
            "generation": _generation_dict(
                session=None,
                metrics=metrics,
                request_delta=request_delta,
                seconds=wall_seconds,
            ),
            "cache_hit": False,
            "response": None,
            "progress_messages": last_progress,
            "error": {
                "phase": "service.ask",
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }


def _attempt_files(run_dir: Path, group: str, record_id: str) -> list[Path]:
    folder = run_dir / group / record_id
    return sorted(folder.glob("attempt-*.json")) if folder.exists() else []


def _latest_attempt(
    run_dir: Path,
    group: str,
    record_id: str,
) -> dict[str, Any] | None:
    files = _attempt_files(run_dir, group, record_id)
    return _json_file(files[-1]) if files else None


def _save_attempt(
    run_dir: Path,
    group: str,
    record_id: str,
    payload: dict[str, Any],
) -> Path:
    attempt = len(_attempt_files(run_dir, group, record_id)) + 1
    payload = {**payload, "execution_attempt": attempt}
    return atomic_write_json(
        run_dir / group / record_id / f"attempt-{attempt:04d}.json",
        payload,
    )


def _should_run(
    previous: dict[str, Any] | None,
    retry_errors: bool,
) -> bool:
    if previous is None:
        return True
    return retry_errors and previous.get("status") == "error"


def _run_question_spec(
    *,
    run_dir: Path,
    service: TutorService,
    spec: QuestionSpec,
    retry_errors: bool,
    progress: ProgressCallback,
    ordinal: int,
    total: int,
) -> dict[str, Any]:
    previous = _latest_attempt(run_dir, "attempts", spec.record_id)
    if not _should_run(previous, retry_errors):
        progress(
            f"[SKIP {ordinal}/{total}] {spec.record_id} "
            f"status={previous.get('status') if previous else 'unknown'}"
        )
        return previous or {}

    progress(f"[RUN {ordinal}/{total}] {spec.record_id} {spec.question}")
    result = execute_question(service, spec, progress=progress)
    saved = _save_attempt(run_dir, "attempts", spec.record_id, result)
    calls = result["generation"]["ollama_call_count"]
    done = ",".join(result["generation"]["done_reasons"]) or "n/a"
    progress(
        f"[{'OK' if result['status'] == 'ok' else 'ERROR'}] "
        f"{spec.record_id} scope={result.get('question_scope') or 'n/a'} "
        f"calls={calls} done={done} time={result['wall_seconds']:.2f}s "
        f"saved={saved}"
    )
    return {**result, "execution_attempt": len(
        _attempt_files(run_dir, "attempts", spec.record_id)
    )}


def _followup_attempt(
    *,
    case: ManualTestCase,
    base_results: Sequence[dict[str, Any]],
    service: TutorService,
) -> dict[str, Any]:
    successful = [item for item in base_results if item.get("status") == "ok"]
    if len(successful) != len(case.turns):
        return {
            "schema_version": "1.0",
            "status": "error",
            "case_id": case.question_id,
            "started_at": _now(),
            "completed_at": _now(),
            "wall_seconds": 0.0,
            "source_result_ids": [
                item.get("spec", {}).get("record_id") for item in base_results
            ],
            "questions": [],
            "followup_trace": None,
            "generation": _generation_dict(
                session=None, metrics=[], request_delta=0, seconds=0.0
            ),
            "error": {
                "phase": "followup_generation",
                "type": "BaseQuestionError",
                "message": "All base turns must succeed before derivation.",
            },
        }

    source = successful[-1]
    response = TutorResponse.model_validate(source["response"])
    provider = service.chat_provider
    snapshot = _metrics_snapshot(provider)
    started_at = _now()
    started = perf_counter()
    try:
        trace: FollowupGenerationTrace = (
            generate_derived_questions_with_trace(
            base_question=source["spec"]["question"],
            response=response,
            chat_provider=provider,
            question_scope=source["question_scope"],
            safety_sensitive=case.category == "safety_boundary",
            policy_sensitive=case.category in {
                "safety_boundary",
                "prompt_injection",
            },
            )
        )
        seconds = perf_counter() - started
        metrics, request_delta = _metrics_since(provider, snapshot)
        return {
            "schema_version": "1.0",
            "status": "ok",
            "case_id": case.question_id,
            "started_at": started_at,
            "completed_at": _now(),
            "wall_seconds": seconds,
            "source_result_ids": [
                item["spec"]["record_id"] for item in successful
            ],
            "questions": [item.model_dump() for item in trace.questions],
            "followup_trace": {
                "source": trace.source,
                "attempts": trace.attempts,
                "errors": trace.errors,
            },
            "generation": _generation_dict(
                session=None,
                metrics=metrics,
                request_delta=request_delta,
                seconds=seconds,
            ),
            "error": None,
        }
    except Exception as exc:
        seconds = perf_counter() - started
        metrics, request_delta = _metrics_since(provider, snapshot)
        return {
            "schema_version": "1.0",
            "status": "error",
            "case_id": case.question_id,
            "started_at": started_at,
            "completed_at": _now(),
            "wall_seconds": seconds,
            "source_result_ids": [
                item["spec"]["record_id"] for item in successful
            ],
            "questions": [],
            "followup_trace": None,
            "generation": _generation_dict(
                session=None,
                metrics=metrics,
                request_delta=request_delta,
                seconds=seconds,
            ),
            "error": {
                "phase": "followup_generation",
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }


def _derived_specs(
    cases: Sequence[ManualTestCase],
    run_dir: Path,
) -> list[QuestionSpec]:
    specs: list[QuestionSpec] = []
    for case in cases:
        result = _latest_attempt(
            run_dir, "followups", case.question_id
        )
        if result is None or result.get("status") != "ok":
            continue
        for index, item in enumerate(result.get("questions", []), start=1):
            specs.append(
                QuestionSpec(
                    record_id=f"{case.question_id}-D{index}",
                    case_id=case.question_id,
                    category=case.category,
                    kind="derived",
                    question=str(item["question"]),
                    response_mode=case.response_mode,
                    derivative_index=index,
                    derivative_kind=str(item.get("kind", "")),
                )
            )
    return specs


def _latest_records(run_dir: Path, group: str) -> list[dict[str, Any]]:
    root = run_dir / group
    if not root.exists():
        return []
    records: list[dict[str, Any]] = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        latest = _latest_attempt(run_dir, group, folder.name)
        if latest is not None:
            records.append(latest)
    return records


def _question_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def _numeric_values(
    records: Iterable[dict[str, Any]],
    *keys: str,
) -> list[float]:
    values: list[float] = []
    for record in records:
        value: Any = record
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _duration_summary(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"sum": 0.0, "mean": 0.0, "median": 0.0, "max": 0.0}
    return {
        "sum": sum(values),
        "mean": mean(values),
        "median": median(values),
        "max": max(values),
    }


def rebuild_manual_report(run_dir: Path) -> dict[str, Any]:
    """Aggregate the latest durable attempts into ``report.json``."""

    manifest = _json_file(run_dir / "run.json")
    question_records = _latest_records(run_dir, "attempts")
    followup_records = _latest_records(run_dir, "followups")
    errors = [
        {
            "record_id": item.get("spec", {}).get("record_id"),
            **(item.get("error") or {}),
        }
        for item in question_records
        if item.get("status") == "error"
    ] + [
        {
            "record_id": item.get("case_id"),
            **(item.get("error") or {}),
        }
        for item in followup_records
        if item.get("status") == "error"
    ]
    scopes = Counter(
        item.get("question_scope") or "unavailable"
        for item in question_records
    )
    adequacy = Counter(
        (
            item.get("evidence_adequacy") or {}
        ).get("status", "not_applicable")
        for item in question_records
    )
    all_runtime_records = [*question_records, *followup_records]
    metric_items = [
        metric
        for item in all_runtime_records
        for metric in (item.get("generation") or {}).get(
            "ollama_metrics", []
        )
    ]
    done_reasons = Counter(
        metric.get("done_reason") or "unknown" for metric in metric_items
    )
    followup_sources = Counter(
        (item.get("followup_trace") or {}).get("source", "unavailable")
        for item in followup_records
    )
    derived_question_entries = [
        {
            "record_id": f"{item.get('case_id')}-D{index}",
            "question": str(question.get("question", "")),
        }
        for item in followup_records
        if item.get("status") == "ok"
        for index, question in enumerate(item.get("questions", []), start=1)
    ]
    duplicate_counts = Counter(
        _question_key(item["question"])
        for item in derived_question_entries
        if _question_key(item["question"])
    )
    duplicate_keys = {
        key for key, count in duplicate_counts.items() if count > 1
    }
    duplicate_groups = [
        {
            "normalized_question": key,
            "record_ids": [
                item["record_id"]
                for item in derived_question_entries
                if _question_key(item["question"]) == key
            ],
            "questions": sorted(
                {
                    item["question"]
                    for item in derived_question_entries
                    if _question_key(item["question"]) == key
                }
            ),
        }
        for key in sorted(duplicate_keys)
    ]
    calls = sum(
        (item.get("generation") or {}).get("ollama_call_count", 0)
        for item in all_runtime_records
    )
    tutor_calls = sum(
        (item.get("generation") or {}).get("ollama_call_count", 0)
        for item in question_records
    )
    followup_calls = calls - tutor_calls
    unavailable_done_reasons = max(calls - len(metric_items), 0)
    if unavailable_done_reasons:
        done_reasons["unavailable"] += unavailable_done_reasons
    question_errors = sum(
        item.get("status") == "error" for item in question_records
    )
    planned = manifest["planned"]
    completed_questions = len(question_records)
    materialized_derived = sum(
        len(item.get("questions", []))
        for item in followup_records
        if item.get("status") == "ok"
    )
    materialized_turns = planned["base_turns"] + materialized_derived
    completed = (
        completed_questions == materialized_turns
        and len(followup_records) == planned["base_cases"]
    )
    status: RunStatus = (
        "completed_with_errors"
        if completed and errors
        else "completed" if completed else "running"
    )
    summary = {
        "status": status,
        "planned": planned,
        "completed_question_turns": completed_questions,
        "successful_question_turns": completed_questions - question_errors,
        "failed_question_turns": question_errors,
        "completed_followup_batches": len(followup_records),
        "materialized_derived_questions": materialized_derived,
        "followup_source_counts": dict(sorted(followup_sources.items())),
        "followup_generation_attempts": sum(
            (item.get("followup_trace") or {}).get("attempts", 0)
            for item in followup_records
        ),
        "normalized_derived_duplicate_count": sum(
            count - 1 for count in duplicate_counts.values() if count > 1
        ),
        "normalized_derived_duplicate_groups": duplicate_groups,
        "scope_counts": dict(sorted(scopes.items())),
        "adequacy_counts": dict(sorted(adequacy.items())),
        "ollama_call_count": calls,
        "tutor_ollama_call_count": tutor_calls,
        "followup_ollama_call_count": followup_calls,
        "done_reason_counts": dict(sorted(done_reasons.items())),
        "prompt_tokens": sum(
            metric.get("prompt_eval_count") or 0 for metric in metric_items
        ),
        "output_tokens": sum(
            metric.get("eval_count") or 0 for metric in metric_items
        ),
        "truncated_call_count": sum(
            bool(metric.get("output_may_be_truncated"))
            for metric in metric_items
        ),
        "question_wall_seconds": _duration_summary(
            _numeric_values(question_records, "wall_seconds")
        ),
        "retrieval_seconds": _duration_summary(
            _numeric_values(question_records, "retrieval", "seconds")
        ),
        "generation_seconds": _duration_summary(
            _numeric_values(question_records, "generation", "seconds")
        ),
        "ollama_total_duration_seconds": _duration_summary(
            [
                float(metric["total_duration_seconds"])
                for metric in metric_items
                if isinstance(metric.get("total_duration_seconds"), (int, float))
            ]
        ),
        "ollama_load_duration_seconds": _duration_summary(
            [
                float(metric["load_duration_seconds"])
                for metric in metric_items
                if isinstance(metric.get("load_duration_seconds"), (int, float))
            ]
        ),
        "ollama_prompt_eval_duration_seconds": _duration_summary(
            [
                float(metric["prompt_eval_duration_seconds"])
                for metric in metric_items
                if isinstance(
                    metric.get("prompt_eval_duration_seconds"),
                    (int, float),
                )
            ]
        ),
        "ollama_eval_duration_seconds": _duration_summary(
            [
                float(metric["eval_duration_seconds"])
                for metric in metric_items
                if isinstance(metric.get("eval_duration_seconds"), (int, float))
            ]
        ),
        "errors": errors,
    }
    report = {
        "schema_version": "1.0",
        "generated_at": _now(),
        "run": manifest,
        "summary": summary,
        "question_results": question_records,
        "followup_generations": followup_records,
    }
    atomic_write_json(run_dir / "report.json", report)
    atomic_write_json(
        run_dir / "checkpoint.json",
        {
            "schema_version": "1.0",
            "run_id": manifest["run_id"],
            "updated_at": _now(),
            "status": status,
            "summary": summary,
        },
    )
    return report


def format_manual_summary(report: dict[str, Any]) -> str:
    summary = report["summary"]
    planned = summary["planned"]
    return "\n".join(
        [
            "[MANUAL LIVE BATCH]",
            f"status: {summary['status']}",
            (
                "question turns: "
                f"{summary['completed_question_turns']}/"
                f"{planned['total_tutor_turns']} "
                f"(ok={summary['successful_question_turns']}, "
                f"error={summary['failed_question_turns']})"
            ),
            f"scope: {summary['scope_counts']}",
            f"adequacy: {summary['adequacy_counts']}",
            f"Ollama calls: {summary['ollama_call_count']}",
            f"done reasons: {summary['done_reason_counts']}",
            (
                "question wall time: "
                f"{summary['question_wall_seconds']['sum']:.2f}s"
            ),
            f"errors: {len(summary['errors'])}",
        ]
    )


def _new_manifest(
    *,
    dataset_path: Path,
    dataset: ManualTestDataset,
    cases: Sequence[ManualTestCase],
    service: TutorService,
    derived_per_case: int,
) -> dict[str, Any]:
    base_turns = sum(len(case.turns) for case in cases)
    derived_questions = len(cases) * derived_per_case
    provider = service.chat_provider
    return {
        "schema_version": "1.0",
        "run_id": f"{dataset.scenario}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "created_at": _now(),
        "scenario": dataset.scenario,
        "dataset_path": str(dataset_path.resolve()),
        "dataset_sha256": _dataset_fingerprint(dataset_path),
        "selected_cases": [case.model_dump() for case in cases],
        "model": getattr(provider, "model", None),
        "top_k": service.top_k,
        "derived_per_case": derived_per_case,
        "history_policy": "independent_single_turn",
        "planned": {
            "base_cases": len(cases),
            "base_turns": base_turns,
            "derived_questions": derived_questions,
            "logical_questions": len(cases) + derived_questions,
            "total_tutor_turns": base_turns + derived_questions,
            "followup_generation_batches": len(cases),
        },
    }


def _validate_resume_manifest(
    manifest: dict[str, Any],
    *,
    dataset_path: Path,
    cases: Sequence[ManualTestCase],
    derived_per_case: int,
    service: TutorService,
) -> None:
    if manifest.get("dataset_sha256") != _dataset_fingerprint(dataset_path):
        raise ValueError("Cannot resume: manual question dataset has changed")
    if manifest.get("derived_per_case") != derived_per_case:
        raise ValueError("Cannot resume: derived question count has changed")
    selected_ids = [item["question_id"] for item in manifest["selected_cases"]]
    if selected_ids != [case.question_id for case in cases]:
        raise ValueError("Cannot resume: selected manual question IDs changed")
    current_model = getattr(service.chat_provider, "model", None)
    if manifest.get("model") != current_model:
        raise ValueError("Cannot resume: chat model has changed")
    if manifest.get("top_k") != service.top_k:
        raise ValueError("Cannot resume: TOP_K has changed")


def run_manual_evaluation(
    *,
    dataset_path: Path,
    run_dir: Path,
    service: TutorService,
    derived_per_case: int = 3,
    case_ids: set[str] | None = None,
    limit: int | None = None,
    resume: bool = False,
    retry_errors: bool = False,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run base turns, derive three questions per case, then run derivatives.

    Each attempt is written atomically before the next item begins.  Existing
    successful attempts are never repeated during resume.  A failed item is
    recorded and does not stop other cases; ``retry_errors`` adds a new attempt.
    Multi-turn cases are deliberately submitted as independent single turns,
    matching the current stateless tutor implementation.
    """

    if derived_per_case != 3:
        raise ValueError(
            "derived_per_case must be 3 because followups return exactly three"
        )
    notify = progress or print
    if service.response_cache_size != 0:
        raise ValueError(
            "Manual live evaluation requires response_cache_size=0"
        )
    dataset = load_manual_test_dataset(dataset_path)
    cases = _selected_cases(dataset, case_ids, limit)
    run_dir = run_dir.resolve()
    manifest_path = run_dir / "run.json"
    if manifest_path.exists():
        if not resume:
            raise FileExistsError(
                f"Run already exists; pass resume=True: {run_dir}"
            )
        manifest = _json_file(manifest_path)
        _validate_resume_manifest(
            manifest,
            dataset_path=dataset_path,
            cases=cases,
            derived_per_case=derived_per_case,
            service=service,
        )
    else:
        if run_dir.exists() and any(run_dir.iterdir()):
            raise FileExistsError(
                f"Output directory is not empty and has no run.json: {run_dir}"
            )
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = _new_manifest(
            dataset_path=dataset_path,
            dataset=dataset,
            cases=cases,
            service=service,
            derived_per_case=derived_per_case,
        )
        atomic_write_json(manifest_path, manifest)

    setattr(service, "manual_scenario", dataset.scenario)
    planned_total = manifest["planned"]["total_tutor_turns"]
    notify(
        "[START] "
        f"run={manifest['run_id']} cases={len(cases)} "
        f"base_turns={manifest['planned']['base_turns']} "
        f"derived={manifest['planned']['derived_questions']} "
        f"total_tutor_turns={planned_total}"
    )

    base_specs = _base_specs(cases)
    for index, spec in enumerate(base_specs, start=1):
        _run_question_spec(
            run_dir=run_dir,
            service=service,
            spec=spec,
            retry_errors=retry_errors,
            progress=notify,
            ordinal=index,
            total=planned_total,
        )
        rebuild_manual_report(run_dir)

    notify(f"[PHASE] deriving {derived_per_case} questions per base case")
    for index, case in enumerate(cases, start=1):
        previous = _latest_attempt(run_dir, "followups", case.question_id)
        if not _should_run(previous, retry_errors):
            notify(
                f"[DERIVE SKIP {index}/{len(cases)}] {case.question_id} "
                f"status={previous.get('status') if previous else 'unknown'}"
            )
            continue
        base_results = [
            _latest_attempt(run_dir, "attempts", f"{case.question_id}-T{i}")
            or {}
            for i in range(1, len(case.turns) + 1)
        ]
        notify(f"[DERIVE {index}/{len(cases)}] {case.question_id}")
        derived = _followup_attempt(
            case=case,
            base_results=base_results,
            service=service,
        )
        saved = _save_attempt(
            run_dir, "followups", case.question_id, derived
        )
        notify(
            f"[DERIVE {'OK' if derived['status'] == 'ok' else 'ERROR'}] "
            f"{case.question_id} calls="
            f"{derived['generation']['ollama_call_count']} saved={saved}"
        )
        rebuild_manual_report(run_dir)

    derived_specs = _derived_specs(cases, run_dir)
    for offset, spec in enumerate(derived_specs, start=1):
        _run_question_spec(
            run_dir=run_dir,
            service=service,
            spec=spec,
            retry_errors=retry_errors,
            progress=notify,
            ordinal=len(base_specs) + offset,
            total=planned_total,
        )
        rebuild_manual_report(run_dir)

    return rebuild_manual_report(run_dir)
