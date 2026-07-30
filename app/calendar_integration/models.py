import uuid
from datetime import UTC, datetime
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalendarProviderConnection(StrictModel):
    connection_id: uuid.UUID
    access_token: SecretStr = Field(repr=False)


class CalendarAccountIdentity(StrictModel):
    provider_account_id: str = Field(min_length=1, max_length=255)
    provider_account_email: str | None = Field(default=None, max_length=320)


class CalendarEventCreateRequest(StrictModel):
    connection_id: uuid.UUID
    provider_account_id: str = Field(min_length=1, max_length=255)
    calendar_id: str = Field(min_length=1)
    event_id: str = Field(min_length=5, max_length=1024)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    start: datetime
    end: datetime
    timezone: str
    task_id: uuid.UUID | None = None
    schedule_plan_id: uuid.UUID
    scheduled_session_id: uuid.UUID

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        _require_aware(self.start)
        _require_aware(self.end)
        if self.start.astimezone(UTC) >= self.end.astimezone(UTC):
            raise ValueError("event start must be before end")
        return self


class CalendarEventCreateResult(StrictModel):
    external_event_id: str = Field(min_length=1)
    calendar_id: str = Field(min_length=1)
    connection_id: uuid.UUID
    provider_account_id: str = Field(min_length=1)
    start: datetime
    end: datetime
    etag: str | None = None
    provider_updated_at: datetime | None = None


class CalendarEventSnapshot(StrictModel):
    external_event_id: str = Field(min_length=1)
    calendar_id: str = Field(min_length=1)
    exists: bool = True
    cancelled: bool = False
    start: datetime | None = None
    end: datetime | None = None
    timezone: str | None = None
    etag: str | None = None
    provider_updated_at: datetime | None = None
    provider_status: str | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if self.exists and not self.cancelled:
            if self.start is None or self.end is None:
                raise ValueError("present calendar events require start and end")
            _require_aware(self.start)
            _require_aware(self.end)
            if self.start.astimezone(UTC) >= self.end.astimezone(UTC):
                raise ValueError("event start must be before end")
        return self


class ExternalCalendar(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    timezone: str | None = None
    primary: bool = False
    selected: bool = False
    access_role: str | None = None


class CalendarBusyInterval(StrictModel):
    start: datetime
    end: datetime
    calendar_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        _require_aware(self.start)
        _require_aware(self.end)
        if self.start.astimezone(UTC) > self.end.astimezone(UTC):
            raise ValueError("busy interval end must not be before start")
        return self


class CalendarQueryError(StrictModel):
    calendar_id: str
    reason: str
    domain: str | None = None


class CalendarBusyResult(StrictModel):
    time_min: datetime
    time_max: datetime
    timezone: str
    intervals: list[CalendarBusyInterval]
    errors: list[CalendarQueryError]

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        _require_aware(self.time_min)
        _require_aware(self.time_max)
        if self.time_min.astimezone(UTC) >= self.time_max.astimezone(UTC):
            raise ValueError("time_min must be before time_max")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {self.timezone}") from exc
        return self


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("calendar datetimes must be timezone-aware")
