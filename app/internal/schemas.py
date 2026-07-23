import uuid
from datetime import UTC, datetime, time
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.preferences import SchedulingPreferences, parse_working_hours
from app.domain.tasks import PreferredTimeOfDay, TaskPriority, validate_task
from app.scheduling import SchedulingTask
from app.schemas.scheduling import (
    MAX_PLANNING_HORIZON,
    DateTimeInterval,
    SchedulePreviewResponse,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TemporaryPreferences(StrictModel):
    timezone: str
    working_hours: dict[str, list[dict[str, str]]]
    preferred_task_time: PreferredTimeOfDay = PreferredTimeOfDay.any
    minimum_break_minutes: int = Field(default=0, ge=0)
    no_deep_work_after: time | None = None
    default_minimum_session_minutes: int = Field(default=15, gt=0)

    @model_validator(mode="after")
    def validate_preferences(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {self.timezone}") from exc
        parse_working_hours(self.working_hours)
        return self

    def to_domain(self) -> SchedulingPreferences:
        return SchedulingPreferences(
            timezone=self.timezone,
            working_hours=parse_working_hours(self.working_hours),
            preferred_task_time=self.preferred_task_time,
            minimum_break_minutes=self.minimum_break_minutes,
            no_deep_work_after=self.no_deep_work_after,
            default_minimum_session_minutes=self.default_minimum_session_minutes,
        )


class TemporaryTask(StrictModel):
    id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    duration_minutes: int = Field(gt=0)
    priority: TaskPriority = TaskPriority.medium
    earliest_start: datetime | None = None
    deadline: datetime | None = None
    preferred_time_of_day: PreferredTimeOfDay = PreferredTimeOfDay.any
    is_splittable: bool = False
    minimum_session_minutes: int = Field(default=15, gt=0)
    maximum_sessions_per_day: int = Field(default=1, gt=0)

    @model_validator(mode="after")
    def validate_task(self) -> Self:
        if self.earliest_start is not None and self.earliest_start.tzinfo is None:
            raise ValueError("earliest_start must be timezone-aware")
        if self.deadline is not None and self.deadline.tzinfo is None:
            raise ValueError("deadline must be timezone-aware")
        validate_task(
            self.duration_minutes,
            self.earliest_start,
            self.deadline,
            is_splittable=self.is_splittable,
            minimum_session_minutes=self.minimum_session_minutes,
            maximum_sessions_per_day=self.maximum_sessions_per_day,
        )
        return self

    def to_domain(self) -> SchedulingTask:
        return SchedulingTask(
            id=self.id,
            duration_minutes=self.duration_minutes,
            priority=self.priority,
            earliest_start=self.earliest_start,
            deadline=self.deadline,
            preferred_time_of_day=self.preferred_time_of_day,
            is_splittable=self.is_splittable,
            minimum_session_minutes=self.minimum_session_minutes,
            maximum_sessions_per_day=self.maximum_sessions_per_day,
        )


class InternalPreviewRequest(StrictModel):
    mode: Literal["existing_user", "product_scenario"]
    planning_window: DateTimeInterval
    busy_intervals: list[DateTimeInterval] = Field(default_factory=list)
    user_id: uuid.UUID | None = None
    timezone: str | None = None
    preferences: TemporaryPreferences | None = None
    tasks: list[TemporaryTask] | None = None

    @model_validator(mode="after")
    def validate_mode_inputs(self) -> Self:
        duration = self.planning_window.end.astimezone(
            UTC
        ) - self.planning_window.start.astimezone(UTC)
        if duration > MAX_PLANNING_HORIZON:
            raise ValueError("planning horizon must not exceed 31 days")
        if self.mode == "existing_user":
            if self.user_id is None:
                raise ValueError("existing_user mode requires user_id")
            if self.preferences is not None or self.tasks is not None:
                raise ValueError(
                    "existing_user mode cannot include temporary tasks or preferences"
                )
        else:
            if self.preferences is None or self.tasks is None:
                raise ValueError(
                    "product_scenario mode requires temporary tasks and preferences"
                )
            if self.timezone is None:
                raise ValueError("product_scenario mode requires timezone")
            if self.timezone != self.preferences.timezone:
                raise ValueError("timezone must match temporary preferences timezone")
        return self


class InternalPreviewResponse(SchedulePreviewResponse):
    task_titles: dict[str, str]


class ScenarioDocument(StrictModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    planning_window: DateTimeInterval
    user_preferences: TemporaryPreferences
    busy_intervals: list[DateTimeInterval]
    tasks: list[TemporaryTask]
    expected_observations: list[str]


class ScenarioSummary(StrictModel):
    filename: str
    name: str
    description: str


class UserSummary(StrictModel):
    id: uuid.UUID
    email: str
    timezone: str
    has_stored_preferences: bool


class PreferencesEnvelope(StrictModel):
    user_id: uuid.UUID
    has_stored_preferences: bool
    preferences: TemporaryPreferences


class ReviewFields(StrictModel):
    score: int = Field(ge=1, le=5)
    verdict: Literal["logical", "acceptable", "questionable", "illogical"]
    notes: str = ""
    observed_problems: list[
        Literal[
            "wrong priority",
            "deadline handling",
            "excessive splitting",
            "insufficient splitting",
            "poor time-of-day choice",
            "unnecessary fragmentation",
            "break issue",
            "task left unscheduled unexpectedly",
            "unclear explanation",
            "other",
        ]
    ] = Field(default_factory=list)


class NormalizedPreviewInputs(StrictModel):
    user_timezone: str
    planning_window: DateTimeInterval
    preferences_used: TemporaryPreferences
    tasks: list[dict[str, Any]]
    busy_intervals: list[dict[str, Any]]


class ReviewExportRequest(StrictModel):
    normalized_inputs: NormalizedPreviewInputs
    generated_preview_result: dict[str, Any]
    review: ReviewFields


class ReviewExportPayload(StrictModel):
    user_timezone: str
    planning_window: dict[str, Any]
    preferences_used: dict[str, Any]
    tasks: list[dict[str, Any]]
    busy_intervals: list[dict[str, Any]]
    generated_preview_result: dict[str, Any]
    score: int
    verdict: str
    notes: str
    observed_problems: list[str]
    exported_at: datetime
