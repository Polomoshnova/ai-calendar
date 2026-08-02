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
)
from app.backlog.service import (
    BacklogEntryAlreadyExistsError,
    BacklogEntryNotFoundError,
    BacklogOwnershipError,
    cancel_backlog_entry,
    create_backlog_entry,
    defer_backlog_entry,
    reactivate_backlog_entry,
    resolve_backlog_entry,
)
from app.internal.dependencies import InternalToolsEnabled
from app.models import Task, User

router = APIRouter(prefix="/internal/api/backlog", tags=["internal-backlog"])


def _response(entry: object) -> BacklogEntryResponse:
    return BacklogEntryResponse.model_validate(entry)


def _http_error(exc: BacklogDomainError) -> HTTPException:
    if isinstance(exc, (BacklogEntryNotFoundError, BacklogOwnershipError)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (BacklogEntryAlreadyExistsError, InvalidBacklogTransitionError)):
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
