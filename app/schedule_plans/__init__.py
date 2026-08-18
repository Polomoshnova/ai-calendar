from app.schedule_plans.errors import (
    InvalidPlanTransitionError,
    SchedulePlanImmutableError,
    SchedulePlanNotFoundError,
    SchedulePlanUserNotFoundError,
    SchedulePlanValidationError,
)
from app.schedule_plans.models import (
    ScheduledSession,
    ScheduledSessionStatus,
    SchedulePlan,
    SchedulePlanSource,
    SchedulePlanStatus,
)


def __getattr__(name: str) -> object:
    """Keep service exports lazy so model imports do not create domain cycles."""
    if name in {
        "confirm_schedule_plan",
        "create_schedule_plan_from_preview",
        "obsolete_schedule_plan",
    }:
        from app.schedule_plans import service

        return getattr(service, name)
    raise AttributeError(name)


__all__ = [
    "InvalidPlanTransitionError",
    "ScheduledSession",
    "ScheduledSessionStatus",
    "SchedulePlan",
    "SchedulePlanImmutableError",
    "SchedulePlanNotFoundError",
    "SchedulePlanSource",
    "SchedulePlanStatus",
    "SchedulePlanUserNotFoundError",
    "SchedulePlanValidationError",
    "confirm_schedule_plan",
    "create_schedule_plan_from_preview",
    "obsolete_schedule_plan",
]
