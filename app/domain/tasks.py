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
    cancelled = "cancelled"


class PreferredTimeOfDay(StrEnum):
    any = "any"
    morning = "morning"
    afternoon = "afternoon"
    evening = "evening"


def validate_task(
    duration_minutes: int,
    earliest_start: datetime | None,
    deadline: datetime | None,
    *,
    is_splittable: bool = False,
    minimum_session_minutes: int = 15,
    maximum_sessions_per_day: int = 1,
) -> None:
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be positive")
    if any(
        value is not None and value.tzinfo is None
        for value in (earliest_start, deadline)
    ):
        raise ValueError("task datetimes must be timezone-aware")
    if (
        earliest_start is not None
        and deadline is not None
        and earliest_start >= deadline
    ):
        raise ValueError("earliest_start must be before deadline")
    if minimum_session_minutes <= 0:
        raise ValueError("minimum_session_minutes must be positive")
    if maximum_sessions_per_day <= 0:
        raise ValueError("maximum_sessions_per_day must be positive")
    if is_splittable and minimum_session_minutes > duration_minutes:
        raise ValueError(
            "minimum_session_minutes cannot exceed duration_minutes for a "
            "splittable task"
        )
