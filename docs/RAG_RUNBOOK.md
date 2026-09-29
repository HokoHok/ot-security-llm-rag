# RAG Operations Runbook

## Implementation status

This runbook describes the verified three-scenario workflow. The `index`,
`retrieve`, `ask`, `compare`, `evaluate`, `evaluate-robustness`, desktop UI,
and Open WebUI adapter commands below are implemented.

Ryuk, Industroyer2, and FrostyGoop use independent knowledge packages,
scenario profiles, and ChromaDB collections. The desktop UI and Open WebUI
adapter expose the corresponding collection-bound service for each scenario.

The maintained Industroyer2 data contains 26 student-retrievable chunks,
26 evidence records, and 9 instructor-side Ground Truth cases. The maintained
FrostyGoop data contains 14 student-retrievable chunks, 14 evidence records,
and 6 instructor-side Ground Truth cases.

The maintained Ryuk teaching data currently contains:

- 17 student-retrievable knowledge chunks;
- 16 evidence catalog records;
- 11 instructor-side Ground Truth evaluation cases.
- 50 instructor-side robustness questions across seven categories.

`ground_truth.json` is not indexed or sent wholesale to the model. It is an
instructor-side baseline for later answer evaluation and progressive hints.

## Current environment

- Development host: Windows
- LLM runtime: Ollama
- Chat model: llama3.1:8b-instruct-q5_0
- Vector database: ChromaDB
- VM data source: SENSOR01

## Data flow

Ryuk VMs
→ SENSOR01
→ ryuk_incident_summary.json
→ USER01 endpoint evidence validation
→ Windows project
→ ingestion
→ hybrid retrieval
→ Llama
→ JSON teaching response

## Current manual workflow

The public GitHub copy includes normalized Ryuk evidence and a curated [evidence summary](../scenarios/ryuk/public_evidence_summary.md). Original Ryuk VM logs are kept only in the local project backup. The collection and re-normalization steps below require access to the original Lab VMs or their raw exports; first-time users can build the included knowledge chunks without those raw files.

1. Run the Ryuk simulation in the VMs.
2. Run collection and normalization on SENSOR01:
   - `~/incident-data/ryuk/collect_ryuk.sh`
   - `python3 ~/incident-data/ryuk/normalize_ryuk.py`
3. Copy these files to Windows:
   - `ryuk_incident_summary.json`
   - `ryuk_ai_brief.md`
   - `raw/collection_metadata.json`
   - the selected `raw/user01/<run_id>/` evidence directory
4. Place them under:
   - `scenarios/ryuk/input/normalized/`
   - `scenarios/ryuk/input/raw/`
5. Rebuild the index:
   - `python -m app.cli index --scenario ryuk`
6. Test hybrid retrieval:
   - `python -m app.cli retrieve --scenario ryuk "..."`
7. Test validated generation:
   - `python -m app.cli ask --scenario ryuk --mode hint "..."`
   - `python -m app.cli ask --scenario ryuk --mode explain "..."`
   - `python -m app.cli ask --scenario industroyer2 --mode explain "..."`
8. Launch the local desktop interface:
   - `python -m app.ui`

### Preliminary A/B command-line comparison

The `compare` command is an instructor-side, human-review aid. It sends the
same question to the same local model twice:

- **A:** the selected scenario's shared student background only (`student_materials/<scenario>/llm_context.md`);
- **B:** the same background plus the normal Top-K hybrid-retrieved evidence,
  production generation refinements, and evidence validation.

It is not a production student command and does not send Ground Truth to
either condition. Run it from the project root:

```powershell
.\.venv\Scripts\python.exe -m app.cli compare --scenario industroyer2 --mode explain "為什麼只看到 source_ip=10.10.60.20，不能判定是正常 ENG 維護？"
```

The terminal prints A, B, B's retrieved chunks, and Ollama telemetry. It is
intended for small human-reviewed samples before any batch A/B evaluation.

The supported scenarios are `ryuk`, `industroyer2`, and `frostygoop`. For
the two additional scenario packages, use the same comparison command with
the corresponding scenario name, for example:

```powershell
.\.venv\Scripts\python.exe -m app.cli compare --scenario ryuk --mode explain "Why is Kali not the direct DC01 Share modification host?"
.\.venv\Scripts\python.exe -m app.cli compare --scenario frostygoop --mode explain "Can a matching Task JSON and PCAP prove that a PLC accepted an FC06 write?"
```

### Open WebUI presentation adapter

Open WebUI is an optional presentation layer, not a second RAG system. Do not
upload chunks, evidence catalogs, Ground Truth, or student materials to its
Knowledge feature. The maintained data path remains:

```text
Open WebUI chat
→ app.openai_adapter (localhost)
→ TutorService
→ ChromaDB dense + BM25 + RRF
→ validated TutorResponse
```

Start the local OpenAI-compatible adapter from the project root. It uses only
the Python standard library and exposes six selectable model labels:

```powershell
.\.venv\Scripts\python.exe -m app.openai_adapter
```

```text
ot-tutor-ryuk-hint                 ot-tutor-ryuk-explain
ot-tutor-industroyer2-hint         ot-tutor-industroyer2-explain
ot-tutor-frostygoop-hint           ot-tutor-frostygoop-explain
```

The adapter binds only to `127.0.0.1:8001` by default. In Open WebUI, add an
OpenAI-compatible connection at **Admin Settings → Connections → OpenAI**:

```text
URL:     http://host.docker.internal:8001/v1
API key: ot-tutor-local
```

The key is a required Open WebUI form value only; the adapter is intentionally
local-only and does not transmit it or accept external connections. Select one
of the six `ot-tutor-*` labels in a new chat. The adapter ignores Open WebUI
system prompts and assistant prose. For an explicit follow-up such as
`那個可以存取 DC01 嗎？`, it may pass exactly one preceding **user question**
to the tutor only to resolve the reference. It clears service follow-up memory
before every request, performs a fresh retrieval, and treats the prior question
as non-evidence. If that prior question names multiple Lab entities, a bare
pronoun such as `它` is considered ambiguous and receives a clarification
request rather than a guessed antecedent. This avoids evidence or context
leaking between browser conversations.

### Final demo startup and validation

Use the demo script after a Windows restart or before a presentation. It
checks Ollama models, starts the existing `ot-tutor-webui` Docker container,
starts the local adapter if necessary, checks all three Chroma collections,
and prints the Open WebUI URL:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_demo.ps1 -OpenBrowser
```

For a slower final check that also runs pytest:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_demo.ps1 -RunQuickValidation
```

The quick report is saved as `reports/final_validation_quick.json` and
`reports/final_validation_quick.md`. To create the complete instructor-side
readiness report with fresh retrieval-only Ground Truth and robustness checks
(it does not call Llama chat generation), run:

```powershell
.\.venv\Scripts\python.exe -m app.final_validation `
  --with-retrieval-evaluations `
  --with-pytest `
  --output .\reports\final_validation
```

Do not modify the original files under `情境資訊/` during import. Copy normalized UTF-8 JSON or Markdown into the scenario input directory.

Source filenames containing `v4`, `stage4`, or `version_d` are legacy
provenance names. Keep them stable unless a migration is planned, but do
not expose them as student-facing scenario versions.

## First-time Windows setup

Prerequisites:

- Python 3.11 or newer;
- Ollama running locally;
- `llama3.1:8b-instruct-q5_0`;
- `embeddinggemma:300m-qat-q8_0`.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
ollama list
```

If PowerShell blocks activation, the virtual environment can still be used directly:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Validation workflow

Run deterministic unit tests without requiring Ollama:

```powershell
python -m pytest
```

Build the Ryuk index:

```powershell
python -m app.cli index --scenario ryuk
```

Run hybrid retrieval for the initial validation question:

```powershell
python -m app.cli retrieve --scenario ryuk "為什麼 Kali 不是直接修改 DC01 Share 的主機？"
```

Ask Llama the initial validation question:

```powershell
python -m app.cli ask --scenario ryuk --mode hint "為什麼 Kali 不是直接修改 DC01 Share 的主機？"
```

Evaluate required evidence for all 11 canonical Ground Truth questions:

```powershell
python -m app.cli evaluate --scenario ryuk
```

Run the slower live-model evaluation and save its detailed JSON report:

```powershell
python -m app.cli evaluate --scenario ryuk --with-llm --output ".\reports\ryuk-ground-truth-live.json"
```

The default evaluation does not call the chat model. It checks exact
required-evidence coverage in Top K and reports Top-1 hit rate, first-hit MRR,
and evidence precision. `--with-llm` additionally checks that
citations remain inside the retrieved whitelist, expected unknowns are
represented, and no likely forbidden claim is found. The command exits with
status `1` when one or more cases fail.

The command prints progress before retrieval and Llama generation for each
case. On the local 8B model the complete run may take several minutes.

Ground Truth expectations are applied only after normal generation. They are
not added to the model prompt and do not alter the response being evaluated.

Forbidden-claim and known-fact/inference similarity scores are deterministic
review signals. They are not treated as perfect semantic judgment and should be
reviewed alongside the generated response in the optional JSON report.

The optional `expected_answer` field is a concise teacher-side direction used
for answer-alignment scoring. It is not sent to the model. Complete
`expected_known_facts` remain diagnostic coverage: a teaching hint only needs
at least one matching known fact, rather than revealing every reference fact.

Evaluate all 50 robustness questions without calling Llama:

```powershell
python -m app.cli evaluate-robustness --scenario ryuk --output ".\reports\ryuk-robustness-retrieval.json"
```

Industroyer2 uses a separate, instructor-only 30-question natural-language
robustness set. It is not indexed with student evidence:

```powershell
python -m app.cli evaluate-robustness --scenario industroyer2 --output ".\reports\industroyer2-robustness-retrieval.json"
```

The robustness set is stored in
`scenarios/ryuk/robustness_questions.json`. It is instructor-only and is not
embedded into ChromaDB. Its categories cover paraphrases, false premises,
insufficient evidence, safety boundaries, clearly off-topic questions, and
English／colloquial variants, plus prompt injection. Expected answer labels are
used only after generation; they are never passed into the production
adequacy or generation decision.

Run a smaller live-model sample before evaluating every in-scope question:

```powershell
python -m app.cli evaluate-robustness --scenario ryuk --with-llm --category false_premise --limit 3 --output ".\reports\ryuk-robustness-live-sample.json"
```

Use `--category` and `--limit` because a complete live run can take many
minutes on the local 8B model. Retrieval-only evaluation should remain the
routine regression check.

After a failure, rerun only that case with `--question-id RYUK-R019`.

Launch the desktop interface:

```powershell
python -m app.ui
```

The student view formats the validated JSON into teaching sections. The
optional developer trace shows chunk IDs, evidence IDs, dense/BM25 ranks,
raw dense/BM25 scores, RRF scores, scope/adequacy decisions, the validated
JSON, and Ollama `done_reason`, token counts, and duration metrics.

The UI offers `提示模式` and `詳解模式`. Both return the same six-field
`TutorResponse` JSON Schema; only the requested teaching depth changes.

The desktop UI keeps one isolated `TutorService` instance per selected
scenario. Repeating the same question within the same scenario uses its
in-memory LRU cache; switching scenarios clears visible results and never
reuses the other scenario's collection. Clearly off-topic,
mixed/ambiguous, unsafe operational, and prompt-injection questions bypass
both ChromaDB and Llama. Unsafe requests receive a fixed refusal plus a safe,
reversible or dry-run alternative. High-confidence lab identifier typos such
as `USR01`, `DCO1`, and `RuykSahre` are normalized before retrieval and
generation; the original question remains visible in the session trace.

In-scope questions are checked for evidence adequacy after retrieval. Valid
evidence IDs explicitly named by the student are guaranteed supporting chunks
inside Top K, even when the initial dense/BM25 candidate pool omitted them;
invalid IDs are removed from the retrieval query and are never invented.
Clearly unsupported requested fields receive a deterministic `unknowns`
response without asking Llama to guess. Generated output is then checked for
the fixed JSON Schema, citation whitelist, explicitly requested citations,
ASCII control characters, and a narrow set of high-confidence contradictions
between Lab configuration, runtime observations, and missing telemetry.  A
shared conservative boundary also prevents the model from turning an evidence
gap (for example, an undecoded PCAP or missing runtime firewall telemetry)
into an unsupported claim that a packet, field, or connection did not exist.
The status line reports retrieval
time, generation time, and the number of Ollama calls so validation retries are
visible. `OLLAMA_KEEP_ALIVE` controls how long Ollama keeps the model loaded;
`RESPONSE_CACHE_SIZE` controls the session cache size. The cache is not written
to disk and is cleared when the application closes.

The desktop UI also retains at most two recent turns for the currently selected
scenario. This short context only resolves follow-up wording such as "那個" or
"前一個"; it is never treated as evidence, never carries an earlier
`evidence_id` into the next answer, and each follow-up performs a new
retrieval. The UI's clear action and every scenario switch remove this context.

If `done_reason=length`, the output may have reached `NUM_PREDICT`; inspect the
developer trace before attributing a short answer to the 8B model itself.
Use [RYUK_MANUAL_TEST.md](RYUK_MANUAL_TEST.md) for the 25-question human
acceptance pass.

The same 25 cases can be run as a resumable live batch. Each case produces
three validated follow-up questions from its final response; the complete run
contains 100 logical questions and 102 independently submitted Tutor turns:

```powershell
python -m app.cli evaluate-manual --scenario ryuk --output-dir ".\reports\ryuk-manual-live"
```

The output directory contains atomic attempts, `checkpoint.json`, and the
aggregated `report.json`. To continue after an interruption:

```powershell
python -m app.cli evaluate-manual --scenario ryuk --output-dir ".\reports\ryuk-manual-live" --resume
```

Add `--retry-errors` with `--resume` only when failed attempts should be run
again. The runner disables the response cache so repeated question text still
exercises the pipeline. It records follow-up provenance, scope, adequacy,
retrieval trace, citations, token counts, `done_reason`, timing, validation
retries, fallback use, and normalized duplicate questions. A successful batch
means the pipeline completed; semantic correctness still requires the human
review described in `RYUK_MANUAL_TEST.md`.

The command succeeds only when the response matches the JSON schema and every returned `evidence_id` is present in the retrieved context.

Live-model checks should be separate, opt-in integration tests. Routine unit tests must mock Ollama HTTP responses.

## FrostyGoop report-derived safe reconstruction

FrostyGoop follows the same deterministic teaching-data skeleton as the other
scenarios, but its evidence boundary is intentionally narrower:

```text
safe Lab task JSON + localhost PCAP
→ FrostyGoop Normalizer
→ normalized JSON / rag_chunks.jsonl
→ app.frostygoop_import
→ chunks.json + evidence_catalog.json
→ isolated Chroma collection
→ dense + BM25 + RRF retrieval
→ instructor-only Ground Truth evaluation
```

The importer reads only:

```text
scenarios/frostygoop/input/normalized/
```

It never reads the original sample, raw PCAP, or VM scripts.  Rebuild the
scenario after synchronizing new normalized output:

```powershell
.\.venv\Scripts\python.exe -m app.frostygoop_import
.\.venv\Scripts\python.exe -m app.cli index --scenario frostygoop --rebuild
.\.venv\Scripts\python.exe -m app.cli evaluate --scenario frostygoop `
  --output ".\reports\frostygoop-ground-truth-retrieval.json"
```

The collection name is `ot_security_chunks_v1_frostygoop`, independent of
Ryuk and Industroyer2.  The current Normalizer-derived corpus has 14 scenario
chunks; shared controlled OT concept chunks are added to the collection at
index time.

Important teaching boundaries:

- TCP/1502 is a localhost Lab port, not production Modbus TCP/502.
- A matching Task JSON and PCAP proves a captured Lab request, not device
  acceptance, register change, or physical impact.
- FC06 and FC16 are safe Lab capability demonstrations, not observed real
  FrostyGoop behavior.
- The instructor-only Ground Truth file is never indexed.

For Industroyer2-like, the normalized evidence records direct and via-ENG
Relay event relationships, but does not contain every-hop routing or firewall
allow/deny telemetry. A request for that complete path therefore receives a
deterministic insufficient-evidence response rather than an inferred route.

## Expected Ryuk facts

The maintained Ryuk chunks must map these facts to explicit evidence IDs before the index is treated as valid:

- Kali is the HTTP delivery source.
- USER01 is the execution host.
- DC01 provides the SMB share.
- Kali must not be treated as directly modifying the share.
- The `.RYK` files are safe simulation outputs.
- Credential exposure, scheduled-task deployment, and service/recovery actions are documented as assumptions or dry runs, not observed harmful actions.
- Restore output is a Caesar-decoded copy created by the lab script, not real Ryuk decryption.

Do not invent evidence IDs in this runbook. Use the IDs defined in the validated scenario JSON.

## Import requirements

SENSOR01 input files must:

- use UTF-8;
- contain valid JSON when the extension is `.json`;
- preserve source timestamps and host names as data, not hard-coded application configuration;
- exclude credentials and secrets;
- clearly distinguish lab observations from public historical facts.

The importer must fail with a clear error when required fields are absent or JSON cannot be decoded.

## Common failures

- **Ollama unavailable:** verify that Ollama is running and that `OLLAMA_BASE_URL` is correct.
- **Model not found:** run `ollama list` and compare names with `CHAT_MODEL` and `EMBED_MODEL`.
- **Invalid JSON response:** reject the response and report schema-validation details; do not silently accept free-form text.
- **Unknown evidence ID:** reject IDs not present in the retrieved chunks.
- **Stale Chroma collection:** rebuild after changing chunks or the embedding model.
- **Insufficient evidence:** return a valid response with a useful entry in `unknowns`.
- **Ground Truth evaluation failure:** inspect each case's missing evidence, generated response, and suspected forbidden-claim matches before changing chunks or thresholds.
- **Slow repeated question:** confirm the UI reports a cache hit and that `OLLAMA_KEEP_ALIVE` is set; CLI invocations start a new process and do not share the UI cache.
- **Multiple Llama calls:** inspect the generated JSON and validation error; role questions are deterministically normalized, but unsupported output may still require a retry.

## Current limitations

- VM data is copied manually.
- Validated CLI generation supports Ryuk, Industroyer2, and FrostyGoop with isolated Chroma collections and scenario profiles.
- The desktop UI and local Open WebUI adapter support all three scenarios; scenario changes never reuse another scenario's collection.
- Context length is 4096.
- The system is a proof of concept, not a production service.
- The `index`, `retrieve`, validated `ask`, Ground Truth `evaluate`, robustness evaluation, and resumable manual live-batch CLI commands are implemented.
- The local Tkinter UI and optional Open WebUI presentation layer are implemented.
- Ground Truth evaluation covers canonical backend questions; it does not yet grade arbitrary student answers.
