from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeChunk(BaseModel):
    """One complete, retrievable unit of scenario knowledge."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(pattern=r"^[A-Z][A-Z0-9]*-[a-z0-9-]+$")
    scenario: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    keywords: list[str] = Field(min_length=1)

    @field_validator("evidence_ids", "keywords")
    @classmethod
    def require_unique_nonempty_values(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("list values must not be empty")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("list values must be unique")
        return cleaned


class EvidenceRecord(BaseModel):
    """Traceability metadata for one stable evidence identifier."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^[A-Z][A-Z0-9]*-[A-Z][0-9]{2,3}$")
    source_path: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    description: str = Field(min_length=1)


class EvidenceCatalog(BaseModel):
    """Versioned catalog that maps evidence IDs to source material."""

    model_config = ConfigDict(extra="forbid")

    catalog_version: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    evidence: list[EvidenceRecord] = Field(min_length=1)


class GroundTruthCase(BaseModel):
    """One instructor-only teaching and evaluation reference case."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^[A-Z][A-Z0-9]*-Q[0-9]{2,3}$")
    topic: str = Field(min_length=1)
    canonical_question: str = Field(min_length=1)
    expected_answer: str | None = Field(default=None, min_length=1)
    required_evidence_ids: list[str] = Field(min_length=1)
    expected_known_facts: list[str] = Field(min_length=1)
    acceptable_inferences: list[str]
    expected_unknowns: list[str]
    forbidden_claims: list[str] = Field(min_length=1)
    hint_levels: list[str] = Field(min_length=3, max_length=3)

    @field_validator(
        "required_evidence_ids",
        "expected_known_facts",
        "acceptable_inferences",
        "expected_unknowns",
        "forbidden_claims",
        "hint_levels",
    )
    @classmethod
    def require_unique_nonempty_list_values(
        cls,
        values: list[str],
    ) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("list values must not be empty")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("list values must be unique")
        return cleaned


class GroundTruth(BaseModel):
    """Instructor-only scenario reference that is never indexed wholesale."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    cases: list[GroundTruthCase] = Field(min_length=1)


class RobustnessQuestion(BaseModel):
    """One non-canonical question used to probe scenario RAG robustness."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^(?:RYUK|IND2|FROSTY)-R[0-9]{3}$")
    category: Literal[
        "paraphrase",
        "false_premise",
        "insufficient_evidence",
        "safety_boundary",
        "off_topic",
        "language_variant",
        "prompt_injection",
    ]
    question: str = Field(min_length=1)
    expected_scope: Literal[
        "in_scope",
        "off_topic",
        "ambiguous",
        "safety_boundary",
        "prompt_injection",
    ]
    expected_behavior: str = Field(min_length=1)
    required_evidence_ids: list[str]
    expected_unknowns: bool
    forbidden_claims: list[str]
    required_answer_terms: list[str] = Field(default_factory=list)
    reference_question_id: str | None = Field(
        default=None,
        pattern=r"^(?:RYUK|IND2|FROSTY)-Q[0-9]{2}$",
    )

    @field_validator(
        "required_evidence_ids",
        "forbidden_claims",
        "required_answer_terms",
    )
    @classmethod
    def require_unique_nonempty_list_values(
        cls,
        values: list[str],
    ) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("list values must not be empty")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("list values must be unique")
        return cleaned


class RobustnessDataset(BaseModel):
    """Instructor-only adversarial and paraphrase evaluation set."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    questions: list[RobustnessQuestion] = Field(min_length=30, max_length=50)


class ManualTestCase(BaseModel):
    """One human-review case; it intentionally contains no expected answer."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^MT-[0-9]{2}$")
    category: Literal[
        "normal",
        "event_analysis",
        "insufficient_evidence",
        "off_topic",
        "ambiguous",
        "false_premise",
        "safety_boundary",
        "language_variant",
        "prompt_injection",
        "conversation",
        "multi_turn",
    ]
    turns: list[str] = Field(min_length=1, max_length=2)
    response_mode: Literal["hint", "explain"] = "explain"

    @field_validator("turns")
    @classmethod
    def require_unique_nonempty_turns(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("turns must not contain empty questions")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("turns must be unique within a case")
        return cleaned


class ManualTestDataset(BaseModel):
    """Instructor-run live question set for later semantic review."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    questions: list[ManualTestCase] = Field(min_length=25, max_length=25)

    @field_validator("questions")
    @classmethod
    def require_unique_manual_question_ids(
        cls,
        values: list[ManualTestCase],
    ) -> list[ManualTestCase]:
        identifiers = [case.question_id for case in values]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("manual question IDs must be unique")
        return values


class TutorResponse(BaseModel):
    """Validated teaching response returned by the local chat model."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(
        min_length=1,
        description=(
            "一至兩句直接回答學生問題；沒有證據時必須明確說無法確認。"
        ),
    )
    known_facts: list[str] = Field(
        description="只列 RETRIEVED_CONTEXT 直接支持的事實。"
    )
    reasonable_inferences: list[str] = Field(
        description="只列由多項已知事實支持、但未直接觀測的推論。"
    )
    unknowns: list[str] = Field(
        description="列出與問題直接相關、但目前檢索證據無法確認的事項。"
    )
    evidence_ids: list[str] = Field(
        max_length=4,
        description=(
            "支持回答的 evidence_id；只能逐字選用允許清單，不得填空字串。"
        )
    )
    next_hint: str = Field(
        min_length=1,
        description="下一步查證方向；不公布完整答案或內部檢索排名。",
    )

    @field_validator("answer", "next_hint")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text must not be empty")
        return cleaned

    @field_validator(
        "known_facts",
        "reasonable_inferences",
        "unknowns",
        "evidence_ids",
    )
    @classmethod
    def require_unique_nonempty_list_values(
        cls,
        values: list[str],
    ) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("list values must not be empty")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("list values must be unique")
        return cleaned
