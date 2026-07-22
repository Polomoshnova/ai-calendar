import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.tasks import validate_task
from app.models import Task, User
from app.schemas.task import TaskCreate, TaskUpdate


class UserNotFoundError(Exception):
    pass


def create_task(session: Session, data: TaskCreate) -> Task:
    if session.get(User, data.user_id) is None:
        raise UserNotFoundError

    task = Task(**data.model_dump())
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def list_tasks(session: Session, user_id: uuid.UUID | None = None) -> list[Task]:
    statement = select(Task).order_by(Task.created_at, Task.id)
    if user_id is not None:
        statement = statement.where(Task.user_id == user_id)
    return list(session.scalars(statement))


def get_task(session: Session, task_id: uuid.UUID) -> Task | None:
    return session.get(Task, task_id)


def update_task(session: Session, task: Task, data: TaskUpdate) -> Task:
    updates = data.model_dump(exclude_unset=True, exclude_none=True)
    duration = updates.get("duration_minutes", task.duration_minutes)
    earliest_start = updates.get("earliest_start", task.earliest_start)
    deadline = updates.get("deadline", task.deadline)
    is_splittable = updates.get("is_splittable", task.is_splittable)
    minimum_session = updates.get(
        "minimum_session_minutes", task.minimum_session_minutes
    )
    maximum_sessions = updates.get(
        "maximum_sessions_per_day", task.maximum_sessions_per_day
    )
    validate_task(
        duration,
        earliest_start,
        deadline,
        is_splittable=is_splittable,
        minimum_session_minutes=minimum_session,
        maximum_sessions_per_day=maximum_sessions,
    )

    for field, value in updates.items():
        setattr(task, field, value)

    session.commit()
    session.refresh(task)
    return task


def delete_task(session: Session, task: Task) -> None:
    session.delete(task)
    session.commit()
