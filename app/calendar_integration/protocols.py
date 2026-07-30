from datetime import datetime
from typing import Protocol

from app.calendar_integration.google.oauth import GoogleTokenSet
from app.calendar_integration.models import (
    CalendarBusyResult,
    CalendarEventCreateRequest,
    CalendarEventCreateResult,
    CalendarProviderConnection,
    ExternalCalendar,
)


class CalendarProvider(Protocol):
    async def list_calendars(
        self,
        connection: CalendarProviderConnection,
    ) -> list[ExternalCalendar]: ...

    async def query_busy_intervals(
        self,
        connection: CalendarProviderConnection,
        *,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
        timezone: str,
    ) -> CalendarBusyResult: ...

    async def create_event(
        self,
        connection: CalendarProviderConnection,
        request: CalendarEventCreateRequest,
    ) -> CalendarEventCreateResult: ...


class TokenCipher(Protocol):
    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, ciphertext: str) -> str: ...


class CalendarOAuthClient(Protocol):
    def authorization_url(self, state: str, *, prompt_consent: bool = False) -> str: ...

    async def refresh_access_token(self, refresh_token: str) -> GoogleTokenSet: ...

    async def revoke_token(self, token: str) -> None: ...
