from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewDecision(StrEnum):
    accepted = "accepted"
    edited = "edited"
    rejected = "rejected"


class ReviewMode(StrEnum):
    explicit = "explicit"
    accept_unreviewed = "accept_unreviewed"
    reject_unreviewed_estimates = "reject_unreviewed_estimates"


def _validate_field_review(
    decision: ReviewDecision, value: object, *, positive: bool = False
) -> None:
    if decision is ReviewDecision.edited:
        if value is None:
            raise ValueError("edited review requires a value")
        if positive and isinstance(value, int) and value <= 0:
            raise ValueError("edited integer value must be positive")
    elif value is not None:
        raise ValueError(f"{decision.value} review must not contain a value")


class StringFieldReview(StrictModel):
    decision: ReviewDecision
    value: str | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        _validate_field_review(self.decision, self.value)
        return self


class IntegerFieldReview(StrictModel):
    decision: ReviewDecision
    value: int | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        _validate_field_review(self.decision, self.value, positive=True)
        return self


class BooleanFieldReview(StrictModel):
    decision: ReviewDecision
    value: bool | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        _validate_field_review(self.decision, self.value)
        return self


class DatetimeFieldReview(StrictModel):
    decision: ReviewDecision
    value: datetime | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        _validate_field_review(self.decision, self.value)
        if self.value is not None and (
            self.value.tzinfo is None or self.value.utcoffset() is None
        ):
            raise ValueError("edited datetime value must be timezone-aware")
        return self


class ProposedStepReview(StrictModel):
    original_order: int = Field(ge=1)
    decision: ReviewDecision
    title: str | None = None
    description: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
    new_order: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_overrides(self) -> Self:
        overrides = (
            self.title,
            self.description,
            self.duration_minutes,
            self.new_order,
        )
        if self.decision is ReviewDecision.edited:
            if all(value is None for value in overrides):
                raise ValueError("edited step review requires at least one override")
        elif any(value is not None for value in overrides):
            raise ValueError(
                f"{self.decision.value} step review must not contain overrides"
            )
        if self.title is not None and not self.title.strip():
            raise ValueError("step title override must not be empty")
        return self


class DraftReview(StrictModel):
    title: StringFieldReview | None = None
    description: StringFieldReview | None = None
    duration: IntegerFieldReview | None = None
    priority: StringFieldReview | None = None
    earliest_start: DatetimeFieldReview | None = None
    deadline: DatetimeFieldReview | None = None
    preferred_time_of_day: StringFieldReview | None = None
    is_splittable: BooleanFieldReview | None = None
    minimum_session_minutes: IntegerFieldReview | None = None
    maximum_sessions_per_day: IntegerFieldReview | None = None
    proposed_steps: list[ProposedStepReview] = Field(default_factory=list)
    mode: ReviewMode = ReviewMode.explicit
    confirmation_note: str | None = Field(default=None, max_length=2000)


class ConfirmedTaskStep(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    duration_minutes: int | None = Field(default=None, ge=1)
    order: int = Field(ge=1)


class ConfirmedTask(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    duration_minutes: int | None = Field(default=None, ge=1)
    priority: str | None = None
    earliest_start: datetime | None = None
    deadline: datetime | None = None
    preferred_time_of_day: str | None = None
    is_splittable: bool
    minimum_session_minutes: int | None = Field(default=None, ge=1)
    maximum_sessions_per_day: int | None = Field(default=None, ge=1)
    steps: list[ConfirmedTaskStep]

    @model_validator(mode="after")
    def validate_task(self) -> Self:
        if not self.title.strip():
            raise ValueError("title must not be empty")
        for name, value in (
            ("earliest_start", self.earliest_start),
            ("deadline", self.deadline),
        ):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError(f"{name} must be timezone-aware")
        if (
            self.earliest_start is not None
            and self.deadline is not None
            and self.earliest_start.astimezone(UTC) > self.deadline.astimezone(UTC)
        ):
            raise ValueError("earliest_start must not be after deadline")
        if [step.order for step in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("step order must be sequential starting at 1")
        step_total = sum(step.duration_minutes or 0 for step in self.steps)
        if (
            self.is_splittable
            and self.duration_minutes is not None
            and step_total
            > max(self.duration_minutes * 1.5, self.duration_minutes + 60)
        ):
            raise ValueError("total step duration unreasonably exceeds total duration")
        return self


class FieldChange(StrictModel):
    field: str
    decision: ReviewDecision
    original_value: Any
    confirmed_value: Any


class StepChange(StrictModel):
    original_order: int
    decision: ReviewDecision
    original_value: dict[str, Any] | None
    confirmed_value: dict[str, Any] | None


class ConfirmationAudit(StrictModel):
    field_changes: list[FieldChange]
    step_changes: list[StepChange]
    accepted_fields: list[str]
    edited_fields: list[str]
    rejected_fields: list[str]


class ConfirmationResult(StrictModel):
    task: ConfirmedTask
    audit: ConfirmationAudit
