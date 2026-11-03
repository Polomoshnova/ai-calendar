import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.calendar_integration.errors import CalendarUnavailableError
from app.calendar_integration.models import (
    CalendarBusyResult,
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
        self.connection_calls: list[uuid.UUID] = []
        self.busy_calendar_ids: list[list[str]] = []

    async def list_events(
        self,
        _connection: CalendarProviderConnection,
        *,
        calendar_id: str,
        **_kwargs: object,
    ) -> list[ExternalCalendarEvent]:
        self.calls.append(calendar_id)
        self.connection_calls.append(_connection.connection_id)
        if calendar_id in self.failures:
            raise CalendarUnavailableError("Google Calendar is unavailable")
        return self.events.get(calendar_id, [])

    async def query_busy_intervals(
        self,
        _connection: CalendarProviderConnection,
        *,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
        timezone: str,
    ) -> CalendarBusyResult:
        self.busy_calendar_ids.append(calendar_ids)
        return CalendarBusyResult(
            time_min=time_min,
            time_max=time_max,
            timezone=timezone,
            intervals=[],
            errors=[],
        )


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
                include_in_day_view=True,
                include_in_availability=True,
            ),
            CalendarSelection(
                external_calendar_id="ignored",
                display_name="Ignored",
                include_in_day_view=False,
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


@pytest.mark.parametrize(
    ("include_in_day_view", "include_in_availability"),
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_visibility_and_availability_are_independent(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
    include_in_day_view: bool,
    include_in_availability: bool,
) -> None:
    connection, provider = calendar_setup
    primary = next(
        item for item in connection.selections if item.external_calendar_id == "primary"
    )
    primary.include_in_day_view = include_in_day_view
    primary.include_in_availability = include_in_availability
    db_session.commit()
    provider.events["primary"] = [
        ExternalCalendarEvent(
            external_event_id="matrix-event",
            calendar_id="primary",
            title="Matrix event",
            start=DAY,
            end=DAY + timedelta(hours=1),
        )
    ]

    day_response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})
    busy_response = client.post(
        f"/internal/api/calendar/connections/{connection.id}/free-busy",
        params={"user_id": str(user.id)},
        json={
            "time_min": "2026-11-03T00:00:00Z",
            "time_max": "2026-11-04T00:00:00Z",
            "timezone": "UTC",
        },
    )

    assert day_response.status_code == 200
    assert bool(day_response.json()["items"]) is include_in_day_view
    assert ("primary" in provider.calls) is include_in_day_view
    if include_in_day_view:
        assert day_response.json()["sync_state"]["status"] == "fresh"
    else:
        assert day_response.json()["sync_state"]["status"] == ("no_calendars_selected")
    if include_in_availability:
        assert busy_response.status_code == 200
        assert provider.busy_calendar_ids == [["primary"]]
    else:
        assert busy_response.status_code == 400
        assert provider.busy_calendar_ids == []


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
    assert payload["warnings"] == []
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
    mapped_on_secondary, _ = add_session(
        db_session,
        user,
        plan_status=SchedulePlanStatus.applied,
        session_status=ScheduledSessionStatus.applied,
        title="Secondary mapped task",
    )
    connection.selections.append(
        CalendarSelection(
            external_calendar_id="secondary",
            display_name="Secondary",
            include_in_day_view=True,
            include_in_availability=True,
        )
    )
    db_session.add(
        CalendarEventMapping(
            scheduled_session=mapped_on_secondary,
            calendar_connection=connection,
            provider=CalendarProviderName.google,
            provider_account_id="owner@example.com",
            calendar_id="secondary",
            external_event_id="missing-on-successful-calendar",
            sync_status=SyncStatus.synced,
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
    assert [warning["code"] for warning in payload["warnings"]] == [
        "mapped_event_not_returned"
    ]
    assert {item["id"] for item in payload["items"]} == {
        f"session:{scheduled.id}",
        f"session:{mapped_on_secondary.id}",
        f"google:{connection.id}:secondary:external",
    }
    mapped_item = next(
        item
        for item in payload["items"]
        if item["id"] == f"session:{mapped_on_secondary.id}"
    )
    assert mapped_item["status"] == "mapped_event_not_returned"
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
    assert len(payload["items"]) == 1
    assert payload["items"][0]["kind"] == "needs_attention"
    assert payload["items"][0]["status"] == "external_event_cancelled"
    assert payload["sync_state"] == {"status": "fresh", "partial": False, "errors": []}
    assert payload["warnings"][0]["code"] == "external_event_cancelled"


def test_partial_loading_keeps_consistency_warning_separate(
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
    connection.selections.append(
        CalendarSelection(
            external_calendar_id="secondary",
            display_name="Secondary",
            include_in_day_view=True,
            include_in_availability=True,
        )
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
    provider.failures.add("secondary")

    response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})

    payload = response.json()
    assert response.status_code == 200
    assert payload["sync_state"]["status"] == "partial"
    assert payload["sync_state"]["partial"] is True
    assert [error["code"] for error in payload["sync_state"]["errors"]] == [
        "calendar_provider_unavailable"
    ]
    assert [warning["code"] for warning in payload["warnings"]] == [
        "external_event_cancelled"
    ]
    assert payload["items"][0]["kind"] == "needs_attention"


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


def test_missing_mapped_event_is_attention_not_confirmed_busy_item(
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
        title="Moved or missing task",
    )
    db_session.add(
        CalendarEventMapping(
            scheduled_session=applied,
            calendar_connection=connection,
            provider=CalendarProviderName.google,
            provider_account_id="owner@example.com",
            calendar_id="primary",
            external_event_id="not-returned",
            sync_status=SyncStatus.synced,
        )
    )
    db_session.commit()
    provider.events["primary"] = []

    response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})

    payload = response.json()
    assert response.status_code == 200
    assert len(payload["items"]) == 1
    assert payload["items"][0]["kind"] == "needs_attention"
    assert payload["items"][0]["status"] == "mapped_event_not_returned"
    assert payload["items"][0]["scheduled_session"]["session_id"] == str(applied.id)
    assert payload["sync_state"] == {"status": "fresh", "partial": False, "errors": []}
    assert payload["warnings"][0]["code"] == "mapped_event_not_returned"
    assert "deleted" not in payload["warnings"][0]["message"].lower()


def test_mapped_session_is_unverified_when_its_calendar_failed(
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
        title="Unverified task",
    )
    db_session.add(
        CalendarEventMapping(
            scheduled_session=applied,
            calendar_connection=connection,
            provider=CalendarProviderName.google,
            provider_account_id="owner@example.com",
            calendar_id="primary",
            external_event_id="unverified",
            sync_status=SyncStatus.synced,
        )
    )
    db_session.commit()
    provider.failures.add("primary")

    response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})

    payload = response.json()
    assert payload["items"][0]["kind"] == "needs_attention"
    assert payload["items"][0]["status"] == "unverified"
    assert payload["sync_state"]["status"] == "error"
    assert payload["warnings"] == []
    assert all(
        error["code"] != "external_event_missing"
        for error in payload["sync_state"]["errors"]
    )


def test_hidden_mapped_session_is_not_returned_or_reported(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    primary = next(
        item for item in connection.selections if item.external_calendar_id == "primary"
    )
    primary.include_in_day_view = False
    applied, _ = add_session(
        db_session,
        user,
        plan_status=SchedulePlanStatus.applied,
        session_status=ScheduledSessionStatus.applied,
        title="Hidden mapped task",
    )
    db_session.add(
        CalendarEventMapping(
            scheduled_session=applied,
            calendar_connection=connection,
            provider=CalendarProviderName.google,
            provider_account_id="owner@example.com",
            calendar_id="primary",
            external_event_id="hidden-event",
            sync_status=SyncStatus.synced,
        )
    )
    db_session.commit()

    response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})

    payload = response.json()
    assert response.status_code == 200
    assert payload["sync_state"]["status"] == "no_calendars_selected"
    assert payload["items"] == []
    assert payload["warnings"] == []
    assert provider.calls == []


def test_calendar_day_query_count_does_not_grow_per_connection(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, _provider = calendar_setup

    def count_selects() -> int:
        query_count = 0

        def listener(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            nonlocal query_count
            if statement.lstrip().upper().startswith("SELECT"):
                query_count += 1

        event.listen(db_session.get_bind(), "before_cursor_execute", listener)
        try:
            response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})
            assert response.status_code == 200
        finally:
            event.remove(db_session.get_bind(), "before_cursor_execute", listener)
        return query_count

    baseline = count_selects()
    second = CalendarConnection(
        user_id=user.id,
        provider=CalendarProviderName.google,
        provider_account_id="second-account",
        access_token_encrypted=connection.access_token_encrypted,
        status=CalendarConnectionStatus.active,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        selections=[
            CalendarSelection(
                external_calendar_id="second-primary",
                display_name="Second primary",
                primary=True,
                include_in_day_view=False,
                include_in_availability=True,
            )
        ],
    )
    db_session.add(second)
    db_session.commit()

    assert count_selects() == baseline


@pytest.mark.parametrize("selection_mode", ["missing", "disabled"])
def test_connection_without_selected_calendars_has_explicit_state(
    client: TestClient,
    db_session: Session,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
    selection_mode: str,
) -> None:
    connection, provider = calendar_setup
    if selection_mode == "missing":
        connection.selections.clear()
    else:
        for selection in connection.selections:
            selection.include_in_day_view = False
    db_session.commit()

    response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})

    assert response.json()["sync_state"]["status"] == "no_calendars_selected"
    assert response.json()["warnings"] == []
    assert provider.calls == []


def test_no_connection_has_not_connected_state(
    client: TestClient,
    db_session: Session,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    db_session.delete(connection)
    db_session.commit()

    response = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})

    assert response.json()["sync_state"]["status"] == "not_connected"
    assert provider.calls == []


def test_dev_identity_ignores_query_and_header_and_filters_other_owner(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    own_connection, provider = calendar_setup
    own_session, _ = add_session(
        db_session,
        user,
        plan_status=SchedulePlanStatus.confirmed,
        session_status=ScheduledSessionStatus.confirmed,
        title="Owner task",
    )
    other = User(email="other-calendar-owner@example.com", timezone="UTC")
    db_session.add(other)
    db_session.flush()
    other_connection = CalendarConnection(
        user_id=other.id,
        provider=CalendarProviderName.google,
        provider_account_id="other@example.com",
        access_token_encrypted=own_connection.access_token_encrypted,
        status=CalendarConnectionStatus.active,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        selections=[
            CalendarSelection(
                external_calendar_id="other-primary",
                display_name="Other primary",
                include_in_day_view=True,
                include_in_availability=True,
            )
        ],
    )
    db_session.add(other_connection)
    other_session, _ = add_session(
        db_session,
        other,
        plan_status=SchedulePlanStatus.confirmed,
        session_status=ScheduledSessionStatus.confirmed,
        title="Other private task",
    )
    db_session.commit()

    response = client.get(
        "/api/v1/calendar/day",
        params={"date": "2026-11-03", "user_id": str(other.id)},
        headers={"X-User-Id": str(other.id)},
    )

    payload = response.json()
    assert response.status_code == 200
    assert {item["scheduled_session"]["session_id"] for item in payload["items"]} == {
        str(own_session.id)
    }
    assert all(
        item["scheduled_session"]["session_id"] != str(other_session.id)
        for item in payload["items"]
    )
    assert provider.connection_calls == [own_connection.id]
    assert other_connection.id not in provider.connection_calls
    assert "access-token" not in response.text


def test_missing_or_unknown_dev_user_configuration_is_closed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    _connection, provider = calendar_setup
    monkeypatch.delenv("CALENDAR_DAY_DEV_USER_EMAIL", raising=False)
    get_settings.cache_clear()
    missing = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})
    assert missing.status_code == 404

    monkeypatch.setenv("CALENDAR_DAY_DEV_USER_EMAIL", "unknown@example.com")
    get_settings.cache_clear()
    unknown = client.get("/api/v1/calendar/day", params={"date": "2026-11-03"})
    assert unknown.status_code == 404
    assert provider.calls == []


def test_warsaw_fallback_keeps_both_repeated_hour_instances(
    client: TestClient,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    _connection, provider = calendar_setup
    provider.events["primary"] = [
        ExternalCalendarEvent(
            external_event_id="first-0230",
            calendar_id="primary",
            title="First 02:30",
            start=datetime.fromisoformat("2026-10-25T02:30:00+02:00"),
            end=datetime.fromisoformat("2026-10-25T02:45:00+02:00"),
            recurring_event_id="master",
        ),
        ExternalCalendarEvent(
            external_event_id="second-0230",
            calendar_id="primary",
            title="Second 02:30",
            start=datetime.fromisoformat("2026-10-25T02:30:00+01:00"),
            end=datetime.fromisoformat("2026-10-25T02:45:00+01:00"),
            recurring_event_id="master",
        ),
    ]

    response = client.get("/api/v1/calendar/day", params={"date": "2026-10-25"})

    payload = response.json()
    assert payload["day_start"] == "2026-10-24T22:00:00Z"
    assert payload["day_end"] == "2026-10-25T23:00:00Z"
    assert [item["title"] for item in payload["items"]] == [
        "First 02:30",
        "Second 02:30",
    ]
    assert payload["items"][0]["start"] != payload["items"][1]["start"]


def test_warsaw_spring_forward_and_all_day_overlap_semantics(
    client: TestClient,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    _connection, provider = calendar_setup
    zone = ZoneInfo("Europe/Warsaw")
    provider.events["primary"] = [
        ExternalCalendarEvent(
            external_event_id="before-shift",
            calendar_id="primary",
            title="Before shift",
            start=datetime.fromisoformat("2026-03-29T01:30:00+01:00"),
            end=datetime.fromisoformat("2026-03-29T01:45:00+01:00"),
        ),
        ExternalCalendarEvent(
            external_event_id="after-shift",
            calendar_id="primary",
            title="After shift",
            start=datetime.fromisoformat("2026-03-29T03:15:00+02:00"),
            end=datetime.fromisoformat("2026-03-29T03:30:00+02:00"),
        ),
        ExternalCalendarEvent(
            external_event_id="one-day",
            calendar_id="primary",
            title="One day",
            start=datetime(2026, 3, 29, tzinfo=zone),
            end=datetime(2026, 3, 30, tzinfo=zone),
            all_day=True,
        ),
        ExternalCalendarEvent(
            external_event_id="multi-day",
            calendar_id="primary",
            title="Multi day",
            start=datetime(2026, 3, 28, tzinfo=zone),
            end=datetime(2026, 3, 31, tzinfo=zone),
            all_day=True,
        ),
    ]

    response = client.get("/api/v1/calendar/day", params={"date": "2026-03-29"})

    payload = response.json()
    assert payload["day_start"] == "2026-03-28T23:00:00Z"
    assert payload["day_end"] == "2026-03-29T22:00:00Z"
    assert {item["title"] for item in payload["items"]} == {
        "Before shift",
        "After shift",
        "One day",
        "Multi day",
    }
    one_day = next(item for item in payload["items"] if item["title"] == "One day")
    assert one_day["all_day"] is True
    assert (
        datetime.fromisoformat(one_day["end"])
        - datetime.fromisoformat(one_day["start"])
    ).total_seconds() == 23 * 3600


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
    assert {item["title"] for item in payload["items"]} == {
        "Cross midnight",
        "Externally deleted task",
    }
    deleted_item = next(
        item for item in payload["items"] if item["title"] == "Externally deleted task"
    )
    assert deleted_item["kind"] == "needs_attention"
    assert deleted_item["status"] == "externally_deleted"
    assert all(
        item["scheduled_session"] is None
        or item["scheduled_session"]["session_id"] != str(proposed.id)
        for item in payload["items"]
    )
    assert payload["warnings"][0]["code"] == "external_event_missing"


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
