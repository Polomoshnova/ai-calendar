import uuid

from fastapi import APIRouter, HTTPException

from app.api.dependencies import DatabaseSession
from app.calendar_integration.errors import CalendarIntegrationError
from app.calendar_sync.pull import (
    CalendarEventMappingNotFoundError,
    pull_calendar_event,
)
from app.calendar_sync.pull_schemas import PullCalendarEventSynchronizationResult
from app.internal.calendar_router import CalendarRuntimeDependency
from app.internal.dependencies import InternalToolsEnabled

router = APIRouter(prefix="/internal/api", tags=["internal-calendar-sync"])


@router.post(
    "/calendar-event-mappings/{mapping_id}/sync",
    response_model=PullCalendarEventSynchronizationResult,
)
async def sync_calendar_event_mapping(
    mapping_id: uuid.UUID,
    user_id: uuid.UUID,
    session: DatabaseSession,
    runtime: CalendarRuntimeDependency,
    _enabled: InternalToolsEnabled,
) -> PullCalendarEventSynchronizationResult:
    try:
        return await pull_calendar_event(
            session,
            user_id=user_id,
            mapping_id=mapping_id,
            provider=runtime.provider,
            oauth_client=runtime.oauth,
            cipher=runtime.cipher,
        )
    except CalendarEventMappingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.to_detail()) from exc
    except CalendarIntegrationError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
