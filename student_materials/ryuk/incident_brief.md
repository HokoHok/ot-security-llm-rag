# 事件通報：DC01 RyukShare 出現安全模擬檔案異常

## 通報摘要

在隔離的 Ryuk-like 教學 Lab 中，DC01 的 RyukShare 出現 `.RYK` 測試檔、
RyukReadMe 與 Caesar marker 等安全模擬 artifacts。偵測規則將這批 Lab 輸出標示為
Critical，但此分類不代表真實 Ryuk 惡意程式或歷史事件影響規模。

目前可知 Kali 提供 HTTP 下載內容、USER01 是可能的執行端、DC01 提供共享資料夾，
而 SENSOR01 已收集跨主機資料。學生的任務是釐清各主機角色、證據序列與安全邊界，
不是推測未記錄的漏洞、惡意工具或真實勒索行為。

## 初始調查問題

1. 哪些證據支持 USER01 是實際執行端，而非只從 DC01 結果回推？
2. Kali、USER01、DC01 與 SENSOR01 各自扮演什麼角色？
3. 為什麼 Lab 設計阻擋 Kali 直接 SMB，仍不能回答 Kali 從未嘗試連線？
4. `.RYK`、Critical 與 Caesar marker 能證明什麼，又不能證明什麼？

## 學習限制

這是可逆、非破壞性的安全模擬。Caesar shift 不是 Ryuk 加密；檔案數量不是歷史
事件統計；Discovery、credential exposure、scheduled task 與 service/recovery 等
紀錄可能是情境假設或 dry run，不能一律視為已觀測到的惡意行為。
