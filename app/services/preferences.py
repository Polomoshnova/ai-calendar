import uuid

from sqlalchemy.orm import Session

from app.domain.preferences import (
    SchedulingPreferences,
    default_working_hours,
    parse_working_hours,
)
from app.domain.tasks import PreferredTimeOfDay
from app.models import User


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
