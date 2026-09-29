# OT 通用概念來源

本文件只記錄會被索引為 `public_reference` 或 `teaching_convention` 的通用教材來源；它不屬於 Ryuk 或 Industroyer2 的事件證據。

| Evidence ID | 類型 | 來源與用途 |
| --- | --- | --- |
| `OT-G01` | public reference | [IEC 60870-5-104:2006+A1:2016](https://webstore.iec.ch/en/publication/25035)；用於 IEC-104 是遠動通訊協定、採標準 TCP/IP 傳輸設定檔的背景。另參考 [ABB IEC 101/104 文件](https://library.e.abb.com/public/f46ecc91c2154b54a9a653dea2525b4d/REC615_IEC101-104point_2NGA002479_ENa.pdf?x-sign=EVBzvuemKLIk1FR53fY6h5ELCnOdI43Q8AQY6xDON3AE1Fcv27QqD642oiFKScbL)。 |
| `OT-G02` | teaching convention | 本專案的事件還原教學原則：傳送紀錄不能單獨證明接收端接受、執行或實體影響。 |
| `OT-G03` | teaching convention | 本專案的資料定義原則：`iec104_sent`、`accepted` 與 case ID 是 Normalizer／Relay 的本地欄位，不是標準術語。 |
| `OT-G04` | teaching convention | 本專案安全原則：Lab 安全模擬觀察不能當作歷史攻擊統計或真實惡意程式行為。 |
| `OT-G05` | public reference | [PowerShell about_Logging](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_logging?view=powershell-5.1)；[Windows Event 4688](https://learn.microsoft.com/ja-jp/previous-versions/windows/it-pro/windows-10/security/threat-protection/auditing/event-4688)。用於 4104 的 Script Block Logging 與 4688 的程序建立記錄背景。 |

這些 chunks 會被複製到每個情境的索引中，但只在「通用概念」或「比較題」路由使用；一般事件事實題不會檢索它們。
