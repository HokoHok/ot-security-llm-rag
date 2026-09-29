from __future__ import annotations

from typing import Any

import pytest

from app.adequacy import (
    assess_evidence_adequacy,
    build_insufficient_evidence_response,
)
from app.retrieval import RetrievedChunk


def _chunk(
    document: str = "USER01 是安全模擬腳本的實際執行端。",
    *,
    evidence_ids: str = "RYUK-U03",
    dense_distance: float | None = 0.50,
    sparse_score: float | None = 4.0,
    rrf_score: float = 0.01,
) -> RetrievedChunk:
    metadata: dict[str, Any] = {
        "evidence_ids": evidence_ids,
        "title": "測試證據",
        "source_type": "observed_log",
    }
    # Store the new raw diagnostics in metadata so this test remains compatible
    # until RetrievedChunk gains first-class optional fields.
    if dense_distance is not None:
        metadata["dense_distance"] = dense_distance
    if sparse_score is not None:
        metadata["sparse_score"] = sparse_score
    return RetrievedChunk(
        chunk_id="RYUK-test-01",
        document=document,
        metadata=metadata,
        rrf_score=rrf_score,
        dense_rank=1,
        sparse_rank=1,
    )


def test_exact_evidence_id_hit_is_sufficient() -> None:
    result = assess_evidence_adequacy(
        "RYUK-U03 能支持什麼？",
        [_chunk(dense_distance=0.90, sparse_score=0.0)],
    )

    assert result.status == "sufficient"
    assert result.require_unknowns is False
    assert result.reasons == ("exact_evidence_id_hit",)


def test_missing_requested_evidence_id_is_insufficient() -> None:
    result = assess_evidence_adequacy(
        "RYUK-U99 能支持什麼？",
        [_chunk(evidence_ids="RYUK-U03")],
    )

    assert result.status == "insufficient"
    assert result.require_unknowns is True
    assert result.reasons == ("requested_evidence_id_not_found",)


def test_no_retrieval_results_are_insufficient() -> None:
    result = assess_evidence_adequacy("哪台主機執行腳本？", [])

    assert result.status == "insufficient"
    assert result.require_unknowns is True
    assert result.best_dense_distance is None
    assert result.best_sparse_score is None


@pytest.mark.parametrize(
    ("question", "expected_reason"),
    [
        (
            "DC01 使用哪一版 BIOS？",
            "requested_facet_not_supported:bios",
        ),
        (
            "這次事件造成多少營業損失和停機時間？",
            "requested_facet_not_supported:business_loss_or_downtime",
        ),
        (
            "employee01 的原始密碼內容是什麼？",
            "requested_facet_not_supported:password_value",
        ),
    ],
)
def test_explicitly_requested_missing_facets_are_insufficient(
    question: str,
    expected_reason: str,
) -> None:
    # Even a deceptively strong dense match cannot supply a missing field.
    result = assess_evidence_adequacy(
        question,
        [_chunk(dense_distance=0.30, sparse_score=8.0)],
    )

    assert result.status == "insufficient"
    assert result.require_unknowns is True
    assert expected_reason in result.reasons


@pytest.mark.parametrize(
    ("question", "document", "facet"),
    [
        (
            "Kali 利用了哪一個 CVE 進入 DC01？",
            "現有紀錄沒有指出使用漏洞，也沒有記載任何 CVE。",
            "cve_or_vulnerability",
        ),
        (
            "Kali 到 DC01 有逐封包或 PCAP 證據嗎？",
            "目前集合沒有逐連線 firewall log、PCAP 或封包紀錄。",
            "packet_or_firewall_telemetry",
        ),
        (
            "SMB session 是幾點建立，又維持多久？",
            "執行後才取得空白 SMB snapshot，無法確認先前 session。",
            "exact_time_or_session_duration",
        ),
        (
            "employee01 的原始密碼內容是什麼？",
            "現有安全模擬未提供密碼內容，也沒有 password collection。",
            "password_value",
        ),
        (
            "这次是不是已经真正窃取了 employee01 的密码？",
            "Credential exposure 是 Demo assumption，並否定 credential theft。",
            "credential_acquisition",
        ),
    ],
)
def test_explicit_limitations_support_a_bounded_unknown_answer(
    question: str,
    document: str,
    facet: str,
) -> None:
    result = assess_evidence_adequacy(question, [_chunk(document)])

    assert result.status == "sufficient"
    assert result.require_unknowns is True
    assert result.reasons == (f"explicit_context_limitation:{facet}",)


def test_strong_dense_match_protects_semantic_paraphrases() -> None:
    result = assess_evidence_adequacy(
        "真正動手的是下載站還是員工端？",
        [_chunk(dense_distance=0.56, sparse_score=0.0)],
    )

    assert result.status == "sufficient"
    assert result.require_unknowns is False
    assert result.reasons == ("strong_dense_semantic_match",)


def test_weak_raw_signals_are_insufficient_regardless_of_rrf() -> None:
    result = assess_evidence_adequacy(
        "量子糾纏是什麼？",
        [
            _chunk(
                dense_distance=0.76,
                sparse_score=0.0,
                rrf_score=9999.0,
            )
        ],
    )

    assert result.status == "insufficient"
    assert result.reasons == ("weak_dense_and_sparse_signals",)


def test_mixed_signals_are_uncertain_and_keep_best_raw_scores() -> None:
    result = assess_evidence_adequacy(
        "這個操作和事件有什麼關係？",
        [
            _chunk(dense_distance=0.68, sparse_score=1.2),
            _chunk(dense_distance=0.66, sparse_score=0.4),
        ],
    )

    assert result.status == "uncertain"
    assert result.require_unknowns is True
    assert result.best_dense_distance == pytest.approx(0.66)
    assert result.best_sparse_score == pytest.approx(1.2)


def test_insufficient_response_is_schema_valid_and_deterministic() -> None:
    first = build_insufficient_evidence_response()
    second = build_insufficient_evidence_response()

    assert first == second
    assert first.known_facts == []
    assert first.reasonable_inferences == []
    assert first.unknowns
    assert first.evidence_ids == []
    assert "無法確認" in first.answer


def test_industroyer2_insufficient_response_uses_scenario_profile() -> None:
    response = build_insufficient_evidence_response("industroyer2")

    assert "Industroyer2-like" in response.answer
    assert "IEC-104" in response.next_hint
    assert "Ryuk" not in response.model_dump_json()


def test_industroyer2_pcap_metadata_is_bounded_but_answerable() -> None:
    document = (
        "PCAP files are present, but tshark is not installed; normalizer "
        "recorded file hashes/sizes but did not decode IEC-104 fields."
    )

    result = assess_evidence_adequacy(
        "SENSOR01 沒有 tshark 時，PCAP 能提供哪些資訊，又不能證明什麼？",
        [_chunk(document)],
    )

    assert result.status == "sufficient"
    assert result.require_unknowns is True
    assert result.reasons == (
        "explicit_context_limitation:packet_or_firewall_telemetry",
    )


def test_frostygoop_normalized_pcap_is_answerable_with_an_unknown_boundary() -> None:
    document = (
        "FROSTY-N02. Modbus FC03 on "
        "localhost TCP/1502. Task JSON and PCAP match; capture does not prove "
        "device acceptance or physical impact."
    )

    result = assess_evidence_adequacy(
        "FC03 Task JSON and PCAP match prove physical impact?",
        [_chunk(document)],
    )

    assert result.status == "sufficient"
    assert result.require_unknowns is True
    assert result.reasons == (
        "explicit_context_limitation:packet_or_firewall_telemetry",
    )


def test_relay_path_is_not_mistaken_for_a_decoded_packet_field_question() -> None:
    document = (
        "Kali requested a command through ENG Relay. ENG01 sent the IEC-104 "
        "command to OT-SERVER01. accepted=True iec104_sent=True."
    )

    result = assess_evidence_adequacy(
        "Kali via-ENG Relay 的封包會經由 ENG01 發送，還是 Kali 直接送到 OT-SERVER01？",
        [_chunk(document)],
    )

    assert result.status == "sufficient"
    assert result.require_unknowns is False


def test_industroyer2_route_and_firewall_detail_requires_insufficient_response() -> None:
    result = assess_evidence_adequacy(
        "請畫出 Kali 到 OT-SERVER01 每一跳路由和防火牆放行紀錄。",
        [_chunk("Kali requested a command through ENG Relay.")],
    )

    assert result.status == "insufficient"
    assert result.require_unknowns is True
    assert result.reasons == (
        "requested_facet_not_supported:route_or_firewall_path",
    )

    response = build_insufficient_evidence_response(
        "industroyer2",
        "請畫出 Kali 到 OT-SERVER01 每一跳路由和防火牆放行紀錄。",
    )
    assert "不能重建完整網路路徑" in response.answer
    assert response.unknowns
