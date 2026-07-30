import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.calendar_integration.errors import CalendarIntegrationError
from app.calendar_integration.models import CalendarEventCreateRequest
from app.calendar_integration.protocols import (
    CalendarOAuthClient,
    CalendarProvider,
    TokenCipher,
)
from app.calendar_integration.service import connection_credentials
from app.calendar_sync.snapshots import SessionWriteTargetSnapshot
from app.models.calendar import CalendarConnection, CalendarConnectionStatus
from app.models.calendar_sync import CalendarEventMapping, SyncStatus
from app.models.task import Task
from app.schedule_plans.apply_schemas import (
    ApplySchedulePlanResult,
    ApplySessionOutcome,
    ApplySessionStatus,
)
from app.schedule_plans.errors import (
    InvalidPlanTransitionError,
    SchedulePlanNotFoundError,
    SchedulePlanValidationError,
)
from app.schedule_plans.models import (
    ScheduledSession,
    ScheduledSessionStatus,
    SchedulePlan,
    SchedulePlanStatus,
)
from app.schedule_plans.repository import get_schedule_plan
from app.schedule_plans.service import transition_schedule_plan


def _mapping(session: ScheduledSession) -> CalendarEventMapping | None:
    return session.calendar_event_mapping


def _load_owned_plan(
    session: Session,
    plan_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> SchedulePlan:
    plan = get_schedule_plan(session, plan_id, for_update=for_update)
    if plan is None or plan.user_id != user_id:
        raise SchedulePlanNotFoundError("Schedule plan not found")
    return plan


def _targets_for_unapplied_sessions(
    session: Session,
    plan: SchedulePlan,
) -> dict[uuid.UUID, tuple[SessionWriteTargetSnapshot, CalendarConnection]]:
    if plan.write_targets_snapshot is None:
        raise SchedulePlanValidationError(
            "schedule plan has no stored calendar write targets"
        )
    try:
        snapshots = [
            SessionWriteTargetSnapshot.model_validate(item)
            for item in plan.write_targets_snapshot
        ]
    except ValueError as exc:
        raise SchedulePlanValidationError(
            "schedule plan calendar write targets are invalid"
        ) from exc
    by_session: dict[uuid.UUID, list[SessionWriteTargetSnapshot]] = {}
    for snapshot in snapshots:
        by_session.setdefault(snapshot.scheduled_session_id, []).append(snapshot)

    resolved: dict[
        uuid.UUID, tuple[SessionWriteTargetSnapshot, CalendarConnection]
    ] = {}
    for scheduled_session in plan.sessions:
        if _mapping(scheduled_session) is not None:
            continue
        targets = by_session.get(scheduled_session.id, [])
        if len(targets) != 1:
            raise SchedulePlanValidationError(
                "every unapplied session requires exactly one stored write target"
            )
        target = targets[0]
        connection = session.get(CalendarConnection, target.connection_id)
        if connection is None or connection.user_id != plan.user_id:
            raise SchedulePlanValidationError(
                "stored calendar write target connection is unavailable"
            )
        if connection.status is not CalendarConnectionStatus.active:
            raise SchedulePlanValidationError(
                "stored calendar write target connection is not active"
            )
        if (
            connection.provider is not target.provider
            or connection.provider_account_id != target.provider_account_id
        ):
            raise SchedulePlanValidationError(
                "stored calendar write target does not match its connection"
            )
        resolved[scheduled_session.id] = (target, connection)
    return resolved


def _already_applied_result(
    plan: SchedulePlan,
    previous_status: SchedulePlanStatus,
) -> ApplySchedulePlanResult:
    outcomes = [
        _mapped_outcome(item, mapping)
        for item in plan.sessions
        if (mapping := _mapping(item)) is not None
    ]
    return ApplySchedulePlanResult(
        plan_id=plan.id,
        previous_status=previous_status,
        resulting_status=plan.status,
        sessions_total=len(plan.sessions),
        sessions_already_applied=len(outcomes),
        sessions_attempted=0,
        sessions_applied=0,
        sessions_failed=0,
        outcomes=outcomes,
    )


def _mapped_outcome(
    scheduled_session: ScheduledSession,
    mapping: CalendarEventMapping,
) -> ApplySessionOutcome:
    return ApplySessionOutcome(
        scheduled_session_id=scheduled_session.id,
        status=ApplySessionStatus.already_applied,
        external_event_id=mapping.external_event_id,
        connection_id=mapping.calendar_connection_id,
        calendar_id=mapping.calendar_id,
    )


async def apply_schedule_plan(
    session: Session,
    *,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
    provider: CalendarProvider,
    oauth_client: CalendarOAuthClient,
    cipher: TokenCipher,
    now: datetime | None = None,
) -> ApplySchedulePlanResult:
    current_time = now or datetime.now(UTC)
    session.expire_all()
    plan = _load_owned_plan(session, plan_id, user_id, for_update=True)
    previous_status = plan.status
    if plan.status is SchedulePlanStatus.applied:
        return _already_applied_result(plan, previous_status)
    if plan.status not in {
        SchedulePlanStatus.confirmed,
        SchedulePlanStatus.partially_applied,
    }:
        raise InvalidPlanTransitionError(
            f"cannot apply plan in {plan.status.value} status"
        )
    targets = _targets_for_unapplied_sessions(session, plan)
    existing_outcomes = [
        _mapped_outcome(item, mapping)
        for item in plan.sessions
        if (mapping := _mapping(item)) is not None
    ]
    transition_schedule_plan(plan, SchedulePlanStatus.applying)
    for item in plan.sessions:
        item.status = (
            ScheduledSessionStatus.applied
            if _mapping(item) is not None
            else ScheduledSessionStatus.applying
        )
        item.failure_code = None
    plan.failure_code = None
    session.commit()

    attempted_outcomes: list[ApplySessionOutcome] = []
    task = session.get(Task, plan.task_id) if plan.task_id is not None else None
    for scheduled_session in plan.sessions:
        if _mapping(scheduled_session) is not None:
            continue
        target, connection = targets[scheduled_session.id]
        try:
            credentials = await connection_credentials(
                session,
                connection,
                cipher=cipher,
                oauth_client=oauth_client,
            )
            created = await provider.create_event(
                credentials,
                CalendarEventCreateRequest(
                    connection_id=connection.id,
                    provider_account_id=target.provider_account_id or "",
                    calendar_id=target.calendar_id,
                    event_id=scheduled_session.id.hex,
                    title=task.title if task is not None else scheduled_session.title,
                    description=scheduled_session.description,
                    start=scheduled_session.start,
                    end=scheduled_session.end,
                    timezone=plan.timezone,
                    task_id=scheduled_session.task_id,
                    schedule_plan_id=plan.id,
                    scheduled_session_id=scheduled_session.id,
                ),
            )
            mapping = CalendarEventMapping(
                scheduled_session_id=scheduled_session.id,
                calendar_connection_id=connection.id,
                provider=target.provider,
                provider_account_id=target.provider_account_id,
                calendar_id=created.calendar_id,
                external_event_id=created.external_event_id,
                etag=created.etag,
                provider_updated_at=created.provider_updated_at,
                sync_status=SyncStatus.synced,
                last_sync_attempt_at=current_time,
                last_synced_at=current_time,
            )
            session.add(mapping)
            scheduled_session.status = ScheduledSessionStatus.applied
            scheduled_session.failure_code = None
            session.commit()
            attempted_outcomes.append(
                ApplySessionOutcome(
                    scheduled_session_id=scheduled_session.id,
                    status=ApplySessionStatus.applied,
                    external_event_id=created.external_event_id,
                    connection_id=connection.id,
                    calendar_id=created.calendar_id,
                )
            )
        except CalendarIntegrationError as exc:
            session.rollback()
            failed_session = session.get(ScheduledSession, scheduled_session.id)
            if failed_session is not None:
                failed_session.status = ScheduledSessionStatus.failed
                failed_session.failure_code = exc.code
                session.commit()
            attempted_outcomes.append(
                ApplySessionOutcome(
                    scheduled_session_id=scheduled_session.id,
                    status=ApplySessionStatus.failed,
                    connection_id=target.connection_id,
                    calendar_id=target.calendar_id,
                    error_code=exc.code,
                    message=exc.message,
                )
            )
        except IntegrityError:
            session.rollback()
            persisted = session.scalar(
                select(CalendarEventMapping).where(
                    CalendarEventMapping.scheduled_session_id == scheduled_session.id
                )
            )
            if persisted is not None:
                attempted_outcomes.append(
                    ApplySessionOutcome(
                        scheduled_session_id=scheduled_session.id,
                        status=ApplySessionStatus.already_applied,
                        external_event_id=persisted.external_event_id,
                        connection_id=persisted.calendar_connection_id,
                        calendar_id=persisted.calendar_id,
                    )
                )
            else:
                failed_session = session.get(ScheduledSession, scheduled_session.id)
                if failed_session is not None:
                    failed_session.status = ScheduledSessionStatus.failed
                    failed_session.failure_code = "mapping_persistence_failed"
                    session.commit()
                attempted_outcomes.append(
                    ApplySessionOutcome(
                        scheduled_session_id=scheduled_session.id,
                        status=ApplySessionStatus.failed,
                        connection_id=target.connection_id,
                        calendar_id=target.calendar_id,
                        error_code="mapping_persistence_failed",
                        message="Calendar event mapping could not be persisted.",
                    )
                )

    session.expire_all()
    final_plan = _load_owned_plan(session, plan_id, user_id, for_update=True)
    mapped_count = sum(_mapping(item) is not None for item in final_plan.sessions)
    if mapped_count == len(final_plan.sessions):
        resulting_status = SchedulePlanStatus.applied
    elif mapped_count:
        resulting_status = SchedulePlanStatus.partially_applied
    else:
        resulting_status = SchedulePlanStatus.failed
    transition_schedule_plan(final_plan, resulting_status)
    final_plan.applied_at = (
        current_time if resulting_status is SchedulePlanStatus.applied else None
    )
    final_plan.failure_code = (
        None
        if resulting_status is SchedulePlanStatus.applied
        else "calendar_apply_failed"
    )
    session.commit()

    failures = sum(
        outcome.status is ApplySessionStatus.failed for outcome in attempted_outcomes
    )
    applied = sum(
        outcome.status is ApplySessionStatus.applied for outcome in attempted_outcomes
    )
    return ApplySchedulePlanResult(
        plan_id=final_plan.id,
        previous_status=previous_status,
        resulting_status=resulting_status,
        sessions_total=len(final_plan.sessions),
        sessions_already_applied=len(existing_outcomes),
        sessions_attempted=len(attempted_outcomes),
        sessions_applied=applied,
        sessions_failed=failures,
        outcomes=[*existing_outcomes, *attempted_outcomes],
    )
