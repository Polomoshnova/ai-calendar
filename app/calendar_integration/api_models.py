import uuid
from datetime import UTC, datetime
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.calendar_integration.models import CalendarQueryError, ExternalCalendar
from app.internal.schemas import TemporaryPreferences, TemporaryTask
from app.schemas.scheduling import (
    MAX_PLANNING_HORIZON,
    DateTimeInterval,
    SchedulePreviewResponse,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OAuthStartRequest(StrictModel):
    user_id: uuid.UUID


class OAuthStartResponse(StrictModel):
    authorization_url: str
    expires_at: datetime


class OAuthCallbackResponse(StrictModel):
    status: Literal["connected"]
    connection_id: uuid.UUID


class ConnectionStatusResponse(StrictModel):
    id: uuid.UUID
    provider: Literal["google"]
    status: str
    provider_account_email: str | None
    scopes: list[str]
    created_at: datetime
    updated_at: datetime
    token_expires_at: datetime | None
    selected_calendar_count: int
    last_successful_sync_at: datetime | None
    last_error_code: str | None


class CalendarSelectionRequest(StrictModel):
    calendar_ids: list[str]


class CalendarSelectionItem(StrictModel):
    id: str
    name: str
    timezone: str | None
    primary: bool
    include_in_availability: bool


class CalendarListResponse(StrictModel):
    calendars: list[ExternalCalendar]


class CalendarSelectionsResponse(StrictModel):
    calendars: list[CalendarSelectionItem]


class FreeBusyRequest(StrictModel):
    time_min: datetime
    time_max: datetime
    timezone: str
    calendar_ids: list[str] | None = None

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        _validate_window(self.time_min, self.time_max, self.timezone)
        return self


class ProviderBusyInterval(StrictModel):
    start: datetime
    end: datetime
    calendar_id: str


class FreeBusyResponse(StrictModel):
    connection_id: uuid.UUID
    provider: Literal["google"]
    time_min: datetime
    time_max: datetime
    timezone: str
    calendar_ids: list[str]
    busy_intervals: list[ProviderBusyInterval]
    errors: list[CalendarQueryError]


class CalendarPreviewRequest(StrictModel):
    planning_window: DateTimeInterval
    timezone: str
    calendar_ids: list[str] | None = None
    additional_busy_intervals: list[DateTimeInterval] = Field(default_factory=list)
    preferences: TemporaryPreferences
    pending_tasks: list[TemporaryTask] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        _validate_window(
            self.planning_window.start, self.planning_window.end, self.timezone
        )
        if self.preferences.timezone != self.timezone:
            raise ValueError("timezone must match preferences timezone")
        return self


class CalendarQuerySummary(StrictModel):
    provider: Literal["google"]
    calendar_ids: list[str]
    provider_busy_interval_count: int
    merged_busy_interval_count: int
    calendar_errors: list[CalendarQueryError]


class CalendarPreviewResponse(StrictModel):
    calendar_context: CalendarQuerySummary
    busy_intervals: list[DateTimeInterval]
    schedule_preview: SchedulePreviewResponse


def _validate_window(start: datetime, end: datetime, timezone: str) -> None:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("time_min must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("time_max must be timezone-aware")
    if start.astimezone(UTC) >= end.astimezone(UTC):
        raise ValueError("time_min must be before time_max")
    if end.astimezone(UTC) - start.astimezone(UTC) > MAX_PLANNING_HORIZON:
        raise ValueError("planning horizon must not exceed 31 days")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {timezone}") from exc
