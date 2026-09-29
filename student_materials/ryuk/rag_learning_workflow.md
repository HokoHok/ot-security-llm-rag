# B 組學習流程：從假設到可追溯還原

## 1. 提出假設

- **假設 A：** USER01 下載並執行安全模擬後，對 DC01 Share 產生 Lab artifacts。
- **假設 B：** Kali 只扮演 HTTP 內容提供端，而非直接修改 Share 的主機。
- **假設 C：** `.RYK` 與 Critical 只是安全模擬與偵測分類，不是已證明的真實 Ryuk。

## 2. 向 RAG 教學助理查證

學生可直接問：

- 哪些 USER01 紀錄直接支持它是執行端？
- Kali 為什麼不是直接修改 DC01 Share 的主機？
- 100 個 `.RYK` 檔案可否代表真實事件規模？
- Credential exposure、scheduled task 與 service disruption 哪些只是情境假設或 dry run？
- 復原結果能證明什麼？為什麼不能稱為真實 Ryuk 解密？

## 3. 逐步還原

1. 用 Kali HTTP access log 確認內容提供與下載請求。
2. 用 USER01 execution context、4104／4688 確認執行端與目標 Share。
3. 用 DC01 impact／detection 描述 Lab artifacts，不擴寫為真實事件。
4. 用 restore result 確認可逆復原副本。
5. 用 SENSOR01 metadata 檢查收集完整性與未收集的遙測。
6. 形成保守結論：內容提供、執行與影響是不同角色與不同證據層，缺少網路觀測時
   不對未記錄的連線或意圖下結論。

## 4. B 組額外價值

B 組依問題選出少量跨主機 evidence chunks，讓回答能引用 evidence ID、說明證據
界線，並避免把安全模擬說成真實惡意行為。
