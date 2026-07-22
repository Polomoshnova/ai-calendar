from datetime import datetime
from enum import StrEnum


class TaskPriority(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class TaskStatus(StrEnum):
    pending = "pending"
    completed = "completed"


def validate_task(
    duration_minutes: int,
    earliest_start: datetime | None,
    deadline: datetime | None,
) -> None:
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be positive")
    if (
        earliest_start is not None
        and deadline is not None
        and earliest_start >= deadline
    ):
        raise ValueError("earliest_start must be before deadline")
