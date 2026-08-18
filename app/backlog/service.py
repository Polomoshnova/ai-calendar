import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.availability import TimeInterval
from app.backlog.domain import (
    OPEN_BACKLOG_STATUSES,
    BacklogDomainError,
    BacklogOrigin,
    BacklogReason,
    BacklogStatus,
    InvalidBacklogTransitionError,
    calculate_remaining_unscheduled_duration,
    validate_backlog_values,
    validate_transition,
)
from app.backlog.repository import (
    add_entry,
    get_entry_for_transition,
    get_open_entry_for_task,
    list_task_sessions,
)
from app.domain.tasks import TaskStatus
from app.models.backlog import BacklogEntry
from app.models.task import Task
from app.services.scheduling import (
    SchedulePreview,
    preview_scheduling_task,
    scheduling_task_from_model,
)


class BacklogEntryNotFoundError(BacklogDomainError):
    pass


class BacklogOwnershipError(BacklogDomainError):
    pass


class BacklogEntryAlreadyExistsError(BacklogDomainError):
    pass


class BacklogPreviewNotAllowedError(BacklogDomainError):
    pass


@dataclass(frozen=True)
class BacklogSchedulePreview:
    entry: BacklogEntry
    remaining_duration_minutes: int
    preview: SchedulePreview


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _lock_owned_task(session: Session, task_id: uuid.UUID, user_id: uuid.UUID) -> Task:
    task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise BacklogEntryNotFoundError("task not found")
    if task.user_id != user_id:
        raise BacklogOwnershipError("task belongs to another user")
    return task


def _validate(entry: BacklogEntry, task: Task) -> None:
    if entry.user_id != task.user_id:
        raise BacklogOwnershipError("backlog entry and task must have the same user")
    validate_backlog_values(
        origin=entry.origin,
        reason=entry.reason,
        note=entry.note,
        status=entry.status,
        remaining_duration_minutes=entry.remaining_duration_minutes,
        task_duration_minutes=task.duration_minutes,
        entered_at=entry.entered_at,
        next_review_at=entry.next_review_at,
        deferred_until=entry.deferred_until,
        resolved_at=entry.resolved_at,
        scheduling_attempt_count=entry.scheduling_attempt_count,
        last_scheduling_attempt_at=entry.last_scheduling_attempt_at,
    )


def _commit_entry(session: Session, entry: BacklogEntry) -> BacklogEntry:
    session.commit()
    session.refresh(entry)
    return entry


def create_backlog_entry(
    session: Session,
    *,
    task_id: uuid.UUID,
    user_id: uuid.UUID,
    origin: BacklogOrigin,
    reason: BacklogReason,
    remaining_duration_minutes: int,
    status: BacklogStatus = BacklogStatus.active,
    entered_at: datetime | None = None,
    next_review_at: datetime | None = None,
    deferred_until: datetime | None = None,
    note: str | None = None,
) -> BacklogEntry:
    task = _lock_owned_task(session, task_id, user_id)
    existing = get_open_entry_for_task(session, task_id, for_update=True)
    effective_entered_at = entered_at or _utc_now()
    if existing is not None:
        equivalent = (
            existing.user_id == user_id
            and existing.reason is reason
            and existing.origin is origin
            and existing.remaining_duration_minutes == remaining_duration_minutes
            and existing.status is status
            and existing.next_review_at == next_review_at
            and existing.deferred_until == deferred_until
            and existing.note == note
        )
        if equivalent:
            return _commit_entry(session, existing)
        raise BacklogEntryAlreadyExistsError(
            "task already has an active or deferred backlog entry"
        )
    entry = BacklogEntry(
        task_id=task.id,
        user_id=user_id,
        origin=origin,
        status=status,
        reason=reason,
        remaining_duration_minutes=remaining_duration_minutes,
        entered_at=effective_entered_at,
        next_review_at=next_review_at,
        deferred_until=deferred_until,
        note=note,
        scheduling_attempt_count=0,
    )
    _validate(entry, task)
    add_entry(session, entry)
    return _commit_entry(session, entry)


def _locked_entry_and_task(
    session: Session, entry_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[BacklogEntry, Task]:
    entry = get_entry_for_transition(session, entry_id, user_id=user_id)
    if entry is None:
        raise BacklogEntryNotFoundError("backlog entry not found")
    task = _lock_owned_task(session, entry.task_id, user_id)
    return entry, task


def defer_backlog_entry(
    session: Session,
    *,
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    deferred_until: datetime | None = None,
    next_review_at: datetime | None = None,
    note: str | None = None,
) -> BacklogEntry:
    entry, task = _locked_entry_and_task(session, entry_id, user_id)
    if entry.status is BacklogStatus.deferred:
        same_dates = (
            entry.deferred_until == deferred_until
            and entry.next_review_at == next_review_at
        )
        if same_dates and (note is None or entry.note == note):
            return _commit_entry(session, entry)
        if same_dates and note is not None:
            entry.note = note
            _validate(entry, task)
            return _commit_entry(session, entry)
        raise BacklogDomainError("deferred dates require an explicit update operation")
    validate_transition(entry.status, BacklogStatus.deferred)
    entry.status = BacklogStatus.deferred
    entry.deferred_until = deferred_until
    entry.next_review_at = next_review_at
    if note is not None:
        entry.note = note
    _validate(entry, task)
    return _commit_entry(session, entry)


def reactivate_backlog_entry(
    session: Session, *, entry_id: uuid.UUID, user_id: uuid.UUID
) -> BacklogEntry:
    entry, task = _locked_entry_and_task(session, entry_id, user_id)
    if entry.status is not BacklogStatus.deferred:
        raise InvalidBacklogTransitionError(
            f"cannot transition backlog entry from {entry.status.value} to active"
        )
    validate_transition(entry.status, BacklogStatus.active)
    entry.status = BacklogStatus.active
    entry.deferred_until = None
    _validate(entry, task)
    return _commit_entry(session, entry)


def update_remaining_duration(
    session: Session,
    *,
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    remaining_duration_minutes: int,
) -> BacklogEntry:
    entry, task = _locked_entry_and_task(session, entry_id, user_id)
    if entry.remaining_duration_minutes == remaining_duration_minutes:
        return _commit_entry(session, entry)
    entry.remaining_duration_minutes = remaining_duration_minutes
    _validate(entry, task)
    return _commit_entry(session, entry)


def resolve_backlog_entry(
    session: Session,
    *,
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    resolved_at: datetime | None = None,
    note: str | None = None,
) -> BacklogEntry:
    entry, task = _locked_entry_and_task(session, entry_id, user_id)
    if entry.status is BacklogStatus.resolved:
        return _commit_entry(session, entry)
    validate_transition(entry.status, BacklogStatus.resolved)
    entry.status = BacklogStatus.resolved
    entry.remaining_duration_minutes = 0
    entry.resolved_at = resolved_at or _utc_now()
    if note is not None:
        entry.note = note
    _validate(entry, task)
    return _commit_entry(session, entry)


def cancel_backlog_entry(
    session: Session,
    *,
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    note: str | None = None,
) -> BacklogEntry:
    entry, task = _locked_entry_and_task(session, entry_id, user_id)
    if entry.status is BacklogStatus.cancelled:
        return _commit_entry(session, entry)
    validate_transition(entry.status, BacklogStatus.cancelled)
    entry.status = BacklogStatus.cancelled
    entry.remaining_duration_minutes = 0
    entry.resolved_at = None
    if note is not None:
        entry.note = note
    _validate(entry, task)
    return _commit_entry(session, entry)


def preview_backlog_entry_schedule(
    session: Session,
    *,
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    planning_window: TimeInterval,
    busy_intervals: tuple[TimeInterval, ...],
) -> BacklogSchedulePreview:
    """Run one explicit stateless retry and persist only attempt metadata."""
    entry, task = _locked_entry_and_task(session, entry_id, user_id)
    if entry.status not in OPEN_BACKLOG_STATUSES:
        raise BacklogPreviewNotAllowedError(
            f"cannot preview a {entry.status.value} backlog entry"
        )
    if task.status in {TaskStatus.cancelled, TaskStatus.completed}:
        raise BacklogPreviewNotAllowedError(
            f"cannot preview a {task.status.value} task"
        )

    remaining = calculate_remaining_unscheduled_duration(
        task.duration_minutes,
        list_task_sessions(session, task_id=task.id),
    )
    if remaining == 0:
        raise BacklogPreviewNotAllowedError(
            "cannot preview a backlog entry with zero remaining duration"
        )

    preview = preview_scheduling_task(
        session=session,
        user_id=user_id,
        planning_window=planning_window,
        busy_intervals=busy_intervals,
        task=scheduling_task_from_model(task, duration_minutes=remaining),
    )
    entry.last_scheduling_attempt_at = _utc_now()
    entry.scheduling_attempt_count += 1
    _validate(entry, task)
    _commit_entry(session, entry)
    return BacklogSchedulePreview(
        entry=entry,
        remaining_duration_minutes=remaining,
        preview=preview,
    )
