from pathlib import Path

from app.evaluation import (
    evaluate_answer,
    evaluate_ground_truth,
    evaluate_retrieval,
    find_forbidden_claim_hits,
    write_evaluation_report,
)
from app.retrieval import RetrievedChunk
from app.schemas import GroundTruth, GroundTruthCase, TutorResponse


def _case() -> GroundTruthCase:
    return GroundTruthCase(
        question_id="RYUK-Q99",
        topic="test_topic",
        canonical_question="Kali 使用哪個漏洞？",
        expected_answer="目前證據無法確認使用哪個漏洞。",
        required_evidence_ids=["RYUK-C01", "RYUK-L02"],
        expected_known_facts=["Kali 是 HTTP 來源。"],
        acceptable_inferences=["USER01 是較合理的執行端。"],
        expected_unknowns=["目前無法確認漏洞名稱。"],
        forbidden_claims=["Kali 利用 SMB 漏洞進入 DC01。"],
        hint_levels=["提示一", "提示二", "提示三"],
    )


def _retrieved() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id="RYUK-topology-01",
            document="Kali 是 HTTP 來源，沒有證據指出漏洞。",
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


def _response(answer: str, unknowns: list[str]) -> TutorResponse:
    return TutorResponse(
        answer=answer,
        known_facts=["Kali 是 HTTP 來源。"],
        reasonable_inferences=["USER01 是較合理的執行端。"],
        unknowns=unknowns,
        evidence_ids=["RYUK-C01"],
        next_hint="請檢查 RYUK-C01。",
    )


def test_retrieval_evaluation_reports_missing_required_evidence() -> None:
    result = evaluate_retrieval(_case(), _retrieved())

    assert not result.retrieval_pass
    assert result.retrieval_recall == 0.5
    assert result.missing_required_evidence_ids == ["RYUK-L02"]
    assert result.first_required_rank == 1
    assert result.reciprocal_rank == 1.0
    assert result.top1_required_hit is True
    assert result.evidence_precision == 1.0


def test_answer_evaluation_requires_relevant_unknowns() -> None:
    retrieval = evaluate_retrieval(_case(), _retrieved())
    result = evaluate_answer(
        _case(),
        retrieval,
        _response("目前證據無法確認漏洞。", []),
    )

    assert not result.answer_pass
    assert not result.unknowns_pass

    corrected = evaluate_answer(
        _case(),
        retrieval,
        _response(
            "目前證據無法確認漏洞。",
            ["沒有證據能確認使用哪個漏洞。"],
        ),
    )
    assert corrected.unknowns_pass
    assert corrected.answer_alignment_pass
    assert corrected.answer_pass

    missing_known_facts = TutorResponse(
        answer="目前證據無法確認漏洞。",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=["沒有證據能確認使用哪個漏洞。"],
        evidence_ids=["RYUK-C01"],
        next_hint="請檢查 RYUK-C01。",
    )
    incomplete = evaluate_answer(
        _case(),
        retrieval,
        missing_known_facts,
    )
    assert incomplete.known_fact_coverage == 0.0
    assert not incomplete.known_facts_pass
    assert not incomplete.answer_pass


def test_answer_evaluation_rejects_unexpected_uncertainty() -> None:
    case = _case().model_copy(
        update={
            "expected_answer": "USER01 已建立遠端排程工作。",
            "expected_unknowns": [],
        }
    )
    response = TutorResponse(
        answer="目前證據無法確認 USER01 是否建立遠端排程工作。",
        known_facts=["Kali 是 HTTP 來源。"],
        reasonable_inferences=[],
        unknowns=["是否建立遠端排程工作。"],
        evidence_ids=["RYUK-C01"],
        next_hint="請檢查 RYUK-C01。",
    )
    result = evaluate_answer(
        case,
        evaluate_retrieval(case, _retrieved()),
        response,
    )

    assert not result.answer_alignment_pass
    assert not result.unknowns_pass
    assert not result.answer_pass


def test_forbidden_claim_check_ignores_negation_but_flags_affirmation() -> None:
    negated = _response(
        "目前證據無法確認 Kali 利用 SMB 漏洞進入 DC01。",
        ["漏洞名稱不明。"],
    )
    assert not find_forbidden_claim_hits(_case().forbidden_claims, negated)

    affirmative = _response(
        "Kali 利用 SMB 漏洞進入 DC01。",
        ["其他細節不明。"],
    )
    hits = find_forbidden_claim_hits(
        _case().forbidden_claims,
        affirmative,
    )
    assert hits
    assert hits[0].generated_claim == affirmative.answer


def test_forbidden_claim_check_recognizes_industroyer2_negative_verbs() -> None:
    ticket_response = _response(
        "IEC-104 封包不攜帶 maintenance ticket。",
        [],
    )
    lockout_response = _response(
        "Relay 嘗試被拒絕且未送出 IEC-104 指令。",
        [],
    )

    assert not find_forbidden_claim_hits(
        ["maintenance ticket 會由 IEC-104 封包傳送給 OT-SERVER01。"],
        ticket_response,
    )
    assert not find_forbidden_claim_hits(
        ["CaddyWiper lockout 後 ENG01 仍成功送出新的 IEC-104 指令。"],
        lockout_response,
    )


def test_forbidden_claim_check_does_not_confuse_ip_baseline_with_attribution() -> None:
    response = TutorResponse(
        answer=(
            "不能。正常 ENG 維護與 Kali via ENG Relay 成功時，Server／PCAP "
            "都可能看到 source_ip=10.10.60.20；來源 IP 本身不足以判定最初發起者。"
        ),
        known_facts=[
            "正常 ENG baseline 在 Server／PCAP 視角使用 source_ip=10.10.60.20。"
        ],
        reasonable_inferences=[],
        unknowns=["僅靠 Server source IP 無法判定最初發起者。"],
        evidence_ids=["IND2-N02", "IND2-N21"],
        next_hint="比較正常 ENG 與 Relay case 的跨主機證據。",
    )

    assert not find_forbidden_claim_hits(
        ["source_ip=10.10.60.20 單獨可確定最初發起者是正常 ENG 維護。"],
        response,
    )


class StubRetriever:
    def retrieve(
        self,
        question: str,
        scenario: str,
        top_k: int,
    ) -> list[RetrievedChunk]:
        assert question
        assert scenario == "ryuk"
        assert top_k == 4
        return _retrieved()


def test_batch_report_is_serializable_and_writable(tmp_path: Path) -> None:
    ground_truth = GroundTruth(
        schema_version="1.0",
        scenario="ryuk",
        purpose="test",
        cases=[_case()],
    )
    progress: list[str] = []
    report = evaluate_ground_truth(
        ground_truth=ground_truth,
        retriever=StubRetriever(),
        top_k=4,
        progress=progress.append,
    )
    output = write_evaluation_report(report, tmp_path / "report.json")

    assert not report.overall_pass
    assert report.to_dict()["summary"]["failed_cases"] == 1
    assert output.read_text(encoding="utf-8").startswith("{")
    assert progress == [
        "[EVALUATE 1/1] RYUK-Q99 正在檢索…",
        "[EVALUATE 1/1] RYUK-Q99 檢索完成：FAIL",
    ]


def test_ground_truth_case_accepts_non_ryuk_scenario_prefix() -> None:
    case = _case().model_copy(update={"question_id": "IND2-Q01"})

    assert case.question_id == "IND2-Q01"
