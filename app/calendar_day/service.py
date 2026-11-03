import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.calendar_day.schemas import (
    CalendarDayError,
    CalendarDayItem,
    CalendarDayKind,
    CalendarDayResponse,
    CalendarDayScheduledSession,
    CalendarDaySource,
    CalendarDaySyncState,
    CalendarDaySyncStatus,
)
from app.calendar_integration.errors import (
    CalendarAuthorizationError,
    CalendarIntegrationError,
    CalendarReconnectRequiredError,
)
from app.calendar_integration.models import ExternalCalendarEvent
from app.calendar_integration.protocols import (
    CalendarOAuthClient,
    CalendarProvider,
    TokenCipher,
)
from app.calendar_integration.service import connection_credentials
from app.models import (
    CalendarConnection,
    CalendarEventMapping,
    CalendarSelection,
    ScheduledSession,
    SchedulePlan,
    Task,
    User,
)
from app.models.calendar import CalendarConnectionStatus
from app.models.calendar_sync import SyncStatus
from app.planner.session_query import timeline_sessions_statement
from app.schedule_plans.models import ScheduledSessionStatus, SchedulePlanStatus

CALENDAR_READ_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    }
)
DAY_VIEW_PLAN_STATUSES = frozenset(
    {
        SchedulePlanStatus.confirmed,
        SchedulePlanStatus.revalidation_required,
        SchedulePlanStatus.applying,
        SchedulePlanStatus.applied,
        SchedulePlanStatus.partially_applied,
    }
)
DAY_VIEW_SESSION_STATUSES = frozenset(
    {ScheduledSessionStatus.confirmed, ScheduledSessionStatus.applied}
)
type TimelineSessionRow = tuple[
    ScheduledSession,
    SchedulePlan,
    Task | None,
    CalendarEventMapping | None,
    CalendarConnection | None,
]


def local_day_bounds(value: date, timezone: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone)
    start = datetime.combine(value, time.min, zone).astimezone(UTC)
    end = datetime.combine(value + timedelta(days=1), time.min, zone).astimezone(UTC)
    return start, end


def selected_day_view_calendars(
    connection: CalendarConnection,
) -> list[CalendarSelection]:
    """Temporary MVP policy, isolated for the future visibility preference."""
    return sorted(
        (
            selection
            for selection in connection.selections
            if selection.include_in_availability
        ),
        key=lambda item: item.external_calendar_id,
    )


class CalendarDayReadService:
    def __init__(
        self,
        session: Session,
        *,
        provider: CalendarProvider,
        oauth_client: CalendarOAuthClient,
        cipher: TokenCipher,
    ) -> None:
        self._session = session
        self._provider = provider
        self._oauth_client = oauth_client
        self._cipher = cipher

    async def read(
        self,
        *,
        user: User,
        selected_date: date,
        now: datetime | None = None,
    ) -> CalendarDayResponse:
        day_start, day_end = local_day_bounds(selected_date, user.timezone)
        connections = list(
            self._session.scalars(
                select(CalendarConnection)
                .where(
                    CalendarConnection.user_id == user.id,
                    CalendarConnection.status == CalendarConnectionStatus.active,
                )
                .options(selectinload(CalendarConnection.selections))
                .order_by(CalendarConnection.id)
            )
        )
        (
            events,
            errors,
            loaded_calendars,
            attempted,
            succeeded,
        ) = await self._external_events(
            connections=connections,
            day_start=day_start,
            day_end=day_end,
            timezone=user.timezone,
        )
        items = self._merge_items(
            user_id=user.id,
            day_start=day_start,
            day_end=day_end,
            events=events,
            errors=errors,
            loaded_calendars=loaded_calendars,
        )
        sync_status = _sync_status(
            connections=connections,
            attempted=attempted,
            succeeded=succeeded,
            errors=errors,
        )
        return CalendarDayResponse(
            date=selected_date,
            timezone=user.timezone,
            day_start=day_start,
            day_end=day_end,
            generated_at=(now or datetime.now(UTC)).astimezone(UTC),
            sync_state=CalendarDaySyncState(
                status=sync_status,
                partial=sync_status is CalendarDaySyncStatus.partial,
                errors=errors,
            ),
            items=sorted(items, key=lambda item: (item.start, item.end, item.id)),
        )

    async def _external_events(
        self,
        *,
        connections: list[CalendarConnection],
        day_start: datetime,
        day_end: datetime,
        timezone: str,
    ) -> tuple[
        list[tuple[uuid.UUID, ExternalCalendarEvent]],
        list[CalendarDayError],
        set[tuple[uuid.UUID, str]],
        int,
        int,
    ]:
        events: list[tuple[uuid.UUID, ExternalCalendarEvent]] = []
        errors: list[CalendarDayError] = []
        loaded_calendars: set[tuple[uuid.UUID, str]] = set()
        attempted = 0
        succeeded = 0
        for connection in connections:
            calendars = selected_day_view_calendars(connection)
            attempted += len(calendars)
            if not calendars:
                continue
            if not CALENDAR_READ_SCOPES.intersection(connection.scopes):
                errors.append(
                    CalendarDayError(
                        code="reauthorization_required",
                        message="Google Calendar access must be reauthorized.",
                        connection_id=connection.id,
                    )
                )
                continue
            try:
                credentials = await connection_credentials(
                    self._session,
                    connection,
                    cipher=self._cipher,
                    oauth_client=self._oauth_client,
                )
            except CalendarIntegrationError as exc:
                errors.append(_provider_error(exc, connection.id))
                continue
            for calendar in calendars:
                try:
                    listed = await self._provider.list_events(
                        credentials,
                        calendar_id=calendar.external_calendar_id,
                        time_min=day_start,
                        time_max=day_end,
                        timezone=timezone,
                    )
                except CalendarAuthorizationError:
                    try:
                        credentials = await connection_credentials(
                            self._session,
                            connection,
                            cipher=self._cipher,
                            oauth_client=self._oauth_client,
                            force_refresh=True,
                        )
                        listed = await self._provider.list_events(
                            credentials,
                            calendar_id=calendar.external_calendar_id,
                            time_min=day_start,
                            time_max=day_end,
                            timezone=timezone,
                        )
                    except CalendarIntegrationError as exc:
                        errors.append(
                            _provider_error(
                                exc, connection.id, calendar.external_calendar_id
                            )
                        )
                        continue
                except CalendarIntegrationError as exc:
                    errors.append(
                        _provider_error(
                            exc, connection.id, calendar.external_calendar_id
                        )
                    )
                    continue
                succeeded += 1
                loaded_calendars.add((connection.id, calendar.external_calendar_id))
                events.extend(
                    (connection.id, event)
                    for event in listed
                    if event.status == "cancelled"
                    or (
                        event.start is not None
                        and event.end is not None
                        and event.end > day_start
                        and event.start < day_end
                    )
                )
        return events, errors, loaded_calendars, attempted, succeeded

    def _merge_items(
        self,
        *,
        user_id: uuid.UUID,
        day_start: datetime,
        day_end: datetime,
        events: Iterable[tuple[uuid.UUID, ExternalCalendarEvent]],
        errors: list[CalendarDayError],
        loaded_calendars: set[tuple[uuid.UUID, str]],
    ) -> list[CalendarDayItem]:
        rows = list(
            self._session.execute(
                timeline_sessions_statement(
                    user_id=user_id,
                    start=day_start,
                    end=day_end,
                    plan_statuses=DAY_VIEW_PLAN_STATUSES,
                )
            )
        )
        sessions_by_identity: dict[tuple[uuid.UUID, str, str], TimelineSessionRow] = {}
        visible_rows: list[TimelineSessionRow] = []
        for raw_row in rows:
            row = cast(TimelineSessionRow, tuple(raw_row))
            scheduled, _plan, _task, mapping, connection = row
            if scheduled.status not in DAY_VIEW_SESSION_STATUSES:
                continue
            visible_rows.append(row)
            if mapping is not None and connection is not None:
                sessions_by_identity[
                    (connection.id, mapping.calendar_id, mapping.external_event_id)
                ] = row

        items: list[CalendarDayItem] = []
        merged_session_ids: set[uuid.UUID] = set()
        for connection_id, event in events:
            linked_row = sessions_by_identity.get(
                (connection_id, event.calendar_id, event.external_event_id)
            )
            if event.status == "cancelled":
                if linked_row is not None:
                    scheduled, plan, task, _mapping, _connection = linked_row
                    merged_session_ids.add(scheduled.id)
                    errors.append(
                        CalendarDayError(
                            code="external_event_cancelled",
                            message="Applied Google Calendar event is cancelled.",
                            connection_id=connection_id,
                            calendar_id=event.calendar_id,
                            scheduled_session_id=scheduled.id,
                        )
                    )
                    items.append(
                        _attention_item(
                            scheduled,
                            plan,
                            task,
                            status="external_event_cancelled",
                        )
                    )
                continue
            assert event.start is not None and event.end is not None
            linked = None
            if linked_row is not None:
                scheduled, plan, task, _mapping, _connection = linked_row
                merged_session_ids.add(scheduled.id)
                linked = _session_link(scheduled, plan, task)
            items.append(
                CalendarDayItem(
                    id=(
                        f"google:{connection_id}:{event.calendar_id}:"
                        f"{event.external_event_id}"
                    ),
                    source=CalendarDaySource.google_calendar,
                    kind=CalendarDayKind.event,
                    title=event.title,
                    start=event.start,
                    end=event.end,
                    all_day=event.all_day,
                    status=event.status or "confirmed",
                    scheduled_session=linked,
                )
            )

        for scheduled, plan, task, mapping, _connection in visible_rows:
            if scheduled.id in merged_session_ids:
                continue
            if (
                mapping is not None
                and mapping.sync_status is SyncStatus.externally_deleted
            ):
                errors.append(
                    CalendarDayError(
                        code="external_event_missing",
                        message=(
                            "Applied calendar event is missing from Google Calendar."
                        ),
                        connection_id=mapping.calendar_connection_id,
                        calendar_id=mapping.calendar_id,
                        scheduled_session_id=scheduled.id,
                    )
                )
                items.append(
                    _attention_item(
                        scheduled,
                        plan,
                        task,
                        status="externally_deleted",
                    )
                )
                continue
            if mapping is not None:
                calendar_loaded = (
                    mapping.calendar_connection_id,
                    mapping.calendar_id,
                ) in loaded_calendars
                if calendar_loaded:
                    errors.append(
                        CalendarDayError(
                            code="mapped_event_not_returned",
                            message=(
                                "Mapped Google Calendar event was not returned "
                                "for this day."
                            ),
                            connection_id=mapping.calendar_connection_id,
                            calendar_id=mapping.calendar_id,
                            scheduled_session_id=scheduled.id,
                        )
                    )
                    attention_status = "mapped_event_not_returned"
                else:
                    attention_status = "unverified"
                items.append(
                    _attention_item(
                        scheduled,
                        plan,
                        task,
                        status=attention_status,
                    )
                )
                continue
            items.append(
                CalendarDayItem(
                    id=f"session:{scheduled.id}",
                    source=CalendarDaySource.ai_calendar,
                    kind=CalendarDayKind.scheduled_session,
                    title=task.title if task is not None else scheduled.title,
                    start=scheduled.start,
                    end=scheduled.end,
                    all_day=False,
                    status=scheduled.status.value,
                    scheduled_session=_session_link(scheduled, plan, task),
                )
            )
        return items


def _session_link(
    scheduled: ScheduledSession, plan: SchedulePlan, task: Task | None
) -> CalendarDayScheduledSession:
    return CalendarDayScheduledSession(
        session_id=scheduled.id,
        plan_id=plan.id,
        task_id=task.id if task is not None else scheduled.task_id,
        plan_status=plan.status,
        session_status=scheduled.status,
    )


def _attention_item(
    scheduled: ScheduledSession,
    plan: SchedulePlan,
    task: Task | None,
    *,
    status: str,
) -> CalendarDayItem:
    return CalendarDayItem(
        id=f"session:{scheduled.id}",
        source=CalendarDaySource.ai_calendar,
        kind=CalendarDayKind.needs_attention,
        title=task.title if task is not None else scheduled.title,
        start=scheduled.start,
        end=scheduled.end,
        all_day=False,
        status=status,
        scheduled_session=_session_link(scheduled, plan, task),
    )


def _provider_error(
    exc: CalendarIntegrationError,
    connection_id: uuid.UUID,
    calendar_id: str | None = None,
) -> CalendarDayError:
    code = (
        "reauthorization_required"
        if isinstance(exc, CalendarReconnectRequiredError)
        else exc.code
    )
    return CalendarDayError(
        code=code,
        message=exc.message,
        connection_id=connection_id,
        calendar_id=calendar_id,
    )


def _sync_status(
    *,
    connections: list[CalendarConnection],
    attempted: int,
    succeeded: int,
    errors: list[CalendarDayError],
) -> CalendarDaySyncStatus:
    if not connections:
        return CalendarDaySyncStatus.not_connected
    if attempted == 0:
        return CalendarDaySyncStatus.no_calendars_selected
    if any(error.code == "reauthorization_required" for error in errors):
        if succeeded == 0:
            return CalendarDaySyncStatus.reauthorization_required
    if errors and succeeded:
        return CalendarDaySyncStatus.partial
    if errors or (attempted > 0 and succeeded == 0):
        return CalendarDaySyncStatus.error
    return CalendarDaySyncStatus.fresh
