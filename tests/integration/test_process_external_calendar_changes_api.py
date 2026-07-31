import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.calendar_integration.google.client import GoogleCalendarProvider
from app.calendar_sync.processing import (
    ExternalCalendarAggregateError,
    ProcessExternalCalendarChangeService,
)
from app.core.config import get_settings
from app.domain.external_calendar_policy import (
    ExternalCalendarAggregate,
    ExternalCalendarChangeInput,
    NoAction,
    PolicyDecision,
    UpdateScheduledSessionTime,
)
from app.main import app
from app.models import (
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
    TaskDeadlineHistory,
    User,
)
from app.models.calendar_sync import SyncStatus
from app.schedule_plans.models import (
    ScheduledSessionStatus,
    SchedulePlanSource,
    SchedulePlanStatus,
)
from app.scheduling import scheduler

NOW = datetime(2026, 7, 31, 8, tzinfo=UTC)


@pytest.fixture(autouse=True)
def enable_internal_tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def snapshot(
    *,
    start: datetime | None,
    end: datetime | None,
    exists: bool = True,
    cancelled: bool = False,
) -> dict[str, Any]:
    return {
        "external_event_id": "event-1",
        "calendar_id": "primary",
        "exists": exists,
        "cancelled": cancelled,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "timezone": "Europe/Warsaw",
        "etag": '"v1"',
        "provider_updated_at": NOW.isoformat(),
        "provider_status": "confirmed" if exists else "not_found",
    }


@pytest.fixture
def processing_setup(
    db_session: Session, user: User
) -> tuple[
    ExternalCalendarChange, Task, ScheduledSession, SchedulePlan, CalendarEventMapping
]:
    task = Task(
        id=uuid.uuid4(),
        user_id=user.id,
        title="Task",
        duration_minutes=60,
        deadline=NOW + timedelta(hours=2),
        is_splittable=False,
        minimum_session_minutes=15,
        maximum_sessions_per_day=1,
    )
    plan = SchedulePlan(
        id=uuid.uuid4(),
        user_id=user.id,
        task_id=task.id,
        plan_group_id=uuid.uuid4(),
        source=SchedulePlanSource.calendar_backed_preview,
        version=1,
        status=SchedulePlanStatus.applied,
        timezone="Europe/Warsaw",
        planning_window_start=NOW - timedelta(hours=1),
        planning_window_end=NOW + timedelta(days=1),
        scheduler_version="test",
        idempotency_key=str(uuid.uuid4()),
        confirmed_task_snapshot={"title": "Task"},
        scheduling_preferences_snapshot={"unchanged": True},
        busy_context_summary={},
        preview_metadata={},
    )
    scheduled = ScheduledSession(
        id=uuid.uuid4(),
        plan=plan,
        task_id=task.id,
        title="Session",
        start=NOW,
        end=NOW + timedelta(hours=1),
        duration_minutes=60,
        order=1,
        status=ScheduledSessionStatus.applied,
    )
    connection = CalendarConnection(
        id=uuid.uuid4(),
        user_id=user.id,
        provider=CalendarProviderName.google,
        provider_account_id="account-1",
        access_token_encrypted="not-used",
        status=CalendarConnectionStatus.active,
    )
    mapping = CalendarEventMapping(
        id=uuid.uuid4(),
        scheduled_session=scheduled,
        calendar_connection=connection,
        provider=CalendarProviderName.google,
        provider_account_id="account-1",
        calendar_id="primary",
        external_event_id="event-1",
        sync_status=SyncStatus.synced,
        last_synced_snapshot=snapshot(
            start=NOW + timedelta(minutes=30),
            end=NOW + timedelta(hours=1, minutes=30),
        ),
    )
    change = ExternalCalendarChange(
        id=uuid.uuid4(),
        mapping=mapping,
        change_type=ExternalChangeType.moved,
        old_values=snapshot(start=NOW, end=NOW + timedelta(hours=1)),
        new_values=snapshot(
            start=NOW + timedelta(minutes=30),
            end=NOW + timedelta(hours=1, minutes=30),
        ),
        transition_hash=uuid.uuid4().hex,
    )
    db_session.add_all([task, plan, connection, change])
    db_session.commit()
    return change, task, scheduled, plan, mapping


def process(client: TestClient, change: ExternalCalendarChange, user: User):
    return client.post(
        f"/internal/api/external-calendar-changes/{change.id}/process",
        params={"user_id": str(user.id)},
    )


def test_moved_session_and_deadline_are_applied_once_atomically(
    client: TestClient,
    db_session: Session,
    user: User,
    processing_setup: tuple[
        ExternalCalendarChange,
        Task,
        ScheduledSession,
        SchedulePlan,
        CalendarEventMapping,
    ],
) -> None:
    change, task, scheduled, plan, _mapping = processing_setup
    original_snapshot = dict(plan.scheduling_preferences_snapshot)
    original_status = plan.status

    first = process(client, change, user)
    second = process(client, change, user)

    assert first.status_code == 200
    payload = first.json()
    assert payload["actions_applied"] == [
        "update_scheduled_session_time",
    ]
    assert payload["deadline_extended"] is False
    assert second.status_code == 200
    assert second.json()["already_processed"] is True
    db_session.refresh(scheduled)
    db_session.refresh(task)
    db_session.refresh(plan)
    assert scheduled.start == NOW + timedelta(minutes=30)
    assert scheduled.end == NOW + timedelta(hours=1, minutes=30)
    assert task.deadline == NOW + timedelta(hours=2)
    assert plan.status is original_status
    assert plan.scheduling_preferences_snapshot == original_snapshot
    assert db_session.scalar(select(func.count()).select_from(SchedulePlan)) == 1
    assert db_session.scalar(select(func.count()).select_from(TaskDeadlineHistory)) == 0


def test_move_beyond_deadline_extends_exactly_and_writes_history_once(
    client: TestClient,
    db_session: Session,
    user: User,
    processing_setup: tuple[
        ExternalCalendarChange,
        Task,
        ScheduledSession,
        SchedulePlan,
        CalendarEventMapping,
    ],
) -> None:
    change, task, _scheduled, _plan, mapping = processing_setup
    new_start = NOW + timedelta(hours=2)
    new_end = NOW + timedelta(hours=3)
    change.new_values = snapshot(start=new_start, end=new_end)
    mapping.last_synced_snapshot = change.new_values
    db_session.commit()

    response = process(client, change, user)
    repeated = process(client, change, user)

    assert response.status_code == 200
    assert response.json()["resulting_deadline"] == new_end.isoformat().replace(
        "+00:00", "Z"
    )
    assert response.json()["deadline_extended"] is True
    assert repeated.json()["already_processed"] is True
    history = db_session.scalar(select(TaskDeadlineHistory))
    assert history is not None
    assert history.previous_deadline == NOW + timedelta(hours=2)
    assert history.new_deadline == new_end
    assert history.external_calendar_change_id == change.id
    assert history.reason == "external_calendar_session_move"
    assert db_session.scalar(select(func.count()).select_from(TaskDeadlineHistory)) == 1
    db_session.refresh(task)
    assert task.deadline == new_end


def test_deleted_event_preserves_domain_entities_and_records_finding(
    client: TestClient,
    db_session: Session,
    user: User,
    processing_setup: tuple[
        ExternalCalendarChange,
        Task,
        ScheduledSession,
        SchedulePlan,
        CalendarEventMapping,
    ],
) -> None:
    change, task, scheduled, plan, mapping = processing_setup
    change.change_type = ExternalChangeType.deleted
    change.new_values = snapshot(start=None, end=None, exists=False)
    mapping.last_synced_snapshot = change.new_values
    original_task_status = task.status
    original_session = (scheduled.start, scheduled.end)
    db_session.commit()

    response = process(client, change, user)

    assert response.status_code == 200
    assert response.json()["external_event_missing"] is True
    assert response.json()["deadline_extended"] is False
    db_session.refresh(mapping)
    db_session.refresh(scheduled)
    db_session.refresh(task)
    assert mapping.sync_status is SyncStatus.externally_deleted
    assert db_session.get(CalendarEventMapping, mapping.id) is not None
    assert db_session.get(ScheduledSession, scheduled.id) is not None
    assert (scheduled.start, scheduled.end) == original_session
    assert task.status is original_task_status
    assert plan.status is SchedulePlanStatus.applied
    finding = db_session.scalar(select(ExternalCalendarConsistencyFinding))
    assert finding is not None
    assert finding.code == "external_event_missing"
    assert finding.external_calendar_change_id == change.id


def test_no_action_and_unsupported_are_processed_typed_noops(
    client: TestClient,
    db_session: Session,
    user: User,
    processing_setup: tuple[
        ExternalCalendarChange,
        Task,
        ScheduledSession,
        SchedulePlan,
        CalendarEventMapping,
    ],
) -> None:
    change, task, scheduled, _plan, _mapping = processing_setup
    change.new_values = change.old_values
    db_session.commit()
    no_action = process(client, change, user)
    assert no_action.json()["actions_applied"] == ["no_action"]

    second = ExternalCalendarChange(
        mapping_id=change.mapping_id,
        change_type=ExternalChangeType.updated,
        old_values=snapshot(start=NOW, end=NOW + timedelta(hours=1)),
        new_values=snapshot(start=NOW, end=NOW + timedelta(hours=1)),
        transition_hash=uuid.uuid4().hex,
    )
    db_session.add(second)
    db_session.commit()
    # A non-move state difference produces the engine's unsupported result.
    second.new_values = {**second.new_values, "calendar_id": "secondary"}
    db_session.commit()
    unsupported = process(client, second, user)
    assert unsupported.status_code == 200
    assert unsupported.json()["actions_applied"] == ["unsupported_external_change"]
    db_session.refresh(task)
    db_session.refresh(scheduled)
    assert task.deadline == NOW + timedelta(hours=2)
    assert scheduled.start == NOW


def test_processing_does_not_invoke_provider_or_scheduler(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    user: User,
    processing_setup: tuple[
        ExternalCalendarChange,
        Task,
        ScheduledSession,
        SchedulePlan,
        CalendarEventMapping,
    ],
) -> None:
    change, *_ = processing_setup
    change.new_values = change.old_values
    db_session.commit()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("external-change processing crossed its orchestration boundary")

    monkeypatch.setattr(GoogleCalendarProvider, "get_event", forbidden)
    monkeypatch.setattr(GoogleCalendarProvider, "create_event", forbidden)
    monkeypatch.setattr(scheduler, "schedule_tasks", forbidden)

    response = process(client, change, user)

    assert response.status_code == 200
    assert response.json()["actions_applied"] == ["no_action"]


def test_ownership_and_lifecycle_errors_are_typed(
    client: TestClient,
    db_session: Session,
    user: User,
    processing_setup: tuple[
        ExternalCalendarChange,
        Task,
        ScheduledSession,
        SchedulePlan,
        CalendarEventMapping,
    ],
) -> None:
    change, *_ = processing_setup
    other = User(id=uuid.uuid4(), email="other@example.com", timezone="UTC")
    db_session.add(other)
    db_session.commit()
    hidden = process(client, change, other)
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "external_calendar_change_not_found"

    change.processing_status = ExternalChangeProcessingStatus.processing
    db_session.commit()
    busy = process(client, change, user)
    assert busy.status_code == 409
    assert busy.json()["detail"]["code"] == "external_calendar_change_processing"


def test_malformed_interval_rolls_back_everything(
    db_session: Session,
    user: User,
    processing_setup: tuple[
        ExternalCalendarChange,
        Task,
        ScheduledSession,
        SchedulePlan,
        CalendarEventMapping,
    ],
) -> None:
    change, task, scheduled, _plan, mapping = processing_setup
    original = (scheduled.start, scheduled.end, task.deadline, mapping.sync_status)
    calls = 0

    def invalid_evaluator(
        _aggregate: ExternalCalendarAggregate, _change: ExternalCalendarChangeInput
    ) -> tuple[PolicyDecision, ...]:
        nonlocal calls
        calls += 1
        return (
            UpdateScheduledSessionTime(
                scheduled_session_id=scheduled.id,
                previous_start=scheduled.start,
                previous_end=scheduled.end,
                new_start=NOW + timedelta(hours=2),
                new_end=NOW + timedelta(hours=1),
            ),
        )

    with pytest.raises(ExternalCalendarAggregateError):
        ProcessExternalCalendarChangeService(
            db_session, evaluator=invalid_evaluator
        ).process(user_id=user.id, change_id=change.id)

    assert calls == 1
    db_session.refresh(change)
    db_session.refresh(task)
    db_session.refresh(scheduled)
    db_session.refresh(mapping)
    assert change.processing_status is ExternalChangeProcessingStatus.pending
    assert (
        scheduled.start,
        scheduled.end,
        task.deadline,
        mapping.sync_status,
    ) == original
    assert db_session.scalar(select(func.count()).select_from(TaskDeadlineHistory)) == 0
    assert (
        db_session.scalar(
            select(func.count()).select_from(ExternalCalendarConsistencyFinding)
        )
        == 0
    )


def test_concurrent_processing_serializes_and_applies_once(
    db_session: Session,
    user: User,
    processing_setup: tuple[
        ExternalCalendarChange,
        Task,
        ScheduledSession,
        SchedulePlan,
        CalendarEventMapping,
    ],
) -> None:
    change, _task, _scheduled, _plan, _mapping = processing_setup
    bind = db_session.get_bind()
    sessions = sessionmaker(bind=bind, expire_on_commit=False)
    entered = Event()
    release = Event()
    results: list[bool] = []

    def blocking_evaluator(
        _aggregate: ExternalCalendarAggregate, _change: ExternalCalendarChangeInput
    ) -> tuple[PolicyDecision, ...]:
        entered.set()
        assert release.wait(timeout=5)
        return (NoAction(),)

    def first() -> None:
        with sessions() as session:
            result = ProcessExternalCalendarChangeService(
                session, evaluator=blocking_evaluator
            ).process(user_id=user.id, change_id=change.id)
            results.append(result.already_processed)

    def second() -> None:
        assert entered.wait(timeout=5)
        with sessions() as session:
            result = ProcessExternalCalendarChangeService(session).process(
                user_id=user.id, change_id=change.id
            )
            results.append(result.already_processed)

    first_thread = Thread(target=first)
    second_thread = Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert entered.wait(timeout=5)
    release.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert sorted(results) == [False, True]
    db_session.expire_all()
    assert db_session.get(ExternalCalendarChange, change.id).processing_status is (
        ExternalChangeProcessingStatus.processed
    )


def test_internal_tools_gate_hides_endpoint(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    user: User,
    processing_setup: tuple[
        ExternalCalendarChange,
        Task,
        ScheduledSession,
        SchedulePlan,
        CalendarEventMapping,
    ],
) -> None:
    change, *_ = processing_setup
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()
    assert process(client, change, user).status_code == 404
    assert (
        "/internal/api/external-calendar-changes/{change_id}/process"
        in app.openapi()["paths"]
    )
