from datetime import datetime
from typing import Protocol

from app.calendar_integration.models import (
    CalendarBusyResult,
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


class TokenCipher(Protocol):
    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, ciphertext: str) -> str: ...
