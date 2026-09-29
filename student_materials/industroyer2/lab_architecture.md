# Lab 架構與證據閱讀指南

## 主機角色

```text
Kali（安全模擬端）
        │
        │  可能的模擬操作要求
        ▼
ENG01（工程工作站／Maintenance Console／Relay）
        │
        │  IEC-104 over TCP/2404
        ▼
OT-SERVER01（控制服務端）

SENSOR01（跨主機蒐集、正規化與匯出證據）
```

### Kali

Kali 僅是隔離 Lab 的安全模擬端。它可產生對照用事件資料；學生不應將它
自動視為每一筆控制活動的最初來源，也不應要求或執行真實 OT 控制操作。

### ENG01

ENG01 同時是正常工程維護工作站與可能的 Relay 節點。它可能對
OT-SERVER01 送出 IEC-104，因此 Server／PCAP 視角可能只看到 ENG01 的
來源 IP。正常維護脈絡中的 `maintenance_ticket` 是 ENG 本機紀錄，並不是
Server state 或 IEC-104 封包中必然存在的欄位。

### OT-SERVER01

OT-SERVER01 會記錄它收到的控制相關事件，例如來源 IP、通訊協定與目的埠。
這些資料可支持「Server 觀測到什麼」，但來源 IP 本身未必能直接證明最初
發起者、操作意圖、完整路由或防火牆決策。

### SENSOR01

SENSOR01 收集 Kali action、ENG Maintenance Console、ENG Relay、Server
command、state JSON 與 PCAP metadata，接著產生 normalized JSON 與 RAG
chunks。教學助理會依學生問題從這些正規化資料取回少量相關證據。

## 可比較的觀測角度

| 觀測位置 | 可以協助回答的問題 | 不應單獨證明的事情 |
| --- | --- | --- |
| Kali action log | 是否有安全模擬操作要求 | ENG01 或 Server 是否一定已執行 |
| ENG Maintenance Console log | 是否存在正常維護脈絡 | 封包一定攜帶維護單號 |
| ENG Relay log | Relay 是否接受要求、是否送出 IEC-104 | 完整每一跳網路路徑 |
| Server command log | Server 看到的來源與控制相關事件 | 最初發起者的身分與意圖 |
| PCAP metadata | 是否存在可收集的封包檔與基本中繼資料 | 未解碼的 IEC-104 欄位或裝置接受結果 |

## 證據界線

- 正常 ENG 維護與經 ENG Relay 的操作，在 Server／PCAP 中都可能顯示
  `source_ip=10.10.60.20`、IEC-104 與 TCP/2404。
- 因此不能只靠 Server source IP 判定是否正常；應關聯 Kali action、ENG
  maintenance、ENG Relay、Server 與 PCAP metadata。
- 部分收集檔案可能為空。空檔可能代表該通道沒有活動，也可能是收集缺口。
- 現階段 PCAP 僅保留檔案大小與 SHA-256 等 metadata；它不是已完整解碼的
  IEC-104 欄位來源。
- 不同主機可能有時間偏移。時間接近可作為關聯線索，但不單獨證明因果。

## 學生作答原則

請把回答分成三類：已知事實、合理推論、證據不足。當證據不足時，說明還
需要哪一類紀錄，而不要把缺少的資料描述成「沒有發生」。
