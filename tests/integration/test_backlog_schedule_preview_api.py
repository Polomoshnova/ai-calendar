import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.backlog.domain import BacklogOrigin, BacklogReason, BacklogStatus
from app.backlog.service import (
    cancel_backlog_entry,
    create_backlog_entry,
    resolve_backlog_entry,
)
from app.calendar_integration.google.client import GoogleCalendarProvider
from app.core.config import get_settings
from app.domain.tasks import TaskStatus
from app.models import ScheduledSession, SchedulePlan, Task, User
from app.schedule_plans.models import (
    ScheduledSessionStatus,
    SchedulePlanSource,
    SchedulePlanStatus,
)

WINDOW_START = datetime(2026, 8, 18, 8, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 18, 18, tzinfo=UTC)


@pytest.fixture(autouse=True)
def enable_internal_tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_task(
    session: Session,
    user: User,
    *,
    duration: int = 60,
    status: TaskStatus = TaskStatus.pending,
) -> Task:
    user.timezone = "UTC"
    task = Task(
        user_id=user.id,
        title="Retry this task",
        duration_minutes=duration,
        status=status,
        is_splittable=True,
        minimum_session_minutes=15,
        maximum_sessions_per_day=4,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def make_entry(
    session: Session,
    user: User,
    task: Task,
    *,
    status: BacklogStatus = BacklogStatus.active,
) -> Any:
    return create_backlog_entry(
        session,
        task_id=task.id,
        user_id=user.id,
        origin=(
            BacklogOrigin.user
            if status is BacklogStatus.deferred
            else BacklogOrigin.scheduler
        ),
        reason=(
            BacklogReason.manual_defer
            if status is BacklogStatus.deferred
            else BacklogReason.no_available_slot
        ),
        status=status,
        remaining_duration_minutes=task.duration_minutes,
        deferred_until=(
            WINDOW_START + timedelta(days=1)
            if status is BacklogStatus.deferred
            else None
        ),
    )


def request_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "planning_window": {
            "start": WINDOW_START.isoformat(),
            "end": WINDOW_END.isoformat(),
        },
        "busy_intervals": [],
    }
    payload.update(overrides)
    return payload


def post_preview(
    client: TestClient,
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    **overrides: Any,
):
    return client.post(
        f"/internal/api/backlog/{entry_id}/schedule-preview",
        params={"user_id": str(user_id)},
        json=request_payload(**overrides),
    )


def add_reserving_session(
    session: Session,
    user: User,
    task: Task,
    *,
    start: datetime,
    minutes: int,
) -> SchedulePlan:
    plan = SchedulePlan(
        user_id=user.id,
        task_id=task.id,
        plan_group_id=uuid.uuid4(),
        source=SchedulePlanSource.manual_preview,
        version=1,
        status=SchedulePlanStatus.confirmed,
        timezone="UTC",
        planning_window_start=WINDOW_START,
        planning_window_end=WINDOW_END,
        scheduler_version="test",
        idempotency_key=str(uuid.uuid4()),
        confirmed_task_snapshot={},
        scheduling_preferences_snapshot={},
        busy_context_summary={},
        preview_metadata={},
    )
    plan.sessions.append(
        ScheduledSession(
            task_id=task.id,
            title=task.title,
            start=start,
            end=start + timedelta(minutes=minutes),
            duration_minutes=minutes,
            order=1,
            status=ScheduledSessionStatus.confirmed,
        )
    )
    session.add(plan)
    session.commit()
    return plan


def test_active_preview_uses_remaining_duration_reservations_and_tracks_attempts(
    client: TestClient,
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid_google_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("preview must not create Google events")

    monkeypatch.setattr(GoogleCalendarProvider, "create_event", forbid_google_write)
    task = make_task(db_session, user, duration=120)
    entry = make_entry(db_session, user, task)
    add_reserving_session(
        db_session, user, task, start=WINDOW_START + timedelta(hours=1), minutes=40
    )
    plans_before = db_session.scalar(select(func.count()).select_from(SchedulePlan))

    first = post_preview(client, entry.id, user.id)
    second = post_preview(client, entry.id, user.id)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["remaining_duration_minutes"] == 80
    assert first.json()["schedule_preview"]["scheduled_blocks"][0]["start"] == (
        "2026-08-18T09:40:00Z"
    )
    assert first.json()["scheduling_attempt_count"] == 1
    assert second.json()["scheduling_attempt_count"] == 2
    assert db_session.scalar(select(func.count()).select_from(SchedulePlan)) == (
        plans_before
    )
    db_session.refresh(entry)
    assert entry.status is BacklogStatus.active
    assert entry.last_scheduling_attempt_at is not None
    assert entry.scheduling_attempt_count == 2


def test_deferred_entry_can_be_previewed_without_transition(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task, status=BacklogStatus.deferred)

    response = post_preview(client, entry.id, user.id)

    assert response.status_code == 200
    db_session.refresh(entry)
    assert entry.status is BacklogStatus.deferred


@pytest.mark.parametrize(
    "terminal_status", [BacklogStatus.resolved, BacklogStatus.cancelled]
)
def test_terminal_entry_is_rejected(
    terminal_status: BacklogStatus,
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)
    if terminal_status is BacklogStatus.resolved:
        resolve_backlog_entry(db_session, entry_id=entry.id, user_id=user.id)
    else:
        cancel_backlog_entry(db_session, entry_id=entry.id, user_id=user.id)

    response = post_preview(client, entry.id, user.id)

    assert response.status_code == 409


@pytest.mark.parametrize("task_status", [TaskStatus.completed, TaskStatus.cancelled])
def test_terminal_task_is_rejected(
    task_status: TaskStatus,
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)
    task.status = task_status
    db_session.commit()

    response = post_preview(client, entry.id, user.id)

    assert response.status_code == 409


def test_another_users_entry_is_not_found(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)
    other = User(email="preview-other@example.com", timezone="UTC")
    db_session.add(other)
    db_session.commit()

    response = post_preview(client, entry.id, other.id)

    assert response.status_code == 404


def test_zero_remaining_duration_is_rejected(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user, duration=60)
    entry = make_entry(db_session, user, task)
    add_reserving_session(
        db_session, user, task, start=WINDOW_START + timedelta(hours=1), minutes=60
    )

    response = post_preview(client, entry.id, user.id)

    assert response.status_code == 409


def test_busy_window_returns_valid_unscheduled_result(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)

    response = post_preview(
        client,
        entry.id,
        user.id,
        busy_intervals=[
            {"start": WINDOW_START.isoformat(), "end": WINDOW_END.isoformat()}
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schedule_preview"]["scheduled_blocks"] == []
    assert body["unscheduled_reason"] is not None
    assert body["scheduling_attempt_count"] == 1


def test_invalid_context_does_not_record_attempt(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)

    response = post_preview(
        client,
        entry.id,
        user.id,
        planning_window={
            "start": WINDOW_END.isoformat(),
            "end": WINDOW_START.isoformat(),
        },
    )

    assert response.status_code == 422
    db_session.refresh(entry)
    assert entry.scheduling_attempt_count == 0
    assert entry.last_scheduling_attempt_at is None


def test_internal_tools_gate_is_enforced(
    client: TestClient,
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()

    response = post_preview(client, entry.id, user.id)

    assert response.status_code == 404


def test_openapi_describes_backlog_schedule_preview(client: TestClient) -> None:
    path = client.get("/openapi.json").json()["paths"][
        "/internal/api/backlog/{entry_id}/schedule-preview"
    ]["post"]
    assert path["summary"] == "Preview backlog entry scheduling"
    example = path["requestBody"]["content"]["application/json"]["schema"]
    assert example["$ref"].endswith("BacklogSchedulePreviewRequest")
