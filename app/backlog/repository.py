import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.backlog.domain import OPEN_BACKLOG_STATUSES
from app.models.backlog import BacklogEntry


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
