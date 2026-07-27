from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calendar_integration.errors import CalendarAuthorizationError
from app.calendar_integration.google import (
    GoogleOAuthClient,
    GoogleOAuthConfig,
    GoogleTokenSet,
)
from app.calendar_integration.security import FernetTokenCipher
from app.calendar_integration.service import (
    consume_oauth_state,
    create_oauth_state,
    store_google_connection,
)
from app.core.config import get_settings
from app.models import CalendarOAuthState, User


@pytest.fixture(autouse=True)
def calendar_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    monkeypatch.delenv("GOOGLE_CALENDAR_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CALENDAR_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("CALENDAR_TOKEN_ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_oauth_start_requires_configuration(client: TestClient, user: object) -> None:
    response = client.post(
        "/internal/api/calendar/google/oauth/start",
        json={"user_id": "11111111-1111-1111-1111-111111111111"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "calendar_not_configured"


def test_openapi_calendar_responses_contain_no_token_fields(
    client: TestClient,
) -> None:
    schema = client.get("/openapi.json").json()
    calendar_paths = {
        path: value
        for path, value in schema["paths"].items()
        if path.startswith("/internal/api/calendar")
    }
    rendered = str(calendar_paths).lower()

    assert calendar_paths
    assert "access_token" not in rendered
    assert "refresh_token" not in rendered
    assert "client_secret" not in rendered


def test_no_calendar_write_endpoint_exists(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    calendar_paths = " ".join(
        path for path in paths if path.startswith("/internal/api/calendar")
    )

    assert "/events" not in calendar_paths
    assert "event/create" not in calendar_paths


def test_oauth_state_is_hashed_expiring_and_single_use(
    db_session: Session, user: User
) -> None:
    oauth = GoogleOAuthClient(
        httpx.AsyncClient(),
        GoogleOAuthConfig(
            client_id="client",
            client_secret="secret",
            redirect_uri="http://localhost/callback",
            scopes=("https://www.googleapis.com/auth/calendar.readonly",),
        ),
    )
    now = datetime(2026, 7, 27, 8, tzinfo=UTC)
    raw_state, expires_at, _ = create_oauth_state(
        db_session, user_id=user.id, oauth_client=oauth, now=now
    )
    stored = db_session.scalar(select(CalendarOAuthState))

    assert stored is not None
    assert stored.state_hash != raw_state
    assert expires_at == now + timedelta(minutes=10)
    consume_oauth_state(db_session, raw_state=raw_state, now=now)
    with pytest.raises(CalendarAuthorizationError, match="consumed"):
        consume_oauth_state(db_session, raw_state=raw_state, now=now)


def test_connection_tokens_are_encrypted_and_repr_is_redacted(
    db_session: Session, user: User
) -> None:
    cipher = FernetTokenCipher(Fernet.generate_key().decode())
    connection = store_google_connection(
        db_session,
        user_id=user.id,
        tokens=GoogleTokenSet(
            access_token="access-secret",
            refresh_token="refresh-secret",
            expires_at=datetime(2026, 7, 27, 9, tzinfo=UTC),
            scopes=("https://www.googleapis.com/auth/calendar.readonly",),
        ),
        cipher=cipher,
    )

    assert connection.access_token_encrypted != "access-secret"
    assert connection.refresh_token_encrypted != "refresh-secret"
    assert cipher.decrypt(connection.access_token_encrypted or "") == "access-secret"
    assert cipher.decrypt(connection.refresh_token_encrypted or "") == "refresh-secret"
    assert "access-secret" not in repr(connection)
    assert "refresh-secret" not in repr(connection)
