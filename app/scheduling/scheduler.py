from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.availability import TimeInterval
from app.domain.tasks import PreferredTimeOfDay, TaskPriority
from app.domain.timezones import validate_timezone
from app.scheduling.types import (
    AcceptedBlock,
    ScheduledBlock,
    ScheduledReasonCode,
    SchedulerPreferences,
    SchedulerResult,
    SchedulerWarning,
    SchedulingTask,
    ScoreComponent,
    UnscheduledReasonCode,
    UnscheduledTask,
    WarningCode,
)

_PRIORITY_ORDER = {
    TaskPriority.urgent: 0,
    TaskPriority.high: 1,
    TaskPriority.medium: 2,
    TaskPriority.low: 3,
}


@dataclass(frozen=True)
class _Candidate:
    interval: TimeInterval
    block: TimeInterval
    preferred_match: int
    deadline_buffer_minutes: int
    fragmentation_penalty: int

    @property
    def score(self) -> int:
        return (
            self.preferred_match * 1_000_000
            + self.block.duration_minutes * 100
            + min(self.deadline_buffer_minutes, 10_080)
            - self.fragmentation_penalty
        )


def schedule_tasks(
    tasks: Iterable[SchedulingTask],
    free_intervals: Iterable[TimeInterval],
    preferences: SchedulerPreferences,
    accepted_blocks: Iterable[AcceptedBlock] = (),
) -> SchedulerResult:
    validate_timezone(preferences.timezone)
    if preferences.minimum_break_minutes < 0:
        raise ValueError("minimum_break_minutes cannot be negative")
    if preferences.default_minimum_session_minutes <= 0:
        raise ValueError("default_minimum_session_minutes must be positive")

    zone = ZoneInfo(preferences.timezone)
    ordered_free = tuple(sorted(interval.as_utc() for interval in free_intervals))
    task_list = tuple(tasks)
    task_by_id = {task.id: task for task in task_list}
    accepted = tuple(sorted(_accepted_as_utc(block) for block in accepted_blocks))

    scheduled: list[ScheduledBlock] = []
    unscheduled: list[UnscheduledTask] = []
    warnings = _validate_accepted_blocks(
        accepted,
        task_by_id,
        ordered_free,
        minimum_break_minutes=preferences.minimum_break_minutes,
    )
    managed: list[TimeInterval] = []
    assigned_minutes: Counter[str] = Counter()
    sessions_by_task_day: Counter[tuple[str, date]] = Counter()

    for block in accepted:
        interval = TimeInterval(block.start, block.end)
        managed.append(interval)
        assigned_minutes[block.task_id] += interval.duration_minutes
        local_day = interval.start.astimezone(zone).date()
        sessions_by_task_day[(block.task_id, local_day)] += 1
        scheduled.append(
            ScheduledBlock(
                task_id=block.task_id,
                start=interval.start,
                end=interval.end,
                reason_codes=(ScheduledReasonCode.preserved_existing_block,),
                score_components=(ScoreComponent("preserved_existing_block", 1),),
            )
        )

    valid_tasks: list[SchedulingTask] = []
    for task in task_list:
        if _task_validation_error(task):
            unscheduled.append(
                UnscheduledTask(
                    task_id=task.id,
                    remaining_minutes=max(task.duration_minutes, 0),
                    reason_code=UnscheduledReasonCode.conflicting_constraints,
                )
            )
        else:
            valid_tasks.append(task)

    ordered_tasks = sorted(valid_tasks, key=_task_sort_key)
    multiple_tasks = len(ordered_tasks) > 1
    for task in ordered_tasks:
        remaining = max(task.duration_minutes - assigned_minutes[task.id], 0)
        if remaining == 0:
            continue

        task_blocks, remaining, failure = _place_task(
            task,
            remaining,
            ordered_free,
            managed,
            sessions_by_task_day,
            preferences,
            zone,
            multiple_tasks=multiple_tasks,
        )
        scheduled.extend(task_blocks)

        if remaining:
            unscheduled.append(
                UnscheduledTask(
                    task_id=task.id,
                    remaining_minutes=remaining,
                    reason_code=failure,
                )
            )

    return SchedulerResult(
        scheduled_blocks=tuple(sorted(scheduled, key=_scheduled_sort_key)),
        unscheduled_tasks=tuple(sorted(unscheduled, key=lambda item: item.task_id)),
        warnings=tuple(sorted(warnings, key=_warning_sort_key)),
    )


def _place_task(
    task: SchedulingTask,
    remaining: int,
    free_intervals: tuple[TimeInterval, ...],
    managed: list[TimeInterval],
    sessions_by_task_day: Counter[tuple[str, date]],
    preferences: SchedulerPreferences,
    zone: ZoneInfo,
    *,
    multiple_tasks: bool,
) -> tuple[list[ScheduledBlock], int, UnscheduledReasonCode]:
    placed: list[ScheduledBlock] = []
    effective_minimum = max(
        task.minimum_session_minutes, preferences.default_minimum_session_minutes
    )

    while remaining:
        available = _available_for_task(
            task, free_intervals, managed, preferences, zone
        )
        candidates = _build_candidates(
            task,
            remaining,
            available,
            sessions_by_task_day,
            effective_minimum,
            preferences,
            zone,
        )
        if not candidates:
            return (
                placed,
                remaining,
                _failure_reason(
                    task,
                    remaining,
                    available,
                    free_intervals,
                    sessions_by_task_day,
                    effective_minimum,
                    zone,
                ),
            )

        selected = min(
            candidates,
            key=lambda item: (-item.score, item.block.start, item.block.end),
        )
        reasons = _scheduled_reasons(
            task,
            selected,
            candidate_count=len(candidates),
            multiple_tasks=multiple_tasks,
            effective_minimum=effective_minimum,
        )
        placed_block = ScheduledBlock(
            task_id=task.id,
            start=selected.block.start,
            end=selected.block.end,
            reason_codes=reasons,
            score_components=(
                ScoreComponent("preferred_time_of_day", selected.preferred_match),
                ScoreComponent(
                    "deadline_buffer_minutes", selected.deadline_buffer_minutes
                ),
                ScoreComponent("contiguous_minutes", selected.block.duration_minutes),
                ScoreComponent("fragmentation_penalty", selected.fragmentation_penalty),
            ),
        )
        placed.append(placed_block)
        managed.append(selected.block)
        local_day = selected.block.start.astimezone(zone).date()
        sessions_by_task_day[(task.id, local_day)] += 1
        remaining -= selected.block.duration_minutes

        if not task.is_splittable:
            break

    return placed, remaining, UnscheduledReasonCode.insufficient_free_time


def _available_for_task(
    task: SchedulingTask,
    free_intervals: tuple[TimeInterval, ...],
    managed: list[TimeInterval],
    preferences: SchedulerPreferences,
    zone: ZoneInfo,
) -> list[TimeInterval]:
    constrained: list[TimeInterval] = []
    earliest = task.earliest_start.astimezone(UTC) if task.earliest_start else None
    deadline = task.deadline.astimezone(UTC) if task.deadline else None
    for interval in free_intervals:
        start = max(interval.start, earliest) if earliest else interval.start
        end = min(interval.end, deadline) if deadline else interval.end
        if start >= end:
            continue
        clipped = _apply_cutoff(TimeInterval(start, end), preferences, zone)
        if clipped is not None:
            constrained.append(clipped)

    break_delta = timedelta(minutes=preferences.minimum_break_minutes)
    reservations = [
        TimeInterval(block.start - break_delta, block.end + break_delta)
        for block in managed
    ]
    return _subtract_intervals(constrained, reservations)


def _apply_cutoff(
    interval: TimeInterval, preferences: SchedulerPreferences, zone: ZoneInfo
) -> TimeInterval | None:
    cutoff = preferences.no_deep_work_after
    if cutoff is None:
        return interval
    local_start = interval.start.astimezone(zone)
    local_cutoff = datetime.combine(local_start.date(), cutoff, tzinfo=zone).astimezone(
        UTC
    )
    end = min(interval.end, local_cutoff)
    return TimeInterval(interval.start, end) if interval.start < end else None


def _build_candidates(
    task: SchedulingTask,
    remaining: int,
    intervals: list[TimeInterval],
    sessions_by_task_day: Counter[tuple[str, date]],
    minimum_session: int,
    preferences: SchedulerPreferences,
    zone: ZoneInfo,
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    preferred = (
        task.preferred_time_of_day
        if task.preferred_time_of_day is not PreferredTimeOfDay.any
        else preferences.preferred_task_time
    )
    for interval in intervals:
        local_day = interval.start.astimezone(zone).date()
        if sessions_by_task_day[(task.id, local_day)] >= task.maximum_sessions_per_day:
            continue

        if task.is_splittable:
            session_minutes = _splittable_session_minutes(
                remaining, interval.duration_minutes, minimum_session
            )
        else:
            session_minutes = remaining if interval.duration_minutes >= remaining else 0
        if session_minutes <= 0:
            continue

        start, preferred_match = _preferred_start(
            interval, session_minutes, preferred, zone
        )
        block = TimeInterval(start, start + timedelta(minutes=session_minutes))
        leftover = interval.duration_minutes - session_minutes
        fragmentation_penalty = leftover if 0 < leftover < minimum_session else 0
        deadline_buffer = (
            max(
                int((task.deadline.astimezone(UTC) - block.end).total_seconds() // 60),
                0,
            )
            if task.deadline
            else 0
        )
        candidates.append(
            _Candidate(
                interval=interval,
                block=block,
                preferred_match=preferred_match,
                deadline_buffer_minutes=deadline_buffer,
                fragmentation_penalty=fragmentation_penalty,
            )
        )
    return candidates


def _splittable_session_minutes(remaining: int, available: int, minimum: int) -> int:
    if available < minimum or remaining < minimum:
        return 0
    if remaining <= available:
        return remaining
    session = available
    if 0 < remaining - session < minimum:
        session = remaining - minimum
    return session if session >= minimum else 0


def _preferred_start(
    interval: TimeInterval,
    duration_minutes: int,
    preferred: PreferredTimeOfDay,
    zone: ZoneInfo,
) -> tuple[datetime, int]:
    if preferred is PreferredTimeOfDay.any:
        return interval.start, 0

    local_start = interval.start.astimezone(zone)
    boundaries = {
        PreferredTimeOfDay.morning: (time(0), time(12)),
        PreferredTimeOfDay.afternoon: (time(12), time(17)),
        PreferredTimeOfDay.evening: (time(17), time.max),
    }
    preferred_start, preferred_end = boundaries[preferred]
    window_start = datetime.combine(
        local_start.date(), preferred_start, tzinfo=zone
    ).astimezone(UTC)
    window_end = datetime.combine(
        local_start.date(), preferred_end, tzinfo=zone
    ).astimezone(UTC)
    start = max(interval.start, window_start)
    end = start + timedelta(minutes=duration_minutes)
    if end <= min(interval.end, window_end):
        return start, 1
    return interval.start, 0


def _failure_reason(
    task: SchedulingTask,
    remaining: int,
    available: list[TimeInterval],
    all_free: tuple[TimeInterval, ...],
    sessions_by_task_day: Counter[tuple[str, date]],
    minimum_session: int,
    zone: ZoneInfo,
) -> UnscheduledReasonCode:
    if not all_free:
        return UnscheduledReasonCode.working_hours_too_restrictive
    if task.deadline and not available:
        return UnscheduledReasonCode.no_time_before_deadline
    if available and max(item.duration_minutes for item in available) < minimum_session:
        return UnscheduledReasonCode.minimum_session_too_large
    if not task.is_splittable and available:
        total = sum(item.duration_minutes for item in available)
        if total >= remaining:
            return UnscheduledReasonCode.task_not_splittable
    if available and all(
        sessions_by_task_day[(task.id, item.start.astimezone(zone).date())]
        >= task.maximum_sessions_per_day
        for item in available
    ):
        return UnscheduledReasonCode.maximum_sessions_exceeded
    return UnscheduledReasonCode.insufficient_free_time


def _task_validation_error(task: SchedulingTask) -> bool:
    values = (task.earliest_start, task.deadline)
    if any(value is not None and value.tzinfo is None for value in values):
        return True
    return bool(
        not task.id
        or task.duration_minutes <= 0
        or task.minimum_session_minutes <= 0
        or task.maximum_sessions_per_day <= 0
        or (
            task.earliest_start
            and task.deadline
            and task.earliest_start.astimezone(UTC) >= task.deadline.astimezone(UTC)
        )
        or (task.is_splittable and task.minimum_session_minutes > task.duration_minutes)
    )


def _validate_accepted_blocks(
    blocks: tuple[AcceptedBlock, ...],
    task_by_id: dict[str, SchedulingTask],
    free_intervals: tuple[TimeInterval, ...],
    *,
    minimum_break_minutes: int,
) -> list[SchedulerWarning]:
    warnings: list[SchedulerWarning] = []
    accepted_minutes: Counter[str] = Counter()
    for index, block in enumerate(blocks):
        interval = TimeInterval(block.start, block.end)
        accepted_minutes[block.task_id] += interval.duration_minutes
        task = task_by_id.get(block.task_id)
        if task is None:
            warnings.append(
                SchedulerWarning(
                    WarningCode.accepted_block_unknown_task, (block.task_id,)
                )
            )
        elif _task_validation_error(task) or _accepted_violates_task(interval, task):
            warnings.append(
                SchedulerWarning(
                    WarningCode.accepted_block_conflicts_hard_constraint,
                    (block.task_id,),
                )
            )
        if not any(
            free.start <= interval.start and interval.end <= free.end
            for free in free_intervals
        ):
            warnings.append(
                SchedulerWarning(
                    WarningCode.accepted_block_outside_free_time, (block.task_id,)
                )
            )
        for other in blocks[index + 1 :]:
            required_break = timedelta(minutes=minimum_break_minutes)
            overlaps = block.start < other.end and other.start < block.end
            too_close = (
                block.end <= other.start < block.end + required_break
                or other.end <= block.start < other.end + required_break
            )
            if overlaps:
                warnings.append(
                    SchedulerWarning(
                        WarningCode.accepted_blocks_overlap,
                        tuple(sorted((block.task_id, other.task_id))),
                    )
                )
            elif too_close:
                warnings.append(
                    SchedulerWarning(
                        WarningCode.accepted_block_conflicts_hard_constraint,
                        tuple(sorted((block.task_id, other.task_id))),
                    )
                )
    for task_id, minutes in sorted(accepted_minutes.items()):
        task = task_by_id.get(task_id)
        if task is not None and minutes > task.duration_minutes:
            warnings.append(
                SchedulerWarning(
                    WarningCode.accepted_block_conflicts_hard_constraint, (task_id,)
                )
            )
    return warnings


def _accepted_violates_task(interval: TimeInterval, task: SchedulingTask) -> bool:
    return bool(
        (task.earliest_start and interval.start < task.earliest_start.astimezone(UTC))
        or (task.deadline and interval.end > task.deadline.astimezone(UTC))
    )


def _accepted_as_utc(block: AcceptedBlock) -> AcceptedBlock:
    if block.start.tzinfo is None or block.end.tzinfo is None:
        raise ValueError("accepted block datetimes must be timezone-aware")
    if block.start.astimezone(UTC) >= block.end.astimezone(UTC):
        raise ValueError("accepted block start must be before end")
    return AcceptedBlock(
        task_id=block.task_id,
        start=block.start.astimezone(UTC),
        end=block.end.astimezone(UTC),
    )


def _subtract_intervals(
    sources: Iterable[TimeInterval], reservations: Iterable[TimeInterval]
) -> list[TimeInterval]:
    result = list(sources)
    for reservation in sorted(reservations):
        next_result: list[TimeInterval] = []
        for source in result:
            if reservation.end <= source.start or reservation.start >= source.end:
                next_result.append(source)
                continue
            if source.start < reservation.start:
                next_result.append(TimeInterval(source.start, reservation.start))
            if reservation.end < source.end:
                next_result.append(TimeInterval(reservation.end, source.end))
        result = next_result
    return result


def _scheduled_reasons(
    task: SchedulingTask,
    candidate: _Candidate,
    *,
    candidate_count: int,
    multiple_tasks: bool,
    effective_minimum: int,
) -> tuple[ScheduledReasonCode, ...]:
    reasons: list[ScheduledReasonCode] = []
    if task.deadline:
        reasons.append(ScheduledReasonCode.before_deadline)
    if candidate.preferred_match:
        reasons.append(ScheduledReasonCode.preferred_time_of_day)
    if multiple_tasks:
        reasons.append(ScheduledReasonCode.higher_priority_first)
    if candidate_count == 1:
        reasons.append(ScheduledReasonCode.only_available_slot)
    leftover = candidate.interval.duration_minutes - candidate.block.duration_minutes
    if leftover == 0 or leftover >= effective_minimum:
        reasons.append(ScheduledReasonCode.avoided_fragmentation)
    return tuple(reasons)


def _task_sort_key(task: SchedulingTask) -> tuple[int, datetime, str]:
    deadline = (
        task.deadline.astimezone(UTC)
        if task.deadline
        else datetime.max.replace(tzinfo=UTC)
    )
    return _PRIORITY_ORDER[task.priority], deadline, task.id


def _scheduled_sort_key(block: ScheduledBlock) -> tuple[datetime, datetime, str]:
    return block.start, block.end, block.task_id


def _warning_sort_key(warning: SchedulerWarning) -> tuple[str, tuple[str, ...]]:
    return warning.code.value, warning.task_ids
