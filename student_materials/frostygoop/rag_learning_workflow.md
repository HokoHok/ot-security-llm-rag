# B 組學習流程：從請求觀測到保守結論

## 1. 提出假設

- **假設 A：** Task JSON 與 PCAP 對照支持某個 Lab Modbus request 被捕捉。
- **假設 B：** Detector 的 HIGH／ALERT 代表規則分類，而不是已證明的物理異常。
- **假設 C：** 若沒有 response 或 device state，不能確認 PLC 接受寫入。

## 2. 向 RAG 教學助理查證

- FC03、FC06、FC16 在本 Lab 中各自能支持什麼？
- Task JSON 與 PCAP match 為什麼不等於設備接受？
- PCAP follow-up request 與設備狀態改變差在哪裡？
- HIGH／ALERT 可以解讀為真實製程異常嗎？
- 為什麼 localhost TCP/1502 不能寫成生產 Modbus TCP/502？

## 3. 逐步還原

1. 讀取 Task JSON，辨識預期 function code 與 register。
2. 比對 PCAP，確認是否捕捉到對應的 localhost Lab request。
3. 檢視 PCAP follow-up observations 與 detector 分類。
4. 逐一分開 request、response、device acceptance 與 physical impact。
5. 以資料品質限制結束結論：現有資料不證明真實設備接受或實體影響。

## 4. B 組額外價值

B 組依問題取回 case-specific chunks，讓模型能指出 FC、register、PCAP match 或
detector 的具體 Lab 證據，並避免把安全能力展示誤寫為真實 FrostyGoop 或 PLC 行為。
