# 學生情境材料

本資料夾放置學生在開始提問前可閱讀的固定背景資料。它和
`scenarios/<scenario>/ground_truth.json`、`evidence_catalog.json` 分開保存：

- 學生材料可用於簡報、教學活動，以及未來的「只給情境卡」Llama 對照組。
- Ground Truth 與完整 evidence catalog 僅供教師端評估，不應直接交給學生或整份放入對照組提示。
- RAG 組與無 RAG 組必須取得同一份學生材料；只有 RAG 組會再取得依問題檢索出的 Top-K chunks。

每個情境預計包含兩份文件：

1. `incident_brief.md`：事件通報／初始任務。
2. `lab_architecture.md`：Lab 架構、角色與資料界線。

目前先完成 Industroyer2-like。Ryuk 與 FrostyGoop 資料夾已預留，尚未放入學生內容。

這些材料只描述隔離、可逆的教學模擬；不提供真實惡意程式、真實 OT 控制或破壞性操作步驟。
