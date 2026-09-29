# OT Security Tutor 專案交接

> 本文件是實習交接紀錄。公開 GitHub 版本的檔案範圍與安裝方式以 [README](README.md) 為準；本機來源資料與備份未公開。

> 交接基準日期：2026-08-28<br>
> 狀態：三情境本地 LLM + RAG 概念驗證完成

## 1. 專案成果

本專案使用本機 Ollama、Llama 3.1 8B、EmbeddingGemma、ChromaDB、BM25、
jieba 與 RRF，將安全模擬或報告衍生的工控事件資料轉成可檢索證據，產生附有
`evidence_id`、已知事實、合理推論與資料缺口的回答。

目前支援：

- Ryuk-like Safe Emulation
- Industroyer2-like Safe Emulation
- FrostyGoop report-derived Safe Reconstruction

本專案沒有進行 Fine-tuning，也不包含真實惡意程式或破壞性 OT 操作。

## 2. 建議閱讀順序

1. [`README.md`](README.md)：專案目的、架構、資料格式與功能總覽。
2. [`docs/RAG_RUNBOOK.md`](docs/RAG_RUNBOOK.md)：環境、索引、CLI、UI 與 Open WebUI 操作。
3. [`LLM檢索系統/Demo_Runbook.md`](LLM檢索系統/Demo_Runbook.md)：展示與操作流程。
4. [`reports/final_validation.md`](reports/final_validation.md)：最後一次包含 pytest 的完整驗收摘要。
5. [`reports/README.md`](reports/README.md)：保留報告的用途。

## 3. 主要目錄

| 路徑 | 用途 |
| --- | --- |
| `app/` | RAG、檢索、生成、驗證、CLI、Tkinter UI 與 OpenAI-compatible adapter |
| `scenarios/` | 三情境 chunks、evidence catalog、Ground Truth、測試題與 normalized input |
| `student_materials/` | A/B 共用情境文件與使用者閱讀材料 |
| `scripts/` | Demo 啟動、驗收與 SENSOR01 normalizer／collector |
| `tests/` | pytest 自動測試 |
| `data/chroma/` | 本機已建立的 ChromaDB；可由 scenario chunks 重建 |
| `reports/` | 精簡後保留的最終驗收、代表性 live sample 與人工檢查結果 |
| `LLM檢索系統/` | Demo 題組、A/B 結果與展示說明 |
| `情境資訊/` | 主管原始資料、VM runbook、報告與簡報圖檔 |
| `_archive/` | 交接前移出的歷史版本；正式交付可排除整個目錄 |

## 4. 本機環境

- Python 3.11+
- Ollama
- `llama3.1:8b-instruct-q5_0`
- `embeddinggemma:300m-qat-q8_0`
- Docker Desktop + Open WebUI（選用）

新電腦建立環境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
ollama pull llama3.1:8b-instruct-q5_0
ollama pull embeddinggemma:300m-qat-q8_0
```

`.env`、`.venv/` 與 `data/chroma/` 是本機項目，不應當成可攜式原始碼交付內容。

## 5. 建立三情境索引

```powershell
.\.venv\Scripts\python.exe -m app.cli index --scenario ryuk --rebuild
.\.venv\Scripts\python.exe -m app.cli index --scenario industroyer2 --rebuild
.\.venv\Scripts\python.exe -m app.cli index --scenario frostygoop --rebuild
```

## 6. 啟動方式

開發者桌面 UI：

```powershell
.\.venv\Scripts\python.exe -m app.ui
```

Open WebUI 完整 Demo：先啟動 Ollama 與 Docker Desktop，再執行：

```powershell
.\scripts\start_demo.ps1 -OpenBrowser
```

若 PowerShell execution policy 阻擋腳本，可在目前程序使用：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

## 7. 驗收

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m app.final_validation --with-pytest --output .\reports\final_validation
```

2026-08-28 清理後的程式測試結果：

- 308 tests passed

目前保留的 `reports/final_validation.md` 記錄日期為 2026-08-28，包含模型、索引、
Open WebUI 與 308 項 pytest 通過的歷史驗收結果。搬移後的環境需要重新驗證；
請將新報告存到另一個輸出名稱，以保留歷史基準。

固定資料的最後完整聯合驗收結果：

- Ryuk Ground Truth：11/11
- Ryuk robustness：50/50
- Industroyer2 Ground Truth：9/9
- Industroyer2 robustness：30/30
- FrostyGoop Ground Truth：6/6
- FrostyGoop robustness：30/30

以上是固定資料與檢索規則下的專案驗收，不代表模型具備通用的客觀準確率。

## 8. 資料更新流程

```text
事件 VM raw logs / JSON / PCAP metadata
→ SENSOR01 collection
→ Normalizer
→ incident_summary / timeline / data_quality / rag_chunks
→ Windows importer
→ chunks.json / evidence_catalog.json
→ ChromaDB + BM25
→ Ollama generation and validation
```

不要直接把大量 raw logs 全部放進 prompt；後續新增情境應沿用 Normalizer、統一
chunk schema、Ground Truth 與 robustness questions。

## 9. 已知限制與後續注意事項

- Llama 3.1 8B 回答品質仍受模型容量、量化與 4096 context 限制。
- `evidence_ids` 已限制為檢索內容中的 ID，但目前仍是回答層級清單，不是每句 claim 的機械式對應。
- 跨主機時鐘偏移、空檔與未解析 PCAP 必須保留在 `data_quality`，不可被解讀成「沒有發生」。
- Industroyer2 情境中的 `Safe CaddyWiper-like lockout` 是 Lab 內部舊命名。真實 CaddyWiper 是資料抹除工具；Lab 只安全模擬工程站資料／工具不可用與復原受阻，不應宣稱它能直接封鎖 IEC-104。舊 case ID、script filename 與 log filename 為了證據來源仍保留。
- Open WebUI 只作為前端；情境檢索、生成與 evidence validation 仍由本專案 adapter 執行。

## 10. 交付範圍

正式交付時建議包含所有主要目錄與根目錄文件，但排除：

```text
_archive/
.venv/
.env
__pycache__/
.pytest_cache/
```

`data/chroma/` 可選擇不交付，接手者能使用第 5 節指令重建。
