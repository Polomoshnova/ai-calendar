import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.availability import TimeInterval, calculate_free_intervals
from app.domain.tasks import TaskStatus
from app.models import Task, User
from app.scheduling import SchedulerPreferences, SchedulerResult, SchedulingTask
from app.scheduling.scheduler import schedule_tasks
from app.services.preferences import load_scheduling_preferences


class PreviewUserNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class SchedulePreview:
    planning_window: TimeInterval
    free_intervals: tuple[TimeInterval, ...]
    result: SchedulerResult


def preview_schedule(
    session: Session,
    user_id: uuid.UUID,
    planning_window: TimeInterval,
    busy_intervals: tuple[TimeInterval, ...],
) -> SchedulePreview:
    if session.get(User, user_id) is None:
        raise PreviewUserNotFoundError

    planning = planning_window.as_utc()
    tasks = _load_preview_tasks(session, user_id, planning)
    stored_preferences = load_scheduling_preferences(session, user_id)
    free = calculate_free_intervals(
        planning,
        stored_preferences.working_hours,
        busy_intervals,
        stored_preferences.timezone,
    )
    scheduling_preferences = SchedulerPreferences(
        timezone=stored_preferences.timezone,
        preferred_task_time=stored_preferences.preferred_task_time,
        minimum_break_minutes=stored_preferences.minimum_break_minutes,
        no_deep_work_after=stored_preferences.no_deep_work_after,
        default_minimum_session_minutes=(
            stored_preferences.default_minimum_session_minutes
        ),
    )
    result = schedule_tasks(
        (_to_scheduling_task(task) for task in tasks),
        free,
        scheduling_preferences,
    )
    return SchedulePreview(
        planning_window=planning,
        free_intervals=free,
        result=result,
    )


def _load_preview_tasks(
    session: Session, user_id: uuid.UUID, planning_window: TimeInterval
) -> list[Task]:
    statement = (
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.status == TaskStatus.pending,
            or_(
                Task.earliest_start.is_(None),
                Task.earliest_start < planning_window.end,
            ),
            or_(Task.deadline.is_(None), Task.deadline > planning_window.start),
        )
        .order_by(Task.id)
    )
    return list(session.scalars(statement))


def _to_scheduling_task(task: Task) -> SchedulingTask:
    return SchedulingTask(
        id=str(task.id),
        duration_minutes=task.duration_minutes,
        priority=task.priority,
        earliest_start=task.earliest_start,
        deadline=task.deadline,
        preferred_time_of_day=task.preferred_time_of_day,
        is_splittable=task.is_splittable,
        minimum_session_minutes=task.minimum_session_minutes,
        maximum_sessions_per_day=task.maximum_sessions_per_day,
    )
