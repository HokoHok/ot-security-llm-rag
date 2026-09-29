# 事件通報：localhost Modbus Lab 出現待釐清的請求紀錄

## 通報摘要

在 FrostyGoop 報告衍生的安全重建 Lab 中，localhost TCP/1502 的 PCAP 與 Task JSON
出現 Modbus FC03、FC06 或 FC16 請求相關資料。行為偵測器可能將寫入類請求標示為
MEDIUM／HIGH 與 ALERT，但這些是 Lab 偵測分類，不代表真實 PLC、RTU 或實體製程
受到影響。

學生的任務是確認 Task JSON、PCAP 與 detector 各自能支持什麼，並清楚保留「request
已捕捉」與「設備已接受／狀態已改變」之間的證據缺口。

## 初始調查問題

1. Task JSON 與 PCAP match 能否證明 PLC 已接受命令？
2. FC06／FC16、WRITE 與 HIGH／ALERT 分別是什麼層級的 Lab 觀測？
3. localhost TCP/1502 與生產 Modbus TCP/502 有何不同？
4. 要確認設備接受或實體影響，還需要哪類資料？

## 學習限制

這是 report-derived safe reconstruction；沒有真實 FrostyGoop sample、PLC、RTU 或
實體製程。FC06／FC16 是安全 Lab capability demonstration，不是聲稱觀測到的真實
FrostyGoop 行為。
