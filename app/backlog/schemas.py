import uuid
from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.backlog.domain import BacklogOrigin, BacklogReason, BacklogStatus
from app.scheduling.types import UnscheduledReasonCode
from app.schemas.scheduling import (
    MAX_PLANNING_HORIZON,
    DateTimeInterval,
    SchedulePreviewResponse,
)


class BacklogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BacklogEntryCreateRequest(BacklogRequest):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "user_id": "11111111-1111-1111-1111-111111111111",
                    "task_id": "22222222-2222-2222-2222-222222222222",
                    "origin": "scheduler",
                    "reason": "no_available_slot",
                    "remaining_duration_minutes": 60,
                    "next_review_at": "2026-08-03T09:00:00Z",
                }
            ]
        },
    )

    user_id: uuid.UUID
    task_id: uuid.UUID
    origin: BacklogOrigin
    reason: BacklogReason
    remaining_duration_minutes: int | None = Field(default=None, gt=0)
    next_review_at: datetime | None = None
    deferred_until: datetime | None = None
    note: str | None = Field(default=None, max_length=4000)


class BacklogDeferRequest(BacklogRequest):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "deferred_until": "2026-08-10T09:00:00Z",
                    "note": "Review next week",
                }
            ]
        },
    )

    deferred_until: datetime | None = None
    next_review_at: datetime | None = None
    note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_review_time(self) -> Self:
        if self.deferred_until is None and self.next_review_at is None:
            raise ValueError("deferred_until or next_review_at is required")
        return self


class BacklogNoteRequest(BacklogRequest):
    note: str | None = Field(default=None, max_length=4000)


class BacklogEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    user_id: uuid.UUID
    status: BacklogStatus
    origin: BacklogOrigin
    reason: BacklogReason
    remaining_duration_minutes: int
    entered_at: datetime
    next_review_at: datetime | None
    deferred_until: datetime | None
    resolved_at: datetime | None
    note: str | None
    scheduling_attempt_count: int
    last_scheduling_attempt_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BacklogSchedulePreviewRequest(BacklogRequest):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "planning_window": {
                        "start": "2026-08-18T08:00:00Z",
                        "end": "2026-08-19T18:00:00Z",
                    },
                    "busy_intervals": [
                        {
                            "start": "2026-08-18T10:00:00Z",
                            "end": "2026-08-18T11:00:00Z",
                        }
                    ],
                }
            ]
        },
    )

    planning_window: DateTimeInterval
    busy_intervals: list[DateTimeInterval] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_horizon(self) -> Self:
        duration = self.planning_window.end.astimezone(
            UTC
        ) - self.planning_window.start.astimezone(UTC)
        if duration > MAX_PLANNING_HORIZON:
            raise ValueError("planning horizon must not exceed 31 days")
        return self


class BacklogSchedulePreviewResponse(BaseModel):
    backlog_entry_id: uuid.UUID
    task_id: uuid.UUID
    remaining_duration_minutes: int
    scheduling_attempt_count: int
    schedule_preview: SchedulePreviewResponse
    unscheduled_reason: UnscheduledReasonCode | None = None
