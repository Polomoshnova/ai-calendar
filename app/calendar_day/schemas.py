import uuid
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.schedule_plans.models import ScheduledSessionStatus, SchedulePlanStatus


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalendarDaySource(StrEnum):
    google_calendar = "google_calendar"
    ai_calendar = "ai_calendar"


class CalendarDayKind(StrEnum):
    event = "event"
    scheduled_session = "scheduled_session"
    needs_attention = "needs_attention"


class CalendarDaySyncStatus(StrEnum):
    fresh = "fresh"
    partial = "partial"
    error = "error"
    not_connected = "not_connected"
    no_calendars_selected = "no_calendars_selected"
    reauthorization_required = "reauthorization_required"


class CalendarDayError(StrictModel):
    code: str
    message: str
    connection_id: uuid.UUID | None = None
    calendar_id: str | None = None
    scheduled_session_id: uuid.UUID | None = None


class CalendarDaySyncState(StrictModel):
    status: CalendarDaySyncStatus
    partial: bool
    errors: list[CalendarDayError]


class CalendarDayScheduledSession(StrictModel):
    session_id: uuid.UUID
    plan_id: uuid.UUID
    task_id: uuid.UUID | None
    plan_status: SchedulePlanStatus
    session_status: ScheduledSessionStatus


class CalendarDayItem(StrictModel):
    id: str
    source: CalendarDaySource
    kind: CalendarDayKind
    title: str
    start: datetime
    end: datetime
    all_day: bool
    status: str
    scheduled_session: CalendarDayScheduledSession | None


class CalendarDayResponse(StrictModel):
    date: date
    timezone: str
    day_start: datetime
    day_end: datetime
    generated_at: datetime
    sync_state: CalendarDaySyncState
    warnings: list[CalendarDayError]
    items: list[CalendarDayItem]
