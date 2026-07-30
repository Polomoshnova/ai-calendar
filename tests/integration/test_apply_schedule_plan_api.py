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

from app.calendar_integration.errors import CalendarProviderError
from app.calendar_integration.models import (
    CalendarEventCreateRequest,
    CalendarEventCreateResult,
    CalendarProviderConnection,
)
from app.calendar_integration.security import FernetTokenCipher
from app.calendar_sync.snapshots import SessionWriteTargetSnapshot
from app.core.config import get_settings
from app.internal.calendar_router import calendar_runtime
from app.main import app
from app.models import (
    CalendarConnection,
    CalendarConnectionStatus,
    CalendarEventMapping,
    CalendarProviderName,
    ScheduledSession,
    SchedulePlan,
    User,
)
from app.schedule_plans.models import (
    ScheduledSessionStatus,
    SchedulePlanSource,
    SchedulePlanStatus,
)

NOW = datetime(2026, 7, 30, 8, tzinfo=UTC)


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[CalendarEventCreateRequest] = []
        self.failures: set[uuid.UUID] = set()

    async def create_event(
        self,
        _connection: CalendarProviderConnection,
        request: CalendarEventCreateRequest,
    ) -> CalendarEventCreateResult:
        self.calls.append(request)
        if request.scheduled_session_id in self.failures:
            raise CalendarProviderError("Safe provider failure.")
        return CalendarEventCreateResult(
            external_event_id=request.event_id,
            calendar_id=request.calendar_id,
            connection_id=request.connection_id,
            provider_account_id=request.provider_account_id,
            start=request.start,
            end=request.end,
            etag='"etag"',
            provider_updated_at=NOW,
        )


@pytest.fixture(autouse=True)
def enable_internal_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    yield
    app.dependency_overrides.pop(calendar_runtime, None)
    get_settings.cache_clear()


@pytest.fixture
def apply_setup(
    db_session: Session,
    user: User,
) -> tuple[CalendarConnection, FakeProvider, FernetTokenCipher]:
    cipher = FernetTokenCipher(Fernet.generate_key().decode())
    connection = CalendarConnection(
        id=uuid.uuid4(),
        user_id=user.id,
        provider=CalendarProviderName.google,
        provider_account_id="google-account-1",
        provider_account_email="owner@example.com",
        access_token_encrypted=cipher.encrypt("access-token"),
        status=CalendarConnectionStatus.active,
        scopes=[
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/calendar.events",
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
    return connection, provider, cipher


def add_plan(
    session: Session,
    user: User,
    connection: CalendarConnection,
    *,
    status: SchedulePlanStatus = SchedulePlanStatus.confirmed,
    with_snapshot: bool = True,
    session_count: int = 2,
    target_connection_id: uuid.UUID | None = None,
) -> SchedulePlan:
    plan_id = uuid.uuid4()
    sessions = [
        ScheduledSession(
            id=uuid.uuid4(),
            plan_id=plan_id,
            title=f"Session {index}",
            description="Safe description",
            start=NOW + timedelta(hours=index),
            end=NOW + timedelta(hours=index + 1),
            duration_minutes=60,
            order=index,
            status=ScheduledSessionStatus.confirmed,
        )
        for index in range(1, session_count + 1)
    ]
    targets = [
        SessionWriteTargetSnapshot(
            scheduled_session_id=item.id,
            connection_id=target_connection_id or connection.id,
            provider=CalendarProviderName.google,
            provider_account_id=connection.provider_account_id,
            calendar_id=f"target-{item.order}@example.com",
        ).model_dump(mode="json")
        for item in sessions
    ]
    plan = SchedulePlan(
        id=plan_id,
        user_id=user.id,
        plan_group_id=uuid.uuid4(),
        source=SchedulePlanSource.calendar_backed_preview,
        version=1,
        status=status,
        timezone="Europe/Warsaw",
        planning_window_start=NOW,
        planning_window_end=NOW + timedelta(days=1),
        scheduler_version="test",
        idempotency_key=str(uuid.uuid4()),
        confirmed_task_snapshot={"title": "Task"},
        scheduling_preferences_snapshot={},
        busy_context_summary={},
        preview_metadata={},
        write_targets_snapshot=targets if with_snapshot else None,
        sessions=sessions,
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def apply(client: TestClient, plan: SchedulePlan, user: User) -> Response:
    return cast(
        Response,
        client.post(
            f"/internal/api/schedule-plans/{plan.id}/apply",
            params={"user_id": str(user.id)},
        ),
    )


def test_successful_apply_creates_all_mappings_and_is_idempotent(
    client: TestClient,
    db_session: Session,
    user: User,
    apply_setup: tuple[CalendarConnection, FakeProvider, FernetTokenCipher],
) -> None:
    connection, provider, _ = apply_setup
    plan = add_plan(db_session, user, connection)

    first = apply(client, plan, user)
    second = apply(client, plan, user)

    assert first.status_code == 200
    assert first.json()["resulting_status"] == "applied", first.json()
    assert first.json()["sessions_applied"] == 2
    assert {item.calendar_id for item in provider.calls} == {
        "target-1@example.com",
        "target-2@example.com",
    }
    assert len(provider.calls) == 2
    assert second.status_code == 200
    assert second.json()["sessions_already_applied"] == 2
    assert len(provider.calls) == 2
    assert (
        db_session.scalar(select(func.count()).select_from(CalendarEventMapping)) == 2
    )
    db_session.refresh(plan)
    assert plan.status is SchedulePlanStatus.applied
    assert all(item.status is ScheduledSessionStatus.applied for item in plan.sessions)


def test_one_failure_continues_and_produces_partial_result(
    client: TestClient,
    db_session: Session,
    user: User,
    apply_setup: tuple[CalendarConnection, FakeProvider, FernetTokenCipher],
) -> None:
    connection, provider, _ = apply_setup
    plan = add_plan(db_session, user, connection)
    provider.failures.add(plan.sessions[0].id)

    response = apply(client, plan, user)

    assert response.status_code == 200
    assert response.json()["resulting_status"] == "partially_applied"
    assert response.json()["sessions_applied"] == 1
    assert response.json()["sessions_failed"] == 1
    assert len(provider.calls) == 2
    assert "Safe provider failure." in str(response.json())
    assert "access-token" not in str(response.json())

    provider.failures.clear()
    retry = apply(client, plan, user)
    assert retry.json()["resulting_status"] == "applied"
    assert retry.json()["sessions_already_applied"] == 1
    assert retry.json()["sessions_attempted"] == 1
    assert len(provider.calls) == 3


def test_complete_provider_failure_produces_failed_plan(
    client: TestClient,
    db_session: Session,
    user: User,
    apply_setup: tuple[CalendarConnection, FakeProvider, FernetTokenCipher],
) -> None:
    connection, provider, _ = apply_setup
    plan = add_plan(db_session, user, connection)
    provider.failures = {item.id for item in plan.sessions}

    response = apply(client, plan, user)

    assert response.status_code == 200
    assert response.json()["resulting_status"] == "failed"
    assert response.json()["sessions_failed"] == 2
    assert len(provider.calls) == 2


@pytest.mark.parametrize(
    "case",
    ["missing_snapshot", "missing_connection", "inactive_connection"],
)
def test_invalid_targets_fail_before_provider_calls(
    client: TestClient,
    db_session: Session,
    user: User,
    apply_setup: tuple[CalendarConnection, FakeProvider, FernetTokenCipher],
    case: str,
) -> None:
    connection, provider, _ = apply_setup
    plan = add_plan(
        db_session,
        user,
        connection,
        with_snapshot=case != "missing_snapshot",
        target_connection_id=(uuid.uuid4() if case == "missing_connection" else None),
    )
    if case == "inactive_connection":
        connection.status = CalendarConnectionStatus.revoked
        db_session.commit()

    response = apply(client, plan, user)

    assert response.status_code == 422
    assert provider.calls == []
    db_session.refresh(plan)
    assert plan.status is SchedulePlanStatus.confirmed


@pytest.mark.parametrize(
    "status",
    [
        SchedulePlanStatus.proposed,
        SchedulePlanStatus.obsolete,
        SchedulePlanStatus.revalidation_required,
        SchedulePlanStatus.applying,
        SchedulePlanStatus.failed,
    ],
)
def test_ineligible_statuses_are_rejected(
    client: TestClient,
    db_session: Session,
    user: User,
    apply_setup: tuple[CalendarConnection, FakeProvider, FernetTokenCipher],
    status: SchedulePlanStatus,
) -> None:
    connection, provider, _ = apply_setup
    plan = add_plan(db_session, user, connection, status=status)

    response = apply(client, plan, user)

    assert response.status_code == 409
    assert provider.calls == []


def test_ownership_isolation_and_internal_gate(
    client: TestClient,
    db_session: Session,
    user: User,
    apply_setup: tuple[CalendarConnection, FakeProvider, FernetTokenCipher],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection, provider, _ = apply_setup
    plan = add_plan(db_session, user, connection)
    other = User(email="other-apply-owner@example.com", timezone="UTC")
    db_session.add(other)
    db_session.commit()

    forbidden = client.post(
        f"/internal/api/schedule-plans/{plan.id}/apply",
        params={"user_id": str(other.id)},
    )
    assert forbidden.status_code == 404
    assert provider.calls == []

    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()
    hidden = apply(client, plan, user)
    assert hidden.status_code == 404
