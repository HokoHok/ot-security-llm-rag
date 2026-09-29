from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioProfile:
    """Generation and routing rules that differ between lab scenarios."""

    key: str
    display_name: str
    event_boundary_instruction: str
    safety_instruction: str
    identifier_example: str
    default_question_focus: str
    default_hint: str
    suggested_topics: str


_PROFILES = {
    "ryuk": ScenarioProfile(
        key="ryuk",
        display_name="Ryuk-like",
        event_boundary_instruction=(
            "本情境的 Kali、USER01、DC01 與 SENSOR01 是安全教學環境中的"
            "角色；回答事件經過時不得超出檢索證據。"
        ),
        safety_instruction=(
            "不得把 Caesar shift 描述為真實 Ryuk 加密，也不得把實驗檔案數"
            "描述成歷史事件統計。"
        ),
        identifier_example=r"RYUK\employee01",
        default_question_focus=(
            "先直接回應學生問的 Ryuk-like 情境重點，再用檢索到的主機角色、"
            "事件階段或端點紀錄說明根據；不要改答成泛泛的事件摘要。"
        ),
        default_hint=(
            "請從最相關的證據挑一項，說明它能直接證明什麼，以及還不能證明什麼。"
        ),
        suggested_topics="Kali、USER01、DC01、事件證據或安全模擬範圍",
    ),
    "industroyer2": ScenarioProfile(
        key="industroyer2",
        display_name="Industroyer2-like",
        event_boundary_instruction=(
            "本情境包含正常 ENG 維護、Kali 直接 IEC-104 操作、Kali 經 ENG Relay"
            " 操作，以及 Safe CaddyWiper lockout；必須依 case 與多端證據區分。"
        ),
        safety_instruction=(
            "不得把 Industroyer2-like 或 Safe CaddyWiper 描述為真實惡意程式。"
            "dummy files 是安全模擬資料；maintenance_ticket 是 ENG 本機證據，"
            "除非檢索內容另有明示，不得說它存在 IEC-104 封包或 Server state。"
            "單靠 source_ip=10.10.60.20 不得判定正常維護或 Kali Relay。"
        ),
        identifier_example="IND2-N20",
        default_question_focus=(
            "先直接回答學生問的 Industroyer2-like 情境差異，再說明是哪一端的"
            "紀錄支持結論；區分命令被接受、實際送出 IEC-104 與 Server 收到命令。"
        ),
        default_hint=(
            "請比較 Kali、ENG01 與 OT-SERVER01 的紀錄，找出哪一項只能由跨主機關聯確認。"
        ),
        suggested_topics=(
            "ENG01、OT-SERVER01、IEC-104、Relay、maintenance ticket、"
            "Safe CaddyWiper 或跨主機證據"
        ),
    ),
    "frostygoop": ScenarioProfile(
        key="frostygoop",
        display_name="FrostyGoop 報告衍生安全重建",
        event_boundary_instruction=(
            "This scenario is a report-derived safe Lab reconstruction. "
            "Only describe Task JSON, localhost PCAP, and detector output as "
            "Lab observations, never as real malware, PLC, RTU, or process facts."
        ),
        safety_instruction=(
            "FC03, FC06, and FC16 are safe localhost TCP/1502 Lab tests. "
            "PCAP proves a captured request, not device acceptance, register "
            "change, or physical impact. FC06 and FC16 are not observed real "
            "FrostyGoop behavior."
        ),
        identifier_example="FROSTY-N06",
        default_question_focus=(
            "Separate Lab facts from conclusions that current Task JSON, PCAP, "
            "detector, and data-quality evidence cannot support."
        ),
        default_hint=(
            "Compare the function code and Task JSON/PCAP correlation, then "
            "check which conclusions the data-quality record excludes."
        ),
        suggested_topics=(
            "Modbus FC03/FC06/FC16, Task JSON, PCAP, TCP/1502, detection, "
            "and evidence limits"
        ),
    ),
}


def get_scenario_profile(scenario: str) -> ScenarioProfile:
    """Return a supported scenario profile or raise a clear error."""

    normalized = scenario.strip().casefold()
    try:
        return _PROFILES[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(_PROFILES))
        raise ValueError(
            f"Unsupported scenario {scenario!r}; expected one of: {supported}"
        ) from exc


def scenario_display_name(scenario: str) -> str:
    """Return the student-facing name for a supported scenario."""

    return get_scenario_profile(scenario).display_name


__all__ = ["ScenarioProfile", "get_scenario_profile", "scenario_display_name"]
