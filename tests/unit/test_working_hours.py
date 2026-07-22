from datetime import time

import pytest

from app.domain.preferences import (
    Weekday,
    default_working_hours_json,
    parse_working_hours,
    serialize_working_hours,
)


def test_default_working_hours_have_stable_seven_day_schema() -> None:
    raw = default_working_hours_json()

    assert list(raw) == [weekday.value for weekday in Weekday]
    assert raw["monday"] == [{"start": "09:00", "end": "18:00"}]
    assert raw["saturday"] == []
    assert serialize_working_hours(parse_working_hours(raw)) == raw


def test_working_hours_require_every_weekday() -> None:
    raw = default_working_hours_json()
    del raw["sunday"]

    with pytest.raises(ValueError, match="exactly seven weekdays"):
        parse_working_hours(raw)


def test_working_hours_reject_overlap() -> None:
    raw = default_working_hours_json()
    raw["monday"] = [
        {"start": "09:00", "end": "12:00"},
        {"start": "11:00", "end": "13:00"},
    ]

    with pytest.raises(ValueError, match="overlapping"):
        parse_working_hours(raw)


def test_parsed_working_hours_use_local_wall_clock_times() -> None:
    parsed = parse_working_hours(default_working_hours_json())

    assert parsed[Weekday.monday][0].start == time(9)
    assert parsed[Weekday.monday][0].start.tzinfo is None
