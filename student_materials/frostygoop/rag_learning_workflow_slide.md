# FrostyGoop-like：RAG 輔助安全重建

## 提出假設

- Task JSON + PCAP 可支持 Lab request
- detector 分類不等於實體異常
- 未見 device evidence 時，不推論 PLC 接受

## 向教學助理查證

- FC03／FC06／FC16 各代表什麼 Lab 觀測？
- PCAP match 為何不足以證明設備接受？
- 哪些資料可確認 request，哪些仍屬未知？

## 逐步還原

```text
Task JSON
        ↓
localhost PCAP request
        ↓
follow-up observation + detector classification
        ↓
request／acceptance／impact 證據界線
```

**重點：** PCAP 證明捕捉到 Lab request；不是 PLC response、register 改變或物理影響。
