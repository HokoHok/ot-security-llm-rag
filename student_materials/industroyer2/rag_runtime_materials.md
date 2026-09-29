# B 組：RAG 執行期材料清單

本文件定義未來 A/B 比較中的 **B 組（學生材料 + RAG）** 會使用哪些資料。
它是材料地圖，不是另一份可被索引的知識庫；實際程式仍讀取下列 canonical
來源，避免複製 JSON 後與正式索引不同步。

## B 組完整輸入

```text
固定學生背景（與 A 組完全相同）
    ├─ incident_brief.md
    └─ lab_architecture.md

學生問題
    ↓
Industroyer2 獨立 Chroma collection
    + BM25 + jieba
    + RRF 混合排序
    ↓
依問題選出的 Top-K chunks（目前預設 Top-4）
    ↓
固定教學／安全／證據界線提示
    ↓
本機 Llama 3.1 8B
```

因此，A 與 B 的唯一資料差異是：B 在每次問題時，多了動態取回的 Top-K
evidence chunks；兩組都不取得教師 Ground Truth。

## 固定學生背景：A、B 共用

| 檔案 | 用途 | 是否每題送入模型 |
| --- | --- | --- |
| `incident_brief.md` | 事件通報、初始觀測與學生任務 | 是 |
| `lab_architecture.md` | 主機角色、可觀測位置與證據界線 | 是 |

這兩份文件不提供 case 的完整結論、完整 evidence ID 對照或教師評分答案。

## 情境專屬 RAG 證據：僅 B 動態使用

**正式來源：** `scenarios/industroyer2/chunks.json`<br>
**目前數量：** 26 個 chunks<br>
**索引前提：** 每個 chunk 都包含 `chunk_id`、`text`、`keywords` 與可引用的
`evidence_ids`。

| 證據群組 | Case／分類 | Chunk 與 evidence 範圍 | 可回答的方向 |
| --- | --- | --- | --- |
| 正常工程維護 | `ind2_normal_eng_001` | 7 chunks；`IND2-N01`～`IND2-N07` | ENG 正常維護、maintenance ticket 的本機界線、正常基線 |
| 直接 IEC-104 安全模擬 | `ind2_vE_002` | 5 chunks；`IND2-N08`～`IND2-N12` | 直接來源觀測、Server 控制紀錄、時間與 PCAP 限制 |
| 直接操作與 Safe CaddyWiper lockout | `ind2_vE_003` | 7 chunks；`IND2-N13`～`IND2-N19` | lockout 後 Relay 被阻擋、`iec104_sent=false`、復原限制 |
| 經 ENG Relay 的成功案例 | `ind2_vE_004` | 7 chunks；`IND2-N20`～`IND2-N26` | Kali action、ENG Relay、Server 視角相似性與多端關聯 |

每次 RAG 只把最相關的少量 chunks 放入模型 context，不會把 26 個 chunks
一次送給 Llama。

## 受控通用概念：僅 B 在相關問題時動態使用

**正式來源：** `scenarios/common/concept_chunks.json`<br>
**目前數量：** 5 個 chunks。索引時會複製到 Industroyer2 的獨立 collection，
但保留可追溯 evidence ID。

| Chunk | Evidence ID | 用途 |
| --- | --- | --- |
| `OT-iec104-overview-01` | `OT-G01` | IEC 60870-5-104 的通用定位 |
| `OT-transmission-evidence-boundary-01` | `OT-G02` | 通訊送出、設備接受與實體影響的證據界線 |
| `OT-lab-field-boundary-01` | `OT-G03` | Lab 欄位與產業通用術語的區別 |
| `OT-safe-simulation-boundary-01` | `OT-G04` | 安全模擬與真實事件的界線 |
| `OT-windows-event-logging-01` | `OT-G05` | Windows 4104／4688 的通用意義；通常不屬於 IND2 核心題 |

這一層讓模型能解釋通用概念，但不能把通用知識偽裝成本次 Lab 已觀測到的
事件事實。

## B 組的生成控制資料

除了檢索 chunks，B 組會套用既有程式中的固定控制：

- `app/scenario_profiles.py` 的 Industroyer2-like 情境與安全界線。
- 輸出 JSON Schema：`answer`、`known_facts`、`reasonable_inferences`、
  `unknowns`、`evidence_ids`、`next_hint`。
- evidence ID 白名單驗證：模型只能引用本次取回 chunks 內實際存在的 ID。
- evidence adequacy 判定：沒有完整路由、防火牆 telemetry、未解碼 PCAP
  欄位或未觀測裝置結果時，應說明證據不足。
- 安全政策：拒絕真實 OT 控制、破壞性或可操作的惡意行為，轉回隔離、可逆
  的 Lab 分析。

## 不屬於 A 或 B 的模型輸入

以下檔案可用於教師驗收或除錯，但不可整份提供給模型作答：

| 檔案 | 原因 |
| --- | --- |
| `scenarios/industroyer2/ground_truth.json` | 教師端期望方向與評估基準；提供後會洩題 |
| `scenarios/industroyer2/evidence_catalog.json` | 教師端證據目錄與追溯資料，不是學生初始材料 |
| `scenarios/industroyer2/robustness_questions.json` | 測試題庫，不是事件證據 |
| `scenarios/industroyer2/input/` 的 raw／normalized 檔 | 原始匯入來源；RAG 應讀轉換完成的 chunks，而非整份 raw log |

## 目前尚未接入應用程式的部分

`llm_context.md` 已由命令列 `compare` 自動讀取並提供給 A、B；它是由
`incident_brief.md`、`lab_architecture.md` 與已正規化的模擬結果整理成的共同
情境文件，包含拓樸、四類操作行為、證據來源及資料限制，同時保留在目前
`NUM_CTX=4096` 可用的篇幅內。B 再以正式 Tutor 生成與驗證流程追加每題取回的
chunks。

初步人工比較範例：

```powershell
.\.venv\Scripts\python.exe -m app.cli compare --scenario industroyer2 --mode explain "為什麼只看到 source_ip=10.10.60.20，不能判定是正常 ENG 維護？"
```

## B 組的學生查證流程

- [`rag_learning_workflow.md`](rag_learning_workflow.md)：一般版，說明提出假設、
  向 RAG 查證與保守還原的步驟。
- [`rag_learning_workflow_slide.md`](rag_learning_workflow_slide.md)：簡報版，濃縮為
  一頁投影片可用的流程文字。
- [`llm_context.md`](llm_context.md)：A、B 共用的中等完整情境文件；供
  `NUM_CTX=4096` 的命令列 A/B 比較使用。

這兩份流程文件是教學呈現材料；在公平 A/B 實驗中，它們不應只提供給 B 組。
若要讓模型讀取流程指引，A、B 必須取得完全相同的版本；B 的唯一額外內容仍是
依問題動態取回的 evidence chunks。
