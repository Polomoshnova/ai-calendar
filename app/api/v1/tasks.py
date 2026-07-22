import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Task
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate
from app.services import tasks as task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])
DatabaseSession = Annotated[Session, Depends(get_db)]


def require_task(session: Session, task_id: uuid.UUID) -> Task:
    task = task_service.get_task(session, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    return task


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(data: TaskCreate, session: DatabaseSession) -> Task:
    try:
        return task_service.create_task(session, data)
    except task_service.UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from exc


@router.get("", response_model=list[TaskRead])
def list_tasks(
    session: DatabaseSession,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[Task]:
    return task_service.list_tasks(session, user_id)


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: uuid.UUID, session: DatabaseSession) -> Task:
    return require_task(session, task_id)


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(task_id: uuid.UUID, data: TaskUpdate, session: DatabaseSession) -> Task:
    task = require_task(session, task_id)
    try:
        return task_service.update_task(session, task, data)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: uuid.UUID, session: DatabaseSession) -> Response:
    task_service.delete_task(session, require_task(session, task_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
