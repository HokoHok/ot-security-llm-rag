from __future__ import annotations

from app.final_validation import SCENARIOS, format_markdown
from app.openai_adapter import TUTOR_MODELS


def test_final_validation_covers_all_supported_scenarios() -> None:
    assert SCENARIOS == ("ryuk", "industroyer2", "frostygoop")
    assert len(TUTOR_MODELS) == 6


def test_final_validation_markdown_shows_runtime_and_retrieval_summary() -> None:
    report = {
        "generated_at": "2026-08-13T16:00:00+08:00",
        "overall_pass": True,
        "configuration": {"chat_model": "llama3.1:8b-instruct-q5_0"},
        "scenario_assets": [
            {
                "scenario": "ryuk",
                "passed": True,
                "checks": [{"passed": True}, {"passed": True}],
            }
        ],
        "runtime": [
            {"name": "ollama", "passed": True, "detail": "models available"}
        ],
        "pytest": {"ran": True, "passed": True, "detail": "289 passed"},
        "retrieval_evaluations": {
            "scenarios": [
                {
                    "scenario": "ryuk",
                    "ground_truth": {"passed_cases": 11, "total_cases": 11},
                    "robustness": {"passed_cases": 30, "total_cases": 30},
                }
            ]
        },
    }

    markdown = format_markdown(report)

    assert "Overall: PASS" in markdown
    assert "ollama: models available" in markdown
    assert "Ground Truth 11/11; robustness 30/30" in markdown
