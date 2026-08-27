import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.backlog.domain import BacklogOrigin, BacklogReason, BacklogStatus
from app.calendar_integration.google.client import GoogleCalendarProvider
from app.core.config import get_settings
from app.internal import planner_router
from app.models import (
    BacklogEntry,
    CalendarConnection,
    CalendarConnectionStatus,
    CalendarEventMapping,
    CalendarProviderName,
    ExternalCalendarChange,
    ExternalCalendarConsistencyFinding,
    ExternalChangeProcessingStatus,
    ExternalChangeType,
    ScheduledSession,
    SchedulePlan,
    Task,
    User,
)
from app.models.calendar_sync import SyncStatus
from app.schedule_plans.models import (
    ScheduledSessionStatus,
    SchedulePlanSource,
    SchedulePlanStatus,
)

NOW = datetime(2026, 8, 27, 22, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def planner_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    monkeypatch.setattr(planner_router, "utc_now", lambda: NOW)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_task(session: Session, user: User, title: str) -> Task:
    task = Task(
        user_id=user.id,
        title=title,
        duration_minutes=60,
        is_splittable=False,
        minimum_session_minutes=15,
        maximum_sessions_per_day=1,
    )
    session.add(task)
    session.flush()
    return task


def make_plan(
    session: Session,
    user: User,
    *,
    title: str,
    status: SchedulePlanStatus,
    starts: list[datetime],
    created_at: datetime | None = None,
    backlog_entry_id: uuid.UUID | None = None,
) -> tuple[SchedulePlan, Task]:
    task = make_task(session, user, title)
    plan = SchedulePlan(
        user_id=user.id,
        task_id=task.id,
        backlog_entry_id=backlog_entry_id,
        plan_group_id=uuid.uuid4(),
        source=SchedulePlanSource.calendar_backed_preview,
        version=1,
        status=status,
        timezone=user.timezone,
        planning_window_start=min(starts) - timedelta(days=1),
        planning_window_end=max(starts) + timedelta(days=40),
        scheduler_version="planner-test",
        idempotency_key=str(uuid.uuid4()),
        confirmed_task_snapshot={"title": title},
        scheduling_preferences_snapshot={"timezone": user.timezone},
        busy_context_summary={"provider": "google"},
        preview_metadata={
            "scheduled_block_metadata": [
                {
                    "start": (start - timedelta(days=20)).isoformat(),
                    "end": (
                        start - timedelta(days=20) + timedelta(hours=1)
                    ).isoformat(),
                }
                for start in starts
            ]
        },
        busy_sources_snapshot=[],
        write_targets_snapshot=[],
        calendar_selection_hash="a" * 64,
        calendar_context_captured_at=NOW - timedelta(hours=1),
        created_at=created_at or NOW,
        updated_at=created_at or NOW,
        confirmed_at=(
            NOW - timedelta(minutes=20)
            if status is not SchedulePlanStatus.proposed
            else None
        ),
    )
    for order, start in enumerate(starts, start=1):
        plan.sessions.append(
            ScheduledSession(
                task_id=task.id,
                title=title,
                start=start,
                end=start + timedelta(hours=1),
                duration_minutes=60,
                order=order,
                status=(
                    ScheduledSessionStatus.proposed
                    if status is SchedulePlanStatus.proposed
                    else ScheduledSessionStatus.confirmed
                ),
            )
        )
    session.add(plan)
    session.flush()
    return plan, task


def add_mapping(
    session: Session,
    user: User,
    scheduled: ScheduledSession,
    *,
    sync_status: SyncStatus = SyncStatus.synced,
) -> CalendarEventMapping:
    connection = CalendarConnection(
        user_id=user.id,
        provider=CalendarProviderName.google,
        provider_account_id=f"account-{uuid.uuid4()}",
        status=CalendarConnectionStatus.active,
    )
    mapping = CalendarEventMapping(
        scheduled_session=scheduled,
        calendar_connection=connection,
        provider=CalendarProviderName.google,
        provider_account_id="safe-account@example.com",
        calendar_id="primary",
        external_event_id=f"event-{uuid.uuid4()}",
        sync_status=sync_status,
        last_synced_at=NOW,
    )
    session.add(mapping)
    session.flush()
    return mapping


def planner(client: TestClient, user: User, *, days: int = 14):
    return client.get(
        "/internal/api/planner",
        params={"user_id": str(user.id), "days": days},
    )


def test_empty_planner_is_200_and_days_are_bounded(
    client: TestClient, user: User
) -> None:
    response = planner(client, user)

    assert response.status_code == 200
    assert response.json() == {
        "generated_at": "2026-08-27T22:30:00Z",
        "timezone": "Europe/Warsaw",
        "today": [],
        "upcoming": [],
        "backlog": [],
        "needs_attention": [],
        "proposed_plans": [],
        "confirmed_not_applied": [],
    }
    assert planner(client, user, days=0).status_code == 422
    assert planner(client, user, days=32).status_code == 422


def test_sessions_use_local_day_current_times_order_and_horizon(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    # NOW is Aug 27 UTC but Aug 28 in Europe/Warsaw.
    yesterday, _ = make_plan(
        db_session,
        user,
        title="Yesterday",
        status=SchedulePlanStatus.applied,
        starts=[datetime(2026, 8, 27, 10, tzinfo=UTC)],
    )
    today_late, _ = make_plan(
        db_session,
        user,
        title="Moved current session",
        status=SchedulePlanStatus.applied,
        starts=[datetime(2026, 8, 28, 18, 30, tzinfo=UTC)],
    )
    today_early, _ = make_plan(
        db_session,
        user,
        title="Early today",
        status=SchedulePlanStatus.confirmed,
        starts=[datetime(2026, 8, 27, 23, tzinfo=UTC)],
    )
    tomorrow, _ = make_plan(
        db_session,
        user,
        title="Tomorrow",
        status=SchedulePlanStatus.confirmed,
        starts=[datetime(2026, 8, 28, 22, 30, tzinfo=UTC)],
    )
    outside, _ = make_plan(
        db_session,
        user,
        title="Outside horizon",
        status=SchedulePlanStatus.confirmed,
        starts=[datetime(2026, 9, 2, 9, tzinfo=UTC)],
    )
    add_mapping(db_session, user, today_late.sessions[0])
    db_session.commit()

    response = planner(client, user, days=2)

    assert response.status_code == 200
    body = response.json()
    assert [item["task_title"] for item in body["today"]] == [
        "Early today",
        "Moved current session",
    ]
    assert [item["task_title"] for item in body["upcoming"]] == ["Tomorrow"]
    assert all(item["task_title"] != "Yesterday" for item in body["today"])
    assert all(item["task_title"] != "Outside horizon" for item in body["upcoming"])
    moved = body["today"][1]
    assert moved["start"] == "2026-08-28T18:30:00Z"
    assert moved["end"] == "2026-08-28T19:30:00Z"
    assert moved["external_calendar"]["calendar_id"] == "primary"
    assert (
        moved["start"]
        != today_late.preview_metadata["scheduled_block_metadata"][0]["start"]
    )
    assert tomorrow.sessions[0].id not in {
        uuid.UUID(item["session_id"]) for item in body["today"]
    }
    assert yesterday.sessions[0].id not in {
        uuid.UUID(item["session_id"]) for item in body["today"]
    }
    assert outside.sessions[0].id not in {
        uuid.UUID(item["session_id"]) for item in body["upcoming"]
    }


def test_backlog_and_plan_lifecycle_sections_are_domain_faithful(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    backlog_tasks = [
        make_task(db_session, user, f"Backlog {index}") for index in range(4)
    ]
    entries = [
        BacklogEntry(
            user_id=user.id,
            task_id=backlog_tasks[0].id,
            status=BacklogStatus.active,
            origin=BacklogOrigin.user,
            reason=BacklogReason.awaiting_user_confirmation,
            remaining_duration_minutes=45,
            entered_at=NOW - timedelta(days=2),
            scheduling_attempt_count=2,
            note="Try scheduling",
        ),
        BacklogEntry(
            user_id=user.id,
            task_id=backlog_tasks[1].id,
            status=BacklogStatus.deferred,
            origin=BacklogOrigin.user,
            reason=BacklogReason.manual_defer,
            remaining_duration_minutes=30,
            entered_at=NOW - timedelta(days=1),
            deferred_until=NOW + timedelta(days=1),
            scheduling_attempt_count=1,
        ),
        BacklogEntry(
            user_id=user.id,
            task_id=backlog_tasks[2].id,
            status=BacklogStatus.resolved,
            origin=BacklogOrigin.user,
            reason=BacklogReason.awaiting_user_confirmation,
            remaining_duration_minutes=0,
            entered_at=NOW,
            resolved_at=NOW,
            scheduling_attempt_count=1,
        ),
        BacklogEntry(
            user_id=user.id,
            task_id=backlog_tasks[3].id,
            status=BacklogStatus.cancelled,
            origin=BacklogOrigin.user,
            reason=BacklogReason.awaiting_user_confirmation,
            remaining_duration_minutes=0,
            entered_at=NOW,
            scheduling_attempt_count=0,
        ),
    ]
    db_session.add_all(entries)
    statuses = [
        SchedulePlanStatus.proposed,
        SchedulePlanStatus.confirmed,
        SchedulePlanStatus.revalidation_required,
        SchedulePlanStatus.applying,
        SchedulePlanStatus.partially_applied,
        SchedulePlanStatus.applied,
    ]
    plans: dict[SchedulePlanStatus, SchedulePlan] = {}
    for offset, status in enumerate(statuses):
        plan, _task = make_plan(
            db_session,
            user,
            title=f"Plan {status.value}",
            status=status,
            starts=[NOW + timedelta(days=offset + 1)],
            created_at=NOW - timedelta(minutes=offset),
        )
        plans[status] = plan
    add_mapping(
        db_session,
        user,
        plans[SchedulePlanStatus.partially_applied].sessions[0],
    )
    db_session.commit()

    body = planner(client, user).json()

    assert [item["task_title"] for item in body["backlog"]] == [
        "Backlog 1",
        "Backlog 0",
    ]
    assert body["backlog"][1]["remaining_duration_minutes"] == 45
    assert [item["status"] for item in body["proposed_plans"]] == ["proposed"]
    pending = {item["status"]: item for item in body["confirmed_not_applied"]}
    assert set(pending) == {
        "confirmed",
        "revalidation_required",
        "applying",
        "partially_applied",
    }
    assert pending["confirmed"]["readiness"] == "ready_to_apply"
    assert pending["revalidation_required"]["revalidation_required"] is True
    assert pending["applying"]["readiness"] == "apply_in_progress"
    assert pending["partially_applied"]["mapped_session_count"] == 1
    assert all(item["status"] != "applied" for item in body["confirmed_not_applied"])


def test_attention_ownership_read_only_and_fixed_query_count(
    client: TestClient,
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, task = make_plan(
        db_session,
        user,
        title="Needs revalidation",
        status=SchedulePlanStatus.revalidation_required,
        starts=[NOW + timedelta(hours=2)],
    )
    partially_applied, _ = make_plan(
        db_session,
        user,
        title="Partially applied",
        status=SchedulePlanStatus.partially_applied,
        starts=[NOW + timedelta(hours=4)],
    )
    failed, _ = make_plan(
        db_session,
        user,
        title="Terminal failed plan",
        status=SchedulePlanStatus.failed,
        starts=[NOW + timedelta(hours=5)],
    )
    mapping = add_mapping(db_session, user, plan.sessions[0])
    pending = ExternalCalendarChange(
        mapping=mapping,
        change_type=ExternalChangeType.moved,
        processing_status=ExternalChangeProcessingStatus.pending,
        transition_hash=uuid.uuid4().hex,
        detected_at=NOW - timedelta(minutes=2),
    )
    processed = ExternalCalendarChange(
        mapping=mapping,
        change_type=ExternalChangeType.updated,
        processing_status=ExternalChangeProcessingStatus.processed,
        transition_hash=uuid.uuid4().hex,
        detected_at=NOW - timedelta(minutes=3),
    )
    db_session.add_all([pending, processed])
    db_session.flush()
    finding = ExternalCalendarConsistencyFinding(
        external_calendar_change_id=processed.id,
        schedule_plan_id=plan.id,
        scheduled_session_id=plan.sessions[0].id,
        code="outside_planning_window",
        severity="warning",
        identity_key=str(plan.sessions[0].id),
        details={},
        detected_at=NOW - timedelta(minutes=1),
    )
    db_session.add(finding)

    other = User(email="planner-other@example.com", timezone="UTC")
    db_session.add(other)
    db_session.flush()
    other_plan, other_task = make_plan(
        db_session,
        other,
        title="Private other plan",
        status=SchedulePlanStatus.revalidation_required,
        starts=[NOW + timedelta(hours=3)],
    )
    other_mapping = add_mapping(db_session, other, other_plan.sessions[0])
    other_change = ExternalCalendarChange(
        mapping=other_mapping,
        change_type=ExternalChangeType.deleted,
        processing_status=ExternalChangeProcessingStatus.pending,
        transition_hash=uuid.uuid4().hex,
        detected_at=NOW,
    )
    other_backlog = BacklogEntry(
        user_id=other.id,
        task_id=other_task.id,
        status=BacklogStatus.active,
        origin=BacklogOrigin.user,
        reason=BacklogReason.awaiting_user_confirmation,
        remaining_duration_minutes=60,
        entered_at=NOW,
        scheduling_attempt_count=0,
    )
    db_session.add_all([other_change, other_backlog])
    db_session.flush()
    other_finding = ExternalCalendarConsistencyFinding(
        external_calendar_change_id=other_change.id,
        schedule_plan_id=other_plan.id,
        scheduled_session_id=other_plan.sessions[0].id,
        code="private_issue",
        severity="error",
        identity_key=str(other_plan.sessions[0].id),
        details={},
        detected_at=NOW,
    )
    db_session.add(other_finding)
    db_session.commit()

    before = {
        model.__tablename__: db_session.scalar(select(func.count()).select_from(model))
        for model in (
            Task,
            BacklogEntry,
            SchedulePlan,
            ScheduledSession,
            CalendarEventMapping,
            ExternalCalendarChange,
            ExternalCalendarConsistencyFinding,
        )
    }

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Planner crossed its read-only boundary")

    monkeypatch.setattr(GoogleCalendarProvider, "get_event", forbidden)
    monkeypatch.setattr(GoogleCalendarProvider, "create_event", forbidden)
    monkeypatch.setattr("app.scheduling.scheduler.schedule_tasks", forbidden)
    monkeypatch.setattr("app.schedule_plans.apply.apply_schedule_plan", forbidden)
    monkeypatch.setattr("app.calendar_sync.pull.pull_calendar_event", forbidden)

    query_count = 0
    mutation_count = 0

    def count_selects(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        nonlocal mutation_count, query_count
        operation = statement.lstrip().split(maxsplit=1)[0].upper()
        if operation == "SELECT":
            query_count += 1
        elif operation in {"INSERT", "UPDATE", "DELETE"}:
            mutation_count += 1

    event.listen(db_session.get_bind(), "before_cursor_execute", count_selects)
    try:
        response = planner(client, user)
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", count_selects)

    assert response.status_code == 200
    body = response.json()
    assert query_count == 5
    assert mutation_count == 0
    attention = body["needs_attention"]
    plan_attention_ids = {
        item["plan_id"] for item in attention if item["type"] == "schedule_plan"
    }
    assert plan_attention_ids == {str(plan.id), str(partially_applied.id)}
    assert str(failed.id) not in plan_attention_ids
    assert str(failed.id) not in response.text
    assert {item["action"] for item in attention} == {
        "revalidate_plan",
        "retry_apply",
        "review_consistency",
        "process_external_change",
    }
    assert {item["external_change_id"] for item in body["needs_attention"]} == {
        None,
        str(finding.external_calendar_change_id),
        str(pending.id),
    }
    serialized = response.text
    assert str(other.id) not in serialized
    assert str(other_task.id) not in serialized
    assert str(other_plan.id) not in serialized
    assert str(other_mapping.id) not in serialized
    assert str(other_change.id) not in serialized
    assert str(other_backlog.id) not in serialized
    assert str(other_finding.id) not in serialized
    assert str(task.id) in serialized

    after = {
        model.__tablename__: db_session.scalar(select(func.count()).select_from(model))
        for model in (
            Task,
            BacklogEntry,
            SchedulePlan,
            ScheduledSession,
            CalendarEventMapping,
            ExternalCalendarChange,
            ExternalCalendarConsistencyFinding,
        )
    }
    assert after == before


def test_planner_is_internal_and_unknown_user_is_404(
    client: TestClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert planner(client, user).status_code == 200
    unknown = client.get("/internal/api/planner", params={"user_id": str(uuid.uuid4())})
    assert unknown.status_code == 404

    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()
    hidden = planner(client, user)
    assert hidden.status_code == 404
