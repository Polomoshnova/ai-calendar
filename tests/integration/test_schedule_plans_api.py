import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.calendar_sync import (
    BusySourceSnapshot,
    SessionWriteTargetSnapshot,
    calendar_context_hash,
)
from app.core.config import get_settings
from app.models import (
    CalendarConnection,
    CalendarProviderName,
    CalendarSelection,
    User,
)
from app.schedule_plans.errors import SchedulePlanImmutableError
from app.schedule_plans.models import (
    ScheduledSession,
    ScheduledSessionStatus,
    SchedulePlan,
    SchedulePlanStatus,
)


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 27, hour, minute, tzinfo=UTC)


def plan_payload(
    user_id: uuid.UUID,
    *,
    idempotency_key: str = "schedule-plan-test-1",
    plan_group_id: str | None = None,
    block_start: datetime | None = None,
) -> dict[str, Any]:
    start = block_start or dt(9)
    payload: dict[str, Any] = {
        "user_id": str(user_id),
        "confirmed_task": {
            "title": "Prepare report",
            "description": "Draft and review the report.",
            "duration_minutes": 90,
            "priority": "medium",
            "earliest_start": None,
            "deadline": None,
            "preferred_time_of_day": "morning",
            "is_splittable": True,
            "minimum_session_minutes": 30,
            "maximum_sessions_per_day": 2,
            "steps": [],
        },
        "schedule_preview": {
            "scheduler_version": "2a.1",
            "planning_window": {
                "start": dt(8).isoformat(),
                "end": dt(18).isoformat(),
            },
            "free_intervals": [],
            "scheduled_blocks": [
                {
                    "task_id": "confirmed-task",
                    "start": (start + (dt(10) - dt(9))).isoformat(),
                    "end": (start + (dt(10, 30) - dt(9))).isoformat(),
                    "reason_codes": ["only_available_slot"],
                    "score_components": [{"name": "fit", "value": 5}],
                },
                {
                    "task_id": "confirmed-task",
                    "start": start.isoformat(),
                    "end": (start + (dt(9, 30) - dt(9))).isoformat(),
                    "reason_codes": ["before_deadline"],
                    "score_components": [{"name": "priority", "value": 10}],
                },
            ],
            "unscheduled_tasks": [
                {
                    "task_id": "confirmed-task",
                    "remaining_minutes": 30,
                    "reason_code": "insufficient_free_time",
                }
            ],
            "warnings": [],
        },
        "planning_context": {
            "timezone": "Europe/Warsaw",
            "planning_window_start": dt(8).isoformat(),
            "planning_window_end": dt(18).isoformat(),
            "source_calendar_snapshot_at": dt(7, 55).isoformat(),
            "scheduler_version": "2a.1",
            "workflow_version": "task-to-schedule-preview.v1",
            "calendar_context": {
                "provider": "google",
                "calendar_ids": ["primary"],
                "provider_busy_interval_count": 4,
                "merged_busy_interval_count": 3,
                "queried_at": dt(7, 55).isoformat(),
            },
            "preferences_snapshot": {
                "timezone": "Europe/Warsaw",
                "minimum_break_minutes": 15,
                "provenance": {"timezone": "stored_user"},
            },
        },
        "source": "calendar_backed_preview",
        "confirmation_note": None,
        "idempotency_key": idempotency_key,
    }
    if plan_group_id is not None:
        payload["plan_group_id"] = plan_group_id
    return payload


@pytest.fixture(autouse=True)
def enable_internal_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def ensure_calendar_connection(
    db_session: Session, user_id: uuid.UUID
) -> CalendarConnection:
    connection = db_session.scalar(
        select(CalendarConnection).where(CalendarConnection.user_id == user_id)
    )
    if connection is None:
        connection = CalendarConnection(
            id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
            user_id=user_id,
            provider=CalendarProviderName.google,
            provider_account_id="google-account-1",
        )
        connection.selections.append(
            CalendarSelection(
                external_calendar_id="primary",
                display_name="Primary",
                primary=True,
                include_in_availability=True,
            )
        )
        db_session.add(connection)
        db_session.commit()
    return connection


def create_plan(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    **kwargs: Any,
) -> dict[str, Any]:
    ensure_calendar_connection(db_session, user_id)
    response = client.post(
        "/internal/api/schedule-plans/from-preview",
        json=plan_payload(user_id, **kwargs),
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def test_create_plan_persists_sessions_and_safe_snapshots(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    user_id = user.id
    body = create_plan(client, db_session, user_id)

    assert body["status"] == "proposed"
    assert body["version"] == 1
    assert [item["order"] for item in body["sessions"]] == [1, 2]
    assert [item["start"] for item in body["sessions"]] == [
        dt(9).isoformat().replace("+00:00", "Z"),
        dt(10).isoformat().replace("+00:00", "Z"),
    ]
    assert [item["duration_minutes"] for item in body["sessions"]] == [30, 30]

    plan = db_session.get(SchedulePlan, uuid.UUID(body["id"]))
    assert plan is not None
    assert plan.confirmed_task_snapshot["title"] == "Prepare report"
    assert "confidence" not in str(plan.confirmed_task_snapshot).lower()
    assert plan.scheduling_preferences_snapshot["provenance"] == {
        "timezone": "stored_user"
    }
    assert plan.busy_context_summary["provider"] == "google"
    assert "event" not in str(plan.busy_context_summary).lower()
    assert plan.preview_metadata["unscheduled_tasks"][0]["remaining_minutes"] == 30
    assert plan.scheduler_version == "2a.1"
    assert plan.workflow_version == "task-to-schedule-preview.v1"
    assert plan.busy_sources_snapshot is not None
    assert plan.write_targets_snapshot is not None
    assert plan.calendar_context_captured_at is not None
    assert {item["calendar_id"] for item in plan.busy_sources_snapshot} == {"primary"}
    assert {item["scheduled_session_id"] for item in plan.write_targets_snapshot} == {
        str(item.id) for item in plan.sessions
    }
    busy_sources = [
        BusySourceSnapshot.model_validate(item) for item in plan.busy_sources_snapshot
    ]
    write_targets = [
        SessionWriteTargetSnapshot.model_validate(item)
        for item in plan.write_targets_snapshot
    ]
    assert plan.calendar_selection_hash == calendar_context_hash(
        busy_sources=busy_sources,
        write_targets=write_targets,
    )


def test_legacy_plan_with_null_calendar_snapshots_does_not_crash(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    created = create_plan(client, db_session, user.id)
    plan = db_session.get(SchedulePlan, uuid.UUID(created["id"]))
    assert plan is not None
    plan.busy_sources_snapshot = None
    plan.write_targets_snapshot = None
    plan.calendar_selection_hash = None
    plan.calendar_context_captured_at = None
    db_session.commit()

    response = client.get(f"/internal/api/schedule-plans/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_plan_and_sessions_are_persisted_atomically(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    ensure_calendar_connection(db_session, user.id)

    def fail_flush(
        _session: Session,
        _flush_context: object,
        _instances: object,
    ) -> None:
        raise IntegrityError(
            "forced session persistence failure",
            params={},
            orig=Exception("forced failure"),
        )

    event.listen(db_session, "before_flush", fail_flush, once=True)
    with pytest.raises(IntegrityError, match="forced session persistence failure"):
        client.post(
            "/internal/api/schedule-plans/from-preview",
            json=plan_payload(user.id, idempotency_key="atomic-failure"),
        )

    assert db_session.scalar(select(func.count()).select_from(SchedulePlan)) == 0
    assert db_session.scalar(select(func.count()).select_from(ScheduledSession)) == 0


def test_create_is_idempotent_without_duplicate_sessions(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    user_id = user.id
    first = create_plan(client, db_session, user_id)
    second = create_plan(client, db_session, user_id)

    assert second["id"] == first["id"]
    assert db_session.scalar(select(func.count()).select_from(SchedulePlan)) == 1
    assert db_session.scalar(select(func.count()).select_from(ScheduledSession)) == 2


def test_confirm_is_idempotent_and_makes_content_immutable(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    created = create_plan(client, db_session, user.id)
    path = f"/internal/api/schedule-plans/{created['id']}/confirm"

    first = client.post(path, json={"confirmation_note": "Approved exact blocks."})
    second = client.post(path, json={"confirmation_note": "Ignored on retry."})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "confirmed"
    assert first.json()["confirmed_at"]
    assert first.json()["confirmation_note"] == "Approved exact blocks."
    assert {item["status"] for item in first.json()["sessions"]} == {"confirmed"}
    assert second.json()["confirmed_at"] == first.json()["confirmed_at"]
    assert second.json()["confirmation_note"] == "Approved exact blocks."

    scheduled_session = db_session.scalar(select(ScheduledSession))
    assert scheduled_session is not None
    scheduled_session.title = "Mutated title"
    with pytest.raises(SchedulePlanImmutableError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize("initial_status", ["proposed", "confirmed"])
def test_obsolete_is_idempotent_and_blocks_confirmation(
    client: TestClient,
    db_session: Session,
    user: User,
    initial_status: str,
) -> None:
    created = create_plan(client, db_session, user.id)
    if initial_status == "confirmed":
        response = client.post(
            f"/internal/api/schedule-plans/{created['id']}/confirm",
            json={},
        )
        assert response.status_code == 200
    path = f"/internal/api/schedule-plans/{created['id']}/obsolete"

    first = client.post(path)
    second = client.post(path)
    confirmation = client.post(
        f"/internal/api/schedule-plans/{created['id']}/confirm",
        json={},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "obsolete"
    assert {item["status"] for item in first.json()["sessions"]} == {"obsolete"}
    assert confirmation.status_code == 409


@pytest.mark.parametrize("operation", ["confirm", "obsolete"])
def test_applied_plan_rejects_exposed_transitions(
    client: TestClient,
    db_session: Session,
    user: User,
    operation: str,
) -> None:
    created = create_plan(client, db_session, user.id)
    plan = db_session.get(SchedulePlan, uuid.UUID(created["id"]))
    assert plan is not None
    plan.status = SchedulePlanStatus.applied
    for item in plan.sessions:
        item.status = ScheduledSessionStatus.applied
    db_session.commit()

    response = client.post(
        f"/internal/api/schedule-plans/{created['id']}/{operation}",
        json={} if operation == "confirm" else None,
    )

    assert response.status_code == 409


def test_revised_plan_increments_version_and_obsoletes_previous(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    user_id = user.id
    first = create_plan(client, db_session, user_id, idempotency_key="revision-1")
    second = create_plan(
        client,
        db_session,
        user_id,
        idempotency_key="revision-2",
        plan_group_id=first["plan_group_id"],
        block_start=dt(11),
    )
    previous = client.get(f"/internal/api/schedule-plans/{first['id']}")

    assert second["plan_group_id"] == first["plan_group_id"]
    assert second["version"] == 2
    assert second["status"] == "proposed"
    assert previous.json()["status"] == "obsolete"


def test_get_and_filtered_list_return_provider_neutral_plans(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    user_id = user.id
    created = create_plan(client, db_session, user_id)
    fetched = client.get(f"/internal/api/schedule-plans/{created['id']}")
    listed = client.get(
        f"/internal/api/users/{user_id}/schedule-plans",
        params={"status": "proposed"},
    )
    empty = client.get(
        f"/internal/api/users/{user_id}/schedule-plans",
        params={"status": "confirmed"},
    )

    assert fetched.status_code == 200
    assert fetched.json()["sessions"]
    assert [item["id"] for item in listed.json()["plans"]] == [created["id"]]
    assert empty.json()["plans"] == []
    rendered = str(fetched.json()).lower()
    assert "token" not in rendered
    assert "provider_etag" not in rendered


def test_missing_plan_unknown_user_and_empty_preview_errors(
    client: TestClient,
    user: User,
) -> None:
    missing = client.get(
        "/internal/api/schedule-plans/22222222-2222-2222-2222-222222222222"
    )
    unknown_payload = plan_payload(uuid.UUID("22222222-2222-2222-2222-222222222222"))
    unknown = client.post(
        "/internal/api/schedule-plans/from-preview",
        json=unknown_payload,
    )
    empty_payload = plan_payload(user.id, idempotency_key="empty")
    empty_payload["schedule_preview"]["scheduled_blocks"] = []
    empty = client.post(
        "/internal/api/schedule-plans/from-preview",
        json=empty_payload,
    )

    assert missing.status_code == 404
    assert unknown.status_code == 404
    assert empty.status_code == 422


def test_schedule_plan_routes_are_guarded_and_internal_only(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    user: User,
) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/internal/api/schedule-plans/from-preview" in paths
    assert "/api/v1/schedule-plans" not in paths
    assert not any(
        "event" in path.lower() and "create" in path.lower() for path in paths
    )

    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()
    response = client.post(
        "/internal/api/schedule-plans/from-preview",
        json=plan_payload(user.id),
    )
    assert response.status_code == 404


def test_google_scopes_are_limited_to_read_and_event_creation() -> None:
    from app.core.config import Settings

    default_scopes = Settings.model_fields["google_calendar_scopes"].default
    assert isinstance(default_scopes, str)
    assert set(default_scopes.split()) == {
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    }


@pytest.mark.parametrize(
    "invalid_session",
    [
        {
            "start": dt(12),
            "end": dt(12),
            "duration_minutes": 0,
            "order": 3,
        },
        {
            "start": dt(13),
            "end": dt(14),
            "duration_minutes": 30,
            "order": 3,
        },
        {
            "start": dt(13),
            "end": dt(14),
            "duration_minutes": 60,
            "order": 1,
        },
    ],
)
def test_session_database_constraints_reject_invalid_rows(
    client: TestClient,
    db_session: Session,
    user: User,
    invalid_session: dict[str, Any],
) -> None:
    created = create_plan(
        client,
        db_session,
        user.id,
        idempotency_key=f"constraint-{invalid_session['duration_minutes']}-"
        f"{invalid_session['order']}",
    )
    db_session.add(
        ScheduledSession(
            plan_id=uuid.UUID(created["id"]),
            task_id=None,
            step_order=None,
            title="Invalid",
            description=None,
            status=ScheduledSessionStatus.proposed,
            **invalid_session,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
