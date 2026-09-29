# LLM 與 RAG A/B 比較結果表

- 執行時間：2026-08-13T16:45:35.425216+08:00
- 模型：`llama3.1:8b-instruct-q5_0`
- A：只提供同一情境的 `llm_context.md`；不提供 RAG 證據。
- B：提供相同情境文件，再加入 production Top-4 hybrid retrieval 與 evidence-ID 驗證。
- Ground Truth 未提供給 A 或 B；以下是單次 live sample，適合做質性展示，非統計顯著性結論。

## 簡報版結果

| 情境 | 問題類型 | A：僅情境文件 | B：情境文件＋RAG | B 的可追溯證據 |
|---|---|---|---|---|
| ryuk | AB01 | Lab 設計中，為了安全性考量，Kali 不是直接修改 DC01 Share 的主機，而是透過 SMB 存取 RyukShare 進行操作。 | Kali 的 HTTP 提供紀錄支持它是本情境的內容來源端。USER01 的端點紀錄則直接支持它是實際執行端，因此兩類證據代表不同角色。實驗環境設計另阻擋 Kali 直接透過 SMB 存取 … | `RYUK-C01, RYUK-U01` |
| ryuk | AB02 | 根據 Lab 設計，.RYK 檔案是安全模擬 artifacts，不代表真實 Ryuk 加密。 在此 Lab 中，Caesar shift 是可逆內容轉換，不是真實 Ryuk 加密。 因此，… | 這個前提不成立：證據只支持本實驗以 Caesar shift 產生 100 個 .RYK 安全模擬檔，不能證明使用了真實 Ryuk，也無法據此判斷真實惡意程式的加密演算法。 | `RYUK-L03, RYUK-L04` |
| industroyer2 | AB01 | 因為只看到 source_ip=10.10.60.20，無法確認是哪一方發起的動作，也無法確定最初的操作意圖。 | 不能。正常 ENG 維護與 Kali via ENG Relay 成功時，Server／PCAP 都可能看到 source_ip=10.10.60.20；來源 IP 本身不足以判定最初發起者… | `IND2-N02, IND2-N21` |
| industroyer2 | AB02 | Safe CaddyWiper-like lockout 後，iec104_sent=false 能證明 OT-SERVER01 沒有接收到 IEC-104 控制封包，但不能證明最初發起者或… | Safe CaddyWiper-like lockout 後，iec104_sent=false 能證明 Safe CaddyWiper lockout 後，ENG relay／recove… | `IND2-N18, IND2-N25` |
| frostygoop | AB01 | FC06 寫入不能直接證明是真實 FrostyGoop 對 PLC 的攻擊，因為 Lab 沒有真實 FrostyGoop sample、PLC、RTU 或實體製程。 | FC06 寫入不能證明是真實 FrostyGoop 對 PLC 的攻擊，因為實驗環境是安全重建，沒有真實 FrostyGoop sample、PLC、RTU 或物理製程。 | `FROSTY-N09, FROSTY-N14, FROSTY-N07, FROSTY-N06` |
| frostygoop | AB02 | 根據 Lab 架構，單一 Modbus 寫入封包不能直接證明造成實體設備影響。 Lab 沒有真實 FrostyGoop sample、PLC、RTU 或實體製程，因此無法提供直接證據。 資料… | 現有證據無法確認 FrostyGoop 報告衍生安全重建中，單一 Modbus 寫入封包直接造成哪些實體設備影響。 | `FROSTY-N07` |

## 可量化觀察

| 指標 | A：僅情境文件 | B：情境文件＋RAG |
|---|---:|---:|
| 測試題數 | 6 | 6 |
| 可引用 evidence_id 的回答 | 0/6 | 6/6 |
| B 的引用皆屬於當次檢索內容 | n/a | 6/6 |
| 使用相同情境文件 | 6/6 | 6/6 |

上述量化項目驗證的是可追溯性與實驗控制，不等同於回答文字一定較自然；自然度仍應由展示者以題目與回答內容人工說明。

## 詳細紀錄

完整 A、B JSON 回覆、Top-4 chunks 與 Ollama telemetry 請見同名 `.json` 檔。
