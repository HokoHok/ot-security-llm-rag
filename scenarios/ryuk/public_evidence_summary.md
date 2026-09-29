# Ryuk 教學情境：公開證據摘要

此檔案由隔離實驗環境的紀錄與 `input/normalized/ryuk_incident_summary.json` 整理而成，供公開作品集追溯 `evidence_id`。原始日誌保留於本機備份，未上傳。以下保留實驗事件關係與安全界線；讀者無法從本公開版本逐行核對原始日誌。`evidence_catalog.json` 的 `source_type` 描述原始證據類型，此檔案是其公開摘要。

## RYUK-L01

Kali HTTP 存取紀錄顯示 USER01 取得下載頁與安全模擬腳本。HTTP 請求支持下載行為，無法單憑此證明腳本已執行或 Kali 修改 DC01 Share。

## RYUK-L02

執行與影響紀錄將 USER01 記為執行來源，Kali 記為下載內容提供端，DC01 RyukShare 記為模擬影響目標。

## RYUK-L03

影響摘要記錄 100 個 `.RYK` 安全模擬檔、4 個說明檔與 100 個 Caesar 轉換標記。此轉換不是 Ryuk 的真實加密方式。

## RYUK-L04

DC01 偵測結果將實驗輸出判定為 Critical，依據包括模擬檔數量、說明檔與轉換標記；分類只適用於此 Lab。

## RYUK-L05

復原結果記錄 100 份可讀的還原副本與 0 次失敗。這是可逆 Lab 轉換的復原測試，不是真實惡意程式解密。

## RYUK-L06

Discovery 模擬記錄 USER01 的主機、網域控制器與 Share 可見性。此項是安全模擬，不代表新增入侵或橫向移動行為。

## RYUK-L07

Credential exposure 僅是情境假設；Lab 沒有執行真實憑證竊取或密碼傾印。

## RYUK-L08

排程工作部署為 dry run；沒有建立遠端排程工作或推送 payload。

## RYUK-L09

服務與復原干擾為 dry run；沒有停止服務、刪除備份、刪除 shadow copy 或變更復原設定。

## RYUK-L10

SENSOR01 記錄證據收集時間、DC01 Share 可用性、Kali HTTP 日誌擷取結果與 USER01 資料選取結果。相關彙整欄位見 `input/normalized/ryuk_incident_summary.json` 的 `collection_evidence`。

## RYUK-U01

USER01 execution context 記錄安全模擬腳本、執行時段與 DC01 Share 目標。公開版保留正規化摘要中的 `user01_endpoint_evidence`，省略原始使用者目錄路徑。

## RYUK-U02

USER01 PowerShell 摘要記錄 4103／4104 事件數與腳本、Share、影響資料夾、Caesar 模擬標記；正規化摘要保留事件統計和標記存在性。

## RYUK-U03

USER01 程序摘要記錄 7 筆 4688 建立程序事件，支持 PowerShell 與安全模擬腳本的執行脈絡。

## RYUK-U04

較晚收集的 SMB 狀態沒有活動連線或 mapping。這個事後快照不能證明也不能否定較早的 SMB 存取；需要 DC01 稽核或網路遙測才能確認。
