# Industroyer2-like 共用情境文件 v2 A/B 抽樣結果

- 執行日期：2026-08-20
- 模型：`llama3.1:8b-instruct-q5_0`
- 模式：`explain`
- A：新版 `llm_context.md`，無 RAG
- B：相同 `llm_context.md`，再加入每題 Top-4 RAG 證據與正式驗證流程
- Ground Truth 未提供給 A 或 B

## 抽樣結果

| 題目 | A：情境文件 | B：情境文件＋RAG | 本輪觀察 |
| --- | --- | --- | --- |
| 正常維護與 Kali via ENG Relay 的共同現象及區分證據 | 正確指出兩者都可能呈現 `.20`、IEC-104、TCP/2404，並列出多端紀錄 | 正確串起 Kali action、ENG Maintenance／Relay、Server／PCAP | A 已足以回答概念；B 的證據關聯更精確，引用 `IND2-N02/N24/N20` |
| via-ENG Relay 是由 ENG 還是 Kali 直接送到 Server | 正確指出由 ENG01 實際送出 | 正確說明 Kali 提出要求、ENG01 送出且 Server 看見 `.20` | A 的 unknown 偏離題意；B 引用 `IND2-N20/N24` 且回答較完整 |
| ENG 是否知道 Kali 介入 | 正確回答人員是否即時知情無法確認 | 區分 Relay 系統紀錄與人員看見／理解／同意 | A 把題目中的假設列入 known facts；B 的事實邊界較穩定，引用 `IND2-N20/N24` |
| `iec104_sent=false` 能與不能證明什麼 | 正確限制在該次 Relay 未送出，沒有擴大成整段期間皆未收到 | 進一步關聯 `accepted=False` 與 lockout，並保留其他來源未知 | A 將部分可能性重複列入 facts、inferences、unknowns；B 引用 `IND2-N17` 且分類較乾淨 |
| `.10` 與 `.20` 的差異 | 正確指出 `.10` 是 Kali direct；`.20` 可能是正常 ENG 或 Relay | 同樣正確，並明確說 `.20` 不能判定最初發起者／意圖 | A 已回答核心差異；B 引用 `IND2-N09/N02/N21` 並補上證據限制 |

## 結論

新版文件讓 A 從「背景不足且容易虛構名詞」提升為能正確回答大部分核心架構題；
五題中未再出現 `Engeladget`、`Engelzeuger ID` 等不存在的實體。這表示先前部分
失敗確實與共用文件過度精簡有關。

B 的合理優勢仍然存在：它能依題目取得特定 case 證據、引用可驗證的
`evidence_id`，且在 known facts、inferences 與 unknowns 的分類上較穩定。A 仍會
出現無關 unknown、把問題假設當成 known fact，或在三個分類重複同一內容。

這是單次本機模型抽樣，適合做質性比較與簡報案例，不代表統計顯著性或所有自然
語言問法都會得到相同結果。

## Ollama prompt telemetry

| 題目 | A prompt tokens | B prompt tokens |
| --- | ---: | ---: |
| 正常維護與 Relay 比較 | 1997 | 2050 |
| Relay 傳送路徑 | 1984 | 2050 |
| ENG 人員是否知道 | 1980 | 2050 |
| `iec104_sent=false` | 1980 | 2050 |
| `.10` 與 `.20` | 1989 | 2050 |

所有十次呼叫的 `done_reason` 均為 `stop`，沒有因輸出長度上限截斷。
