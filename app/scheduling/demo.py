from datetime import UTC, datetime

from app.availability import TimeInterval, calculate_free_intervals
from app.domain.preferences import default_working_hours
from app.domain.tasks import PreferredTimeOfDay, TaskPriority
from app.scheduling import AcceptedBlock, SchedulerPreferences, SchedulingTask
from app.scheduling.scheduler import schedule_tasks


def utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 20, hour, minute, tzinfo=UTC)


def main() -> None:
    planning_window = TimeInterval(utc(8), utc(18))
    busy = (
        TimeInterval(utc(12), utc(13)),
        TimeInterval(utc(15), utc(15, 30)),
    )
    free = calculate_free_intervals(
        planning_window,
        default_working_hours(),
        busy,
        "UTC",
    )
    tasks = (
        SchedulingTask(
            id="quarterly-report",
            duration_minutes=120,
            priority=TaskPriority.urgent,
            deadline=utc(16),
            preferred_time_of_day=PreferredTimeOfDay.morning,
        ),
        SchedulingTask(
            id="roadmap-review",
            duration_minutes=90,
            priority=TaskPriority.high,
            is_splittable=True,
            minimum_session_minutes=30,
            maximum_sessions_per_day=2,
        ),
        SchedulingTask(
            id="impossible-workshop",
            duration_minutes=300,
            priority=TaskPriority.low,
            deadline=utc(17),
        ),
        SchedulingTask(
            id="email-review",
            duration_minutes=30,
            priority=TaskPriority.medium,
        ),
    )
    accepted = (AcceptedBlock("email-review", utc(9), utc(9, 30)),)
    preferences = SchedulerPreferences(
        timezone="UTC",
        preferred_task_time=PreferredTimeOfDay.afternoon,
        minimum_break_minutes=15,
        no_deep_work_after=None,
        default_minimum_session_minutes=30,
    )
    result = schedule_tasks(tasks, free, preferences, accepted)

    print("FREE INTERVALS")
    for interval in free:
        print(f"  {interval.start.isoformat()} -> {interval.end.isoformat()}")

    print("\nSCHEDULED BLOCKS")
    for block in result.scheduled_blocks:
        reasons = ", ".join(reason.value for reason in block.reason_codes)
        scores = ", ".join(
            f"{component.name}={component.value}"
            for component in block.score_components
        )
        print(
            f"  {block.task_id}: {block.start.isoformat()} -> {block.end.isoformat()}"
        )
        print(f"    reasons: {reasons}")
        print(f"    scores: {scores}")

    print("\nUNSCHEDULED TASKS")
    for item in result.unscheduled_tasks:
        print(
            f"  {item.task_id}: remaining={item.remaining_minutes}, "
            f"reason={item.reason_code.value}"
        )

    print("\nWARNINGS")
    if not result.warnings:
        print("  none")
    for warning in result.warnings:
        print(f"  {warning.code.value}: {', '.join(warning.task_ids)}")

    print(f"\nSCHEDULER VERSION: {result.scheduler_version}")


if __name__ == "__main__":
    main()
