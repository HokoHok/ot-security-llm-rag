from pathlib import Path

from app.ingestion import (
    load_chunks,
    load_evidence_catalog,
    load_ground_truth,
    load_index_chunks,
    load_manual_test_dataset,
    load_robustness_dataset,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RYUK_DIR = PROJECT_ROOT / "scenarios" / "ryuk"
IND2_DIR = PROJECT_ROOT / "scenarios" / "industroyer2"
COMMON_DIR = PROJECT_ROOT / "scenarios" / "common"


def test_fixed_ryuk_chunks_match_schema() -> None:
    chunks = load_chunks(RYUK_DIR / "chunks.json")
    chunk_ids = {chunk.chunk_id for chunk in chunks}
    expected_teaching_topics = {
        "RYUK-smb-access-01",
        "RYUK-discovery-01",
        "RYUK-credential-assumption-01",
        "RYUK-scheduled-task-dryrun-01",
        "RYUK-service-recovery-dryrun-01",
        "RYUK-collection-quality-01",
        "RYUK-user01-endpoint-01",
        "RYUK-lab-workflow-01",
    }

    assert len(chunks) == 17
    assert {chunk.scenario for chunk in chunks} == {"ryuk"}
    assert expected_teaching_topics <= chunk_ids
    assert all(chunk.source_type != "ground_truth" for chunk in chunks)


def test_all_chunk_evidence_ids_exist_in_catalog() -> None:
    chunks = load_chunks(RYUK_DIR / "chunks.json")
    catalog = load_evidence_catalog(RYUK_DIR / "evidence_catalog.json")
    known_ids = {record.evidence_id for record in catalog.evidence}

    referenced_ids = {
        evidence_id
        for chunk in chunks
        for evidence_id in chunk.evidence_ids
    }

    assert referenced_ids <= known_ids


def test_ground_truth_matches_schema_and_evidence_catalog() -> None:
    ground_truth = load_ground_truth(RYUK_DIR / "ground_truth.json")
    catalog = load_evidence_catalog(RYUK_DIR / "evidence_catalog.json")
    known_ids = {record.evidence_id for record in catalog.evidence}
    referenced_ids = {
        evidence_id
        for case in ground_truth.cases
        for evidence_id in case.required_evidence_ids
    }

    assert ground_truth.scenario == "ryuk"
    assert len(ground_truth.cases) == 11
    assert referenced_ids <= known_ids
    assert all(case.expected_answer for case in ground_truth.cases)
    assert all(len(case.hint_levels) == 3 for case in ground_truth.cases)
    assert any(case.expected_unknowns for case in ground_truth.cases)


def test_industroyer2_ground_truth_matches_26_chunk_evidence_catalog() -> None:
    ground_truth = load_ground_truth(IND2_DIR / "ground_truth.json")
    catalog = load_evidence_catalog(IND2_DIR / "evidence_catalog.json")
    known_ids = {record.evidence_id for record in catalog.evidence}
    referenced_ids = {
        evidence_id
        for case in ground_truth.cases
        for evidence_id in case.required_evidence_ids
    }

    assert ground_truth.scenario == "industroyer2"
    assert len(ground_truth.cases) == 9
    assert {case.question_id for case in ground_truth.cases} == {
        f"IND2-Q{number:02d}" for number in range(1, 10)
    }
    assert referenced_ids <= known_ids
    assert all(case.expected_answer for case in ground_truth.cases)
    assert all(len(case.hint_levels) == 3 for case in ground_truth.cases)


def test_industroyer2_robustness_set_has_30_valid_natural_questions() -> None:
    dataset = load_robustness_dataset(
        IND2_DIR / "robustness_questions.json"
    )
    catalog = load_evidence_catalog(IND2_DIR / "evidence_catalog.json")
    known_ids = {record.evidence_id for record in catalog.evidence}
    referenced_ids = {
        evidence_id
        for question in dataset.questions
        for evidence_id in question.required_evidence_ids
    }

    assert dataset.scenario == "industroyer2"
    assert len(dataset.questions) == 30
    assert {question.question_id for question in dataset.questions} == {
        f"IND2-R{number:03d}" for number in range(1, 31)
    }
    assert referenced_ids <= known_ids
    assert {
        "paraphrase",
        "false_premise",
        "insufficient_evidence",
        "safety_boundary",
        "off_topic",
        "language_variant",
        "prompt_injection",
    } <= {question.category for question in dataset.questions}


def test_user01_endpoint_evidence_is_cataloged_and_present() -> None:
    catalog = load_evidence_catalog(RYUK_DIR / "evidence_catalog.json")
    records = {
        record.evidence_id: record
        for record in catalog.evidence
        if record.evidence_id.startswith("RYUK-U")
    }

    assert set(records) == {
        "RYUK-U01",
        "RYUK-U02",
        "RYUK-U03",
        "RYUK-U04",
    }
    for record in records.values():
        assert (PROJECT_ROOT / record.source_path).is_file()


def test_robustness_dataset_covers_required_question_classes() -> None:
    dataset = load_robustness_dataset(
        RYUK_DIR / "robustness_questions.json"
    )
    catalog = load_evidence_catalog(RYUK_DIR / "evidence_catalog.json")
    known_ids = {record.evidence_id for record in catalog.evidence}
    referenced_ids = {
        evidence_id
        for question in dataset.questions
        for evidence_id in question.required_evidence_ids
    }

    assert len(dataset.questions) == 50
    assert {question.category for question in dataset.questions} == {
        "paraphrase",
        "false_premise",
        "insufficient_evidence",
        "safety_boundary",
        "off_topic",
        "language_variant",
        "prompt_injection",
    }
    assert referenced_ids <= known_ids
    assert all(
        not question.required_evidence_ids
        for question in dataset.questions
        if question.expected_scope != "in_scope"
    )


def test_manual_dataset_has_25_cases_and_27_student_turns() -> None:
    dataset = load_manual_test_dataset(RYUK_DIR / "manual_questions.json")

    assert dataset.scenario == "ryuk"
    assert len(dataset.questions) == 25
    assert sum(len(case.turns) for case in dataset.questions) == 27
    assert len({case.question_id for case in dataset.questions}) == 25
    assert all(case.response_mode == "explain" for case in dataset.questions)
    assert {case.category for case in dataset.questions} >= {
        "event_analysis",
        "insufficient_evidence",
        "off_topic",
        "ambiguous",
        "false_premise",
        "safety_boundary",
        "language_variant",
        "prompt_injection",
        "multi_turn",
    }


def test_industroyer2_manual_dataset_has_ui_acceptance_coverage() -> None:
    dataset = load_manual_test_dataset(
        IND2_DIR / "manual_questions.json"
    )

    assert dataset.scenario == "industroyer2"
    assert len(dataset.questions) == 25
    assert sum(len(case.turns) for case in dataset.questions) == 28
    assert len({case.question_id for case in dataset.questions}) == 25
    assert {case.category for case in dataset.questions} >= {
        "event_analysis",
        "insufficient_evidence",
        "off_topic",
        "ambiguous",
        "false_premise",
        "safety_boundary",
        "language_variant",
        "prompt_injection",
        "multi_turn",
    }


def test_index_chunks_merge_scenario_evidence_with_common_concepts() -> None:
    chunks = load_index_chunks(
        IND2_DIR / "chunks.json",
        COMMON_DIR / "concept_chunks.json",
        "industroyer2",
    )
    catalog = load_evidence_catalog(COMMON_DIR / "evidence_catalog.json")

    assert len(chunks) == 31
    assert {chunk.scenario for chunk in chunks} == {"industroyer2"}
    assert {"OT-iec104-overview-01", "OT-lab-field-boundary-01"} <= {
        chunk.chunk_id for chunk in chunks
    }
    assert {record.evidence_id for record in catalog.evidence} == {
        "OT-G01",
        "OT-G02",
        "OT-G03",
        "OT-G04",
        "OT-G05",
    }
