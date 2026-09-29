"""Render reader-oriented, cited-only evidence explanations for chat clients."""

from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache
from html import escape
import re
from typing import Iterable, Sequence

from app.config import PROJECT_ROOT
from app.ingestion import load_chunks, load_evidence_catalog
from app.retrieval import RetrievedChunk
from app.schemas import EvidenceRecord, KnowledgeChunk


_SOURCE_TYPE_LABELS = {
    "collection_metadata": "收集與正規化紀錄",
    "evidence_synthesis": "跨來源證據關聯",
    "lab_configuration": "Lab 架構設定",
    "normalized_analysis": "事件分析",
    "normalized_correlation": "事件關聯",
    "normalized_data_quality": "資料品質限制",
    "normalized_state": "系統狀態",
    "normalized_summary": "事件摘要",
    "normalized_timeline": "事件時間線",
    "observed_detection": "偵測結果",
    "observed_endpoint_logs": "端點紀錄",
    "observed_log": "觀測紀錄",
    "observed_restore": "復原結果",
    "observed_summary": "事件摘要",
    "public_reference": "公開參考資料",
    "simulation_log": "安全模擬紀錄",
    "teaching_convention": "教學安全界線",
}
_CHUNK_METADATA_PREFIX = re.compile(r"^[^。]*證據類型=[^。]+。")
_CASE_ID_PATTERN = re.compile(r"\bcase_id=([A-Za-z0-9_]+)", re.IGNORECASE)
_NORMALIZED_CASE_PATH_PATTERN = re.compile(
    r"(?:^|/)normalized/([^/]+)/",
    re.IGNORECASE,
)
_CLOCK_OFFSET_MARKER = "Possible cross-host clock offset detected"
_CASE_LABELS = {
    "ind2_normal_eng_001": "正常 ENG 維護對照組",
    "ind2_ve_002": "Kali direct IEC-104 案例",
    "ind2_ve_003": "direct IEC-104 + Safe CaddyWiper-like lockout 案例",
    "ind2_ve_004": "Kali via ENG Relay 成功案例",
    "frostygoop_fc03_read_001": "FC03 讀取基線案例",
    "frostygoop_fc06_write_001": "FC06 單一寄存器寫入案例",
    "frostygoop_fc16_write_001": "FC16 多寄存器寫入案例",
}


@lru_cache(maxsize=8)
def _catalog_records(scenario: str) -> dict[str, EvidenceRecord]:
    """Load one scenario catalog plus shared teaching/reference evidence."""

    normalized = scenario.strip().casefold()
    if not normalized:
        return {}
    paths = (
        PROJECT_ROOT / "scenarios" / normalized / "evidence_catalog.json",
        PROJECT_ROOT / "scenarios" / "common" / "evidence_catalog.json",
    )
    records: dict[str, EvidenceRecord] = {}
    for path in paths:
        try:
            catalog = load_evidence_catalog(path)
        except ValueError:
            continue
        records.update({record.evidence_id: record for record in catalog.evidence})
    return records


@lru_cache(maxsize=8)
def _raw_chunks(scenario: str) -> dict[str, KnowledgeChunk]:
    """Load readable chunks; callers still show only currently cited records."""

    normalized = scenario.strip().casefold()
    paths = (
        PROJECT_ROOT / "scenarios" / normalized / "chunks.json",
        PROJECT_ROOT / "scenarios" / "common" / "concept_chunks.json",
    )
    chunks: dict[str, KnowledgeChunk] = {}
    for path in paths:
        try:
            chunks.update({chunk.chunk_id: chunk for chunk in load_chunks(path)})
        except ValueError:
            continue
    return chunks


def cited_evidence_records(
    scenario: str,
    evidence_ids: Iterable[str],
) -> list[EvidenceRecord]:
    """Return catalog records only for cited IDs, preserving citation order."""

    records = _catalog_records(scenario)
    ordered: list[EvidenceRecord] = []
    seen: set[str] = set()
    for evidence_id in evidence_ids:
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        record = records.get(evidence_id)
        if record is not None:
            ordered.append(record)
    return ordered


def _source_type_label(source_type: str) -> str:
    reader_labels = {
        "normalized_detection": "\u5075\u6e2c\u7d50\u679c",
        "normalized_data_quality": "\u8cc7\u6599\u54c1\u8cea\u9650\u5236",
        "normalized_correlation": "\u8b49\u64da\u95dc\u806f",
        "normalized_summary": "\u4e8b\u4ef6\u6458\u8981",
        "normalized_timeline": "\u6642\u9593\u7dda\u89c0\u6e2c",
        "observed_endpoint_log_summary": "\u7aef\u9ede\u7d00\u9304",
    }
    return reader_labels.get(
        source_type,
        _SOURCE_TYPE_LABELS.get(source_type, source_type.replace("_", " ")),
    )


def _chunk_for_evidence(
    evidence_id: str,
    retrieved: Sequence[RetrievedChunk],
) -> RetrievedChunk | None:
    return next(
        (chunk for chunk in retrieved if evidence_id in chunk.evidence_ids),
        None,
    )


def _reader_summary(text: str, record: EvidenceRecord) -> str:
    """Turn a retrieved chunk into a concise student-readable claim."""

    # Some endpoint exports are intentionally consolidated in one RAG chunk.
    # The catalog record, rather than that shared chunk body, is the accurate
    # student-facing distinction between the two Windows event sources.
    if record.evidence_id == "RYUK-U02":
        return (
            "USER01 的 PowerShell 4103／4104 摘要，在執行時間窗內命中"
            "腳本名稱、DC01 Share、impact folder 與 Caesar marker。"
        )
    if record.evidence_id == "RYUK-U03":
        return (
            "USER01 的 Security 4688 摘要記錄 PowerShell 程序建立，並保留"
            "腳本名稱、employee01 與 command line 是否可用。"
        )
    if (
        "does not prove device acceptance" in text
        or ("PCAP proves" in text and "not device acceptance" in text)
    ):
        return (
            "PCAP 只能確認捕獲到這筆 Lab Modbus 請求；它本身不能確認"
            "設備是否接受請求，也不能確認是否造成實體影響。"
        )
    if "Behavior detector classified" in text:
        match = re.search(
            r"classified\s+(FC\d+)\s+as\s+(READ|WRITE).*?risk\s+(\w+).*?result\s+(\w+)",
            text,
            flags=re.IGNORECASE,
        )
        if match is not None:
            function_code, activity, risk, result = match.groups()
            return (
                f"\u5be6\u9a57\u74b0\u5883\u5075\u6e2c\u5668\u5c07 {function_code.upper()} \u6b78\u985e\u70ba {activity.upper()} "
                f"\u6d3b\u52d5\uff0c\u6a19\u793a\u98a8\u96aa {risk.upper()} \u8207\u7d50\u679c {result.upper()} \u3002"
            )
    if "Possible cross-host clock offset detected" in text:
        return (
            "Normalizer 偵測到不同主機的時鐘可能有偏移；"
            "應保留原始時間戳，不能只靠絕對時間判定因果順序。"
        )
    if "tshark is not installed" in text:
        return "目前 PCAP 尚未完整解析 IEC-104 欄位，只保存檔案中繼資料。"
    if "Server/PCAP source IP alone cannot distinguish" in text:
        return (
            "只看 Server／PCAP 的來源 IP，不能區分正常 ENG 操作與"
            "Kali 經 ENG Relay 的操作；仍需關聯 ENG 與 Kali 紀錄。"
        )
    if "accepted=True" in text and "iec104_sent=True" in text:
        if "accepted=False" in text and "iec104_sent=False" in text:
            return (
                "同一份 ENG Relay 時間線同時包含失敗嘗試與成功事件；"
                "其中成功事件明確記錄 accepted=True、iec104_sent=True，"
                "表示 ENG01 已透過 Relay 流程送出 IEC-104。"
            )
        return (
            "ENG Relay 成功事件明確記錄 accepted=True、iec104_sent=True，"
            "可支持 ENG01 已透過 Relay 流程送出 IEC-104。"
        )
    if "accepted=False" in text and "iec104_sent=False" in text:
        return (
            "ENG Relay 嘗試被拒絕，且 IEC-104 尚未由 Relay 送出；"
            "不能把這筆 Relay 紀錄當成 Server 已觀測到命令的證據。"
        )
    if "PowerShell Operational events 4103 and 4104" in record.description:
        return (
            "USER01 的 PowerShell 4103／4104 摘要，在執行時間窗內命中"
            "腳本名稱、DC01 Share、impact folder 與 Caesar marker。"
        )
    if "Security 4688 process-creation" in record.description:
        return (
            "USER01 的 Security 4688 摘要記錄 PowerShell 程序建立，"
            "並對應腳本名稱、employee01 與 command line。"
        )
    if "Normal ENG traffic establishes" in text:
        return "正常 ENG 維護基線中，Server 與 PCAP 看到 ENG01 的 source_ip=10.10.60.20。"
    if "Via-ENG relay success can look similar" in text:
        return (
            "Kali 成功經 ENG Relay 操作時，Server／PCAP 也可能看到"
            "source_ip=10.10.60.20，因此它會與正常 ENG 視角相似。"
        )
    if "Task JSON requests FC6" in text and "decoded PCAP match" in text:
        return (
            "Task JSON 的 FC06 請求與解碼後 PCAP 相符；"
            "這支持 Lab 內已捕獲相符請求，但不代表 PLC 已接受寫入。"
        )

    cleaned = _CHUNK_METADATA_PREFIX.sub("", text).strip()
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > 220:
        cleaned = cleaned[:219].rstrip() + "…"
    return cleaned or record.description


def _reader_limit(source_type: str) -> str:
    limits = {
        "collection_metadata": (
            "只能證明資料曾被收集與驗證，不能代替來源主機的事件內容。"
        ),
        "evidence_synthesis": (
            "屬於跨來源整理結果；重要結論仍應回看它所關聯的端點、網路或狀態紀錄。"
        ),
        "lab_configuration": (
            "描述預期角色、權限或阻擋規則，不等同執行期間真的觀測到該行為。"
        ),
        "normalized_analysis": (
            "屬於 Normalizer 的分析結論，需要搭配被分析的原始或正規化觀測。"
        ),
        "normalized_correlation": (
            "能證明兩份資料可對應，但不自動證明接收端已執行或造成實體影響。"
        ),
        "normalized_data_quality": (
            "用來界定哪些結論不能下，本身不是攻擊動作或系統狀態的觀測。"
        ),
        "normalized_detection": (
            "只代表偵測器的分類與風險標籤，不等同設備接受、實體影響或根因。"
        ),
        "normalized_state": (
            "是特定時間點的狀態快照，不能單獨證明造成該狀態的原因。"
        ),
        "normalized_summary": (
            "提供案例概覽，細節仍需由時間線、端點或封包證據確認。"
        ),
        "normalized_timeline": (
            "可支持該來源內的觀測順序；跨主機先後仍需校時與共同事件對齊。"
        ),
        "observed_detection": (
            "代表偵測腳本如何判定 Lab 輸出，不等同真實惡意程式或實際業務損失。"
        ),
        "observed_endpoint_log_summary": (
            "提供該端點的主機紀錄，但不能單獨證明另一台主機已收到或執行。"
        ),
        "observed_endpoint_logs": (
            "提供該端點的主機紀錄，但不能單獨證明另一台主機已收到或執行。"
        ),
        "observed_log": (
            "提供單一來源的直接觀測，跨主機歸因仍需其他來源交叉驗證。"
        ),
        "observed_restore": (
            "能證明 Lab 復原輸出，不代表真實勒索軟體復原能力或原始檔已被覆寫。"
        ),
        "observed_summary": (
            "提供事件輸出摘要，不能取代其底層日誌與逐項觀測。"
        ),
        "public_reference": (
            "只用來解釋通用概念，不能當成本次 Lab 實際發生過的事件證據。"
        ),
        "simulation_log": (
            "只描述安全、可逆的 Lab 模擬，不代表真實惡意程式行為。"
        ),
        "teaching_convention": (
            "是判讀與安全界線，不是本次事件中某項行為已發生的觀測。"
        ),
    }
    return limits.get(
        source_type,
        "它支持這次回答中的特定觀測，不單獨代表完整事件。",
    )


def _evidence_role(source_type: str, text: str) -> str:
    """Explain why this evidence type is useful without repeating the answer."""

    if _CLOCK_OFFSET_MARKER in text:
        return "作為資料品質警告，用來降低僅依絕對時間排序事件的可信度。"
    if "tshark is not installed" in text:
        return "標示 PCAP 目前只有檔案中繼資料，避免把未解析欄位誤當成不存在。"
    if (
        "does not prove device acceptance" in text
        or ("PCAP proves" in text and "not device acceptance" in text)
    ):
        return (
            "區分「已捕獲請求」與「設備已接受／產生實體影響」"
            "兩種證據強度不同的結論。"
        )
    if "Server/PCAP source IP alone cannot distinguish" in text:
        return "指出來源 IP 的歸因能力有限，提醒分析者必須改做跨主機關聯。"
    if "accepted=True" in text and "iec104_sent=True" in text:
        if "accepted=False" in text and "iec104_sent=False" in text:
            return (
                "區分同一時間線中的失敗與成功嘗試，並以成功事件的"
                " accepted／iec104_sent 欄位確認 Relay 處理結果。"
            )
        return "提供 Relay 端成功接受並送出 IEC-104 的直接處理狀態。"
    if "accepted=False" in text and "iec104_sent=False" in text:
        return "提供 Relay 端的處理狀態，用來區分「嘗試被拒絕」與「命令已送出」。"
    roles = {
        "collection_metadata": "確認證據收集流程與檔案完整性是否可供後續分析。",
        "evidence_synthesis": "把不同主機或不同階段的觀測串成可檢查的證據關係。",
        "lab_configuration": "建立主機角色、允許路徑與預期阻擋規則的設計基線。",
        "normalized_analysis": "提供由多項觀測整理出的鑑別規則或分析結論。",
        "normalized_correlation": "核對兩種資料是否描述同一筆請求或同一個事件。",
        "normalized_data_quality": "揭露缺檔、舊紀錄、時鐘偏移或未解析欄位等可信度限制。",
        "normalized_detection": "說明偵測器如何分類某項 Lab 活動及其風險標籤。",
        "normalized_state": "保存特定階段的系統或安全模擬狀態，供前後狀態比較。",
        "normalized_summary": "快速定位案例的角色、路徑與主要結果。",
        "normalized_timeline": "定位單一來源內事件的觀測順序與關鍵欄位。",
        "observed_detection": "顯示偵測腳本對安全模擬輸出的實際判定結果。",
        "observed_endpoint_log_summary": "提供端點直接紀錄，用來確認程序、腳本或使用者活動。",
        "observed_endpoint_logs": "提供端點直接紀錄，用來確認程序、腳本或使用者活動。",
        "observed_log": "提供某一來源直接記錄到的事件內容。",
        "observed_restore": "驗證安全模擬資料是否成功產生可讀的復原副本。",
        "observed_summary": "彙整本次 Lab 已觀測到的主要輸出。",
        "public_reference": "補充協定、事件編號或資安名詞的通用背景。",
        "simulation_log": "記錄安全模擬腳本實際執行的非破壞性操作。",
        "teaching_convention": "界定什麼能由傳送紀錄推論，以及什麼仍需接收端證據。",
    }
    return roles.get(source_type, "說明這個 evidence_id 在證據鏈中提供哪一層觀測。")


def _case_id(text: str, record: EvidenceRecord) -> str | None:
    """Extract a stable case identifier without exposing a local file path."""

    match = _CASE_ID_PATTERN.search(text)
    if match is not None:
        return match.group(1)
    normalized_path = record.source_path.replace("\\", "/")
    match = _NORMALIZED_CASE_PATH_PATTERN.search(normalized_path)
    return match.group(1) if match is not None else None


def _case_label(case_id: str | None) -> str | None:
    if case_id is None:
        return None
    return _CASE_LABELS.get(case_id.casefold(), case_id)


def _is_clock_offset_group(texts: Sequence[str]) -> bool:
    return bool(texts) and all(_CLOCK_OFFSET_MARKER in text for text in texts)


def _combined_support(
    summaries: Sequence[str],
    texts: Sequence[str],
    source_type: str,
    case_ids: Sequence[str | None],
    evidence_ids: Sequence[str],
) -> str:
    """Explain the extra value gained by citing several matching records."""

    if _is_clock_offset_group(texts):
        return (
            "三個不同實驗 case 都出現相同的 clock-offset 警告，"
            "表示這是跨主機事件還原的共同資料限制，"
            "不是單一 case 的偶發問題。"
        )
    joined_text = "\n".join(texts)
    if (
        "Normal ENG traffic establishes" in joined_text
        and "Via-ENG relay success can look similar" in joined_text
    ):
        return (
            "正常維護對照組與 Relay 案例出現相同的 source_ip=10.10.60.20，"
            "證明這個來源 IP 不具唯一歸因能力；必須再關聯最初發起端紀錄。"
        )
    if {"RYUK-U02", "RYUK-U03"} <= set(evidence_ids):
        return (
            "PowerShell 4103／4104 提供腳本內容層證據，Security 4688 提供"
            "程序建立層證據；兩者合併可交叉驗證 USER01 的執行活動。"
        )
    unique_cases = {case_id.casefold() for case_id in case_ids if case_id}
    if len(unique_cases) > 1:
        return (
            f"把 {len(unique_cases)} 個不同案例的同類證據放在一起比較；"
            f"{_evidence_role(source_type, texts[0])}"
        )
    return (
        "同一案例中的多份紀錄指向相同解讀；"
        f"{_evidence_role(source_type, texts[0])}"
    )


def _missing_evidence(limit: str, texts: Sequence[str]) -> str:
    """State the concrete missing observation for a grouped card."""

    if _is_clock_offset_group(texts):
        return (
            "各主機的精確時間偏移量、NTP 同步狀態，以及可在多台主機間"
            "對齊的共同事件；因此仍不能校正出完全精確的時間線。"
        )
    return limit


def _card_title(
    source_type: str,
    texts: Sequence[str],
    evidence_count: int,
    evidence_ids: Sequence[str],
) -> str:
    if evidence_count > 1 and _is_clock_offset_group(texts):
        return "跨案例資料品質限制"
    if {"RYUK-U02", "RYUK-U03"} <= set(evidence_ids):
        return "端點紀錄交叉驗證"
    if evidence_count > 1 and source_type in {
        "normalized_analysis",
        "normalized_correlation",
    }:
        return "跨案例證據比較"
    return _source_type_label(source_type)


def render_evidence_cards(
    scenario: str,
    evidence_ids: Iterable[str],
    retrieved: Sequence[RetrievedChunk] = (),
) -> str:
    """Render cited evidence as short explanations and group duplicates."""

    records = cited_evidence_records(scenario, evidence_ids)
    raw_chunks = _raw_chunks(scenario)
    grouped: OrderedDict[
        tuple[str, str],
        list[tuple[str, str | None, str, str]],
    ] = OrderedDict()
    for record in records:
        retrieved_chunk = _chunk_for_evidence(record.evidence_id, retrieved)
        raw_chunk = (
            raw_chunks.get(retrieved_chunk.chunk_id)
            if retrieved_chunk is not None
            else None
        )
        text = (
            raw_chunk.text
            if raw_chunk is not None
            else retrieved_chunk.document
            if retrieved_chunk is not None
            else record.description
        )
        source_type = (
            raw_chunk.source_type
            if raw_chunk is not None
            else str(retrieved_chunk.metadata.get("source_type", record.source_type))
            if retrieved_chunk is not None
            else record.source_type
        )
        summary = _reader_summary(text, record)
        key = (source_type, _reader_limit(source_type))
        grouped.setdefault(key, []).append(
            (record.evidence_id, _case_id(text, record), text, summary)
        )
    if not grouped:
        return ""

    cards: list[str] = ["## 這些證據如何支持回答"]
    for (source_type, limit), grouped_items in grouped.items():
        grouped_ids = [item[0] for item in grouped_items]
        grouped_case_ids = [item[1] for item in grouped_items]
        grouped_texts = [item[2] for item in grouped_items]
        grouped_summaries = [item[3] for item in grouped_items]
        evidence_label = "、".join(
            f"<code>{escape(value)}</code>" for value in grouped_ids
        )
        title = _card_title(
            source_type,
            grouped_texts,
            len(grouped_items),
            grouped_ids,
        )
        if len(grouped_items) > 1:
            coverage = ["**證據涵蓋：**"]
            summaries_are_same = len(set(grouped_summaries)) == 1
            for evidence_id, case_id, _text, item_summary in grouped_items:
                label = _case_label(case_id)
                source = (
                    f"{escape(label)}（<code>{escape(case_id)}</code>）"
                    if label is not None and case_id is not None
                    else "同類型觀測"
                )
                coverage.append(
                    f"- <code>{escape(evidence_id)}</code>｜{source}"
                    + (
                        ""
                        if summaries_are_same
                        else f"：{escape(item_summary)}"
                    )
                )
            body = (
                *coverage,
                "",
                f"**合併後能說明：** "
                f"{escape(_combined_support(grouped_summaries, grouped_texts, source_type, grouped_case_ids, grouped_ids))}",
                "",
                (
                    "**仍缺少：** "
                    if _is_clock_offset_group(grouped_texts)
                    else "**仍需搭配／限制：** "
                )
                + f"{escape(_missing_evidence(limit, grouped_texts))}",
            )
        else:
            _evidence_id, case_id, _text, summary = grouped_items[0]
            label = _case_label(case_id)
            source_line = (
                (
                    f"**證據來源：** {escape(label)}"
                    f"（<code>{escape(case_id)}</code>）"
                )
                if label is not None and case_id is not None
                else ""
            )
            body = tuple(
                line
                for line in (
                    source_line,
                    "" if source_line else None,
                    f"**觀測內容：** {escape(summary)}",
                    "",
                    f"**本題中的作用：** "
                    f"{escape(_evidence_role(source_type, _text))}",
                    "",
                    f"**需搭配／限制：** {escape(limit)}",
                )
                if line is not None
            )
        cards.append(
            "\n".join(
                (
                    "<details>",
                    f"<summary>{evidence_label}｜{escape(title)}</summary>",
                    "",
                    *body,
                    "</details>",
                )
            )
        )
    return "\n\n".join(cards)


__all__ = ["cited_evidence_records", "render_evidence_cards"]
