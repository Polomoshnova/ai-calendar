from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.api.dependencies import DatabaseSession
from app.calendar_day import CalendarDayReadService, CalendarDayResponse
from app.core.config import Settings, get_settings
from app.internal.calendar_router import CalendarRuntimeDependency
from app.internal.dependencies import InternalToolsEnabled
from app.models import User

router = APIRouter(prefix="/calendar", tags=["calendar"])


def require_dev_calendar_user(
    session: DatabaseSession,
    settings: Annotated[Settings, Depends(get_settings)],
    _enabled: InternalToolsEnabled,
) -> User:
    email = settings.calendar_day_dev_user_email
    if email is None or not email.strip():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    user = session.scalar(
        select(User).where(func.lower(User.email) == email.strip().lower())
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configured development user not found",
        )
    return user


DevCalendarUser = Annotated[User, Depends(require_dev_calendar_user)]


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
