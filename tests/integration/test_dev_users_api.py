from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import User


@pytest.fixture(autouse=True)
def enable_internal_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_create_dev_user_persists_normalized_user(
    client: TestClient,
    db_session: Session,
) -> None:
    response = client.post(
        "/internal/api/dev/users",
        json={
            "email": "  Dev.User@Example.COM  ",
            "timezone": "Europe/Madrid",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] is True
    assert payload["email"] == "dev.user@example.com"
    assert payload["timezone"] == "Europe/Madrid"
    assert payload["id"]
    assert payload["created_at"]
    assert payload["updated_at"]

    stored = db_session.scalar(select(User).where(User.email == "dev.user@example.com"))
    assert stored is not None
    assert str(stored.id) == payload["id"]
    assert stored.timezone == "Europe/Madrid"


def test_dev_user_is_idempotent_and_preserves_existing_timezone(
    client: TestClient,
    db_session: Session,
) -> None:
    first = client.post(
        "/internal/api/dev/users",
        json={"email": "user@example.com", "timezone": "Europe/Warsaw"},
    )
    second = client.post(
        "/internal/api/dev/users",
        json={"email": "USER@EXAMPLE.COM", "timezone": "Europe/Madrid"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["email"] == "user@example.com"
    assert second.json()["timezone"] == "Europe/Warsaw"
    count = db_session.scalar(
        select(func.count())
        .select_from(User)
        .where(func.lower(User.email) == "user@example.com")
    )
    assert count == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "timezone": "Europe/Warsaw"},
        {"email": "user@example.com", "timezone": "Mars/Olympus_Mons"},
    ],
)
def test_dev_user_rejects_invalid_input(
    client: TestClient,
    payload: dict[str, str],
) -> None:
    response = client.post("/internal/api/dev/users", json=payload)

    assert response.status_code == 422


def test_dev_user_is_unavailable_when_internal_tools_are_disabled(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()

    response = client.post(
        "/internal/api/dev/users",
        json={"email": "user@example.com", "timezone": "Europe/Warsaw"},
    )

    assert response.status_code == 404


def test_dev_user_route_is_internal_and_calendar_contract_is_unchanged(
    client: TestClient,
) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/internal/api/dev/users" in paths
    assert "/internal/api/calendar/google/oauth/start" in paths
    assert "/api/v1/users" not in paths
    assert "/users" not in paths
