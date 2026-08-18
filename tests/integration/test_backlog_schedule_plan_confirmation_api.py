import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.backlog.domain import BacklogOrigin, BacklogReason, BacklogStatus
from app.backlog.service import (
    cancel_backlog_entry,
    create_backlog_entry,
    resolve_backlog_entry,
)
from app.calendar_integration.google.client import GoogleCalendarProvider
from app.core.config import get_settings
from app.domain.tasks import TaskStatus
from app.models import (
    CalendarConnection,
    CalendarSelection,
    SchedulePlan,
    Task,
    User,
)
from app.models.calendar import CalendarProviderName
from app.schedule_plans.models import SchedulePlanStatus
from app.schedule_plans.service import confirm_schedule_plan

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
    duration: int = 240,
) -> Task:
    user.timezone = "UTC"
    task = Task(
        user_id=user.id,
        title="Backlog plan task",
        duration_minutes=duration,
        status=TaskStatus.pending,
        is_splittable=True,
        minimum_session_minutes=15,
        maximum_sessions_per_day=8,
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
):
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


def ensure_calendar_context(session: Session, user: User) -> None:
    connection = CalendarConnection(
        user_id=user.id,
        provider=CalendarProviderName.google,
        provider_account_id=f"account-{user.id}",
    )
    connection.selections.append(
        CalendarSelection(
            external_calendar_id="primary",
            display_name="Primary",
            primary=True,
            include_in_availability=True,
        )
    )
    session.add(connection)
    session.commit()


def request_preview(client: TestClient, entry_id: uuid.UUID, user_id: uuid.UUID):
    response = client.post(
        f"/internal/api/backlog/{entry_id}/schedule-preview",
        params={"user_id": str(user_id)},
        json={
            "planning_window": {
                "start": WINDOW_START.isoformat(),
                "end": WINDOW_END.isoformat(),
            },
            "busy_intervals": [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def selected_preview(preview_result: dict[str, Any], *, minutes: int) -> dict[str, Any]:
    preview = cast(dict[str, Any], preview_result["schedule_preview"])
    block = cast(dict[str, Any], preview["scheduled_blocks"][0])
    start = datetime.fromisoformat(str(block["start"]).replace("Z", "+00:00"))
    block["end"] = (start + timedelta(minutes=minutes)).isoformat()
    preview["scheduled_blocks"] = [block]
    preview["unscheduled_tasks"] = []
    return preview


def plan_payload(
    preview_result: dict[str, Any],
    *,
    minutes: int,
    preview_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preview = preview_override or selected_preview(preview_result, minutes=minutes)
    return {
        "scheduling_attempt_count": preview_result["scheduling_attempt_count"],
        "schedule_preview": preview,
        "planning_context": {
            "timezone": "UTC",
            "planning_window_start": WINDOW_START.isoformat(),
            "planning_window_end": WINDOW_END.isoformat(),
            "scheduler_version": preview["scheduler_version"],
            "calendar_context": {
                "provider": "google",
                "calendar_ids": ["primary"],
                "provider_busy_interval_count": 0,
                "merged_busy_interval_count": 0,
            },
            "preferences_snapshot": {"timezone": "UTC"},
        },
    }


def create_plan(
    client: TestClient,
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: dict[str, Any],
):
    return client.post(
        f"/internal/api/backlog/{entry_id}/schedule-plan",
        params={"user_id": str(user_id)},
        json=payload,
    )


@pytest.mark.parametrize("entry_status", [BacklogStatus.active, BacklogStatus.deferred])
def test_active_or_deferred_backlog_creates_proposed_plan_with_provenance(
    entry_status: BacklogStatus,
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task, status=entry_status)
    ensure_calendar_context(db_session, user)
    preview = request_preview(client, entry.id, user.id)

    response = create_plan(client, entry.id, user.id, plan_payload(preview, minutes=90))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "proposed"
    assert body["backlog_entry_id"] == str(entry.id)
    plan = db_session.get(SchedulePlan, uuid.UUID(body["id"]))
    assert plan is not None
    assert plan.busy_sources_snapshot
    assert plan.write_targets_snapshot
    assert plan.calendar_selection_hash
    db_session.refresh(entry)
    assert entry.status is entry_status
    assert entry.remaining_duration_minutes == 240


def test_same_preview_is_idempotent_but_fresh_preview_creates_new_plan(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)
    ensure_calendar_context(db_session, user)
    first_preview = request_preview(client, entry.id, user.id)
    payload = plan_payload(first_preview, minutes=90)

    first = create_plan(client, entry.id, user.id, payload)
    repeated = create_plan(client, entry.id, user.id, payload)
    second_preview = request_preview(client, entry.id, user.id)
    second = create_plan(
        client, entry.id, user.id, plan_payload(second_preview, minutes=90)
    )

    assert first.status_code == repeated.status_code == second.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]
    assert second.json()["id"] != first.json()["id"]
    assert db_session.scalar(select(func.count()).select_from(SchedulePlan)) == 2


def test_stale_preview_terminal_states_and_cross_user_are_rejected(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)
    ensure_calendar_context(db_session, user)
    stale = request_preview(client, entry.id, user.id)
    request_preview(client, entry.id, user.id)
    stale_response = create_plan(
        client, entry.id, user.id, plan_payload(stale, minutes=60)
    )
    assert stale_response.status_code == 409

    other = User(email="plan-other@example.com", timezone="UTC")
    db_session.add(other)
    db_session.commit()
    cross_user = create_plan(
        client, entry.id, other.id, plan_payload(stale, minutes=60)
    )
    assert cross_user.status_code == 404

    current = request_preview(client, entry.id, user.id)
    resolve_backlog_entry(db_session, entry_id=entry.id, user_id=user.id)
    assert (
        create_plan(
            client, entry.id, user.id, plan_payload(current, minutes=60)
        ).status_code
        == 409
    )

    cancelled_task = make_task(db_session, user)
    cancelled_entry = make_entry(db_session, user, cancelled_task)
    cancelled_preview = request_preview(client, cancelled_entry.id, user.id)
    cancel_backlog_entry(db_session, entry_id=cancelled_entry.id, user_id=user.id)
    assert (
        create_plan(
            client,
            cancelled_entry.id,
            user.id,
            plan_payload(cancelled_preview, minutes=60),
        ).status_code
        == 409
    )


def test_excessive_overlapping_and_malformed_selection_are_rejected(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user, duration=120)
    entry = make_entry(db_session, user, task)
    ensure_calendar_context(db_session, user)
    preview_result = request_preview(client, entry.id, user.id)
    preview = selected_preview(preview_result, minutes=60)
    block = cast(dict[str, Any], preview["scheduled_blocks"][0])
    start = datetime.fromisoformat(str(block["start"]).replace("Z", "+00:00"))

    excessive = selected_preview(preview_result, minutes=180)
    overlapping = {**preview, "scheduled_blocks": [block, dict(block)]}
    malformed = {
        **preview,
        "scheduled_blocks": [
            {**block, "end": (start - timedelta(minutes=1)).isoformat()}
        ],
    }

    for candidate in (excessive, overlapping, malformed):
        response = create_plan(
            client,
            entry.id,
            user.id,
            plan_payload(preview_result, minutes=60, preview_override=candidate),
        )
        assert response.status_code == 422, response.text


@pytest.mark.parametrize("task_status", [TaskStatus.completed, TaskStatus.cancelled])
def test_terminal_task_is_rejected_before_plan_creation(
    task_status: TaskStatus,
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)
    ensure_calendar_context(db_session, user)
    preview = request_preview(client, entry.id, user.id)
    task.status = task_status
    db_session.commit()

    response = create_plan(client, entry.id, user.id, plan_payload(preview, minutes=60))

    assert response.status_code == 409


def test_partial_then_full_confirmation_recalculates_from_reservations(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user, duration=240)
    entry = make_entry(db_session, user, task)
    ensure_calendar_context(db_session, user)
    preview_a = request_preview(client, entry.id, user.id)
    plan_a = create_plan(
        client, entry.id, user.id, plan_payload(preview_a, minutes=90)
    ).json()

    confirmed_a = client.post(
        f"/internal/api/schedule-plans/{plan_a['id']}/confirm", json={}
    )
    assert confirmed_a.status_code == 200
    db_session.refresh(entry)
    assert entry.status is BacklogStatus.active
    assert entry.remaining_duration_minutes == 150
    assert entry.reason is BacklogReason.partially_scheduled

    preview_b = request_preview(client, entry.id, user.id)
    plan_b = create_plan(
        client, entry.id, user.id, plan_payload(preview_b, minutes=150)
    ).json()
    confirmed_b = client.post(
        f"/internal/api/schedule-plans/{plan_b['id']}/confirm", json={}
    )
    repeated_b = client.post(
        f"/internal/api/schedule-plans/{plan_b['id']}/confirm", json={}
    )

    assert confirmed_b.status_code == repeated_b.status_code == 200
    db_session.refresh(entry)
    assert entry.status is BacklogStatus.resolved
    assert entry.remaining_duration_minutes == 0
    assert entry.resolved_at is not None
    assert repeated_b.json()["confirmed_at"] == confirmed_b.json()["confirmed_at"]


def test_proposed_obsolete_failed_and_unrelated_plans_do_not_change_backlog(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)
    ensure_calendar_context(db_session, user)
    preview = request_preview(client, entry.id, user.id)
    proposed = create_plan(
        client, entry.id, user.id, plan_payload(preview, minutes=60)
    ).json()

    db_session.refresh(entry)
    assert entry.remaining_duration_minutes == 240
    assert (
        client.post(
            f"/internal/api/schedule-plans/{proposed['id']}/obsolete"
        ).status_code
        == 200
    )
    db_session.refresh(entry)
    assert entry.remaining_duration_minutes == 240

    plan = db_session.get(SchedulePlan, uuid.UUID(proposed["id"]))
    assert plan is not None
    plan.status = SchedulePlanStatus.failed
    db_session.commit()
    db_session.refresh(entry)
    assert entry.remaining_duration_minutes == 240


def test_confirming_normal_plan_preserves_backlog_state(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)
    ensure_calendar_context(db_session, user)
    preview_result = request_preview(client, entry.id, user.id)
    backlog_payload = plan_payload(preview_result, minutes=60)
    normal_payload = {
        "user_id": str(user.id),
        "task_id": str(task.id),
        "confirmed_task": {
            "title": task.title,
            "description": None,
            "duration_minutes": 60,
            "priority": "medium",
            "earliest_start": None,
            "deadline": None,
            "preferred_time_of_day": "any",
            "is_splittable": True,
            "minimum_session_minutes": 15,
            "maximum_sessions_per_day": 8,
            "steps": [],
        },
        "schedule_preview": backlog_payload["schedule_preview"],
        "planning_context": backlog_payload["planning_context"],
        "source": "manual_preview",
    }
    created = client.post(
        "/internal/api/schedule-plans/from-preview", json=normal_payload
    )
    assert created.status_code == 201, created.text
    assert created.json()["backlog_entry_id"] is None

    confirmed = client.post(
        f"/internal/api/schedule-plans/{created.json()['id']}/confirm", json={}
    )

    assert confirmed.status_code == 200
    db_session.refresh(entry)
    assert entry.status is BacklogStatus.active
    assert entry.remaining_duration_minutes == 240


def test_confirmation_failure_rolls_back_plan_and_backlog(
    client: TestClient,
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)
    ensure_calendar_context(db_session, user)
    preview = request_preview(client, entry.id, user.id)
    plan = create_plan(
        client, entry.id, user.id, plan_payload(preview, minutes=60)
    ).json()

    def fail_update(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced backlog update failure")

    monkeypatch.setattr(
        "app.schedule_plans.service._update_backlog_after_confirmation", fail_update
    )
    with pytest.raises(RuntimeError, match="forced backlog update failure"):
        client.post(f"/internal/api/schedule-plans/{plan['id']}/confirm", json={})

    persisted = db_session.get(SchedulePlan, uuid.UUID(plan["id"]))
    db_session.refresh(entry)
    assert persisted is not None and persisted.status is SchedulePlanStatus.proposed
    assert entry.remaining_duration_minutes == 240


def test_concurrent_confirmation_is_idempotent(
    client: TestClient, db_session: Session, user: User
) -> None:
    task = make_task(db_session, user, duration=120)
    entry = make_entry(db_session, user, task)
    ensure_calendar_context(db_session, user)
    preview = request_preview(client, entry.id, user.id)
    created = create_plan(
        client, entry.id, user.id, plan_payload(preview, minutes=60)
    ).json()
    plan_id = uuid.UUID(created["id"])
    concurrent_sessions = sessionmaker(
        bind=db_session.get_bind(), expire_on_commit=False
    )

    def confirm_once() -> SchedulePlanStatus:
        with concurrent_sessions() as session:
            return confirm_schedule_plan(session, plan_id).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _item: confirm_once(), range(2)))

    db_session.expire_all()
    assert statuses == [SchedulePlanStatus.confirmed, SchedulePlanStatus.confirmed]
    assert entry.remaining_duration_minutes == 60


def test_plan_creation_does_not_run_scheduler_apply_or_google_write(
    client: TestClient,
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = make_task(db_session, user)
    entry = make_entry(db_session, user, task)
    ensure_calendar_context(db_session, user)
    preview = request_preview(client, entry.id, user.id)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("out-of-scope component invoked")

    monkeypatch.setattr("app.scheduling.scheduler.schedule_tasks", forbidden)
    monkeypatch.setattr(GoogleCalendarProvider, "create_event", forbidden)
    monkeypatch.setattr(
        "app.internal.schedule_plans_router.apply_schedule_plan", forbidden
    )

    response = create_plan(client, entry.id, user.id, plan_payload(preview, minutes=60))

    assert response.status_code == 201


def test_openapi_documents_backlog_plan_creation(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"][
        "/internal/api/backlog/{entry_id}/schedule-plan"
    ]["post"]
    assert operation["summary"] == "Create a SchedulePlan from a backlog preview"
    assert "does not confirm" in operation["description"]
