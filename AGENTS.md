# AGENTS.md

# 工作範圍
工作範圍為本檔案所在的專案根目錄。所有專案路徑以此目錄為基準，避免依賴原開發電腦的絕對路徑。
`情境資訊/` 保留實習來源資料，僅供本機參考；公開前需確認使用權限。

## Project overview

This project is a completed proof-of-concept baseline for a local-LLM-assisted
OT cybersecurity incident retrieval and reconstruction system.

The maintained scenarios are Ryuk-like, Industroyer2-like, and FrostyGoop
report-derived safe reconstruction. Each scenario uses an isolated knowledge
package and ChromaDB collection while sharing the deterministic RAG pipeline.

Main workflow:

1. Incident VMs generate logs and JSON.
2. SENSOR01 normalizes evidence.
3. Windows runs ChromaDB, hybrid retrieval, and Ollama.
4. Llama 3.1 generates evidence-grounded teaching hints.

This is a teaching simulation, not a production security product.

## Required reading

Before modifying the project, read:

1. `HANDOFF.md`
2. `README.md`
3. `docs/RAG_RUNBOOK.md`

Do not assume VM paths, JSON fields, or expected outputs that are not
documented in these files.

## Current technology

- Python 3.11+
- Ollama
- llama3.1:8b-instruct-q5_0
- EmbeddingGemma
- ChromaDB
- BM25 + jieba
- Reciprocal Rank Fusion
- pytest

## Project structure

- `app/`: Python application code
- `scenarios/`: scenario knowledge, Ground Truth, and sample incidents
- `tests/`: automated tests
- `docs/`: architecture and data format documentation
- `scripts/`: indexing and development utilities

## Development rules

- Inspect existing files before creating or overwriting files.
- Make small, reviewable changes.
- Do not redesign unrelated modules.
- Do not introduce unnecessary frameworks.
- Prefer simple Python modules over an agent framework.
- Add type hints to public functions.
- Add clear error messages for API, JSON, and database failures.
- Keep configuration in environment variables.
- Never hard-code host IP addresses, passwords, or local absolute paths.
- Preserve backward compatibility with existing JSON files when possible.
- Explain schema changes before modifying existing schemas.

## RAG rules

The initial system must use a deterministic pipeline:

student question
→ dense retrieval
→ BM25 retrieval
→ rank fusion
→ context construction
→ Ollama generation
→ output validation

Do not implement autonomous tool selection or an agent loop unless explicitly requested.

Each knowledge chunk must contain:

- `chunk_id`
- `scenario`
- `stage`
- `source_type`
- `title`
- `text`
- `evidence_ids`
- `keywords`

All generated claims must be traceable to retrieved `evidence_ids`.

The model must distinguish:

- known facts
- reasonable inferences
- unknown or insufficient evidence

The model must not cite an evidence ID that is absent from the retrieved context.

## Teaching rules

- Student input may use natural language.
- The backend converts student input into structured data.
- Do not require students to manually write raw JSON.
- Do not reveal the complete Ground Truth at the first hint level.
- Hints should progress from general guidance to specific evidence.
- Use Taiwan Traditional Chinese in student-facing responses.
- Keep responses concise and focused on the current learning stage.

## Safety rules

- This repository contains safe teaching simulations only.
- Do not add real malware, destructive payloads, or persistence mechanisms.
- Do not convert simulation scripts into operational malware.
- Do not describe Caesar Shift as real Ryuk encryption.
- Do not describe experimental file counts as historical incident facts.
- Clearly distinguish public incident facts from lab-specific simulation design.

## Configuration

Use these environment variables:

- `OLLAMA_BASE_URL`
- `CHAT_MODEL`
- `EMBED_MODEL`
- `CHROMA_PATH`
- `TOP_K`
- `NUM_CTX`

Do not commit `.env` files containing machine-specific settings.

## Required validation

Before completing Python changes, run:

```bash
python -m pytest
```
