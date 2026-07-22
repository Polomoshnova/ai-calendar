from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True, order=True)
class TimeInterval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("interval datetimes must be timezone-aware")
        if self.start.astimezone(UTC) >= self.end.astimezone(UTC):
            raise ValueError("interval start must be before end")

    @property
    def duration(self) -> timedelta:
        return self.end.astimezone(UTC) - self.start.astimezone(UTC)

    @property
    def duration_minutes(self) -> int:
        return int(self.duration.total_seconds() // 60)

    def as_utc(self) -> "TimeInterval":
        return TimeInterval(self.start.astimezone(UTC), self.end.astimezone(UTC))
