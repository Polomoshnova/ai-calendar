import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased, joinedload

from app.backlog.domain import OPEN_BACKLOG_STATUSES
from app.models import (
    BacklogEntry,
    CalendarConnection,
    CalendarEventMapping,
    ExternalCalendarChange,
    ExternalCalendarConsistencyFinding,
    ExternalChangeProcessingStatus,
    ScheduledSession,
    SchedulePlan,
    Task,
    User,
)
from app.planner.schemas import (
    PlannerAttentionAction,
    PlannerAttentionItem,
    PlannerAttentionType,
    PlannerBacklogItem,
    PlannerCalendarContext,
    PlannerExternalCalendarState,
    PlannerPlan,
    PlannerPlanReadiness,
    PlannerResponse,
    PlannerSession,
)
from app.schedule_plans.models import SchedulePlanStatus
from app.schedule_plans.repository import RESERVING_PLAN_STATUSES

CONFIRMED_NOT_APPLIED_STATUSES = frozenset(
    {
        SchedulePlanStatus.confirmed,
        SchedulePlanStatus.revalidation_required,
        SchedulePlanStatus.applying,
        SchedulePlanStatus.partially_applied,
    }
)
ATTENTION_PLAN_STATUSES = frozenset(
    {
        SchedulePlanStatus.revalidation_required,
        SchedulePlanStatus.partially_applied,
    }
)
PLANNER_PLAN_STATUSES = frozenset(
    {
        SchedulePlanStatus.proposed,
        *CONFIRMED_NOT_APPLIED_STATUSES,
        *ATTENTION_PLAN_STATUSES,
    }
)


class PlannerUserNotFoundError(LookupError):
    pass


class PlannerReadService:
    """Build a bounded UI projection from persisted domain state only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def read(
        self,
        *,
        user_id: uuid.UUID,
        days: int,
        now: datetime,
    ) -> PlannerResponse:
        user = self._session.get(User, user_id)
        if user is None:
            raise PlannerUserNotFoundError("User not found")

        generated_at = now.astimezone(UTC)
        timezone = ZoneInfo(user.timezone)
        local_today = generated_at.astimezone(timezone).date()
        today_start = datetime.combine(local_today, time.min, timezone).astimezone(UTC)
        tomorrow_start = datetime.combine(
            local_today + timedelta(days=1), time.min, timezone
        ).astimezone(UTC)
        upcoming_end = datetime.combine(
            local_today + timedelta(days=days + 1), time.min, timezone
        ).astimezone(UTC)

        today, upcoming = self._sessions(
            user_id=user_id,
            today_start=today_start,
            tomorrow_start=tomorrow_start,
            upcoming_end=upcoming_end,
        )
        backlog = self._backlog(user_id)
        plans, task_titles = self._plans(user_id)
        proposed = [
            self._plan(item, task_titles.get(item.id), user_id)
            for item in plans
            if item.status is SchedulePlanStatus.proposed
        ]
        confirmed_not_applied = [
            self._plan(item, task_titles.get(item.id), user_id)
            for item in plans
            if item.status in CONFIRMED_NOT_APPLIED_STATUSES
        ]
        needs_attention = self._needs_attention(
            user_id=user_id,
            plans=plans,
            task_titles=task_titles,
        )
        return PlannerResponse(
            generated_at=generated_at,
            timezone=user.timezone,
            today=today,
            upcoming=upcoming,
            backlog=backlog,
            needs_attention=needs_attention,
            proposed_plans=proposed,
            confirmed_not_applied=confirmed_not_applied,
        )

    def _sessions(
        self,
        *,
        user_id: uuid.UUID,
        today_start: datetime,
        tomorrow_start: datetime,
        upcoming_end: datetime,
    ) -> tuple[list[PlannerSession], list[PlannerSession]]:
        statement = (
            select(
                ScheduledSession,
                SchedulePlan,
                Task,
                CalendarEventMapping,
                CalendarConnection,
            )
            .join(SchedulePlan, SchedulePlan.id == ScheduledSession.plan_id)
            .outerjoin(
                Task,
                (Task.id == ScheduledSession.task_id) & (Task.user_id == user_id),
            )
            .outerjoin(
                CalendarEventMapping,
                CalendarEventMapping.scheduled_session_id == ScheduledSession.id,
            )
            .outerjoin(
                CalendarConnection,
                CalendarConnection.id == CalendarEventMapping.calendar_connection_id,
            )
            .where(
                SchedulePlan.user_id == user_id,
                SchedulePlan.status.in_(RESERVING_PLAN_STATUSES),
                ScheduledSession.start < upcoming_end,
                ScheduledSession.end > today_start,
            )
            .order_by(ScheduledSession.start, ScheduledSession.id)
        )
        today: list[PlannerSession] = []
        upcoming: list[PlannerSession] = []
        for scheduled, plan, task, mapping, connection in self._session.execute(
            statement
        ):
            item = self._session_item(
                scheduled,
                plan,
                task.id if task is not None else None,
                task.title if task is not None else None,
                mapping,
                connection,
                user_id,
            )
            if scheduled.end > today_start and scheduled.start < tomorrow_start:
                today.append(item)
            elif scheduled.start >= tomorrow_start:
                upcoming.append(item)
        return today, upcoming

    def _backlog(self, user_id: uuid.UUID) -> list[PlannerBacklogItem]:
        review_at = func.least(BacklogEntry.next_review_at, BacklogEntry.deferred_until)
        statement = (
            select(BacklogEntry, Task.title)
            .join(Task, Task.id == BacklogEntry.task_id)
            .where(
                BacklogEntry.user_id == user_id,
                Task.user_id == user_id,
                BacklogEntry.status.in_(OPEN_BACKLOG_STATUSES),
            )
            .order_by(
                review_at.asc().nulls_last(),
                BacklogEntry.entered_at,
                BacklogEntry.id,
            )
        )
        return [
            PlannerBacklogItem(
                backlog_entry_id=entry.id,
                task_id=entry.task_id,
                task_title=title,
                status=entry.status,
                origin=entry.origin,
                reason=entry.reason,
                remaining_duration_minutes=entry.remaining_duration_minutes,
                entered_at=entry.entered_at,
                next_review_at=entry.next_review_at,
                deferred_until=entry.deferred_until,
                scheduling_attempt_count=entry.scheduling_attempt_count,
                last_scheduling_attempt_at=entry.last_scheduling_attempt_at,
                note=entry.note,
            )
            for entry, title in self._session.execute(statement)
        ]

    def _plans(
        self, user_id: uuid.UUID
    ) -> tuple[list[SchedulePlan], dict[uuid.UUID, str | None]]:
        statement = (
            select(SchedulePlan, Task.title)
            .outerjoin(
                Task,
                (Task.id == SchedulePlan.task_id) & (Task.user_id == user_id),
            )
            .where(
                SchedulePlan.user_id == user_id,
                SchedulePlan.status.in_(PLANNER_PLAN_STATUSES),
            )
            .order_by(SchedulePlan.created_at.desc(), SchedulePlan.id.desc())
            .options(
                joinedload(SchedulePlan.sessions)
                .joinedload(ScheduledSession.calendar_event_mapping)
                .joinedload(CalendarEventMapping.calendar_connection)
            )
        )
        rows = self._session.execute(statement).unique().all()
        return (
            [plan for plan, _title in rows],
            {plan.id: title for plan, title in rows},
        )

    def _needs_attention(
        self,
        *,
        user_id: uuid.UUID,
        plans: list[SchedulePlan],
        task_titles: dict[uuid.UUID, str | None],
    ) -> list[PlannerAttentionItem]:
        items: list[PlannerAttentionItem] = []
        finding_session = aliased(ScheduledSession)
        finding_statement = (
            select(
                ExternalCalendarConsistencyFinding,
                Task.id,
                finding_session.id,
            )
            .join(
                SchedulePlan,
                SchedulePlan.id == ExternalCalendarConsistencyFinding.schedule_plan_id,
            )
            .outerjoin(
                Task,
                (Task.id == SchedulePlan.task_id) & (Task.user_id == user_id),
            )
            .outerjoin(
                finding_session,
                (
                    finding_session.id
                    == ExternalCalendarConsistencyFinding.scheduled_session_id
                )
                & (finding_session.plan_id == SchedulePlan.id),
            )
            .where(SchedulePlan.user_id == user_id)
        )
        for finding, task_id, session_id in self._session.execute(finding_statement):
            items.append(
                PlannerAttentionItem(
                    type=PlannerAttentionType.consistency_finding,
                    item_id=finding.id,
                    severity=finding.severity,
                    summary=(
                        "Calendar consistency issue: " + finding.code.replace("_", " ")
                    ),
                    action=PlannerAttentionAction.review_consistency,
                    task_id=task_id,
                    plan_id=finding.schedule_plan_id,
                    session_id=session_id,
                    mapping_id=None,
                    external_change_id=finding.external_calendar_change_id,
                    created_at=finding.detected_at,
                )
            )

        change_statement = (
            select(
                ExternalCalendarChange,
                CalendarEventMapping,
                ScheduledSession,
                SchedulePlan,
                Task.id,
            )
            .join(
                CalendarEventMapping,
                CalendarEventMapping.id == ExternalCalendarChange.mapping_id,
            )
            .join(
                ScheduledSession,
                ScheduledSession.id == CalendarEventMapping.scheduled_session_id,
            )
            .join(SchedulePlan, SchedulePlan.id == ScheduledSession.plan_id)
            .outerjoin(
                Task,
                (Task.id == SchedulePlan.task_id) & (Task.user_id == user_id),
            )
            .join(
                CalendarConnection,
                CalendarConnection.id == CalendarEventMapping.calendar_connection_id,
            )
            .where(
                SchedulePlan.user_id == user_id,
                CalendarConnection.user_id == user_id,
                ExternalCalendarChange.processing_status
                != ExternalChangeProcessingStatus.processed,
            )
        )
        for change, mapping, scheduled, plan, task_id in self._session.execute(
            change_statement
        ):
            items.append(
                PlannerAttentionItem(
                    type=PlannerAttentionType.external_calendar_change,
                    item_id=change.id,
                    severity=(
                        "error"
                        if change.processing_status
                        is ExternalChangeProcessingStatus.failed
                        else "warning"
                    ),
                    summary=(
                        f"External calendar {change.change_type.value} change "
                        "requires processing"
                    ),
                    action=PlannerAttentionAction.process_external_change,
                    task_id=task_id,
                    plan_id=plan.id,
                    session_id=scheduled.id,
                    mapping_id=mapping.id,
                    external_change_id=change.id,
                    created_at=change.detected_at,
                )
            )

        for plan in plans:
            if plan.status not in ATTENTION_PLAN_STATUSES:
                continue
            action, severity, summary = _plan_attention(plan.status)
            items.append(
                PlannerAttentionItem(
                    type=PlannerAttentionType.schedule_plan,
                    item_id=plan.id,
                    severity=severity,
                    summary=(
                        f"{task_titles.get(plan.id) or _task_title(plan)}: {summary}"
                    ),
                    action=action,
                    task_id=(
                        plan.task_id if task_titles.get(plan.id) is not None else None
                    ),
                    plan_id=plan.id,
                    session_id=None,
                    mapping_id=None,
                    external_change_id=None,
                    created_at=plan.updated_at,
                )
            )
        severity_rank = {"error": 3, "warning": 2, "info": 1}
        items.sort(
            key=lambda item: (
                -severity_rank.get(item.severity or "info", 1),
                -item.created_at.timestamp(),
                item.type.value,
                str(item.item_id),
            )
        )
        return items

    def _plan(
        self,
        plan: SchedulePlan,
        task_title: str | None,
        user_id: uuid.UUID,
    ) -> PlannerPlan:
        sessions = [
            self._session_item(
                scheduled,
                plan,
                plan.task_id if task_title is not None else None,
                task_title,
                scheduled.calendar_event_mapping,
                (
                    scheduled.calendar_event_mapping.calendar_connection
                    if scheduled.calendar_event_mapping is not None
                    else None
                ),
                user_id,
            )
            for scheduled in sorted(
                plan.sessions, key=lambda item: (item.order, item.id)
            )
        ]
        return PlannerPlan(
            plan_id=plan.id,
            task_id=plan.task_id if task_title is not None else None,
            task_title=task_title or _task_title(plan),
            backlog_entry_id=plan.backlog_entry_id,
            status=plan.status,
            readiness=_readiness(plan.status),
            sessions=sessions,
            total_scheduled_minutes=sum(
                item.duration_minutes for item in plan.sessions
            ),
            mapped_session_count=sum(
                _mapping_owned_by(item.calendar_event_mapping, user_id)
                for item in plan.sessions
            ),
            total_session_count=len(plan.sessions),
            revalidation_required=(
                plan.status is SchedulePlanStatus.revalidation_required
            ),
            created_at=plan.created_at,
            updated_at=plan.updated_at,
            confirmed_at=plan.confirmed_at,
            applied_at=plan.applied_at,
            calendar_context=_calendar_context(plan),
        )

    @staticmethod
    def _session_item(
        scheduled: ScheduledSession,
        plan: SchedulePlan,
        task_id: uuid.UUID | None,
        task_title: str | None,
        mapping: CalendarEventMapping | None,
        connection: CalendarConnection | None,
        user_id: uuid.UUID,
    ) -> PlannerSession:
        external: PlannerExternalCalendarState | None = None
        if (
            mapping is not None
            and connection is not None
            and connection.user_id == user_id
        ):
            external = PlannerExternalCalendarState(
                mapping_id=mapping.id,
                sync_status=mapping.sync_status,
                connection_id=mapping.calendar_connection_id,
                provider=mapping.provider.value,
                provider_account_id=mapping.provider_account_id,
                calendar_id=mapping.calendar_id,
                external_event_id=mapping.external_event_id,
                last_synced_at=mapping.last_synced_at,
                sync_error_code=mapping.sync_error_code,
            )
        return PlannerSession(
            session_id=scheduled.id,
            task_id=task_id,
            task_title=task_title or scheduled.title,
            start=scheduled.start,
            end=scheduled.end,
            timezone=plan.timezone,
            plan_id=plan.id,
            plan_status=plan.status,
            session_status=scheduled.status,
            external_calendar=external,
        )


def _task_title(plan: SchedulePlan) -> str:
    value = plan.confirmed_task_snapshot.get("title")
    return str(value) if value else "Untitled task"


def _mapping_owned_by(mapping: CalendarEventMapping | None, user_id: uuid.UUID) -> bool:
    return (
        mapping is not None
        and mapping.calendar_connection is not None
        and mapping.calendar_connection.user_id == user_id
    )


def _calendar_context(plan: SchedulePlan) -> PlannerCalendarContext:
    busy_sources = plan.busy_sources_snapshot or []
    connection_ids = sorted(
        {
            uuid.UUID(str(item["connection_id"]))
            for item in busy_sources
            if item.get("connection_id")
        },
        key=str,
    )
    calendar_ids = sorted(
        {str(item["calendar_id"]) for item in busy_sources if item.get("calendar_id")}
    )
    provider = plan.busy_context_summary.get("provider")
    return PlannerCalendarContext(
        provider=str(provider) if provider else None,
        connection_ids=connection_ids,
        calendar_ids=calendar_ids,
        captured_at=plan.calendar_context_captured_at,
        selection_hash=plan.calendar_selection_hash,
    )


def _readiness(status: SchedulePlanStatus) -> PlannerPlanReadiness:
    return {
        SchedulePlanStatus.proposed: PlannerPlanReadiness.awaiting_confirmation,
        SchedulePlanStatus.confirmed: PlannerPlanReadiness.ready_to_apply,
        SchedulePlanStatus.revalidation_required: (
            PlannerPlanReadiness.needs_revalidation
        ),
        SchedulePlanStatus.applying: PlannerPlanReadiness.apply_in_progress,
        SchedulePlanStatus.partially_applied: PlannerPlanReadiness.partially_applied,
    }[status]


def _plan_attention(
    status: SchedulePlanStatus,
) -> tuple[PlannerAttentionAction, str, str]:
    if status is SchedulePlanStatus.revalidation_required:
        return (
            PlannerAttentionAction.revalidate_plan,
            "warning",
            "schedule plan needs revalidation",
        )
    if status is not SchedulePlanStatus.partially_applied:
        raise ValueError(f"unsupported attention plan status: {status}")
    return (
        PlannerAttentionAction.retry_apply,
        "error",
        "schedule plan was only partially applied",
    )
