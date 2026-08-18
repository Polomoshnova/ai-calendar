import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.backlog.domain import (
    BacklogDomainError,
    BacklogOrigin,
    BacklogReason,
    BacklogStatus,
    InvalidBacklogTransitionError,
    calculate_remaining_unscheduled_duration,
)
from app.backlog.repository import (
    get_entry,
    list_backlog_entries,
    list_task_sessions,
)
from app.backlog.schemas import (
    BacklogDeferRequest,
    BacklogEntryCreateRequest,
    BacklogEntryResponse,
    BacklogNoteRequest,
    BacklogSchedulePlanCreateRequest,
    BacklogSchedulePreviewRequest,
    BacklogSchedulePreviewResponse,
)
from app.backlog.service import (
    BacklogEntryAlreadyExistsError,
    BacklogEntryNotFoundError,
    BacklogOwnershipError,
    BacklogPreviewNotAllowedError,
    cancel_backlog_entry,
    create_backlog_entry,
    create_backlog_schedule_plan,
    defer_backlog_entry,
    preview_backlog_entry_schedule,
    reactivate_backlog_entry,
    resolve_backlog_entry,
)
from app.internal.dependencies import InternalToolsEnabled
from app.internal.schedule_plans_router import schedule_plan_response
from app.models import Task, User
from app.schedule_plans.errors import SchedulePlanValidationError
from app.schedule_plans.schemas import SchedulePlanResponse
from app.schemas.scheduling import SchedulePreviewResponse

router = APIRouter(prefix="/internal/api/backlog", tags=["internal-backlog"])


def _response(entry: object) -> BacklogEntryResponse:
    return BacklogEntryResponse.model_validate(entry)


def _http_error(exc: BacklogDomainError) -> HTTPException:
    if isinstance(exc, (BacklogEntryNotFoundError, BacklogOwnershipError)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(
        exc,
        (
            BacklogEntryAlreadyExistsError,
            BacklogPreviewNotAllowedError,
            InvalidBacklogTransitionError,
        ),
    ):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get(
    "",
    response_model=list[BacklogEntryResponse],
    summary="List backlog entries",
    description=(
        "Lists owned backlog entries. Without a status filter, only active and "
        "deferred entries are returned. This endpoint never runs scheduling."
    ),
)
def list_entries(
    user_id: uuid.UUID,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
    entry_status: Annotated[BacklogStatus | None, Query(alias="status")] = None,
    reason: BacklogReason | None = None,
    origin: BacklogOrigin | None = None,
    due_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BacklogEntryResponse]:
    if session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    return [
        _response(entry)
        for entry in list_backlog_entries(
            session,
            user_id=user_id,
            status=entry_status,
            reason=reason,
            origin=origin,
            due_only=due_only,
            due_at=datetime.now(UTC) if due_only else None,
            limit=limit,
            offset=offset,
        )
    ]


@router.get(
    "/{entry_id}",
    response_model=BacklogEntryResponse,
    summary="Get a backlog entry",
)
def read_entry(
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> BacklogEntryResponse:
    entry = get_entry(session, entry_id, user_id=user_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Backlog entry not found")
    return _response(entry)


@router.post(
    "",
    response_model=BacklogEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a backlog entry",
    description=(
        "Creates an explicit backlog entry without invoking scheduling. If "
        "remaining duration is omitted, persisted reserving sessions are used."
    ),
)
def create_entry(
    data: BacklogEntryCreateRequest,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> BacklogEntryResponse:
    task = session.get(Task, data.task_id)
    if task is None or task.user_id != data.user_id:
        raise HTTPException(status_code=404, detail="Task not found")
    remaining = data.remaining_duration_minutes
    if remaining is None:
        remaining = calculate_remaining_unscheduled_duration(
            task.duration_minutes,
            list_task_sessions(session, task_id=task.id),
        )
    try:
        entry = create_backlog_entry(
            session,
            task_id=task.id,
            user_id=data.user_id,
            origin=data.origin,
            reason=data.reason,
            remaining_duration_minutes=remaining,
            next_review_at=data.next_review_at,
            deferred_until=data.deferred_until,
            note=data.note,
        )
    except BacklogDomainError as exc:
        raise _http_error(exc) from exc
    return _response(entry)


@router.post(
    "/{entry_id}/defer",
    response_model=BacklogEntryResponse,
    summary="Defer a backlog entry",
)
def defer_entry(
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    data: BacklogDeferRequest,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> BacklogEntryResponse:
    try:
        entry = defer_backlog_entry(
            session,
            entry_id=entry_id,
            user_id=user_id,
            deferred_until=data.deferred_until,
            next_review_at=data.next_review_at,
            note=data.note,
        )
    except BacklogDomainError as exc:
        raise _http_error(exc) from exc
    return _response(entry)


@router.post(
    "/{entry_id}/reactivate",
    response_model=BacklogEntryResponse,
    summary="Reactivate a deferred backlog entry",
)
def reactivate_entry(
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> BacklogEntryResponse:
    try:
        entry = reactivate_backlog_entry(session, entry_id=entry_id, user_id=user_id)
    except BacklogDomainError as exc:
        raise _http_error(exc) from exc
    return _response(entry)


@router.post(
    "/{entry_id}/resolve",
    response_model=BacklogEntryResponse,
    summary="Resolve a backlog entry",
)
def resolve_entry(
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
    data: BacklogNoteRequest | None = None,
) -> BacklogEntryResponse:
    try:
        entry = resolve_backlog_entry(
            session,
            entry_id=entry_id,
            user_id=user_id,
            note=data.note if data else None,
        )
    except BacklogDomainError as exc:
        raise _http_error(exc) from exc
    return _response(entry)


@router.post(
    "/{entry_id}/cancel",
    response_model=BacklogEntryResponse,
    summary="Cancel backlog tracking",
)
def cancel_entry(
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
    data: BacklogNoteRequest | None = None,
) -> BacklogEntryResponse:
    try:
        entry = cancel_backlog_entry(
            session,
            entry_id=entry_id,
            user_id=user_id,
            note=data.note if data else None,
        )
    except BacklogDomainError as exc:
        raise _http_error(exc) from exc
    return _response(entry)


@router.post(
    "/{entry_id}/schedule-preview",
    response_model=BacklogSchedulePreviewResponse,
    summary="Preview backlog entry scheduling",
    description=(
        "Explicitly retries deterministic scheduling for one active or deferred "
        "entry using its current unscheduled duration. Includes request busy "
        "intervals and reserving SchedulePlan intervals. Persists attempt metadata "
        "only; it never creates a plan, writes Google Calendar, or changes backlog "
        "status."
    ),
)
def schedule_preview_entry(
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    data: BacklogSchedulePreviewRequest,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> BacklogSchedulePreviewResponse:
    try:
        result = preview_backlog_entry_schedule(
            session,
            entry_id=entry_id,
            user_id=user_id,
            planning_window=data.planning_window.to_domain(),
            busy_intervals=tuple(item.to_domain() for item in data.busy_intervals),
        )
    except BacklogDomainError as exc:
        raise _http_error(exc) from exc
    preview = SchedulePreviewResponse.from_domain(
        result.preview.planning_window,
        result.preview.free_intervals,
        result.preview.result,
    )
    unscheduled_reason = (
        preview.unscheduled_tasks[0].reason_code if preview.unscheduled_tasks else None
    )
    return BacklogSchedulePreviewResponse(
        backlog_entry_id=result.entry.id,
        task_id=result.entry.task_id,
        remaining_duration_minutes=result.remaining_duration_minutes,
        scheduling_attempt_count=result.entry.scheduling_attempt_count,
        schedule_preview=preview,
        unscheduled_reason=unscheduled_reason,
    )


@router.post(
    "/{entry_id}/schedule-plan",
    response_model=SchedulePlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a SchedulePlan from a backlog preview",
    description=(
        "Persists the explicitly selected backlog preview as an idempotent "
        "proposed SchedulePlan with calendar provenance snapshots. This endpoint "
        "does not confirm the plan, change backlog state, rerun scheduling, or "
        "write Google Calendar."
    ),
)
def create_schedule_plan_entry(
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    data: BacklogSchedulePlanCreateRequest,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> SchedulePlanResponse:
    try:
        plan = create_backlog_schedule_plan(
            session,
            entry_id=entry_id,
            user_id=user_id,
            scheduling_attempt_count=data.scheduling_attempt_count,
            schedule_preview=data.schedule_preview,
            planning_context=data.planning_context,
            confirmation_note=data.confirmation_note,
            client_idempotency_key=data.idempotency_key,
        )
    except BacklogDomainError as exc:
        raise _http_error(exc) from exc
    except SchedulePlanValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return schedule_plan_response(plan)
