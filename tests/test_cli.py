from __future__ import annotations

from typing import NoReturn

from app import cli


def _raise_runtime_error(*_args: object, **_kwargs: object) -> NoReturn:
    raise RuntimeError("Ollama test failure")


def test_cli_reports_operational_failure_without_traceback(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_run_ask", _raise_runtime_error)

    exit_code = cli.run_cli(["ask", "--scenario", "ryuk", "test question"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "[ERROR] Ollama test failure" in captured.err
    assert "Traceback" not in captured.err


def test_industroyer2_is_available_for_index_retrieve_evaluate_and_ask() -> None:
    parser = cli._build_parser()
    for command in ("index", "retrieve", "evaluate", "evaluate-robustness", "ask"):
        arguments = [command, "--scenario", "industroyer2"]
        if command == "index":
            arguments.append("--rebuild")
        elif command in {"retrieve", "ask"}:
            arguments.append("source IP evidence")
        assert parser.parse_args(arguments).scenario == "industroyer2"

    selected = parser.parse_args(
        [
            "evaluate-robustness",
            "--scenario",
            "industroyer2",
            "--question-id",
            "IND2-R001",
            "--question-id",
            "IND2-R004",
        ]
    )
    assert selected.question_id == ["IND2-R001", "IND2-R004"]
    assert selected.mode == "hint"


def test_all_scenarios_are_available_for_preliminary_ab_comparison() -> None:
    parser = cli._build_parser()
    for scenario in ("ryuk", "industroyer2", "frostygoop"):
        selected = parser.parse_args(
            [
                "compare",
                "--scenario",
                scenario,
                "--mode",
                "explain",
                "comparison question",
            ]
        )

        assert selected.scenario == scenario
        assert selected.mode == "explain"


def test_frostygoop_is_available_for_index_retrieve_evaluate_and_ask() -> None:
    parser = cli._build_parser()
    for command in ("index", "retrieve", "evaluate", "ask"):
        arguments = [command, "--scenario", "frostygoop"]
        if command == "index":
            arguments.append("--rebuild")
        elif command in {"retrieve", "ask"}:
            arguments.append("What does FC16 Lab evidence support?")
        assert parser.parse_args(arguments).scenario == "frostygoop"
