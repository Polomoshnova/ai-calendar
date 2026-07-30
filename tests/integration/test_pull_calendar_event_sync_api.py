import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calendar_integration.errors import (
    CalendarAuthorizationError,
    CalendarEventNotFoundError,
    CalendarUnavailableError,
)
from app.calendar_integration.models import (
    CalendarEventSnapshot,
    CalendarProviderConnection,
)
from app.calendar_integration.security import FernetTokenCipher
from app.core.config import get_settings
from app.internal.calendar_router import calendar_runtime
from app.main import app
from app.models import (
    CalendarConnection,
    CalendarConnectionStatus,
    CalendarEventMapping,
    CalendarProviderName,
    ExternalCalendarChange,
    ScheduledSession,
    SchedulePlan,
    User,
)
from app.models.calendar_sync import ExternalChangeType, SyncStatus
from app.schedule_plans.models import (
    ScheduledSessionStatus,
    SchedulePlanSource,
    SchedulePlanStatus,
)

NOW = datetime(2026, 7, 30, 8, tzinfo=UTC)


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, str, str]] = []
        self.snapshot = CalendarEventSnapshot(
            external_event_id="event-1",
            calendar_id="primary",
            start=NOW,
            end=NOW + timedelta(hours=1),
            timezone="Europe/Warsaw",
            etag='"v1"',
            provider_updated_at=NOW,
            provider_status="confirmed",
        )
        self.error: Exception | None = None

    async def get_event(
        self,
        connection: CalendarProviderConnection,
        *,
        calendar_id: str,
        external_event_id: str,
    ) -> CalendarEventSnapshot:
        self.calls.append((connection.connection_id, calendar_id, external_event_id))
        if self.error is not None:
            raise self.error
        return self.snapshot


@pytest.fixture(autouse=True)
def enable_internal_tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    yield
    app.dependency_overrides.pop(calendar_runtime, None)
    get_settings.cache_clear()


@pytest.fixture
def sync_setup(
    db_session: Session,
    user: User,
) -> tuple[CalendarEventMapping, FakeProvider]:
    cipher = FernetTokenCipher(Fernet.generate_key().decode())
    connection = CalendarConnection(
        id=uuid.uuid4(),
        user_id=user.id,
        provider=CalendarProviderName.google,
        provider_account_id="account-1",
        access_token_encrypted=cipher.encrypt("access-token"),
        status=CalendarConnectionStatus.active,
    )
    plan = SchedulePlan(
        id=uuid.uuid4(),
        user_id=user.id,
        plan_group_id=uuid.uuid4(),
        source=SchedulePlanSource.calendar_backed_preview,
        version=1,
        status=SchedulePlanStatus.applied,
        timezone="Europe/Warsaw",
        planning_window_start=NOW,
        planning_window_end=NOW + timedelta(days=1),
        scheduler_version="test",
        idempotency_key=str(uuid.uuid4()),
        confirmed_task_snapshot={"title": "Task"},
        scheduling_preferences_snapshot={},
        busy_context_summary={},
        preview_metadata={},
    )
    scheduled = ScheduledSession(
        id=uuid.uuid4(),
        plan=plan,
        title="Session",
        start=NOW,
        end=NOW + timedelta(hours=1),
        duration_minutes=60,
        order=1,
        status=ScheduledSessionStatus.applied,
    )
    mapping = CalendarEventMapping(
        id=uuid.uuid4(),
        scheduled_session=scheduled,
        calendar_connection=connection,
        provider=CalendarProviderName.google,
        provider_account_id="account-1",
        calendar_id="primary",
        external_event_id="event-1",
        etag='"v1"',
        provider_updated_at=NOW,
        sync_status=SyncStatus.synced,
        last_synced_at=NOW,
    )
    db_session.add_all([connection, plan, mapping])
    db_session.commit()
    provider = FakeProvider()
    app.dependency_overrides[calendar_runtime] = lambda: SimpleNamespace(
        provider=provider,
        oauth=SimpleNamespace(),
        cipher=cipher,
    )
    return mapping, provider


def sync(client: TestClient, mapping: CalendarEventMapping, user: User) -> Response:
    return cast(
        Response,
        client.post(
            f"/internal/api/calendar-event-mappings/{mapping.id}/sync",
            params={"user_id": str(user.id)},
        ),
    )


def test_unchanged_pull_is_idempotent_and_uses_requested_identity(
    client: TestClient,
    db_session: Session,
    user: User,
    sync_setup: tuple[CalendarEventMapping, FakeProvider],
) -> None:
    mapping, provider = sync_setup

    first = sync(client, mapping, user)
    second = sync(client, mapping, user)

    assert first.status_code == 200
    assert first.json()["outcome"] == "no_change"
    assert second.json()["outcome"] == "no_change"
    assert provider.calls == [
        (mapping.calendar_connection_id, "primary", "event-1"),
        (mapping.calendar_connection_id, "primary", "event-1"),
    ]
    assert (
        db_session.scalar(select(func.count()).select_from(ExternalCalendarChange)) == 0
    )


@pytest.mark.parametrize(
    ("start_delta", "end_delta"),
    [(timedelta(hours=1), timedelta(hours=1)), (timedelta(), timedelta(hours=1))],
)
def test_moved_event_creates_one_change_without_mutating_plan_or_session(
    client: TestClient,
    db_session: Session,
    user: User,
    sync_setup: tuple[CalendarEventMapping, FakeProvider],
    start_delta: timedelta,
    end_delta: timedelta,
) -> None:
    mapping, provider = sync_setup
    original_plan_status = mapping.scheduled_session.plan.status
    original_start = mapping.scheduled_session.start
    provider.snapshot = provider.snapshot.model_copy(
        update={
            "start": NOW + start_delta,
            "end": NOW + timedelta(hours=1) + end_delta,
            "etag": '"v2"',
            "provider_updated_at": NOW + timedelta(minutes=1),
        }
    )

    response = sync(client, mapping, user)
    repeated = sync(client, mapping, user)

    assert response.json()["outcome"] == "change_detected"
    assert response.json()["change_kind"] == "moved"
    assert repeated.json()["outcome"] == "no_change"
    changes = list(db_session.scalars(select(ExternalCalendarChange)))
    assert len(changes) == 1
    assert changes[0].change_type is ExternalChangeType.moved
    db_session.refresh(mapping.scheduled_session)
    db_session.refresh(mapping.scheduled_session.plan)
    assert mapping.scheduled_session.start == original_start
    assert mapping.scheduled_session.plan.status is original_plan_status


@pytest.mark.parametrize("mode", ["missing", "cancelled"])
def test_missing_or_cancelled_event_is_recorded_not_deleted(
    client: TestClient,
    db_session: Session,
    user: User,
    sync_setup: tuple[CalendarEventMapping, FakeProvider],
    mode: str,
) -> None:
    mapping, provider = sync_setup
    if mode == "missing":
        provider.error = CalendarEventNotFoundError("not found")
    else:
        provider.snapshot = CalendarEventSnapshot(
            external_event_id="event-1",
            calendar_id="primary",
            cancelled=True,
            etag='"v2"',
            provider_status="cancelled",
        )

    response = sync(client, mapping, user)

    assert response.json()["outcome"] == "external_event_missing"
    assert response.json()["change_kind"] == "deleted"
    db_session.refresh(mapping)
    assert mapping.sync_status is SyncStatus.externally_deleted
    assert db_session.get(CalendarEventMapping, mapping.id) is not None


@pytest.mark.parametrize(
    "error",
    [
        CalendarUnavailableError("temporary"),
        CalendarAuthorizationError("authorization failed"),
    ],
)
def test_provider_failure_does_not_create_deletion_or_mutate_baseline(
    client: TestClient,
    db_session: Session,
    user: User,
    sync_setup: tuple[CalendarEventMapping, FakeProvider],
    error: Exception,
) -> None:
    mapping, provider = sync_setup
    provider.error = error

    response = sync(client, mapping, user)

    assert response.status_code == 200
    assert response.json()["outcome"] == "provider_error"
    assert "access-token" not in response.text
    assert (
        db_session.scalar(select(func.count()).select_from(ExternalCalendarChange)) == 0
    )
    db_session.refresh(mapping)
    assert mapping.last_synced_snapshot is None


def test_ownership_and_inactive_connection_are_enforced(
    client: TestClient,
    db_session: Session,
    user: User,
    sync_setup: tuple[CalendarEventMapping, FakeProvider],
) -> None:
    mapping, provider = sync_setup
    other = User(id=uuid.uuid4(), email="other@example.com", timezone="UTC")
    db_session.add(other)
    db_session.commit()

    assert sync(client, mapping, other).status_code == 404
    mapping.calendar_connection.status = CalendarConnectionStatus.revoked
    db_session.commit()
    assert sync(client, mapping, user).status_code == 422
    assert provider.calls == []
