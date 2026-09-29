from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.schemas import (
    EvidenceCatalog,
    GroundTruth,
    KnowledgeChunk,
    ManualTestDataset,
    RobustnessDataset,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"JSON file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: "
            f"{exc.msg}"
        ) from exc


def _validate(model: type[ModelT], value: Any, path: Path) -> ModelT:
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise ValueError(f"Schema validation failed for {path}: {exc}") from exc


def load_chunks(path: Path) -> list[KnowledgeChunk]:
    """Load chunks and reject malformed data or duplicate chunk IDs."""

    data = _read_json(path)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array of chunks in {path}")

    chunks = [
        _validate(KnowledgeChunk, item, path)
        for item in data
    ]
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError(f"Duplicate chunk_id found in {path}")
    return chunks


def load_index_chunks(
    scenario_path: Path,
    common_path: Path,
    scenario: str,
) -> list[KnowledgeChunk]:
    """Load one scenario's evidence plus shared, traceable concept chunks.

    Shared chunks are copied into each scenario collection with that scenario's
    metadata value.  This keeps Chroma collections isolated while allowing the
    same controlled OT reference material to serve both lessons.
    """

    normalized_scenario = scenario.strip().casefold()
    if not normalized_scenario:
        raise ValueError("scenario must not be empty")

    scenario_chunks = load_chunks(scenario_path)
    invalid_scenario_chunks = [
        chunk.chunk_id
        for chunk in scenario_chunks
        if chunk.scenario.casefold() != normalized_scenario
    ]
    if invalid_scenario_chunks:
        raise ValueError(
            "Scenario chunk metadata does not match requested scenario: "
            + ", ".join(invalid_scenario_chunks)
        )

    common_chunks = load_chunks(common_path)
    invalid_common_chunks = [
        chunk.chunk_id
        for chunk in common_chunks
        if chunk.scenario.casefold() != "common"
    ]
    if invalid_common_chunks:
        raise ValueError(
            "Common concept chunks must use scenario='common': "
            + ", ".join(invalid_common_chunks)
        )

    combined = [
        *scenario_chunks,
        *[
            chunk.model_copy(update={"scenario": normalized_scenario})
            for chunk in common_chunks
        ],
    ]
    chunk_ids = [chunk.chunk_id for chunk in combined]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("Duplicate chunk_id after combining scenario and common chunks")
    return combined


def load_evidence_catalog(path: Path) -> EvidenceCatalog:
    """Load the evidence catalog and reject duplicate evidence IDs."""

    catalog = _validate(EvidenceCatalog, _read_json(path), path)
    evidence_ids = [record.evidence_id for record in catalog.evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError(f"Duplicate evidence_id found in {path}")
    return catalog


def load_ground_truth(path: Path) -> GroundTruth:
    """Load instructor Ground Truth and reject duplicate question IDs."""

    ground_truth = _validate(GroundTruth, _read_json(path), path)
    question_ids = [case.question_id for case in ground_truth.cases]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError(f"Duplicate question_id found in {path}")
    return ground_truth


def load_robustness_dataset(path: Path) -> RobustnessDataset:
    """Load a robustness set and reject duplicate question IDs."""

    dataset = _validate(RobustnessDataset, _read_json(path), path)
    question_ids = [question.question_id for question in dataset.questions]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError(f"Duplicate robustness question_id found in {path}")
    return dataset


def load_manual_test_dataset(path: Path) -> ManualTestDataset:
    """Load the instructor-run live manual question set."""

    return _validate(ManualTestDataset, _read_json(path), path)
