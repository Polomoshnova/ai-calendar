import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.calendar_integration.errors import CalendarUnavailableError
from app.calendar_integration.models import (
    CalendarProviderConnection,
    ExternalCalendarEvent,
)
from app.calendar_integration.security import FernetTokenCipher
from app.core.config import get_settings
from app.internal.calendar_router import calendar_runtime
from app.main import app
from app.models import (
    CalendarConnection,
    CalendarEventMapping,
    CalendarProviderName,
    CalendarSelection,
    ScheduledSession,
    SchedulePlan,
    Task,
    User,
)
from app.models.calendar import CalendarConnectionStatus
from app.models.calendar_sync import SyncStatus
from app.schedule_plans.models import (
    ScheduledSessionStatus,
    SchedulePlanSource,
    SchedulePlanStatus,
)

DAY = datetime(2026, 11, 3, 10, tzinfo=UTC)


class FakeProvider:
    def __init__(self) -> None:
        self.events: dict[str, list[ExternalCalendarEvent]] = {}
        self.failures: set[str] = set()
        self.calls: list[str] = []

    async def list_events(
        self,
        _connection: CalendarProviderConnection,
        *,
        calendar_id: str,
        **_kwargs: object,
    ) -> list[ExternalCalendarEvent]:
        self.calls.append(calendar_id)
        if calendar_id in self.failures:
            raise CalendarUnavailableError("Google Calendar is unavailable")
        return self.events.get(calendar_id, [])


@pytest.fixture(autouse=True)
def calendar_day_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    monkeypatch.setenv("CALENDAR_DAY_DEV_USER_EMAIL", "owner@example.com")
    get_settings.cache_clear()
    yield
    app.dependency_overrides.pop(calendar_runtime, None)
    get_settings.cache_clear()


@pytest.fixture
def calendar_setup(
    db_session: Session, user: User
) -> tuple[CalendarConnection, FakeProvider]:
    cipher = FernetTokenCipher(Fernet.generate_key().decode())
    connection = CalendarConnection(
        user_id=user.id,
        provider=CalendarProviderName.google,
        provider_account_id="owner@example.com",
        access_token_encrypted=cipher.encrypt("access-token"),
        status=CalendarConnectionStatus.active,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        selections=[
            CalendarSelection(
                external_calendar_id="primary",
                display_name="Primary",
                primary=True,
                include_in_availability=True,
            ),
            CalendarSelection(
                external_calendar_id="ignored",
                display_name="Ignored",
                include_in_availability=False,
            ),
        ],
    )
    db_session.add(connection)
    db_session.commit()
    provider = FakeProvider()
    app.dependency_overrides[calendar_runtime] = lambda: SimpleNamespace(
        provider=provider,
        oauth=SimpleNamespace(),
        cipher=cipher,
    )
    return connection, provider


def add_session(
    session: Session,
    user: User,
    *,
    plan_status: SchedulePlanStatus,
    session_status: ScheduledSessionStatus,
    title: str,
) -> tuple[ScheduledSession, Task]:
    task = Task(
        user_id=user.id,
        title=title,
        duration_minutes=60,
        minimum_session_minutes=15,
        maximum_sessions_per_day=1,
    )
    session.add(task)
    session.flush()
    plan = SchedulePlan(
        user_id=user.id,
        task_id=task.id,
        plan_group_id=uuid.uuid4(),
        source=SchedulePlanSource.calendar_backed_preview,
        version=1,
        status=plan_status,
        timezone=user.timezone,
        planning_window_start=DAY - timedelta(days=1),
        planning_window_end=DAY + timedelta(days=1),
        scheduler_version="test",
        idempotency_key=str(uuid.uuid4()),
        confirmed_task_snapshot={"title": title},
        scheduling_preferences_snapshot={},
        busy_context_summary={},
        preview_metadata={},
    )
    scheduled = ScheduledSession(
        task_id=task.id,
        title=title,
        start=DAY,
        end=DAY + timedelta(hours=1),
        duration_minutes=60,
        order=1,
        status=session_status,
    )
    plan.sessions.append(scheduled)
    session.add(plan)
    session.commit()
    return scheduled, task


def test_day_merges_mapped_session_and_keeps_confirmed_local_session(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    applied, _ = add_session(
        db_session,
        user,
        plan_status=SchedulePlanStatus.applied,
        session_status=ScheduledSessionStatus.applied,
        title="Applied task",
    )
    confirmed, _ = add_session(
        db_session,
        user,
        plan_status=SchedulePlanStatus.confirmed,
        session_status=ScheduledSessionStatus.confirmed,
        title="Confirmed task",
    )
    mapping = CalendarEventMapping(
        scheduled_session=applied,
        calendar_connection=connection,
        provider=CalendarProviderName.google,
        provider_account_id="owner@example.com",
        calendar_id="primary",
        external_event_id="applied-event",
        sync_status=SyncStatus.synced,
    )
    db_session.add(mapping)
    db_session.commit()
    provider.events["primary"] = [
        ExternalCalendarEvent(
            external_event_id="applied-event",
            calendar_id="primary",
            title="Applied task from Google",
            start=DAY,
            end=DAY + timedelta(hours=1),
            status="confirmed",
        )
    ]

    response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["sync_state"] == {"status": "fresh", "partial": False, "errors": []}
    assert len(payload["items"]) == 2
    google_item = next(
        item for item in payload["items"] if item["source"] == "google_calendar"
    )
    assert google_item["scheduled_session"]["session_id"] == str(applied.id)
    assert (
        sum(
            item["scheduled_session"]["session_id"] == str(applied.id)
            for item in payload["items"]
        )
        == 1
    )
    assert any(item["id"] == f"session:{confirmed.id}" for item in payload["items"])
    assert provider.calls == ["primary"]


def test_partial_failure_preserves_local_data_and_skips_unselected_calendar(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    scheduled, _ = add_session(
        db_session,
        user,
        plan_status=SchedulePlanStatus.confirmed,
        session_status=ScheduledSessionStatus.confirmed,
        title="Local task",
    )
    connection.selections.append(
        CalendarSelection(
            external_calendar_id="secondary",
            display_name="Secondary",
            include_in_availability=True,
        )
    )
    db_session.commit()
    provider.failures.add("primary")
    provider.events["secondary"] = [
        ExternalCalendarEvent(
            external_event_id="external",
            calendar_id="secondary",
            title="External event",
            start=DAY + timedelta(hours=2),
            end=DAY + timedelta(hours=3),
        )
    ]

    response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["sync_state"]["status"] == "partial"
    assert payload["sync_state"]["partial"] is True
    assert payload["sync_state"]["errors"][0]["code"] == "calendar_provider_unavailable"
    assert {item["id"] for item in payload["items"]} == {
        f"session:{scheduled.id}",
        f"google:{connection.id}:secondary:external",
    }
    assert provider.calls == ["primary", "secondary"]


def test_cancelled_mapped_event_is_reported_without_busy_item(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    applied, _ = add_session(
        db_session,
        user,
        plan_status=SchedulePlanStatus.applied,
        session_status=ScheduledSessionStatus.applied,
        title="Cancelled task",
    )
    db_session.add(
        CalendarEventMapping(
            scheduled_session=applied,
            calendar_connection=connection,
            provider=CalendarProviderName.google,
            provider_account_id="owner@example.com",
            calendar_id="primary",
            external_event_id="cancelled-event",
            sync_status=SyncStatus.synced,
        )
    )
    db_session.commit()
    provider.events["primary"] = [
        ExternalCalendarEvent(
            external_event_id="cancelled-event",
            calendar_id="primary",
            title="Cancelled task",
            status="cancelled",
        )
    ]

    response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})

    payload = response.json()
    assert response.status_code == 200
    assert payload["items"] == []
    assert payload["sync_state"]["status"] == "partial"
    assert payload["sync_state"]["errors"][0]["code"] == "external_event_cancelled"


def test_missing_scope_requires_reauthorization_without_provider_call(
    client: TestClient,
    db_session: Session,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    connection.scopes = []
    db_session.commit()

    response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})

    assert response.status_code == 200
    assert response.json()["sync_state"]["status"] == "reauthorization_required"
    assert provider.calls == []


def test_overlap_boundaries_and_hidden_session_lifecycle_states(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    proposed, _ = add_session(
        db_session,
        user,
        plan_status=SchedulePlanStatus.proposed,
        session_status=ScheduledSessionStatus.proposed,
        title="Proposed task",
    )
    deleted, _ = add_session(
        db_session,
        user,
        plan_status=SchedulePlanStatus.applied,
        session_status=ScheduledSessionStatus.applied,
        title="Externally deleted task",
    )
    db_session.add(
        CalendarEventMapping(
            scheduled_session=deleted,
            calendar_connection=connection,
            provider=CalendarProviderName.google,
            provider_account_id="owner@example.com",
            calendar_id="primary",
            external_event_id="deleted-event",
            sync_status=SyncStatus.externally_deleted,
        )
    )
    db_session.commit()
    day_start = datetime(2026, 11, 2, 23, tzinfo=UTC)
    day_end = datetime(2026, 11, 3, 23, tzinfo=UTC)
    provider.events["primary"] = [
        ExternalCalendarEvent(
            external_event_id="ends-at-start",
            calendar_id="primary",
            title="Previous day",
            start=day_start - timedelta(hours=1),
            end=day_start,
        ),
        ExternalCalendarEvent(
            external_event_id="starts-at-end",
            calendar_id="primary",
            title="Next day",
            start=day_end,
            end=day_end + timedelta(hours=1),
        ),
        ExternalCalendarEvent(
            external_event_id="crosses-midnight",
            calendar_id="primary",
            title="Cross midnight",
            start=day_start - timedelta(minutes=30),
            end=day_start + timedelta(minutes=30),
        ),
    ]

    response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})

    payload = response.json()
    assert response.status_code == 200
    assert [item["title"] for item in payload["items"]] == ["Cross midnight"]
    assert all(
        item["scheduled_session"] is None
        or item["scheduled_session"]["session_id"] != str(proposed.id)
        for item in payload["items"]
    )
    assert payload["sync_state"]["errors"][0]["code"] == "external_event_missing"


def test_invalid_date_and_production_isolation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    assert client.get("/api/v1/calendar/day", params={"date": "bad"}).status_code == 422

    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()
    response = client.get(
        "/api/v1/calendar/day",
        params={"date": "2026-11-03", "user_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404
