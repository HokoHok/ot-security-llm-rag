from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from app.config import (
    PROJECT_ROOT,
    Settings,
    collection_name_for_scenario,
    load_settings,
)
from app.ab_comparison import (
    format_ab_comparison,
    load_ab_student_context,
    run_ab_comparison,
)
from app.embeddings import OllamaEmbeddingClient
from app.evaluation import (
    evaluate_ground_truth,
    format_evaluation_report,
    write_evaluation_report,
)
from app.generation import OllamaChatClient, ResponseMode
from app.indexing import chunk_to_document, persist_index
from app.ingestion import (
    load_ground_truth,
    load_index_chunks,
    load_robustness_dataset,
)
from app.manual_evaluation import (
    create_manual_tutor_service,
    format_manual_summary,
    run_manual_evaluation,
)
from app.retrieval import HybridRetriever, RetrievedChunk
from app.robustness import (
    evaluate_robustness,
    format_robustness_report,
    write_robustness_report,
)
from app.service import TutorService
from app.scope import classify_question_scope, scope_uses_retrieval


_INDEXED_SCENARIOS = ("ryuk", "industroyer2", "frostygoop")
_ROBUSTNESS_SCENARIOS = ("ryuk", "industroyer2", "frostygoop")
_AB_COMPARISON_SCENARIOS = ("ryuk", "industroyer2", "frostygoop")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OT Security Tutor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser(
        "index",
        help="Build a persistent scenario index",
    )
    index_parser.add_argument(
        "--scenario",
        choices=_INDEXED_SCENARIOS,
        required=True,
    )
    index_parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Replace the generated collection before indexing",
    )

    retrieve_parser = subparsers.add_parser(
        "retrieve",
        help="Run dense, BM25, and RRF retrieval without generation",
    )
    retrieve_parser.add_argument(
        "--scenario",
        choices=_INDEXED_SCENARIOS,
        required=True,
    )
    retrieve_parser.add_argument(
        "question",
        nargs="+",
        help="Natural-language student question",
    )

    ask_parser = subparsers.add_parser(
        "ask",
        help="Retrieve evidence and generate a validated teaching response",
    )
    ask_parser.add_argument(
        "--scenario",
        choices=_INDEXED_SCENARIOS,
        required=True,
    )

    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare the same Llama question with shared context, with and without RAG",
    )
    compare_parser.add_argument(
        "--scenario",
        choices=_AB_COMPARISON_SCENARIOS,
        required=True,
    )
    compare_parser.add_argument(
        "--mode",
        choices=["hint", "explain"],
        default="explain",
        help="Teaching depth for both conditions (default: explain)",
    )
    compare_parser.add_argument(
        "question",
        nargs="+",
        help="One natural-language question sent to both A and B",
    )
    ask_parser.add_argument(
        "--trace",
        action="store_true",
        help="Show dense, BM25, RRF, chunk, and evidence details",
    )
    ask_parser.add_argument(
        "--mode",
        choices=["hint", "explain"],
        default="hint",
        help="Teaching depth: hint (default) or explain",
    )
    ask_parser.add_argument(
        "question",
        nargs="+",
        help="Natural-language student question",
    )

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="Evaluate canonical questions against instructor Ground Truth",
    )
    evaluate_parser.add_argument(
        "--scenario",
        choices=_INDEXED_SCENARIOS,
        required=True,
    )
    evaluate_parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Also call the chat model and evaluate validated responses",
    )
    evaluate_parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for a detailed UTF-8 JSON report",
    )

    robustness_parser = subparsers.add_parser(
        "evaluate-robustness",
        help="Evaluate scenario paraphrases, false premises, and scope routing",
    )
    robustness_parser.add_argument(
        "--scenario",
        choices=_ROBUSTNESS_SCENARIOS,
        required=True,
    )
    robustness_parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Also call the chat model for in-scope questions",
    )
    robustness_parser.add_argument(
        "--mode",
        choices=["hint", "explain"],
        default="hint",
        help="Student response detail level when --with-llm is used",
    )
    robustness_parser.add_argument(
        "--category",
        choices=[
            "paraphrase",
            "false_premise",
            "insufficient_evidence",
            "safety_boundary",
            "off_topic",
            "language_variant",
            "prompt_injection",
        ],
        help="Evaluate only one question category",
    )
    robustness_parser.add_argument(
        "--limit",
        type=int,
        help="Evaluate only the first N selected questions",
    )
    robustness_parser.add_argument(
        "--question-id",
        action="append",
        help=(
            "Evaluate one exact robustness question ID; may be supplied more "
            "than once, for example RYUK-R019"
        ),
    )
    robustness_parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for a detailed UTF-8 JSON report",
    )

    manual_parser = subparsers.add_parser(
        "evaluate-manual",
        help="Run the 25-case live Llama review and three follow-ups per case",
    )
    manual_parser.add_argument("--scenario", choices=["ryuk"], required=True)
    manual_parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for resumable attempts, checkpoint, and report.json",
    )
    manual_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an existing run without repeating successful records",
    )
    manual_parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="When resuming, retry records whose latest attempt failed",
    )
    manual_parser.add_argument(
        "--limit",
        type=int,
        help="Run only the first N selected cases (useful for a smoke test)",
    )
    manual_parser.add_argument(
        "--question-id",
        action="append",
        help="Run one case ID; may be supplied more than once",
    )
    return parser


def _scenario_chunks_path(scenario: str) -> Path:
    return PROJECT_ROOT / "scenarios" / scenario / "chunks.json"


def _common_concept_chunks_path() -> Path:
    return PROJECT_ROOT / "scenarios" / "common" / "concept_chunks.json"


def _scenario_ground_truth_path(scenario: str) -> Path:
    return PROJECT_ROOT / "scenarios" / scenario / "ground_truth.json"


def _scenario_robustness_path(scenario: str) -> Path:
    return PROJECT_ROOT / "scenarios" / scenario / "robustness_questions.json"


def _scenario_manual_questions_path(scenario: str) -> Path:
    return PROJECT_ROOT / "scenarios" / scenario / "manual_questions.json"


def _run_index(scenario: str, rebuild: bool) -> None:
    settings = load_settings()
    chunks = load_index_chunks(
        _scenario_chunks_path(scenario),
        _common_concept_chunks_path(),
        scenario,
    )
    documents = [chunk_to_document(chunk) for chunk in chunks]

    embedder = OllamaEmbeddingClient(
        settings.ollama_base_url,
        settings.embed_model,
    )
    embeddings = embedder.embed(documents)
    collection = persist_index(
        chunks=chunks,
        embeddings=embeddings,
        chroma_path=settings.chroma_path,
        collection_name=collection_name_for_scenario(
            settings.collection_name,
            scenario,
        ),
        embedding_model=settings.embed_model,
        rebuild=rebuild,
    )

    print(f"[OK] Indexed chunks: {collection.count()}")
    print(f"[OK] Collection: {collection.name}")
    print(f"[OK] ChromaDB: {settings.chroma_path.resolve()}")
    print(f"[OK] Embedding model: {settings.embed_model}")
    print(f"[OK] Embedding dimensions: {len(embeddings[0])}")


def _create_retriever(scenario: str) -> tuple[HybridRetriever, Settings]:
    settings = load_settings()
    embedder = OllamaEmbeddingClient(
        settings.ollama_base_url,
        settings.embed_model,
    )
    retriever = HybridRetriever.from_persistent_chroma(
        chroma_path=str(settings.chroma_path),
        collection_name=collection_name_for_scenario(
            settings.collection_name,
            scenario,
        ),
        embedder=embedder,
        dense_weight=settings.dense_weight,
        sparse_weight=settings.sparse_weight,
    )
    return retriever, settings


def _print_retrieval_trace(
    question: str,
    results: Sequence[RetrievedChunk],
) -> None:
    print(f"[QUESTION] {question}")
    print("[RETRIEVED]")
    for rank, result in enumerate(results, start=1):
        dense_rank = result.dense_rank if result.dense_rank is not None else "-"
        sparse_rank = result.sparse_rank if result.sparse_rank is not None else "-"
        print(
            f"{rank}. {result.chunk_id} "
            f"(RRF={result.rrf_score:.6f}, "
            f"dense={dense_rank}, BM25={sparse_rank})"
        )
        print(f"   title: {result.metadata.get('title', '')}")
        print(f"   evidence_ids: {', '.join(result.evidence_ids)}")


def _run_retrieve(scenario: str, question_parts: Sequence[str]) -> None:
    retriever, settings = _create_retriever(scenario)
    question = " ".join(question_parts).strip()
    results = retriever.retrieve(
        question=question,
        scenario=scenario,
        top_k=settings.top_k,
    )
    _print_retrieval_trace(question, results)


def _run_ask(
    scenario: str,
    question_parts: Sequence[str],
    trace: bool,
    mode: ResponseMode = "hint",
) -> None:
    settings = load_settings()
    question = " ".join(question_parts).strip()
    service = TutorService.from_settings(settings, scenario=scenario)
    def print_progress(message: str) -> None:
        print(f"[PROGRESS] {message}")

    session = service.ask(
        question=question,
        scenario=scenario,
        response_mode=mode,
        progress=print_progress,
    )
    if trace:
        _print_retrieval_trace(question, session.retrieved)
    llama_calls_text = (
        "n/a"
        if session.generation_attempts is None
        else str(session.generation_attempts)
    )
    timing = (
        "cache"
        if session.cache_hit
        else (
            f"retrieval={session.retrieval_seconds:.2f}s, "
            f"generation={session.generation_seconds:.2f}s, "
            f"llama_calls={llama_calls_text}"
        )
    )
    print(f"[TIMING] {timing}")
    for metrics in session.ollama_metrics:
        tokens_per_second = metrics.output_tokens_per_second
        rate_text = (
            f"{tokens_per_second:.2f}"
            if tokens_per_second is not None
            else "n/a"
        )
        print(
            "[OLLAMA] "
            f"call={metrics.request_number}, "
            f"done_reason={metrics.done_reason or 'unknown'}, "
            f"prompt_tokens={metrics.prompt_eval_count or 'n/a'}, "
            f"output_tokens={metrics.eval_count or 'n/a'}, "
            f"load={metrics.load_duration_seconds or 0:.2f}s, "
            f"eval={metrics.eval_duration_seconds or 0:.2f}s, "
            f"tokens_per_second={rate_text}"
        )
        if metrics.output_may_be_truncated:
            print(
                "[WARNING] Ollama done_reason=length；輸出可能達到 "
                "NUM_PREDICT 上限而被截短。"
            )
    print(
        json.dumps(
            session.response.model_dump(),
            ensure_ascii=False,
            indent=2,
        )
    )


def _run_compare(
    scenario: str,
    question_parts: Sequence[str],
    mode: ResponseMode,
) -> None:
    """Print one preliminary, same-model A/B comparison for human review."""

    question = " ".join(question_parts).strip()
    context_path, student_context = load_ab_student_context(PROJECT_ROOT, scenario)
    retriever, settings = _create_retriever(scenario)
    chat_provider = OllamaChatClient(
        base_url=settings.ollama_base_url,
        model=settings.chat_model,
        num_ctx=settings.num_ctx,
        num_predict=settings.num_predict,
        temperature=settings.temperature,
        keep_alive=settings.ollama_keep_alive,
    )
    question_scope = classify_question_scope(question, scenario=scenario)
    if not scope_uses_retrieval(question_scope):
        raise ValueError(
            "A/B compare supports one in-scope teaching question; "
            f"this question was routed as {question_scope!r}"
        )
    retrieval_service = TutorService(
        retriever=retriever,
        chat_provider=chat_provider,
        top_k=settings.top_k,
        response_cache_size=0,
    )
    retrieved = retrieval_service._retrieve_for_scope(
        question=question,
        scenario=scenario,
        question_scope=question_scope,
    )
    print("[A/B] 正在執行 A：共用情境文件、無 RAG…", flush=True)
    print("[A/B] 隨後執行 B：相同文件加上 Top-K RAG 證據…", flush=True)
    comparison = run_ab_comparison(
        scenario=scenario,
        question=question,
        student_context_path=context_path,
        student_context=student_context,
        retrieved=retrieved,
        chat_provider=chat_provider,
        response_mode=mode,
    )
    print(format_ab_comparison(comparison))


def _run_evaluate(
    scenario: str,
    with_llm: bool,
    output: Path | None,
) -> bool:
    retriever, settings = _create_retriever(scenario)
    ground_truth = load_ground_truth(_scenario_ground_truth_path(scenario))
    chat_provider = (
        OllamaChatClient(
            base_url=settings.ollama_base_url,
            model=settings.chat_model,
            num_ctx=settings.num_ctx,
            num_predict=settings.num_predict,
            temperature=settings.temperature,
            keep_alive=settings.ollama_keep_alive,
        )
        if with_llm
        else None
    )
    report = evaluate_ground_truth(
        ground_truth=ground_truth,
        retriever=retriever,
        top_k=settings.top_k,
        chat_provider=chat_provider,
        progress=print,
    )
    print(format_evaluation_report(report))
    if output is not None:
        output_path = (
            output
            if output.is_absolute()
            else PROJECT_ROOT / output
        )
        written = write_evaluation_report(report, output_path)
        print(f"[REPORT] {written}")
    return report.overall_pass


def _run_robustness_evaluate(
    scenario: str,
    with_llm: bool,
    mode: ResponseMode,
    category: str | None,
    limit: int | None,
    question_ids: Sequence[str] | None,
    output: Path | None,
) -> bool:
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1")

    retriever, settings = _create_retriever(scenario)
    dataset = load_robustness_dataset(_scenario_robustness_path(scenario))
    selected = [
        question
        for question in dataset.questions
        if (category is None or question.category == category)
        and (
            question_ids is None
            or question.question_id in set(question_ids)
        )
    ]
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        raise ValueError("No robustness questions matched the selection")
    dataset = dataset.model_copy(update={"questions": selected})

    chat_provider = (
        OllamaChatClient(
            base_url=settings.ollama_base_url,
            model=settings.chat_model,
            num_ctx=settings.num_ctx,
            num_predict=settings.num_predict,
            temperature=settings.temperature,
            keep_alive=settings.ollama_keep_alive,
        )
        if with_llm
        else None
    )
    report = evaluate_robustness(
        dataset=dataset,
        retriever=retriever,
        top_k=settings.top_k,
        chat_provider=chat_provider,
        progress=print,
        response_mode=mode,
    )
    print(format_robustness_report(report))
    if output is not None:
        output_path = output if output.is_absolute() else PROJECT_ROOT / output
        written = write_robustness_report(report, output_path)
        print(f"[REPORT] {written}")
    return report.overall_pass


def _run_manual_evaluate(
    scenario: str,
    output_dir: Path,
    resume: bool,
    retry_errors: bool,
    limit: int | None,
    question_ids: Sequence[str] | None,
) -> bool:
    if retry_errors and not resume:
        raise ValueError("--retry-errors requires --resume")
    resolved_output = (
        output_dir if output_dir.is_absolute() else PROJECT_ROOT / output_dir
    )
    service = create_manual_tutor_service()
    report = run_manual_evaluation(
        dataset_path=_scenario_manual_questions_path(scenario),
        run_dir=resolved_output,
        service=service,
        case_ids=set(question_ids) if question_ids else None,
        limit=limit,
        resume=resume,
        retry_errors=retry_errors,
        progress=lambda message: print(message, flush=True),
    )
    print(format_manual_summary(report))
    print(f"[REPORT] {(resolved_output / 'report.json').resolve()}")
    return report["summary"]["status"] == "completed"


def main(argv: Sequence[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _build_parser().parse_args(argv)
    if args.command == "index":
        _run_index(args.scenario, args.rebuild)
    elif args.command == "retrieve":
        _run_retrieve(args.scenario, args.question)
    elif args.command == "ask":
        _run_ask(args.scenario, args.question, args.trace, args.mode)
    elif args.command == "compare":
        _run_compare(args.scenario, args.question, args.mode)
    elif args.command == "evaluate":
        passed = _run_evaluate(
            args.scenario,
            args.with_llm,
            args.output,
        )
        if not passed:
            raise SystemExit(1)
    elif args.command == "evaluate-robustness":
        passed = _run_robustness_evaluate(
            args.scenario,
            args.with_llm,
            args.mode,
            args.category,
            args.limit,
            args.question_id,
            args.output,
        )
        if not passed:
            raise SystemExit(1)
    elif args.command == "evaluate-manual":
        passed = _run_manual_evaluate(
            args.scenario,
            args.output_dir,
            args.resume,
            args.retry_errors,
            args.limit,
            args.question_id,
        )
        if not passed:
            raise SystemExit(1)


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Run the CLI without exposing an unhandled traceback to students.

    Evaluation commands deliberately use ``SystemExit(1)`` to communicate a
    failed benchmark and remain unchanged.  Operational failures such as a
    missing Chroma collection, an unavailable Ollama server, or exhausted
    response validation are reported as a concise error and return code 2.
    """

    try:
        main(argv)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
