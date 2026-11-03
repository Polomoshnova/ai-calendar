from datetime import date

from fastapi import APIRouter

from app.api.dependencies import DatabaseSession
from app.calendar_day import CalendarDayReadService, CalendarDayResponse
from app.internal.calendar_router import CalendarRuntimeDependency
from app.internal.dependencies import DevCalendarUser

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("/day", response_model=CalendarDayResponse)
async def calendar_day(
    date: date,
    session: DatabaseSession,
    user: DevCalendarUser,
    runtime: CalendarRuntimeDependency,
) -> CalendarDayResponse:
    return await CalendarDayReadService(
        session,
        provider=runtime.provider,
        oauth_client=runtime.oauth,
        cipher=runtime.cipher,
    ).read(user=user, selected_date=date)
