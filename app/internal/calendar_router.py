import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.calendar_integration.api_models import (
    CalendarListResponse,
    CalendarPreviewRequest,
    CalendarPreviewResponse,
    CalendarQuerySummary,
    CalendarSelectionItem,
    CalendarSelectionRequest,
    CalendarSelectionsResponse,
    ConnectionStatusResponse,
    FreeBusyRequest,
    FreeBusyResponse,
    OAuthCallbackResponse,
    OAuthStartRequest,
    OAuthStartResponse,
    ProviderBusyInterval,
)
from app.calendar_integration.errors import (
    CalendarAuthorizationError,
    CalendarConfigurationError,
    CalendarConnectionNotFoundError,
    CalendarIntegrationError,
    CalendarProviderError,
    CalendarRateLimitError,
    CalendarReconnectRequiredError,
    CalendarSelectionError,
    CalendarUnavailableError,
    CalendarValidationError,
)
from app.calendar_integration.mapper import normalize_calendar_busy_intervals
from app.calendar_integration.models import CalendarBusyInterval, CalendarBusyResult
from app.calendar_integration.runtime import CalendarRuntime, build_calendar_runtime
from app.calendar_integration.service import (
    consume_oauth_state,
    create_oauth_state,
    disconnect_connection,
    list_connection_calendars,
    owned_connection,
    query_connection_busy,
    replace_calendar_selections,
    store_google_connection,
)
from app.core.config import Settings, get_settings
from app.internal.dependencies import InternalToolsEnabled
from app.schemas.scheduling import SchedulePreviewResponse
from app.services.scheduling import generate_schedule_preview

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/api/calendar", tags=["internal-calendar"])


async def calendar_runtime(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[CalendarRuntime]:
    timeout = httpx.Timeout(settings.google_calendar_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            yield build_calendar_runtime(settings, client)
        except CalendarIntegrationError as exc:
            raise _http_error(exc) from exc


CalendarRuntimeDependency = Annotated[CalendarRuntime, Depends(calendar_runtime)]


@router.post("/google/oauth/start", response_model=OAuthStartResponse)
def oauth_start(
    data: OAuthStartRequest,
    session: DatabaseSession,
    runtime: CalendarRuntimeDependency,
    _enabled: InternalToolsEnabled,
) -> OAuthStartResponse:
    try:
        _, expires_at, url = create_oauth_state(
            session, user_id=data.user_id, oauth_client=runtime.oauth
        )
        return OAuthStartResponse(authorization_url=url, expires_at=expires_at)
    except CalendarIntegrationError as exc:
        raise _http_error(exc) from exc


@router.get("/google/oauth/callback", response_model=OAuthCallbackResponse)
async def oauth_callback(
    session: DatabaseSession,
    runtime: CalendarRuntimeDependency,
    _enabled: InternalToolsEnabled,
    state: str = Query(min_length=1),
    code: str | None = None,
    error: str | None = None,
) -> OAuthCallbackResponse:
    try:
        oauth_state = consume_oauth_state(session, raw_state=state)
        if error is not None:
            raise CalendarAuthorizationError("Google authorization was not completed")
        if not code:
            raise CalendarAuthorizationError("Missing authorization code")
        tokens = await runtime.oauth.exchange_code(code)
        connection = store_google_connection(
            session,
            user_id=oauth_state.user_id,
            tokens=tokens,
            cipher=runtime.cipher,
        )
        await list_connection_calendars(
            session,
            connection,
            provider=runtime.provider,
            oauth_client=runtime.oauth,
            cipher=runtime.cipher,
        )
        return OAuthCallbackResponse(status="connected", connection_id=connection.id)
    except CalendarIntegrationError as exc:
        raise _http_error(exc) from exc


@router.get("/connections/{connection_id}", response_model=ConnectionStatusResponse)
def connection_status(
    connection_id: uuid.UUID,
    user_id: uuid.UUID,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> ConnectionStatusResponse:
    try:
        connection = owned_connection(session, connection_id, user_id)
    except CalendarIntegrationError as exc:
        raise _http_error(exc) from exc
    return ConnectionStatusResponse(
        id=connection.id,
        provider="google",
        status=connection.status.value,
        provider_account_email=connection.provider_account_email,
        scopes=connection.scopes,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
        token_expires_at=connection.token_expires_at,
        selected_calendar_count=sum(
            item.include_in_availability for item in connection.selections
        ),
        last_successful_sync_at=connection.last_successful_sync_at,
        last_error_code=connection.last_error_code,
    )


@router.delete("/connections/{connection_id}", status_code=204)
async def disconnect(
    connection_id: uuid.UUID,
    user_id: uuid.UUID,
    session: DatabaseSession,
    runtime: CalendarRuntimeDependency,
    _enabled: InternalToolsEnabled,
) -> None:
    try:
        connection = owned_connection(session, connection_id, user_id)
        await disconnect_connection(
            session, connection, oauth_client=runtime.oauth, cipher=runtime.cipher
        )
    except CalendarIntegrationError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/connections/{connection_id}/calendars", response_model=CalendarListResponse
)
async def calendars(
    connection_id: uuid.UUID,
    user_id: uuid.UUID,
    session: DatabaseSession,
    runtime: CalendarRuntimeDependency,
    _enabled: InternalToolsEnabled,
) -> CalendarListResponse:
    try:
        connection = owned_connection(session, connection_id, user_id)
        items = await list_connection_calendars(
            session,
            connection,
            provider=runtime.provider,
            oauth_client=runtime.oauth,
            cipher=runtime.cipher,
        )
        return CalendarListResponse(calendars=items)
    except CalendarIntegrationError as exc:
        raise _http_error(exc) from exc


@router.put(
    "/connections/{connection_id}/selections",
    response_model=CalendarSelectionsResponse,
)
async def selections(
    connection_id: uuid.UUID,
    data: CalendarSelectionRequest,
    user_id: uuid.UUID,
    session: DatabaseSession,
    runtime: CalendarRuntimeDependency,
    _enabled: InternalToolsEnabled,
) -> CalendarSelectionsResponse:
    try:
        connection = owned_connection(session, connection_id, user_id)
        records = await replace_calendar_selections(
            session,
            connection,
            calendar_ids=data.calendar_ids,
            provider=runtime.provider,
            oauth_client=runtime.oauth,
            cipher=runtime.cipher,
        )
        return CalendarSelectionsResponse(
            calendars=[
                CalendarSelectionItem(
                    id=item.external_calendar_id,
                    name=item.display_name,
                    timezone=item.timezone,
                    primary=item.primary,
                    include_in_availability=item.include_in_availability,
                )
                for item in records
            ]
        )
    except CalendarIntegrationError as exc:
        raise _http_error(exc) from exc


@router.post("/connections/{connection_id}/free-busy", response_model=FreeBusyResponse)
async def free_busy(
    connection_id: uuid.UUID,
    data: FreeBusyRequest,
    user_id: uuid.UUID,
    session: DatabaseSession,
    runtime: CalendarRuntimeDependency,
    _enabled: InternalToolsEnabled,
) -> FreeBusyResponse:
    try:
        connection = owned_connection(session, connection_id, user_id)
        calendar_ids, result = await query_connection_busy(
            session,
            connection,
            calendar_ids=data.calendar_ids,
            time_min=data.time_min,
            time_max=data.time_max,
            timezone=data.timezone,
            provider=runtime.provider,
            oauth_client=runtime.oauth,
            cipher=runtime.cipher,
        )
        return FreeBusyResponse(
            connection_id=connection.id,
            provider="google",
            time_min=result.time_min,
            time_max=result.time_max,
            timezone=result.timezone,
            calendar_ids=calendar_ids,
            busy_intervals=[
                ProviderBusyInterval(**item.model_dump()) for item in result.intervals
            ],
            errors=result.errors,
        )
    except CalendarIntegrationError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/connections/{connection_id}/scheduling/preview",
    response_model=CalendarPreviewResponse,
)
async def calendar_preview(
    connection_id: uuid.UUID,
    data: CalendarPreviewRequest,
    user_id: uuid.UUID,
    session: DatabaseSession,
    runtime: CalendarRuntimeDependency,
    _enabled: InternalToolsEnabled,
) -> CalendarPreviewResponse:
    try:
        connection = owned_connection(session, connection_id, user_id)
        calendar_ids, result = await query_connection_busy(
            session,
            connection,
            calendar_ids=data.calendar_ids,
            time_min=data.planning_window.start,
            time_max=data.planning_window.end,
            timezone=data.timezone,
            provider=runtime.provider,
            oauth_client=runtime.oauth,
            cipher=runtime.cipher,
        )
        combined = CalendarBusyResult(
            time_min=result.time_min,
            time_max=result.time_max,
            timezone=result.timezone,
            intervals=[
                *result.intervals,
                *[
                    CalendarBusyInterval(
                        start=item.start,
                        end=item.end,
                        calendar_id="additional",
                    )
                    for item in data.additional_busy_intervals
                ],
            ],
            errors=result.errors,
        )
        merged = normalize_calendar_busy_intervals(combined)
        preview = generate_schedule_preview(
            planning_window=data.planning_window.to_domain(),
            busy_intervals=tuple(item.to_domain() for item in merged),
            tasks=tuple(item.to_domain() for item in data.pending_tasks),
            preferences=data.preferences.to_domain(),
        )
        return CalendarPreviewResponse(
            calendar_context=CalendarQuerySummary(
                provider="google",
                calendar_ids=calendar_ids,
                provider_busy_interval_count=len(result.intervals),
                merged_busy_interval_count=len(merged),
                calendar_errors=result.errors,
            ),
            busy_intervals=merged,
            schedule_preview=SchedulePreviewResponse.from_domain(
                preview.planning_window, preview.free_intervals, preview.result
            ),
        )
    except CalendarIntegrationError as exc:
        raise _http_error(exc) from exc


def _http_error(exc: CalendarIntegrationError) -> HTTPException:
    if isinstance(exc, CalendarConnectionNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, CalendarReconnectRequiredError):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, CalendarRateLimitError):
        code = status.HTTP_429_TOO_MANY_REQUESTS
    elif isinstance(exc, (CalendarConfigurationError, CalendarUnavailableError)):
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif isinstance(exc, CalendarProviderError):
        code = status.HTTP_502_BAD_GATEWAY
    elif isinstance(
        exc,
        (
            CalendarValidationError,
            CalendarSelectionError,
            CalendarAuthorizationError,
        ),
    ):
        code = status.HTTP_400_BAD_REQUEST
    else:
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
    logger.warning("calendar_integration_error code=%s", exc.code)
    return HTTPException(status_code=code, detail=exc.to_detail())
