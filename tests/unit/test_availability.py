from datetime import UTC, datetime, time

import pytest

from app.availability import TimeInterval, calculate_free_intervals
from app.domain.preferences import Weekday, WorkingHours, default_working_hours


def dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


def monday_window() -> TimeInterval:
    return TimeInterval(dt(20, 0), dt(21, 0))


def test_free_interval_with_no_busy_time() -> None:
    free = calculate_free_intervals(monday_window(), default_working_hours(), [], "UTC")

    assert free == (TimeInterval(dt(20, 9), dt(20, 18)),)


def test_overlapping_busy_intervals_are_merged() -> None:
    free = calculate_free_intervals(
        monday_window(),
        default_working_hours(),
        [
            TimeInterval(dt(20, 10), dt(20, 12)),
            TimeInterval(dt(20, 11), dt(20, 13)),
        ],
        "UTC",
    )

    assert free == (
        TimeInterval(dt(20, 9), dt(20, 10)),
        TimeInterval(dt(20, 13), dt(20, 18)),
    )


def test_adjacent_busy_intervals_are_merged() -> None:
    free = calculate_free_intervals(
        monday_window(),
        default_working_hours(),
        [
            TimeInterval(dt(20, 10), dt(20, 11)),
            TimeInterval(dt(20, 11), dt(20, 12)),
        ],
        "UTC",
    )

    assert free == (
        TimeInterval(dt(20, 9), dt(20, 10)),
        TimeInterval(dt(20, 12), dt(20, 18)),
    )


def test_busy_intervals_are_clipped_to_planning_window() -> None:
    planning = TimeInterval(dt(20, 10), dt(20, 16))
    free = calculate_free_intervals(
        planning,
        default_working_hours(),
        [
            TimeInterval(dt(20, 8), dt(20, 11)),
            TimeInterval(dt(20, 15), dt(20, 19)),
        ],
        "UTC",
    )

    assert free == (TimeInterval(dt(20, 11), dt(20, 15)),)


def test_unavailable_weekday_returns_no_intervals() -> None:
    hours = default_working_hours()
    hours[Weekday.monday] = ()

    assert calculate_free_intervals(monday_window(), hours, [], "UTC") == ()


def test_explicit_all_day_busy_interval_removes_workday() -> None:
    free = calculate_free_intervals(
        monday_window(),
        default_working_hours(),
        [TimeInterval(dt(20, 0), dt(21, 0))],
        "UTC",
    )

    assert free == ()


def test_dst_boundary_uses_actual_elapsed_time() -> None:
    hours: WorkingHours = {weekday: () for weekday in Weekday}
    hours[Weekday.sunday] = (
        type(default_working_hours()[Weekday.monday][0])(time(1), time(4)),
    )
    planning = TimeInterval(
        datetime(2026, 3, 28, tzinfo=UTC),
        datetime(2026, 3, 30, tzinfo=UTC),
    )

    free = calculate_free_intervals(planning, hours, [], "Europe/Warsaw")

    assert len(free) == 1
    assert free[0].duration_minutes == 120
    assert free[0].start == datetime(2026, 3, 29, 0, tzinfo=UTC)
    assert free[0].end == datetime(2026, 3, 29, 2, tzinfo=UTC)


def test_naive_intervals_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        TimeInterval(datetime(2026, 7, 20, 9), datetime(2026, 7, 20, 10))
