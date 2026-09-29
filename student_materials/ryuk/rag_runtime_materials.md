# B 組：Ryuk RAG 執行期材料清單

## B 組完整輸入

```text
共用學生背景：llm_context.md
        +
學生問題
        ↓
Ryuk 獨立 Chroma collection + BM25 + jieba + RRF
        ↓
Top-K evidence chunks（預設 4）
        ↓
正式教學／安全／evidence-ID 驗證 → 本機 Llama
```

A 與 B 都讀取相同學生背景；B 唯一額外資料是每題動態取回的 chunks。

## 情境專屬證據

**正式來源：** `scenarios/ryuk/chunks.json`（17 chunks）

| 證據群組 | 主要 evidence | 可回答方向 |
| --- | --- | --- |
| 拓撲與下載 | `RYUK-C01`、`RYUK-L01` | Kali／USER01／DC01 角色與 HTTP delivery |
| USER01 執行 | `RYUK-U01`～`U04`、`RYUK-L02`、`N01` | 執行端、PowerShell／process evidence、SMB 目標 |
| 影響、偵測、復原 | `RYUK-L03`～`L05` | 安全 artifacts、Critical 分類、可逆復原 |
| 情境邊界 | `RYUK-L06`～`L09` | Discovery、credential assumption、dry run 限制 |
| 收集與品質 | `RYUK-L10`、`RYUK-N01` | SENSOR01 角色、收集完整性與遙測缺口 |

## 受控通用概念

索引亦含 `scenarios/common/concept_chunks.json` 的可追溯通用概念，例如 Windows
4104／4688 與安全模擬／通訊證據界線。它們可解釋概念，但不可假裝是本次事件事實。

## 不提供給 A 或 B 的資料

- `ground_truth.json`：教師評估基準。
- `evidence_catalog.json`：教師追溯目錄。
- `robustness_questions.json`：測試題庫。
- `input/` 下的 raw／normalized 來源資料：RAG 應讀已轉換的 chunks，而非整份 log。

## 比較指令

```powershell
.\.venv\Scripts\python.exe -m app.cli compare --scenario ryuk --mode explain "為什麼 Kali 不是直接修改 DC01 Share 的主機？"
```
