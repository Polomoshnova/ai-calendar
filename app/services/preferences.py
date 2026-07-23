import uuid

from sqlalchemy.orm import Session

from app.domain.preferences import (
    SchedulingPreferences,
    default_working_hours,
    parse_working_hours,
)
from app.domain.tasks import PreferredTimeOfDay
from app.models import User
from app.models.user_preferences import UserPreferences


class UserNotFoundError(Exception):
    pass


def load_scheduling_preferences(
    session: Session, user_id: uuid.UUID
) -> SchedulingPreferences:
    user = session.get(User, user_id)
    if user is None:
        raise UserNotFoundError

    stored = user.preferences
    if stored is None:
        return SchedulingPreferences(
            timezone=user.timezone,
            working_hours=default_working_hours(),
            preferred_task_time=PreferredTimeOfDay.any,
            minimum_break_minutes=0,
            no_deep_work_after=None,
            default_minimum_session_minutes=15,
        )

    return SchedulingPreferences(
        timezone=user.timezone,
        working_hours=parse_working_hours(stored.working_hours),
        preferred_task_time=stored.preferred_task_time,
        minimum_break_minutes=stored.minimum_break_minutes,
        no_deep_work_after=stored.no_deep_work_after,
        default_minimum_session_minutes=stored.default_minimum_session_minutes,
    )


def save_scheduling_preferences(
    session: Session,
    user_id: uuid.UUID,
    preferences: SchedulingPreferences,
) -> SchedulingPreferences:
    user = session.get(User, user_id)
    if user is None:
        raise UserNotFoundError
    if user.timezone != preferences.timezone:
        raise ValueError("preferences timezone must match the user timezone")

    stored = user.preferences
    if stored is None:
        stored = UserPreferences(user_id=user.id)
        user.preferences = stored
    from app.domain.preferences import serialize_working_hours

    stored.working_hours = serialize_working_hours(preferences.working_hours)
    stored.preferred_task_time = preferences.preferred_task_time
    stored.minimum_break_minutes = preferences.minimum_break_minutes
    stored.no_deep_work_after = preferences.no_deep_work_after
    stored.default_minimum_session_minutes = preferences.default_minimum_session_minutes
    session.commit()
    session.refresh(stored)
    return load_scheduling_preferences(session, user_id)
