# A/B 比較結果表（簡報版）

## 比較設定

- 同一模型：`llama3.1:8b-instruct-q5_0`
- 同一問題、同一情境卡：`student_materials/<scenario>/llm_context.md`
- A：只提供情境卡，不提供個案 RAG 證據。
- B：提供相同情境卡，再加入 production Top-4 hybrid retrieval 與 evidence-ID 驗證。
- 測試題數：6 題，每個情境 2 題；Ground Truth 未提供給 A 或 B。

## 結果摘要

| 情境 | 問題面向 | A：僅情境卡的可觀察結果 | B：加入 RAG 後的可觀察結果 | B 引用的個案證據 |
|---|---|---|---|---|
| Ryuk | 主機角色與執行歸因 | 可描述 Lab 中的主機角色，但無法把結論連回此次事件的執行端紀錄。 | 能連結 Kali 的 HTTP delivery 角色與 USER01 的實際執行端證據。 | `RYUK-C01`、`RYUK-U01` |
| Ryuk | `.RYK` 檔案的意義 | 可提出安全模擬的概念性說明，但沒有個案證據來源。 | 能指出 100 個檔案是 Caesar shift Lab artifacts，不能等同真實 Ryuk 加密。 | `RYUK-L03`、`RYUK-L04` |
| Industroyer2-like | `source_ip=10.10.60.20` 的歸因限制 | 只知道 IP 不足以判定，較難說明是哪兩種事件造成歧義。 | 能具體比較 normal ENG 與 Kali via ENG Relay 成功時的相同 Server／PCAP 視角。 | `IND2-N02`、`IND2-N21` |
| Industroyer2-like | lockout 後 `iec104_sent=false` | 容易把欄位直接延伸為 Server 未接收命令，缺少 Relay 階段佐證。 | 將判斷限制在 Safe CaddyWiper／Relay 狀態；仍應避免在沒有 Server 證據時斷言 Server 結果。 | `IND2-N18`、`IND2-N25` |
| FrostyGoop | FC06 與真實攻擊前提 | 可說明隔離 Lab 的安全限制，但沒有具體分析來源。 | 能把 FC06 定位為 Lab capability demonstration，避免將它說成真實 FrostyGoop 或真實 PLC 攻擊。 | `FROSTY-N09`、`FROSTY-N14`、`FROSTY-N07`、`FROSTY-N06` |
| FrostyGoop | 單一 Modbus 寫入的影響 | 可保守地表示尚不足以判定，但理由較概略。 | 能明確保留 unknown：單一封包不足以證明實體設備或製程影響。 | `FROSTY-N07` |

## 可量化項目

| 指標 | A：僅情境卡 | B：情境卡＋RAG |
|---|---:|---:|
| live 測試題數 | 6 | 6 |
| 回答可引用個案 `evidence_id` | 0/6 | 6/6 |
| B 的引用皆存在於當次 Top-4 檢索內容 | n/a | 6/6 |
| 使用同一份情境卡 | 6/6 | 6/6 |

## 簡報可用結論

> 情境卡讓模型知道「這是什麼 Lab」；RAG 則讓模型能回答「這一次案例中，哪一份證據支持這個結論」，並在證據不足時保留未知。

## 解讀限制

- `0/6` 與 `6/6` 量化的是**證據可追溯性**，不是宣稱 B 的自然語言品質必然較高。
- 本表是 6 題的 live sample，不是統計顯著性評估；完整原始回覆、Top-4 chunks 與 Ollama telemetry 見 [AB_Comparison_Results.json](AB_Comparison_Results.json) 與 [AB_Comparison_Results.md](AB_Comparison_Results.md)。
- Industroyer2-like 的 lockout 題保留為誠實的邊界案例：即使有 RAG，也必須避免把 Relay 層的 `iec104_sent=false` 擴張為未被觀測到的 Server 結果。
