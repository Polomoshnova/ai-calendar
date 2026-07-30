import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schedule_plans.models import SchedulePlanStatus
from app.schedule_plans.revalidation_models import (
    SchedulePlanRevalidationStatus,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BusyIntervalSource(StrEnum):
    provider_busy = "provider_busy"
    internal_busy = "internal_busy"
    temporary_busy = "temporary_busy"


class SchedulePlanConflictType(StrEnum):
    direct_overlap = "direct_overlap"
    minimum_break_violation = "minimum_break_violation"


class BusyIntervalReference(StrictModel):
    start: datetime
    end: datetime
    calendar_id: str | None
    provider: str
    source: BusyIntervalSource


class SchedulePlanConflict(StrictModel):
    session_id: uuid.UUID
    session_order: int
    session_start: datetime
    session_end: datetime
    conflicting_busy_intervals: list[BusyIntervalReference]
    conflict_type: SchedulePlanConflictType
    overlap_minutes: int
    reason_code: str


class SchedulePlanRevalidationRequest(StrictModel):
    connection_id: uuid.UUID
    calendar_ids: list[str] | None = None
    include_internal_busy: bool = True
    minimum_break_minutes: int | None = Field(default=None, ge=0, le=1440)
    request_id: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("calendar_ids")
    @classmethod
    def validate_calendar_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = list(dict.fromkeys(item.strip() for item in value))
        if not normalized or any(not item for item in normalized):
            raise ValueError("calendar_ids must contain non-empty identifiers")
        return normalized

    @field_validator("request_id", mode="before")
    @classmethod
    def trim_request_id(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class SchedulePlanRevalidationResult(BaseModel):
    revalidation_id: uuid.UUID
    plan_id: uuid.UUID
    plan_status_before: SchedulePlanStatus
    plan_status_after: SchedulePlanStatus
    result: SchedulePlanRevalidationStatus
    checked_at: datetime
    valid_until: datetime | None
    sessions_hash: str
    planning_window_start: datetime
    planning_window_end: datetime
    provider: str
    connection_id: uuid.UUID | None
    queried_calendar_ids: list[str]
    provider_busy_interval_count: int
    merged_busy_interval_count: int
    conflicting_session_count: int
    conflicts: list[SchedulePlanConflict]
    diagnostics: dict[str, Any]
    can_apply: bool
    failure_code: str | None


class SchedulePlanRevalidationHistoryResponse(BaseModel):
    revalidations: list[SchedulePlanRevalidationResult]
