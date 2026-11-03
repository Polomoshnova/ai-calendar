import uuid
from datetime import datetime

from sqlalchemy import Select, select

from app.models import (
    CalendarConnection,
    CalendarEventMapping,
    ScheduledSession,
    SchedulePlan,
    Task,
)
from app.schedule_plans.models import SchedulePlanStatus


def timeline_sessions_statement(
    *,
    user_id: uuid.UUID,
    start: datetime,
    end: datetime,
    plan_statuses: frozenset[SchedulePlanStatus],
) -> Select[
    tuple[
        ScheduledSession, SchedulePlan, Task, CalendarEventMapping, CalendarConnection
    ]
]:
    """Build the ownership-scoped overlap query shared by calendar read models."""
    return (
        select(
            ScheduledSession,
            SchedulePlan,
            Task,
            CalendarEventMapping,
            CalendarConnection,
        )
        .join(SchedulePlan, SchedulePlan.id == ScheduledSession.plan_id)
        .outerjoin(
            Task,
            (Task.id == ScheduledSession.task_id) & (Task.user_id == user_id),
        )
        .outerjoin(
            CalendarEventMapping,
            CalendarEventMapping.scheduled_session_id == ScheduledSession.id,
        )
        .outerjoin(
            CalendarConnection,
            CalendarConnection.id == CalendarEventMapping.calendar_connection_id,
        )
        .where(
            SchedulePlan.user_id == user_id,
            SchedulePlan.status.in_(plan_statuses),
            ScheduledSession.start < end,
            ScheduledSession.end > start,
        )
        .order_by(ScheduledSession.start, ScheduledSession.id)
    )
