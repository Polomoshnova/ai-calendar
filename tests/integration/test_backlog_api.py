import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.backlog.domain import BacklogOrigin, BacklogReason, BacklogStatus
from app.backlog.service import create_backlog_entry
from app.calendar_integration.google.client import GoogleCalendarProvider
from app.core.config import get_settings
from app.models import ScheduledSession, SchedulePlan, Task, User
from app.schedule_plans.models import (
    ScheduledSessionStatus,
    SchedulePlanSource,
    SchedulePlanStatus,
)
from app.scheduling import scheduler

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def enable_internal_tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_task(db_session: Session, user: User, *, duration: int = 120) -> Task:
    task = Task(user_id=user.id, title="Planner task", duration_minutes=duration)
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


def payload(user: User, task: Task, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "user_id": str(user.id),
        "task_id": str(task.id),
        "origin": "scheduler",
        "reason": "no_available_slot",
        "remaining_duration_minutes": task.duration_minutes,
    }
    value.update(overrides)
    return value


def post_create(client: TestClient, user: User, task: Task, **overrides: Any):
    return client.post("/internal/api/backlog", json=payload(user, task, **overrides))


def add_applied_session(
    db_session: Session, user: User, task: Task, *, minutes: int
) -> None:
    plan = SchedulePlan(
        user_id=user.id,
        task_id=task.id,
        plan_group_id=uuid.uuid4(),
        source=SchedulePlanSource.manual_preview,
        version=1,
        status=SchedulePlanStatus.confirmed,
        timezone="UTC",
        planning_window_start=NOW,
        planning_window_end=NOW + timedelta(days=1),
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
            start=NOW,
            end=NOW + timedelta(minutes=minutes),
            duration_minutes=minutes,
            order=1,
            status=ScheduledSessionStatus.confirmed,
        )
    )
    db_session.add(plan)
    db_session.commit()


def test_create_get_idempotency_and_ownership(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    first = post_create(client, user, task)
    second = post_create(client, user, task)
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    entry_id = first.json()["id"]
    read = client.get(
        f"/internal/api/backlog/{entry_id}", params={"user_id": str(user.id)}
    )
    assert read.status_code == 200
    assert read.json()["task_id"] == str(task.id)

    other = User(email="backlog-other@example.com", timezone="UTC")
    db_session.add(other)
    db_session.commit()
    assert (
        client.get(
            f"/internal/api/backlog/{entry_id}",
            params={"user_id": str(other.id)},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/internal/api/backlog/{entry_id}/defer",
            params={"user_id": str(other.id)},
            json={"next_review_at": (NOW + timedelta(days=1)).isoformat()},
        ).status_code
        == 404
    )
    assert post_create(client, other, task).status_code == 404


def test_omitted_remaining_duration_uses_reserving_sessions(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user, duration=100)
    add_applied_session(db_session, user, task, minutes=40)
    response = post_create(
        client,
        user,
        task,
        remaining_duration_minutes=None,
        reason="partially_scheduled",
    )
    assert response.status_code == 201
    assert response.json()["remaining_duration_minutes"] == 60


def test_conflicting_creation_and_other_validation(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    assert post_create(client, user, task).status_code == 201
    conflict = post_create(client, user, task, reason="insufficient_capacity")
    assert conflict.status_code == 409

    other_task = make_task(db_session, user)
    missing_note = post_create(
        client,
        user,
        other_task,
        origin="system",
        reason="other",
    )
    assert missing_note.status_code == 422
    accepted = post_create(
        client,
        user,
        other_task,
        origin="system",
        reason="other",
        note="Needs manual classification",
    )
    assert accepted.status_code == 201
    assert accepted.json()["note"] == "Needs manual classification"
    assert (
        post_create(
            client, user, make_task(db_session, user), reason="calendar_unavailable"
        ).status_code
        == 422
    )


def test_list_filters_due_order_and_pagination(
    client: TestClient, db_session: Session, user: User
) -> None:
    first_task = make_task(db_session, user)
    second_task = make_task(db_session, user)
    third_task = make_task(db_session, user)
    first = create_backlog_entry(
        db_session,
        task_id=first_task.id,
        user_id=user.id,
        origin=BacklogOrigin.scheduler,
        reason=BacklogReason.no_available_slot,
        remaining_duration_minutes=120,
        entered_at=NOW,
        next_review_at=NOW - timedelta(hours=2),
    )
    second = create_backlog_entry(
        db_session,
        task_id=second_task.id,
        user_id=user.id,
        origin=BacklogOrigin.user,
        reason=BacklogReason.manual_defer,
        status=BacklogStatus.deferred,
        remaining_duration_minutes=120,
        entered_at=NOW + timedelta(minutes=1),
        deferred_until=NOW - timedelta(hours=1),
    )
    create_backlog_entry(
        db_session,
        task_id=third_task.id,
        user_id=user.id,
        origin=BacklogOrigin.system,
        reason=BacklogReason.awaiting_user_confirmation,
        remaining_duration_minutes=120,
        entered_at=NOW + timedelta(minutes=2),
    )

    listed = client.get(
        "/internal/api/backlog", params={"user_id": str(user.id), "due_only": True}
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [str(first.id), str(second.id)]
    default_list = client.get("/internal/api/backlog", params={"user_id": str(user.id)})
    assert {item["status"] for item in default_list.json()} == {"active", "deferred"}
    filtered = client.get(
        "/internal/api/backlog",
        params={
            "user_id": str(user.id),
            "status": "deferred",
            "reason": "manual_defer",
            "origin": "user",
        },
    )
    assert [item["id"] for item in filtered.json()] == [str(second.id)]
    page = client.get(
        "/internal/api/backlog",
        params={"user_id": str(user.id), "limit": 1, "offset": 1},
    )
    assert page.status_code == 200
    assert page.json()[0]["id"] == str(second.id)


def test_lifecycle_endpoints(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    created = post_create(client, user, task).json()
    entry_id = created["id"]
    assert (
        client.post(
            f"/internal/api/backlog/{entry_id}/defer",
            params={"user_id": str(user.id)},
            json={},
        ).status_code
        == 422
    )
    deferred = client.post(
        f"/internal/api/backlog/{entry_id}/defer",
        params={"user_id": str(user.id)},
        json={"deferred_until": (NOW + timedelta(days=1)).isoformat(), "note": "Later"},
    )
    assert deferred.status_code == 200
    assert deferred.json()["status"] == "deferred"
    assert deferred.json()["note"] == "Later"
    reactivated = client.post(
        f"/internal/api/backlog/{entry_id}/reactivate",
        params={"user_id": str(user.id)},
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "active"
    assert (
        client.post(
            f"/internal/api/backlog/{entry_id}/reactivate",
            params={"user_id": str(user.id)},
        ).status_code
        == 409
    )
    resolved = client.post(
        f"/internal/api/backlog/{entry_id}/resolve",
        params={"user_id": str(user.id)},
        json={"note": "Scheduled manually"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["remaining_duration_minutes"] == 0
    assert (
        client.post(
            f"/internal/api/backlog/{entry_id}/reactivate",
            params={"user_id": str(user.id)},
        ).status_code
        == 409
    )

    cancelled_task = make_task(db_session, user)
    cancelled_id = post_create(client, user, cancelled_task).json()["id"]
    cancelled = client.post(
        f"/internal/api/backlog/{cancelled_id}/cancel",
        params={"user_id": str(user.id)},
        json={"note": "No longer tracked"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_openapi_has_typed_backlog_contract(client: TestClient) -> None:
    document = client.get("/openapi.json").json()
    paths = document["paths"]
    assert paths["/internal/api/backlog"]["get"]["summary"] == ("List backlog entries")
    assert paths["/internal/api/backlog"]["post"]["summary"] == (
        "Create a backlog entry"
    )
    create_schema = document["components"]["schemas"]["BacklogEntryCreateRequest"]
    assert create_schema["examples"][0]["origin"] == "scheduler"
    response_schema = document["components"]["schemas"]["BacklogEntryResponse"]
    assert {"status", "origin", "reason", "remaining_duration_minutes"} <= set(
        response_schema["required"]
    )


def test_gate_and_no_external_calls(
    client: TestClient,
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external component must not be called")

    monkeypatch.setattr(scheduler, "schedule_tasks", forbidden)
    monkeypatch.setattr(GoogleCalendarProvider, "create_event", forbidden)
    task = make_task(db_session, user)
    assert post_create(client, user, task).status_code == 201

    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()
    response = client.get("/internal/api/backlog", params={"user_id": str(user.id)})
    assert response.status_code == 404
