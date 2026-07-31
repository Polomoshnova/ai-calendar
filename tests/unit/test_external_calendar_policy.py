import uuid
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from app.domain.external_calendar_policy import (
    ConflictCode,
    ConflictDetail,
    ConflictSeverity,
    ExtendTaskDeadline,
    ExternalCalendarAggregate,
    ExternalCalendarChangeInput,
    ExternalCalendarSession,
    ExternalEventState,
    MarkExternalEventMissing,
    NoAction,
    RecordConflict,
    UnsupportedExternalChange,
    UpdateScheduledSessionTime,
    evaluate_external_calendar_policy,
)

TASK_ID = uuid.UUID("10000000-0000-0000-0000-000000000000")
PLAN_ID = uuid.UUID("20000000-0000-0000-0000-000000000000")
SESSION_1 = uuid.UUID("30000000-0000-0000-0000-000000000001")
SESSION_2 = uuid.UUID("30000000-0000-0000-0000-000000000002")


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 30, hour, minute, tzinfo=UTC)


DEFAULT_START = dt(9)
DEFAULT_END = dt(10)
DEFAULT_DEADLINE = dt(17)
DEFAULT_SECOND_START = dt(11)
DEFAULT_SECOND_END = dt(12)


def state(
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
    *,
    exists: bool = True,
    cancelled: bool = False,
) -> ExternalEventState:
    return ExternalEventState(
        exists=exists,
        cancelled=cancelled,
        start=start if exists and not cancelled else None,
        end=end if exists and not cancelled else None,
        calendar_id="primary",
    )


def aggregate(
    *,
    deadline: datetime | None = DEFAULT_DEADLINE,
    second_start: datetime = DEFAULT_SECOND_START,
    second_end: datetime = DEFAULT_SECOND_END,
) -> ExternalCalendarAggregate:
    return ExternalCalendarAggregate(
        task_id=TASK_ID,
        schedule_plan_id=PLAN_ID,
        changed_session_id=SESSION_1,
        task_deadline=deadline,
        planning_window_start=dt(8),
        planning_window_end=dt(18),
        sessions=(
            ExternalCalendarSession(
                scheduled_session_id=SESSION_1,
                order=1,
                scheduled_start=dt(9),
                scheduled_end=dt(10),
            ),
            ExternalCalendarSession(
                scheduled_session_id=SESSION_2,
                order=2,
                scheduled_start=second_start,
                scheduled_end=second_end,
            ),
        ),
    )


def moved(
    start: datetime,
    end: datetime,
) -> ExternalCalendarChangeInput:
    return ExternalCalendarChangeInput(
        change_type="moved",
        previous=state(),
        current=state(start, end),
    )


def test_no_difference_returns_no_action() -> None:
    unchanged = state()

    assert evaluate_external_calendar_policy(
        aggregate(),
        ExternalCalendarChangeInput(
            change_type="updated",
            previous=unchanged,
            current=unchanged,
        ),
    ) == (NoAction(),)


def test_external_move_updates_session_time_without_extending_deadline() -> None:
    decisions = evaluate_external_calendar_policy(aggregate(), moved(dt(10), dt(11)))

    assert decisions == (
        UpdateScheduledSessionTime(
            scheduled_session_id=SESSION_1,
            previous_start=dt(9),
            previous_end=dt(10),
            new_start=dt(10),
            new_end=dt(11),
        ),
    )


def test_move_beyond_deadline_uses_latest_mapped_session_and_never_shortens() -> None:
    decisions = evaluate_external_calendar_policy(
        aggregate(deadline=dt(12)),
        moved(dt(12), dt(13)),
    )

    assert decisions[:2] == (
        UpdateScheduledSessionTime(
            scheduled_session_id=SESSION_1,
            previous_start=dt(9),
            previous_end=dt(10),
            new_start=dt(12),
            new_end=dt(13),
        ),
        ExtendTaskDeadline(
            task_id=TASK_ID,
            previous_deadline=dt(12),
            new_deadline=dt(13),
        ),
    )
    within_deadline = evaluate_external_calendar_policy(
        aggregate(deadline=dt(15)),
        moved(dt(12), dt(13)),
    )
    assert not any(isinstance(item, ExtendTaskDeadline) for item in within_deadline)


def test_latest_other_externally_positioned_session_controls_deadline() -> None:
    value = aggregate(deadline=dt(12))
    value = ExternalCalendarAggregate(
        task_id=value.task_id,
        schedule_plan_id=value.schedule_plan_id,
        changed_session_id=value.changed_session_id,
        task_deadline=value.task_deadline,
        planning_window_start=value.planning_window_start,
        planning_window_end=dt(20),
        sessions=(
            value.sessions[0],
            ExternalCalendarSession(
                scheduled_session_id=SESSION_2,
                order=2,
                scheduled_start=dt(11),
                scheduled_end=dt(12),
                observed_start=dt(16),
                observed_end=dt(17),
            ),
        ),
    )

    decisions = evaluate_external_calendar_policy(value, moved(dt(10), dt(11)))

    assert (
        ExtendTaskDeadline(
            task_id=TASK_ID,
            previous_deadline=dt(12),
            new_deadline=dt(17),
        )
        in decisions
    )


@pytest.mark.parametrize(
    ("change_type", "current", "reason"),
    [
        ("deleted", state(exists=False), "deleted"),
        ("cancelled", state(cancelled=True), "cancelled"),
    ],
)
def test_deleted_and_cancelled_events_are_marked_missing_without_backlog_actions(
    change_type: str,
    current: ExternalEventState,
    reason: str,
) -> None:
    decisions = evaluate_external_calendar_policy(
        aggregate(),
        ExternalCalendarChangeInput(
            change_type=change_type,
            previous=state(),
            current=current,
        ),
    )

    assert decisions[0] == MarkExternalEventMissing(
        scheduled_session_id=SESSION_1,
        reason=reason,
    )
    assert decisions[1] == RecordConflict(
        code=ConflictCode.external_event_missing,
        severity=ConflictSeverity.error,
        session_ids=(SESSION_1,),
        details=(ConflictDetail("reason", reason),),
    )
    assert all("backlog" not in type(item).__name__.lower() for item in decisions)
    assert all("recreate" not in type(item).__name__.lower() for item in decisions)


def test_unsupported_change_returns_typed_decision_instead_of_throwing() -> None:
    decisions = evaluate_external_calendar_policy(
        aggregate(),
        ExternalCalendarChangeInput(
            change_type="provider_color_changed",
            previous=state(),
            current=ExternalEventState(
                exists=True,
                cancelled=False,
                start=dt(9),
                end=dt(10),
                calendar_id="another-calendar",
            ),
        ),
    )

    assert decisions == (
        UnsupportedExternalChange(
            change_type="provider_color_changed",
            reason="change is not supported by the current policy engine",
        ),
    )


def test_overlap_and_outside_window_are_reported_without_reconciliation() -> None:
    overlap = evaluate_external_calendar_policy(
        aggregate(second_start=dt(11), second_end=dt(12)),
        moved(dt(10, 30), dt(11, 30)),
    )
    outside = evaluate_external_calendar_policy(
        aggregate(),
        moved(dt(7), dt(9)),
    )

    overlap_conflict = next(
        item
        for item in overlap
        if isinstance(item, RecordConflict)
        and item.code is ConflictCode.session_overlap
    )
    assert overlap_conflict.severity is ConflictSeverity.error
    assert overlap_conflict.session_ids == (SESSION_1, SESSION_2)
    assert any(
        isinstance(item, RecordConflict)
        and item.code is ConflictCode.outside_planning_window
        and item.severity is ConflictSeverity.warning
        for item in outside
    )


def test_multiple_decisions_are_stable_and_repeated_execution_is_identical() -> None:
    value = aggregate(deadline=dt(10), second_start=dt(11), second_end=dt(12))
    change = moved(dt(10, 30), dt(11, 30))

    first = evaluate_external_calendar_policy(value, change)
    second = evaluate_external_calendar_policy(value, change)

    assert first == second
    assert [type(item) for item in first] == [
        UpdateScheduledSessionTime,
        ExtendTaskDeadline,
        RecordConflict,
    ]


def test_inputs_and_outputs_are_immutable_and_engine_does_not_mutate_inputs() -> None:
    value = aggregate()
    change = moved(dt(10), dt(11))
    before = (value, change)

    decisions = evaluate_external_calendar_policy(value, change)

    assert (value, change) == before
    with pytest.raises(FrozenInstanceError):
        value.task_deadline = dt(20)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decisions[0].new_start = dt(12)  # type: ignore[union-attr,misc]


def test_domain_module_has_no_infrastructure_dependencies() -> None:
    source = __import__(
        "app.domain.external_calendar_policy",
        fromlist=["__file__"],
    ).__file__
    assert source is not None
    contents = open(source).read()  # noqa: SIM115

    forbidden = (
        "fastapi",
        "sqlalchemy",
        "repository",
        "calendarprovider",
        "google",
        "http",
        "logging",
    )
    assert not any(value in contents.lower() for value in forbidden)
