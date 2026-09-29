# B 組學習流程：從假設到可追溯還原

本文件只供「學生材料 + RAG」的 B 組使用。學生先閱讀事件通報與 Lab 架構，
再用自然語言向教學助理查證；教學助理會依問題取回少量相關證據，並以
evidence ID 支持回答。

## 1. 提出可檢驗的假設

面對 OT-SERVER01 的 IEC-104 控制相關紀錄，學生可先提出互相競爭的假設：

- **假設 A：** 此活動屬於正常 ENG 維護。
- **假設 B：** 操作可能經由 ENG01 Relay 轉送，因此 Server 看到的是 ENG01。
- **假設 C：** ENG01 處於 Safe CaddyWiper-like lockout，Relay 操作沒有成功
  送出 IEC-104。

假設不是答案。它們的用途是讓學生明確知道要查證哪一個關係、哪一個觀測
位置，以及哪些結果可能仍屬未知。

## 2. 向 RAG 教學助理查證

學生不需要輸入 evidence ID，可直接使用自然語言提問，例如：

- 為什麼只看到 `source_ip=10.10.60.20`，不能直接判定是正常 ENG 維護？
- 正常 ENG 維護與 Kali via ENG Relay 成功時，Server 端可能有哪些相同現象？
- 若要區分兩者，還要比對 Kali、ENG01、OT-SERVER01 的哪些紀錄？
- `maintenance_ticket` 為什麼不會出現在 Server state 或 IEC-104 packet？
- `iec104_sent=false` 能直接證明什麼？又不能證明什麼？
- 現有資料能否重建 Kali 到 OT-SERVER01 的每一跳路由與防火牆決策？

每次回答都應協助學生檢查：

1. **已知事實：** 哪些敘述由本次取回的 evidence 支持？
2. **合理推論：** 哪些是多端紀錄關聯後的保守解讀？
3. **證據不足：** 哪些欄位、封包、路由、裝置回應或時間關係尚未被記錄？

## 3. 逐步還原事件

學生可依下列順序整理調查筆記：

### 步驟 1：確認 Server 視角

先確認 OT-SERVER01 實際觀測到的來源 IP、通訊協定與目的埠。這只能回答
「Server 看到了什麼」，不能單獨確認最初發起者或意圖。

### 步驟 2：建立正常基線

查詢正常 ENG 維護時的 ENG Maintenance Console、maintenance ticket 與
Server／PCAP 觀測。目的是理解正常操作在 Server 視角下可能長什麼樣子。

### 步驟 3：查詢 Relay 脈絡

查詢 Kali action 與 ENG Relay 記錄，確認是否有 Relay 請求、是否被接受、
以及是否真的送出 IEC-104。不要將「提出請求」直接等同於「Server 已收到」。

### 步驟 4：比對 lockout 影響

若情境出現 Safe CaddyWiper-like lockout，應確認 ENG console／relay 是否可用，
並檢查 `iec104_sent` 與 blocked reason。這可支持某次 Relay 嘗試是否被阻擋，
但不等同於真實惡意程式或真實工程站破壞。

### 步驟 5：寫出保守結論

結論應至少回答：

- 哪一端被 Server 直接觀測到？
- 哪一端的本機紀錄提供操作脈絡？
- 哪些跨主機紀錄可支持事件關聯？
- 還有哪些資訊不足，不能下結論？

例如，若 Server／PCAP 僅看到 `source_ip=10.10.60.20`，可說明 ENG01 是
可觀測的送出端；但是否為正常維護或經 Relay 發起，仍要以 ENG 本機、Kali
action 與 Relay 證據共同判斷。

## 4. RAG 在 B 組額外提供的價值

B 組不是自動公布答案，而是在學生問題與證據之間建立可追溯的橋樑：

- 依問題選出相關 case 與跨主機紀錄，而不是把完整 raw logs 丟進 prompt。
- 將答案中的 Lab 事實限制在取回 evidence 的範圍內。
- 讓模型引用可檢查的 evidence ID。
- 當資料不足時，明確指出缺口，而不是用通用知識補齊本次事件。
- 對真實 OT 控制、破壞性或惡意操作要求，維持安全拒絕並導向可逆的 Lab 分析。
