# Industroyer2-like A/B 共用 Llama 情境文件

本文件由事件通報、Lab 架構與已正規化的模擬結果整理而成，供 `NUM_CTX=4096`
的 A/B 比較使用。A 與 B 都取得本文件；B 唯一額外資訊是依每題動態檢索的 RAG
chunks。本文不包含 Ground Truth、標準答案或 evidence ID。

## 範圍與名詞

這是隔離、可逆的 Industroyer2-like 安全教學模擬，不是真實入侵事件，也未使用
真實惡意程式。本文的 `ENG` 與 `ENG Relay` 都是指 ENG01 上的工程端與模擬 Relay
元件，不存在 `Engeladget`、`Engelzeuger ID` 或其他未列出的主機與欄位。
`maintenance_ticket`、`accepted` 與 `iec104_sent` 是本 Lab 的紀錄欄位，不應
當成 IEC-104 標準封包欄位。

## Lab 拓樸與角色

| 主機 | IP | 本 Lab 角色 |
| --- | --- | --- |
| Kali-IND2 | `10.10.60.10` | 安全模擬端，可在指定 case 直接送出模擬 IEC-104，或向 ENG Relay 提出操作要求。 |
| OT-ENG01 | `10.10.60.20` | 工程工作站；可執行正常維護，也可由其 Relay 元件向 Server 送出模擬 IEC-104。 |
| OT-SERVER01 | `10.10.60.30` | Mock OT Server；接收 TCP/2404 的模擬 IEC-104 並留下 command／state 紀錄。 |
| SENSOR01-IND2 | `10.10.60.50` | 收集 Kali、ENG01、Server 與 PCAP metadata，排除或標示舊資料後輸出正規化 JSON。 |

## 已完成的四組安全模擬

1. **正常 ENG 維護（`ind2_normal_eng_001`）**：ENG Maintenance Console 在
   ENG01 本機留下維護紀錄與 `maintenance_ticket=MW-2026-001`，再由 ENG01 對
   OT-SERVER01 送出模擬 IEC-104。Server／PCAP 看到的來源是
   `10.10.60.20`、協定為 IEC-104、目的埠為 TCP/2404。此 case 的 Kali action
   與 ENG Relay action 為空。
2. **Kali 直接操作（`ind2_vE_002`）**：Kali 直接對 OT-SERVER01 送出
   Industroyer2-like 模擬 IEC-104。Server 看到 `source_ip=10.10.60.10` 與
   TCP/2404，並記錄本 Lab 的模擬狀態變化。
3. **直接操作加 Safe CaddyWiper-like lockout（`ind2_vE_003`）**：Kali 先完成
   直接模擬操作，Server 的相關命令來源仍是 `10.10.60.10`；之後 ENG01 啟用
   可逆的 Safe CaddyWiper-like console lockout。後續特定 Relay／recovery 嘗試
   留下 `accepted=false`、`iec104_sent=false`，表示該次 Relay 要求在 ENG01
   送出 IEC-104 前被拒絕。這兩個欄位不能單獨證明其他來源未曾送出封包，也不能
   單獨證明 Server 在整段期間未收到任何 IEC-104。
4. **Kali 經 ENG Relay 成功（`ind2_vE_004`）**：Safe CaddyWiper-like lockout
   尚未啟用時，Kali 向 ENG Relay 提出要求；Relay 紀錄 `accepted=true` 與
   `iec104_sent=true`，由 ENG01 實際建立對 OT-SERVER01 的 IEC-104 傳送。
   Server／PCAP 因此看到 `source_ip=10.10.60.20`，而不是 Kali 的 `.10`。

正常維護與 Kali 經 ENG Relay 成功時，Server／PCAP 都可能只看到 ENG01 的
`.20`、IEC-104 與 TCP/2404。`maintenance_ticket` 只存在 ENG 本機維護紀錄，
不會被 IEC-104 封包攜帶，Server state 也不會靠這個欄位辨認授權意圖。因此，
判斷兩者時需要關聯多端紀錄，不能只靠 Server source IP。

## 各證據來源可以支持的範圍

| 證據來源 | 可以支持 | 不能單獨支持 |
| --- | --- | --- |
| Kali action log | Kali 是否提出直接或 Relay 模擬要求。 | ENG01 是否確實送出、Server 是否收到或接受。 |
| ENG Maintenance log | ENG01 本機是否留下正常維護與 ticket 脈絡。 | ticket 是否存在於網路封包或 Server state。 |
| ENG Relay log | Relay 是否接受要求，以及 `iec104_sent` 記錄的該次送出結果。 | 操作人員當時是否看見、理解或承認這項活動。 |
| Server command／state log | Server 觀測到的來源 IP、命令與 Lab state。 | 最初提出要求者、操作意圖，或完整的中間轉送路徑。 |
| PCAP metadata | 已擷取檔案、端點、連線與 TCP/2404 等基本資訊。 | 尚未解碼的完整 IEC-104 欄位、裝置接受結果或實體影響。 |

「ENG01 留有 Relay log」是事後可供調查的系統證據，不等於 ENG 操作人員當下
已經知道 Kali 介入。現有資料沒有操作人員告警畫面、確認動作或訪談紀錄，因此
人員是否即時知情應列為無法確認。依本 Lab 的應用紀錄，via-ENG Relay 是
Kali 提出要求、ENG01 Relay 接受後由 ENG01 送到 Server；但若問題要求每一跳的
完整網路路由或防火牆 allow／deny 過程，仍需額外 telemetry。

## 資料品質與安全邊界

- 空的 collected file 可能代表該 channel 沒有活動，也可能是 collection gap。
- Normalizer 會排除或標示 stale／legacy event，不把它們直接納入主要結論。
- 不同主機可能有 clock offset；應保留原始時間戳並以共同事件關聯，不能只靠
  絕對時間排列完整因果。
- SENSOR01 未安裝 tshark；目前 PCAP 只記錄檔案大小與 SHA-256，未完整解析
  IEC-104 欄位。
- 缺少路由、防火牆 telemetry、封包欄位、操作人員確認或裝置回應時，應回答
  「現有資料無法確認」，不可把證據缺口寫成「沒有發生」。
- 不得把 Lab 數值與結果說成真實 Industroyer2 歷史事實，也不得提供可對真實
  OT 設備下控制指令、破壞資料或部署惡意程式的步驟。
