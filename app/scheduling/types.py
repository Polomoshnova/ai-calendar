import json
from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from typing import Any

from app.domain.tasks import PreferredTimeOfDay, TaskPriority

SCHEDULER_VERSION = "2a.1"


class ScheduledReasonCode(StrEnum):
    before_deadline = "before_deadline"
    preferred_time_of_day = "preferred_time_of_day"
    higher_priority_first = "higher_priority_first"
    only_available_slot = "only_available_slot"
    avoided_fragmentation = "avoided_fragmentation"
    preserved_existing_block = "preserved_existing_block"


class UnscheduledReasonCode(StrEnum):
    insufficient_free_time = "insufficient_free_time"
    no_time_before_deadline = "no_time_before_deadline"
    task_not_splittable = "task_not_splittable"
    minimum_session_too_large = "minimum_session_too_large"
    maximum_sessions_exceeded = "maximum_sessions_exceeded"
    working_hours_too_restrictive = "working_hours_too_restrictive"
    conflicting_constraints = "conflicting_constraints"


class WarningCode(StrEnum):
    accepted_blocks_overlap = "accepted_blocks_overlap"
    accepted_block_conflicts_hard_constraint = (
        "accepted_block_conflicts_hard_constraint"
    )
    accepted_block_outside_free_time = "accepted_block_outside_free_time"
    accepted_block_unknown_task = "accepted_block_unknown_task"


@dataclass(frozen=True)
class SchedulingTask:
    id: str
    duration_minutes: int
    priority: TaskPriority = TaskPriority.medium
    earliest_start: datetime | None = None
    deadline: datetime | None = None
    preferred_time_of_day: PreferredTimeOfDay = PreferredTimeOfDay.any
    is_splittable: bool = False
    minimum_session_minutes: int = 15
    maximum_sessions_per_day: int = 1


@dataclass(frozen=True)
class SchedulerPreferences:
    timezone: str
    preferred_task_time: PreferredTimeOfDay = PreferredTimeOfDay.any
    minimum_break_minutes: int = 0
    no_deep_work_after: time | None = None
    default_minimum_session_minutes: int = 15


@dataclass(frozen=True, order=True)
class AcceptedBlock:
    task_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    value: int


@dataclass(frozen=True)
class ScheduledBlock:
    task_id: str
    start: datetime
    end: datetime
    reason_codes: tuple[ScheduledReasonCode, ...]
    score_components: tuple[ScoreComponent, ...]


@dataclass(frozen=True)
class UnscheduledTask:
    task_id: str
    remaining_minutes: int
    reason_code: UnscheduledReasonCode


@dataclass(frozen=True)
class SchedulerWarning:
    code: WarningCode
    task_ids: tuple[str, ...]


@dataclass(frozen=True)
class SchedulerResult:
    scheduled_blocks: tuple[ScheduledBlock, ...]
    unscheduled_tasks: tuple[UnscheduledTask, ...]
    warnings: tuple[SchedulerWarning, ...]
    scheduler_version: str = SCHEDULER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheduled_blocks": [
                {
                    "task_id": block.task_id,
                    "start": block.start.isoformat(),
                    "end": block.end.isoformat(),
                    "reason_codes": [code.value for code in block.reason_codes],
                    "score_components": [
                        {"name": component.name, "value": component.value}
                        for component in block.score_components
                    ],
                }
                for block in self.scheduled_blocks
            ],
            "unscheduled_tasks": [
                {
                    "task_id": task.task_id,
                    "remaining_minutes": task.remaining_minutes,
                    "reason_code": task.reason_code.value,
                }
                for task in self.unscheduled_tasks
            ],
            "warnings": [
                {"code": warning.code.value, "task_ids": list(warning.task_ids)}
                for warning in self.warnings
            ],
            "scheduler_version": self.scheduler_version,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
