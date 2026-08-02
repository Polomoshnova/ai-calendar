from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from app.schedule_plans.models import ScheduledSession, ScheduledSessionStatus
from app.schedule_plans.repository import schedule_plan_status_reserves_time


class BacklogStatus(StrEnum):
    active = "active"
    deferred = "deferred"
    resolved = "resolved"
    cancelled = "cancelled"


class BacklogOrigin(StrEnum):
    user = "user"
    scheduler = "scheduler"
    system = "system"
    calendar_sync = "calendar_sync"


class BacklogReason(StrEnum):
    no_deadline = "no_deadline"
    no_available_slot = "no_available_slot"
    insufficient_capacity = "insufficient_capacity"
    planning_horizon_exceeded = "planning_horizon_exceeded"
    awaiting_user_confirmation = "awaiting_user_confirmation"
    manual_defer = "manual_defer"
    partially_scheduled = "partially_scheduled"
    other = "other"


OPEN_BACKLOG_STATUSES = frozenset({BacklogStatus.active, BacklogStatus.deferred})
TERMINAL_BACKLOG_STATUSES = frozenset({BacklogStatus.resolved, BacklogStatus.cancelled})


class BacklogDomainError(ValueError):
    pass


class InvalidBacklogTransitionError(BacklogDomainError):
    pass


def require_aware(value: datetime | None, *, field: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise BacklogDomainError(f"{field} must be timezone-aware")


def validate_backlog_values(
    *,
    origin: BacklogOrigin,
    reason: BacklogReason,
    note: str | None,
    status: BacklogStatus,
    remaining_duration_minutes: int,
    task_duration_minutes: int,
    entered_at: datetime,
    next_review_at: datetime | None,
    deferred_until: datetime | None,
    resolved_at: datetime | None,
    scheduling_attempt_count: int,
    last_scheduling_attempt_at: datetime | None,
) -> None:
    require_aware(entered_at, field="entered_at")
    require_aware(next_review_at, field="next_review_at")
    require_aware(deferred_until, field="deferred_until")
    require_aware(resolved_at, field="resolved_at")
    require_aware(last_scheduling_attempt_at, field="last_scheduling_attempt_at")
    if remaining_duration_minutes < 0:
        raise BacklogDomainError("remaining_duration_minutes cannot be negative")
    if remaining_duration_minutes > task_duration_minutes:
        raise BacklogDomainError(
            "remaining_duration_minutes cannot exceed Task.duration_minutes"
        )
    if status in OPEN_BACKLOG_STATUSES and remaining_duration_minutes <= 0:
        raise BacklogDomainError(
            "open backlog entries require positive remaining duration"
        )
    if status in TERMINAL_BACKLOG_STATUSES and remaining_duration_minutes != 0:
        raise BacklogDomainError(
            "terminal backlog entries require zero remaining duration"
        )
    if status is BacklogStatus.deferred and not (deferred_until or next_review_at):
        raise BacklogDomainError(
            "deferred backlog entries require deferred_until or next_review_at"
        )
    if status is BacklogStatus.resolved and resolved_at is None:
        raise BacklogDomainError("resolved backlog entries require resolved_at")
    if status in OPEN_BACKLOG_STATUSES and resolved_at is not None:
        raise BacklogDomainError("open backlog entries cannot have resolved_at")
    if status is BacklogStatus.cancelled and resolved_at is not None:
        raise BacklogDomainError("cancelled backlog entries cannot have resolved_at")
    if scheduling_attempt_count < 0:
        raise BacklogDomainError("scheduling_attempt_count cannot be negative")
    required_origins = {
        BacklogReason.manual_defer: BacklogOrigin.user,
        BacklogReason.no_available_slot: BacklogOrigin.scheduler,
        BacklogReason.insufficient_capacity: BacklogOrigin.scheduler,
        BacklogReason.planning_horizon_exceeded: BacklogOrigin.scheduler,
        BacklogReason.partially_scheduled: BacklogOrigin.scheduler,
    }
    required_origin = required_origins.get(reason)
    if required_origin is not None and origin is not required_origin:
        raise BacklogDomainError(
            f"{reason.value} backlog entries require origin={required_origin.value}"
        )
    if reason is BacklogReason.other and (note is None or not note.strip()):
        raise BacklogDomainError("other backlog entries require a meaningful note")


def calculate_remaining_unscheduled_duration(
    task_duration_minutes: int,
    sessions: Iterable[ScheduledSession],
) -> int:
    """Return unassigned work without calling a provider or scheduler."""
    if task_duration_minutes <= 0:
        raise BacklogDomainError("task_duration_minutes must be positive")
    excluded_session_statuses = {
        ScheduledSessionStatus.failed,
        ScheduledSessionStatus.obsolete,
    }
    scheduled_minutes = sum(
        item.duration_minutes
        for item in sessions
        if schedule_plan_status_reserves_time(item.plan.status)
        and item.status not in excluded_session_statuses
    )
    return max(task_duration_minutes - scheduled_minutes, 0)


def validate_transition(current: BacklogStatus, target: BacklogStatus) -> None:
    if current is target and current in OPEN_BACKLOG_STATUSES:
        return
    allowed = {
        BacklogStatus.active: {
            BacklogStatus.deferred,
            BacklogStatus.resolved,
            BacklogStatus.cancelled,
        },
        BacklogStatus.deferred: {
            BacklogStatus.active,
            BacklogStatus.resolved,
            BacklogStatus.cancelled,
        },
        BacklogStatus.resolved: set(),
        BacklogStatus.cancelled: set(),
    }
    if target not in allowed[current]:
        raise InvalidBacklogTransitionError(
            f"cannot transition backlog entry from {current.value} to {target.value}"
        )
