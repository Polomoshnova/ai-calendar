import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CalendarConnection, CalendarEventMapping, User
from app.schedule_plans.models import (
    ScheduledSession,
    ScheduledSessionStatus,
    SchedulePlan,
    SchedulePlanSource,
    SchedulePlanStatus,
)
from app.schedule_plans.repository import list_reserved_intervals


def dt(hour: int) -> datetime:
    return datetime(2026, 7, 28, hour, tzinfo=UTC)


def add_plan(
    session: Session,
    user: User,
    *,
    status: SchedulePlanStatus,
    start: datetime,
    end: datetime,
) -> SchedulePlan:
    plan = SchedulePlan(
        user_id=user.id,
        plan_group_id=uuid.uuid4(),
        source=SchedulePlanSource.manual_preview,
        version=1,
        status=status,
        timezone="Europe/Warsaw",
        planning_window_start=dt(0),
        planning_window_end=dt(23),
        scheduler_version="test",
        idempotency_key=str(uuid.uuid4()),
        confirmed_task_snapshot={},
        scheduling_preferences_snapshot={},
        busy_context_summary={},
        preview_metadata={},
    )
    plan.sessions.append(
        ScheduledSession(
            title="Session",
            start=start,
            end=end,
            duration_minutes=int((end - start).total_seconds() // 60),
            order=1,
            status=ScheduledSessionStatus.confirmed,
        )
    )
    session.add(plan)
    session.commit()
    return plan


@pytest.mark.parametrize(
    "status",
    [
        SchedulePlanStatus.confirmed,
        SchedulePlanStatus.revalidation_required,
        SchedulePlanStatus.applying,
        SchedulePlanStatus.applied,
        SchedulePlanStatus.partially_applied,
    ],
)
def test_reserving_plan_statuses_return_intervals(
    db_session: Session,
    user: User,
    status: SchedulePlanStatus,
) -> None:
    plan = add_plan(db_session, user, status=status, start=dt(9), end=dt(10))

    result = list_reserved_intervals(
        db_session,
        user_id=user.id,
        start=dt(9),
        end=dt(11),
    )

    assert [item.plan_id for item in result] == [plan.id]


@pytest.mark.parametrize(
    "status",
    [
        SchedulePlanStatus.proposed,
        SchedulePlanStatus.failed,
        SchedulePlanStatus.obsolete,
    ],
)
def test_non_reserving_plan_statuses_are_ignored(
    db_session: Session,
    user: User,
    status: SchedulePlanStatus,
) -> None:
    add_plan(db_session, user, status=status, start=dt(9), end=dt(10))

    assert (
        list_reserved_intervals(
            db_session,
            user_id=user.id,
            start=dt(9),
            end=dt(11),
        )
        == []
    )


def test_reserved_intervals_are_half_open_and_support_exclusion(
    db_session: Session,
    user: User,
) -> None:
    plan = add_plan(
        db_session,
        user,
        status=SchedulePlanStatus.confirmed,
        start=dt(9),
        end=dt(10),
    )

    assert (
        list_reserved_intervals(
            db_session,
            user_id=user.id,
            start=dt(10),
            end=dt(11),
        )
        == []
    )
    assert (
        list_reserved_intervals(
            db_session,
            user_id=user.id,
            start=dt(9),
            end=dt(10),
            exclude_plan_id=plan.id,
        )
        == []
    )


def test_calendar_event_mapping_enforces_one_per_session_and_external_identity(
    db_session: Session,
    user: User,
) -> None:
    plan = add_plan(
        db_session,
        user,
        status=SchedulePlanStatus.confirmed,
        start=dt(9),
        end=dt(10),
    )
    connection = CalendarConnection(user_id=user.id, provider="google", scopes=[])
    db_session.add(connection)
    db_session.commit()
    first = CalendarEventMapping(
        scheduled_session_id=plan.sessions[0].id,
        calendar_connection_id=connection.id,
        provider="google",
        calendar_id="primary",
        external_event_id="event-1",
    )
    db_session.add(first)
    db_session.commit()

    duplicate = CalendarEventMapping(
        scheduled_session_id=plan.sessions[0].id,
        calendar_connection_id=connection.id,
        provider="google",
        calendar_id="primary",
        external_event_id="event-2",
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
