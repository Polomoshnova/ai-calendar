import hashlib
import json
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.calendar_integration import (
    CalendarBusyInterval,
    CalendarBusyResult,
    normalize_calendar_busy_intervals,
)
from app.calendar_integration.errors import (
    CalendarIntegrationError,
    CalendarRateLimitError,
    CalendarReconnectRequiredError,
    CalendarUnavailableError,
)
from app.models import CalendarConnection, CalendarConnectionStatus
from app.schedule_plans.errors import (
    InvalidPlanTransitionError,
    SchedulePlanNotFoundError,
    SchedulePlanValidationError,
)
from app.schedule_plans.models import SchedulePlan, SchedulePlanStatus
from app.schedule_plans.repository import get_schedule_plan, list_reserved_intervals
from app.schedule_plans.revalidation_models import (
    SchedulePlanRevalidation,
    SchedulePlanRevalidationStatus,
)
from app.schedule_plans.revalidation_schemas import (
    BusyIntervalReference,
    BusyIntervalSource,
    SchedulePlanConflict,
    SchedulePlanConflictType,
    SchedulePlanRevalidationResult,
)
from app.schedule_plans.service import transition_schedule_plan

BusyQuery = Callable[
    [
        CalendarConnection,
        list[str],
        datetime,
        datetime,
        str,
    ],
    Awaitable[tuple[list[str], CalendarBusyResult]],
]

ELIGIBLE_REVALIDATION_STATUSES = {
    SchedulePlanStatus.confirmed,
    SchedulePlanStatus.revalidation_required,
}


class RevalidationConnectionError(SchedulePlanValidationError):
    pass


class PlanChangedDuringRevalidationError(InvalidPlanTransitionError):
    pass


def sessions_hash(plan: SchedulePlan) -> str:
    payload = {
        "plan_version": plan.version,
        "sessions": [
            {
                "id": str(item.id),
                "order": item.order,
                "start": item.start.astimezone(UTC).isoformat(),
                "end": item.end.astimezone(UTC).isoformat(),
                "duration_minutes": item.duration_minutes,
            }
            for item in sorted(plan.sessions, key=lambda value: value.order)
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _safe_provider_failure(exc: CalendarIntegrationError) -> tuple[str, str]:
    if isinstance(exc, CalendarReconnectRequiredError):
        return "calendar_reconnect_required", "Calendar reconnection is required."
    if isinstance(exc, CalendarRateLimitError):
        return "rate_limited", "Calendar provider rate limit was reached."
    if isinstance(exc, CalendarUnavailableError):
        return "provider_unavailable", "Calendar provider is unavailable."
    return "provider_failure", "Calendar FreeBusy query failed."


def _safe_calendar_error(reason: str) -> str:
    mapping = {
        "notFound": "calendar_not_found",
        "forbidden": "calendar_access_denied",
        "rateLimitExceeded": "rate_limited",
        "userRateLimitExceeded": "rate_limited",
    }
    return mapping.get(reason, "provider_calendar_error")


def _resolve_calendar_ids(
    plan: SchedulePlan,
    connection: CalendarConnection,
    requested: list[str] | None,
) -> list[str]:
    original_provider = plan.busy_context_summary.get("provider")
    if (
        original_provider is not None
        and str(original_provider) != connection.provider.value
    ):
        raise RevalidationConnectionError(
            "Calendar provider differs from the plan snapshot"
        )
    original = plan.busy_context_summary.get("calendar_ids", [])
    original_ids = (
        [str(item) for item in original] if isinstance(original, list) else []
    )
    known_ids = {item.external_calendar_id for item in connection.selections}
    current_ids = [
        item.external_calendar_id
        for item in connection.selections
        if item.include_in_availability
    ]
    if set(original_ids) - known_ids:
        raise RevalidationConnectionError(
            "Original calendar selection is no longer available"
        )
    effective = original_ids or current_ids
    if not effective:
        raise RevalidationConnectionError(
            "No compatible calendar selection is available"
        )
    if requested is not None and requested != effective:
        raise RevalidationConnectionError(
            "Requested calendar selection differs from the plan snapshot"
        )
    return effective


def _minimum_break_minutes(
    plan: SchedulePlan,
    requested: int | None,
) -> int:
    if requested is not None:
        return requested
    stored = plan.scheduling_preferences_snapshot.get("minimum_break_minutes", 0)
    return stored if isinstance(stored, int) and stored >= 0 else 0


def _query_window(
    plan: SchedulePlan,
    *,
    padding_minutes: int,
    minimum_break_minutes: int,
) -> tuple[datetime, datetime]:
    if not plan.sessions:
        raise SchedulePlanValidationError("Schedule plan has no sessions")
    padding = timedelta(minutes=max(padding_minutes, minimum_break_minutes))
    start = min(item.start for item in plan.sessions).astimezone(UTC) - padding
    end = max(item.end for item in plan.sessions).astimezone(UTC) + padding
    return start, end


@dataclass(frozen=True)
class SourcedBusyInterval:
    interval: CalendarBusyInterval
    provider: str
    source: BusyIntervalSource


def _interval_reference(sourced: SourcedBusyInterval) -> BusyIntervalReference:
    interval = sourced.interval
    return BusyIntervalReference(
        start=interval.start.astimezone(UTC),
        end=interval.end.astimezone(UTC),
        calendar_id=interval.calendar_id,
        provider=sourced.provider,
        source=sourced.source,
    )


def _detect_sourced_conflicts(
    plan: SchedulePlan,
    intervals: list[SourcedBusyInterval],
    *,
    minimum_break_minutes: int,
) -> list[SchedulePlanConflict]:
    conflicts: list[SchedulePlanConflict] = []
    minimum_break = timedelta(minutes=minimum_break_minutes)
    for scheduled_session in sorted(plan.sessions, key=lambda item: item.order):
        session_start = scheduled_session.start.astimezone(UTC)
        session_end = scheduled_session.end.astimezone(UTC)
        overlaps: dict[tuple[str, BusyIntervalSource], list[SourcedBusyInterval]] = {}
        break_violations: dict[
            tuple[str, BusyIntervalSource], list[SourcedBusyInterval]
        ] = {}
        overlap_seconds: dict[tuple[str, BusyIntervalSource], float] = {}
        for sourced in intervals:
            interval = sourced.interval
            key = (sourced.provider, sourced.source)
            busy_start = interval.start.astimezone(UTC)
            busy_end = interval.end.astimezone(UTC)
            if session_start < busy_end and busy_start < session_end:
                overlaps.setdefault(key, []).append(sourced)
                overlap_seconds[key] = (
                    overlap_seconds.get(key, 0.0)
                    + (
                        min(session_end, busy_end) - max(session_start, busy_start)
                    ).total_seconds()
                )
                continue
            if minimum_break_minutes <= 0:
                continue
            gap: timedelta | None = None
            if session_end <= busy_start:
                gap = busy_start - session_end
            elif busy_end <= session_start:
                gap = session_start - busy_end
            if gap is not None and gap < minimum_break:
                break_violations.setdefault(key, []).append(sourced)
        for key, matching in overlaps.items():
            source = key[1]
            conflicts.append(
                SchedulePlanConflict(
                    session_id=scheduled_session.id,
                    session_order=scheduled_session.order,
                    session_start=session_start,
                    session_end=session_end,
                    conflicting_busy_intervals=[
                        _interval_reference(item) for item in matching
                    ],
                    conflict_type=SchedulePlanConflictType.direct_overlap,
                    overlap_minutes=max(1, math.ceil(overlap_seconds[key] / 60)),
                    reason_code=(
                        "session_overlaps_reserved_plan"
                        if source is BusyIntervalSource.internal_busy
                        else "session_overlaps_provider_busy"
                    ),
                )
            )
        for matching in break_violations.values():
            conflicts.append(
                SchedulePlanConflict(
                    session_id=scheduled_session.id,
                    session_order=scheduled_session.order,
                    session_start=session_start,
                    session_end=session_end,
                    conflicting_busy_intervals=[
                        _interval_reference(item) for item in matching
                    ],
                    conflict_type=(SchedulePlanConflictType.minimum_break_violation),
                    overlap_minutes=0,
                    reason_code="minimum_break_violation",
                )
            )
    return conflicts


def detect_conflicts(
    plan: SchedulePlan,
    intervals: list[CalendarBusyInterval],
    *,
    provider: str,
    minimum_break_minutes: int,
) -> list[SchedulePlanConflict]:
    return _detect_sourced_conflicts(
        plan,
        [
            SourcedBusyInterval(
                interval=item,
                provider=provider,
                source=BusyIntervalSource.provider_busy,
            )
            for item in intervals
        ],
        minimum_break_minutes=minimum_break_minutes,
    )


def _result_from_record(
    record: SchedulePlanRevalidation,
) -> SchedulePlanRevalidationResult:
    diagnostics = record.diagnostics
    plan_status_before = SchedulePlanStatus(diagnostics["plan_status_before"])
    plan_status_after = SchedulePlanStatus(diagnostics["plan_status_after"])
    conflicts = [
        SchedulePlanConflict.model_validate(item)
        for item in diagnostics.get("conflicts", [])
    ]
    return SchedulePlanRevalidationResult(
        revalidation_id=record.id,
        plan_id=record.plan_id,
        plan_status_before=plan_status_before,
        plan_status_after=plan_status_after,
        result=record.status,
        checked_at=record.checked_at,
        valid_until=record.valid_until,
        sessions_hash=record.sessions_hash,
        planning_window_start=record.planning_window_start,
        planning_window_end=record.planning_window_end,
        provider=record.provider,
        connection_id=record.connection_id,
        queried_calendar_ids=[
            str(item) for item in diagnostics.get("queried_calendar_ids", [])
        ],
        provider_busy_interval_count=record.provider_busy_interval_count,
        merged_busy_interval_count=record.merged_busy_interval_count,
        conflicting_session_count=record.conflicting_session_count,
        conflicts=conflicts,
        diagnostics=diagnostics,
        can_apply=bool(diagnostics.get("can_apply", False)),
        failure_code=record.failure_code,
    )


def get_revalidation_by_request_id(
    session: Session,
    plan_id: uuid.UUID,
    request_id: str,
) -> SchedulePlanRevalidation | None:
    return session.scalar(
        select(SchedulePlanRevalidation).where(
            SchedulePlanRevalidation.plan_id == plan_id,
            SchedulePlanRevalidation.request_id == request_id,
        )
    )


def list_revalidations(
    session: Session,
    *,
    plan_id: uuid.UUID,
    status: SchedulePlanRevalidationStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SchedulePlanRevalidation]:
    statement = (
        select(SchedulePlanRevalidation)
        .where(SchedulePlanRevalidation.plan_id == plan_id)
        .order_by(
            SchedulePlanRevalidation.checked_at.desc(),
            SchedulePlanRevalidation.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        statement = statement.where(SchedulePlanRevalidation.status == status)
    return list(session.scalars(statement))


def history_results(
    records: list[SchedulePlanRevalidation],
) -> list[SchedulePlanRevalidationResult]:
    return [_result_from_record(item) for item in records]


async def revalidate_schedule_plan(
    session: Session,
    *,
    plan_id: uuid.UUID,
    connection_id: uuid.UUID,
    calendar_ids: list[str] | None,
    include_internal_busy: bool,
    minimum_break_minutes: int | None,
    request_id: str | None,
    query_busy: BusyQuery,
    ttl_seconds: int = 120,
    padding_minutes: int = 15,
    now: datetime | None = None,
) -> SchedulePlanRevalidationResult:
    if request_id is not None:
        existing = get_revalidation_by_request_id(session, plan_id, request_id)
        if existing is not None:
            return _result_from_record(existing)
    plan = get_schedule_plan(session, plan_id)
    if plan is None:
        raise SchedulePlanNotFoundError("Schedule plan not found")
    if plan.status not in ELIGIBLE_REVALIDATION_STATUSES:
        raise InvalidPlanTransitionError(
            f"cannot revalidate plan in {plan.status.value} status"
        )
    connection = session.scalar(
        select(CalendarConnection)
        .where(CalendarConnection.id == connection_id)
        .options(selectinload(CalendarConnection.selections))
        .execution_options(populate_existing=True)
    )
    if connection is None:
        raise SchedulePlanNotFoundError("Calendar connection not found")
    if connection.user_id != plan.user_id:
        raise RevalidationConnectionError(
            "Calendar connection does not belong to the plan user"
        )
    if connection.status is not CalendarConnectionStatus.active:
        raise RevalidationConnectionError("Calendar connection is not active")
    resolved_ids = _resolve_calendar_ids(plan, connection, calendar_ids)
    break_minutes = _minimum_break_minutes(plan, minimum_break_minutes)
    query_start, query_end = _query_window(
        plan,
        padding_minutes=padding_minutes,
        minimum_break_minutes=break_minutes,
    )
    reserved_intervals = (
        list_reserved_intervals(
            session,
            user_id=plan.user_id,
            start=query_start,
            end=query_end,
            exclude_plan_id=plan.id,
        )
        if include_internal_busy
        else []
    )
    expected_version = plan.version
    expected_updated_at = plan.updated_at
    expected_hash = sessions_hash(plan)
    status_before = plan.status
    checked_at = now or datetime.now(UTC)
    query_started = time.perf_counter()
    provider_result: CalendarBusyResult | None = None
    provider_failure: tuple[str, str] | None = None
    try:
        queried_ids, provider_result = await query_busy(
            connection,
            resolved_ids,
            query_start,
            query_end,
            plan.timezone,
        )
    except CalendarIntegrationError as exc:
        queried_ids = resolved_ids
        provider_failure = _safe_provider_failure(exc)
    provider_query_duration_ms = round((time.perf_counter() - query_started) * 1000, 3)

    session.expire_all()
    current = get_schedule_plan(session, plan_id, for_update=True)
    if current is None:
        raise SchedulePlanNotFoundError("Schedule plan not found")
    if (
        current.status not in ELIGIBLE_REVALIDATION_STATUSES
        or current.version != expected_version
        or current.updated_at != expected_updated_at
        or sessions_hash(current) != expected_hash
    ):
        session.rollback()
        raise PlanChangedDuringRevalidationError("plan_changed_during_revalidation")

    detection_started = time.perf_counter()
    intervals = provider_result.intervals if provider_result is not None else []
    merged = (
        normalize_calendar_busy_intervals(provider_result)
        if provider_result is not None
        else []
    )
    sourced_intervals = [
        SourcedBusyInterval(
            interval=item,
            provider=connection.provider.value,
            source=BusyIntervalSource.provider_busy,
        )
        for item in intervals
    ]
    sourced_intervals.extend(
        SourcedBusyInterval(
            interval=CalendarBusyInterval(
                start=item.start,
                end=item.end,
                calendar_id=f"reserved-plan:{item.plan_id}",
            ),
            provider="internal",
            source=BusyIntervalSource.internal_busy,
        )
        for item in reserved_intervals
    )
    conflicts = _detect_sourced_conflicts(
        current,
        sourced_intervals,
        minimum_break_minutes=break_minutes,
    )
    conflict_detection_duration_ms = round(
        (time.perf_counter() - detection_started) * 1000, 3
    )
    failed_calendar_codes: dict[str, str] = {}
    if provider_result is not None:
        failed_calendar_codes = {
            error.calendar_id: _safe_calendar_error(error.reason)
            for error in provider_result.errors
        }

    if provider_failure is not None:
        result_status = SchedulePlanRevalidationStatus.provider_failure
        failure_code, failure_message = provider_failure
    elif failed_calendar_codes:
        result_status = SchedulePlanRevalidationStatus.provider_partial_failure
        failure_code = "provider_partial_failure"
        failure_message = "One or more required calendars could not be queried."
        if current.status is SchedulePlanStatus.confirmed:
            transition_schedule_plan(current, SchedulePlanStatus.revalidation_required)
    elif conflicts:
        result_status = SchedulePlanRevalidationStatus.conflict
        failure_code = None
        failure_message = None
        if current.status is SchedulePlanStatus.confirmed:
            transition_schedule_plan(current, SchedulePlanStatus.revalidation_required)
    else:
        result_status = SchedulePlanRevalidationStatus.valid
        failure_code = None
        failure_message = None
        if current.status is SchedulePlanStatus.revalidation_required:
            transition_schedule_plan(current, SchedulePlanStatus.confirmed)

    status_after = current.status
    can_apply = (
        result_status is SchedulePlanRevalidationStatus.valid
        and status_after is SchedulePlanStatus.confirmed
        and connection.status is CalendarConnectionStatus.active
    )
    valid_until = checked_at + timedelta(seconds=ttl_seconds) if can_apply else None
    session.flush()
    snapshot_age_seconds = (
        max(
            0,
            int((checked_at - current.source_calendar_snapshot_at).total_seconds()),
        )
        if current.source_calendar_snapshot_at is not None
        else None
    )
    conflicting_session_ids = {conflict.session_id for conflict in conflicts}
    warnings: list[str] = []
    diagnostics: dict[str, Any] = {
        "plan_status_before": status_before.value,
        "plan_status_after": status_after.value,
        "original_snapshot_at": (
            current.source_calendar_snapshot_at.isoformat()
            if current.source_calendar_snapshot_at is not None
            else None
        ),
        "checked_at": checked_at.isoformat(),
        "snapshot_age_seconds": snapshot_age_seconds,
        "queried_calendar_ids": queried_ids,
        "queried_calendar_count": len(queried_ids),
        "successful_calendar_count": (len(queried_ids) - len(failed_calendar_codes)),
        "failed_calendar_count": len(failed_calendar_codes),
        "failed_calendars": failed_calendar_codes,
        "provider_query_duration_ms": provider_query_duration_ms,
        "conflict_detection_duration_ms": conflict_detection_duration_ms,
        "include_internal_busy": include_internal_busy,
        "minimum_break_minutes": break_minutes,
        "reserved_interval_count": len(reserved_intervals),
        "combined_busy_interval_count": len(sourced_intervals),
        "warnings": warnings,
        "failure_codes": (
            sorted(set(failed_calendar_codes.values()))
            if failed_calendar_codes
            else ([failure_code] if failure_code is not None else [])
        ),
        "conflicts": [item.model_dump(mode="json") for item in conflicts],
        "can_apply": can_apply,
    }
    record = SchedulePlanRevalidation(
        plan_id=current.id,
        user_id=current.user_id,
        connection_id=connection.id,
        provider=connection.provider.value,
        status=result_status,
        checked_at=checked_at,
        planning_window_start=query_start,
        planning_window_end=query_end,
        source_snapshot_at=current.source_calendar_snapshot_at,
        provider_busy_interval_count=len(intervals),
        merged_busy_interval_count=len(merged),
        conflicting_session_count=len(conflicting_session_ids),
        diagnostics=diagnostics,
        provider_request_id=None,
        request_id=request_id,
        failure_code=failure_code,
        failure_message=failure_message,
        plan_version=current.version,
        plan_updated_at=current.updated_at,
        sessions_hash=expected_hash,
        valid_until=valid_until,
    )
    session.add(record)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if request_id is None:
            raise
        existing = get_revalidation_by_request_id(session, plan_id, request_id)
        if existing is None:
            raise
        return _result_from_record(existing)
    session.refresh(record)
    return _result_from_record(record)
