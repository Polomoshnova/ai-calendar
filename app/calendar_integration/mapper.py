from datetime import UTC

from app.calendar_integration.errors import CalendarValidationError
from app.calendar_integration.models import CalendarBusyResult
from app.schemas.scheduling import DateTimeInterval


def normalize_calendar_busy_intervals(
    result: CalendarBusyResult,
) -> list[DateTimeInterval]:
    """Return sorted, merged scheduler intervals without mutating provider data."""
    intervals: list[DateTimeInterval] = []
    for item in result.intervals:
        start = item.start
        end = item.end
        if start.astimezone(UTC) > end.astimezone(UTC):
            raise CalendarValidationError(
                f"Invalid busy interval for calendar {item.calendar_id}"
            )
        if start.astimezone(UTC) == end.astimezone(UTC):
            continue
        intervals.append(DateTimeInterval(start=start, end=end))
    intervals.sort(
        key=lambda item: (
            item.start.astimezone(UTC),
            item.end.astimezone(UTC),
        )
    )
    merged: list[DateTimeInterval] = []
    for current in intervals:
        if not merged:
            merged.append(current)
            continue
        previous = merged[-1]
        if current.start.astimezone(UTC) <= previous.end.astimezone(UTC):
            merged[-1] = DateTimeInterval(
                start=previous.start,
                end=max(previous.end, current.end),
            )
        else:
            merged.append(current)
    return merged
