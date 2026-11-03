import asyncio
from datetime import UTC, datetime

import httpx

from app.calendar_day import local_day_bounds
from app.calendar_integration.google import GoogleCalendarProvider
from app.calendar_integration.models import (
    CalendarProviderConnection,
    ExternalCalendarEvent,
)


def test_warsaw_day_bounds_cover_normal_and_dst_days() -> None:
    normal_start, normal_end = local_day_bounds(
        datetime(2026, 11, 3).date(), "Europe/Warsaw"
    )
    spring_start, spring_end = local_day_bounds(
        datetime(2026, 3, 29).date(), "Europe/Warsaw"
    )
    autumn_start, autumn_end = local_day_bounds(
        datetime(2026, 10, 25).date(), "Europe/Warsaw"
    )

    assert normal_start == datetime(2026, 11, 2, 23, tzinfo=UTC)
    assert normal_end == datetime(2026, 11, 3, 23, tzinfo=UTC)
    assert (spring_end - spring_start).total_seconds() == 23 * 3600
    assert (autumn_end - autumn_start).total_seconds() == 25 * 3600


def test_google_event_list_paginates_and_maps_timed_all_day_and_cancelled() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("pageToken") is None:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "timed",
                            "summary": "Repeated hour",
                            "status": "confirmed",
                            "start": {"dateTime": "2026-10-25T02:30:00+02:00"},
                            "end": {"dateTime": "2026-10-25T02:45:00+02:00"},
                            "etag": '"v1"',
                            "updated": "2026-10-24T10:00:00Z",
                        },
                        {
                            "id": "cancelled",
                            "status": "cancelled",
                        },
                    ],
                    "nextPageToken": "page-2",
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "all-day",
                        "status": "confirmed",
                        "start": {"date": "2026-10-25"},
                        "end": {"date": "2026-10-26"},
                    }
                ]
            },
        )

    async def run() -> list[ExternalCalendarEvent]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await GoogleCalendarProvider(client).list_events(
                CalendarProviderConnection(
                    connection_id="00000000-0000-0000-0000-000000000001",
                    access_token="secret",
                ),
                calendar_id="primary",
                time_min=datetime(2026, 10, 24, 22, tzinfo=UTC),
                time_max=datetime(2026, 10, 25, 23, tzinfo=UTC),
                timezone="Europe/Warsaw",
            )

    events = asyncio.run(run())

    assert [event.external_event_id for event in events] == [
        "timed",
        "cancelled",
        "all-day",
    ]
    assert events[1].start is None
    assert events[2].title == "Untitled event"
    assert events[2].all_day is True
    assert events[2].start is not None
    assert events[2].start.isoformat() == "2026-10-25T00:00:00+02:00"
    assert requests[0].url.params["singleEvents"] == "true"
    assert requests[0].url.params["showDeleted"] == "true"
    assert requests[1].url.params["pageToken"] == "page-2"
