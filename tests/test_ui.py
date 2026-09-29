import pytest

from app.retrieval import RetrievedChunk
from app.schemas import TutorResponse
from app.service import TutorSession
from app.ui import (
    ScenarioServiceManager,
    default_question_for_scenario,
    developer_trace_payload,
    format_tutor_response,
    response_mode_from_label,
    retrieval_trace_rows,
    scenario_from_label,
    scenario_label,
)


def test_formats_validated_response_for_students() -> None:
    response = TutorResponse(
        answer="先比較主機角色。",
        known_facts=["USER01 是執行端。"],
        reasonable_inferences=["Kali 不是直接 impact 主機。"],
        unknowns=[],
        evidence_ids=["RYUK-C01", "RYUK-L02"],
        next_hint="請比較 RYUK-C01 與 RYUK-L02。",
    )

    formatted = format_tutor_response(response)

    assert "已知事實" in formatted
    assert "合理推論" in formatted
    assert "• 無" in formatted
    assert "RYUK-C01" in formatted


def test_builds_developer_trace_rows() -> None:
    response = TutorResponse(
        answer="測試",
        known_facts=[],
        reasonable_inferences=[],
        unknowns=["測試資料不足。"],
        evidence_ids=[],
        next_hint="請補充證據。",
    )
    retrieved = RetrievedChunk(
        chunk_id="RYUK-topology-01",
        document="測試",
        metadata={
            "stage": "topology",
            "title": "通訊角色",
            "evidence_ids": "RYUK-C01",
        },
        rrf_score=0.016393,
        dense_rank=1,
        sparse_rank=1,
    )
    session = TutorSession(
        question="測試問題",
        scenario="ryuk",
        retrieved=[retrieved],
        response=response,
    )

    rows = retrieval_trace_rows(session)

    assert rows[0][1] == "RYUK-topology-01"
    assert rows[0][4] == "RYUK-C01"
    assert rows[0][-1] == "0.016393"

    payload = developer_trace_payload(session)
    assert payload["scenario"] == "ryuk"
    assert payload["tutor_response"]["answer"] == "測試"
    assert payload["runtime"]["cache_hit"] is False
    assert payload["runtime"]["ollama_calls"] == []
    assert payload["routing"]["question_scope"] == "in_scope"
    assert payload["routing"]["response_mode"] == "hint"
    assert payload["retrieval"][0]["chunk_id"] == "RYUK-topology-01"


def test_maps_chinese_response_mode_labels() -> None:
    assert response_mode_from_label("提示模式") == "hint"
    assert response_mode_from_label("詳解模式") == "explain"


def test_maps_scenario_labels_and_starter_questions() -> None:
    assert scenario_label("ryuk") == "Ryuk-like 安全模擬"
    assert scenario_from_label("Industroyer2-like 安全模擬") == "industroyer2"
    assert "Kali" in default_question_for_scenario("ryuk")
    assert "source_ip=10.10.60.20" in default_question_for_scenario(
        "industroyer2"
    )

    with pytest.raises(ValueError, match="Unknown scenario"):
        scenario_label("unknown")
    with pytest.raises(ValueError, match="Unknown scenario label"):
        scenario_from_label("Unknown scenario")


def test_scenario_service_manager_isolates_services_and_caches() -> None:
    created: list[str] = []
    services: dict[str, object] = {}

    def factory(scenario: str) -> object:
        created.append(scenario)
        service = object()
        services[scenario] = service
        return service

    manager = ScenarioServiceManager(service_factory=factory)  # type: ignore[arg-type]

    ryuk_service = manager.active_service()
    manager.select("industroyer2")
    ind2_service = manager.active_service()
    manager.select("ryuk")

    assert manager.active_service() is ryuk_service
    assert ind2_service is services["industroyer2"]
    assert created == ["ryuk", "industroyer2"]
