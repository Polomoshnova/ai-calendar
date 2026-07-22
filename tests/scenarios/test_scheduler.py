from datetime import UTC, datetime, time

from app.availability import TimeInterval
from app.domain.tasks import PreferredTimeOfDay, TaskPriority
from app.scheduling import AcceptedBlock, SchedulerPreferences, SchedulingTask
from app.scheduling.scheduler import schedule_tasks
from app.scheduling.types import (
    ScheduledReasonCode,
    UnscheduledReasonCode,
    WarningCode,
)


def dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


def preferences(**overrides: object) -> SchedulerPreferences:
    values: dict[str, object] = {
        "timezone": "UTC",
        "minimum_break_minutes": 0,
        "default_minimum_session_minutes": 15,
    }
    values.update(overrides)
    return SchedulerPreferences(**values)  # type: ignore[arg-type]


def task(task_id: str, duration: int, **overrides: object) -> SchedulingTask:
    values: dict[str, object] = {"id": task_id, "duration_minutes": duration}
    values.update(overrides)
    return SchedulingTask(**values)  # type: ignore[arg-type]


def test_task_fits_exactly() -> None:
    result = schedule_tasks(
        [task("exact", 60)],
        [TimeInterval(dt(20, 9), dt(20, 10))],
        preferences(),
    )

    assert [(block.start, block.end) for block in result.scheduled_blocks] == [
        (dt(20, 9), dt(20, 10))
    ]
    assert result.unscheduled_tasks == ()


def test_urgent_task_wins_over_low_priority_task() -> None:
    result = schedule_tasks(
        [
            task("low", 60, priority=TaskPriority.low),
            task("urgent", 60, priority=TaskPriority.urgent),
        ],
        [TimeInterval(dt(20, 9), dt(20, 10))],
        preferences(),
    )

    assert [block.task_id for block in result.scheduled_blocks] == ["urgent"]
    assert result.unscheduled_tasks[0].task_id == "low"


def test_earlier_deadline_wins_with_equal_priority() -> None:
    result = schedule_tasks(
        [
            task("later", 60, deadline=dt(21, 18)),
            task("earlier", 60, deadline=dt(20, 12)),
        ],
        [TimeInterval(dt(20, 9), dt(20, 10))],
        preferences(),
    )

    assert [block.task_id for block in result.scheduled_blocks] == ["earlier"]


def test_preferred_morning_interval_is_selected() -> None:
    result = schedule_tasks(
        [
            task(
                "focus",
                60,
                preferred_time_of_day=PreferredTimeOfDay.morning,
            )
        ],
        [
            TimeInterval(dt(20, 14), dt(20, 16)),
            TimeInterval(dt(21, 9), dt(21, 11)),
        ],
        preferences(),
    )

    assert result.scheduled_blocks[0].start == dt(21, 9)
    assert (
        ScheduledReasonCode.preferred_time_of_day
        in result.scheduled_blocks[0].reason_codes
    )


def test_splittable_task_is_divided() -> None:
    result = schedule_tasks(
        [
            task(
                "split",
                120,
                is_splittable=True,
                minimum_session_minutes=30,
                maximum_sessions_per_day=1,
            )
        ],
        [
            TimeInterval(dt(20, 9), dt(20, 10)),
            TimeInterval(dt(21, 9), dt(21, 10)),
        ],
        preferences(),
    )

    assert [block.task_id for block in result.scheduled_blocks] == ["split", "split"]
    assert result.unscheduled_tasks == ()


def test_non_splittable_task_remains_unscheduled() -> None:
    result = schedule_tasks(
        [task("whole", 120)],
        [
            TimeInterval(dt(20, 9), dt(20, 10)),
            TimeInterval(dt(20, 11), dt(20, 12)),
        ],
        preferences(),
    )

    assert result.scheduled_blocks == ()
    assert result.unscheduled_tasks[0].reason_code is (
        UnscheduledReasonCode.task_not_splittable
    )


def test_minimum_session_length_is_respected() -> None:
    result = schedule_tasks(
        [
            task(
                "minimum",
                60,
                is_splittable=True,
                minimum_session_minutes=30,
                maximum_sessions_per_day=2,
            )
        ],
        [TimeInterval(dt(20, 9), dt(20, 9, 20))],
        preferences(),
    )

    assert result.scheduled_blocks == ()
    assert result.unscheduled_tasks[0].reason_code is (
        UnscheduledReasonCode.minimum_session_too_large
    )


def test_maximum_sessions_per_day_is_respected() -> None:
    result = schedule_tasks(
        [
            task(
                "limited",
                60,
                is_splittable=True,
                minimum_session_minutes=30,
                maximum_sessions_per_day=1,
            )
        ],
        [
            TimeInterval(dt(20, 9), dt(20, 9, 30)),
            TimeInterval(dt(20, 10), dt(20, 10, 30)),
        ],
        preferences(),
    )

    assert len(result.scheduled_blocks) == 1
    assert result.unscheduled_tasks[0].reason_code is (
        UnscheduledReasonCode.maximum_sessions_exceeded
    )


def test_deep_work_cutoff_is_respected() -> None:
    result = schedule_tasks(
        [task("cutoff", 60)],
        [TimeInterval(dt(20, 16), dt(20, 18))],
        preferences(no_deep_work_after=time(17)),
    )

    assert result.scheduled_blocks[0].start == dt(20, 16)
    assert result.scheduled_blocks[0].end == dt(20, 17)


def test_accepted_block_is_preserved_and_break_applies_to_new_block() -> None:
    accepted = AcceptedBlock("accepted", dt(20, 10), dt(20, 11))
    result = schedule_tasks(
        [task("accepted", 60), task("new", 30)],
        [TimeInterval(dt(20, 9), dt(20, 12))],
        preferences(minimum_break_minutes=30),
        [accepted],
    )

    accepted_result = next(
        block for block in result.scheduled_blocks if block.task_id == "accepted"
    )
    new_result = next(
        block for block in result.scheduled_blocks if block.task_id == "new"
    )
    assert (accepted_result.start, accepted_result.end) == (dt(20, 10), dt(20, 11))
    assert ScheduledReasonCode.preserved_existing_block in accepted_result.reason_codes
    assert new_result.end <= dt(20, 9, 30) or new_result.start >= dt(20, 11, 30)


def test_conflicting_accepted_blocks_are_preserved_with_warning() -> None:
    result = schedule_tasks(
        [task("one", 60), task("two", 60)],
        [TimeInterval(dt(20, 9), dt(20, 12))],
        preferences(),
        [
            AcceptedBlock("one", dt(20, 9), dt(20, 10)),
            AcceptedBlock("two", dt(20, 9, 30), dt(20, 10, 30)),
        ],
    )

    assert len(result.scheduled_blocks) == 2
    assert any(
        warning.code is WarningCode.accepted_blocks_overlap
        for warning in result.warnings
    )


def test_accepted_block_hard_constraint_conflict_is_warned_not_moved() -> None:
    result = schedule_tasks(
        [task("fixed", 60, earliest_start=dt(20, 10))],
        [TimeInterval(dt(20, 9), dt(20, 12))],
        preferences(),
        [AcceptedBlock("fixed", dt(20, 9), dt(20, 10))],
    )

    assert result.scheduled_blocks[0].start == dt(20, 9)
    assert any(
        warning.code is WarningCode.accepted_block_conflicts_hard_constraint
        for warning in result.warnings
    )


def test_break_violation_between_accepted_blocks_is_warned_not_moved() -> None:
    result = schedule_tasks(
        [task("one", 30), task("two", 30)],
        [TimeInterval(dt(20, 9), dt(20, 12))],
        preferences(minimum_break_minutes=15),
        [
            AcceptedBlock("one", dt(20, 9), dt(20, 9, 30)),
            AcceptedBlock("two", dt(20, 9, 35), dt(20, 10, 5)),
        ],
    )

    assert [(block.start, block.end) for block in result.scheduled_blocks] == [
        (dt(20, 9), dt(20, 9, 30)),
        (dt(20, 9, 35), dt(20, 10, 5)),
    ]
    assert any(
        warning.code is WarningCode.accepted_block_conflicts_hard_constraint
        for warning in result.warnings
    )


def test_identical_input_has_byte_for_byte_equivalent_ordered_output() -> None:
    tasks = [task("b", 30), task("a", 30)]
    free = [TimeInterval(dt(20, 9), dt(20, 11))]

    first = schedule_tasks(tasks, free, preferences()).to_json().encode()
    second = schedule_tasks(tasks, free, preferences()).to_json().encode()

    assert first == second


def test_every_unscheduled_task_has_explicit_reason() -> None:
    result = schedule_tasks(
        [task("one", 60), task("two", 60), task("invalid", -1)],
        [],
        preferences(),
    )

    assert {item.task_id for item in result.unscheduled_tasks} == {
        "one",
        "two",
        "invalid",
    }
    assert all(item.reason_code for item in result.unscheduled_tasks)
