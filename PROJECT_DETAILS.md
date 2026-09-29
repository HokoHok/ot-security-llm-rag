# OT Security Tutor

> 此文件保留實習交接時的完整技術說明。公開 GitHub 版本的檔案範圍與最新安裝入口請以 [README](README.md) 為準；其中提到的主管來源資料、VM 備份、逐題 JSON 及本機向量索引未隨公開版本提供。

> **Status:** Completed proof of concept / handoff baseline<br>
> **Purpose:** Local LLM + hybrid RAG for evidence-grounded OT incident lookup<br>
> **中文導覽:** Start with [`專案導覽.md`](專案導覽.md) for a concise project map.<br>
> **Handoff:** Continue with [`HANDOFF.md`](HANDOFF.md), then use [`docs/RAG_RUNBOOK.md`](docs/RAG_RUNBOOK.md) for detailed operations.

### Current implementation status

The repository now has a working three-scenario proof-of-concept pipeline.

- Available now: maintained application code, scenario knowledge, normalized evidence, evaluation datasets, final validation reports, presentation assets, and operating documentation.
- Implemented at the repository root: 17 validated Ryuk chunks, a 16-record evidence catalog, 11 teacher-side Ground Truth cases, a 50-question robustness set, a 25-case live manual set, persistent ChromaDB indexing, BM25 + jieba retrieval, weighted RRF, five-result scope/policy routing, evidence-adequacy checks, exact evidence-ID pinning, Llama teaching-response generation, evidence/control-character/semantic validation, conservative proof-gap versus absence protection, Ground Truth/robustness evaluation, resumable follow-up batch evaluation, and deterministic tests.
- Industroyer2: 26 normalized-evidence chunks, a 26-record evidence catalog, 9 instructor-side Ground Truth cases, an independent Chroma collection, retrieval evaluation, scenario-aware validated generation, and UI support.
- FrostyGoop: 14 normalized-evidence chunks, a 14-record evidence catalog, 6 instructor-side Ground Truth cases, an independent Chroma collection, retrieval evaluation, and UI support. It is explicitly a report-derived safe reconstruction, not a real malware or PLC reproduction.
- Shared OT concept layer: both scenario indexes also include a small, source-traceable reference set for general concepts (currently IEC-104 and Windows Event IDs 4104/4688). Routing separates event facts, general concepts, and explicit comparisons, so Lab-only fields are not presented as industry standards.
- Implemented user interface: a local Tkinter desktop window with hint/explain modes, formatted teaching output, optional developer retrieval/Ollama trace, and a per-scenario two-turn in-memory follow-up context.
- Not yet implemented: automatic progressive-hint delivery, grading of free-form student answers, and student evidence selection.
- Open WebUI integration is available through the local OpenAI-compatible adapter; the Tkinter UI remains available for developer inspection.

## 1. Project Overview

This project is a proof of concept for a **local-LLM-assisted OT cybersecurity incident reconstruction tutor**.

The system will organize public incident reports, lab logs, packet summaries, operating procedures, expected outputs, and Ground Truth into structured data. A hybrid RAG pipeline will retrieve relevant evidence and provide it to a local Llama model, which will act as a teaching assistant.

The maintained scenarios are **Ryuk-like Safe Emulation**, **Industroyer2-like Safe Emulation**, and **FrostyGoop report-derived Safe Reconstruction**.

## 2. Project Goals

The project aims to demonstrate that a local LLM can:

1. Understand a predefined OT cybersecurity teaching scenario.
2. Retrieve relevant evidence from structured scenario data.
3. Distinguish known facts, reasonable inferences, and unknowns.
4. Reference valid `evidence_id` values.
5. Provide gradual hints instead of immediately revealing full answers.
6. Use Taiwan Traditional Chinese for student-facing responses.
7. Keep the scenario reproducible and controllable.
8. Avoid treating safe simulations as real malware behavior.

This project does **not** aim to prove that a local 8B model is more capable than ChatGPT or Claude in general reasoning.

Its value is based on:

- local execution;
- fixed model version;
- controlled knowledge scope;
- repeatable prompts and outputs;
- scenario-specific Ground Truth;
- closed experimental data;
- evidence-grounded teaching guidance.

## 3. System Architecture

```text
Incident VMs
Kali / USER01 / DC01
        ↓
SENSOR01
Collect and normalize logs
        ↓
JSON / Markdown exports
        ↓
Windows development host
        ↓
Dense Retrieval
Embedding Model + ChromaDB
        +
Sparse Retrieval
BM25 + jieba
        ↓
Reciprocal Rank Fusion
        ↓
Top relevant chunks
        ↓
Ollama
Llama 3.1 8B
        ↓
Validated JSON response
        ↓
Student-facing teaching hint
```

The maintained implementation uses a fixed RAG pipeline:

```text
Student question
→ lab-identifier normalization
→ scope and policy routing
→ dense + BM25 retrieval
→ exact evidence-ID pinning and rank fusion
→ context construction
→ Llama generation
→ JSON, citation, text-safety, and conservative semantic validation
```

The maintained project scope excludes:

- autonomous agents;
- tool-selection loops;
- fine-tuning;
- complex orchestration frameworks;
- multi-user deployment.

## 4. Current Development Environment

### Windows Host

- Windows 10 / 11
- Visual Studio Code
- Codex
- Python 3.11+
- Ollama
- ChromaDB
- NVIDIA GTX 1660 SUPER 6 GB
- Intel Core i7-12700
- 128 GB RAM

### Local Models

```text
Chat model:
llama3.1:8b-instruct-q5_0

Embedding model:
embeddinggemma:300m-qat-q8_0
```

### Current Runtime Constraints

- Initial context length: `4096`
- Llama currently uses mixed CPU/GPU inference
- Raw logs should not be passed to the model in full
- Retrieval and chunking are required to reduce context size
- Current target is single-user proof-of-concept testing

## 5. Ryuk Teaching Scenario

The current Ryuk environment contains four virtual machines.

### Kali

Responsibilities:

- host a fake download page;
- provide the safe simulation script;
- produce HTTP access logs.

### USER01

Responsibilities:

- open the fake download page;
- download the safe simulation script;
- execute the safe simulation script;
- access the DC01 SMB share using an authorized account.

### DC01

Responsibilities:

- Active Directory;
- DNS;
- SMB share;
- detection;
- restore.

### SENSOR01

Responsibilities:

- collect evidence from Kali and DC01;
- correlate multi-host evidence;
- normalize data;
- export JSON and Markdown files for RAG.

### Ryuk Workflow

```text
delivery
→ download
→ execution
→ impact
→ detection
→ restore
→ collection
→ normalization
```

### Important Ground Truth

- Kali is the HTTP delivery source.
- USER01 is the execution host.
- USER01 is allowed to access the DC01 SMB share.
- Direct Kali-to-DC01 SMB access should be blocked.
- `.RYK` files are safe simulation outputs.
- Caesar Shift is not real Ryuk encryption.
- Experimental file counts are not historical incident statistics.
- No real Ryuk malware sample is used.

## 6. Repository Structure

For a concise Chinese explanation of these directories, see
[`專案導覽.md`](專案導覽.md).

```text
ot-security-llm-rag/
├─ AGENTS.md
├─ HANDOFF.md
├─ README.md
├─ 專案導覽.md
├─ .env.example
├─ .gitignore
├─ requirements.txt
│
├─ app/                       # application, retrieval, generation, validation, UI
├─ scenarios/
│  ├─ common/                 # controlled shared OT concepts
│  ├─ ryuk/
│  ├─ industroyer2/
│  └─ frostygoop/
├─ data/chroma/               # generated per-scenario vector indexes
├─ docs/                      # detailed runbooks and test guides
├─ scripts/                   # demo startup and evaluation utilities
├─ student_materials/         # shared-context A/B material
├─ reports/                   # retained validation and review results
├─ tests/                     # deterministic pytest suite
├─ 情境資訊/                    # source reports, Lab notes, runbooks, figures
├─ LLM檢索系統/                 # demo and presentation handoff material
└─ _archive/                  # reversible pre-handoff archive, not active input
```

The `data/chroma/` contents are generated locally and are not intended for
manual editing.

## 7. Python Dependencies

```text
chromadb
requests
rank-bm25
jieba
pydantic
python-dotenv
pytest
```

The project intentionally avoids LangChain, LlamaIndex, and autonomous agent frameworks. The maintained pipeline is implemented with small Python modules.

## 8. Environment Variables

Example `.env.example`:

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434

CHAT_MODEL=llama3.1:8b-instruct-q5_0
EMBED_MODEL=embeddinggemma:300m-qat-q8_0

CHROMA_PATH=./data/chroma

TOP_K=4
NUM_CTX=4096
NUM_PREDICT=500
OLLAMA_KEEP_ALIVE=15m
RESPONSE_CACHE_SIZE=32

DENSE_WEIGHT=0.7
SPARSE_WEIGHT=0.3

TEMPERATURE=0.15
```

Machine-specific `.env` files should not be committed to Git.

Do not place passwords, access tokens, internal credentials, or unredacted production incident data in `.env.example`, scenario samples, test fixtures, or committed ChromaDB data.

## 8.1 Windows environment setup

The verified Windows setup flow is:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
ollama list
python -m pytest
```

Required Ollama models:

```text
llama3.1:8b-instruct-q5_0
embeddinggemma:300m-qat-q8_0
```

## 9. Knowledge Chunk Format

Each chunk should describe one complete event, operation, evidence relationship, expected output, or safety rule.

Example:

```json
{
  "chunk_id": "RYUK-execution-impact-01",
  "scenario": "ryuk",
  "stage": "execution",
  "source_type": "observed_log",
  "title": "USER01 execution and simulated impact on DC01 Share",
  "text": "The execution log identifies USER01 as the source host, Kali HTTP Server as the payload source, and DC01 RyukShare as the target.",
  "evidence_ids": [
    "RYUK-L02"
  ],
  "keywords": [
    "USER01",
    "Kali",
    "DC01",
    "SMB",
    "execution"
  ]
}
```

### Chunking Rules

- Do not create one chunk for every log line.
- Each chunk should contain a complete meaning.
- Each chunk should answer one small class of questions.
- Preserve `scenario` and `stage`.
- Verifiable claims should reference `evidence_ids`.
- Technical identifiers may be stored in `keywords`.
- Use natural-language `text` for embedding and LLM understanding.

## 10. Ground Truth

`ground_truth.json` defines teacher-side evaluation cases. Each case records the
evidence required for a question, a concise expected-answer direction, expected
known facts, acceptable inferences, expected unknowns, forbidden claims, and
three progressive hint levels.

Example:

```json
{
  "schema_version": "1.0",
  "scenario": "ryuk",
  "purpose": "Instructor-only evaluation baseline",
  "cases": [
    {
      "question_id": "RYUK-Q01",
      "topic": "execution_attribution",
      "canonical_question": "為什麼 Kali 不是直接修改 DC01 Share 的主機？",
      "expected_answer": "Kali 是 HTTP 內容提供端；Lab impact 由 USER01 經既有 SMB 權限作用於 DC01 Share。",
      "required_evidence_ids": ["RYUK-C01", "RYUK-L02", "RYUK-N01"],
      "expected_known_facts": [
        "Kali 是 HTTP 內容來源。",
        "USER01 是執行端，DC01 RyukShare 是目標。"
      ],
      "acceptable_inferences": [
        "依實驗設計與執行紀錄，影響路徑是 USER01 經 SMB 存取 DC01。"
      ],
      "expected_unknowns": [
        "目前沒有防火牆日誌或封包擷取可證明 Kali 從未嘗試直接 SMB。"
      ],
      "forbidden_claims": [
        "捏造未記錄的漏洞、工具或攻擊流程。"
      ],
      "hint_levels": [
        "先比較三台主機的角色。",
        "再查看執行來源與 SMB 目標。",
        "最後區分實驗拓樸與逐封包證據。"
      ]
    }
  ]
}
```

Ground Truth is instructor-only evaluation data. It is not embedded into the
student retrieval collection and should not be fully revealed at the beginning
of an exercise.

## 11. Student Input

Students should not be required to manually write raw JSON.

A student may answer in natural language:

```text
I think USER01 executed the downloaded script before the DC01 shared folder changed.
```

The interface may also allow students to select supporting evidence.

The backend converts the input into structured data:

```json
{
  "scenario": "ryuk",
  "stage": "impact",
  "student_claim": "I think USER01 executed the downloaded script before the DC01 shared folder changed.",
  "evidence_ids": [
    "RYUK-L12",
    "RYUK-L13"
  ],
  "confidence": "medium"
}
```

## 12. Llama Output

The local model should return a validated JSON structure.

Example:

```json
{
  "answer": "Your reasoning is mostly correct.",
  "known_facts": [
    "USER01 executed the safe simulation script.",
    "The DC01 share contained .RYK test files."
  ],
  "reasonable_inferences": [
    "USER01 execution is related to the file changes on DC01."
  ],
  "unknowns": [
    "The provided evidence does not prove that real Ryuk malware was used."
  ],
  "evidence_ids": [
    "RYUK-L12",
    "RYUK-L13"
  ],
  "next_hint": "Review the allowed communication paths between Kali, USER01, and DC01."
}
```

### Output Rules

- Use Taiwan Traditional Chinese for student-facing responses.
- Answer only from retrieved context.
- Do not cite nonexistent `evidence_id` values.
- All required fields must be present; arrays must not be `null`.
- Reject unexpected top-level fields during backend validation.
- Separate facts, inferences, and unknowns.
- State when evidence is insufficient.
- Do not add unsupported vulnerabilities or tools.
- Do not reveal more than the current hint level allows.
- Do not describe a safe simulation as real malware.

The first-version schema keeps a single top-level `evidence_ids` list for backward compatibility. The backend can verify that every cited ID exists in the retrieved context, but this alone does not mechanically prove a one-to-one mapping between every sentence and an evidence ID. Claim-level evidence mapping may be proposed later as a versioned schema change.

The response must contain at least one useful `unknowns` entry when no relevant chunk is retrieved, when a required relationship is absent from the retrieved context, or when the retrieved evidence cannot support the student's claim.

## 13. Hybrid Retrieval

The maintained implementation combines dense and sparse retrieval.

### Dense Retrieval

```text
EmbeddingGemma
→ Ollama /api/embed
→ ChromaDB
```

Best suited for:

- natural-language meaning;
- paraphrases;
- Chinese descriptions;
- abstract event questions.

### Sparse Retrieval

```text
BM25
+ jieba
```

Best suited for:

- host names;
- evidence IDs;
- IP addresses;
- filenames;
- protocol names;
- exact technical terms.

### Fusion

Dense and BM25 rankings are combined with Reciprocal Rank Fusion.

Initial settings:

```text
Dense weight: 0.7
Sparse weight: 0.3
Top K: 4
```

These values are experimental and may be changed after evaluation.

The index must record the embedding model and a data-version fingerprint. If the embedding model or source chunks change, rebuild the collection instead of silently mixing incompatible embeddings.

## 14. Representative Validation Question

The first main test question is:

```text
Why is Kali not the host that directly modified the DC01 Share?
```

Expected retrieved knowledge should include:

- Kali provides HTTP delivery.
- USER01 is the execution host.
- USER01 can access DC01 through SMB.
- Direct Kali-to-DC01 SMB access should be blocked.
- `.RYK` files are safe simulation outputs.

The model must not invent vulnerabilities, malware tools, or attack commands that are absent from the retrieved context.

## 15. SENSOR01 Data Import

The current workflow is manual.

On SENSOR01:

```bash
~/incident-data/ryuk/collect_ryuk.sh
python3 ~/incident-data/ryuk/normalize_ryuk.py
```

Expected outputs include:

```text
ryuk_incident_summary.json
ryuk_ai_brief.md
```

These files can be copied into:

```text
scenarios/ryuk/input/normalized/
```

The collector also selects the latest timestamped USER01 evidence run from
the read-only DC01 Share and stores it under:

```text
scenarios/ryuk/input/raw/user01/<run_id>/
```

The current normalized summary verifies USER01 execution context,
PowerShell 4103/4104, Security 4688, and exported manifest hashes. An empty
SMB snapshot collected after execution is retained as a limitation; it does
not prove that no earlier SMB session existed.

Legacy source filenames containing `v4`, `stage4`, or `version_d` remain
unchanged for evidence provenance and backward compatibility. They are not
presented to students as separate scenario versions.

## 16. Testing

Launch the local desktop interface:

```powershell
python -m app.ui
```

The default student view shows the validated teaching response. Enable
`顯示開發者追蹤` to inspect retrieved chunk IDs, evidence IDs, dense/BM25
ranks and raw scores, RRF scores, scope/adequacy decisions, validated JSON,
and Ollama token/duration metrics.

The default `提示模式` keeps the first response compact. Select `詳解模式`
for a two-to-three-sentence explanation while preserving the same JSON Schema.

Run all tests:

```powershell
python -m pytest
```

Evaluate all canonical Ground Truth questions without chat generation:

```powershell
python -m app.cli evaluate --scenario ryuk
```

Run the slower live-model evaluation and optionally save a detailed JSON
report:

```powershell
python -m app.cli evaluate --scenario ryuk --with-llm --output ".\reports\ryuk-ground-truth-live.json"
```

Run the 50-question robustness retrieval suite:

```powershell
python -m app.cli evaluate-robustness --scenario ryuk --output ".\reports\ryuk-robustness-retrieval.json"
```

Run the 30-question Industroyer2 natural-language robustness suite:

```powershell
python -m app.cli evaluate-robustness --scenario industroyer2 --output ".\reports\industroyer2-robustness-retrieval.json"
```

For a small live-model sample, select one category and keep the limit low:

```powershell
python -m app.cli evaluate-robustness --scenario industroyer2 --with-llm --category false_premise --limit 3 --output ".\reports\industroyer2-robustness-live-sample.json"
```

Use `--question-id` more than once to build a deliberately mixed live sample:

```powershell
python -m app.cli evaluate-robustness --scenario industroyer2 --with-llm --mode explain --question-id IND2-R001 --question-id IND2-R004 --output ".\reports\industroyer2-robustness-live-sample.json"
```

Run a small live-model sample by category:

```powershell
python -m app.cli evaluate-robustness --scenario ryuk --with-llm --category false_premise --limit 3 --output ".\reports\ryuk-robustness-live-sample.json"
```

Run the 25-case live manual set and generate three validated follow-up
questions from each case's final response. The complete set contains 100
logical questions and 102 independently submitted Tutor turns because two
cases contain a second base turn:

```powershell
python -m app.cli evaluate-manual --scenario ryuk --output-dir ".\reports\ryuk-manual-live"
```

The command writes one atomic attempt per item plus `checkpoint.json` and
`report.json`. Resume an interrupted run without repeating successful items:

```powershell
python -m app.cli evaluate-manual --scenario ryuk --output-dir ".\reports\ryuk-manual-live" --resume
```

The evaluator exits with status code `1` when any case fails. Retrieval
coverage and citation checks are exact. Reports also include Top-1 required
evidence hit rate, first required rank/MRR, and retrieved-evidence precision.
Known-fact/inference coverage and
forbidden-claim similarity are deterministic review aids, not a substitute for
human semantic grading.

For the final human review, follow [docs/RYUK_MANUAL_TEST.md](docs/RYUK_MANUAL_TEST.md).

Live Ground Truth evaluation prints progress for each of the 11 questions. Depending on the
local model speed, it may take several minutes to finish.

`expected_answer` is used only after generation to check the minimum answer
direction. It is never added to the Llama prompt.

Initial tests should include:

```text
test_retrieves_user01_as_execution_host
test_retrieves_kali_as_delivery_source
test_rejects_unknown_evidence_ids
test_reports_missing_information_as_unknown
test_output_matches_json_schema
test_does_not_describe_simulation_as_real_malware
```

Retrieval tests should not depend on nondeterministic Llama output.

Retrieval and generation should be tested separately where possible.

Unit tests must mock Ollama HTTP responses. Tests that call installed local models should be opt-in integration tests so routine test runs remain deterministic.

## 17. Development Principles

- Keep Ryuk, Industroyer2, and FrostyGoop knowledge packages and ChromaDB collections isolated.
- Prefer simple Python modules.
- Do not add an autonomous agent framework without an explicit new requirement.
- Fine-tuning is outside the maintained proof-of-concept scope.
- Do not send complete raw logs directly to Llama.
- Do not hard-code machine IPs or absolute local paths.
- Use environment variables for configuration.
- Explain JSON schema changes before applying them.
- Validate all model-referenced evidence IDs.
- Keep sample data independent from the virtual machines.
- Make small, reviewable changes.
- Run relevant tests after modifications.

## 18. Safety Boundaries

This repository contains safe teaching simulations only.

Do not introduce:

- real Ryuk malware;
- destructive ransomware logic;
- destructive file encryption;
- operational persistence mechanisms;
- unauthorized intrusion functionality;
- modifications that turn simulation scripts into malware.

Allowed content includes:

- reversible test-file transformations;
- test extension changes;
- isolated impact simulation;
- log generation;
- detection;
- restore;
- packet analysis;
- Ground Truth;
- incident timeline reconstruction.

## 19. Current Limitations

- Ryuk, Industroyer2, and FrostyGoop are available through the deterministic CLI RAG pipeline.
- The desktop UI supports all three scenarios. Each selection uses an isolated scenario service and Chroma collection.
- VM-to-Windows data transfer is manual.
- Context length is currently 4096.
- The desktop UI retains at most two recent turns per selected scenario only to resolve follow-up wording. It is not persistent memory, is cleared by the UI clear action or scenario switch, and every follow-up still performs fresh evidence retrieval.
- ChromaDB currently contains a small dataset.
- BM25 may initially be rebuilt at startup.
- Open WebUI is available as an optional browser presentation layer through the local adapter; multi-user deployment is not implemented.
- Canonical backend outputs can be evaluated; free-form student-answer grading is not implemented.
- No fine-tuning has been performed.
- Multi-user support is out of scope.
- This is a proof of concept, not a production system.

## 20. Implementation Summary

The maintained handoff baseline includes:

- three normalized scenario knowledge packages and independent ChromaDB collections;
- dense retrieval, BM25, weighted RRF, scope routing, and evidence-adequacy checks;
- validated Llama JSON generation with evidence-ID whitelisting;
- Ground Truth, robustness, manual, and cross-scenario evaluation data;
- a Tkinter desktop UI and an Open WebUI-compatible local adapter;
- deterministic tests and final-validation reporting.

Optional future work includes automated VM-to-Windows synchronization,
progressive hint delivery, free-form student-answer grading, evidence selection,
and multi-user deployment. These are not required for the completed PoC.

## 21. Codex Workflow

Codex should read these files before modifying the project:

```text
AGENTS.md
README.md
docs/RAG_RUNBOOK.md
```

Before each task, Codex should:

1. inspect existing files;
2. describe the intended modification scope;
3. make small changes;
4. run relevant tests;
5. report modified files;
6. report commands executed;
7. avoid converting the project into a complex agent-based system without explicit approval.

## 22. Proof-of-Concept Completion Criteria

The maintained baseline meets these core criteria:

- all three scenario knowledge packages can be indexed independently;
- natural-language questions retrieve correct evidence;
- dense and BM25 rankings can be combined;
- Llama returns valid JSON;
- cited `evidence_ids` exist in retrieved context;
- facts, inferences, and unknowns are separated;
- safe simulations are not presented as real malicious activity;
- full raw logs are not required in a single prompt;
- the pipeline runs locally through Ollama on Windows;
- the same RAG service is available through CLI, the desktop UI, and Open WebUI.

## 23. Document Status

This README describes the completed proof-of-concept handoff baseline.

When behavior changes, update this file together with the relevant code and tests.
