import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.schedule_plans.models import ScheduledSession, SchedulePlan, SchedulePlanStatus

RESERVING_PLAN_STATUSES = frozenset(
    {
        SchedulePlanStatus.confirmed,
        SchedulePlanStatus.revalidation_required,
        SchedulePlanStatus.applying,
        SchedulePlanStatus.applied,
        SchedulePlanStatus.partially_applied,
    }
)


def schedule_plan_status_reserves_time(status: SchedulePlanStatus) -> bool:
    """Use the single reservation policy shared by planning consumers."""
    return status in RESERVING_PLAN_STATUSES


@dataclass(frozen=True)
class ReservedInterval:
    plan_id: uuid.UUID
    scheduled_session_id: uuid.UUID
    start: datetime
    end: datetime


def get_schedule_plan(
    session: Session,
    plan_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> SchedulePlan | None:
    statement = (
        select(SchedulePlan)
        .where(SchedulePlan.id == plan_id)
        .options(
            selectinload(SchedulePlan.sessions).selectinload(
                ScheduledSession.calendar_event_mapping
            )
        )
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def get_schedule_plan_by_idempotency_key(
    session: Session,
    idempotency_key: str,
) -> SchedulePlan | None:
    return session.scalar(
        select(SchedulePlan)
        .where(SchedulePlan.idempotency_key == idempotency_key)
        .options(selectinload(SchedulePlan.sessions))
    )


def latest_plan_in_group(
    session: Session,
    plan_group_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> SchedulePlan | None:
    statement = (
        select(SchedulePlan)
        .where(SchedulePlan.plan_group_id == plan_group_id)
        .order_by(SchedulePlan.version.desc())
        .limit(1)
        .options(selectinload(SchedulePlan.sessions))
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def list_schedule_plans(
    session: Session,
    *,
    user_id: uuid.UUID,
    status: SchedulePlanStatus | None = None,
    task_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SchedulePlan]:
    statement = (
        select(SchedulePlan)
        .where(SchedulePlan.user_id == user_id)
        .order_by(SchedulePlan.created_at.desc(), SchedulePlan.id.desc())
        .limit(limit)
        .offset(offset)
        .options(selectinload(SchedulePlan.sessions))
    )
    if status is not None:
        statement = statement.where(SchedulePlan.status == status)
    if task_id is not None:
        statement = statement.where(SchedulePlan.task_id == task_id)
    return list(session.scalars(statement))


def list_reserved_intervals(
    session: Session,
    *,
    user_id: uuid.UUID,
    start: datetime,
    end: datetime,
    exclude_plan_id: uuid.UUID | None = None,
) -> list[ReservedInterval]:
    """Return plan reservations overlapping the half-open window [start, end)."""
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if start >= end:
        raise ValueError("start must be before end")

    statement = (
        select(
            ScheduledSession.plan_id,
            ScheduledSession.id,
            ScheduledSession.start,
            ScheduledSession.end,
        )
        .join(SchedulePlan, SchedulePlan.id == ScheduledSession.plan_id)
        .where(
            SchedulePlan.user_id == user_id,
            SchedulePlan.status.in_(RESERVING_PLAN_STATUSES),
            ScheduledSession.start < end,
            start < ScheduledSession.end,
        )
        .order_by(ScheduledSession.start, ScheduledSession.end, ScheduledSession.id)
    )
    if exclude_plan_id is not None:
        statement = statement.where(SchedulePlan.id != exclude_plan_id)
    return [
        ReservedInterval(
            plan_id=row.plan_id,
            scheduled_session_id=row.id,
            start=row.start,
            end=row.end,
        )
        for row in session.execute(statement)
    ]
