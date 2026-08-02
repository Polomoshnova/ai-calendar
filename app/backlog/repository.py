import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.backlog.domain import (
    OPEN_BACKLOG_STATUSES,
    BacklogOrigin,
    BacklogReason,
    BacklogStatus,
)
from app.models.backlog import BacklogEntry
from app.schedule_plans.models import ScheduledSession


def get_entry(
    session: Session, entry_id: uuid.UUID, *, user_id: uuid.UUID
) -> BacklogEntry | None:
    return session.scalar(
        select(BacklogEntry).where(
            BacklogEntry.id == entry_id, BacklogEntry.user_id == user_id
        )
    )


def get_open_entry_for_task(
    session: Session, task_id: uuid.UUID, *, for_update: bool = False
) -> BacklogEntry | None:
    statement = select(BacklogEntry).where(
        BacklogEntry.task_id == task_id,
        BacklogEntry.status.in_(OPEN_BACKLOG_STATUSES),
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def get_entry_for_transition(
    session: Session, entry_id: uuid.UUID, *, user_id: uuid.UUID
) -> BacklogEntry | None:
    return session.scalar(
        select(BacklogEntry)
        .where(BacklogEntry.id == entry_id, BacklogEntry.user_id == user_id)
        .with_for_update()
    )


def list_open_entries_for_user(
    session: Session, user_id: uuid.UUID
) -> list[BacklogEntry]:
    return list(
        session.scalars(
            select(BacklogEntry)
            .where(
                BacklogEntry.user_id == user_id,
                BacklogEntry.status.in_(OPEN_BACKLOG_STATUSES),
            )
            .order_by(BacklogEntry.entered_at, BacklogEntry.id)
        )
    )


def list_backlog_entries(
    session: Session,
    *,
    user_id: uuid.UUID,
    status: BacklogStatus | None = None,
    reason: BacklogReason | None = None,
    origin: BacklogOrigin | None = None,
    due_only: bool = False,
    due_at: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[BacklogEntry]:
    review_at = func.least(BacklogEntry.next_review_at, BacklogEntry.deferred_until)
    statement = select(BacklogEntry).where(BacklogEntry.user_id == user_id)
    if status is None:
        statement = statement.where(BacklogEntry.status.in_(OPEN_BACKLOG_STATUSES))
    else:
        statement = statement.where(BacklogEntry.status == status)
    if reason is not None:
        statement = statement.where(BacklogEntry.reason == reason)
    if origin is not None:
        statement = statement.where(BacklogEntry.origin == origin)
    if due_only:
        if due_at is None:
            raise ValueError("due_at is required when due_only is true")
        statement = statement.where(
            or_(
                BacklogEntry.next_review_at <= due_at,
                BacklogEntry.deferred_until <= due_at,
            )
        )
    return list(
        session.scalars(
            statement.order_by(
                review_at.asc().nulls_last(),
                BacklogEntry.entered_at,
                BacklogEntry.id,
            )
            .limit(limit)
            .offset(offset)
        )
    )


def list_task_sessions(
    session: Session, *, task_id: uuid.UUID
) -> list[ScheduledSession]:
    return list(
        session.scalars(
            select(ScheduledSession)
            .where(ScheduledSession.task_id == task_id)
            .options(selectinload(ScheduledSession.plan))
            .order_by(ScheduledSession.start, ScheduledSession.id)
        )
    )


def list_entries_due_for_review(
    session: Session, *, user_id: uuid.UUID, due_at: datetime
) -> list[BacklogEntry]:
    return list(
        session.scalars(
            select(BacklogEntry)
            .where(
                BacklogEntry.user_id == user_id,
                BacklogEntry.status.in_(OPEN_BACKLOG_STATUSES),
                or_(
                    BacklogEntry.next_review_at <= due_at,
                    BacklogEntry.deferred_until <= due_at,
                ),
            )
            .order_by(
                BacklogEntry.next_review_at.asc().nulls_last(),
                BacklogEntry.deferred_until.asc().nulls_last(),
                BacklogEntry.entered_at,
                BacklogEntry.id,
            )
        )
    )


def add_entry(session: Session, entry: BacklogEntry) -> None:
    session.add(entry)
