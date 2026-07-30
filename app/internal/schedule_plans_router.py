import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.calendar_integration.models import CalendarBusyResult
from app.calendar_integration.service import query_connection_busy
from app.core.config import Settings, get_settings
from app.internal.calendar_router import CalendarRuntimeDependency
from app.internal.dependencies import InternalToolsEnabled
from app.models import CalendarConnection, User
from app.schedule_plans.errors import (
    InvalidPlanTransitionError,
    SchedulePlanNotFoundError,
    SchedulePlanUserNotFoundError,
    SchedulePlanValidationError,
)
from app.schedule_plans.models import SchedulePlan, SchedulePlanStatus
from app.schedule_plans.repository import get_schedule_plan, list_schedule_plans
from app.schedule_plans.revalidation import (
    PlanChangedDuringRevalidationError,
    RevalidationConnectionError,
    history_results,
    list_revalidations,
    revalidate_schedule_plan,
)
from app.schedule_plans.revalidation_models import (
    SchedulePlanRevalidationStatus,
)
from app.schedule_plans.revalidation_schemas import (
    SchedulePlanRevalidationHistoryResponse,
    SchedulePlanRevalidationRequest,
    SchedulePlanRevalidationResult,
)
from app.schedule_plans.schemas import (
    PlanConfirmationRequest,
    ScheduledSessionResponse,
    SchedulePlanCreateRequest,
    SchedulePlanListResponse,
    SchedulePlanResponse,
)
from app.schedule_plans.service import (
    confirm_schedule_plan,
    create_schedule_plan_from_preview,
    obsolete_schedule_plan,
)

router = APIRouter(
    prefix="/internal/api",
    tags=["internal-schedule-plans"],
)


def _response(plan: SchedulePlan) -> SchedulePlanResponse:
    return SchedulePlanResponse(
        id=plan.id,
        user_id=plan.user_id,
        task_id=plan.task_id,
        plan_group_id=plan.plan_group_id,
        version=plan.version,
        source=plan.source,
        status=plan.status,
        timezone=plan.timezone,
        planning_window_start=plan.planning_window_start,
        planning_window_end=plan.planning_window_end,
        source_calendar_snapshot_at=plan.source_calendar_snapshot_at,
        scheduler_version=plan.scheduler_version,
        workflow_version=plan.workflow_version,
        sessions=[
            ScheduledSessionResponse(
                id=item.id,
                plan_id=item.plan_id,
                task_id=item.task_id,
                step_order=item.step_order,
                title=item.title,
                description=item.description,
                start=item.start,
                end=item.end,
                duration_minutes=item.duration_minutes,
                order=item.order,
                status=item.status,
                external_provider=item.external_provider,
                external_calendar_id=item.external_calendar_id,
                external_event_id=item.external_event_id,
                failure_code=item.failure_code,
            )
            for item in plan.sessions
        ],
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        confirmed_at=plan.confirmed_at,
        applied_at=plan.applied_at,
        confirmation_note=plan.confirmation_note,
        failure_code=plan.failure_code,
    )


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (SchedulePlanNotFoundError, SchedulePlanUserNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, InvalidPlanTransitionError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, SchedulePlanValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Unexpected schedule plan failure")


@router.post(
    "/schedule-plans/from-preview",
    response_model=SchedulePlanResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_from_preview(
    data: SchedulePlanCreateRequest,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> SchedulePlanResponse:
    try:
        plan = create_schedule_plan_from_preview(
            session,
            user_id=data.user_id,
            task_id=data.task_id,
            plan_group_id=data.plan_group_id,
            confirmed_task=data.confirmed_task,
            schedule_preview=data.schedule_preview,
            planning_context=data.planning_context,
            source=data.source,
            confirmation_note=data.confirmation_note,
            idempotency_key=data.idempotency_key,
        )
    except (
        InvalidPlanTransitionError,
        SchedulePlanUserNotFoundError,
        SchedulePlanValidationError,
    ) as exc:
        raise _http_error(exc) from exc
    return _response(plan)


@router.get(
    "/schedule-plans/{plan_id}",
    response_model=SchedulePlanResponse,
)
def read_plan(
    plan_id: uuid.UUID,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> SchedulePlanResponse:
    plan = get_schedule_plan(session, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Schedule plan not found")
    return _response(plan)


@router.get(
    "/users/{user_id}/schedule-plans",
    response_model=SchedulePlanListResponse,
)
def read_user_plans(
    user_id: uuid.UUID,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
    plan_status: Annotated[SchedulePlanStatus | None, Query(alias="status")] = None,
    task_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SchedulePlanListResponse:
    if session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    return SchedulePlanListResponse(
        plans=[
            _response(plan)
            for plan in list_schedule_plans(
                session,
                user_id=user_id,
                status=plan_status,
                task_id=task_id,
                limit=limit,
                offset=offset,
            )
        ]
    )


@router.post(
    "/schedule-plans/{plan_id}/confirm",
    response_model=SchedulePlanResponse,
)
def confirm_plan(
    plan_id: uuid.UUID,
    data: PlanConfirmationRequest,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> SchedulePlanResponse:
    try:
        return _response(
            confirm_schedule_plan(
                session,
                plan_id,
                confirmation_note=data.confirmation_note,
            )
        )
    except (InvalidPlanTransitionError, SchedulePlanNotFoundError) as exc:
        raise _http_error(exc) from exc


@router.post(
    "/schedule-plans/{plan_id}/obsolete",
    response_model=SchedulePlanResponse,
)
def obsolete_plan(
    plan_id: uuid.UUID,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> SchedulePlanResponse:
    try:
        return _response(obsolete_schedule_plan(session, plan_id))
    except (InvalidPlanTransitionError, SchedulePlanNotFoundError) as exc:
        raise _http_error(exc) from exc


@router.post(
    "/schedule-plans/{plan_id}/revalidate",
    response_model=SchedulePlanRevalidationResult,
)
async def revalidate_plan(
    plan_id: uuid.UUID,
    data: SchedulePlanRevalidationRequest,
    session: DatabaseSession,
    runtime: CalendarRuntimeDependency,
    _enabled: InternalToolsEnabled,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SchedulePlanRevalidationResult:
    async def query_busy(
        connection: CalendarConnection,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
        timezone: str,
    ) -> tuple[list[str], CalendarBusyResult]:
        return await query_connection_busy(
            session,
            connection,
            calendar_ids=calendar_ids,
            time_min=time_min,
            time_max=time_max,
            timezone=timezone,
            provider=runtime.provider,
            oauth_client=runtime.oauth,
            cipher=runtime.cipher,
        )

    try:
        return await revalidate_schedule_plan(
            session,
            plan_id=plan_id,
            connection_id=data.connection_id,
            calendar_ids=data.calendar_ids,
            include_internal_busy=data.include_internal_busy,
            minimum_break_minutes=data.minimum_break_minutes,
            request_id=data.request_id,
            query_busy=query_busy,
            ttl_seconds=settings.schedule_plan_revalidation_ttl_seconds,
            padding_minutes=(settings.schedule_plan_revalidation_padding_minutes),
        )
    except SchedulePlanNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        InvalidPlanTransitionError,
        PlanChangedDuringRevalidationError,
        RevalidationConnectionError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SchedulePlanValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/schedule-plans/{plan_id}/revalidations",
    response_model=SchedulePlanRevalidationHistoryResponse,
)
def revalidation_history(
    plan_id: uuid.UUID,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
    revalidation_status: Annotated[
        SchedulePlanRevalidationStatus | None, Query(alias="status")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SchedulePlanRevalidationHistoryResponse:
    if get_schedule_plan(session, plan_id) is None:
        raise HTTPException(status_code=404, detail="Schedule plan not found")
    return SchedulePlanRevalidationHistoryResponse(
        revalidations=history_results(
            list_revalidations(
                session,
                plan_id=plan_id,
                status=revalidation_status,
                limit=limit,
                offset=offset,
            )
        )
    )
