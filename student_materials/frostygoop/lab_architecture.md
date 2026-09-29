# Lab 架構與證據閱讀指南

## 資料流程

```text
安全 Lab Task JSON
        │
        ▼
localhost Modbus request（TCP/1502）
        │
        ├─ PCAP decode／follow-up observations
        └─ behavior detector classification
        ↓
FrostyGoop Normalizer → normalized JSON／RAG chunks
```

## 可用證據

### Task JSON

Task JSON 描述 Lab 預期請求，例如 FC03、FC06 或 FC16 與指定 register。它可支持
「Lab 任務請求了什麼」，但不能單獨證明網路傳送、設備接受或實體結果。

### localhost PCAP

PCAP 可支持捕捉到對應的 localhost Lab request，並可保留後續受支援的請求觀測。
PCAP request 不是 PLC response，也不是 device acceptance 或 physical impact 證據。

### behavior detector

Detector 的 READ／WRITE、LOW／MEDIUM／HIGH、NOTICE／ALERT 是 Lab 分類輸出。
它可支持規則如何標示請求，不可直接等同於實體製程異常。

### Normalizer 與資料品質

Normalizer 將 Task JSON、PCAP 與 detector 輸出整理為 chunks。Lab 使用 TCP/1502，
不是生產 Modbus TCP/502；沒有真實 PLC、RTU 或 FrostyGoop sample。

## 學生作答原則

請將「捕捉到 request」、「設備接受」、「register 改變」與「實體影響」視為不同
證據層。未觀測 response 或 device state 時，使用「現有資料無法確認」，而不是
推論成功寫入或沒有任何回應。
