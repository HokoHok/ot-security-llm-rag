# B 組：FrostyGoop RAG 執行期材料清單

## B 組完整輸入

```text
共用學生背景：llm_context.md
        +
學生問題
        ↓
FrostyGoop 獨立 Chroma collection + BM25 + jieba + RRF
        ↓
Top-K evidence chunks（預設 4）
        ↓
正式教學／安全／evidence-ID 驗證 → 本機 Llama
```

## 情境專屬證據

**正式來源：** `scenarios/frostygoop/chunks.json`（14 chunks）

| 證據群組 | evidence 範圍 | 可回答方向 |
| --- | --- | --- |
| FC03 read baseline | `FROSTY-N01`～`N04` | read request、PCAP match、NOTICE 與安全限制 |
| FC06 single-register Lab write | `FROSTY-N05`～`N09` | Lab capability、follow-up request、WRITE／ALERT 與邊界 |
| FC16 multiple-register Lab write | `FROSTY-N10`～`N14` | multiple-register request、WRITE／HIGH／ALERT 與邊界 |

## 受控通用概念與排除資料

索引可含 `scenarios/common/concept_chunks.json` 的通用證據界線；它不能被說成本次
Lab 的額外觀測。教師端 `ground_truth.json`、`evidence_catalog.json`、
`robustness_questions.json` 與 `input/` raw／normalized 來源檔，都不可整份交給
A 或 B 的模型。

## 比較指令

```powershell
.\.venv\Scripts\python.exe -m app.cli compare --scenario frostygoop --mode explain "Task JSON 與 PCAP match 能證明 PLC 已接受 FC06 寫入嗎？"
```
