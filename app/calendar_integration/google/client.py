import asyncio
from datetime import date, datetime, time
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from app.calendar_integration.errors import (
    CalendarAuthorizationError,
    CalendarEventNotFoundError,
    CalendarNotFoundError,
    CalendarProviderError,
    CalendarRateLimitError,
    CalendarUnavailableError,
)
from app.calendar_integration.models import (
    CalendarAccountIdentity,
    CalendarBusyInterval,
    CalendarBusyResult,
    CalendarEventCreateRequest,
    CalendarEventCreateResult,
    CalendarEventSnapshot,
    CalendarProviderConnection,
    CalendarQueryError,
    ExternalCalendar,
    ExternalCalendarEvent,
)

CALENDAR_LIST_ENDPOINT = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
FREE_BUSY_ENDPOINT = "https://www.googleapis.com/calendar/v3/freeBusy"
EVENTS_ENDPOINT = (
    "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
)
FREE_BUSY_BATCH_SIZE = 50
MAX_ATTEMPTS = 3


def _event_interval(
    payload: dict[str, Any], zone: ZoneInfo
) -> tuple[datetime | None, datetime | None, bool]:
    if payload.get("status") == "cancelled" and (
        "start" not in payload or "end" not in payload
    ):
        return None, None, False
    start_data = payload["start"]
    end_data = payload["end"]
    if not isinstance(start_data, dict) or not isinstance(end_data, dict):
        raise TypeError("event boundaries must be objects")
    if "dateTime" in start_data and "dateTime" in end_data:
        return (
            datetime.fromisoformat(str(start_data["dateTime"])),
            datetime.fromisoformat(str(end_data["dateTime"])),
            False,
        )
    if "date" in start_data and "date" in end_data:
        return (
            datetime.combine(
                date.fromisoformat(str(start_data["date"])), time.min, zone
            ),
            datetime.combine(date.fromisoformat(str(end_data["date"])), time.min, zone),
            True,
        )
    raise KeyError("event boundaries")


def _original_start_time(
    payload: dict[str, Any], zone: ZoneInfo
) -> datetime | date | None:
    value = payload.get("originalStartTime")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("originalStartTime must be an object")
    if value.get("dateTime") is not None:
        return datetime.fromisoformat(str(value["dateTime"]))
    if value.get("date") is not None:
        return date.fromisoformat(str(value["date"]))
    raise KeyError("originalStartTime")


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

    async def get_account_identity(
        self, connection: CalendarProviderConnection
    ) -> CalendarAccountIdentity:
        primary = [
            calendar
            for calendar in await self.list_calendars(connection)
            if calendar.primary
        ]
        if len(primary) != 1:
            raise CalendarProviderError(
                "Google Calendar did not return one verified primary calendar"
            )
        account_id = primary[0].id
        return CalendarAccountIdentity(
            provider_account_id=account_id,
            provider_account_email=account_id if "@" in account_id else None,
        )

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

    async def create_event(
        self,
        connection: CalendarProviderConnection,
        request: CalendarEventCreateRequest,
    ) -> CalendarEventCreateResult:
        fallback = {
            "id": request.event_id,
            "start": {"dateTime": request.start.isoformat()},
            "end": {"dateTime": request.end.isoformat()},
        }
        payload = await self._request(
            "POST",
            EVENTS_ENDPOINT.format(calendar_id=quote(request.calendar_id, safe="")),
            connection=connection,
            params={"sendUpdates": "none"},
            json={
                "id": request.event_id,
                "summary": request.title,
                "description": request.description,
                "start": {
                    "dateTime": request.start.isoformat(),
                    "timeZone": request.timezone,
                },
                "end": {
                    "dateTime": request.end.isoformat(),
                    "timeZone": request.timezone,
                },
                "extendedProperties": {
                    "private": {
                        "task_id": str(request.task_id) if request.task_id else "",
                        "schedule_plan_id": str(request.schedule_plan_id),
                        "scheduled_session_id": str(request.scheduled_session_id),
                    }
                },
            },
            conflict_payload=fallback,
        )
        try:
            event_id = str(payload["id"])
            start = datetime.fromisoformat(str(payload["start"]["dateTime"]))
            end = datetime.fromisoformat(str(payload["end"]["dateTime"]))
            updated = (
                datetime.fromisoformat(str(payload["updated"]))
                if payload.get("updated")
                else None
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CalendarProviderError(
                "Google Calendar returned a malformed created event"
            ) from exc
        return CalendarEventCreateResult(
            external_event_id=event_id,
            calendar_id=request.calendar_id,
            connection_id=request.connection_id,
            provider_account_id=request.provider_account_id,
            start=start,
            end=end,
            etag=str(payload["etag"]) if payload.get("etag") else None,
            provider_updated_at=updated,
        )

    async def get_event(
        self,
        connection: CalendarProviderConnection,
        *,
        calendar_id: str,
        external_event_id: str,
    ) -> CalendarEventSnapshot:
        payload = await self._request(
            "GET",
            (
                EVENTS_ENDPOINT.format(calendar_id=quote(calendar_id, safe=""))
                + f"/{quote(external_event_id, safe='')}"
            ),
            connection=connection,
            not_found_context="event",
        )
        status = str(payload.get("status")) if payload.get("status") else None
        cancelled = status == "cancelled"
        start: datetime | None = None
        end: datetime | None = None
        timezone: str | None = None
        try:
            event_id = str(payload["id"])
            if not cancelled:
                start_data = payload["start"]
                end_data = payload["end"]
                if "dateTime" not in start_data or "dateTime" not in end_data:
                    raise KeyError("dateTime")
                start = datetime.fromisoformat(str(start_data["dateTime"]))
                end = datetime.fromisoformat(str(end_data["dateTime"]))
                timezone = (
                    str(start_data["timeZone"])
                    if start_data.get("timeZone") is not None
                    else None
                )
            updated = (
                datetime.fromisoformat(str(payload["updated"]))
                if payload.get("updated")
                else None
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CalendarProviderError(
                "Google Calendar returned a malformed event"
            ) from exc
        return CalendarEventSnapshot(
            external_event_id=event_id,
            calendar_id=calendar_id,
            exists=True,
            cancelled=cancelled,
            start=start,
            end=end,
            timezone=timezone,
            etag=str(payload["etag"]) if payload.get("etag") else None,
            provider_updated_at=updated,
            provider_status=status,
        )

    async def list_events(
        self,
        connection: CalendarProviderConnection,
        *,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
        timezone: str,
    ) -> list[ExternalCalendarEvent]:
        events: list[ExternalCalendarEvent] = []
        page_token: str | None = None
        zone = ZoneInfo(timezone)
        while True:
            params: dict[str, Any] = {
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "timeZone": timezone,
                "singleEvents": "true",
                "showDeleted": "true",
                "maxResults": 2500,
            }
            if page_token is not None:
                params["pageToken"] = page_token
            payload = await self._request(
                "GET",
                EVENTS_ENDPOINT.format(calendar_id=quote(calendar_id, safe="")),
                connection=connection,
                params=params,
            )
            items = payload.get("items", [])
            if not isinstance(items, list):
                raise CalendarProviderError(
                    "Google Calendar returned a malformed event list"
                )
            for item in items:
                if not isinstance(item, dict) or not item.get("id"):
                    raise CalendarProviderError(
                        "Google Calendar returned a malformed event"
                    )
                try:
                    status = str(item.get("status")) if item.get("status") else None
                    start, end, all_day = _event_interval(item, zone)
                    updated = (
                        datetime.fromisoformat(str(item["updated"]))
                        if item.get("updated")
                        else None
                    )
                    events.append(
                        ExternalCalendarEvent(
                            external_event_id=str(item["id"]),
                            calendar_id=calendar_id,
                            title=str(item.get("summary") or "Untitled event"),
                            start=start,
                            end=end,
                            all_day=all_day,
                            status=status,
                            etag=str(item["etag"]) if item.get("etag") else None,
                            provider_updated_at=updated,
                            recurring_event_id=(
                                str(item["recurringEventId"])
                                if item.get("recurringEventId")
                                else None
                            ),
                            original_start_time=_original_start_time(item, zone),
                        )
                    )
                except (KeyError, TypeError, ValueError, ValidationError) as exc:
                    raise CalendarProviderError(
                        "Google Calendar returned a malformed event"
                    ) from exc
            next_page = payload.get("nextPageToken")
            if not next_page:
                return events
            page_token = str(next_page)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        connection: CalendarProviderConnection,
        conflict_payload: dict[str, Any] | None = None,
        not_found_context: str | None = None,
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
            if response.status_code == 409 and conflict_payload is not None:
                return conflict_payload
            if response.status_code == 404 and not_found_context == "event":
                try:
                    error_payload = response.json()
                    message = str(error_payload.get("error", {}).get("message", ""))
                except (ValueError, AttributeError):
                    message = ""
                lowered_message = message.lower()
                if "calendar" in lowered_message and "event" not in lowered_message:
                    raise CalendarNotFoundError("Google Calendar was not found")
                raise CalendarEventNotFoundError("Google Calendar event was not found")
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
