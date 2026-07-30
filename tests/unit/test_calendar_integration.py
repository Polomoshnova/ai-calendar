import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.calendar_integration.errors import (
    CalendarEventNotFoundError,
    CalendarUnavailableError,
)
from app.calendar_integration.google import (
    GoogleCalendarProvider,
    GoogleOAuthClient,
    GoogleOAuthConfig,
)
from app.calendar_integration.mapper import normalize_calendar_busy_intervals
from app.calendar_integration.models import (
    CalendarBusyInterval,
    CalendarBusyResult,
    CalendarEventCreateRequest,
    CalendarEventSnapshot,
    CalendarProviderConnection,
    ExternalCalendar,
)
from app.calendar_integration.security import FernetTokenCipher

NOW = datetime(2026, 7, 27, 8, tzinfo=UTC)


def test_fernet_cipher_encrypts_and_decrypts_without_plaintext() -> None:
    cipher = FernetTokenCipher(Fernet.generate_key().decode())
    encrypted = cipher.encrypt("private-access-token")

    assert encrypted != "private-access-token"
    assert "private-access-token" not in encrypted
    assert cipher.decrypt(encrypted) == "private-access-token"


def test_oauth_authorization_url_is_read_only_and_safe() -> None:
    oauth = GoogleOAuthClient(
        httpx.AsyncClient(),
        GoogleOAuthConfig(
            client_id="public-client-id",
            client_secret="private-secret",
            redirect_uri="http://127.0.0.1:8000/internal/api/calendar/google/oauth/callback",
            scopes=("https://www.googleapis.com/auth/calendar.readonly",),
        ),
    )
    query = parse_qs(urlparse(oauth.authorization_url("random-state")).query)

    assert query["scope"] == ["https://www.googleapis.com/auth/calendar.readonly"]
    assert query["access_type"] == ["offline"]
    assert query["include_granted_scopes"] == ["true"]
    assert query["state"] == ["random-state"]
    assert query["redirect_uri"] == [
        "http://127.0.0.1:8000/internal/api/calendar/google/oauth/callback"
    ]
    assert "private-secret" not in str(query)

    account_query = parse_qs(
        urlparse(oauth.authorization_url("account-state", prompt_consent=True)).query
    )
    assert account_query["prompt"] == ["consent select_account"]


def test_busy_normalization_merges_overlap_touch_and_calendars() -> None:
    result = CalendarBusyResult(
        time_min=NOW,
        time_max=NOW + timedelta(hours=6),
        timezone="UTC",
        intervals=[
            CalendarBusyInterval(
                start=NOW + timedelta(hours=2),
                end=NOW + timedelta(hours=3),
                calendar_id="work",
            ),
            CalendarBusyInterval(
                start=NOW,
                end=NOW + timedelta(hours=2),
                calendar_id="primary",
            ),
            CalendarBusyInterval(
                start=NOW + timedelta(hours=4),
                end=NOW + timedelta(hours=4),
                calendar_id="zero",
            ),
        ],
        errors=[],
    )

    normalized = normalize_calendar_busy_intervals(result)

    assert len(normalized) == 1
    assert normalized[0].start == NOW
    assert normalized[0].end == NOW + timedelta(hours=3)
    assert len(result.intervals) == 3


def test_google_calendar_list_paginates_and_maps_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = request.url.params.get("pageToken")
        if page is None:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "primary",
                            "summary": "Main",
                            "primary": True,
                            "timeZone": "Europe/Warsaw",
                            "unknown": "ignored",
                        }
                    ],
                    "nextPageToken": "next",
                },
            )
        return httpx.Response(
            200,
            json={"items": [{"id": "shared", "summary": "Shared"}]},
        )

    async def run() -> list[ExternalCalendar]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = GoogleCalendarProvider(client)
            return await provider.list_calendars(
                CalendarProviderConnection(
                    connection_id="00000000-0000-0000-0000-000000000001",
                    access_token="token",
                )
            )

    calendars = asyncio.run(run())

    assert [item.id for item in calendars] == ["primary", "shared"]
    assert calendars[0].primary is True
    assert calendars[0].timezone == "Europe/Warsaw"
    assert requests[0].url.params["maxResults"] == "250"
    assert requests[1].url.params["pageToken"] == "next"


def test_google_account_identity_comes_from_verified_primary_calendar() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "verified-account@example.com",
                        "summary": "Verified account",
                        "primary": True,
                    }
                ]
            },
        )

    async def run() -> tuple[str, str | None]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            identity = await GoogleCalendarProvider(client).get_account_identity(
                CalendarProviderConnection(
                    connection_id="00000000-0000-0000-0000-000000000001",
                    access_token="token",
                )
            )
            return identity.provider_account_id, identity.provider_account_email

    assert asyncio.run(run()) == (
        "verified-account@example.com",
        "verified-account@example.com",
    )


def test_free_busy_batches_more_than_fifty_calendars() -> None:
    batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        ids = [item["id"] for item in body["items"]]
        batches.append(ids)
        return httpx.Response(
            200,
            json={"calendars": {item: {"busy": []} for item in ids}},
        )

    ids = [f"calendar-{index}" for index in range(51)]

    async def run() -> CalendarBusyResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await GoogleCalendarProvider(client).query_busy_intervals(
                CalendarProviderConnection(
                    connection_id="00000000-0000-0000-0000-000000000001",
                    access_token="token",
                ),
                calendar_ids=ids,
                time_min=NOW,
                time_max=NOW + timedelta(days=1),
                timezone="UTC",
            )

    result = asyncio.run(run())

    assert [len(batch) for batch in batches] == [50, 1]
    assert result.intervals == []


def test_google_create_event_uses_exact_target_and_returns_typed_result() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": body["id"],
                "etag": '"revision-1"',
                "updated": "2026-07-30T08:01:00+00:00",
                "start": body["start"],
                "end": body["end"],
            },
        )

    async def run() -> tuple[str, str]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await GoogleCalendarProvider(client).create_event(
                CalendarProviderConnection(
                    connection_id="00000000-0000-0000-0000-000000000001",
                    access_token="token",
                ),
                CalendarEventCreateRequest(
                    connection_id="00000000-0000-0000-0000-000000000001",
                    provider_account_id="account-1",
                    calendar_id="team/calendar@example.com",
                    event_id="1234567890abcdef",
                    title="Prepare report",
                    description="Draft it.",
                    start=NOW,
                    end=NOW + timedelta(hours=1),
                    timezone="Europe/Warsaw",
                    task_id=None,
                    schedule_plan_id="00000000-0000-0000-0000-000000000002",
                    scheduled_session_id="00000000-0000-0000-0000-000000000003",
                ),
            )
            return result.external_event_id, result.calendar_id

    assert asyncio.run(run()) == ("1234567890abcdef", "team/calendar@example.com")
    assert "team%2Fcalendar%40example.com" in str(requests[0].url)
    body = __import__("json").loads(requests[0].content)
    assert body["start"] == {
        "dateTime": NOW.isoformat(),
        "timeZone": "Europe/Warsaw",
    }
    assert body["end"]["dateTime"] == (NOW + timedelta(hours=1)).isoformat()


def test_google_get_event_uses_exact_identity_and_normalizes_snapshot() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "event/one",
                "status": "confirmed",
                "etag": '"revision-2"',
                "updated": "2026-07-30T08:05:00+00:00",
                "start": {
                    "dateTime": NOW.isoformat(),
                    "timeZone": "Europe/Warsaw",
                },
                "end": {
                    "dateTime": (NOW + timedelta(hours=1)).isoformat(),
                    "timeZone": "Europe/Warsaw",
                },
            },
        )

    async def run() -> CalendarEventSnapshot:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await GoogleCalendarProvider(client).get_event(
                CalendarProviderConnection(
                    connection_id="00000000-0000-0000-0000-000000000001",
                    access_token="token",
                ),
                calendar_id="team/calendar@example.com",
                external_event_id="event/one",
            )

    result = asyncio.run(run())

    assert result.start == NOW
    assert result.end == NOW + timedelta(hours=1)
    assert result.timezone == "Europe/Warsaw"
    assert result.etag == '"revision-2"'
    assert "team%2Fcalendar%40example.com/events/event%2Fone" in str(requests[0].url)


def test_google_get_event_normalizes_cancelled_and_not_found() -> None:
    responses = iter(
        [
            httpx.Response(200, json={"id": "event-1", "status": "cancelled"}),
            httpx.Response(404, json={"error": {"message": "Event not found"}}),
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    async def run() -> tuple[bool, str]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = GoogleCalendarProvider(client)
            cancelled = await provider.get_event(
                CalendarProviderConnection(
                    connection_id="00000000-0000-0000-0000-000000000001",
                    access_token="token",
                ),
                calendar_id="primary",
                external_event_id="event-1",
            )
            with pytest.raises(CalendarEventNotFoundError) as caught:
                await provider.get_event(
                    CalendarProviderConnection(
                        connection_id="00000000-0000-0000-0000-000000000001",
                        access_token="token",
                    ),
                    calendar_id="primary",
                    external_event_id="event-2",
                )
            return cancelled.cancelled, caught.value.code

    assert asyncio.run(run()) == (True, "calendar_event_not_found")


def test_google_get_event_distinguishes_temporary_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(CalendarUnavailableError):
                await GoogleCalendarProvider(client).get_event(
                    CalendarProviderConnection(
                        connection_id="00000000-0000-0000-0000-000000000001",
                        access_token="token",
                    ),
                    calendar_id="primary",
                    external_event_id="event-1",
                )

    asyncio.run(run())


def test_busy_interval_requires_timezone_and_valid_order() -> None:
    with pytest.raises(ValidationError):
        CalendarBusyInterval(
            start=NOW,
            end=NOW - timedelta(minutes=1),
            calendar_id="primary",
        )
