# Industroyer2-like：RAG 輔助事件還原

## 提出假設

- A：正常 ENG 維護
- B：Kali 經 ENG01 Relay 發起操作
- C：Safe CaddyWiper-like lockout 阻擋 Relay 操作

## 向教學助理查證

- 為何 `source_ip=10.10.60.20` 不足以判定正常？
- Server、ENG Maintenance、ENG Relay、Kali action 各能證明什麼？
- `maintenance_ticket`、`iec104_sent` 與 lockout 狀態在哪一端可觀測？
- 哪些結論仍因缺少封包、路由或裝置資料而未知？

## 逐步還原

```text
Server／PCAP 視角
        ↓
正常 ENG 維護基線
        ↓
Kali action + ENG Relay 關聯
        ↓
lockout 與 iec104_sent 結果
        ↓
已知事實／合理推論／證據不足
```

## B 組額外價值

學生材料 + 問題
→ RAG 取回 Top-K 跨主機證據
→ Llama 產生可引用 evidence ID 的教學回覆

**重點：** Server 的來源 IP 可指出可觀測送出端；是否為正常維護或 Relay
操作，仍須多端證據關聯，且不能補出未收集到的網路或裝置事實。
