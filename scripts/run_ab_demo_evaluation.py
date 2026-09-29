"""Run a compact, repeatable A/B sample for the final presentation.

A receives the same scenario card available to a student. B receives that
same card plus the production Top-K hybrid-retrieved evidence. Ground Truth is
not supplied to either condition.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.ab_comparison import load_ab_student_context, run_ab_comparison
from app.config import PROJECT_ROOT, collection_name_for_scenario, load_settings
from app.embeddings import OllamaEmbeddingClient
from app.generation import OllamaChatClient
from app.retrieval import HybridRetriever
from app.scope import classify_question_scope, scope_uses_retrieval


DEMO_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "RYUK-AB01",
        "ryuk",
        "為什麼 Kali 不是直接修改 DC01 Share 的主機？",
    ),
    (
        "RYUK-AB02",
        "ryuk",
        "100 個 .RYK 檔案是否證明發生真實 Ryuk 加密？",
    ),
    (
        "IND2-AB01",
        "industroyer2",
        "為什麼只看到 source_ip=10.10.60.20，不能判定是正常 ENG 維護？",
    ),
    (
        "IND2-AB02",
        "industroyer2",
        "Safe CaddyWiper-like lockout 後，iec104_sent=false 能證明什麼？不能證明什麼？",
    ),
    (
        "FROSTY-AB01",
        "frostygoop",
        "FC06 寫入是否能證明是真實 FrostyGoop 對 PLC 的攻擊？",
    ),
    (
        "FROSTY-AB02",
        "frostygoop",
        "只看單一 Modbus 寫入封包，能直接證明造成哪些實體設備影響嗎？",
    ),
)


def _single_line(text: str, limit: int = 96) -> str:
    """Return a compact answer excerpt for a slide-friendly Markdown table."""

    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _retriever_for_scenario(scenario: str) -> HybridRetriever:
    """Open the persistent collection without modifying it."""

    settings = load_settings()
    embedder = OllamaEmbeddingClient(
        settings.ollama_base_url,
        settings.embed_model,
    )
    return HybridRetriever.from_persistent_chroma(
        chroma_path=str(settings.chroma_path),
        collection_name=collection_name_for_scenario(
            settings.collection_name,
            scenario,
        ),
        embedder=embedder,
        dense_weight=settings.dense_weight,
        sparse_weight=settings.sparse_weight,
    )


def _run_sample(case_id: str, scenario: str, question: str) -> dict[str, Any]:
    """Run the two conditions once and return a serializable comparison row."""

    settings = load_settings()
    scope = classify_question_scope(question, scenario=scenario)
    if not scope_uses_retrieval(scope):
        raise ValueError(f"Demo question {case_id} was routed as {scope!r}")

    retriever = _retriever_for_scenario(scenario)
    retrieved = retriever.retrieve(question, scenario, settings.top_k)
    context_path, student_context = load_ab_student_context(PROJECT_ROOT, scenario)
    chat_provider = OllamaChatClient(
        base_url=settings.ollama_base_url,
        model=settings.chat_model,
        num_ctx=settings.num_ctx,
        num_predict=settings.num_predict,
        temperature=settings.temperature,
        keep_alive=settings.ollama_keep_alive,
    )
    comparison = run_ab_comparison(
        scenario=scenario,
        question=question,
        student_context_path=context_path,
        student_context=student_context,
        retrieved=retrieved,
        chat_provider=chat_provider,
        response_mode="explain",
    )
    baseline = comparison.baseline_response
    rag = comparison.rag_response
    retrieved_ids = [
        evidence_id
        for chunk in comparison.retrieved
        for evidence_id in chunk.evidence_ids
    ]
    return {
        "case_id": case_id,
        "scenario": scenario,
        "question": question,
        "shared_context": str(context_path.relative_to(PROJECT_ROOT)),
        "condition_a": baseline.model_dump(),
        "condition_b": rag.model_dump(),
        "condition_b_retrieved_chunk_ids": [
            chunk.chunk_id for chunk in comparison.retrieved
        ],
        "condition_b_retrieved_evidence_ids": retrieved_ids,
        "comparison_observations": {
            "same_student_context": True,
            "condition_a_evidence_id_count": len(baseline.evidence_ids),
            "condition_b_evidence_id_count": len(rag.evidence_ids),
            "condition_b_citations_are_retrieved": set(rag.evidence_ids)
            <= set(retrieved_ids),
            "condition_a_answer_excerpt": _single_line(baseline.answer),
            "condition_b_answer_excerpt": _single_line(rag.answer),
        },
        "ollama_calls": [metric.to_dict() for metric in comparison.ollama_metrics],
    }


def _format_markdown(report: dict[str, Any]) -> str:
    """Render full-method and presentation tables from live sample output."""

    lines = [
        "# LLM 與 RAG A/B 比較結果表",
        "",
        f"- 執行時間：{report['generated_at']}",
        f"- 模型：`{report['configuration']['chat_model']}`",
        "- A：只提供同一情境的 `llm_context.md`；不提供 RAG 證據。",
        "- B：提供相同情境文件，再加入 production Top-4 hybrid retrieval 與 evidence-ID 驗證。",
        "- Ground Truth 未提供給 A 或 B；以下是單次 live sample，適合做質性展示，非統計顯著性結論。",
        "",
        "## 簡報版結果",
        "",
        "| 情境 | 問題類型 | A：僅情境文件 | B：情境文件＋RAG | B 的可追溯證據 |",
        "|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        observation = row["comparison_observations"]
        question_type = row["case_id"].split("-")[-1]
        evidence = ", ".join(row["condition_b"]["evidence_ids"]) or "無"
        lines.append(
            "| "
            f"{row['scenario']} | {question_type} | "
            f"{observation['condition_a_answer_excerpt']} | "
            f"{observation['condition_b_answer_excerpt']} | `{evidence}` |"
        )

    lines.extend(
        [
            "",
            "## 可量化觀察",
            "",
            "| 指標 | A：僅情境文件 | B：情境文件＋RAG |",
            "|---|---:|---:|",
            "| 測試題數 | 6 | 6 |",
            "| 可引用 evidence_id 的回答 | 0/6 | "
            + f"{sum(bool(row['condition_b']['evidence_ids']) for row in report['rows'])}/6 |",
            "| B 的引用皆屬於當次檢索內容 | n/a | "
            + f"{sum(row['comparison_observations']['condition_b_citations_are_retrieved'] for row in report['rows'])}/6 |",
            "| 使用相同情境文件 | 6/6 | 6/6 |",
            "",
            "上述量化項目驗證的是可追溯性與實驗控制，不等同於回答文字一定較自然；自然度仍應由展示者以題目與回答內容人工說明。",
            "",
            "## 詳細紀錄",
            "",
            "完整 A、B JSON 回覆、Top-4 chunks 與 Ollama telemetry 請見同名 `.json` 檔。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    """Write a six-question live A/B report for presentation preparation."""

    settings = load_settings()
    rows = [_run_sample(*question) for question in DEMO_QUESTIONS]
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(),
        "purpose": "Small live A/B sample: shared scenario context versus shared context plus RAG.",
        "configuration": {
            "chat_model": settings.chat_model,
            "embedding_model": settings.embed_model,
            "top_k": settings.top_k,
            "temperature": settings.temperature,
        },
        "rows": rows,
    }
    output_dir = PROJECT_ROOT / "LLM檢索系統"
    output_dir.mkdir(exist_ok=True)
    json_path = output_dir / "AB_Comparison_Results.json"
    markdown_path = output_dir / "AB_Comparison_Results.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_format_markdown(report), encoding="utf-8")
    print(f"[OK] JSON: {json_path}")
    print(f"[OK] Markdown: {markdown_path}")


if __name__ == "__main__":
    main()
