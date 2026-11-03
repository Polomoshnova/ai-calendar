from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import func, select

from app.api.dependencies import DatabaseSession
from app.core.config import Settings, get_settings
from app.models import User


def require_internal_tools(
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if not settings.enable_internal_tools:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


InternalToolsEnabled = Annotated[None, Depends(require_internal_tools)]


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
