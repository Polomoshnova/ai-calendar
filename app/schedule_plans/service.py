import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.backlog.domain import (
    OPEN_BACKLOG_STATUSES,
    BacklogOrigin,
    BacklogReason,
    BacklogStatus,
    calculate_remaining_unscheduled_duration,
    validate_backlog_values,
)
from app.backlog.repository import get_entry_for_transition, list_task_sessions
from app.calendar_sync.snapshots import (
    BusySourceSnapshot,
    SessionWriteTargetSnapshot,
    calendar_context_hash,
)
from app.models.backlog import BacklogEntry
from app.models.calendar import (
    CalendarConnection,
    CalendarProviderName,
    CalendarSelection,
)
from app.models.task import Task
from app.models.user import User
from app.schedule_plans.errors import (
    InvalidPlanTransitionError,
    SchedulePlanNotFoundError,
    SchedulePlanUserNotFoundError,
    SchedulePlanValidationError,
)
from app.schedule_plans.models import (
    ScheduledSession,
    ScheduledSessionStatus,
    SchedulePlan,
    SchedulePlanSource,
    SchedulePlanStatus,
)
from app.schedule_plans.repository import (
    get_schedule_plan,
    get_schedule_plan_by_idempotency_key,
    latest_plan_in_group,
)
from app.schedule_plans.schemas import SchedulePlanContext
from app.schemas.scheduling import SchedulePreviewResponse
from app.task_confirmation.models import ConfirmedTask

ALLOWED_PLAN_TRANSITIONS: dict[SchedulePlanStatus, frozenset[SchedulePlanStatus]] = {
    SchedulePlanStatus.proposed: frozenset(
        {SchedulePlanStatus.confirmed, SchedulePlanStatus.obsolete}
    ),
    SchedulePlanStatus.confirmed: frozenset(
        {
            SchedulePlanStatus.obsolete,
            SchedulePlanStatus.revalidation_required,
            SchedulePlanStatus.applying,
        }
    ),
    SchedulePlanStatus.revalidation_required: frozenset(
        {SchedulePlanStatus.confirmed, SchedulePlanStatus.obsolete}
    ),
    SchedulePlanStatus.applying: frozenset(
        {
            SchedulePlanStatus.applied,
            SchedulePlanStatus.partially_applied,
            SchedulePlanStatus.failed,
        }
    ),
    SchedulePlanStatus.partially_applied: frozenset(
        {SchedulePlanStatus.applying, SchedulePlanStatus.failed}
    ),
    SchedulePlanStatus.applied: frozenset(),
    SchedulePlanStatus.failed: frozenset(),
    SchedulePlanStatus.obsolete: frozenset(),
}


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _validate_preview(
    confirmed_task: ConfirmedTask,
    schedule_preview: SchedulePreviewResponse,
    context: SchedulePlanContext,
) -> list[tuple[datetime, datetime, int]]:
    if any(
        value.tzinfo is None or value.utcoffset() is None
        for block in schedule_preview.scheduled_blocks
        for value in (block.start, block.end)
    ):
        raise SchedulePlanValidationError(
            "scheduled session datetimes must be timezone-aware"
        )
    blocks = sorted(
        schedule_preview.scheduled_blocks,
        key=lambda block: (_as_utc(block.start), _as_utc(block.end)),
    )
    if not blocks:
        raise SchedulePlanValidationError(
            "schedule plan requires at least one scheduled block"
        )
    if schedule_preview.scheduler_version != context.scheduler_version:
        raise SchedulePlanValidationError(
            "schedule preview and context scheduler versions must match"
        )
    preview_start = _as_utc(schedule_preview.planning_window.start)
    preview_end = _as_utc(schedule_preview.planning_window.end)
    window_start = _as_utc(context.planning_window_start)
    window_end = _as_utc(context.planning_window_end)
    if preview_start != window_start or preview_end != window_end:
        raise SchedulePlanValidationError(
            "schedule preview and context planning windows must match"
        )

    normalized: list[tuple[datetime, datetime, int]] = []
    previous_end: datetime | None = None
    for block in blocks:
        start = _as_utc(block.start)
        end = _as_utc(block.end)
        if start >= end:
            raise SchedulePlanValidationError(
                "scheduled session start must be before end"
            )
        if start < window_start or end > window_end:
            raise SchedulePlanValidationError(
                "scheduled session must be inside the planning window"
            )
        if previous_end is not None and start < previous_end:
            raise SchedulePlanValidationError("scheduled sessions must not overlap")
        seconds = (end - start).total_seconds()
        if seconds % 60 != 0:
            raise SchedulePlanValidationError(
                "scheduled session duration must use whole minutes"
            )
        duration_minutes = int(seconds // 60)
        if duration_minutes <= 0:
            raise SchedulePlanValidationError(
                "scheduled session duration must be positive"
            )
        normalized.append((start, end, duration_minutes))
        previous_end = end

    total_duration = sum(item[2] for item in normalized)
    if (
        confirmed_task.duration_minutes is not None
        and total_duration > confirmed_task.duration_minutes
    ):
        raise SchedulePlanValidationError(
            "scheduled duration exceeds confirmed task duration"
        )
    return normalized


def _derived_idempotency_key(
    *,
    user_id: uuid.UUID,
    task_id: uuid.UUID | None,
    backlog_entry_id: uuid.UUID | None,
    plan_group_id: uuid.UUID | None,
    source: SchedulePlanSource,
    scheduler_version: str,
    blocks: list[tuple[datetime, datetime, int]],
) -> str:
    payload = {
        "user_id": str(user_id),
        "task_id": str(task_id) if task_id is not None else None,
        "backlog_entry_id": (
            str(backlog_entry_id) if backlog_entry_id is not None else None
        ),
        "plan_group_id": (str(plan_group_id) if plan_group_id is not None else None),
        "source": source.value,
        "scheduler_version": scheduler_version,
        "blocks": [
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "duration_minutes": duration,
            }
            for start, end, duration in blocks
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"schedule-plan:{digest}"


def _busy_context_summary(context: SchedulePlanContext) -> dict[str, Any]:
    if context.calendar_context is None:
        return {
            "provider": None,
            "calendar_ids": [],
            "provider_busy_interval_count": 0,
            "merged_busy_interval_count": 0,
            "queried_at": None,
            "planning_window": {
                "start": context.planning_window_start.isoformat(),
                "end": context.planning_window_end.isoformat(),
            },
        }
    summary = context.calendar_context.model_dump(mode="json")
    summary["planning_window"] = {
        "start": context.planning_window_start.isoformat(),
        "end": context.planning_window_end.isoformat(),
    }
    return summary


def _calendar_snapshots(
    session: Session,
    *,
    user_id: uuid.UUID,
    planning_context: SchedulePlanContext,
    scheduled_session_ids: list[uuid.UUID],
    captured_at: datetime,
) -> tuple[
    list[BusySourceSnapshot],
    list[SessionWriteTargetSnapshot],
]:
    calendar_context = planning_context.calendar_context
    if calendar_context is None:
        raise SchedulePlanValidationError(
            "calendar context is required to create a schedule plan"
        )
    try:
        provider = CalendarProviderName(calendar_context.provider)
    except ValueError as exc:
        raise SchedulePlanValidationError(
            "calendar context provider is not supported"
        ) from exc
    connection = session.scalar(
        select(CalendarConnection).where(
            CalendarConnection.user_id == user_id,
            CalendarConnection.provider == provider,
        )
    )
    if connection is None:
        raise SchedulePlanValidationError(
            "calendar context connection must exist and belong to the "
            "schedule plan user"
        )
    calendar_ids = list(dict.fromkeys(calendar_context.calendar_ids))
    if not calendar_ids:
        raise SchedulePlanValidationError(
            "calendar context requires at least one busy source"
        )

    selections = list(
        session.scalars(
            select(CalendarSelection).where(
                CalendarSelection.connection_id == connection.id
            )
        )
    )
    selected_by_id = {item.external_calendar_id: item for item in selections}
    unknown_ids = [
        calendar_id for calendar_id in calendar_ids if calendar_id not in selected_by_id
    ]
    if unknown_ids:
        raise SchedulePlanValidationError(
            "calendar context contains calendars not known to its connection"
        )

    primary_ids = {item.external_calendar_id for item in selections if item.primary}
    selected_ids = {
        item.external_calendar_id for item in selections if item.include_in_availability
    }
    if len(primary_ids) == 1:
        write_calendar_id = next(iter(primary_ids))
    elif len(selected_ids) == 1:
        write_calendar_id = next(iter(selected_ids))
    else:
        raise SchedulePlanValidationError(
            "schedule plan requires a deterministic calendar write target"
        )

    busy_sources = [
        BusySourceSnapshot(
            connection_id=connection.id,
            provider=connection.provider,
            provider_account_id=connection.provider_account_id,
            calendar_id=calendar_id,
            captured_at=captured_at,
        )
        for calendar_id in calendar_ids
    ]
    write_targets = [
        SessionWriteTargetSnapshot(
            scheduled_session_id=session_id,
            connection_id=connection.id,
            provider=connection.provider,
            provider_account_id=connection.provider_account_id,
            calendar_id=write_calendar_id,
        )
        for session_id in scheduled_session_ids
    ]
    return busy_sources, write_targets


def _preview_metadata(schedule_preview: SchedulePreviewResponse) -> dict[str, Any]:
    return {
        "scheduler_version": schedule_preview.scheduler_version,
        "warnings": [
            warning.model_dump(mode="json") for warning in schedule_preview.warnings
        ],
        "unscheduled_tasks": [
            item.model_dump(mode="json") for item in schedule_preview.unscheduled_tasks
        ],
        "scheduled_block_metadata": [
            {
                "task_id": block.task_id,
                "start": block.start.isoformat(),
                "end": block.end.isoformat(),
                "reason_codes": [item.value for item in block.reason_codes],
                "score_components": [
                    item.model_dump(mode="json") for item in block.score_components
                ],
            }
            for block in schedule_preview.scheduled_blocks
        ],
    }


def _mark_obsolete(plan: SchedulePlan) -> None:
    transition_schedule_plan(plan, SchedulePlanStatus.obsolete)
    for scheduled_session in plan.sessions:
        scheduled_session.status = ScheduledSessionStatus.obsolete


def transition_schedule_plan(plan: SchedulePlan, target: SchedulePlanStatus) -> None:
    if plan.status is target:
        return
    if target not in ALLOWED_PLAN_TRANSITIONS[plan.status]:
        raise InvalidPlanTransitionError(
            f"cannot transition plan from {plan.status.value} to {target.value}"
        )
    plan.status = target


def _existing_idempotent_plan(
    session: Session,
    *,
    key: str,
    user_id: uuid.UUID,
) -> SchedulePlan | None:
    existing = get_schedule_plan_by_idempotency_key(session, key)
    if existing is not None and existing.user_id != user_id:
        raise SchedulePlanValidationError("idempotency key belongs to another user")
    return existing


def create_schedule_plan_from_preview(
    session: Session,
    *,
    user_id: uuid.UUID,
    confirmed_task: ConfirmedTask,
    schedule_preview: SchedulePreviewResponse,
    planning_context: SchedulePlanContext,
    source: SchedulePlanSource,
    task_id: uuid.UUID | None = None,
    backlog_entry_id: uuid.UUID | None = None,
    plan_group_id: uuid.UUID | None = None,
    confirmation_note: str | None = None,
    idempotency_key: str | None = None,
) -> SchedulePlan:
    if session.get(User, user_id) is None:
        raise SchedulePlanUserNotFoundError("User not found")
    if task_id is not None:
        task = session.get(Task, task_id)
        if task is None or task.user_id != user_id:
            raise SchedulePlanValidationError(
                "task must exist and belong to the schedule plan user"
            )
    if backlog_entry_id is not None:
        backlog_entry = get_entry_for_transition(
            session, backlog_entry_id, user_id=user_id
        )
        if backlog_entry is None:
            raise SchedulePlanValidationError(
                "backlog entry must exist and belong to the schedule plan user"
            )
        if task_id is None or backlog_entry.task_id != task_id:
            raise SchedulePlanValidationError(
                "backlog entry and schedule plan must reference the same task"
            )
    normalized_blocks = _validate_preview(
        confirmed_task,
        schedule_preview,
        planning_context,
    )
    key = idempotency_key or _derived_idempotency_key(
        user_id=user_id,
        task_id=task_id,
        backlog_entry_id=backlog_entry_id,
        plan_group_id=plan_group_id,
        source=source,
        scheduler_version=schedule_preview.scheduler_version,
        blocks=normalized_blocks,
    )
    existing = _existing_idempotent_plan(
        session,
        key=key,
        user_id=user_id,
    )
    if existing is not None:
        return existing

    plan_id = uuid.uuid4()
    scheduled_session_ids = [uuid.uuid4() for _ in normalized_blocks]
    calendar_context_captured_at = datetime.now(UTC)
    busy_sources, write_targets = _calendar_snapshots(
        session,
        user_id=user_id,
        planning_context=planning_context,
        scheduled_session_ids=scheduled_session_ids,
        captured_at=calendar_context_captured_at,
    )
    group_id = plan_group_id or uuid.uuid4()
    previous = (
        latest_plan_in_group(session, group_id, for_update=True)
        if plan_group_id is not None
        else None
    )
    if previous is not None and previous.user_id != user_id:
        raise SchedulePlanValidationError("plan group does not belong to user")
    if previous is not None and previous.status in {
        SchedulePlanStatus.applied,
        SchedulePlanStatus.applying,
        SchedulePlanStatus.partially_applied,
    }:
        raise InvalidPlanTransitionError(
            "cannot revise a plan that is applying or applied"
        )
    version = previous.version + 1 if previous is not None else 1
    if previous is not None and previous.status in {
        SchedulePlanStatus.proposed,
        SchedulePlanStatus.confirmed,
        SchedulePlanStatus.revalidation_required,
    }:
        _mark_obsolete(previous)

    plan = SchedulePlan(
        id=plan_id,
        user_id=user_id,
        task_id=task_id,
        backlog_entry_id=backlog_entry_id,
        plan_group_id=group_id,
        source=source,
        version=version,
        status=SchedulePlanStatus.proposed,
        timezone=planning_context.timezone,
        planning_window_start=planning_context.planning_window_start,
        planning_window_end=planning_context.planning_window_end,
        source_calendar_snapshot_at=(planning_context.source_calendar_snapshot_at),
        scheduler_version=planning_context.scheduler_version,
        workflow_version=planning_context.workflow_version,
        confirmation_note=confirmation_note,
        idempotency_key=key,
        confirmed_task_snapshot=confirmed_task.model_dump(mode="json"),
        scheduling_preferences_snapshot=planning_context.preferences_snapshot,
        busy_context_summary=_busy_context_summary(planning_context),
        preview_metadata=_preview_metadata(schedule_preview),
        busy_sources_snapshot=[item.model_dump(mode="json") for item in busy_sources],
        write_targets_snapshot=[item.model_dump(mode="json") for item in write_targets],
        calendar_selection_hash=calendar_context_hash(
            busy_sources=busy_sources,
            write_targets=write_targets,
        ),
        calendar_context_captured_at=calendar_context_captured_at,
    )
    for order, (session_id, block) in enumerate(
        zip(scheduled_session_ids, normalized_blocks, strict=True),
        start=1,
    ):
        start, end, duration_minutes = block
        plan.sessions.append(
            ScheduledSession(
                id=session_id,
                plan_id=plan_id,
                task_id=task_id,
                step_order=None,
                title=confirmed_task.title,
                description=confirmed_task.description,
                start=start,
                end=end,
                duration_minutes=duration_minutes,
                order=order,
                status=ScheduledSessionStatus.proposed,
            )
        )
    session.add(plan)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _existing_idempotent_plan(
            session,
            key=key,
            user_id=user_id,
        )
        if existing is None:
            raise
        return existing
    session.refresh(plan)
    return plan


def confirm_schedule_plan(
    session: Session,
    plan_id: uuid.UUID,
    *,
    confirmation_note: str | None = None,
    now: datetime | None = None,
) -> SchedulePlan:
    plan = get_schedule_plan(session, plan_id, for_update=True)
    if plan is None:
        raise SchedulePlanNotFoundError("Schedule plan not found")
    if plan.status is SchedulePlanStatus.confirmed:
        return plan
    if plan.status is not SchedulePlanStatus.proposed:
        raise InvalidPlanTransitionError(
            f"cannot confirm plan in {plan.status.value} status"
        )
    effective_now = now or datetime.now(UTC)
    backlog_entry = None
    task = None
    if plan.backlog_entry_id is not None:
        backlog_entry = get_entry_for_transition(
            session, plan.backlog_entry_id, user_id=plan.user_id
        )
        if backlog_entry is None:
            raise SchedulePlanValidationError("Linked backlog entry not found")
        if backlog_entry.status not in OPEN_BACKLOG_STATUSES:
            raise SchedulePlanValidationError(
                f"cannot confirm a plan for a {backlog_entry.status.value} "
                "backlog entry"
            )
        if plan.task_id is None or backlog_entry.task_id != plan.task_id:
            raise SchedulePlanValidationError(
                "linked backlog entry and plan must reference the same task"
            )
        task = session.get(Task, plan.task_id)
        if task is None or task.user_id != plan.user_id:
            raise SchedulePlanValidationError("Linked backlog task not found")
        remaining_before_confirmation = calculate_remaining_unscheduled_duration(
            task.duration_minutes,
            list_task_sessions(session, task_id=task.id),
        )
        selected_duration = sum(item.duration_minutes for item in plan.sessions)
        if selected_duration > remaining_before_confirmation:
            raise SchedulePlanValidationError(
                "schedule plan duration exceeds current remaining backlog work"
            )
    try:
        transition_schedule_plan(plan, SchedulePlanStatus.confirmed)
        plan.confirmed_at = effective_now
        if confirmation_note is not None:
            plan.confirmation_note = confirmation_note
        for scheduled_session in plan.sessions:
            scheduled_session.status = ScheduledSessionStatus.confirmed
        if backlog_entry is not None and task is not None:
            _update_backlog_after_confirmation(
                session,
                backlog_entry=backlog_entry,
                task=task,
                resolved_at=effective_now,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(plan)
    return plan


def _update_backlog_after_confirmation(
    session: Session,
    *,
    backlog_entry: BacklogEntry,
    task: Task,
    resolved_at: datetime,
) -> None:
    """Recalculate backlog state after the linked plan starts reserving time."""
    remaining = calculate_remaining_unscheduled_duration(
        task.duration_minutes,
        list_task_sessions(session, task_id=task.id),
    )
    backlog_entry.remaining_duration_minutes = remaining
    if remaining == 0:
        backlog_entry.status = BacklogStatus.resolved
        backlog_entry.resolved_at = resolved_at
    else:
        backlog_entry.reason = BacklogReason.partially_scheduled
        backlog_entry.origin = BacklogOrigin.scheduler
    validate_backlog_values(
        origin=backlog_entry.origin,
        reason=backlog_entry.reason,
        note=backlog_entry.note,
        status=backlog_entry.status,
        remaining_duration_minutes=backlog_entry.remaining_duration_minutes,
        task_duration_minutes=task.duration_minutes,
        entered_at=backlog_entry.entered_at,
        next_review_at=backlog_entry.next_review_at,
        deferred_until=backlog_entry.deferred_until,
        resolved_at=backlog_entry.resolved_at,
        scheduling_attempt_count=backlog_entry.scheduling_attempt_count,
        last_scheduling_attempt_at=backlog_entry.last_scheduling_attempt_at,
    )


def obsolete_schedule_plan(
    session: Session,
    plan_id: uuid.UUID,
) -> SchedulePlan:
    plan = get_schedule_plan(session, plan_id, for_update=True)
    if plan is None:
        raise SchedulePlanNotFoundError("Schedule plan not found")
    if plan.status is SchedulePlanStatus.obsolete:
        return plan
    if plan.status not in {
        SchedulePlanStatus.proposed,
        SchedulePlanStatus.confirmed,
        SchedulePlanStatus.revalidation_required,
    }:
        raise InvalidPlanTransitionError(
            f"cannot obsolete plan in {plan.status.value} status"
        )
    _mark_obsolete(plan)
    session.commit()
    session.refresh(plan)
    return plan
