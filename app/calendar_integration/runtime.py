from dataclasses import dataclass

import httpx

from app.calendar_integration.errors import CalendarConfigurationError
from app.calendar_integration.google import (
    GoogleCalendarProvider,
    GoogleOAuthClient,
    GoogleOAuthConfig,
)
from app.calendar_integration.security import FernetTokenCipher
from app.core.config import Settings

READ_ONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
EVENT_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"


@dataclass(frozen=True)
class CalendarRuntime:
    oauth: GoogleOAuthClient
    provider: GoogleCalendarProvider
    cipher: FernetTokenCipher


def build_calendar_runtime(
    settings: Settings, http_client: httpx.AsyncClient
) -> CalendarRuntime:
    if (
        not settings.google_calendar_client_id
        or settings.google_calendar_client_secret is None
        or settings.calendar_token_encryption_key is None
    ):
        raise CalendarConfigurationError(
            "Google Calendar integration is not configured"
        )
    scopes = tuple(settings.google_calendar_scopes.split())
    if set(scopes) != {READ_ONLY_SCOPE, EVENT_WRITE_SCOPE}:
        raise CalendarConfigurationError(
            "Google Calendar must use calendar.readonly and calendar.events scopes"
        )
    oauth = GoogleOAuthClient(
        http_client,
        GoogleOAuthConfig(
            client_id=settings.google_calendar_client_id,
            client_secret=settings.google_calendar_client_secret.get_secret_value(),
            redirect_uri=settings.google_calendar_redirect_uri,
            scopes=scopes,
        ),
    )
    return CalendarRuntime(
        oauth=oauth,
        provider=GoogleCalendarProvider(http_client),
        cipher=FernetTokenCipher(
            settings.calendar_token_encryption_key.get_secret_value()
        ),
    )
