from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.tasks import PreferredTimeOfDay, TaskPriority

PROMPT_VERSION_V1 = "ai-intake.task-draft.v1"
PROMPT_VERSION = "ai-intake.task-draft.v2"
SCHEMA_VERSION_V1: Literal["task-draft.schema.v1"] = "task-draft.schema.v1"
SCHEMA_VERSION: Literal["task-draft.schema.v2"] = "task-draft.schema.v2"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# The v1 contract remains importable while consumers migrate to v2.
class ProposedStepV1(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None
    duration_minutes: int | None = Field(ge=1)


class UncertaintyV1(StrictModel):
    field: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


class TaskDraftV1(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None
    duration_minutes: int | None = Field(ge=1)
    priority: TaskPriority | None
    earliest_start: datetime | None
    deadline: datetime | None
    preferred_time_of_day: PreferredTimeOfDay | None
    is_splittable: bool | None
    minimum_session_minutes: int | None = Field(ge=1)
    maximum_sessions_per_day: int | None = Field(ge=1)
    proposed_steps: list[ProposedStepV1] = Field(max_length=20)
    assumptions: list[str] = Field(max_length=20)
    uncertainties: list[UncertaintyV1] = Field(max_length=20)
    prompt_version: str = PROMPT_VERSION_V1
    schema_version: Literal["task-draft.schema.v1"] = SCHEMA_VERSION_V1


class ValueSource(StrEnum):
    user = "user"
    inferred = "inferred"
    estimated = "estimated"
    default = "default"


def _validate_draft_value(
    *,
    value: Any,
    source: ValueSource | None,
    confidence: float | None,
    explanation: str | None,
) -> None:
    if value is None:
        if source is not None:
            raise ValueError("source must be null when value is null")
        if confidence is not None:
            raise ValueError("confidence must be null when value is null")
        return
    if source is None:
        raise ValueError("source is required when value is present")
    if confidence is None:
        raise ValueError("confidence is required when value is present")
    if source is ValueSource.user and confidence != 1.0:
        raise ValueError("user-sourced values must have confidence 1.0")
    if source is not ValueSource.user and not explanation:
        raise ValueError(
            "inferred, estimated, and default values require an explanation"
        )


class DraftValueBase(StrictModel):
    source: ValueSource | None
    confidence: float | None = Field(ge=0, le=1)
    explanation: str | None = Field(max_length=1000)
    requires_confirmation: bool

    def validate_value(self, value: Any) -> None:
        _validate_draft_value(
            value=value,
            source=self.source,
            confidence=self.confidence,
            explanation=self.explanation,
        )


class DraftStringValue(DraftValueBase):
    value: str | None = Field(max_length=10_000)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.validate_value(self.value)
        return self


class DraftIntegerValue(DraftValueBase):
    value: int | None = Field(ge=1)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.validate_value(self.value)
        return self


class DraftDatetimeValue(DraftValueBase):
    value: datetime | None

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.validate_value(self.value)
        if self.value is not None and (
            self.value.tzinfo is None or self.value.utcoffset() is None
        ):
            raise ValueError("datetime value must be timezone-aware")
        return self


class DraftBooleanValue(DraftValueBase):
    value: bool | None

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.validate_value(self.value)
        return self


class DraftPriorityValue(DraftValueBase):
    value: TaskPriority | None

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.validate_value(self.value)
        return self


class DraftTimeOfDayValue(DraftValueBase):
    value: PreferredTimeOfDay | None

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.validate_value(self.value)
        return self


class ClarificationImportance(StrEnum):
    required = "required"
    recommended = "recommended"
    optional = "optional"


class ClarificationQuestion(StrictModel):
    field: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=1000)
    importance: ClarificationImportance

    @model_validator(mode="after")
    def validate_question(self) -> Self:
        if not self.question.rstrip().endswith("?"):
            raise ValueError("clarification question must be phrased as a question")
        return self


class ProposedStepV2(StrictModel):
    title: DraftStringValue
    description: DraftStringValue
    duration: DraftIntegerValue
    order: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_title(self) -> Self:
        if self.title.value is None or not self.title.value.strip():
            raise ValueError("proposed step title.value must not be empty")
        return self


TASK_DRAFT_FIELDS = {
    "title",
    "description",
    "duration",
    "priority",
    "earliest_start",
    "deadline",
    "preferred_time_of_day",
    "is_splittable",
    "minimum_session_minutes",
    "maximum_sessions_per_day",
    "proposed_steps",
}


class TaskDraftV2(StrictModel):
    title: DraftStringValue
    description: DraftStringValue
    duration: DraftIntegerValue
    priority: DraftPriorityValue
    earliest_start: DraftDatetimeValue
    deadline: DraftDatetimeValue
    preferred_time_of_day: DraftTimeOfDayValue
    is_splittable: DraftBooleanValue
    minimum_session_minutes: DraftIntegerValue
    maximum_sessions_per_day: DraftIntegerValue
    proposed_steps: list[ProposedStepV2] = Field(max_length=20)
    clarification_questions: list[ClarificationQuestion] = Field(max_length=20)
    prompt_version: str = Field(min_length=1)
    schema_version: Literal["task-draft.schema.v2"]

    @model_validator(mode="after")
    def validate_draft(self) -> Self:
        if self.title.value is None or not self.title.value.strip():
            raise ValueError("title.value is required and must not be empty")
        if self.title.source is None:
            raise ValueError("title.source is required")
        start = self.earliest_start.value
        deadline = self.deadline.value
        if (
            start is not None
            and deadline is not None
            and start.astimezone(UTC) > deadline.astimezone(UTC)
        ):
            raise ValueError("earliest_start must not be after deadline")

        orders = [step.order for step in self.proposed_steps]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("proposed step order must be unique and sequential")

        step_total = sum(step.duration.value or 0 for step in self.proposed_steps)
        total = self.duration.value
        if total is not None and step_total > max(total * 1.5, total + 60):
            raise ValueError(
                "total proposed step duration unreasonably exceeds total duration"
            )
        if (
            total is not None
            and self.duration.source is ValueSource.inferred
            and self.proposed_steps
            and not self.duration.explanation
        ):
            raise ValueError("inferred total duration must explain its calculation")

        unknown_fields = {
            question.field
            for question in self.clarification_questions
            if question.field not in TASK_DRAFT_FIELDS
        }
        if unknown_fields:
            raise ValueError(
                f"clarification questions reference unknown fields: "
                f"{sorted(unknown_fields)}"
            )
        return self


TaskDraft = TaskDraftV2
ProposedStep = ProposedStepV2
Uncertainty = UncertaintyV1
TASK_DRAFT_JSON_SCHEMA = TaskDraftV2.model_json_schema()
