import uuid
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.timezones import validate_timezone
from app.schedule_plans.models import (
    ScheduledSessionStatus,
    SchedulePlanSource,
    SchedulePlanStatus,
)
from app.schemas.scheduling import SchedulePreviewResponse
from app.task_confirmation.models import ConfirmedTask


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _contains_secret_key(value: object) -> bool:
    forbidden = {
        "access_token",
        "refresh_token",
        "client_secret",
        "oauth_state",
        "password",
        "token",
        "secret",
    }
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower()
            if normalized in forbidden or normalized.endswith(("_token", "_secret")):
                return True
            if _contains_secret_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


class CalendarContextSnapshot(StrictModel):
    provider: str = Field(min_length=1, max_length=50)
    calendar_ids: list[str] = Field(default_factory=list)
    provider_busy_interval_count: int = Field(ge=0)
    merged_busy_interval_count: int = Field(ge=0)
    queried_at: datetime | None = None

    @field_validator("queried_at")
    @classmethod
    def validate_queried_at(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            _require_aware(value, "queried_at")
        return value


class SchedulePlanContext(StrictModel):
    timezone: str
    planning_window_start: datetime
    planning_window_end: datetime
    source_calendar_snapshot_at: datetime | None = None
    scheduler_version: str = Field(min_length=1, max_length=100)
    workflow_version: str | None = Field(default=None, max_length=100)
    calendar_context: CalendarContextSnapshot | None = None
    preferences_snapshot: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timezone")
    @classmethod
    def validate_iana_timezone(cls, value: str) -> str:
        return validate_timezone(value)

    @field_validator("preferences_snapshot")
    @classmethod
    def reject_secret_snapshot_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _contains_secret_key(value):
            raise ValueError("preferences_snapshot contains forbidden secret fields")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        _require_aware(self.planning_window_start, "planning_window_start")
        _require_aware(self.planning_window_end, "planning_window_end")
        if self.planning_window_start.astimezone(
            UTC
        ) >= self.planning_window_end.astimezone(UTC):
            raise ValueError("planning_window_start must be before planning_window_end")
        if self.source_calendar_snapshot_at is not None:
            _require_aware(
                self.source_calendar_snapshot_at,
                "source_calendar_snapshot_at",
            )
        return self


class SchedulePlanCreateRequest(StrictModel):
    user_id: uuid.UUID
    task_id: uuid.UUID | None = None
    plan_group_id: uuid.UUID | None = None
    confirmed_task: ConfirmedTask
    schedule_preview: SchedulePreviewResponse
    planning_context: SchedulePlanContext
    source: SchedulePlanSource
    confirmation_note: str | None = Field(default=None, max_length=2000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def trim_idempotency_key(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class PlanConfirmationRequest(StrictModel):
    confirmation_note: str | None = Field(default=None, max_length=2000)


class ScheduledSessionResponse(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    task_id: uuid.UUID | None
    step_order: int | None
    title: str
    description: str | None
    start: datetime
    end: datetime
    duration_minutes: int
    order: int
    status: ScheduledSessionStatus
    external_provider: str | None
    external_calendar_id: str | None
    external_event_id: str | None
    failure_code: str | None


class SchedulePlanResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    task_id: uuid.UUID | None
    plan_group_id: uuid.UUID
    version: int
    source: SchedulePlanSource
    status: SchedulePlanStatus
    timezone: str
    planning_window_start: datetime
    planning_window_end: datetime
    source_calendar_snapshot_at: datetime | None
    scheduler_version: str
    workflow_version: str | None
    sessions: list[ScheduledSessionResponse]
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    applied_at: datetime | None
    confirmation_note: str | None
    failure_code: str | None


class SchedulePlanListResponse(BaseModel):
    plans: list[SchedulePlanResponse]
