from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func, select
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
from app.models import (
    CalendarConnection,
    CalendarOAuthState,
    CalendarProviderName,
    CalendarSelection,
    User,
)


@pytest.fixture(autouse=True)
def calendar_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_SECRET", "")
    monkeypatch.setenv("CALENDAR_TOKEN_ENCRYPTION_KEY", "")
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
        provider_account_id="account-1",
        provider_account_email="account-1@example.com",
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


def token_set(access_token: str, refresh_token: str | None = None) -> GoogleTokenSet:
    return GoogleTokenSet(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=datetime(2026, 7, 27, 9, tzinfo=UTC),
        scopes=("https://www.googleapis.com/auth/calendar.readonly",),
    )


def test_one_user_can_connect_two_google_accounts_and_reconnect_one(
    db_session: Session, user: User
) -> None:
    cipher = FernetTokenCipher(Fernet.generate_key().decode())
    first = store_google_connection(
        db_session,
        user_id=user.id,
        provider_account_id="google-account-1",
        provider_account_email="one@example.com",
        tokens=token_set("first-access", "first-refresh"),
        cipher=cipher,
    )
    second = store_google_connection(
        db_session,
        user_id=user.id,
        provider_account_id="google-account-2",
        provider_account_email="two@example.com",
        tokens=token_set("second-access", "second-refresh"),
        cipher=cipher,
    )
    reconnected = store_google_connection(
        db_session,
        user_id=user.id,
        provider_account_id="google-account-1",
        provider_account_email="updated-one@example.com",
        tokens=token_set("updated-access"),
        cipher=cipher,
    )

    assert first.id != second.id
    assert reconnected.id == first.id
    assert reconnected.provider_account_email == "updated-one@example.com"
    assert cipher.decrypt(reconnected.access_token_encrypted or "") == "updated-access"
    assert cipher.decrypt(reconnected.refresh_token_encrypted or "") == "first-refresh"
    assert db_session.scalar(select(func.count()).select_from(CalendarConnection)) == 2


def test_two_users_can_connect_the_same_google_account(
    db_session: Session, user: User
) -> None:
    cipher = FernetTokenCipher(Fernet.generate_key().decode())
    other = User(email="other-google-owner@example.com", timezone="UTC")
    db_session.add(other)
    db_session.commit()

    first = store_google_connection(
        db_session,
        user_id=user.id,
        provider_account_id="shared-google-account",
        provider_account_email="shared@example.com",
        tokens=token_set("first"),
        cipher=cipher,
    )
    second = store_google_connection(
        db_session,
        user_id=other.id,
        provider_account_id="shared-google-account",
        provider_account_email="shared@example.com",
        tokens=token_set("second"),
        cipher=cipher,
    )

    assert first.id != second.id


def test_reconnect_claims_only_a_verified_matching_legacy_connection(
    db_session: Session, user: User
) -> None:
    cipher = FernetTokenCipher(Fernet.generate_key().decode())
    legacy = CalendarConnection(
        user_id=user.id,
        provider=CalendarProviderName.google,
        provider_account_id=None,
        access_token_encrypted=cipher.encrypt("legacy-access"),
        scopes=[],
    )
    legacy.selections.append(
        CalendarSelection(
            external_calendar_id="verified-legacy@example.com",
            display_name="Primary",
            primary=True,
            include_in_availability=True,
        )
    )
    db_session.add(legacy)
    db_session.commit()

    reconnected = store_google_connection(
        db_session,
        user_id=user.id,
        provider_account_id="verified-legacy@example.com",
        provider_account_email="verified-legacy@example.com",
        tokens=token_set("updated-legacy-access"),
        cipher=cipher,
    )

    assert reconnected.id == legacy.id
    assert reconnected.provider_account_id == "verified-legacy@example.com"
