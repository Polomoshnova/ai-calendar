import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.tasks import (
    PreferredTimeOfDay,
    TaskPriority,
    TaskStatus,
    validate_task,
)


class TaskCreate(BaseModel):
    user_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    duration_minutes: int = Field(gt=0)
    earliest_start: datetime | None = None
    deadline: datetime | None = None
    priority: TaskPriority = TaskPriority.medium
    preferred_time_of_day: PreferredTimeOfDay = PreferredTimeOfDay.any
    is_splittable: bool = False
    minimum_session_minutes: int = Field(default=15, gt=0)
    maximum_sessions_per_day: int = Field(default=1, gt=0)

    @model_validator(mode="after")
    def validate_domain_rules(self) -> Self:
        validate_task(
            self.duration_minutes,
            self.earliest_start,
            self.deadline,
            is_splittable=self.is_splittable,
            minimum_session_minutes=self.minimum_session_minutes,
            maximum_sessions_per_day=self.maximum_sessions_per_day,
        )
        return self


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    earliest_start: datetime | None = None
    deadline: datetime | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    preferred_time_of_day: PreferredTimeOfDay | None = None
    is_splittable: bool | None = None
    minimum_session_minutes: int | None = Field(default=None, gt=0)
    maximum_sessions_per_day: int | None = Field(default=None, gt=0)


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
    preferred_time_of_day: PreferredTimeOfDay
    is_splittable: bool
    minimum_session_minutes: int
    maximum_sessions_per_day: int
    created_at: datetime
    updated_at: datetime
