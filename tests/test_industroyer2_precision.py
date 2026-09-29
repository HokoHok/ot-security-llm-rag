from app.generation import refine_student_response
from app.retrieval import RetrievedChunk
from app.schemas import TutorResponse


def _chunk(
    chunk_id: str,
    document: str,
    evidence_id: str,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document=document,
        metadata={
            "stage": "analysis",
            "source_type": "normalized_analysis",
            "evidence_ids": evidence_id,
        },
        rrf_score=0.01,
        dense_rank=1,
        sparse_rank=1,
    )


def _response(evidence_ids: list[str]) -> TutorResponse:
    return TutorResponse(
        answer="模型的原始回答。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=[],
        evidence_ids=evidence_ids,
        next_hint="請繼續檢查證據。",
    )


def test_industroyer2_source_ip_pair_refinement_keeps_both_cases() -> None:
    retrieved = [
        _chunk(
            "IND2-direct-01",
            "Direct attack is distinguishable by source_ip=10.10.60.10.",
            "IND2-N09",
        ),
        _chunk(
            "IND2-normal-01",
            "Normal ENG traffic uses source_ip=10.10.60.20.",
            "IND2-N02",
        ),
        _chunk(
            "IND2-relay-01",
            "Via-ENG relay success can use source_ip=10.10.60.20 and must be correlated.",
            "IND2-N22",
        ),
    ]

    refined = refine_student_response(
        "若 Server 看到 source_ip=10.10.60.10，和 10.10.60.20 有什麼差異？",
        _response(["IND2-N02"]),
        retrieved,
        scenario="industroyer2",
    )

    assert "10.10.60.10" in refined.answer
    assert "10.10.60.20" in refined.answer
    assert "Kali direct" in refined.answer
    assert "正常 ENG" in refined.answer
    assert set(refined.evidence_ids) == {"IND2-N09", "IND2-N02", "IND2-N22"}


def test_industroyer2_lockout_false_send_stays_with_one_relay_attempt() -> None:
    retrieved = [
        _chunk(
            "IND2-lockout-01",
            "relay accepted=False iec104_sent=False outcome=blocked_by_safe_caddywiper",
            "IND2-N17",
        ),
        _chunk(
            "IND2-lockout-note-01",
            "After Safe CaddyWiper lockout, relay attempts may be rejected before IEC-104 is sent.",
            "IND2-N15",
        ),
    ]

    refined = refine_student_response(
        "Safe CaddyWiper-like lockout 後，iec104_sent=false 能證明什麼？不能證明什麼？",
        _response(["IND2-N15"]),
        retrieved,
        scenario="industroyer2",
    )

    assert "該筆嘗試未送出" in refined.answer
    assert "其他來源" in refined.answer
    assert refined.evidence_ids == ["IND2-N17"]
