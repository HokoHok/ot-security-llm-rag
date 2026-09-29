"""Generate a repeatable final readiness report for the local OT tutor POC.

The default run is intentionally quick: it validates scenario assets, indexed
collection counts, and local service health without calling the chat model.
Optional flags run retrieval-only Ground Truth/robustness checks and pytest.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import chromadb

from app.config import PROJECT_ROOT, Settings, collection_name_for_scenario, load_settings
from app.embeddings import OllamaEmbeddingClient
from app.evaluation import evaluate_ground_truth
from app.ingestion import (
    load_evidence_catalog,
    load_ground_truth,
    load_chunks,
    load_index_chunks,
    load_robustness_dataset,
)
from app.openai_adapter import TUTOR_MODELS
from app.retrieval import HybridRetriever
from app.robustness import evaluate_robustness


SCENARIOS = ("ryuk", "industroyer2", "frostygoop")
EXPECTED_SCENARIO_CHUNK_COUNTS = {
    "ryuk": 17,
    "industroyer2": 26,
    "frostygoop": 14,
}


@dataclass(frozen=True)
class Check:
    """One serializable readiness outcome."""

    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def _http_json(url: str, timeout_seconds: float = 4.0) -> tuple[int, object]:
    """Fetch a local JSON endpoint without third-party HTTP dependencies."""

    try:
        with urlopen(url, timeout=timeout_seconds) as response:  # nosec B310
            return response.status, json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return 0, {"error": str(exc)}


def _http_status(url: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
    """Fetch a local HTTP endpoint and return a concise status detail."""

    try:
        with urlopen(url, timeout=timeout_seconds) as response:  # nosec B310
            return response.status, f"HTTP {response.status}"
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return 0, str(exc)


def _scenario_paths(scenario: str) -> dict[str, Path]:
    root = PROJECT_ROOT / "scenarios" / scenario
    return {
        "chunks": root / "chunks.json",
        "catalog": root / "evidence_catalog.json",
        "ground_truth": root / "ground_truth.json",
        "robustness": root / "robustness_questions.json",
    }


def scenario_asset_checks(settings: Settings, scenario: str) -> dict[str, object]:
    """Validate one scenario's schemas and persistent Chroma collection."""

    paths = _scenario_paths(scenario)
    checks: list[Check] = []
    try:
        chunks = load_index_chunks(
            paths["chunks"],
            PROJECT_ROOT / "scenarios" / "common" / "concept_chunks.json",
            scenario,
        )
        scenario_chunk_count = len(load_chunks(paths["chunks"]))
        expected = EXPECTED_SCENARIO_CHUNK_COUNTS[scenario]
        checks.append(
            Check(
                "chunk_schema_and_count",
                scenario_chunk_count == expected,
                f"scenario chunks={scenario_chunk_count}; expected={expected}",
            )
        )
    except Exception as exc:
        chunks = []
        checks.append(Check("chunk_schema_and_count", False, str(exc)))

    try:
        catalog = load_evidence_catalog(paths["catalog"])
        checks.append(
            Check(
                "evidence_catalog",
                catalog.scenario == scenario,
                f"evidence records={len(catalog.evidence)}",
            )
        )
    except Exception as exc:
        checks.append(Check("evidence_catalog", False, str(exc)))

    try:
        ground_truth = load_ground_truth(paths["ground_truth"])
        checks.append(
            Check(
                "ground_truth_schema",
                ground_truth.scenario == scenario,
                f"Ground Truth cases={len(ground_truth.cases)}",
            )
        )
    except Exception as exc:
        checks.append(Check("ground_truth_schema", False, str(exc)))

    try:
        robustness = load_robustness_dataset(paths["robustness"])
        checks.append(
            Check(
                "robustness_schema",
                robustness.scenario == scenario,
                f"robustness cases={len(robustness.questions)}",
            )
        )
    except Exception as exc:
        checks.append(Check("robustness_schema", False, str(exc)))

    collection_name = collection_name_for_scenario(settings.collection_name, scenario)
    try:
        client = chromadb.PersistentClient(path=str(settings.chroma_path))
        collection = client.get_collection(collection_name)
        indexed_count = collection.count()
        expected_indexed = len(chunks)
        checks.append(
            Check(
                "persistent_chroma_collection",
                indexed_count == expected_indexed,
                f"collection={collection_name}; count={indexed_count}; expected={expected_indexed}",
            )
        )
    except Exception as exc:
        checks.append(
            Check("persistent_chroma_collection", False, f"{collection_name}: {exc}"))

    return {
        "scenario": scenario,
        "checks": [check.to_dict() for check in checks],
        "passed": all(check.passed for check in checks),
    }


def runtime_checks(settings: Settings) -> list[Check]:
    """Check the three local processes that make the display layer usable."""

    checks: list[Check] = []
    ollama_status, ollama_payload = _http_json(f"{settings.ollama_base_url}/api/tags")
    model_names = {
        item.get("name")
        for item in ollama_payload.get("models", [])
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    } if isinstance(ollama_payload, Mapping) else set()
    required_models = {settings.chat_model, settings.embed_model}
    missing_models = sorted(required_models - model_names)
    checks.append(
        Check(
            "ollama",
            ollama_status == 200 and not missing_models,
            "models available" if not missing_models else "missing: " + ", ".join(missing_models),
        )
    )

    adapter_status, adapter_payload = _http_json("http://127.0.0.1:8001/v1/models")
    adapter_ids = {
        item.get("id")
        for item in adapter_payload.get("data", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    } if isinstance(adapter_payload, Mapping) else set()
    expected_ids = {model.model_id for model in TUTOR_MODELS}
    missing_adapter_models = sorted(expected_ids - adapter_ids)
    checks.append(
        Check(
            "openai_adapter",
            adapter_status == 200 and not missing_adapter_models,
            "six scenario models available"
            if not missing_adapter_models
            else "missing: " + ", ".join(missing_adapter_models),
        )
    )

    webui_status, webui_detail = _http_status("http://127.0.0.1:3000")
    checks.append(Check("open_webui", webui_status == 200, webui_detail))
    return checks


def retrieval_evaluations(settings: Settings) -> dict[str, object]:
    """Run instructor-only retrieval checks without calling the chat model."""

    results: list[dict[str, object]] = []
    for scenario in SCENARIOS:
        embedder = OllamaEmbeddingClient(settings.ollama_base_url, settings.embed_model)
        retriever = HybridRetriever.from_persistent_chroma(
            chroma_path=str(settings.chroma_path),
            collection_name=collection_name_for_scenario(settings.collection_name, scenario),
            embedder=embedder,
            dense_weight=settings.dense_weight,
            sparse_weight=settings.sparse_weight,
        )
        paths = _scenario_paths(scenario)
        ground_truth = evaluate_ground_truth(
            load_ground_truth(paths["ground_truth"]), retriever, settings.top_k
        )
        robustness = evaluate_robustness(
            load_robustness_dataset(paths["robustness"]), retriever, settings.top_k
        )
        results.append(
            {
                "scenario": scenario,
                "ground_truth": ground_truth.to_dict()["summary"],
                "robustness": robustness.to_dict()["summary"],
            }
        )
    return {"mode": "retrieval_only", "scenarios": results}


def pytest_check(run: bool) -> dict[str, object]:
    """Optionally run the repository test suite and retain a concise result."""

    if not run:
        return {"ran": False, "passed": None, "detail": "not requested"}
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip().splitlines()
    return {
        "ran": True,
        "passed": completed.returncode == 0,
        "return_code": completed.returncode,
        "detail": output[-1] if output else "pytest returned no output",
    }


def build_report(
    settings: Settings,
    *,
    with_retrieval_evaluations: bool,
    with_pytest: bool,
) -> dict[str, object]:
    """Build one report that never invokes Llama chat generation."""

    scenarios = [scenario_asset_checks(settings, scenario) for scenario in SCENARIOS]
    runtime = runtime_checks(settings)
    report: dict[str, object] = {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(),
        "purpose": "Final local OT security RAG readiness check; no chat-model generation.",
        "configuration": {
            "chat_model": settings.chat_model,
            "embedding_model": settings.embed_model,
            "chroma_path": str(settings.chroma_path),
            "top_k": settings.top_k,
        },
        "scenario_assets": scenarios,
        "runtime": [check.to_dict() for check in runtime],
        "pytest": pytest_check(with_pytest),
    }
    if with_retrieval_evaluations:
        report["retrieval_evaluations"] = retrieval_evaluations(settings)
    retrieval_passed = True
    if with_retrieval_evaluations:
        retrieval_results = report["retrieval_evaluations"]  # type: ignore[index]
        retrieval_passed = all(
            item["ground_truth"]["passed_cases"] == item["ground_truth"]["total_cases"]
            and item["robustness"]["passed_cases"] == item["robustness"]["total_cases"]
            for item in retrieval_results["scenarios"]
        )
    report["overall_pass"] = (
        all(item["passed"] for item in scenarios)
        and all(check.passed for check in runtime)
        and (not with_pytest or bool(report["pytest"]["passed"]))  # type: ignore[index]
        and retrieval_passed
    )
    return report


def format_markdown(report: Mapping[str, object]) -> str:
    """Render a concise report suitable for final-demo evidence."""

    lines = [
        "# OT Security Tutor Final Validation",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Overall: {'PASS' if report['overall_pass'] else 'CHECK REQUIRED'}",
        f"- Chat model: {report['configuration']['chat_model']}",  # type: ignore[index]
        "",
        "## Scenario assets and indexes",
        "",
        "| Scenario | Asset/index checks | Status |",
        "|---|---:|---|",
    ]
    for scenario in report["scenario_assets"]:  # type: ignore[index]
        checks = scenario["checks"]  # type: ignore[index]
        passed = sum(item["passed"] for item in checks)  # type: ignore[index]
        lines.append(
            f"| {scenario['scenario']} | {passed}/{len(checks)} | "  # type: ignore[index]
            f"{'PASS' if scenario['passed'] else 'CHECK REQUIRED'} |"  # type: ignore[index]
        )

    lines.extend(["", "## Local runtime", ""])
    for check in report["runtime"]:  # type: ignore[index]
        lines.append(
            f"- {'PASS' if check['passed'] else 'CHECK REQUIRED'} — "  # type: ignore[index]
            f"{check['name']}: {check['detail']}"  # type: ignore[index]
        )

    pytest_result = report["pytest"]  # type: ignore[index]
    lines.extend(["", "## Test suite", ""])
    lines.append(
        "- "
        + (
            f"{'PASS' if pytest_result['passed'] else 'CHECK REQUIRED'} — {pytest_result['detail']}"
            if pytest_result["ran"]
            else "Not run by this command. Use --with-pytest for a fresh result."
        )
    )

    retrieval = report.get("retrieval_evaluations")
    if isinstance(retrieval, Mapping):
        lines.extend(["", "## Retrieval-only evaluations", ""])
        for scenario in retrieval["scenarios"]:  # type: ignore[index]
            ground_truth = scenario["ground_truth"]  # type: ignore[index]
            robustness = scenario["robustness"]  # type: ignore[index]
            lines.append(
                f"- {scenario['scenario']}: Ground Truth "  # type: ignore[index]
                f"{ground_truth['passed_cases']}/{ground_truth['total_cases']}; "
                f"robustness {robustness['passed_cases']}/{robustness['total_cases']}"
            )
    lines.append("")
    return "\n".join(lines)


def write_report(report: Mapping[str, object], output_path: Path) -> tuple[Path, Path]:
    """Write matching UTF-8 JSON and Markdown readiness reports."""

    json_path = output_path.with_suffix(".json")
    markdown_path = output_path.with_suffix(".md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(format_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def main() -> None:
    """Run quick or full final validation from the project root."""

    parser = argparse.ArgumentParser(description="Generate final OT tutor readiness reports")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "final_validation",
        help="Report base path without extension",
    )
    parser.add_argument(
        "--with-retrieval-evaluations",
        action="store_true",
        help="Run Ground Truth and robustness retrieval-only checks for all scenarios",
    )
    parser.add_argument(
        "--with-pytest",
        action="store_true",
        help="Run pytest -q and include its fresh result",
    )
    args = parser.parse_args()
    report = build_report(
        load_settings(),
        with_retrieval_evaluations=args.with_retrieval_evaluations,
        with_pytest=args.with_pytest,
    )
    json_path, markdown_path = write_report(report, args.output)
    print(format_markdown(report))
    print(f"[OK] JSON: {json_path}")
    print(f"[OK] Markdown: {markdown_path}")
    if not report["overall_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
