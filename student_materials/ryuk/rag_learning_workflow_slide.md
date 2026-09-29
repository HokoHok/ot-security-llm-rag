# Ryuk-like：RAG 輔助事件還原

## 提出假設

- USER01 是實際執行端
- Kali 是 HTTP 內容提供端
- `.RYK`／Critical 是安全模擬 artifacts

## 向教學助理查證

- 哪個端點證據支持 USER01 執行？
- Kali 為何不是直接修改 DC01 Share 的主機？
- 哪些內容是觀測事實，哪些是 dry run／情境假設？

## 逐步還原

```text
Kali HTTP delivery
        ↓
USER01 endpoint execution
        ↓
DC01 Share impact／detection
        ↓
restore result + SENSOR01 normalization
        ↓
已知事實／合理推論／證據不足
```

**重點：** 實驗網路設定、端點紀錄與 Share artifacts 是不同證據層；安全模擬不等於
真實 Ryuk 或歷史事件。
