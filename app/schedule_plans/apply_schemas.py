import uuid
from enum import StrEnum

from pydantic import BaseModel

from app.schedule_plans.models import SchedulePlanStatus


class ApplySessionStatus(StrEnum):
    already_applied = "already_applied"
    applied = "applied"
    failed = "failed"


class ApplySessionOutcome(BaseModel):
    scheduled_session_id: uuid.UUID
    status: ApplySessionStatus
    external_event_id: str | None = None
    connection_id: uuid.UUID | None = None
    calendar_id: str | None = None
    error_code: str | None = None
    message: str | None = None


class ApplySchedulePlanResult(BaseModel):
    plan_id: uuid.UUID
    previous_status: SchedulePlanStatus
    resulting_status: SchedulePlanStatus
    sessions_total: int
    sessions_already_applied: int
    sessions_attempted: int
    sessions_applied: int
    sessions_failed: int
    outcomes: list[ApplySessionOutcome]
