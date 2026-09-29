# Lab 架構與證據閱讀指南

## 主機角色

```text
Kali（HTTP 下載內容提供端）
        │ HTTP
        ▼
USER01（下載與安全腳本執行端）
        │ SMB；既有授權網域身分
        ▼
DC01（AD／DNS／RyukShare／偵測與復原）

SENSOR01（跨主機收集、驗證與正規化）
```

### Kali

Kali 在 Lab 中提供 HTTP 下載內容與 access log。它不是 DC01 Share 的已觀測執行端。
Lab 網路設計阻擋 Kali 直接 SMB 到 DC01，但此設定不是執行期間的逐封包防火牆紀錄。

### USER01

USER01 下載並執行安全模擬腳本，使用既有授權網域身分存取 DC01 RyukShare。其
execution context、PowerShell 4103／4104 與 Security 4688 可提供端點執行脈絡。

### DC01

DC01 提供 RyukShare，保留安全模擬的 impact、detection 與 restore 輸出。它能描述
Share 上觀測到的 artifacts，但單靠結果不足以識別最初執行端。

### SENSOR01

SENSOR01 讀取、驗證、收集並正規化 Kali、USER01 與 DC01 證據；它不是下載、執行
或影響主機。

## 證據界線

| 證據位置 | 可直接支持 | 不應單獨支持 |
| --- | --- | --- |
| Kali HTTP access log | 下載頁／腳本請求與內容提供角色 | USER01 一定已執行、Kali 直接 SMB 寫入 |
| USER01 endpoint evidence | 執行主機、使用者、腳本與 Share 目標 | 漏洞利用或密碼竊取方式 |
| DC01 impact／detection | Lab artifacts 與偵測分類 | 真實 Ryuk、歷史事件規模 |
| restore result | 可逆副本建立與復原測試 | 真實 Ryuk 解密能力 |
| SENSOR01 metadata | 收集完整性與資料品質 | 所有可能網路遙測都存在 |

## 學生作答原則

回答應區分已知事實、合理推論與證據不足。沒有 runtime firewall log、PCAP 或執行
期間 SMB snapshot 時，應說「無法確認」，不可把資料缺口寫成「沒有發生」。
