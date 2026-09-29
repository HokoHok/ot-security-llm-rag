from __future__ import annotations

import re
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.schemas import TutorResponse


_RESPONSE_LIST_FIELDS = (
    "known_facts",
    "reasonable_inferences",
    "unknowns",
    "evidence_ids",
)


def _deduplicate_response_lists(payload: Any) -> Any:
    """Remove exact duplicate model list items before strict validation.

    A repeated JSON array value carries no additional claim or evidence.  The
    final TutorResponse still enforces non-empty, unique lists; this narrow
    normalization merely keeps a small local model's harmless repetition from
    consuming every semantic-validation retry.
    """

    if not isinstance(payload, Mapping):
        return payload
    normalized = dict(payload)
    for field in _RESPONSE_LIST_FIELDS:
        values = normalized.get(field)
        if not isinstance(values, list):
            continue
        unique: list[Any] = []
        seen_text: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                unique.append(value)
                continue
            key = value.strip()
            if key in seen_text:
                continue
            seen_text.add(key)
            unique.append(value)
        normalized[field] = unique
    return normalized


def validate_tutor_response(
    payload: Any,
    allowed_evidence_ids: Iterable[str],
    require_unknowns: bool = False,
) -> TutorResponse:
    """Validate structure, evidence whitelist, and insufficient-data behavior."""

    try:
        response = TutorResponse.model_validate(
            _deduplicate_response_lists(payload)
        )
    except ValidationError as exc:
        raise ValueError(f"Tutor response schema validation failed: {exc}") from exc

    allowed = set(allowed_evidence_ids)
    cited = set(response.evidence_ids)
    invalid_ids = sorted(cited - allowed)
    if invalid_ids:
        raise ValueError(
            "Tutor response cited evidence IDs outside retrieved context: "
            + ", ".join(invalid_ids)
        )

    has_supported_claims = bool(
        response.known_facts or response.reasonable_inferences
    )
    if has_supported_claims and not response.evidence_ids:
        raise ValueError(
            "Tutor response contains facts or inferences without evidence IDs"
        )

    if require_unknowns and not response.unknowns:
        raise ValueError(
            "Tutor response must report unknowns when evidence is insufficient"
        )

    internal_trace_pattern = re.compile(
        r"檢索結果\s*\d+|RRF\s*=|dense\s*=|BM25\s*=",
        flags=re.IGNORECASE,
    )
    response_text = [
        response.answer,
        *response.known_facts,
        *response.reasonable_inferences,
        *response.unknowns,
        response.next_hint,
    ]
    if any(internal_trace_pattern.search(text) for text in response_text):
        raise ValueError(
            "Tutor response exposed internal retrieval labels"
        )

    return response
