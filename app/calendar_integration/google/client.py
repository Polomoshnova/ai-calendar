import asyncio
from datetime import datetime
from typing import Any

import httpx
from pydantic import ValidationError

from app.calendar_integration.errors import (
    CalendarAuthorizationError,
    CalendarProviderError,
    CalendarRateLimitError,
    CalendarUnavailableError,
)
from app.calendar_integration.models import (
    CalendarBusyInterval,
    CalendarBusyResult,
    CalendarProviderConnection,
    CalendarQueryError,
    ExternalCalendar,
)

CALENDAR_LIST_ENDPOINT = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
FREE_BUSY_ENDPOINT = "https://www.googleapis.com/calendar/v3/freeBusy"
FREE_BUSY_BATCH_SIZE = 50
MAX_ATTEMPTS = 3


class GoogleCalendarProvider:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def list_calendars(
        self, connection: CalendarProviderConnection
    ) -> list[ExternalCalendar]:
        calendars: list[ExternalCalendar] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "maxResults": 250,
                "showDeleted": "false",
                "showHidden": "false",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = await self._request(
                "GET",
                CALENDAR_LIST_ENDPOINT,
                connection=connection,
                params=params,
            )
            items = payload.get("items", [])
            if not isinstance(items, list):
                raise CalendarProviderError(
                    "Google Calendar returned a malformed calendar list"
                )
            for item in items:
                if not isinstance(item, dict) or not item.get("id"):
                    raise CalendarProviderError(
                        "Google Calendar returned a malformed calendar"
                    )
                calendars.append(
                    ExternalCalendar(
                        id=str(item["id"]),
                        name=str(item.get("summary") or item["id"]),
                        description=(
                            str(item["description"])
                            if item.get("description") is not None
                            else None
                        ),
                        timezone=(
                            str(item["timeZone"])
                            if item.get("timeZone") is not None
                            else None
                        ),
                        primary=bool(item.get("primary", False)),
                        selected=bool(item.get("selected", False)),
                        access_role=(
                            str(item["accessRole"])
                            if item.get("accessRole") is not None
                            else None
                        ),
                    )
                )
            next_token = payload.get("nextPageToken")
            if not next_token:
                return calendars
            page_token = str(next_token)

    async def query_busy_intervals(
        self,
        connection: CalendarProviderConnection,
        *,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
        timezone: str,
    ) -> CalendarBusyResult:
        intervals: list[CalendarBusyInterval] = []
        errors: list[CalendarQueryError] = []
        for start in range(0, len(calendar_ids), FREE_BUSY_BATCH_SIZE):
            batch = calendar_ids[start : start + FREE_BUSY_BATCH_SIZE]
            payload = await self._request(
                "POST",
                FREE_BUSY_ENDPOINT,
                connection=connection,
                json={
                    "timeMin": time_min.isoformat(),
                    "timeMax": time_max.isoformat(),
                    "timeZone": timezone,
                    "items": [{"id": calendar_id} for calendar_id in batch],
                },
            )
            calendars = payload.get("calendars")
            if not isinstance(calendars, dict):
                raise CalendarProviderError(
                    "Google Calendar returned malformed free/busy data"
                )
            for calendar_id in batch:
                if calendar_id not in calendars:
                    raise CalendarProviderError(
                        "Google Calendar omitted requested free/busy data"
                    )
                data = calendars[calendar_id]
                if not isinstance(data, dict):
                    raise CalendarProviderError(
                        "Google Calendar returned malformed calendar data"
                    )
                for error in data.get("errors", []):
                    if isinstance(error, dict):
                        errors.append(
                            CalendarQueryError(
                                calendar_id=calendar_id,
                                reason=str(error.get("reason", "unknown")),
                                domain=(
                                    str(error["domain"])
                                    if error.get("domain")
                                    else None
                                ),
                            )
                        )
                for busy in data.get("busy", []):
                    try:
                        intervals.append(
                            CalendarBusyInterval(
                                start=datetime.fromisoformat(str(busy["start"])),
                                end=datetime.fromisoformat(str(busy["end"])),
                                calendar_id=calendar_id,
                            )
                        )
                    except (KeyError, ValueError, TypeError, ValidationError) as exc:
                        raise CalendarProviderError(
                            "Google Calendar returned malformed busy intervals"
                        ) from exc
        return CalendarBusyResult(
            time_min=time_min,
            time_max=time_max,
            timezone=timezone,
            intervals=intervals,
            errors=errors,
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        connection: CalendarProviderConnection,
        **kwargs: Any,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": (f"Bearer {connection.access_token.get_secret_value()}")
        }
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self._client.request(
                    method, url, headers=headers, **kwargs
                )
            except httpx.RequestError as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise CalendarUnavailableError(
                        "Google Calendar is temporarily unavailable"
                    ) from exc
                await asyncio.sleep(0.1 * (2**attempt))
                continue
            if response.status_code == 401:
                raise CalendarAuthorizationError(
                    "Google Calendar access token was rejected"
                )
            if response.status_code == 429:
                if attempt == MAX_ATTEMPTS - 1:
                    raise CalendarRateLimitError("Google Calendar rate limit exceeded")
                await asyncio.sleep(0.1 * (2**attempt))
                continue
            if response.status_code >= 500:
                if attempt == MAX_ATTEMPTS - 1:
                    raise CalendarUnavailableError(
                        "Google Calendar is temporarily unavailable"
                    )
                await asyncio.sleep(0.1 * (2**attempt))
                continue
            if response.status_code >= 400:
                raise CalendarProviderError("Google Calendar request failed")
            try:
                payload = response.json()
            except ValueError as exc:
                raise CalendarProviderError(
                    "Google Calendar returned malformed JSON"
                ) from exc
            if not isinstance(payload, dict):
                raise CalendarProviderError("Google Calendar returned malformed JSON")
            return payload
        raise AssertionError("unreachable")
