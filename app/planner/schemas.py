import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.backlog.domain import BacklogOrigin, BacklogReason, BacklogStatus
from app.models.calendar_sync import SyncStatus
from app.schedule_plans.models import (
    ScheduledSessionStatus,
    SchedulePlanStatus,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlannerExternalCalendarState(StrictModel):
    mapping_id: uuid.UUID
    sync_status: SyncStatus
    connection_id: uuid.UUID
    provider: str
    provider_account_id: str | None
    calendar_id: str
    external_event_id: str
    last_synced_at: datetime | None
    sync_error_code: str | None


class PlannerSession(StrictModel):
    session_id: uuid.UUID
    task_id: uuid.UUID | None
    task_title: str
    start: datetime
    end: datetime
    timezone: str
    plan_id: uuid.UUID
    plan_status: SchedulePlanStatus
    session_status: ScheduledSessionStatus
    external_calendar: PlannerExternalCalendarState | None


class PlannerBacklogItem(StrictModel):
    backlog_entry_id: uuid.UUID
    task_id: uuid.UUID
    task_title: str
    status: BacklogStatus
    origin: BacklogOrigin
    reason: BacklogReason
    remaining_duration_minutes: int
    entered_at: datetime
    next_review_at: datetime | None
    deferred_until: datetime | None
    scheduling_attempt_count: int
    last_scheduling_attempt_at: datetime | None
    note: str | None


class PlannerCalendarContext(StrictModel):
    provider: str | None
    connection_ids: list[uuid.UUID]
    calendar_ids: list[str]
    captured_at: datetime | None
    selection_hash: str | None


class PlannerPlanReadiness(StrEnum):
    awaiting_confirmation = "awaiting_confirmation"
    ready_to_apply = "ready_to_apply"
    needs_revalidation = "needs_revalidation"
    apply_in_progress = "apply_in_progress"
    partially_applied = "partially_applied"


class PlannerPlan(StrictModel):
    plan_id: uuid.UUID
    task_id: uuid.UUID | None
    task_title: str
    backlog_entry_id: uuid.UUID | None
    status: SchedulePlanStatus
    readiness: PlannerPlanReadiness
    sessions: list[PlannerSession]
    total_scheduled_minutes: int
    mapped_session_count: int
    total_session_count: int
    revalidation_required: bool
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    applied_at: datetime | None
    calendar_context: PlannerCalendarContext


class PlannerAttentionType(StrEnum):
    consistency_finding = "consistency_finding"
    external_calendar_change = "external_calendar_change"
    schedule_plan = "schedule_plan"


class PlannerAttentionAction(StrEnum):
    review_consistency = "review_consistency"
    process_external_change = "process_external_change"
    revalidate_plan = "revalidate_plan"
    retry_apply = "retry_apply"


class PlannerAttentionItem(StrictModel):
    type: PlannerAttentionType
    item_id: uuid.UUID
    severity: str | None
    summary: str
    action: PlannerAttentionAction
    task_id: uuid.UUID | None
    plan_id: uuid.UUID | None
    session_id: uuid.UUID | None
    mapping_id: uuid.UUID | None
    external_change_id: uuid.UUID | None
    created_at: datetime


class PlannerResponse(StrictModel):
    generated_at: datetime
    timezone: str
    today: list[PlannerSession]
    upcoming: list[PlannerSession]
    backlog: list[PlannerBacklogItem]
    needs_attention: list[PlannerAttentionItem]
    proposed_plans: list[PlannerPlan]
    confirmed_not_applied: list[PlannerPlan]
