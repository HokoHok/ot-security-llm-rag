from typing import Any

import pytest

from app.generation import (
    OllamaChatClient,
    allowed_evidence_ids,
    build_contextual_hint,
    build_messages,
    build_question_focus,
    build_response_schema,
    explicitly_requested_evidence_ids,
    generate_tutor_response,
    normalize_student_text,
    observed_execution_attribution,
    refine_student_response,
    repair_domain_identity_format,
    validate_context_identifiers,
    validate_operational_safety,
    validate_question_alignment,
    validate_response_evidence_mentions,
    validate_requested_evidence_citations,
    validate_response_semantics,
    validate_student_visible_text,
)
from app.retrieval import RetrievedChunk
from app.schemas import TutorResponse
from app.validation import validate_tutor_response


def _retrieved() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id="RYUK-execution-impact-01",
            document=(
                "標題：USER01 執行\n"
                "內容：Source Host 記為 USER01，"
                "DC01 是 Share 所在端。"
            ),
            metadata={
                "stage": "execution",
                "source_type": "observed_log",
                "evidence_ids": "RYUK-L02",
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]


def test_general_iec104_refinement_keeps_standard_and_lab_field_separate() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="OT-iec104-overview-01",
            document="IEC-104 uses TCP/IP transport profiles for telecontrol.",
            metadata={
                "source_type": "public_reference",
                "evidence_ids": "OT-G01",
            },
            rrf_score=0.02,
            dense_rank=1,
            sparse_rank=1,
        ),
        RetrievedChunk(
            chunk_id="OT-transmission-evidence-boundary-01",
            document="Sent records do not prove acceptance or physical impact.",
            metadata={
                "source_type": "teaching_convention",
                "evidence_ids": "OT-G02",
            },
            rrf_score=0.01,
            dense_rank=2,
            sparse_rank=2,
        ),
        RetrievedChunk(
            chunk_id="OT-lab-field-boundary-01",
            document="iec104_sent is a local Relay and Normalizer field.",
            metadata={
                "source_type": "teaching_convention",
                "evidence_ids": "OT-G03",
            },
            rrf_score=0.01,
            dense_rank=3,
            sparse_rank=3,
        ),
    ]
    response = TutorResponse(
        answer="模型原始回答。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["OT-G01"],
        next_hint="原始提示。",
    )

    refined = refine_student_response(
        "廣義的 iec104_sent 是 SCADA 中用 TCP/IP 做遠端控制的嗎？先不論目前模擬。",
        response,
        retrieved,
        scenario="industroyer2",
        knowledge_scope="general_concept",
    )

    assert "TCP/IP" in refined.answer
    assert "不是資安領域或 IEC 標準的通用術語" in refined.answer
    assert "Kali" not in refined.answer
    assert "\n" not in refined.answer
    assert refined.evidence_ids == ["OT-G01", "OT-G02", "OT-G03"]


def test_frostygoop_impact_question_keeps_device_acceptance_unknown() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="frostygoop-fc06-data-quality-05",
                document=(
                    "PCAP proves the captured Lab request and does not prove device "
                    "acceptance or physical impact. No real PLC, RTU, or physical "
                    "process is involved."
                ),
            metadata={
                "source_type": "normalized_data_quality",
                "evidence_ids": "FROSTY-N09",
            },
            rrf_score=0.02,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    response = TutorResponse(
        answer="偵測器顯示 ALERT。",
        known_facts=["偵測器有結果。"],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["FROSTY-N09"],
        next_hint="請查看偵測器結果。",
    )

    refined = refine_student_response(
        "只看單一 Modbus 寫入封包，能直接證明造成哪些實體設備影響嗎？",
        response,
        retrieved,
        response_mode="explain",
        scenario="frostygoop",
    )

    assert refined.answer.startswith("不能")
    assert "實體設備" in refined.unknowns[0]
    assert refined.evidence_ids == ["FROSTY-N09"]
    assert "接收端狀態" in refined.next_hint


def test_general_windows_event_refinement_keeps_facts_out_of_inferences() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="OT-windows-event-logging-01",
            document="4104 records script blocks and 4688 records process creation.",
            metadata={
                "source_type": "public_reference",
                "evidence_ids": "OT-G05",
            },
            rrf_score=0.02,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    response = TutorResponse(
        answer="模型原始回答。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["OT-G05"],
        next_hint="原始提示。",
    )

    refined = refine_student_response(
        "PowerShell 4104 與 Security 4688 在資安領域分別代表什麼？先不論這次 Ryuk Lab。",
        response,
        retrieved,
        knowledge_scope="general_concept",
    )

    assert len(refined.known_facts) == 2
    assert not refined.reasonable_inferences
    assert refined.evidence_ids == ["OT-G05"]
    assert "Lab" in refined.next_hint


class StubChatProvider:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.messages: list[dict[str, str]] = []

    def generate_json(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> Any:
        self.messages = messages
        assert response_schema["additionalProperties"] is False
        return self.payload


class SequenceChatProvider:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.call_count = 0

    def generate_json(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> Any:
        payload = self.payloads[self.call_count]
        self.call_count += 1
        return payload


def _valid_payload() -> dict[str, Any]:
    return {
        "answer": "USER01 是有直接觀測支持的實際執行端。",
        "known_facts": ["USER01 是執行端。"],
        "reasonable_inferences": [],
        "unknowns": [],
        "evidence_ids": ["RYUK-L02"],
        "next_hint": "哪一份紀錄直接標示 Source Host？",
    }


def _industroyer2_retrieved() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id="IND2-comparison-source-01",
            document=(
                "正常 ENG 與 Kali via ENG Relay 都可能讓 OT-SERVER01 看見 "
                "source_ip=10.10.60.20。Server source IP alone cannot distinguish "
                "normal ENG operation from Kali via ENG relay；必須關聯 Kali action "
                "log、ENG maintenance console log、ENG relay log 與 Server command log。"
            ),
            metadata={
                "stage": "analysis",
                "source_type": "normalized_analysis",
                "title": "相同 ENG01 source IP 的歸因限制",
                "evidence_ids": "IND2-N20,IND2-R20",
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]


def test_industroyer2_prompt_uses_scenario_profile_without_ryuk_rules() -> None:
    messages = build_messages(
        "source_ip=10.10.60.20 能直接證明是正常維護嗎？",
        _industroyer2_retrieved(),
        scenario="industroyer2",
    )
    combined = "\n".join(message["content"] for message in messages)

    assert "目前情境：Industroyer2-like" in combined
    assert "單靠 source_ip=10.10.60.20 不得判定" in combined
    assert "正常 ENG 與 Kali via ENG Relay" in combined
    assert "Caesar shift" not in combined
    assert r"RYUK\employee01" not in combined


def test_industroyer2_question_focus_and_hint_target_attribution_gap() -> None:
    question = "source_ip=10.10.60.20 能直接證明是正常維護嗎？"

    focus = build_question_focus(question, scenario="industroyer2")
    hint = build_contextual_hint(
        question,
        _industroyer2_retrieved(),
        scenario="industroyer2",
    )

    assert "來源歸因題" in focus
    assert "相同" in focus
    assert "10.10.60.20" in hint
    assert "真正的發起來源" in hint


def test_industroyer2_semantics_rejects_source_ip_only_attribution() -> None:
    response = TutorResponse(
        answer="10.10.60.20 一定證明這是 Kali Relay。",
        known_facts=["OT-SERVER01 看見 source_ip=10.10.60.20。"],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N20"],
        next_hint="接著查看 ENG01。",
    )

    with pytest.raises(ValueError, match="absolute attribution"):
        validate_response_semantics(
            response,
            _industroyer2_retrieved(),
            scenario="industroyer2",
        )


def test_industroyer2_valid_bounded_attribution_passes_semantics() -> None:
    response = TutorResponse(
        answer=(
            "不能只靠 10.10.60.20 判定；正常 ENG 與 Kali Relay 都可能留下"
            "這個 Server 來源 IP。"
        ),
        known_facts=["OT-SERVER01 看見 source_ip=10.10.60.20。"],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N20", "IND2-R20"],
        next_hint="哪一端的 action log 能補上原始發起者？",
    )

    validate_response_semantics(
        response,
        _industroyer2_retrieved(),
        scenario="industroyer2",
    )


def test_industroyer2_source_ip_host_mapping_keeps_eng01_and_server_distinct() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="IND2-normal-eng-source-01",
            document=(
                "Normal ENG traffic: Server and PCAP see ENG01 as "
                "source_ip=10.10.60.20 over TCP/2404."
            ),
            metadata={"evidence_ids": "IND2-N02"},
            rrf_score=0.02,
            dense_rank=1,
            sparse_rank=1,
        ),
        RetrievedChunk(
            chunk_id="IND2-relay-success-01",
            document=(
                "ENG01 sent the IEC-104 command to OT-SERVER01, and Server "
                "observed source_ip=10.10.60.20 over TCP/2404."
            ),
            metadata={"evidence_ids": "IND2-N20"},
            rrf_score=0.01,
            dense_rank=2,
            sparse_rank=2,
        ),
    ]
    raw = TutorResponse(
        answer="10.10.60.20 是 OT-SERVER01 的 IP。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N02"],
        next_hint="確認來源。",
    )

    refined = refine_student_response(
        "10.10.60.20 是哪一台虛擬機的 IP？",
        raw,
        retrieved,
        response_mode="explain",
        scenario="industroyer2",
    )

    rendered = "\n".join(
        [refined.answer, *refined.known_facts, *refined.reasonable_inferences]
    )
    assert "10.10.60.20 是 ENG01 的 IP" in refined.answer
    assert "OT-SERVER01 是接收端" in refined.answer
    assert "OT-SERVER01 的 IP" not in rendered
    assert refined.evidence_ids == ["IND2-N02", "IND2-N20"]


def test_industroyer2_lockout_alignment_requires_confirmed_not_sent() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="IND2-ve-003-eng-relay-05",
            document=(
                "After Safe CaddyWiper lockout: accepted=False "
                "iec104_sent=False outcome=blocked_by_safe_caddywiper."
            ),
            metadata={"evidence_ids": "IND2-N17"},
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    vague = TutorResponse(
        answer="Relay 被阻擋，所以無法確認是否送出 IEC-104。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N17"],
        next_hint="查看 Relay 狀態。",
    )

    with pytest.raises(ValueError, match="iec104_sent=false"):
        validate_question_alignment(
            "CaddyWiper lockout 後，Relay 有送出 IEC-104 嗎？",
            vague,
            retrieved,
            scenario="industroyer2",
        )


def test_industroyer2_lockout_natural_wording_is_refined() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="IND2-ve-003-eng-relay-05",
            document=(
                "After Safe CaddyWiper lockout: accepted=False "
                "iec104_sent=False outcome=blocked_by_safe_caddywiper."
            ),
            metadata={"evidence_ids": "IND2-N17"},
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    raw = TutorResponse(
        answer="可能有送到 Server。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N17"],
        next_hint="查看 Relay。",
    )

    refined = refine_student_response(
        "Relay 被 CaddyWiper lockout 擋住時，命令有真的送到 Server 嗎？",
        raw,
        retrieved,
        response_mode="explain",
        scenario="industroyer2",
    )

    assert "accepted=False" in refined.answer
    assert "iec104_sent=False" in refined.answer
    assert "不能" in refined.answer


def test_industroyer2_english_source_ip_question_keeps_unknown() -> None:
    raw = TutorResponse(
        answer="The source IP is normal ENG.",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N20"],
        next_hint="Compare records.",
    )

    refined = refine_student_response(
        "Why can’t source_ip=10.10.60.20 prove this was normal ENG maintenance?",
        raw,
        _industroyer2_retrieved(),
        response_mode="explain",
        scenario="industroyer2",
    )

    assert "不能" in refined.answer
    assert "最初發起者" in refined.answer
    assert refined.unknowns


def test_industroyer2_detailed_iec104_fields_are_bounded() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="IND2-server-command",
            document="Server received IEC-104 command to IOA 1002.",
            metadata={"evidence_ids": "IND2-N23"},
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        ),
        RetrievedChunk(
            chunk_id="IND2-data-quality",
            document=(
                "PCAP files are present, but tshark is not installed; "
                "normalizer did not decode IEC-104 fields."
            ),
            metadata={"evidence_ids": "IND2-N26"},
            rrf_score=0.009,
            dense_rank=2,
            sparse_rank=2,
        ),
    ]
    raw = TutorResponse(
        answer="ASDU details are available.",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N23"],
        next_hint="Check packet fields.",
    )

    refined = refine_student_response(
        "請列出 ind2_vE_004 的 IEC-104 ASDU、type ID 和 IOA 詳細值。",
        raw,
        retrieved,
        response_mode="explain",
        scenario="industroyer2",
    )

    assert "不能列出完整" in refined.answer
    assert "tshark" in refined.answer
    assert refined.unknowns


def test_industroyer2_relay_correlation_requires_three_evidence_layers() -> None:
    incomplete = TutorResponse(
        answer="需要查看 Kali action 與 ENG Relay 紀錄。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N20"],
        next_hint="再找一層證據。",
    )

    with pytest.raises(ValueError, match="Server command"):
        validate_question_alignment(
            "要判斷 Kali 是否經 ENG Relay 成功，需關聯哪些紀錄？",
            incomplete,
            _industroyer2_retrieved(),
            scenario="industroyer2",
        )


def test_industroyer2_relay_answer_keeps_unrequested_ids_out_of_prose() -> None:
    response = TutorResponse(
        answer=(
            "IND2-N20 顯示 Kali action 提出要求，ENG Relay 送出 IEC-104，"
            "Server command 紀錄確認收到指令。"
        ),
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N20"],
        next_hint="再比較三端紀錄。",
    )

    with pytest.raises(ValueError, match="do not repeat evidence IDs"):
        validate_question_alignment(
            "要判斷 Kali 是否經 ENG Relay 成功，需關聯哪些紀錄？",
            response,
            _industroyer2_retrieved(),
            scenario="industroyer2",
        )


def test_industroyer2_relay_correlation_is_refined_to_three_layers() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="IND2-relay-state",
            document=(
                "relay accepted=True iec104_sent=True "
                "outcome=eng_relay_successful_iec104_send"
            ),
            metadata={"evidence_ids": "IND2-N24"},
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        ),
        RetrievedChunk(
            chunk_id="IND2-relay-summary",
            document=(
                "Kali requested a command through ENG Relay. Server observed "
                "source_ip=10.10.60.20 and must be correlated with Kali logs."
            ),
            metadata={"evidence_ids": "IND2-N20"},
            rrf_score=0.009,
            dense_rank=2,
            sparse_rank=2,
        ),
        RetrievedChunk(
            chunk_id="IND2-relay-quality",
            document=(
                "SENSOR01 does not have tshark installed; PCAP metadata has "
                "file size and SHA-256 only, not decoded IEC-104 fields."
            ),
            metadata={"evidence_ids": "IND2-N26"},
            rrf_score=0.008,
            dense_rank=3,
            sparse_rank=3,
        ),
    ]
    raw = TutorResponse(
        answer="IND2-N24 顯示成功。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N24"],
        next_hint="請比較證據。",
    )

    refined = refine_student_response(
        "要判斷 Kali 是否經 ENG Relay 成功，需關聯哪些紀錄？",
        raw,
        retrieved,
        response_mode="explain",
        scenario="industroyer2",
    )

    assert "Kali action" in refined.answer
    assert "ENG Relay" in refined.answer
    assert "Server command" in refined.answer
    assert "IND2-N24" not in refined.answer
    assert refined.evidence_ids == ["IND2-N24", "IND2-N20"]
    assert any("tshark" in item for item in refined.unknowns)


def test_relay_comparison_question_is_not_overwritten_by_path_answer() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="IND2-relay-state",
            document=(
                "relay accepted=True iec104_sent=True "
                "outcome=eng_relay_successful_iec104_send"
            ),
            metadata={"evidence_ids": "IND2-N24"},
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        ),
        RetrievedChunk(
            chunk_id="IND2-relay-summary",
            document=(
                "Kali requested a command through ENG Relay. ENG01 sent the "
                "IEC-104 command to OT-SERVER01. Server observed "
                "source_ip=10.10.60.20; records must be correlated."
            ),
            metadata={"evidence_ids": "IND2-N20"},
            rrf_score=0.009,
            dense_rank=2,
            sparse_rank=2,
        ),
        RetrievedChunk(
            chunk_id="IND2-normal-eng",
            document=(
                "Normal ENG traffic establishes source_ip=10.10.60.20 "
                "over IEC-104 TCP/2404."
            ),
            metadata={"evidence_ids": "IND2-N02"},
            rrf_score=0.008,
            dense_rank=3,
            sparse_rank=3,
        ),
    ]
    raw = TutorResponse(
        answer="兩者看起來相似。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N20"],
        next_hint="請比較證據。",
    )

    refined = refine_student_response(
        (
            "正常 ENG 維護與 Kali via ENG Relay 成功時，Server 端可能有什麼"
            "相同現象？那要額外比對哪些證據，才能區分兩者？"
        ),
        raw,
        retrieved,
        response_mode="explain",
        scenario="industroyer2",
    )

    assert "Kali action" in refined.answer
    assert "ENG Relay" in refined.answer
    assert "Server command" in refined.answer
    assert "正常 ENG 維護" in refined.answer
    assert "source_ip=10.10.60.20" in refined.answer
    assert "封包不是由 Kali 直接送到" not in refined.answer


def test_industroyer2_relay_path_and_awareness_stay_evidence_bounded() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="IND2-relay-summary",
            document=(
                "Kali requested a command through ENG Relay. ENG01 sent the "
                "IEC-104 command to OT-SERVER01 and must be correlated with "
                "Kali logs."
            ),
            metadata={"evidence_ids": "IND2-N20"},
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        ),
        RetrievedChunk(
            chunk_id="IND2-relay-send",
            document=(
                "accepted=True iec104_sent=True "
                "outcome=eng_relay_successful_iec104_send"
            ),
            metadata={"evidence_ids": "IND2-N24"},
            rrf_score=0.009,
            dense_rank=2,
            sparse_rank=2,
        ),
    ]
    raw = TutorResponse(
        answer="ENG 不會知道 Kali 的操作。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N20"],
        next_hint="請看 Relay。",
    )

    path = refine_student_response(
        "Kali via-ENG Relay 的封包會經由 ENG01 發送，還是 Kali 直接送到 OT-SERVER01？",
        raw,
        retrieved,
        response_mode="explain",
        scenario="industroyer2",
    )
    awareness = refine_student_response(
        "如果 Kali 接入 ENG，以 ENG 名義改 Server，ENG 會知道嗎？",
        raw,
        retrieved,
        response_mode="explain",
        scenario="industroyer2",
    )

    assert "不是由 Kali 直接送到" in path.answer
    assert "ENG01 實際送出 IEC-104" in path.answer
    assert path.evidence_ids == ["IND2-N20", "IND2-N24"]
    assert "ENG 人員是否看見、理解或同意" in awareness.answer
    assert "無法確認 ENG 人員是否知情" in awareness.unknowns[0]
    assert "ENG不會知道" not in awareness.answer
    assert awareness.evidence_ids == ["IND2-N20", "IND2-N24"]


def test_industroyer2_caddywiper_state_does_not_require_relay_lockout_claim() -> None:
    response = TutorResponse(
        answer=(
            "Safe CaddyWiper 模擬 ENG01 的 engineering console、backup、"
            "IOA mapping 與 restore 都不可用，且 dummy_files_wiped=true。"
        ),
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N16"],
        next_hint="確認這是安全模擬狀態。",
    )

    validate_question_alignment(
        "Safe CaddyWiper 在 ind2_vE_003 對 ENG01 的模擬狀態是什麼？",
        response,
        _industroyer2_retrieved(),
        scenario="industroyer2",
    )


def test_industroyer2_direct_question_does_not_confuse_normal_eng() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="IND2-ve-002-summary",
            document=(
                "ind2_vE_002: Kali sent a direct Industroyer2-like IEC-104 "
                "payload to OT-SERVER01. Server observed source_ip=10.10.60.10 "
                "over TCP/2404 and OT state changed to service disruption."
            ),
            metadata={"evidence_ids": "IND2-N08"},
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        ),
        RetrievedChunk(
            chunk_id="IND2-ve-002-comparison",
            document=(
                "Direct attack is distinguishable at Server/PCAP by "
                "source_ip=10.10.60.10 sending IEC-104 to TCP/2404."
            ),
            metadata={"evidence_ids": "IND2-N09"},
            rrf_score=0.009,
            dense_rank=2,
            sparse_rank=2,
        ),
    ]
    raw = TutorResponse(
        answer="正常 ENG 維護不會有這樣的紀錄。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N08"],
        next_hint="比較來源。",
    )

    refined = refine_student_response(
        "ind2_vE_002 中，OT-SERVER01 為什麼能把 Kali direct IEC-104 操作與正常 ENG 維護區分開？",
        raw,
        retrieved,
        response_mode="explain",
        scenario="industroyer2",
    )

    assert "source_ip=10.10.60.10" in refined.answer
    assert "正常 ENG 維護不會" not in refined.answer
    assert refined.unknowns == []
    assert refined.evidence_ids == ["IND2-N08", "IND2-N09"]


def test_industroyer2_clock_question_adds_relevant_boundary() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="IND2-ve-002-data-quality",
            document=(
                "Possible cross-host clock offset detected. Preserve original "
                "timestamps and do not infer causality from absolute time alone."
            ),
            metadata={"evidence_ids": "IND2-N12"},
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    raw = TutorResponse(
        answer="不能直接判斷。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["IND2-N12"],
        next_hint="看時間。",
    )

    refined = refine_student_response(
        "ind2_vE_002 的跨主機時間戳可以直接用來判斷因果順序嗎？",
        raw,
        retrieved,
        response_mode="explain",
        scenario="industroyer2",
    )

    assert "clock offset" in refined.answer
    assert "無法只用跨主機絕對時間" in refined.unknowns[0]
    assert refined.evidence_ids == ["IND2-N12"]


def test_prompt_contains_only_retrieved_evidence_whitelist() -> None:
    messages = build_messages("哪台主機執行？", _retrieved())
    combined = "\n".join(message["content"] for message in messages)

    assert "RYUK-L02" in combined


def test_response_validation_repairs_exact_duplicate_list_values() -> None:
    response = validate_tutor_response(
        {
            "answer": "ENG01 是實際送出端。",
            "known_facts": [
                "ENG01 實際送出 IEC-104。",
                " ENG01 實際送出 IEC-104。 ",
            ],
            "reasonable_inferences": [],
            "unknowns": [],
            "evidence_ids": ["IND2-N24", "IND2-N24"],
            "next_hint": "請查看 Relay 紀錄。",
        },
        allowed_evidence_ids=["IND2-N24"],
    )

    assert response.known_facts == ["ENG01 實際送出 IEC-104。"]
    assert response.evidence_ids == ["IND2-N24"]


def test_conversation_context_is_explicitly_not_event_evidence() -> None:
    messages = build_messages(
        "那個主機是什麼角色？",
        _retrieved(),
        conversation_context=(
            "上一輪學生問題：USER01 做了什麼？\n"
            "上一輪回覆摘要：USER01 是執行端。"
        ),
    )
    combined = "\n".join(message["content"] for message in messages)

    assert "CONVERSATION_CONTEXT" in combined
    assert "只用於理解「它、那個、前一項」等指涉" in combined
    assert "不是事件證據" in combined
    assert "USER01 是執行端" in combined
    assert "RYUK-NOT-RETRIEVED" not in combined
    assert "學生問題中的前提不是證據" in combined
    assert "通訊協定、連線權限、檔名和風險" in combined
    assert "實驗環境設計的已知事實" in combined
    assert "不要為了顯得嚴謹" in combined
    assert "不要用只有檔案數量的 impact" in combined
    assert "教學解釋區" in combined
    assert "不得把一般知識說成這次事件已發生的事實" in combined


def test_explain_prompt_requests_depth_without_expanding_scope() -> None:
    messages = build_messages(
        "哪台主機執行？",
        _retrieved(),
        response_mode="explain",
    )
    combined = "\n".join(message["content"] for message in messages)

    assert "answer 使用二至四句" in combined
    assert "known_facts 以二至四項具體事實為目標" in combined
    assert "哪類證據如何支持該結論" in combined
    assert "不得為了湊足數量而捏造" in combined
    assert "next_hint 仍只提出一個具體的教師追問" in combined
    assert "事件事實區" in combined
    assert "只能來自 RETRIEVED_CONTEXT" in combined


def test_question_focus_keeps_definition_and_workflow_intents_separate() -> None:
    definition = build_question_focus(
        "PowerShell 4104 和 Security 4688 的數字本身有意義嗎？"
    )
    workflow = build_question_focus(
        "要怎麼用虛擬機還原出安全模擬的情況？"
    )

    assert "名詞／編號定義題" in definition
    assert "事件 ID 各代表什麼" in definition
    assert "安全流程題" in workflow
    assert "不要只回答 artifacts 數量" in workflow


def test_response_schema_restricts_evidence_ids_to_retrieved_context() -> None:
    schema = build_response_schema(["RYUK-L02"])
    evidence_property = schema["properties"]["evidence_ids"]

    assert evidence_property["items"]["enum"] == ["RYUK-L02"]
    assert evidence_property["minItems"] == 1
    assert evidence_property["maxItems"] == 4

    empty_schema = build_response_schema([])
    assert empty_schema["properties"]["evidence_ids"]["maxItems"] == 0


def test_response_modes_preserve_tutor_response_fields() -> None:
    hint_schema = build_response_schema(
        ["RYUK-L02"],
        response_mode="hint",
    )
    explain_schema = build_response_schema(
        ["RYUK-L02"],
        response_mode="explain",
    )
    expected_fields = {
        "answer",
        "known_facts",
        "reasonable_inferences",
        "unknowns",
        "evidence_ids",
        "next_hint",
    }

    assert set(hint_schema["properties"]) == expected_fields
    assert set(explain_schema["properties"]) == expected_fields
    assert hint_schema["required"] == explain_schema["required"]
    assert "二至四句" in explain_schema["properties"]["answer"]["description"]


def test_rejects_unknown_response_mode() -> None:
    with pytest.raises(ValueError, match="response_mode"):
        build_messages(
            "哪台主機執行？",
            _retrieved(),
            response_mode="verbose",  # type: ignore[arg-type]
        )


def test_generate_validates_structured_response() -> None:
    provider = StubChatProvider(_valid_payload())

    response = generate_tutor_response(
        question="哪台主機執行？",
        retrieved=_retrieved(),
        chat_provider=provider,
    )

    assert response.evidence_ids == ["RYUK-L02"]
    assert response.known_facts == ["USER01 是執行端。"]
    assert allowed_evidence_ids(_retrieved()) == ["RYUK-L02"]


def test_rejects_controls_in_every_student_visible_text_field() -> None:
    payloads = []
    for field_name in (
        "answer",
        "known_facts",
        "reasonable_inferences",
        "unknowns",
        "evidence_ids",
        "next_hint",
    ):
        payload = _valid_payload()
        if field_name in {
            "known_facts",
            "reasonable_inferences",
            "unknowns",
            "evidence_ids",
        }:
            payload[field_name] = ["前\t後"]
        else:
            payload[field_name] = "前\t後"
        payloads.append((field_name, payload))

    for field_name, payload in payloads:
        response = TutorResponse.model_validate(payload)
        with pytest.raises(
            ValueError,
            match=rf"{field_name}.*U\+0009",
        ):
            validate_student_visible_text(response)


def test_control_character_caused_by_json_escape_retries_generation() -> None:
    invalid = _valid_payload()
    invalid["known_facts"] = ["帳號 RYUK\faculty01 出現在紀錄中。"]
    provider = SequenceChatProvider([invalid, _valid_payload()])

    response = generate_tutor_response(
        question="哪台主機執行？",
        retrieved=_retrieved(),
        chat_provider=provider,
    )

    assert provider.call_count == 2
    assert response.answer == _valid_payload()["answer"]


def test_explicit_retrieved_evidence_ids_are_all_required() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="RYUK-user01-endpoint-01",
            document=(
                "RYUK-U02 對應 PowerShell 4104；"
                "RYUK-U03 對應 Security 4688。"
            ),
            metadata={
                "stage": "execution",
                "source_type": "observed_endpoint_logs",
                "evidence_ids": "RYUK-U02,RYUK-U03",
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    missing_u03 = {
        "answer": "RYUK-U02 與 RYUK-U03 對應不同的端點紀錄。",
        "known_facts": ["RYUK-U02 對應 PowerShell 4104。"],
        "reasonable_inferences": [],
        "unknowns": [],
        "evidence_ids": ["RYUK-U02"],
        "next_hint": "兩種事件紀錄觀測的層次有何不同？",
    }
    complete = {
        **missing_u03,
        "known_facts": [
            "RYUK-U02 對應 PowerShell 4104。",
            "RYUK-U03 對應 Security 4688。",
        ],
        "evidence_ids": ["RYUK-U02", "RYUK-U03"],
    }
    provider = SequenceChatProvider([missing_u03, complete])

    with pytest.raises(ValueError, match="omitted evidence IDs"):
        validate_requested_evidence_citations(
            question="RYUK-U02 和 RYUK-U03 各能支持什麼？",
            response=TutorResponse.model_validate(missing_u03),
            evidence_whitelist=["RYUK-U02", "RYUK-U03"],
        )

    response = generate_tutor_response(
        question="RYUK-U02 和 RYUK-U03 各能支持什麼？",
        retrieved=retrieved,
        chat_provider=provider,
        response_mode="explain",
    )

    # This well-known evidence comparison is normalized deterministically
    # after retrieval, so it does not spend another Llama call merely to add
    # the already retrieved ID.  The validator above remains strict for all
    # other response paths.
    assert provider.call_count == 1
    assert response.evidence_ids == ["RYUK-U02", "RYUK-U03"]


def test_invalid_or_nonretrieved_question_id_is_not_forced() -> None:
    assert explicitly_requested_evidence_ids(
        "請比較 RYUK-X99 與 RYUK-U02-extra。",
        ["RYUK-U02"],
    ) == []
    response = TutorResponse.model_validate(_valid_payload())

    validate_requested_evidence_citations(
        question="RYUK-X99 能支持什麼？",
        response=response,
        evidence_whitelist=["RYUK-L02"],
    )


def test_restore_unknown_contradicting_direct_evidence_retries() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="RYUK-restore-01",
            document=(
                "復原腳本未修改原始 .RYK，而是把復原副本寫入 "
                "restored_from_v4。"
            ),
            metadata={
                "stage": "restore",
                "source_type": "observed_restore",
                "evidence_ids": "RYUK-L05",
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    contradictory = {
        "answer": "目前證據無法確認復原腳本是否修改原始 .RYK 檔案。",
        "known_facts": ["復原腳本未修改原始 .RYK 檔案。"],
        "reasonable_inferences": [],
        "unknowns": ["復原腳本是否修改原始 .RYK 檔案。"],
        "evidence_ids": ["RYUK-L05"],
        "next_hint": "復原輸出位置與來源檔案有何差異？",
    }
    corrected = {
        **contradictory,
        "answer": (
            "現有復原紀錄直接指出腳本沒有修改原始 .RYK，"
            "而是另外寫出復原副本。"
        ),
        "unknowns": [],
    }
    provider = SequenceChatProvider([contradictory, corrected])

    response = generate_tutor_response(
        question="現有證據能否證明復原腳本沒有修改原始 .RYK 檔案？",
        retrieved=retrieved,
        chat_provider=provider,
        response_mode="explain",
    )

    assert provider.call_count == 2
    assert "沒有修改原始 .RYK" in response.answer
    assert response.unknowns == []


def _kali_design_retrieval() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id="RYUK-topology-01",
            document=(
                "實驗環境設計上由防火牆規則阻擋 Kali 到 DC01 的直接 "
                "SMB 通訊；但缺少本次執行期間的逐封包紀錄。"
            ),
            metadata={
                "stage": "topology",
                "source_type": "lab_configuration",
                "evidence_ids": "RYUK-C01",
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]


def test_lab_design_rule_unknown_is_separated_from_runtime_unknown() -> None:
    retrieved = _kali_design_retrieval()
    invalid = TutorResponse.model_validate(
        {
            "answer": (
                "目前證據無法確認防火牆規則是否阻擋 Kali 直接 SMB。"
            ),
            "known_facts": [],
            "reasonable_inferences": [],
            "unknowns": ["防火牆規則是否阻擋 Kali 直接 SMB。"],
            "evidence_ids": ["RYUK-C01"],
            "next_hint": "還需要哪一類紀錄？",
        }
    )

    with pytest.raises(ValueError, match="Lab SMB blocking rule"):
        validate_response_semantics(invalid, retrieved)

    valid = TutorResponse.model_validate(
        {
            "answer": (
                "實驗環境設計上阻擋 Kali 到 DC01 的直接 SMB；"
                "目前沒有逐封包紀錄可指出本次丟棄的封包。"
            ),
            "known_facts": [
                "實驗環境設計上阻擋 Kali 到 DC01 的直接 SMB。"
            ],
            "reasonable_inferences": [],
            "unknowns": ["本次實際丟棄的封包內容。"],
            "evidence_ids": ["RYUK-C01"],
            "next_hint": "PCAP 或防火牆事件紀錄能補上哪一層觀測？",
        }
    )

    validate_response_semantics(valid, retrieved)


@pytest.mark.parametrize(
    ("document", "claim", "scenario", "message"),
    [
        (
            "PCAP proves the captured Lab request and does not prove device acceptance.",
            "The PCAP has no PLC response packet.",
            "frostygoop",
            "FrostyGoop PCAP evidence",
        ),
        (
            "Kali direct SMB is blocked by design, but Kali whether attempted it \u4ecd\u662f\u8b49\u64da\u4e0d\u8db3\u3002",
            "Kali never attempted SMB.",
            "ryuk",
            "Ryuk evidence lacks runtime telemetry",
        ),
        (
            "PCAP files are present, but tshark is not installed and did not decode IEC-104 fields.",
            "The PCAP contains no IEC-104 ASDU fields.",
            "industroyer2",
            "Industroyer2 PCAP fields were not decoded",
        ),
    ],
)
def test_rejects_absence_claim_when_context_only_reports_a_proof_gap(
    document: str,
    claim: str,
    scenario: str,
    message: str,
) -> None:
    response = TutorResponse(
        answer=claim,
        known_facts=[],
        reasonable_inferences=[],
        unknowns=["The available evidence cannot confirm the missing detail."],
        evidence_ids=["TEST-N01"],
        next_hint="Review what the collected evidence can prove.",
    )
    retrieved = [
        RetrievedChunk(
            chunk_id="test-boundary-01",
            document=document,
            metadata={"source_type": "data_quality", "evidence_ids": "TEST-N01"},
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]

    with pytest.raises(ValueError, match=message):
        validate_response_semantics(response, retrieved, scenario=scenario)


def test_refines_scheduled_task_false_premise_to_dry_run_boundary() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="RYUK-scheduled-task-dryrun-01",
            document=(
                "Scheduled task deployment is a dry run. No remote scheduled "
                "task was created and no payload was pushed to another host."
            ),
            metadata={"source_type": "simulation_log", "evidence_ids": "RYUK-L08"},
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    raw = TutorResponse(
        answer="The malicious scheduled task was named Scheduled task deployment.",
        known_facts=["A scheduled task was created."],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["RYUK-L08"],
        next_hint="Check the scheduled task name.",
    )

    refined = refine_student_response(
        "DC01 上的惡意排程工作已經建立，它的名稱是什麼？",
        raw,
        retrieved,
        scenario="ryuk",
        response_mode="explain",
    )

    assert "沒有建立遠端 scheduled task" in refined.answer
    assert any("dry run" in item for item in refined.known_facts)
    assert refined.unknowns == []
    assert refined.evidence_ids == ["RYUK-L08"]


def test_unknown_packet_with_specific_packet_inference_retries() -> None:
    retrieved = _kali_design_retrieval()
    contradictory = {
        "answer": (
            "目前證據無法確認 Kali 連 DC01 時實際丟棄哪一個封包。"
        ),
        "known_facts": [
            "實驗環境設計上阻擋 Kali 到 DC01 的直接 SMB。"
        ],
        "reasonable_inferences": [
            "防火牆實際丟棄的是 Kali 到 DC01 的 SMB 通訊封包。"
        ],
        "unknowns": ["本次實際丟棄的封包內容。"],
        "evidence_ids": ["RYUK-C01"],
        "next_hint": "逐封包紀錄能回答什麼？",
    }
    corrected = {
        **contradictory,
        "reasonable_inferences": [],
    }
    provider = SequenceChatProvider([contradictory, corrected])

    response = generate_tutor_response(
        question=(
            "Kali 嘗試連 DC01 時，防火牆實際丟棄的是哪一個封包？"
        ),
        retrieved=retrieved,
        chat_provider=provider,
        response_mode="explain",
    )

    assert provider.call_count == 2
    assert response.reasonable_inferences == []
    assert response.unknowns == ["本次實際丟棄的封包內容。"]


def test_refines_mechanical_language_and_generic_hint() -> None:
    payload = _valid_payload()
    payload["answer"] = "Lab 設計將 USER01 設為 execution context 的來源"
    payload["known_facts"] = [
        "檢索內容 記錄 execution context 將 Source Host=USER01"
    ]
    payload["unknowns"] = ["缺少 firewall log"]
    payload["next_hint"] = (
        "請比較 RYUK-L02 支持的已知事實，"
        "並找出哪些部分仍需要額外證據。"
    )
    response = validate_tutor_response(
        payload,
        allowed_evidence_ids=["RYUK-L02"],
    )

    refined = refine_student_response(
        question="哪台主機執行並修改 Share？",
        response=response,
        retrieved=_retrieved(),
    )

    combined = " ".join(
        [
            refined.answer,
            *refined.known_facts,
            *refined.unknowns,
            refined.next_hint,
        ]
    )
    assert "Lab" not in combined
    assert "檢索內容" not in combined
    assert "Source Host" not in combined
    assert "firewall log" not in combined
    assert "runtime network telemetry" not in combined
    assert "內容提供端" in refined.next_hint


def test_endpoint_question_removes_smb_snapshot_citation() -> None:
    endpoint_result = RetrievedChunk(
        chunk_id="RYUK-user01-endpoint-01",
        document="USER01 PowerShell 4104 與 Security 4688。",
        metadata={
            "stage": "execution",
            "source_type": "observed_endpoint_logs",
            "evidence_ids": "RYUK-U01,RYUK-U02,RYUK-U03,RYUK-N01",
            "title": "USER01 端點執行證據",
        },
        rrf_score=0.01,
        dense_rank=1,
        sparse_rank=1,
    )
    response = validate_tutor_response(
        {
            "answer": "目前有直接從 USER01 收到執行證據。",
            "known_facts": ["USER01 有 PowerShell 與 4688 紀錄。"],
            "reasonable_inferences": [],
            "unknowns": [],
            "evidence_ids": [
                "RYUK-U01",
                "RYUK-U02",
                "RYUK-U03",
                "RYUK-U04",
            ],
            "next_hint": (
                "請比較 RYUK-U01、RYUK-U02 支持的已知事實，"
                "並找出哪些部分仍需要額外證據。"
            ),
        },
        allowed_evidence_ids=[
            "RYUK-U01",
            "RYUK-U02",
            "RYUK-U03",
            "RYUK-U04",
            "RYUK-N01",
        ],
    )

    refined = refine_student_response(
        question="目前有直接從 USER01 收到執行證據嗎？",
        response=response,
        retrieved=[endpoint_result],
    )

    assert refined.evidence_ids == ["RYUK-U01", "RYUK-U02", "RYUK-U03"]
    assert "4104" in refined.next_hint
    assert "4688" in refined.next_hint


def test_contextual_hint_for_vulnerability_does_not_supply_an_answer() -> None:
    hint = build_contextual_hint("Kali 使用哪個漏洞？", _retrieved())

    assert "漏洞名稱或 CVE" in hint
    assert "RYUK-" not in hint


def test_normalizes_direct_smb_word_order() -> None:
    answer = normalize_student_text(
        "Lab 設計阻擋 Kali 透過 SMB 通訊與 DC01 主機"
    )
    unknown = normalize_student_text(
        "目前無法確認 Kali 是否嘗試直接修改 DC01 Share 的主機"
    )

    assert answer == "實驗環境設計阻擋 Kali 直接透過 SMB 與 DC01 通訊"
    assert unknown == "目前無法確認 Kali 是否曾嘗試直接透過 SMB 寫入 DC01 Share"


def test_normalizes_common_simplified_chinese_in_student_visible_text() -> None:
    normalized = normalize_student_text(
        "这是个安全模拟问题，请说明真实设备为何不能控制。"
    )

    assert normalized == "這是個安全模擬問題，請說明真實設備為何不能控制。"

    telemetry_unknown = normalize_student_text(
        "目前無法確認 Kali 是否嘗試與 DC01 進行 SMB 通信，"
        "且無法確認本次執行期間的 runtime network telemetry"
    )
    assert telemetry_unknown == (
        "目前無法確認 Kali 是否曾嘗試透過 SMB 與 DC01 通訊，"
        "且缺少本次執行期間的網路遙測"
    )


def test_role_question_uses_evidence_driven_natural_answer() -> None:
    retrieved = _retrieved()
    retrieved.append(
        RetrievedChunk(
            chunk_id="RYUK-topology-01",
            document=(
                "Kali 是 HTTP 下載來源；實驗環境阻擋 Kali 到 DC01 的直接 SMB。"
            ),
            metadata={
                "stage": "topology",
                "source_type": "lab_configuration",
                "evidence_ids": "RYUK-C01",
            },
            rrf_score=0.009,
            dense_rank=2,
            sparse_rank=2,
        )
    )
    response = validate_tutor_response(
        {
            "answer": (
                "實驗環境阻擋 Kali 透過 SMB 通訊直接修改 "
                "DC01 Share 的主機 USER01。"
            ),
            "known_facts": [
                "Kali 是 HTTP 下載來源。",
                "USER01 是執行端。",
                "Kali 到 DC01 的直接 SMB 被阻擋。",
            ],
            "reasonable_inferences": [
                "Kali 不是直接修改 DC01 Share 的主機 USER01。"
            ],
            "unknowns": [
                "是否有嘗試使用 SMB 進行 Kali 到 DC01 的連線。"
            ],
            "evidence_ids": ["RYUK-C01", "RYUK-L02"],
            "next_hint": "請比較角色。",
        },
        allowed_evidence_ids=["RYUK-C01", "RYUK-L02"],
    )

    refined = refine_student_response(
        question="為什麼 Kali 不是直接修改 DC01 Share 的主機？",
        response=response,
        retrieved=retrieved,
    )

    assert refined.answer == (
        "Kali 在本情境只負責透過 HTTP 提供腳本；"
        "USER01 是有端點紀錄支持的實際執行端。"
        "實驗環境設計阻擋 Kali 直接透過 SMB 存取 DC01。"
    )
    assert refined.reasonable_inferences == []
    assert refined.unknowns == [
        "目前無法確認 Kali 是否曾嘗試直接透過 SMB 連線 DC01。"
    ]


def test_explain_mode_expands_role_answer_from_same_evidence() -> None:
    retrieved = _retrieved()
    retrieved.append(
        RetrievedChunk(
            chunk_id="RYUK-topology-01",
            document=(
                "Kali 是 HTTP 下載來源；實驗環境阻擋 Kali 到 DC01 的直接 SMB。"
            ),
            metadata={
                "stage": "topology",
                "source_type": "lab_configuration",
                "evidence_ids": "RYUK-C01",
            },
            rrf_score=0.009,
            dense_rank=2,
            sparse_rank=2,
        )
    )
    response = validate_tutor_response(
        {
            "answer": "USER01 是執行端。",
            "known_facts": ["USER01 是執行端。"],
            "reasonable_inferences": [],
            "unknowns": [],
            "evidence_ids": ["RYUK-C01", "RYUK-L02"],
            "next_hint": "請比較角色。",
        },
        allowed_evidence_ids=["RYUK-C01", "RYUK-L02"],
    )

    refined = refine_student_response(
        question="為什麼 Kali 不是直接修改 DC01 Share 的主機？",
        response=response,
        retrieved=retrieved,
        response_mode="explain",
    )

    assert refined.answer.count("。") == 3
    assert "HTTP 提供紀錄支持" in refined.answer
    assert "端點紀錄則直接支持" in refined.answer
    assert "兩類證據代表不同角色" in refined.answer
    assert refined.evidence_ids == ["RYUK-C01", "RYUK-L02"]


def test_generate_passes_explain_mode_to_prompt_and_schema() -> None:
    provider = StubChatProvider(_valid_payload())

    response = generate_tutor_response(
        question="USER01 有哪些執行證據？",
        retrieved=_retrieved(),
        chat_provider=provider,
        response_mode="explain",
    )

    combined = "\n".join(message["content"] for message in provider.messages)
    assert "answer 使用二至四句" in combined
    assert response.model_dump().keys() == _valid_payload().keys()


def test_direct_kali_role_question_finishes_after_one_model_call() -> None:
    retrieved = _retrieved()
    retrieved.append(
        RetrievedChunk(
            chunk_id="RYUK-topology-01",
            document=(
                "Kali 是 HTTP 下載來源；Kali 到 DC01 的直接 SMB 在設計上"
                "被阻擋，但本次是否曾嘗試仍是證據不足。"
            ),
            metadata={
                "stage": "topology",
                "source_type": "lab_configuration",
                "evidence_ids": "RYUK-C01",
            },
            rrf_score=0.009,
            dense_rank=2,
            sparse_rank=2,
        )
    )
    incomplete = _valid_payload()
    incomplete["answer"] = "DC01 是 Share 所在端。"
    incomplete["known_facts"] = ["DC01 提供 Share。"]
    incomplete["unknowns"] = []
    provider = SequenceChatProvider([incomplete])

    response = generate_tutor_response(
        question="為什麼 Kali 不是直接修改 DC01 Share 的主機？",
        retrieved=retrieved,
        chat_provider=provider,
    )

    assert provider.call_count == 1
    assert "USER01" in response.answer
    assert "HTTP" in response.answer
    assert "SMB" in response.answer
    assert response.unknowns


def test_false_premise_with_dot_ryk_is_corrected_from_safe_context() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="RYUK-impact-01",
            document=(
                "本次 Lab 產生 100 個 .RYK 安全模擬檔，內容使用 Caesar "
                "shift，不是真實 Ryuk 加密。"
            ),
            metadata={
                "stage": "impact",
                "source_type": "observed_log",
                "evidence_ids": "RYUK-L03,RYUK-L04",
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    response = validate_tutor_response(
        {
            "answer": "目前無法確認影響規模。",
            "known_facts": ["有 .RYK 檔案。"],
            "reasonable_inferences": [],
            "unknowns": ["影響規模。"],
            "evidence_ids": ["RYUK-L03"],
            "next_hint": "先確認檔案數。",
        },
        allowed_evidence_ids=["RYUK-L03", "RYUK-L04"],
    )

    refined = refine_student_response(
        question="真實事件總共有 100 個 .RYK，這代表影響規模對吧？",
        response=response,
        retrieved=retrieved,
    )

    assert "安全模擬" in refined.answer
    assert "100" in refined.answer
    assert refined.evidence_ids == ["RYUK-L03", "RYUK-L04"]


def test_rejects_domain_identity_absent_from_context() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="RYUK-user01-endpoint-01",
            document="使用者為 RYUK\\employee01。",
            metadata={
                "stage": "execution",
                "source_type": "observed_endpoint_logs",
                "evidence_ids": "RYUK-U01",
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    response = validate_tutor_response(
        {
            "answer": "USER01 有直接執行證據。",
            "known_facts": ["使用者為 RYUK\\faculty01。"],
            "reasonable_inferences": [],
            "unknowns": [],
            "evidence_ids": ["RYUK-U01"],
            "next_hint": "哪一份紀錄能確認帳號？",
        },
        allowed_evidence_ids=["RYUK-U01"],
    )

    with pytest.raises(ValueError, match="RYUK\\\\faculty01"):
        validate_context_identifiers(response, retrieved)


def test_repairs_punctuation_inside_known_domain_identity() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="RYUK-user01-endpoint-01",
            document="使用者為 RYUK\\employee01。",
            metadata={
                "stage": "execution",
                "source_type": "observed_endpoint_logs",
                "evidence_ids": "RYUK-U01",
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]

    repaired = repair_domain_identity_format(
        "記錄使用者 RYUK\\、employee01。",
        retrieved,
    )

    assert repaired == "記錄使用者 RYUK\\employee01。"


def test_role_attribution_retries_when_answer_omits_observed_host() -> None:
    incomplete = _valid_payload()
    incomplete["answer"] = "只知道 DC01 是 Share 所在端。"
    provider = SequenceChatProvider([incomplete, _valid_payload()])

    response = generate_tutor_response(
        question="哪台主機執行並修改 Share？",
        retrieved=_retrieved(),
        chat_provider=provider,
    )

    assert provider.call_count == 2
    assert "USER01" in response.answer


def test_role_attribution_rejects_tangential_impact_citation() -> None:
    payload = _valid_payload()
    payload["evidence_ids"] = ["RYUK-L02", "RYUK-L03"]
    payload["next_hint"] = "比較 RYUK-L02 與 RYUK-L03。"
    response = validate_tutor_response(
        payload,
        allowed_evidence_ids=["RYUK-L02", "RYUK-L03"],
    )

    with pytest.raises(ValueError, match="tangential evidence IDs"):
        validate_question_alignment(
            question="哪台主機執行並修改 Share？",
            response=response,
            retrieved=_retrieved(),
        )


def test_role_alignment_is_not_applied_to_unrelated_question() -> None:
    payload = _valid_payload()
    payload["answer"] = "目前證據無法確認漏洞。"
    response = validate_tutor_response(
        payload,
        allowed_evidence_ids=["RYUK-L02"],
    )

    validate_question_alignment(
        question="使用了哪個漏洞？",
        response=response,
        retrieved=_retrieved(),
    )


def test_role_comparison_requires_a_concrete_evidence_type() -> None:
    response = TutorResponse(
        answer="Kali 提供內容，USER01 執行，DC01 提供 Share。",
        known_facts=["三台主機有不同角色。"],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["RYUK-L02"],
        next_hint="哪類證據最直接？",
    )

    with pytest.raises(ValueError, match="concrete supporting evidence type"):
        validate_question_alignment(
            question="請比較 Kali、USER01 和 DC01 的角色。",
            response=response,
            retrieved=_retrieved(),
        )

    response.answer += "USER01 的端點紀錄直接支持執行角色。"
    validate_question_alignment(
        question="請比較 Kali、USER01 和 DC01 的角色。",
        response=response,
        retrieved=_retrieved(),
    )


def test_direct_kali_path_requires_runtime_unknown() -> None:
    retrieved = _retrieved()
    retrieved.append(
        RetrievedChunk(
            chunk_id="RYUK-topology-01",
            document=(
                "Kali 是 HTTP 下載來源；"
                "Kali 到 DC01 的直接 SMB 在設計上被阻擋，"
                "但本次是否曾嘗試仍是證據不足。"
            ),
            metadata={
                "stage": "topology",
                "source_type": "lab_configuration",
                "evidence_ids": "RYUK-C01",
            },
            rrf_score=0.009,
            dense_rank=2,
            sparse_rank=2,
        )
    )
    response = validate_tutor_response(
        _valid_payload(),
        allowed_evidence_ids=["RYUK-L02", "RYUK-C01"],
    )

    with pytest.raises(ValueError, match="runtime network telemetry"):
        validate_question_alignment(
            question="為什麼 Kali 不是直接修改 DC01 Share 的主機？",
            response=response,
            retrieved=retrieved,
        )

    response.answer = (
        "實驗環境阻擋 Kali 到 DC01 的直接 SMB；"
        "Kali 是 HTTP 來源，USER01 是實際執行端。"
    )
    response.known_facts.append("Kali 到 DC01 的直接 SMB 被阻擋。")
    response.known_facts.append("Kali 是檢索證據記錄的 HTTP 來源。")
    response.unknowns.append("缺少本次執行期間的 firewall log 或 PCAP。")
    validate_question_alignment(
        question="為什麼 Kali 不是直接修改 DC01 Share 的主機？",
        response=response,
        retrieved=retrieved,
    )


def test_repairs_missing_execution_citation_from_observed_host() -> None:
    retrieved = _retrieved()
    retrieved.append(
        RetrievedChunk(
            chunk_id="RYUK-topology-01",
            document="Kali 直接 SMB 仍是證據不足。",
            metadata={
                "stage": "topology",
                "source_type": "lab_configuration",
                "evidence_ids": "RYUK-C01",
            },
            rrf_score=0.009,
            dense_rank=2,
            sparse_rank=2,
        )
    )
    payload = _valid_payload()
    payload["answer"] = "Kali 是 HTTP 來源，USER01 是實際執行端。"
    payload["known_facts"].append("Kali 是檢索證據記錄的 HTTP 來源。")
    payload["unknowns"] = ["缺少本次執行期間的 firewall log 或 PCAP。"]
    payload["evidence_ids"] = ["RYUK-C01"]
    provider = SequenceChatProvider([payload])

    response = generate_tutor_response(
        question="為什麼 Kali 不是直接修改 DC01 Share 的主機？",
        retrieved=retrieved,
        chat_provider=provider,
    )

    hosts, evidence_ids = observed_execution_attribution(retrieved)
    assert hosts == ["USER01"]
    assert evidence_ids == ["RYUK-L02"]
    assert provider.call_count == 1
    assert response.evidence_ids == ["RYUK-C01", "RYUK-L02"]


def test_rejects_unknown_evidence_ids() -> None:
    payload = _valid_payload()
    payload["evidence_ids"] = ["RYUK-N99"]

    with pytest.raises(ValueError, match="outside retrieved context"):
        validate_tutor_response(
            payload,
            allowed_evidence_ids=["RYUK-L02"],
        )


def test_textual_nonretrieved_evidence_id_retries_generation() -> None:
    invalid = _valid_payload()
    invalid["answer"] = (
        "根據 RYUK-X99，USER01 是有直接觀測支持的執行端。"
    )
    corrected = _valid_payload()
    provider = SequenceChatProvider([invalid, corrected])

    with pytest.raises(ValueError, match="absent from retrieved context"):
        validate_response_evidence_mentions(
            TutorResponse.model_validate(invalid),
            ["RYUK-L02"],
        )

    response = generate_tutor_response(
        question="哪一台主機執行安全模擬腳本？",
        retrieved=_retrieved(),
        chat_provider=provider,
    )

    assert provider.call_count == 2
    assert "RYUK-X99" not in response.answer


def _safe_simulation_retrieval() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id="RYUK-impact-restore-safety-01",
            document=(
                "本次 Lab 以可逆 Caesar shift 產生 100 個 .RYK "
                "安全模擬檔，不是真實 Ryuk 加密；100 只代表本次 "
                "Lab 輸出，不是歷史事件統計。復原腳本只建立解碼"
                "副本，不能推論可解密真實 Ryuk。"
            ),
            metadata={
                "stage": "impact",
                "source_type": "simulation_overview",
                "evidence_ids": "RYUK-L03,RYUK-L05",
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]


@pytest.mark.parametrize(
    "unsafe_claim",
    [
        "本次 Lab 使用真實 Ryuk 惡意程式加密測試檔案。",
        "Caesar shift 就是真實 Ryuk 的加密演算法。",
        "這個復原腳本是真實 Ryuk 解密工具。",
        "100 個 .RYK 是歷史 Ryuk 事件的影響統計。",
    ],
)
def test_rejects_affirmative_real_ryuk_claims_in_safe_context(
    unsafe_claim: str,
) -> None:
    response = TutorResponse.model_validate(
        {
            "answer": unsafe_claim,
            "known_facts": [unsafe_claim],
            "reasonable_inferences": [],
            "unknowns": [],
            "evidence_ids": ["RYUK-L03"],
            "next_hint": "請確認證據描述的是實驗還是真實事件。",
        }
    )

    with pytest.raises(ValueError, match="safe simulation"):
        validate_response_semantics(response, _safe_simulation_retrieval())


@pytest.mark.parametrize(
    "safe_claim",
    [
        "本次 Lab 未使用真實 Ryuk 惡意程式。",
        "Caesar shift 不是真實 Ryuk 加密。",
        "這個復原腳本不能解密真實 Ryuk。",
        "100 個 .RYK 不能當成歷史 Ryuk 事件統計。",
    ],
)
def test_allows_explicit_safe_simulation_corrections(
    safe_claim: str,
) -> None:
    response = TutorResponse.model_validate(
        {
            "answer": safe_claim,
            "known_facts": [safe_claim],
            "reasonable_inferences": [],
            "unknowns": [],
            "evidence_ids": ["RYUK-L03"],
            "next_hint": "請區分 Lab 輸出與真實事件。",
        }
    )

    validate_response_semantics(response, _safe_simulation_retrieval())


def test_real_ryuk_misstatement_retries_generation() -> None:
    invalid = {
        "answer": "本次 Lab 使用真實 Ryuk 惡意程式加密檔案。",
        "known_facts": ["檔案由真實 Ryuk 惡意程式加密。"],
        "reasonable_inferences": [],
        "unknowns": [],
        "evidence_ids": ["RYUK-L03"],
        "next_hint": "請觀察加密後的檔案。",
    }
    corrected = {
        **invalid,
        "answer": "本次 Lab 只使用可逆 Caesar shift 安全模擬。",
        "known_facts": ["Caesar shift 不是真實 Ryuk 加密。"],
    }
    provider = SequenceChatProvider([invalid, corrected])

    response = generate_tutor_response(
        question="本次 impact 使用什麼內容轉換？",
        retrieved=_safe_simulation_retrieval(),
        chat_provider=provider,
    )

    assert provider.call_count == 2
    assert "安全模擬" in response.answer


@pytest.mark.parametrize(
    "dangerous_text",
    [
        "Stop-Service -Name VSS -Force",
        "vssadmin delete shadows /all /quiet",
        r"Remove-Item -Recurse -Force C:\\RyukShare",
    ],
)
def test_rejects_operational_destructive_commands(
    dangerous_text: str,
) -> None:
    payload = _valid_payload()
    payload["answer"] = dangerous_text

    with pytest.raises(ValueError, match="destructive command"):
        validate_operational_safety(TutorResponse.model_validate(payload))


def test_destructive_command_retries_but_forensic_negation_is_allowed() -> None:
    invalid = _valid_payload()
    invalid["next_hint"] = "Stop-Service -Name VSS -Force"
    corrected = _valid_payload()
    corrected["answer"] = (
        "證據明確記錄沒有停止服務，也沒有刪除 backup 或 shadow copy。"
    )
    corrected["known_facts"] = [
        "Lab 沒有停止服務，也沒有刪除備份或 shadow copy。"
    ]
    provider = SequenceChatProvider([invalid, corrected])

    validate_operational_safety(TutorResponse.model_validate(corrected))
    response = generate_tutor_response(
        question="服務與復原中斷在這次 Lab 真的發生了嗎？",
        retrieved=_retrieved(),
        chat_provider=provider,
    )

    assert provider.call_count == 2
    assert "沒有停止服務" in response.answer


def test_insufficient_evidence_requires_unknowns() -> None:
    payload = {
        "answer": "目前證據不足。",
        "known_facts": [],
        "reasonable_inferences": [],
        "unknowns": [],
        "evidence_ids": [],
        "next_hint": "還需要哪類記錄才能判斷？",
    }

    with pytest.raises(ValueError, match="must report unknowns"):
        validate_tutor_response(
            payload,
            allowed_evidence_ids=[],
            require_unknowns=True,
        )

    payload["unknowns"] = ["沒有可用證據能確認學生的主張。"]
    response = validate_tutor_response(
        payload,
        allowed_evidence_ids=[],
        require_unknowns=True,
    )
    assert response.unknowns


def test_repairs_hint_that_exposes_internal_retrieval_labels() -> None:
    invalid = _valid_payload()
    invalid["next_hint"] = "請比較檢索結果 1 與檢索結果 2。"
    provider = SequenceChatProvider([invalid])

    response = generate_tutor_response(
        question="哪台主機執行？",
        retrieved=_retrieved(),
        chat_provider=provider,
    )

    assert provider.call_count == 1
    assert "檢索結果" not in response.next_hint
    assert "內容提供端" in response.next_hint


def test_repairs_internal_rank_labels_in_known_facts() -> None:
    invalid = _valid_payload()
    invalid["known_facts"] = ["檢索結果 1 顯示 USER01 是執行端。"]
    provider = SequenceChatProvider([invalid])

    response = generate_tutor_response(
        question="哪台主機執行？",
        retrieved=_retrieved(),
        chat_provider=provider,
    )

    assert provider.call_count == 1
    assert response.known_facts == ["USER01 是執行端。"]


def test_hint_repair_does_not_bypass_role_alignment() -> None:
    invalid = _valid_payload()
    invalid["answer"] = "只知道 DC01 是 Share 所在端。"
    invalid["next_hint"] = "請比較檢索結果 1 與檢索結果 2。"
    provider = SequenceChatProvider([invalid, _valid_payload()])

    response = generate_tutor_response(
        question="哪台主機執行並修改 Share？",
        retrieved=_retrieved(),
        chat_provider=provider,
    )

    assert provider.call_count == 2
    assert "USER01" in response.answer


def test_retries_invalid_evidence_then_accepts_corrected_response() -> None:
    invalid = _valid_payload()
    invalid["evidence_ids"] = ["RYUK-N99"]
    provider = SequenceChatProvider([invalid, _valid_payload()])

    response = generate_tutor_response(
        question="哪台主機執行？",
        retrieved=_retrieved(),
        chat_provider=provider,
    )

    assert provider.call_count == 2
    assert response.evidence_ids == ["RYUK-L02"]


def test_rejects_internal_retrieval_labels_after_retry_limit() -> None:
    invalid = _valid_payload()
    invalid["next_hint"] = "請查看 RRF=0.01 的結果。"

    with pytest.raises(ValueError, match="internal retrieval labels"):
        validate_tutor_response(
            invalid,
            allowed_evidence_ids=["RYUK-L02"],
        )


def test_ollama_chat_request_sets_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class StubResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "message": {
                    "content": (
                        '{"answer":"ok","known_facts":[],'
                        '"reasonable_inferences":[],"unknowns":[],'
                        '"evidence_ids":[],"next_hint":"next"}'
                    )
                },
                "done_reason": "stop",
                "prompt_eval_count": 321,
                "eval_count": 87,
                "total_duration": 12_000_000_000,
                "load_duration": 2_000_000_000,
                "prompt_eval_duration": 3_000_000_000,
                "eval_duration": 7_000_000_000,
            }

    def fake_post(*_args: Any, **kwargs: Any) -> StubResponse:
        captured.update(kwargs)
        return StubResponse()

    monkeypatch.setattr("app.generation.requests.post", fake_post)
    client = OllamaChatClient(
        base_url="http://127.0.0.1:11434",
        model="test-model",
        keep_alive="20m",
    )

    client.generate_json([], {"type": "object"})

    assert captured["json"]["keep_alive"] == "20m"
    assert client.metrics_history[0].done_reason == "stop"
    assert client.metrics_history[0].prompt_eval_count == 321
    assert client.metrics_history[0].eval_count == 87
    assert client.metrics_history[0].load_duration_seconds == 2.0
    assert client.metrics_history[0].output_tokens_per_second == pytest.approx(
        87 / 7
    )
    assert client.metrics_history[0].output_may_be_truncated is False


def test_refines_simplified_credential_false_premise_naturally() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="RYUK-credential-assumption-01",
            document=(
                "Credential exposure log 將 RYUK\\employee01 標為 Demo "
                "assumption，並否定真實 credential theft、password dumping、"
                "LSASS access 與 browser password collection。"
            ),
            metadata={
                "source_type": "simulation_log",
                "evidence_ids": "RYUK-L07",
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    raw = TutorResponse(
        answer="目前无法确认。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=["是否窃取了 employee01 的密码。"],
        evidence_ids=["RYUK-L07"],
        next_hint="請比較證據。",
    )

    refined = refine_student_response(
        "这次是不是已经真正窃取了 employee01 的密码？",
        raw,
        retrieved,
        response_mode="explain",
    )

    assert "Demo assumption" in refined.answer
    assert "不支持" in refined.answer
    assert "窃" not in "\n".join(
        [refined.answer, *refined.unknowns, refined.next_hint]
    )
    assert refined.evidence_ids == ["RYUK-L07"]
    assert "安全否定" in refined.next_hint


def test_refines_sensor01_collection_false_attribution() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="RYUK-collection-01",
            document=(
                "SENSOR01 負責收集、驗證與正規化證據，不是 delivery、"
                "execution、impact 或 detection 主機。"
            ),
            metadata={
                "source_type": "collection_metadata",
                "evidence_ids": "RYUK-L10,RYUK-U01,RYUK-U02,RYUK-U03",
            },
            rrf_score=0.02,
            dense_rank=1,
            sparse_rank=1,
        ),
        RetrievedChunk(
            chunk_id="RYUK-user01-endpoint-01",
            document=(
                "USER01 端點紀錄支持 USER01 執行腳本，DC01 RyukShare "
                "是安全模擬目標。"
            ),
            metadata={
                "source_type": "observed_endpoint_logs",
                "evidence_ids": "RYUK-U01,RYUK-U02,RYUK-U03",
            },
            rrf_score=0.01,
            dense_rank=2,
            sparse_rank=2,
        ),
    ]
    raw = TutorResponse(
        answer="SENSOR01 產生 JSON，所以它修改了 DC01 檔案。",
        known_facts=["SENSOR01 修改了 DC01。"],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["RYUK-L10"],
        next_hint="請比較檢索內容。",
    )

    refined = refine_student_response(
        "JSON 是 SENSOR01 產生的，所以 DC01 檔案也是 SENSOR01 修改的。",
        raw,
        retrieved,
        response_mode="explain",
    )

    assert "歸因前提不成立" in refined.answer
    assert "不能因此推成" in refined.answer
    assert "USER01 是執行端" in refined.answer
    assert refined.reasonable_inferences == []
    assert any("server-side audit" in item for item in refined.unknowns)
    assert refined.evidence_ids == [
        "RYUK-L10",
        "RYUK-U01",
        "RYUK-U02",
        "RYUK-U03",
    ]
    assert "三個待證明的命題" in refined.next_hint


def test_refines_4104_and_4688_as_different_evidence_layers() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="RYUK-user01-endpoint-01",
            document=(
                "USER01 的 PowerShell Operational 摘要含 4103 與 4104，"
                "腳本名稱、DC01 Share、impact folder 與 Caesar marker "
                "都有命中；Security 摘要另記錄 4688 process creation，"
                "並命中 PowerShell、employee01 與 command line。"
            ),
            metadata={
                "source_type": "observed_endpoint_logs",
                "evidence_ids": "RYUK-U02,RYUK-U03",
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    raw = TutorResponse(
        answer="兩個都只證明有 PowerShell。",
        known_facts=["有 PowerShell。"],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["RYUK-U02"],
        next_hint="請比較證據。",
    )

    refined = refine_student_response(
        "PowerShell 4104 和 Security 4688 各自能證明什麼？",
        raw,
        retrieved,
        response_mode="explain",
    )

    assert "內容層與程序建立層" in refined.answer
    assert "process-creation" in refined.known_facts[1]
    assert refined.evidence_ids == ["RYUK-U02", "RYUK-U03"]
    assert "script-block" in refined.next_hint


def test_distinguishes_event_id_definition_from_evidence_comparison() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="RYUK-user01-endpoint-01",
            document=(
                "USER01 的 PowerShell Operational 摘要含 4104，腳本與 "
                "DC01 Share 標記有命中；Security 摘要另記錄 4688 "
                "process creation、employee01 與 command line。"
            ),
            metadata={
                "source_type": "observed_endpoint_logs",
                "evidence_ids": "RYUK-U02,RYUK-U03",
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    raw = TutorResponse(
        answer="兩個編號都和 USER01 有關。",
        known_facts=["USER01 有事件紀錄。"],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["RYUK-U02"],
        next_hint="請比較證據。",
    )

    refined = refine_student_response(
        "PowerShell 4104 和 Security 4688 中的兩個數字本身有意義嗎？",
        raw,
        retrieved,
        response_mode="explain",
    )

    assert "Windows 事件 ID" in refined.answer
    assert "Script Block Logging" in refined.answer
    assert "建立了新程序" in refined.answer
    assert refined.evidence_ids == ["RYUK-U02", "RYUK-U03"]
    assert "哪一個確認腳本內容" in refined.next_hint


def test_refines_user01_share_question_without_moving_process_to_dc01() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="RYUK-execution-impact-01",
            document=(
                "Source Host=USER01；USER01 的 execution context、"
                "PowerShell 4104 與 Security 4688 摘要將腳本和 DC01 "
                "RyukShare 目標串在同一次執行期間。"
            ),
            metadata={
                "source_type": "observed_endpoint_logs",
                "evidence_ids": (
                    "RYUK-U01,RYUK-U02,RYUK-U03,RYUK-L02,RYUK-N01"
                ),
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    raw = TutorResponse(
        answer="目前無法確認是否跑了腳本。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=["RYUK-L02"],
        next_hint="請比較證據。",
    )

    refined = refine_student_response(
        "USER01 是不是在 DC01 的 RyukShare 跑了腳本？",
        raw,
        retrieved,
        response_mode="explain",
    )

    assert "腳本程序是在 USER01 執行" in refined.answer
    assert "不等於腳本程序在 DC01 上啟動" in refined.answer
    assert refined.unknowns == []
    assert refined.evidence_ids == [
        "RYUK-U01",
        "RYUK-U02",
        "RYUK-U03",
        "RYUK-L02",
    ]


def test_refines_empty_post_run_smb_snapshot_without_double_negative() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="RYUK-smb-access-01",
            document=(
                "USER01 的 SMB state 是在執行後才查詢，當時沒有 active "
                "connection 或 mapping；這不能否定先前存取。Execution、"
                "PowerShell 與 Discovery 紀錄將 USER01 和 DC01 "
                "RyukShare 串在本次模擬中。"
            ),
            metadata={
                "source_type": "evidence_synthesis",
                "evidence_ids": (
                    "RYUK-C01,RYUK-L02,RYUK-L06,RYUK-U02,RYUK-U04,RYUK-N01"
                ),
            },
            rrf_score=0.01,
            dense_rank=1,
            sparse_rank=1,
        )
    ]
    raw = TutorResponse(
        answer="目前無法確認空白 snapshot 是否能否否定先前存取。",
        known_facts=[],
        reasonable_inferences=["USER01 曾經存取 DC01。"],
        unknowns=["是否利用漏洞。"],
        evidence_ids=["RYUK-C01"],
        next_hint="請比較證據。",
    )

    refined = refine_student_response(
        "Does the empty post-run SMB snapshot prove that USER01 never "
        "accessed DC01?",
        raw,
        retrieved,
        response_mode="explain",
    )

    assert refined.answer.startswith("不能。")
    assert "是否能否" not in refined.answer
    assert refined.reasonable_inferences == []
    assert refined.evidence_ids[0] == "RYUK-U04"
    assert "server-side" in refined.unknowns[0]
