import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calendar_integration.errors import (
    CalendarAuthorizationError,
    CalendarConnectionNotFoundError,
    CalendarProviderError,
    CalendarReconnectRequiredError,
    CalendarSelectionError,
)
from app.calendar_integration.google import (
    GoogleCalendarProvider,
    GoogleOAuthClient,
    GoogleTokenSet,
)
from app.calendar_integration.models import (
    CalendarBusyResult,
    CalendarProviderConnection,
    ExternalCalendar,
)
from app.calendar_integration.protocols import TokenCipher
from app.models import (
    CalendarConnection,
    CalendarConnectionStatus,
    CalendarOAuthState,
    CalendarProviderName,
    CalendarSelection,
    User,
)

OAUTH_STATE_TTL = timedelta(minutes=10)
TOKEN_REFRESH_MARGIN = timedelta(seconds=60)


def create_oauth_state(
    session: Session,
    *,
    user_id: uuid.UUID,
    oauth_client: GoogleOAuthClient,
    now: datetime | None = None,
) -> tuple[str, datetime, str]:
    if session.get(User, user_id) is None:
        raise CalendarConnectionNotFoundError("User not found")
    current = now or datetime.now(UTC)
    raw_state = secrets.token_urlsafe(32)
    expires_at = current + OAUTH_STATE_TTL
    session.add(
        CalendarOAuthState(
            state_hash=_hash_state(raw_state),
            user_id=user_id,
            provider=CalendarProviderName.google,
            expires_at=expires_at,
        )
    )
    session.commit()
    return (
        raw_state,
        expires_at,
        oauth_client.authorization_url(raw_state, prompt_consent=True),
    )


def consume_oauth_state(
    session: Session,
    *,
    raw_state: str,
    now: datetime | None = None,
) -> CalendarOAuthState:
    current = now or datetime.now(UTC)
    state = session.scalar(
        select(CalendarOAuthState)
        .where(
            CalendarOAuthState.state_hash == _hash_state(raw_state),
            CalendarOAuthState.provider == CalendarProviderName.google,
        )
        .with_for_update()
    )
    if state is None:
        raise CalendarAuthorizationError("Unknown OAuth state")
    if state.consumed_at is not None:
        raise CalendarAuthorizationError("OAuth state has already been consumed")
    if state.expires_at <= current:
        raise CalendarAuthorizationError("OAuth state has expired")
    state.consumed_at = current
    session.commit()
    return state


def store_google_connection(
    session: Session,
    *,
    user_id: uuid.UUID,
    provider_account_id: str,
    provider_account_email: str | None,
    tokens: GoogleTokenSet,
    cipher: TokenCipher,
) -> CalendarConnection:
    if not provider_account_id.strip():
        raise CalendarAuthorizationError(
            "Google account identity is required before storing a connection"
        )
    connection = session.scalar(
        select(CalendarConnection)
        .where(
            CalendarConnection.user_id == user_id,
            CalendarConnection.provider == CalendarProviderName.google,
            CalendarConnection.provider_account_id == provider_account_id,
        )
        .with_for_update()
    )
    if connection is None:
        connection = session.scalar(
            select(CalendarConnection)
            .join(CalendarSelection)
            .where(
                CalendarConnection.user_id == user_id,
                CalendarConnection.provider == CalendarProviderName.google,
                CalendarConnection.provider_account_id.is_(None),
                CalendarSelection.primary.is_(True),
                CalendarSelection.external_calendar_id == provider_account_id,
            )
            .with_for_update()
        )
    if connection is None:
        connection = CalendarConnection(
            user_id=user_id,
            provider=CalendarProviderName.google,
            provider_account_id=provider_account_id,
            provider_account_email=provider_account_email,
            scopes=list(tokens.scopes),
        )
        session.add(connection)
    else:
        connection.provider_account_id = provider_account_id
        connection.provider_account_email = provider_account_email
    existing_refresh = connection.refresh_token_encrypted
    connection.access_token_encrypted = cipher.encrypt(tokens.access_token)
    connection.refresh_token_encrypted = (
        cipher.encrypt(tokens.refresh_token)
        if tokens.refresh_token is not None
        else existing_refresh
    )
    connection.token_expires_at = tokens.expires_at
    connection.scopes = list(tokens.scopes)
    connection.status = CalendarConnectionStatus.active
    connection.last_error_code = None
    session.commit()
    session.refresh(connection)
    return connection


def list_owned_connections(
    session: Session, user_id: uuid.UUID
) -> list[CalendarConnection]:
    if session.get(User, user_id) is None:
        raise CalendarConnectionNotFoundError("User not found")
    return list(
        session.scalars(
            select(CalendarConnection)
            .where(CalendarConnection.user_id == user_id)
            .order_by(CalendarConnection.created_at, CalendarConnection.id)
        )
    )


def owned_connection(
    session: Session, connection_id: uuid.UUID, user_id: uuid.UUID
) -> CalendarConnection:
    connection = session.scalar(
        select(CalendarConnection).where(
            CalendarConnection.id == connection_id,
            CalendarConnection.user_id == user_id,
        )
    )
    if connection is None:
        raise CalendarConnectionNotFoundError("Calendar connection not found")
    return connection


async def connection_credentials(
    session: Session,
    connection: CalendarConnection,
    *,
    cipher: TokenCipher,
    oauth_client: GoogleOAuthClient,
    force_refresh: bool = False,
    now: datetime | None = None,
) -> CalendarProviderConnection:
    locked_connection = session.scalar(
        select(CalendarConnection)
        .where(CalendarConnection.id == connection.id)
        .with_for_update()
    )
    if locked_connection is None:
        raise CalendarConnectionNotFoundError("Calendar connection not found")
    connection = locked_connection
    current = now or datetime.now(UTC)
    if connection.status is not CalendarConnectionStatus.active:
        raise CalendarReconnectRequiredError(
            "Google Calendar access must be reauthorized."
        )
    if (
        not force_refresh
        and connection.access_token_encrypted
        and (
            connection.token_expires_at is None
            or connection.token_expires_at > current + TOKEN_REFRESH_MARGIN
        )
    ):
        return CalendarProviderConnection(
            connection_id=connection.id,
            access_token=cipher.decrypt(connection.access_token_encrypted),
        )
    if connection.refresh_token_encrypted is None:
        _expire_connection(session, connection, "missing_refresh_token")
        raise CalendarReconnectRequiredError(
            "Google Calendar access must be reauthorized."
        )
    refresh_token = cipher.decrypt(connection.refresh_token_encrypted)
    try:
        tokens = await oauth_client.refresh_access_token(refresh_token)
    except CalendarReconnectRequiredError:
        _expire_connection(session, connection, "invalid_grant")
        raise
    connection.access_token_encrypted = cipher.encrypt(tokens.access_token)
    if tokens.refresh_token is not None:
        connection.refresh_token_encrypted = cipher.encrypt(tokens.refresh_token)
    connection.token_expires_at = tokens.expires_at
    connection.scopes = list(tokens.scopes)
    connection.status = CalendarConnectionStatus.active
    connection.last_error_code = None
    session.commit()
    return CalendarProviderConnection(
        connection_id=connection.id,
        access_token=tokens.access_token,
    )


async def list_connection_calendars(
    session: Session,
    connection: CalendarConnection,
    *,
    provider: GoogleCalendarProvider,
    oauth_client: GoogleOAuthClient,
    cipher: TokenCipher,
) -> list[ExternalCalendar]:
    credentials = await connection_credentials(
        session, connection, cipher=cipher, oauth_client=oauth_client
    )
    try:
        calendars = await provider.list_calendars(credentials)
    except CalendarAuthorizationError:
        credentials = await connection_credentials(
            session,
            connection,
            cipher=cipher,
            oauth_client=oauth_client,
            force_refresh=True,
        )
        try:
            calendars = await provider.list_calendars(credentials)
        except CalendarAuthorizationError as exc:
            _expire_connection(session, connection, "second_unauthorized")
            raise CalendarReconnectRequiredError(
                "Google Calendar access must be reauthorized."
            ) from exc
    _sync_calendar_selections(session, connection, calendars)
    connection.last_successful_sync_at = datetime.now(UTC)
    connection.last_error_code = None
    session.commit()
    selected_ids = {
        selection.external_calendar_id
        for selection in connection.selections
        if selection.include_in_availability
    }
    return [
        calendar.model_copy(update={"selected": calendar.id in selected_ids})
        for calendar in calendars
    ]


async def replace_calendar_selections(
    session: Session,
    connection: CalendarConnection,
    *,
    calendar_ids: list[str],
    provider: GoogleCalendarProvider,
    oauth_client: GoogleOAuthClient,
    cipher: TokenCipher,
) -> list[CalendarSelection]:
    unique_ids = list(dict.fromkeys(calendar_ids))
    calendars = await list_connection_calendars(
        session,
        connection,
        provider=provider,
        oauth_client=oauth_client,
        cipher=cipher,
    )
    available_ids = {calendar.id for calendar in calendars}
    unknown = [
        calendar_id for calendar_id in unique_ids if calendar_id not in available_ids
    ]
    if unknown:
        raise CalendarSelectionError(
            f"Unknown calendars for connection: {', '.join(unknown)}"
        )
    selected = set(unique_ids)
    for selection in connection.selections:
        selection.include_in_availability = selection.external_calendar_id in selected
    session.commit()
    return sorted(
        connection.selections,
        key=lambda item: (
            not item.primary,
            item.display_name,
            item.external_calendar_id,
        ),
    )


async def query_connection_busy(
    session: Session,
    connection: CalendarConnection,
    *,
    calendar_ids: list[str] | None,
    time_min: datetime,
    time_max: datetime,
    timezone: str,
    provider: GoogleCalendarProvider,
    oauth_client: GoogleOAuthClient,
    cipher: TokenCipher,
) -> tuple[list[str], CalendarBusyResult]:
    resolved_ids = (
        list(dict.fromkeys(calendar_ids))
        if calendar_ids is not None
        else [
            selection.external_calendar_id
            for selection in connection.selections
            if selection.include_in_availability
        ]
    )
    if not resolved_ids:
        raise CalendarSelectionError("At least one calendar must be selected")
    credentials = await connection_credentials(
        session, connection, cipher=cipher, oauth_client=oauth_client
    )
    try:
        result = await provider.query_busy_intervals(
            credentials,
            calendar_ids=resolved_ids,
            time_min=time_min,
            time_max=time_max,
            timezone=timezone,
        )
    except CalendarAuthorizationError:
        credentials = await connection_credentials(
            session,
            connection,
            cipher=cipher,
            oauth_client=oauth_client,
            force_refresh=True,
        )
        try:
            result = await provider.query_busy_intervals(
                credentials,
                calendar_ids=resolved_ids,
                time_min=time_min,
                time_max=time_max,
                timezone=timezone,
            )
        except CalendarAuthorizationError as exc:
            _expire_connection(session, connection, "second_unauthorized")
            raise CalendarReconnectRequiredError(
                "Google Calendar access must be reauthorized."
            ) from exc
    failed_ids = {error.calendar_id for error in result.errors}
    if failed_ids == set(resolved_ids):
        raise CalendarProviderError("Free/busy failed for every selected calendar")
    connection.last_successful_sync_at = datetime.now(UTC)
    connection.last_error_code = None
    session.commit()
    return resolved_ids, result


async def disconnect_connection(
    session: Session,
    connection: CalendarConnection,
    *,
    oauth_client: GoogleOAuthClient,
    cipher: TokenCipher,
) -> None:
    token_encrypted = (
        connection.refresh_token_encrypted or connection.access_token_encrypted
    )
    if token_encrypted:
        token = cipher.decrypt(token_encrypted)
        try:
            await oauth_client.revoke_token(token)
        except CalendarProviderError:
            connection.last_error_code = "remote_revocation_failed"
    connection.status = CalendarConnectionStatus.revoked
    connection.access_token_encrypted = None
    connection.refresh_token_encrypted = None
    connection.token_expires_at = None
    connection.selections.clear()
    session.commit()


def _sync_calendar_selections(
    session: Session,
    connection: CalendarConnection,
    calendars: list[ExternalCalendar],
) -> None:
    existing = {
        selection.external_calendar_id: selection for selection in connection.selections
    }
    first_sync = not existing
    for calendar in calendars:
        selection = existing.get(calendar.id)
        if selection is None:
            selection = CalendarSelection(
                connection=connection,
                external_calendar_id=calendar.id,
                display_name=calendar.name,
                timezone=calendar.timezone,
                primary=calendar.primary,
                include_in_availability=first_sync and calendar.primary,
            )
            session.add(selection)
        else:
            selection.display_name = calendar.name
            selection.timezone = calendar.timezone
            selection.primary = calendar.primary


def _expire_connection(
    session: Session, connection: CalendarConnection, error_code: str
) -> None:
    connection.status = CalendarConnectionStatus.expired
    connection.access_token_encrypted = None
    connection.token_expires_at = None
    connection.last_error_code = error_code
    session.commit()


def _hash_state(raw_state: str) -> str:
    return hashlib.sha256(raw_state.encode()).hexdigest()
