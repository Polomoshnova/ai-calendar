from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ai_intake.types import TaskDraftV2
from app.internal.schemas import TemporaryPreferences, TemporaryTask
from app.schemas.scheduling import (
    MAX_PLANNING_HORIZON,
    DateTimeInterval,
    SchedulePreviewResponse,
)
from app.task_confirmation.models import (
    ConfirmationResult,
    ConfirmedTaskStep,
    DraftReview,
)

WORKFLOW_VERSION = "task-to-schedule-preview.v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowAIContext(StrictModel):
    current_datetime: datetime | None = None
    timezone: str | None = None

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if self.current_datetime is not None and (
            self.current_datetime.tzinfo is None
            or self.current_datetime.utcoffset() is None
        ):
            raise ValueError("current_datetime must be timezone-aware")
        if self.timezone is not None:
            try:
                ZoneInfo(self.timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"unknown IANA timezone: {self.timezone}") from exc
        return self


class WorkflowSchedulingContext(StrictModel):
    window_start: datetime
    window_end: datetime
    timezone: str
    busy_intervals: list[DateTimeInterval] = Field(default_factory=list)
    preferences: TemporaryPreferences
    existing_pending_tasks: list[TemporaryTask] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        for name, value in (
            ("window_start", self.window_start),
            ("window_end", self.window_end),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.window_start.astimezone(UTC) >= self.window_end.astimezone(UTC):
            raise ValueError("window_start must be before window_end")
        if (
            self.window_end.astimezone(UTC) - self.window_start.astimezone(UTC)
            > MAX_PLANNING_HORIZON
        ):
            raise ValueError("planning horizon must not exceed 31 days")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {self.timezone}") from exc
        if self.timezone != self.preferences.timezone:
            raise ValueError("timezone must match preferences.timezone")
        return self

    def planning_window(self) -> DateTimeInterval:
        return DateTimeInterval(start=self.window_start, end=self.window_end)


class TaskToSchedulePreviewRequest(StrictModel):
    text: str = Field(min_length=1, max_length=10_000)
    review: DraftReview
    scheduling_context: WorkflowSchedulingContext
    ai_context: WorkflowAIContext | None = None
    include_trace: bool = True

    @field_validator("text", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class WorkflowStage(StrEnum):
    ai_intake = "ai_intake"
    confirmation = "confirmation"
    scheduler_mapping = "scheduler_mapping"
    scheduling_preview = "scheduling_preview"


class WorkflowTraceStatus(StrEnum):
    completed = "completed"
    skipped = "skipped"
    failed = "failed"


class SchedulerValueSource(StrEnum):
    confirmed = "confirmed"
    scheduler_default = "scheduler_default"


class SchedulerResolvedValues(StrictModel):
    priority: SchedulerValueSource
    preferred_time_of_day: SchedulerValueSource
    minimum_session_minutes: SchedulerValueSource
    maximum_sessions_per_day: SchedulerValueSource
    is_splittable: SchedulerValueSource


class WorkflowTraceEntry(StrictModel):
    stage: WorkflowStage
    status: WorkflowTraceStatus
    summary: str
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SchedulerTaskSnapshot(StrictModel):
    id: str
    title: str
    description: str | None
    duration_minutes: int
    priority: str
    earliest_start: datetime | None
    deadline: datetime | None
    preferred_time_of_day: str
    is_splittable: bool
    minimum_session_minutes: int
    maximum_sessions_per_day: int
    steps: list[ConfirmedTaskStep]
    value_sources: SchedulerResolvedValues


class SchedulerInputSnapshot(StrictModel):
    task: SchedulerTaskSnapshot
    existing_pending_tasks: list[TemporaryTask]
    window_start: datetime
    window_end: datetime
    timezone: str
    busy_interval_count: int = Field(ge=0)
    pending_task_count: int = Field(ge=0)


class TaskToSchedulePreviewResponse(StrictModel):
    draft: TaskDraftV2
    confirmation: ConfirmationResult
    scheduler_input: SchedulerInputSnapshot
    schedule_preview: SchedulePreviewResponse
    trace: list[WorkflowTraceEntry]
    workflow_version: str = WORKFLOW_VERSION


class WorkflowExpectedOutcome(StrictModel):
    status: str
    expected_duration_minutes: int | None = None
    expected_warning_codes: list[str] = Field(default_factory=list)
    expected_error_code: str | None = None


class WorkflowReplayCase(StrictModel):
    name: str
    request: TaskToSchedulePreviewRequest
    fake_ai_response: TaskDraftV2
    expected: WorkflowExpectedOutcome
