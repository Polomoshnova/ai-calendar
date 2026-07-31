import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.calendar_integration.models import CalendarEventSnapshot
from app.calendar_sync.processing_schemas import (
    ConsistencyFindingResult,
    ProcessExternalCalendarChangeResult,
)
from app.domain.external_calendar_policy import (
    ExtendTaskDeadline,
    ExternalCalendarAggregate,
    ExternalCalendarChangeInput,
    ExternalCalendarSession,
    ExternalEventState,
    MarkExternalEventMissing,
    NoAction,
    PolicyDecision,
    RecordConflict,
    UnsupportedExternalChange,
    UpdateScheduledSessionTime,
    evaluate_external_calendar_policy,
)
from app.models.calendar import CalendarConnection
from app.models.calendar_sync import (
    CalendarEventMapping,
    ExternalCalendarChange,
    ExternalCalendarConsistencyFinding,
    ExternalChangeProcessingStatus,
    SyncStatus,
    TaskDeadlineHistory,
)
from app.models.task import Task
from app.schedule_plans.models import ScheduledSession, SchedulePlan

PolicyEvaluator = Callable[
    [ExternalCalendarAggregate, ExternalCalendarChangeInput],
    tuple[PolicyDecision, ...],
]


class ExternalCalendarProcessingError(Exception):
    code = "external_calendar_processing_error"
    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class ExternalCalendarChangeNotFoundError(ExternalCalendarProcessingError):
    code = "external_calendar_change_not_found"
    status_code = 404


class ExternalCalendarChangeConcurrencyError(ExternalCalendarProcessingError):
    code = "external_calendar_change_processing"
    status_code = 409


class ExternalCalendarChangeStateError(ExternalCalendarProcessingError):
    code = "external_calendar_change_status_not_processable"
    status_code = 409


class ExternalCalendarAggregateError(ExternalCalendarProcessingError):
    code = "external_calendar_aggregate_invalid"


class UnsupportedPolicyDecisionError(ExternalCalendarProcessingError):
    code = "unsupported_policy_decision"


def _snapshot(
    value: dict[str, object] | None, *, field: str
) -> ExternalEventState | None:
    if value is None:
        return None
    try:
        snapshot = CalendarEventSnapshot.model_validate(value)
        return ExternalEventState(
            exists=snapshot.exists,
            cancelled=snapshot.cancelled,
            start=snapshot.start,
            end=snapshot.end,
            calendar_id=snapshot.calendar_id,
        )
    except (ValidationError, ValueError) as exc:
        raise ExternalCalendarAggregateError(
            f"External calendar change has malformed {field} snapshot"
        ) from exc


def _observed_interval(
    mapping: CalendarEventMapping | None,
) -> tuple[datetime | None, datetime | None]:
    if mapping is None or mapping.last_synced_snapshot is None:
        return None, None
    state = _snapshot(mapping.last_synced_snapshot, field="mapping")
    if state is None or not state.exists or state.cancelled:
        return None, None
    return state.start, state.end


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExternalCalendarAggregateError(
            "Policy decision times must be timezone-aware"
        )
    return value.astimezone(UTC)


class ProcessExternalCalendarChangeService:
    def __init__(
        self,
        session: Session,
        *,
        evaluator: PolicyEvaluator = evaluate_external_calendar_policy,
    ) -> None:
        self._session = session
        self._evaluator = evaluator

    def process(
        self,
        *,
        user_id: uuid.UUID,
        change_id: uuid.UUID,
        now: datetime | None = None,
    ) -> ProcessExternalCalendarChangeResult:
        current_time = now or datetime.now(UTC)
        try:
            change = self._session.scalar(
                select(ExternalCalendarChange)
                .where(ExternalCalendarChange.id == change_id)
                .with_for_update()
            )
            if change is None:
                raise ExternalCalendarChangeNotFoundError(
                    "External calendar change not found"
                )

            mapping = self._session.get(CalendarEventMapping, change.mapping_id)
            if mapping is None:
                raise ExternalCalendarAggregateError(
                    "External calendar change mapping is unavailable"
                )
            scheduled = self._session.get(
                ScheduledSession, mapping.scheduled_session_id
            )
            if scheduled is None:
                raise ExternalCalendarAggregateError(
                    "Mapped scheduled session is unavailable"
                )
            plan = self._session.get(SchedulePlan, scheduled.plan_id)
            if plan is None:
                raise ExternalCalendarAggregateError("Schedule plan is unavailable")
            task_id = scheduled.task_id or plan.task_id
            task = self._session.get(Task, task_id) if task_id is not None else None
            connection = self._session.get(
                CalendarConnection, mapping.calendar_connection_id
            )
            if (
                task is None
                or connection is None
                or scheduled.task_id not in {None, task.id}
                or plan.task_id not in {None, task.id}
            ):
                raise ExternalCalendarAggregateError(
                    "External calendar change has incomplete related state"
                )
            if not (plan.user_id == task.user_id == connection.user_id == user_id):
                raise ExternalCalendarChangeNotFoundError(
                    "External calendar change not found"
                )

            previous_status = change.processing_status
            if previous_status is ExternalChangeProcessingStatus.processed:
                if change.processing_result is None:
                    raise ExternalCalendarAggregateError(
                        "Processed external calendar change has no result"
                    )
                result = ProcessExternalCalendarChangeResult.model_validate(
                    change.processing_result
                )
                result.already_processed = True
                result.previous_processing_status = previous_status
                self._session.rollback()
                return result
            if previous_status is ExternalChangeProcessingStatus.processing:
                raise ExternalCalendarChangeConcurrencyError(
                    "External calendar change is already being processed"
                )
            if previous_status not in {
                ExternalChangeProcessingStatus.pending,
                ExternalChangeProcessingStatus.failed,
            }:
                raise ExternalCalendarChangeStateError(
                    "External calendar change status "
                    f"{previous_status.value!r} is not processable"
                )

            plan_sessions = list(
                self._session.scalars(
                    select(ScheduledSession)
                    .where(
                        ScheduledSession.plan_id == plan.id,
                        or_(
                            ScheduledSession.task_id.is_(None),
                            ScheduledSession.task_id == task.id,
                        ),
                    )
                    .order_by(ScheduledSession.order, ScheduledSession.id)
                )
            )
            if scheduled.id not in {item.id for item in plan_sessions}:
                raise ExternalCalendarAggregateError(
                    "Mapped scheduled session does not belong to its plan"
                )
            mappings = {
                item.scheduled_session_id: item
                for item in self._session.scalars(
                    select(CalendarEventMapping).where(
                        CalendarEventMapping.scheduled_session_id.in_(
                            item.id for item in plan_sessions
                        )
                    )
                )
            }
            aggregate_sessions: list[ExternalCalendarSession] = []
            for item in plan_sessions:
                item_mapping = mappings.get(item.id)
                observed_start, observed_end = _observed_interval(item_mapping)
                aggregate_sessions.append(
                    ExternalCalendarSession(
                        scheduled_session_id=item.id,
                        order=item.order,
                        scheduled_start=item.start,
                        scheduled_end=item.end,
                        mapped=item_mapping is not None,
                        externally_missing=(
                            item_mapping is not None
                            and item_mapping.sync_status
                            is SyncStatus.externally_deleted
                        ),
                        observed_start=observed_start,
                        observed_end=observed_end,
                    )
                )
            try:
                aggregate = ExternalCalendarAggregate(
                    task_id=task.id,
                    schedule_plan_id=plan.id,
                    changed_session_id=scheduled.id,
                    task_deadline=task.deadline,
                    planning_window_start=plan.planning_window_start,
                    planning_window_end=plan.planning_window_end,
                    sessions=tuple(aggregate_sessions),
                )
                policy_change = ExternalCalendarChangeInput(
                    change_type=change.change_type.value,
                    previous=_snapshot(change.old_values, field="previous"),
                    current=_snapshot(change.new_values, field="current"),
                )
            except ValueError as exc:
                raise ExternalCalendarAggregateError(
                    "Persisted external calendar aggregate is invalid"
                ) from exc

            change.processing_status = ExternalChangeProcessingStatus.processing
            decisions = self._evaluator(aggregate, policy_change)
            self._validate_decisions(
                decisions,
                aggregate=aggregate,
                scheduled=scheduled,
                task=task,
            )

            previous_start = scheduled.start
            previous_end = scheduled.end
            previous_deadline = task.deadline
            actions: list[str] = []
            finding_results: list[ConsistencyFindingResult] = []
            deadline_extended = False
            external_event_missing = False
            for decision in decisions:
                if isinstance(decision, NoAction):
                    actions.append("no_action")
                elif isinstance(decision, UpdateScheduledSessionTime):
                    duration_seconds = int(
                        (
                            _utc(decision.new_end) - _utc(decision.new_start)
                        ).total_seconds()
                    )
                    scheduled.start = decision.new_start
                    scheduled.end = decision.new_end
                    scheduled.duration_minutes = duration_seconds // 60
                    actions.append("update_scheduled_session_time")
                elif isinstance(decision, ExtendTaskDeadline):
                    if task.deadline is None or _utc(decision.new_deadline) > _utc(
                        task.deadline
                    ):
                        old_deadline = task.deadline
                        task.deadline = decision.new_deadline
                        self._session.add(
                            TaskDeadlineHistory(
                                task_id=task.id,
                                external_calendar_change_id=change.id,
                                previous_deadline=old_deadline,
                                new_deadline=decision.new_deadline,
                                reason="external_calendar_session_move",
                                changed_at=current_time,
                            )
                        )
                        deadline_extended = True
                        actions.append("extend_task_deadline")
                elif isinstance(decision, MarkExternalEventMissing):
                    mapping.sync_status = SyncStatus.externally_deleted
                    mapping.sync_error_code = None
                    mapping.sync_error_message = None
                    external_event_missing = True
                    actions.append("mark_external_event_missing")
                elif isinstance(decision, RecordConflict):
                    details = {item.key: item.value for item in decision.details}
                    identity_key = ",".join(
                        str(item) for item in sorted(decision.session_ids, key=str)
                    )
                    applicable_session_id = (
                        scheduled.id if scheduled.id in decision.session_ids else None
                    )
                    self._session.add(
                        ExternalCalendarConsistencyFinding(
                            external_calendar_change_id=change.id,
                            schedule_plan_id=plan.id,
                            scheduled_session_id=applicable_session_id,
                            code=decision.code.value,
                            severity=decision.severity.value,
                            identity_key=identity_key,
                            details=details,
                            detected_at=current_time,
                        )
                    )
                    finding_results.append(
                        ConsistencyFindingResult(
                            code=decision.code.value,
                            severity=decision.severity.value,
                            details=details,
                        )
                    )
                    actions.append("record_conflict")
                elif isinstance(decision, UnsupportedExternalChange):
                    actions.append("unsupported_external_change")
                else:
                    raise UnsupportedPolicyDecisionError(
                        f"Unsupported policy decision: {type(decision).__name__}"
                    )

            change.processing_status = ExternalChangeProcessingStatus.processed
            change.processed_at = current_time
            result = ProcessExternalCalendarChangeResult(
                external_change_id=change.id,
                mapping_id=mapping.id,
                scheduled_session_id=scheduled.id,
                schedule_plan_id=plan.id,
                task_id=task.id,
                previous_processing_status=previous_status,
                resulting_processing_status=ExternalChangeProcessingStatus.processed,
                actions_applied=actions,
                previous_session_start=previous_start,
                previous_session_end=previous_end,
                resulting_session_start=scheduled.start,
                resulting_session_end=scheduled.end,
                previous_deadline=previous_deadline,
                resulting_deadline=task.deadline,
                deadline_extended=deadline_extended,
                external_event_missing=external_event_missing,
                consistency_findings=finding_results,
            )
            change.processing_result = result.model_dump(mode="json")
            self._session.info["external_calendar_session_time_updates"] = {
                scheduled.id
            }
            self._session.flush()
            self._session.info.pop("external_calendar_session_time_updates", None)
            self._session.commit()
            return result
        except Exception:
            self._session.info.pop("external_calendar_session_time_updates", None)
            self._session.rollback()
            raise

    @staticmethod
    def _validate_decisions(
        decisions: tuple[PolicyDecision, ...],
        *,
        aggregate: ExternalCalendarAggregate,
        scheduled: ScheduledSession,
        task: Task,
    ) -> None:
        if not decisions:
            raise UnsupportedPolicyDecisionError("Policy engine returned no decisions")
        terminal_decisions = sum(
            isinstance(item, NoAction | UnsupportedExternalChange) for item in decisions
        )
        if terminal_decisions and len(decisions) != 1:
            raise ExternalCalendarAggregateError(
                "No-action policy decisions cannot be combined with mutations"
            )
        known_ids = {item.scheduled_session_id for item in aggregate.sessions}
        for decision in decisions:
            if isinstance(decision, NoAction | UnsupportedExternalChange):
                continue
            if isinstance(decision, UpdateScheduledSessionTime):
                if decision.scheduled_session_id != scheduled.id:
                    raise ExternalCalendarAggregateError(
                        "Policy decision targets an unrelated scheduled session"
                    )
                if _utc(decision.previous_start) != _utc(scheduled.start) or _utc(
                    decision.previous_end
                ) != _utc(scheduled.end):
                    raise ExternalCalendarAggregateError(
                        "Policy decision is stale for the scheduled session"
                    )
                seconds = (
                    _utc(decision.new_end) - _utc(decision.new_start)
                ).total_seconds()
                if seconds <= 0 or seconds % 60:
                    raise ExternalCalendarAggregateError(
                        "Policy decision session interval must be positive "
                        "whole minutes"
                    )
            elif isinstance(decision, ExtendTaskDeadline):
                if decision.task_id != task.id:
                    raise ExternalCalendarAggregateError(
                        "Policy decision targets an unrelated task"
                    )
                _utc(decision.new_deadline)
            elif isinstance(decision, MarkExternalEventMissing):
                if decision.scheduled_session_id != scheduled.id:
                    raise ExternalCalendarAggregateError(
                        "Policy decision targets an unrelated scheduled session"
                    )
            elif isinstance(decision, RecordConflict):
                if (
                    not decision.session_ids
                    or not set(decision.session_ids) <= known_ids
                ):
                    raise ExternalCalendarAggregateError(
                        "Policy conflict references an unrelated scheduled session"
                    )
            else:
                raise UnsupportedPolicyDecisionError(
                    f"Unsupported policy decision: {type(decision).__name__}"
                )
