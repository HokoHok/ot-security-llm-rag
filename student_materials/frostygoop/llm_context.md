# FrostyGoop-like A/B 共用 Llama 背景摘要

本摘要由事件通報與 Lab 架構整理，供 `NUM_CTX=4096` 的 A/B 比較使用。A 與 B 都
取得相同內容；B 唯一額外資訊是依問題動態檢索的 RAG chunks。

本情境是 FrostyGoop 報告衍生的安全重建。Lab 使用 Task JSON、localhost TCP/1502
的 Modbus request、PCAP decode／follow-up observations 與 behavior detector，接著
由 Normalizer 匯出可檢索資料。FC03 是讀取基線；FC06、FC16 是安全 Lab capability
demonstration。READ／WRITE、LOW／MEDIUM／HIGH、NOTICE／ALERT 是 Lab 分類。

Task JSON 與 PCAP match 可支持捕捉到相對應的 Lab request，不能單獨證明設備接受、
register 改變、PLC response 或物理影響。Lab 沒有真實 FrostyGoop sample、PLC、RTU
或實體製程，TCP/1502 也不是生產 Modbus TCP/502。資料不足時必須保留此界線，且不得
提供真實 PLC 控制或 Modbus 寫入步驟。
