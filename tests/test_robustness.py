from pathlib import Path
from typing import Any

from app.ingestion import load_robustness_dataset
from app.retrieval import RetrievedChunk
from app.robustness import evaluate_robustness
from app.schemas import TutorResponse
from app.scope import classify_question_scope, is_clearly_off_topic

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RYUK_DIR = PROJECT_ROOT / "scenarios" / "ryuk"
IND2_DIR = PROJECT_ROOT / "scenarios" / "industroyer2"


class StubRetriever:
    def __init__(self) -> None:
        self.call_count = 0

    def retrieve(
        self,
        question: str,
        scenario: str,
        top_k: int,
    ) -> list[RetrievedChunk]:
        self.call_count += 1
        return [
            RetrievedChunk(
                chunk_id="RYUK-user01-endpoint-01",
                document="USER01 Security 4688 process creation.",
                metadata={"evidence_ids": "RYUK-U03"},
                rrf_score=0.01,
                dense_rank=1,
                sparse_rank=1,
            )
        ]


def test_scope_gate_is_conservative() -> None:
    assert is_clearly_off_topic("明天台北天氣會下雨嗎？")
    assert not is_clearly_off_topic("DC01 事件當天下雨會影響證據嗎？")
    assert not is_clearly_off_topic("Kali 利用了哪一個 CVE？")
    assert classify_question_scope("量子糾纏是什麼？") == "off_topic"
    assert (
        classify_question_scope("Kali 今天氣溫多少？它在事件裡負責什麼？")
        == "ambiguous"
    )
    assert classify_question_scope("誰真的跑了那支 ps1？") == "in_scope"
    assert (
        classify_question_scope("USR01 在 DCO1 的 RuykSahre 做了什麼？")
        == "in_scope"
    )


def test_robustness_evaluator_skips_retrieval_for_off_topic() -> None:
    dataset = load_robustness_dataset(
        RYUK_DIR / "robustness_questions.json"
    )
    selected_ids = {"RYUK-R036", "RYUK-R043"}
    selected = [
        question
        for question in dataset.questions
        if question.question_id in selected_ids
    ]
    subset = dataset.model_copy(update={"questions": selected})
    retriever = StubRetriever()

    report = evaluate_robustness(
        dataset=subset,
        retriever=retriever,
        top_k=4,
    )

    assert report.overall_pass
    assert report.passed_cases == 2
    assert retriever.call_count == 1
    assert report.mean_reciprocal_rank == 1.0
    assert report.top1_hit_rate == 1.0


class ScenarioTrackingRetriever:
    def __init__(self) -> None:
        self.scenarios: list[str] = []

    def retrieve(
        self,
        question: str,
        scenario: str,
        top_k: int,
    ) -> list[RetrievedChunk]:
        self.scenarios.append(scenario)
        return [
            RetrievedChunk(
                chunk_id="IND2-comparison-source-01",
                document=(
                    "正常 ENG 與 Kali via ENG Relay 都可能讓 OT-SERVER01 看見 "
                    "source_ip=10.10.60.20。"
                ),
                metadata={"evidence_ids": "IND2-N02,IND2-N21"},
                rrf_score=0.01,
                dense_rank=1,
                sparse_rank=1,
            )
        ]


def test_industroyer2_robustness_uses_industroyer2_scope_and_collection() -> None:
    dataset = load_robustness_dataset(
        IND2_DIR / "robustness_questions.json"
    )
    selected = next(
        question
        for question in dataset.questions
        if question.question_id == "IND2-R003"
    )
    retriever = ScenarioTrackingRetriever()

    report = evaluate_robustness(
        dataset=dataset.model_copy(update={"questions": [selected]}),
        retriever=retriever,
        top_k=4,
    )

    assert report.overall_pass
    assert report.cases[0].detected_scope == "in_scope"
    assert retriever.scenarios == ["industroyer2"]


class StubRoleRetriever:
    def retrieve(
        self,
        question: str,
        scenario: str,
        top_k: int,
    ) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk_id="RYUK-role-test-01",
                document=(
                    "Kali 是 HTTP 下載來源；實驗環境阻擋 Kali 直接透過 "
                    "SMB 存取 DC01。Source Host=USER01，USER01 執行腳本。"
                ),
                metadata={
                    "source_type": "lab_configuration",
                    "evidence_ids": "RYUK-C01,RYUK-U02",
                },
                rrf_score=0.01,
                dense_rank=1,
                sparse_rank=1,
                dense_distance=0.5,
                sparse_score=5.0,
            )
        ]


class StubRoleChatProvider:
    def __init__(self) -> None:
        self.call_count = 0

    def generate_json(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.call_count += 1
        return TutorResponse(
            answer="Kali 提供 HTTP 下載，USER01 執行腳本。",
            known_facts=[
                "Kali 是 HTTP 下載來源。",
                "USER01 是實際執行端。",
                "Kali 到 DC01 的直接 SMB 路徑受阻擋。",
            ],
            reasonable_inferences=[],
            unknowns=[],
            evidence_ids=["RYUK-C01", "RYUK-U02"],
            next_hint="哪一項端點紀錄能支持 USER01 的執行角色？",
        ).model_dump()


def test_live_evaluator_does_not_leak_expected_unknowns_into_generation() -> None:
    dataset = load_robustness_dataset(
        RYUK_DIR / "robustness_questions.json"
    )
    selected = next(
        question
        for question in dataset.questions
        if question.question_id == "RYUK-R041"
    )
    subset = dataset.model_copy(update={"questions": [selected]})
    chat_provider = StubRoleChatProvider()

    report = evaluate_robustness(
        dataset=subset,
        retriever=StubRoleRetriever(),
        top_k=4,
        chat_provider=chat_provider,
    )

    case = report.cases[0]
    assert chat_provider.call_count == 1
    assert case.generation_error is None
    assert case.unknowns_pass is False
    assert case.answer_pass is False


class ForbiddenPolicyChatProvider:
    def generate_json(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        raise AssertionError("Policy-routed questions must not call Llama")


def test_policy_cases_skip_retrieval_and_llama_in_live_evaluator() -> None:
    dataset = load_robustness_dataset(
        RYUK_DIR / "robustness_questions.json"
    )
    selected_ids = {"RYUK-R034", "RYUK-R035", "RYUK-R047"}
    selected = [
        question
        for question in dataset.questions
        if question.question_id in selected_ids
    ]
    subset = dataset.model_copy(update={"questions": selected})
    retriever = StubRetriever()

    report = evaluate_robustness(
        dataset=subset,
        retriever=retriever,
        top_k=4,
        chat_provider=ForbiddenPolicyChatProvider(),
    )

    assert report.overall_pass
    assert retriever.call_count == 0
    assert {case.detected_scope for case in report.cases} == {
        "safety_boundary",
        "prompt_injection",
    }
    assert all(case.response is not None for case in report.cases)
