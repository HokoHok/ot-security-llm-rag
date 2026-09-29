# Ryuk-like A/B 共用 Llama 背景摘要

本摘要由事件通報與 Lab 架構整理而成，供 `NUM_CTX=4096` 的 A/B 比較使用。A 與 B
都取得完全相同的內容；B 唯一額外資訊是依問題動態檢索的 RAG chunks。

這是隔離、可逆的 Ryuk-like 教學 Lab。Kali 提供 HTTP 下載內容；USER01 是下載與
安全腳本執行端，可用既有授權網域身分透過 SMB 存取 DC01 RyukShare；DC01 提供
Share、偵測與復原輸出；SENSOR01 收集並正規化跨主機證據。Lab 設計阻擋 Kali 直接
SMB 到 DC01，但這不是執行期間的封包或防火牆 telemetry。

DC01 的 `.RYK`、RyukReadMe、Caesar marker 與 Critical 是本 Lab 的安全模擬
artifacts 與分類。Caesar shift 是可逆內容轉換，不是真實 Ryuk 加密。Discovery、
credential exposure、scheduled task 與 service/recovery 的證據層級不同，可能是
假設或 dry run。缺少觀測時應說「現有資料無法確認」，不得補出漏洞、竊密、真實
惡意程式或歷史事件細節。
