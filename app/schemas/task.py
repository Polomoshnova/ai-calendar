import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.tasks import TaskPriority, TaskStatus, validate_task


class TaskCreate(BaseModel):
    user_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    duration_minutes: int = Field(gt=0)
    earliest_start: datetime | None = None
    deadline: datetime | None = None
    priority: TaskPriority = TaskPriority.medium

    @model_validator(mode="after")
    def validate_domain_rules(self) -> Self:
        validate_task(self.duration_minutes, self.earliest_start, self.deadline)
        return self


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    earliest_start: datetime | None = None
    deadline: datetime | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    description: str | None
    duration_minutes: int
    earliest_start: datetime | None
    deadline: datetime | None
    priority: TaskPriority
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
