import uuid
from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.calendar_integration.models import (
    CalendarBusyInterval,
    CalendarBusyResult,
    CalendarProviderConnection,
    ExternalCalendar,
)
from app.calendar_integration.security import FernetTokenCipher
from app.core.config import get_settings
from app.internal.calendar_router import calendar_runtime
from app.main import app
from app.models import (
    CalendarConnection,
    CalendarConnectionStatus,
    CalendarProviderName,
    CalendarSelection,
    User,
)


class FakeProvider:
    def __init__(self) -> None:
        self.list_calls: list[uuid.UUID] = []
        self.busy_calls: list[uuid.UUID] = []

    async def list_calendars(
        self, connection: CalendarProviderConnection
    ) -> list[ExternalCalendar]:
        self.list_calls.append(connection.connection_id)
        return [
            ExternalCalendar(
                id=f"{connection.connection_id}-primary",
                name=f"Primary {connection.connection_id}",
                primary=True,
            ),
            ExternalCalendar(
                id=f"{connection.connection_id}-team",
                name=f"Team {connection.connection_id}",
            ),
        ]

    async def query_busy_intervals(
        self,
        connection: CalendarProviderConnection,
        *,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
        timezone: str,
    ) -> CalendarBusyResult:
        self.busy_calls.append(connection.connection_id)
        return CalendarBusyResult(
            time_min=time_min,
            time_max=time_max,
            timezone=timezone,
            intervals=[
                CalendarBusyInterval(
                    start=time_min,
                    end=time_min,
                    calendar_id=calendar_ids[0],
                )
            ],
            errors=[],
        )


class FakeOAuth:
    def __init__(self) -> None:
        self.revoked: list[str] = []

    async def revoke_token(self, token: str) -> None:
        self.revoked.append(token)


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
def multi_account_setup(
    db_session: Session,
    user: User,
) -> tuple[list[CalendarConnection], FakeProvider, FakeOAuth]:
    cipher = FernetTokenCipher(Fernet.generate_key().decode())
    connections = [
        CalendarConnection(
            id=uuid.uuid4(),
            user_id=user.id,
            provider=CalendarProviderName.google,
            provider_account_id=f"account-{index}",
            provider_account_email=f"account-{index}@example.com",
            access_token_encrypted=cipher.encrypt(f"access-{index}"),
            refresh_token_encrypted=cipher.encrypt(f"refresh-{index}"),
            status=CalendarConnectionStatus.active,
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        )
        for index in (1, 2)
    ]
    for connection in connections:
        connection.selections.append(
            CalendarSelection(
                external_calendar_id=f"{connection.id}-primary",
                display_name="Primary",
                primary=True,
                include_in_availability=True,
            )
        )
    db_session.add_all(connections)
    db_session.commit()
    provider = FakeProvider()
    oauth = FakeOAuth()
    app.dependency_overrides[calendar_runtime] = lambda: SimpleNamespace(
        provider=provider,
        oauth=oauth,
        cipher=cipher,
    )
    return connections, provider, oauth


def test_connection_operations_use_the_requested_account(
    client: TestClient,
    user: User,
    multi_account_setup: tuple[list[CalendarConnection], FakeProvider, FakeOAuth],
) -> None:
    connections, provider, _ = multi_account_setup
    first, second = connections

    listed = client.get(
        "/internal/api/calendar/connections",
        params={"user_id": str(user.id)},
    )
    diagnostics = client.get(
        f"/internal/api/calendar/connections/{second.id}",
        params={"user_id": str(user.id)},
    )
    calendars = client.get(
        f"/internal/api/calendar/connections/{second.id}/calendars",
        params={"user_id": str(user.id)},
    )
    selection = client.put(
        f"/internal/api/calendar/connections/{second.id}/selections",
        params={"user_id": str(user.id)},
        json={"calendar_ids": [f"{second.id}-team"]},
    )
    busy = client.post(
        f"/internal/api/calendar/connections/{first.id}/free-busy",
        params={"user_id": str(user.id)},
        json={
            "time_min": "2026-07-30T08:00:00Z",
            "time_max": "2026-07-30T09:00:00Z",
            "timezone": "UTC",
            "calendar_ids": [f"{first.id}-primary"],
        },
    )

    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["connections"]} == {
        str(first.id),
        str(second.id),
    }
    assert diagnostics.json()["provider_account_email"] == "account-2@example.com"
    assert diagnostics.json()["provider_account_id"] == "account-2"
    assert calendars.status_code == 200
    assert provider.list_calls[-1] == second.id
    assert selection.status_code == 200
    assert {
        item["id"]
        for item in selection.json()["calendars"]
        if item["include_in_availability"]
    } == {f"{second.id}-team"}
    assert busy.status_code == 200
    assert provider.busy_calls == [first.id]


def test_disconnect_one_connection_preserves_other_accounts(
    client: TestClient,
    db_session: Session,
    user: User,
    multi_account_setup: tuple[list[CalendarConnection], FakeProvider, FakeOAuth],
) -> None:
    connections, _, oauth = multi_account_setup
    first, second = connections
    other_user = User(email="isolated-owner@example.com", timezone="UTC")
    other_connection = CalendarConnection(
        user=other_user,
        provider=CalendarProviderName.google,
        provider_account_id="other-user-account",
        access_token_encrypted=first.access_token_encrypted,
        status=CalendarConnectionStatus.active,
        scopes=[],
    )
    db_session.add(other_connection)
    db_session.commit()

    response = client.delete(
        f"/internal/api/calendar/connections/{first.id}",
        params={"user_id": str(user.id)},
    )
    db_session.refresh(first)
    db_session.refresh(second)
    db_session.refresh(other_connection)

    assert response.status_code == 204
    assert first.status is CalendarConnectionStatus.revoked
    assert second.status is CalendarConnectionStatus.active
    assert other_connection.status is CalendarConnectionStatus.active
    assert oauth.revoked == ["refresh-1"]


@pytest.mark.parametrize(
    ("method", "suffix", "json"),
    [
        ("get", "", None),
        ("get", "/calendars", None),
        ("put", "/selections", {"calendar_ids": []}),
        (
            "post",
            "/free-busy",
            {
                "time_min": "2026-07-30T08:00:00Z",
                "time_max": "2026-07-30T09:00:00Z",
                "timezone": "UTC",
            },
        ),
        ("delete", "", None),
    ],
)
def test_connection_operations_enforce_ownership(
    client: TestClient,
    db_session: Session,
    user: User,
    multi_account_setup: tuple[list[CalendarConnection], FakeProvider, FakeOAuth],
    method: str,
    suffix: str,
    json: object,
) -> None:
    connection = multi_account_setup[0][0]
    other = User(email="unauthorized-owner@example.com", timezone="UTC")
    db_session.add(other)
    db_session.commit()

    response = client.request(
        method,
        f"/internal/api/calendar/connections/{connection.id}{suffix}",
        params={"user_id": str(other.id)},
        json=json,
    )

    assert response.status_code == 404
