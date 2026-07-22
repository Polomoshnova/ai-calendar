from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from typing import Any

from app.domain.tasks import PreferredTimeOfDay


class Weekday(StrEnum):
    monday = "monday"
    tuesday = "tuesday"
    wednesday = "wednesday"
    thursday = "thursday"
    friday = "friday"
    saturday = "saturday"
    sunday = "sunday"


WEEKDAYS = tuple(Weekday)


@dataclass(frozen=True, order=True)
class WallClockWindow:
    start: time
    end: time

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError("working-hours start must be before end")


WorkingHours = dict[Weekday, tuple[WallClockWindow, ...]]


@dataclass(frozen=True)
class SchedulingPreferences:
    timezone: str
    working_hours: WorkingHours
    preferred_task_time: PreferredTimeOfDay
    minimum_break_minutes: int
    no_deep_work_after: time | None
    default_minimum_session_minutes: int


def default_working_hours() -> WorkingHours:
    weekday_window = (WallClockWindow(start=time(9), end=time(18)),)
    return {
        weekday: weekday_window if weekday.value not in {"saturday", "sunday"} else ()
        for weekday in WEEKDAYS
    }


def serialize_working_hours(
    working_hours: WorkingHours,
) -> dict[str, list[dict[str, str]]]:
    return {
        weekday.value: [
            {
                "start": window.start.isoformat(timespec="minutes"),
                "end": window.end.isoformat(timespec="minutes"),
            }
            for window in working_hours[weekday]
        ]
        for weekday in WEEKDAYS
    }


def default_working_hours_json() -> dict[str, list[dict[str, str]]]:
    return serialize_working_hours(default_working_hours())


def parse_working_hours(value: Mapping[str, Any]) -> WorkingHours:
    expected_keys = {weekday.value for weekday in WEEKDAYS}
    actual_keys = set(value)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ValueError(
            f"working_hours must contain exactly seven weekdays; "
            f"missing={missing}, extra={extra}"
        )

    parsed: WorkingHours = {}
    for weekday in WEEKDAYS:
        raw_windows = value[weekday.value]
        if not isinstance(raw_windows, Sequence) or isinstance(raw_windows, str):
            raise ValueError(f"working_hours.{weekday.value} must be a list")

        windows = tuple(_parse_window(weekday, raw) for raw in raw_windows)
        ordered = tuple(sorted(windows))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if previous.end > current.start:
                raise ValueError(
                    f"working_hours.{weekday.value} contains overlapping windows"
                )
        parsed[weekday] = ordered

    return parsed


def _parse_window(weekday: Weekday, value: object) -> WallClockWindow:
    if not isinstance(value, Mapping) or set(value) != {"start", "end"}:
        raise ValueError(f"working_hours.{weekday.value} entries require start and end")
    try:
        start = time.fromisoformat(str(value["start"]))
        end = time.fromisoformat(str(value["end"]))
    except ValueError as exc:
        raise ValueError(f"working_hours.{weekday.value} times must use HH:MM") from exc
    if start.tzinfo is not None or end.tzinfo is not None:
        raise ValueError("working-hours times must be local wall-clock times")
    if start.second or start.microsecond or end.second or end.microsecond:
        raise ValueError("working-hours times must have minute precision")
    return WallClockWindow(start=start, end=end)
