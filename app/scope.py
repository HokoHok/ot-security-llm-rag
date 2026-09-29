from __future__ import annotations

import re
from typing import Literal

from app.scenario_profiles import get_scenario_profile
from app.schemas import TutorResponse

QuestionScope = Literal[
    "in_scope",
    "general_concept",
    "comparison",
    "off_topic",
    "ambiguous",
    "safety_boundary",
    "prompt_injection",
]

_SCOPE_ALIASES = {
    "usr01": "USER01",
    "dco1": "DC01",
    "dc0l": "DC01",
    "sens0r01": "SENSOR01",
    "ruyksahre": "RyukShare",
}

_RYUK_SCOPE_MARKERS = (
    "ryuk",
    "kali",
    "user01",
    "dc01",
    "sensor01",
    "smb",
    "http",
    ".ryk",
    "caesar",
    "powershell",
    "ps1",
    "4103",
    "4104",
    "4688",
    "ryukshare",
    "事件",
    "證據",
    "下載",
    "腳本",
    "執行",
    "影響",
    "偵測",
    "復原",
    "還原",
    "排程工作",
    "憑證",
    "密碼",
    "勒索",
    "evidence",
    "ground truth",
    "process creation",
    "endpoint",
    "snapshot",
    "discovery",
    "employee01",
    "kill chain",
    "dry run",
    "服務",
    "服务",
    "備份",
    "备份",
    "shadow copy",
    "vss",
    "網路 share",
    "网络共享",
    "加密",
    "事件還原",
    "证据",
    "下载",
    "执行",
    "影响",
    "检测",
    "恢复",
    "密码",
)

_INDUSTROYER2_SCOPE_MARKERS = (
    "industroyer2",
    "ind2_",
    "kali",
    "eng01",
    "ot-server01",
    "ot server",
    "iec-104",
    "iec104",
    "tcp/2404",
    "2404",
    "source_ip",
    "10.10.60.10",
    "10.10.60.20",
    "10.10.60.30",
    "maintenance ticket",
    "maintenance_ticket",
    "relay",
    "caddywiper",
    "lockout",
    "ioa",
    "tshark",
    "pcap",
    "正常 eng",
    "normal eng",
    "clock offset",
    "事件",
    "證據",
    "紀錄",
    "日誌",
    "來源 ip",
    "封包",
    "時間線",
    "因果",
    "時間戳",
    "timestamp",
    "安全模擬",
    "工廠",
    "停機",
    "損失",
)

_FROSTYGOOP_SCOPE_MARKERS = (
    "frostygoop", "frostygoop_", "modbus", "fc03", "fc3", "fc06", "fc6",
    "fc16", "tcp/1502", "1502", "53760", "53882", "task json", "pcap",
    "localhost", "127.0.0.1", "register", "plc", "rtu", "device acceptance",
    "設備接受", "實體影響", "安全重建", "工廠", "控制站",
)

_FROSTYGOOP_OPERATIONAL_CONTROL_PATTERN = re.compile(
    r"(?:請給我|幫我|give\s+me|show\s+me).{0,80}"
    r"(?:真實\s*plc|可連線的\s*modbus|real\s*plc|reachable\s*modbus).{0,80}"
    r"(?:完整步驟|步驟|指令|程式|control|write|modify|全部改掉|all)|"
    r"(?:寫程式|write\s+code).{0,60}"
    r"(?:可連線的\s*modbus|reachable\s*modbus).{0,60}"
    r"(?:全部改掉|modify|write|all)",
    re.IGNORECASE,
)

_CLEARLY_OFF_TOPIC_MARKERS = (
    "量子糾纏",
    "quantum entanglement",
    "pizza recipe",
    "football score",
    "天氣",
    "氣溫",
    "下雨",
    "食譜",
    "晚餐",
    "股票",
    "股價",
    "投資標的",
    "電影",
    "球賽",
    "旅遊",
    "行程",
    "星座",
    "笑話",
    "排序演算法",
    "bubble sort",
    "量子",
    "糾纏",
    "纠缠",
    "木星",
    "衛星",
    "卫星",
    "哈姆雷特",
    "文學",
    "文学",
)

# General OT concepts are allowed only through the small, curated reference
# corpus.  These markers deliberately do not route every protocol mention away
# from incident evidence: a host, case ID, or local status field keeps the
# question in the scenario unless the student explicitly asks for a comparison.
_GENERAL_CONCEPT_REQUEST_MARKERS = (
    "在資安領域",
    "一般而言",
    "一般來說",
    "通常來說",
    "通用概念",
    "通用定義",
    "標準定義",
    "協定是什麼",
    "protocol definition",
    "what is",
    "in cybersecurity",
    "in scada",
    "scada中",
    "scada 中",
)

_EXPLICIT_GENERAL_ONLY_MARKERS = (
    "廣義",
    "先不論",
    "不論目前",
    "不考慮目前",
    "不考慮本情境",
    "先不談這個情境",
    "不含本次模擬",
)

_GENERAL_CONCEPT_TERMS = (
    "iec-104",
    "iec104",
    "scada",
    "ics",
    "tcp/ip",
    "telecontrol",
    "powershell 4104",
    "security 4688",
    "事件 id",
    "event id",
    "ransomware",
    "勒索軟體",
)

_LAB_SPECIFIC_MARKERS = {
    "ryuk": (
        "ryuk",
        "kali",
        "user01",
        "dc01",
        "sensor01",
        "ryukshare",
        ".ryk",
        "employee01",
    ),
    "industroyer2": (
        "ind2_",
        "kali",
        "eng01",
        "ot-server01",
        "ot server",
        "source_ip",
        "10.10.60.",
        "maintenance_ticket",
        "maintenance ticket",
        "iec104_sent",
        "accepted=",
        "caddywiper",
        "lockout",
        "relay",
    ),
    "frostygoop": (
        "frostygoop", "frostygoop_", "modbus", "fc03", "fc3", "fc06", "fc6",
        "fc16", "tcp/1502", "1502", "53760", "53882", "task json", "pcap",
        "localhost", "127.0.0.1",
    ),
}

_DEFINITION_MARKERS = (
    "是什麼",
    "代表什麼",
    "有什麼意義",
    "what does",
    "what is",
)

# These patterns identify requests to *perform* an unsafe action.  The action
# lead and target are both required so merely naming encryption, services, or
# backups does not trigger the policy route.
_UNSAFE_ACTION_LEAD = (
    r"(?:請|请|幫我|帮我|教我|給我|给我|如何|怎麼|怎么|怎樣|怎样|改成|"
    r"弄成|做成|實作|实现|提供|撰寫|撰写|編寫|编写|寫|写|產生|生成|"
    r"自動|自动|直接|我要|我想要|一鍵|一键|"
    r"how\s+(?:do|can|to)|give\s+me|show\s+me|help\s+me|please|"
    r"write|create|build|make|modify|implement|turn|execute|run)"
)

_UNSAFE_ACTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        _UNSAFE_ACTION_LEAD
        + r".{0,60}(?:不可逆|無法復原|无法恢复|真的(?:勒索)?|真正的勒索|"
        r"irreversib(?:le|ly)|real\s+ransomware).{0,20}(?:加密|encrypt)",
        _UNSAFE_ACTION_LEAD
        + r".{0,60}(?:加密|encrypt).{0,20}(?:不可逆|無法復原|无法恢复|"
        r"irreversib(?:le|ly)|without\s+(?:a\s+)?recovery)",
        _UNSAFE_ACTION_LEAD
        + r".{0,60}(?:擴散|扩散|傳播|传播|遍歷|遍历|掃描|扫描|掃|扫|處理|处理|"
        r"修改|加密|spread|propagat(?:e|ion)|traverse|scan|encrypt|modify)"
        r".{0,25}(?:所有|全部|可存取|可访问|reachable|every|all)"
        r".{0,20}(?:網路\s*share|网络共享|共享資料夾|共享文件夹|smb\s*share|"
        r"network\s*share)",
        _UNSAFE_ACTION_LEAD
        + r".{0,60}(?:所有|全部|可存取|可访问|reachable|every|all)"
        r".{0,20}(?:網路\s*share|网络共享|共享資料夾|共享文件夹|smb\s*share|"
        r"network\s*share).{0,25}(?:擴散|扩散|傳播|传播|遍歷|遍历|掃描|扫描|掃|扫|"
        r"處理|处理|修改|加密|spread|propagate|traverse|scan|encrypt|modify)",
        _UNSAFE_ACTION_LEAD
        + r".{0,45}(?:停止|停用|停掉|關閉|关闭|终止|終止|禁用|kill|stop|disable|"
        r"shut\s*down).{0,20}(?:服務|服务|service)",
        _UNSAFE_ACTION_LEAD
        + r".{0,45}(?:服務|服务|service).{0,20}"
        r"(?:停止|停用|停掉|關閉|关闭|终止|終止|禁用|kill|stop|disable|"
        r"shut\s*down)",
        _UNSAFE_ACTION_LEAD
        + r".{0,45}(?:刪除|删除|清除|清掉|抹除|移除|wipe|delete|remove|clear)"
        r".{0,20}(?:備份|备份|backup|shadow\s*cop(?:y|ies)|陰影複製|"
        r"卷影副本|vss(?:\s+snapshot)?|restore\s*point)",
        _UNSAFE_ACTION_LEAD
        + r".{0,45}(?:備份|备份|backup|shadow\s*cop(?:y|ies)|陰影複製|"
        r"卷影副本|vss(?:\s+snapshot)?|restore\s*point).{0,20}"
        r"(?:刪除|删除|清除|清掉|抹除|移除|wipe|delete|remove|clear)",
        r"(?:不可逆加密|停止\s*dc01\s*(?:服務|服务)|刪除\s*dc01\s*(?:備份|备份)|"
        r"irreversible\s+encryption|stop\s+dc01\s+services?|"
        r"delete\s+dc01\s+backups?).{0,35}"
        r"(?:指令|命令|腳本|程式碼|代码|command|script|code)",
        r"^\s*(?:不可逆地?|irreversibly)\s*(?:加密|encrypt)",
        r"^\s*(?:加密|encrypt).{0,30}(?:不可逆|無法復原|无法恢复|"
        r"irreversib(?:le|ly)|without\s+(?:a\s+)?recovery)",
        r"^\s*(?:停止|停用|停掉|關閉|关闭|终止|終止|禁用|kill|stop|disable|"
        r"shut\s*down).{0,25}(?:服務|服务|services?)",
        r"^\s*(?:刪除|删除|清除|清掉|抹除|移除|wipe|delete|remove|clear)"
        r".{0,25}(?:備份|备份|backups?|shadow\s*cop(?:y|ies)|陰影複製|"
        r"卷影副本|vss(?:\s+snapshot)?|restore\s*point)",
        r"^\s*(?:擴散|扩散|傳播|传播|加密|spread|propagate|encrypt).{0,40}"
        r"(?:所有|全部|可存取|可访问|reachable|every|all).{0,20}"
        r"(?:網路\s*share|网络共享|共享資料夾|共享文件夹|smb\s*share|"
        r"network\s*share)",
        _UNSAFE_ACTION_LEAD
        + r".{0,45}(?:iec-?104|ot-server01|ot\s*server).{0,25}"
        r"(?:控制|control|指令|命令|command)",
        _UNSAFE_ACTION_LEAD
        + r".{0,45}(?:真的|真正|real).{0,20}"
        r"(?:刪除|删除|清除|清掉|抹除|wipe|delete|remove).{0,25}"
        r"(?:檔案|文件|files?|data)",
    )
)

# Questions about whether an action was observed are evidence-analysis, not
# operational requests.  An explicit request for commands/code still wins.
_INCIDENT_ANALYSIS_PATTERN = re.compile(
    r"(?:是否|有沒有|有没有|有無|有无|真的發生|真的发生|實際發生|实际发生|"
    r"觀察到|观察到|證據|证据|紀錄|记录|日誌|日志|能否證明|能否证明|"
    r"能不能證明|能不能证明|可否確認|可否确认|可以確認|可以确认|只是|"
    r"dry[- ]?run|模擬|模拟|observed|whether|"
    r"(?:從|从|根據|根据)?\s*(?:evidence|證據|证据|logs?|日誌|日志)"
    r".{0,30}(?:顯示|显示|判斷|判断|證明|证明|支持|show|prove|establish|"
    r"support|indicate|whether|observed|occur)|"
    r"(?:did|was|were).{0,30}(?:happen|occur|observed))",
    re.IGNORECASE,
)

_OPERATIONAL_DELIVERABLE_PATTERN = re.compile(
    r"(?:改成|弄成|做成|實作|实现|自動|自动|一鍵|一键|"
    r"(?:給我|给我|提供|撰寫|撰写|編寫|编写|寫|写|give\s+me|show\s+me|"
    r"write|create|build).{0,25}(?:指令|命令|腳本|程式碼|代码|command|"
    r"script|code)|"
    r"(?:幫我|帮我|教我|給我|给我|直接|help\s+me|please|execute|run).{0,25}"
    r"(?:加密|刪除|删除|清掉|停止|停用|停掉|擴散|扩散|傳播|传播|encrypt|delete|"
    r"remove|stop|disable|spread|propagate))",
    re.IGNORECASE,
)


_SAFE_SIMULATION_GUIDANCE_PATTERN = re.compile(
    r"(?:隔離(?:環境)?|isolated\s+lab|安全可控|safe\s+(?:lab|simulation)|"
    r"安全模擬|可逆(?:模擬)?).{0,50}"
    r"(?:步驟|流程|方式|怎麼做|如何(?:進行|模擬)|how\s+to)",
    re.IGNORECASE,
)

_PROMPT_INJECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:忽略|无视|跳过|绕过|不要遵守|不必遵守|解除|別管|别管)"
        r".{0,35}(?:先前|之前|系统|规则|指示|限制|证据|證據|evidence|"
        r"schema|prompt)",
        r"(?:ignore|disregard|bypass|override).{0,35}"
        r"(?:previous|prior|system|instruction|rule|evidence|schema|prompt|"
        r"retrieved\s+context)",
        r"(?:顯示|显示|列出|輸出|输出|洩漏|泄露|透露|reveal|show|print|leak)"
        r".{0,25}(?:系統提示|系统提示|隱藏提示|隐藏提示|system\s*prompt|"
        r"developer\s*message)",
        r"(?:引用|使用|加入|填入|捏造|偽造|伪造|虛構|虚构|編造|编造|cite|"
        r"use|invent|fabricate|make\s+up).{0,35}"
        r"(?:不存在|假的|虛構|虚构|fake|nonexistent|made[- ]?up).{0,15}"
        r"(?:evidence|證據|证据)",
        r"(?:捏造|偽造|伪造|虛構|虚构|編造|编造|亂編|乱编|隨便編|随便编|"
        r"invent|fabricate|make\s+up)"
        r".{0,25}(?:evidence|證據|证据)(?:\s*(?:id|編號|编号))?",
        r"(?:引用|使用|cite|use).{0,20}ryuk-x\d+",
        r"(?:不要|別|别).{0,20}(?:檢索|检索|證據|证据).{0,30}"
        r"(?:自己的知識|自己的知识|常識|常识|own\s+knowledge)",
        r"(?:不要|別|别).{0,25}(?:區分|区分|顯示|显示|輸出|输出)"
        r".{0,40}(?:known[ _-]?facts|reasonable[ _-]?inferences|unknowns)",
        r"(?:answer|respond).{0,30}(?:own|external).{0,20}knowledge.{0,35}"
        r"(?:instead|without).{0,25}(?:retrieved|evidence|context)",
    )
)

_CONTEXT_DEPENDENT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(?:那|所以|因此|然後)?\s*(?:前一個|後一個|上一個|上面那個|剛才那個)",
        r"^(?:所以|因此|那)\s*(?:它|他|這個|那個|這些|那些)"
        r"\s*(?:就是|是|能|可以|實際|到底|有|沒有)",
        r"^(?:它|他|這個|那個)\s*(?:就是|是|能|可以|實際|到底|有|沒有)",
    )
)


def normalize_question_identifiers(question: str) -> str:
    """Repair high-confidence lab identifier typos without changing prose."""

    normalized = question.strip()
    for typo, replacement in _SCOPE_ALIASES.items():
        normalized = re.sub(
            re.escape(typo),
            replacement,
            normalized,
            flags=re.IGNORECASE,
        )
    return normalized


def normalize_scope_text(question: str) -> str:
    """Return a case-insensitive form used only for deterministic routing."""

    return normalize_question_identifiers(question).casefold()


def is_safety_boundary_request(question: str) -> bool:
    """Return whether the student requests an operationally unsafe action."""

    normalized = normalize_scope_text(question)
    unsafe_match = any(
        pattern.search(normalized) for pattern in _UNSAFE_ACTION_PATTERNS
    )
    if not unsafe_match:
        return False
    if (
        _INCIDENT_ANALYSIS_PATTERN.search(normalized)
        and not _OPERATIONAL_DELIVERABLE_PATTERN.search(normalized)
    ):
        return False
    return True


def is_prompt_injection_request(question: str) -> bool:
    """Detect attempts to bypass policy, evidence, or prompt constraints."""

    normalized = normalize_scope_text(question)
    return any(pattern.search(normalized) for pattern in _PROMPT_INJECTION_PATTERNS)


def depends_on_missing_conversation_context(question: str) -> bool:
    """Detect a leading reference whose antecedent is absent from the turn.

    This deliberately checks leading references only.  A question such as
    ``復原流程中，它實際輸出什麼？`` contains its antecedent in the same
    sentence and must remain eligible for incident retrieval.
    """

    normalized = normalize_scope_text(question)
    return any(pattern.search(normalized) for pattern in _CONTEXT_DEPENDENT_PATTERNS)


def scope_uses_retrieval(question_scope: QuestionScope) -> bool:
    """Return whether the scope needs a controlled knowledge retrieval."""

    return question_scope in {"in_scope", "general_concept", "comparison"}


def is_general_knowledge_scope(question_scope: QuestionScope) -> bool:
    """Return whether generic OT reference chunks must be included."""

    return question_scope in {"general_concept", "comparison"}


def classify_question_scope(
    question: str,
    scenario: str = "ryuk",
) -> QuestionScope:
    """Classify without retrieval; mixed or unrecognized intent is ambiguous."""

    profile = get_scenario_profile(scenario)
    normalized = normalize_scope_text(question)
    if not normalized:
        return "ambiguous"
    # Policy checks run before topic and ambiguity routing.  These requests
    # must never reach incident retrieval or a generative model.
    if is_prompt_injection_request(normalized):
        return "prompt_injection"
    if profile.key == "frostygoop" and _FROSTYGOOP_OPERATIONAL_CONTROL_PATTERN.search(
        normalized
    ):
        return "safety_boundary"
    if is_safety_boundary_request(normalized):
        return "safety_boundary"
    # A request for a safe, isolated simulation workflow receives the same
    # deterministic safety redirect as an unsafe operational request. The
    # redirect stays high level and never provides device-control steps.
    if _SAFE_SIMULATION_GUIDANCE_PATTERN.search(normalized):
        return "safety_boundary"
    if depends_on_missing_conversation_context(normalized):
        return "ambiguous"
    has_general_term = any(
        marker in normalized for marker in _GENERAL_CONCEPT_TERMS
    )
    has_general_request = any(
        marker in normalized for marker in _GENERAL_CONCEPT_REQUEST_MARKERS
    )
    explicitly_general_only = any(
        marker in normalized for marker in _EXPLICIT_GENERAL_ONLY_MARKERS
    )
    has_lab_specific_marker = any(
        marker in normalized
        for marker in _LAB_SPECIFIC_MARKERS[profile.key]
    )
    definition_question = any(
        marker in normalized for marker in _DEFINITION_MARKERS
    )
    if explicitly_general_only and has_general_term:
        return "general_concept"
    if has_general_request and has_general_term:
        return "comparison" if has_lab_specific_marker else "general_concept"
    if definition_question and has_general_term and not has_lab_specific_marker:
        return "general_concept"
    scope_markers = {
        "ryuk": _RYUK_SCOPE_MARKERS,
        "industroyer2": _INDUSTROYER2_SCOPE_MARKERS,
        "frostygoop": _FROSTYGOOP_SCOPE_MARKERS,
    }[profile.key]
    has_scope_marker = any(
        marker.casefold() in normalized for marker in scope_markers
    )
    has_off_topic_marker = any(
        marker.casefold() in normalized
        for marker in _CLEARLY_OFF_TOPIC_MARKERS
    )
    if has_scope_marker and has_off_topic_marker:
        return "ambiguous"
    if has_off_topic_marker:
        return "off_topic"
    if has_scope_marker:
        return "in_scope"
    return "ambiguous"


def is_clearly_off_topic(
    question: str,
    scenario: str = "ryuk",
) -> bool:
    """Conservatively detect questions clearly outside the Ryuk lesson."""

    return classify_question_scope(question, scenario=scenario) == "off_topic"


def build_off_topic_response(scenario: str = "ryuk") -> TutorResponse:
    """Return a stable response without retrieval or Llama generation."""

    profile = get_scenario_profile(scenario)
    return TutorResponse(
        answer=f"這個問題不屬於目前的 {profile.display_name} 事件還原教學範圍。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=["目前的情境資料沒有與此問題相關的事件證據。"],
        evidence_ids=[],
        next_hint=(
            f"你可以改問 {profile.suggested_topics}。"
        ),
    )


def build_ambiguous_response(
    scenario: str = "ryuk",
    conversation_context_available: bool = False,
) -> TutorResponse:
    """Ask the student to split mixed or context-free questions."""

    profile = get_scenario_profile(scenario)
    if conversation_context_available:
        return TutorResponse(
            answer=(
                "我保留了前一輪的對話脈絡，但「那個」仍可能指主機、通訊路徑或證據；"
                "請補上要問的名稱、IP、主機或 evidence ID。"
            ),
            known_facts=[],
            reasonable_inferences=[],
            unknowns=["目前無法唯一判定代詞所指的事件項目。"],
            evidence_ids=[],
            next_hint="例如可改問「前一輪提到的 Kali 到 DC01 SMB 路徑是什麼？」。",
        )
    return TutorResponse(
        answer=(
            f"這個問題缺少足夠的 {profile.display_name} 情境線索，或同時混合了情境內外的"
            "不同問題，因此目前先不進行事件檢索。"
        ),
        known_facts=[],
        reasonable_inferences=[],
        unknowns=["目前無法確定學生希望查證的單一事件問題。"],
        evidence_ids=[],
        next_hint=(
            "請一次聚焦一個問題，並指出要查證的主機、事件階段或 evidence ID。"
        ),
    )


def build_safety_boundary_response(scenario: str = "ryuk") -> TutorResponse:
    """Refuse destructive operations and redirect to safe lab techniques."""

    profile = get_scenario_profile(scenario)
    if profile.key == "frostygoop":
        return TutorResponse(
            answer=(
                "我不能提供對真實 PLC 發送 Modbus 控制、修改暫存器，或掃描可連線設備的步驟與程式。"
            ),
            known_facts=[
                "本情境只允許報告衍生、隔離且安全的 localhost Lab 重建。"
            ],
            reasonable_inferences=[],
            unknowns=[],
            evidence_ids=[],
            next_hint=(
                "可以改從 Task JSON、PCAP 對照與資料品質限制分析：哪些 Lab 請求被捕獲，"
                "以及為何這仍不代表設備接受或實體影響。"
            ),
        )
    if profile.key == "industroyer2":
        return TutorResponse(
            answer=(
                "我不能提供讓 OT-SERVER01 執行實際 IEC-104 控制，或把 "
                "Safe CaddyWiper 改成真實刪除／破壞行為的操作指令。"
                "若目標是隔離且可逆的教學模擬，可使用既有安全 case，"
                "再由 SENSOR01 收集、正規化並比較證據。"
            ),
            known_facts=[
                "本專案只允許隔離、可逆且不影響真實 OT 設備的教學模擬。",
                "既有流程會以 Kali、ENG01、OT-SERVER01 與 SENSOR01 的紀錄進行事件還原。",
            ],
            reasonable_inferences=[],
            unknowns=[],
            evidence_ids=[],
            next_hint=(
                "可先選擇正常 ENG、Kali direct 或 Kali via ENG Relay 的既有安全 case；"
                "完成後只檢視收集、正規化與證據關聯結果，不對真實設備下達控制。"
            ),
        )
    return TutorResponse(
        answer=(
            "我不能協助不可逆加密、擴散到網路 Share、停止服務或刪除備份，"
            "也不會提供相關操作指令。"
        ),
        known_facts=[
            "本專案只允許隔離、可逆且不破壞系統的教學模擬。"
        ],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=[],
        next_hint=(
            "可以改為設計隔離測試資料上的可逆轉換、dry-run，並驗證偵測與復原流程。"
        ),
    )


def build_prompt_injection_response(scenario: str = "ryuk") -> TutorResponse:
    """Reject attempts to bypass evidence and system-output constraints."""

    profile = get_scenario_profile(scenario)
    return TutorResponse(
        answer=(
            "我不會忽略系統規則、evidence 白名單或輸出驗證，也不會捏造或引用"
            "不存在的證據。"
        ),
        known_facts=[],
        reasonable_inferences=[],
        unknowns=["未經檢索驗證，不能接受題目預先指定的證據或結論。"],
        evidence_ids=[],
        next_hint=(
            f"請改問一個能由目前 {profile.display_name} 情境證據查證的事件問題。"
        ),
    )


def build_scope_response(
    question_scope: QuestionScope,
    scenario: str = "ryuk",
) -> TutorResponse:
    """Build the deterministic response for a non-retrieval scope result."""

    if scope_uses_retrieval(question_scope):
        raise ValueError(f"{question_scope} questions require retrieval")
    if question_scope == "safety_boundary":
        return build_safety_boundary_response(scenario)
    builders = {
        "off_topic": build_off_topic_response,
        "ambiguous": build_ambiguous_response,
        "prompt_injection": build_prompt_injection_response,
    }
    return builders[question_scope](scenario)
