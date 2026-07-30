import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


def _require_aware(name: str, value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must be timezone-aware")


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ExternalEventState:
    exists: bool
    cancelled: bool
    start: datetime | None
    end: datetime | None
    calendar_id: str

    def __post_init__(self) -> None:
        _require_aware("start", self.start)
        _require_aware("end", self.end)
        if self.exists and not self.cancelled:
            if self.start is None or self.end is None:
                raise ValueError("present events require start and end")
            if _utc(self.start) >= _utc(self.end):
                raise ValueError("event start must be before end")


@dataclass(frozen=True, slots=True)
class ExternalCalendarChangeInput:
    change_type: str
    previous: ExternalEventState | None
    current: ExternalEventState | None


@dataclass(frozen=True, slots=True)
class ExternalCalendarSession:
    scheduled_session_id: uuid.UUID
    order: int
    scheduled_start: datetime
    scheduled_end: datetime
    mapped: bool = True
    externally_missing: bool = False
    observed_start: datetime | None = None
    observed_end: datetime | None = None

    def __post_init__(self) -> None:
        _require_aware("scheduled_start", self.scheduled_start)
        _require_aware("scheduled_end", self.scheduled_end)
        _require_aware("observed_start", self.observed_start)
        _require_aware("observed_end", self.observed_end)
        if self.order <= 0:
            raise ValueError("session order must be positive")
        if _utc(self.scheduled_start) >= _utc(self.scheduled_end):
            raise ValueError("session start must be before end")
        if (self.observed_start is None) is not (self.observed_end is None):
            raise ValueError("observed start and end must be provided together")
        if (
            self.observed_start is not None
            and self.observed_end is not None
            and _utc(self.observed_start) >= _utc(self.observed_end)
        ):
            raise ValueError("observed session start must be before end")

    @property
    def effective_start(self) -> datetime:
        return self.observed_start or self.scheduled_start

    @property
    def effective_end(self) -> datetime:
        return self.observed_end or self.scheduled_end


@dataclass(frozen=True, slots=True)
class ExternalCalendarAggregate:
    task_id: uuid.UUID
    schedule_plan_id: uuid.UUID
    changed_session_id: uuid.UUID
    task_deadline: datetime | None
    planning_window_start: datetime
    planning_window_end: datetime
    sessions: tuple[ExternalCalendarSession, ...]

    def __post_init__(self) -> None:
        _require_aware("task_deadline", self.task_deadline)
        _require_aware("planning_window_start", self.planning_window_start)
        _require_aware("planning_window_end", self.planning_window_end)
        if _utc(self.planning_window_start) >= _utc(self.planning_window_end):
            raise ValueError("planning window start must be before end")
        ids = tuple(item.scheduled_session_id for item in self.sessions)
        if len(set(ids)) != len(ids):
            raise ValueError("aggregate sessions must be unique")
        if self.changed_session_id not in ids:
            raise ValueError("changed session must belong to aggregate")


class ConflictCode(StrEnum):
    outside_planning_window = "outside_planning_window"
    session_overlap = "session_overlap"
    external_event_missing = "external_event_missing"


class ConflictSeverity(StrEnum):
    warning = "warning"
    error = "error"


@dataclass(frozen=True, slots=True)
class ConflictDetail:
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class NoAction:
    reason: str = "no_difference"


@dataclass(frozen=True, slots=True)
class UpdateScheduledSessionTime:
    scheduled_session_id: uuid.UUID
    previous_start: datetime
    previous_end: datetime
    new_start: datetime
    new_end: datetime


@dataclass(frozen=True, slots=True)
class ExtendTaskDeadline:
    task_id: uuid.UUID
    previous_deadline: datetime | None
    new_deadline: datetime


@dataclass(frozen=True, slots=True)
class MarkExternalEventMissing:
    scheduled_session_id: uuid.UUID
    reason: str


@dataclass(frozen=True, slots=True)
class RecordConflict:
    code: ConflictCode
    severity: ConflictSeverity
    session_ids: tuple[uuid.UUID, ...]
    details: tuple[ConflictDetail, ...] = ()


@dataclass(frozen=True, slots=True)
class UnsupportedExternalChange:
    change_type: str
    reason: str


type PolicyDecision = (
    NoAction
    | UpdateScheduledSessionTime
    | ExtendTaskDeadline
    | MarkExternalEventMissing
    | RecordConflict
    | UnsupportedExternalChange
)


def _changed_session(
    aggregate: ExternalCalendarAggregate,
) -> ExternalCalendarSession:
    return next(
        item
        for item in aggregate.sessions
        if item.scheduled_session_id == aggregate.changed_session_id
    )


def _projected_intervals(
    aggregate: ExternalCalendarAggregate,
    current: ExternalEventState,
) -> tuple[tuple[ExternalCalendarSession, datetime, datetime], ...]:
    projected: list[tuple[ExternalCalendarSession, datetime, datetime]] = []
    for session in aggregate.sessions:
        if not session.mapped or session.externally_missing:
            continue
        if session.scheduled_session_id == aggregate.changed_session_id:
            assert current.start is not None and current.end is not None
            start, end = current.start, current.end
        else:
            start, end = session.effective_start, session.effective_end
        projected.append((session, start, end))
    return tuple(
        sorted(
            projected,
            key=lambda item: (
                _utc(item[1]),
                _utc(item[2]),
                item[0].order,
                str(item[0].scheduled_session_id),
            ),
        )
    )


def _move_conflicts(
    aggregate: ExternalCalendarAggregate,
    current: ExternalEventState,
) -> tuple[RecordConflict, ...]:
    assert current.start is not None and current.end is not None
    conflicts: list[RecordConflict] = []
    if _utc(current.start) < _utc(aggregate.planning_window_start) or _utc(
        current.end
    ) > _utc(aggregate.planning_window_end):
        conflicts.append(
            RecordConflict(
                code=ConflictCode.outside_planning_window,
                severity=ConflictSeverity.warning,
                session_ids=(aggregate.changed_session_id,),
                details=(
                    ConflictDetail("start", current.start.isoformat()),
                    ConflictDetail("end", current.end.isoformat()),
                ),
            )
        )
    projected = _projected_intervals(aggregate, current)
    for previous, following in zip(projected, projected[1:], strict=False):
        if _utc(following[1]) < _utc(previous[2]):
            conflicts.append(
                RecordConflict(
                    code=ConflictCode.session_overlap,
                    severity=ConflictSeverity.error,
                    session_ids=(
                        previous[0].scheduled_session_id,
                        following[0].scheduled_session_id,
                    ),
                )
            )
    return tuple(conflicts)


def evaluate_external_calendar_policy(
    aggregate: ExternalCalendarAggregate,
    change: ExternalCalendarChangeInput,
) -> tuple[PolicyDecision, ...]:
    if change.previous == change.current:
        return (NoAction(),)

    current = change.current
    normalized_type = change.change_type.strip().lower()
    missing = (
        normalized_type in {"deleted", "cancelled"}
        or current is None
        or not current.exists
        or current.cancelled
    )
    if missing:
        reason = "cancelled" if current is not None and current.cancelled else "deleted"
        return (
            MarkExternalEventMissing(
                scheduled_session_id=aggregate.changed_session_id,
                reason=reason,
            ),
            RecordConflict(
                code=ConflictCode.external_event_missing,
                severity=ConflictSeverity.error,
                session_ids=(aggregate.changed_session_id,),
                details=(ConflictDetail("reason", reason),),
            ),
        )

    assert current is not None
    previous = change.previous
    if (
        normalized_type != "moved"
        or previous is None
        or previous.start is None
        or previous.end is None
        or current.start is None
        or current.end is None
        or (previous.start == current.start and previous.end == current.end)
    ):
        return (
            UnsupportedExternalChange(
                change_type=change.change_type,
                reason="change is not supported by the current policy engine",
            ),
        )

    decisions: list[PolicyDecision] = [
        UpdateScheduledSessionTime(
            scheduled_session_id=aggregate.changed_session_id,
            previous_start=previous.start,
            previous_end=previous.end,
            new_start=current.start,
            new_end=current.end,
        )
    ]
    projected = _projected_intervals(aggregate, current)
    latest_end = max((item[2] for item in projected), key=_utc)
    if aggregate.task_deadline is None or _utc(latest_end) > _utc(
        aggregate.task_deadline
    ):
        decisions.append(
            ExtendTaskDeadline(
                task_id=aggregate.task_id,
                previous_deadline=aggregate.task_deadline,
                new_deadline=latest_end,
            )
        )
    decisions.extend(_move_conflicts(aggregate, current))
    return tuple(decisions)
