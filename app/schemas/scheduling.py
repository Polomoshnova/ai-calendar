import uuid
from datetime import UTC, datetime, timedelta
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.availability import TimeInterval
from app.scheduling.types import (
    ScheduledReasonCode,
    SchedulerResult,
    UnscheduledReasonCode,
    WarningCode,
)

MAX_PLANNING_HORIZON = timedelta(days=31)


class DateTimeInterval(BaseModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        _require_aware(self.start)
        _require_aware(self.end)
        if self.start.astimezone(UTC) >= self.end.astimezone(UTC):
            raise ValueError("interval start must be before end")
        return self

    def to_domain(self) -> TimeInterval:
        return TimeInterval(self.start, self.end)


class SchedulePreviewRequest(BaseModel):
    user_id: uuid.UUID
    planning_window: DateTimeInterval
    busy_intervals: list[DateTimeInterval] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_horizon(self) -> Self:
        duration = self.planning_window.end.astimezone(
            UTC
        ) - self.planning_window.start.astimezone(UTC)
        if duration > MAX_PLANNING_HORIZON:
            raise ValueError("planning horizon must not exceed 31 days")
        return self


class DateTimeIntervalResponse(BaseModel):
    start: datetime
    end: datetime


class ScoreComponentResponse(BaseModel):
    name: str
    value: int


class ScheduledBlockResponse(BaseModel):
    task_id: str
    start: datetime
    end: datetime
    reason_codes: list[ScheduledReasonCode]
    score_components: list[ScoreComponentResponse]


class UnscheduledTaskResponse(BaseModel):
    task_id: str
    remaining_minutes: int
    reason_code: UnscheduledReasonCode


class SchedulerWarningResponse(BaseModel):
    code: WarningCode
    task_ids: list[str]


class SchedulePreviewResponse(BaseModel):
    scheduler_version: str
    planning_window: DateTimeIntervalResponse
    free_intervals: list[DateTimeIntervalResponse]
    scheduled_blocks: list[ScheduledBlockResponse]
    unscheduled_tasks: list[UnscheduledTaskResponse]
    warnings: list[SchedulerWarningResponse]

    @classmethod
    def from_domain(
        cls,
        planning_window: TimeInterval,
        free_intervals: tuple[TimeInterval, ...],
        result: SchedulerResult,
    ) -> "SchedulePreviewResponse":
        planning = planning_window.as_utc()
        return cls(
            scheduler_version=result.scheduler_version,
            planning_window=DateTimeIntervalResponse(
                start=planning.start, end=planning.end
            ),
            free_intervals=[
                DateTimeIntervalResponse(start=item.start, end=item.end)
                for item in free_intervals
            ],
            scheduled_blocks=[
                ScheduledBlockResponse(
                    task_id=block.task_id,
                    start=block.start,
                    end=block.end,
                    reason_codes=list(block.reason_codes),
                    score_components=[
                        ScoreComponentResponse(name=score.name, value=score.value)
                        for score in block.score_components
                    ],
                )
                for block in result.scheduled_blocks
            ],
            unscheduled_tasks=[
                UnscheduledTaskResponse(
                    task_id=item.task_id,
                    remaining_minutes=item.remaining_minutes,
                    reason_code=item.reason_code,
                )
                for item in result.unscheduled_tasks
            ],
            warnings=[
                SchedulerWarningResponse(
                    code=warning.code, task_ids=list(warning.task_ids)
                )
                for warning in result.warnings
            ],
        )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetimes must be timezone-aware")
