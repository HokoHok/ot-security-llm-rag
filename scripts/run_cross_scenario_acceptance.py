"""Run a compact, repeatable live acceptance sample across all scenarios.

The runner selects six representative instructor-only robustness questions
per scenario.  Each base response produces two standalone follow-up questions
through the existing validated follow-up generator, then submits both follow-
ups as independent tutor turns.  It records structural success and review
signals, not a claim that semantic correctness has been fully automated.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import load_settings
from app.followups import deterministic_followups
from app.ingestion import load_robustness_dataset
from app.manual_evaluation import atomic_write_json
from app.schemas import TutorResponse
from app.service import TutorService

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_SELECTIONS: dict[str, tuple[str, ...]] = {
    "ryuk": (
        "RYUK-R001", "RYUK-R003", "RYUK-R011", "RYUK-R015",
        "RYUK-R019", "RYUK-R021",
    ),
    "industroyer2": (
        "IND2-R001", "IND2-R003", "IND2-R011", "IND2-R015",
        "IND2-R019", "IND2-R021",
    ),
    "frostygoop": (
        "FROSTY-R001", "FROSTY-R003", "FROSTY-R011", "FROSTY-R015",
        "FROSTY-R019", "FROSTY-R021",
    ),
}


def _response_record(session: Any) -> dict[str, Any]:
    """Serialize only student-relevant result data and review signals."""

    return {
        "status": "ok",
        "scope": session.question_scope,
        "retrieved_chunk_ids": [item.chunk_id for item in session.retrieved],
        "retrieved_evidence_ids": [
            evidence_id
            for item in session.retrieved
            for evidence_id in item.evidence_ids
        ],
        "requires_unknowns": (
            session.evidence_adequacy.require_unknowns
            if session.evidence_adequacy is not None
            else False
        ),
        "unknowns_present": bool(session.response.unknowns),
        "generation_attempts": session.generation_attempts,
        "generation_seconds": session.generation_seconds,
        "response": session.response.model_dump(),
    }


def _ask_independently(
    service: TutorService,
    *,
    scenario: str,
    question: str,
    response_mode: str,
) -> dict[str, Any]:
    """Submit one standalone turn and preserve errors in the report."""

    service.clear_conversation()
    started = perf_counter()
    try:
        session = service.ask(
            question=question,
            scenario=scenario,
            response_mode=response_mode,  # type: ignore[arg-type]
        )
        return {
            "question": question,
            "wall_seconds": perf_counter() - started,
            **_response_record(session),
        }
    except Exception as exc:  # pragma: no cover - integration protection
        return {
            "question": question,
            "wall_seconds": perf_counter() - started,
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def run_acceptance(output_path: Path) -> dict[str, Any]:
    """Run 18 base questions and two response-derived questions per base."""

    settings = replace(load_settings(), response_cache_size=0)
    scenarios: list[dict[str, Any]] = []
    total_turns = 0
    successful_turns = 0
    followup_failures = 0

    for scenario, selected_ids in _SELECTIONS.items():
        dataset_path = PROJECT_ROOT / "scenarios" / scenario / "robustness_questions.json"
        dataset = load_robustness_dataset(dataset_path)
        by_id = {item.question_id: item for item in dataset.questions}
        service = TutorService.from_settings(settings, scenario=scenario)
        scenario_cases: list[dict[str, Any]] = []

        for ordinal, question_id in enumerate(selected_ids, start=1):
            case = by_id[question_id]
            print(
                f"[{scenario} {ordinal}/6] base {question_id}",
                flush=True,
            )
            base = _ask_independently(
                service,
                scenario=scenario,
                question=case.question,
                response_mode="explain",
            )
            total_turns += 1
            successful_turns += base["status"] == "ok"
            record: dict[str, Any] = {
                "question_id": question_id,
                "category": case.category,
                "base": base,
                "followup_generation": None,
                "derived": [],
            }
            if base["status"] != "ok":
                scenario_cases.append(record)
                continue

            try:
                trace = deterministic_followups(
                    base_question=case.question,
                    response=TutorResponse.model_validate(base["response"]),
                    question_scope=base["scope"],
                    safety_sensitive=case.category == "safety_boundary",
                    policy_sensitive=case.category in {
                        "safety_boundary", "prompt_injection",
                    },
                )
                record["followup_generation"] = {
                    "status": "ok",
                    "source": "deterministic_response_derived",
                    "attempts": 0,
                    "errors": [],
                    "questions": [item.model_dump() for item in trace.questions],
                }
            except Exception as exc:  # pragma: no cover - integration protection
                followup_failures += 1
                record["followup_generation"] = {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                scenario_cases.append(record)
                continue

            for derived in trace.questions[:2]:
                print(
                    f"[{scenario} {ordinal}/6] derived {derived.kind}",
                    flush=True,
                )
                result = _ask_independently(
                    service,
                    scenario=scenario,
                    question=derived.question,
                    response_mode="explain",
                )
                result["kind"] = derived.kind
                record["derived"].append(result)
                total_turns += 1
                successful_turns += result["status"] == "ok"
            scenario_cases.append(record)
        scenarios.append({"scenario": scenario, "cases": scenario_cases})

    report = {
        "schema_version": "1.0",
        "purpose": "Cross-scenario live acceptance sample; human semantic review remains required.",
        "selection": {key: list(value) for key, value in _SELECTIONS.items()},
        "summary": {
            "base_questions": 18,
            "derived_questions_per_base": 2,
            "planned_tutor_turns": 54,
            "completed_tutor_turns": total_turns,
            "successful_tutor_turns": successful_turns,
            "failed_tutor_turns": total_turns - successful_turns,
            "followup_generation_failures": followup_failures,
        },
        "scenarios": scenarios,
    }
    atomic_write_json(output_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "cross-scenario-acceptance-live.json",
    )
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    report = run_acceptance(output)
    print(f"[REPORT] {output.resolve()}")
    print(report["summary"])


if __name__ == "__main__":
    main()
