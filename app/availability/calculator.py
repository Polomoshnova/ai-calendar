from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.availability.types import TimeInterval
from app.domain.preferences import WEEKDAYS, Weekday, WorkingHours
from app.domain.timezones import validate_timezone


def calculate_free_intervals(
    planning_window: TimeInterval,
    working_hours: WorkingHours,
    busy_intervals: Iterable[TimeInterval],
    timezone: str,
) -> tuple[TimeInterval, ...]:
    validate_timezone(timezone)
    _validate_working_hours(working_hours)
    zone = ZoneInfo(timezone)
    planning = planning_window.as_utc()
    working = _working_intervals(planning, working_hours, zone)
    busy = _normalize_busy_intervals(busy_intervals, planning)
    return tuple(_subtract_busy(working, busy))


def _validate_working_hours(working_hours: WorkingHours) -> None:
    if set(working_hours) != set(WEEKDAYS):
        raise ValueError("working_hours must define all seven weekdays")


def _working_intervals(
    planning: TimeInterval,
    working_hours: WorkingHours,
    zone: ZoneInfo,
) -> list[TimeInterval]:
    first_date = planning.start.astimezone(zone).date()
    last_date = planning.end.astimezone(zone).date()
    intervals: list[TimeInterval] = []

    current_date = first_date
    while current_date <= last_date:
        weekday = Weekday(current_date.strftime("%A").lower())
        for window in working_hours[weekday]:
            local_start = _resolve_local(current_date, window.start, zone, is_end=False)
            local_end = _resolve_local(current_date, window.end, zone, is_end=True)
            clipped_start = max(local_start.astimezone(UTC), planning.start)
            clipped_end = min(local_end.astimezone(UTC), planning.end)
            if clipped_start < clipped_end:
                intervals.append(TimeInterval(clipped_start, clipped_end))
        current_date += timedelta(days=1)

    return _merge_intervals(intervals)


def _resolve_local(
    local_date: date,
    wall_time: time,
    zone: ZoneInfo,
    *,
    is_end: bool,
) -> datetime:
    naive = datetime.combine(local_date, wall_time)
    for minute_offset in range(181):
        candidate_naive = naive + timedelta(minutes=minute_offset)
        candidates = _valid_local_candidates(candidate_naive, zone)
        if candidates:
            return candidates[-1] if is_end else candidates[0]
    raise ValueError(f"unable to resolve local time {naive.isoformat()} in {zone.key}")


def _valid_local_candidates(naive: datetime, zone: ZoneInfo) -> list[datetime]:
    by_instant: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        instant = candidate.astimezone(UTC)
        round_trip = instant.astimezone(zone).replace(tzinfo=None)
        if round_trip == naive:
            by_instant[instant] = candidate
    return [by_instant[key] for key in sorted(by_instant)]


def _normalize_busy_intervals(
    busy_intervals: Iterable[TimeInterval], planning: TimeInterval
) -> list[TimeInterval]:
    clipped: list[TimeInterval] = []
    for interval in busy_intervals:
        busy = interval.as_utc()
        start = max(busy.start, planning.start)
        end = min(busy.end, planning.end)
        if start < end:
            clipped.append(TimeInterval(start, end))
    return _merge_intervals(clipped)


def _merge_intervals(intervals: Iterable[TimeInterval]) -> list[TimeInterval]:
    ordered = sorted(interval.as_utc() for interval in intervals)
    merged: list[TimeInterval] = []
    for interval in ordered:
        if not merged or merged[-1].end < interval.start:
            merged.append(interval)
            continue
        previous = merged[-1]
        merged[-1] = TimeInterval(previous.start, max(previous.end, interval.end))
    return merged


def _subtract_busy(
    working_intervals: Iterable[TimeInterval], busy_intervals: list[TimeInterval]
) -> list[TimeInterval]:
    free: list[TimeInterval] = []
    for working in working_intervals:
        cursor = working.start
        for busy in busy_intervals:
            if busy.end <= cursor:
                continue
            if busy.start >= working.end:
                break
            if busy.start > cursor:
                free.append(TimeInterval(cursor, min(busy.start, working.end)))
            cursor = max(cursor, busy.end)
            if cursor >= working.end:
                break
        if cursor < working.end:
            free.append(TimeInterval(cursor, working.end))
    return free
