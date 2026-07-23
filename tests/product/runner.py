import json
from collections.abc import Mapping
from datetime import datetime, time
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from app.availability import TimeInterval, calculate_free_intervals
from app.domain.preferences import parse_working_hours
from app.domain.tasks import PreferredTimeOfDay, TaskPriority
from app.scheduling import SchedulerPreferences, SchedulingTask, schedule_tasks

EXAMPLES_DIRECTORY = Path(__file__).parent / "examples"


def main() -> int:
    scenario_paths = sorted(EXAMPLES_DIRECTORY.glob("*.json"))
    if not scenario_paths:
        print(f"No scenarios found in {EXAMPLES_DIRECTORY}")
        return 1

    failures = 0
    print(f"Product scheduling review: {len(scenario_paths)} scenarios\n")
    for scenario_path in scenario_paths:
        try:
            run_scenario(scenario_path)
        except Exception as exc:  # noqa: BLE001 - runner must continue by design
            failures += 1
            print(f"Scenario file: {scenario_path.name}")
            print(f"ERROR: {type(exc).__name__}: {exc}")
            print("-" * 72)

    print("SUMMARY")
    print(f"  Scenarios discovered: {len(scenario_paths)}")
    print(f"  Scenarios rendered:   {len(scenario_paths) - failures}")
    print(f"  Execution failures:   {failures}")
    print("  Quality verdicts:     human review required")
    return 1 if failures else 0


def run_scenario(path: Path) -> None:
    scenario = _object(json.loads(path.read_text()))
    name = _string(scenario, "name")
    description = _string(scenario, "description")
    planning = _interval(_object(scenario["planning_window"]))
    preferences_data = _object(scenario["user_preferences"])
    timezone = _string(preferences_data, "timezone")
    working_hours = parse_working_hours(_object(preferences_data["working_hours"]))
    preferences = SchedulerPreferences(
        timezone=timezone,
        preferred_task_time=PreferredTimeOfDay(
            preferences_data.get("preferred_task_time", "any")
        ),
        minimum_break_minutes=int(preferences_data.get("minimum_break_minutes", 0)),
        no_deep_work_after=_optional_time(preferences_data.get("no_deep_work_after")),
        default_minimum_session_minutes=int(
            preferences_data.get("default_minimum_session_minutes", 15)
        ),
    )
    busy = tuple(
        _interval(_object(item)) for item in _array(scenario, "busy_intervals")
    )
    tasks_data = [_object(item) for item in _array(scenario, "tasks")]
    tasks = tuple(_task(item) for item in tasks_data)
    titles = {_string(item, "id"): _string(item, "title") for item in tasks_data}

    free = calculate_free_intervals(planning, working_hours, busy, timezone)
    result = schedule_tasks(tasks, free, preferences)
    zone = ZoneInfo(timezone)

    print(f"Scenario: {name}")
    print(f"Description: {description}")
    print("Result:")
    print(f"  ✓ Generated {len(result.scheduled_blocks)} blocks")
    print(f"  ✓ {len(result.unscheduled_tasks)} unscheduled tasks")
    if result.warnings:
        print(f"  ⚠ {len(result.warnings)} warnings")
    else:
        print("  ✓ No warnings")

    print("\nGenerated schedule:")
    if not result.scheduled_blocks:
        print("  (none)")
    for block in result.scheduled_blocks:
        start = block.start.astimezone(zone)
        end = block.end.astimezone(zone)
        title = titles.get(block.task_id, block.task_id)
        print(f"  {start:%a %H:%M}–{end:%H:%M}  {title}")
        print(
            "    reason codes: " + ", ".join(code.value for code in block.reason_codes)
        )

    print("\nUnscheduled tasks:")
    if not result.unscheduled_tasks:
        print("  (none)")
    for item in result.unscheduled_tasks:
        title = titles.get(item.task_id, item.task_id)
        print(
            f"  {title}: {item.remaining_minutes} minutes remaining "
            f"[{item.reason_code.value}]"
        )

    print("\nWarnings:")
    if not result.warnings:
        print("  (none)")
    for warning in result.warnings:
        print(f"  {warning.code.value}: {', '.join(warning.task_ids)}")

    print("\nExpected observations:")
    for observation in _array(scenario, "expected_observations"):
        print(f"  - {observation}")
    print("-" * 72)


def _task(data: Mapping[str, Any]) -> SchedulingTask:
    return SchedulingTask(
        id=_string(data, "id"),
        duration_minutes=int(data["duration_minutes"]),
        priority=TaskPriority(data.get("priority", "medium")),
        earliest_start=_optional_datetime(data.get("earliest_start")),
        deadline=_optional_datetime(data.get("deadline")),
        preferred_time_of_day=PreferredTimeOfDay(
            data.get("preferred_time_of_day", "any")
        ),
        is_splittable=bool(data.get("is_splittable", False)),
        minimum_session_minutes=int(data.get("minimum_session_minutes", 15)),
        maximum_sessions_per_day=int(data.get("maximum_sessions_per_day", 1)),
    )


def _interval(data: Mapping[str, Any]) -> TimeInterval:
    return TimeInterval(_datetime(data["start"]), _datetime(data["end"]))


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("datetime values must be ISO-8601 strings")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"datetime must be timezone-aware: {value}")
    return parsed


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)


def _optional_time(value: object) -> time | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("time values must use HH:MM strings")
    return time.fromisoformat(value)


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("scenario fields must contain JSON objects")
    return cast(dict[str, Any], value)


def _array(data: Mapping[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a JSON array")
    return value


def _string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
