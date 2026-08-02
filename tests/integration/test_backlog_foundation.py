import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.backlog.domain import (
    BacklogDomainError,
    BacklogOrigin,
    BacklogReason,
    BacklogStatus,
    InvalidBacklogTransitionError,
)
from app.backlog.repository import (
    get_open_entry_for_task,
    list_entries_due_for_review,
    list_open_entries_for_user,
)
from app.backlog.service import (
    BacklogOwnershipError,
    cancel_backlog_entry,
    create_backlog_entry,
    defer_backlog_entry,
    reactivate_backlog_entry,
    resolve_backlog_entry,
    update_remaining_duration,
)
from app.domain.tasks import TaskStatus
from app.models import BacklogEntry, Task, User

NOW = datetime(2026, 8, 2, 10, tzinfo=UTC)


def make_task(db_session: Session, user: User, *, duration: int = 120) -> Task:
    task = Task(
        user_id=user.id,
        title="Backlog task",
        duration_minutes=duration,
        status=TaskStatus.pending,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


def create(
    db_session: Session, user: User, task: Task, **kwargs: object
) -> BacklogEntry:
    values: dict[str, object] = {
        "task_id": task.id,
        "user_id": user.id,
        "origin": BacklogOrigin.scheduler,
        "reason": BacklogReason.no_available_slot,
        "remaining_duration_minutes": task.duration_minutes,
        "entered_at": NOW,
    }
    values.update(kwargs)
    return create_backlog_entry(db_session, **values)  # type: ignore[arg-type]


def test_creation_validation_ownership_and_idempotency(
    db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    entry = create(db_session, user, task)
    repeated = create(db_session, user, task)
    assert repeated.id == entry.id
    invalid_task = make_task(db_session, user)
    with pytest.raises(BacklogDomainError, match="cannot exceed"):
        create_backlog_entry(
            db_session,
            task_id=invalid_task.id,
            user_id=user.id,
            origin=BacklogOrigin.scheduler,
            reason=BacklogReason.no_available_slot,
            remaining_duration_minutes=121,
        )
    other = User(email="other@example.com", timezone="UTC")
    db_session.add(other)
    db_session.commit()
    with pytest.raises(BacklogOwnershipError):
        create_backlog_entry(
            db_session,
            task_id=task.id,
            user_id=other.id,
            origin=BacklogOrigin.scheduler,
            reason=BacklogReason.no_available_slot,
            remaining_duration_minutes=120,
        )


def test_origin_is_required_and_other_requires_explanation(
    db_session: Session, user: User
) -> None:
    missing_origin_task = make_task(db_session, user)
    db_session.add(
        BacklogEntry(
            task_id=missing_origin_task.id,
            user_id=user.id,
            status=BacklogStatus.active,
            reason=BacklogReason.no_deadline,
            remaining_duration_minutes=120,
            entered_at=NOW,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    other_task = make_task(db_session, user)
    with pytest.raises(BacklogDomainError, match="meaningful note"):
        create(
            db_session,
            user,
            other_task,
            origin=BacklogOrigin.system,
            reason=BacklogReason.other,
        )
    db_session.rollback()
    entry = create(
        db_session,
        user,
        other_task,
        origin=BacklogOrigin.system,
        reason=BacklogReason.other,
        note="Needs classification by the planner workflow",
    )
    assert entry.note == "Needs classification by the planner workflow"


def test_lifecycle_and_new_entry_after_history(db_session: Session, user: User) -> None:
    task = make_task(db_session, user)
    entry = create(db_session, user, task)
    deferred = defer_backlog_entry(
        db_session,
        entry_id=entry.id,
        user_id=user.id,
        deferred_until=NOW + timedelta(days=1),
    )
    assert deferred.status is BacklogStatus.deferred
    active = reactivate_backlog_entry(db_session, entry_id=entry.id, user_id=user.id)
    assert active.status is BacklogStatus.active
    update_remaining_duration(
        db_session, entry_id=entry.id, user_id=user.id, remaining_duration_minutes=60
    )
    resolved = resolve_backlog_entry(db_session, entry_id=entry.id, user_id=user.id)
    assert resolved.status is BacklogStatus.resolved
    assert resolved.remaining_duration_minutes == 0
    assert resolved.resolved_at is not None
    assert (
        resolve_backlog_entry(db_session, entry_id=entry.id, user_id=user.id).id
        == entry.id
    )
    with pytest.raises(InvalidBacklogTransitionError):
        reactivate_backlog_entry(db_session, entry_id=entry.id, user_id=user.id)
    replacement = create(
        db_session,
        user,
        task,
        origin=BacklogOrigin.user,
        reason=BacklogReason.manual_defer,
    )
    assert replacement.id != entry.id
    cancelled = cancel_backlog_entry(
        db_session, entry_id=replacement.id, user_id=user.id
    )
    assert cancelled.status is BacklogStatus.cancelled
    with pytest.raises(InvalidBacklogTransitionError):
        reactivate_backlog_entry(db_session, entry_id=cancelled.id, user_id=user.id)


@pytest.mark.parametrize("terminal", [BacklogStatus.resolved, BacklogStatus.cancelled])
def test_deferred_entries_can_be_closed(
    db_session: Session, user: User, terminal: BacklogStatus
) -> None:
    task = make_task(db_session, user)
    entry = create(
        db_session,
        user,
        task,
        status=BacklogStatus.deferred,
        deferred_until=NOW + timedelta(days=1),
    )
    if terminal is BacklogStatus.resolved:
        result = resolve_backlog_entry(db_session, entry_id=entry.id, user_id=user.id)
    else:
        result = cancel_backlog_entry(db_session, entry_id=entry.id, user_id=user.id)
    assert result.status is terminal


def test_repository_listing_due_order_and_isolation(
    db_session: Session, user: User
) -> None:
    first_task = make_task(db_session, user)
    second_task = make_task(db_session, user)
    first = create(
        db_session,
        user,
        first_task,
        entered_at=NOW,
        next_review_at=NOW - timedelta(hours=2),
    )
    second = create(
        db_session,
        user,
        second_task,
        status=BacklogStatus.deferred,
        entered_at=NOW + timedelta(minutes=1),
        deferred_until=NOW - timedelta(hours=1),
    )
    assert get_open_entry_for_task(db_session, first_task.id).id == first.id  # type: ignore[union-attr]
    assert [item.id for item in list_open_entries_for_user(db_session, user.id)] == [
        first.id,
        second.id,
    ]
    assert [
        item.id
        for item in list_entries_due_for_review(db_session, user_id=user.id, due_at=NOW)
    ] == [first.id, second.id]
    stranger = User(email="isolated@example.com", timezone="UTC")
    db_session.add(stranger)
    db_session.commit()
    assert list_open_entries_for_user(db_session, stranger.id) == []


def test_database_partial_uniqueness(db_session: Session, user: User) -> None:
    task = make_task(db_session, user)
    create(db_session, user, task)
    db_session.add(
        BacklogEntry(
            task_id=task.id,
            user_id=user.id,
            origin=BacklogOrigin.scheduler,
            status=BacklogStatus.active,
            reason=BacklogReason.other,
            note="Duplicate for constraint test",
            remaining_duration_minutes=1,
            entered_at=NOW,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_concurrent_creation_returns_one_open_entry(
    db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    session_factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)

    def worker() -> uuid.UUID:
        with session_factory() as session:
            return create_backlog_entry(
                session,
                task_id=task.id,
                user_id=user.id,
                origin=BacklogOrigin.scheduler,
                reason=BacklogReason.no_available_slot,
                remaining_duration_minutes=120,
                entered_at=NOW,
            ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: worker(), range(2)))
    assert ids[0] == ids[1]
    assert len(list(db_session.scalars(select(BacklogEntry)))) == 1
