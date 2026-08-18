import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base
from app.schedule_plans.errors import SchedulePlanImmutableError

if TYPE_CHECKING:
    from app.models.backlog import BacklogEntry
    from app.models.calendar_sync import CalendarEventMapping
    from app.schedule_plans.revalidation_models import SchedulePlanRevalidation


class SchedulePlanSource(StrEnum):
    manual_preview = "manual_preview"
    ai_workflow = "ai_workflow"
    calendar_backed_preview = "calendar_backed_preview"


class SchedulePlanStatus(StrEnum):
    proposed = "proposed"
    confirmed = "confirmed"
    obsolete = "obsolete"
    revalidation_required = "revalidation_required"
    applying = "applying"
    applied = "applied"
    partially_applied = "partially_applied"
    failed = "failed"


class ScheduledSessionStatus(StrEnum):
    proposed = "proposed"
    confirmed = "confirmed"
    applying = "applying"
    applied = "applied"
    failed = "failed"
    obsolete = "obsolete"


class SchedulePlan(Base):
    __tablename__ = "schedule_plans"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_schedule_plans_positive_version"),
        CheckConstraint(
            "planning_window_start < planning_window_end",
            name="ck_schedule_plans_valid_window",
        ),
        UniqueConstraint(
            "plan_group_id",
            "version",
            name="uq_schedule_plans_group_version",
        ),
        UniqueConstraint("idempotency_key", name="uq_schedule_plans_idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    backlog_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backlog_entries.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    source: Mapped[SchedulePlanSource] = mapped_column(
        Enum(SchedulePlanSource, name="schedule_plan_source"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[SchedulePlanStatus] = mapped_column(
        Enum(SchedulePlanStatus, name="schedule_plan_status"),
        nullable=False,
        default=SchedulePlanStatus.proposed,
        index=True,
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    planning_window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    planning_window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_calendar_snapshot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduler_version: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confirmation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    confirmed_task_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    scheduling_preferences_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    busy_context_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    preview_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    busy_sources_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    write_targets_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    calendar_selection_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    calendar_context_captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sessions: Mapped[list["ScheduledSession"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="ScheduledSession.order",
    )
    backlog_entry: Mapped["BacklogEntry | None"] = relationship(
        back_populates="schedule_plans"
    )
    revalidations: Mapped[list["SchedulePlanRevalidation"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
    )


class ScheduledSession(Base):
    __tablename__ = "scheduled_sessions"
    __table_args__ = (
        CheckConstraint('start < "end"', name="ck_scheduled_sessions_valid_interval"),
        CheckConstraint(
            "duration_minutes > 0",
            name="ck_scheduled_sessions_positive_duration",
        ),
        CheckConstraint('"order" > 0', name="ck_scheduled_sessions_positive_order"),
        CheckConstraint(
            "\"end\" - start = duration_minutes * interval '1 minute'",
            name="ck_scheduled_sessions_duration_matches_interval",
        ),
        UniqueConstraint(
            "plan_id",
            "order",
            name="uq_scheduled_sessions_plan_order",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schedule_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    step_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ScheduledSessionStatus] = mapped_column(
        Enum(ScheduledSessionStatus, name="scheduled_session_status"),
        nullable=False,
        default=ScheduledSessionStatus.proposed,
        index=True,
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    plan: Mapped[SchedulePlan] = relationship(back_populates="sessions")
    calendar_event_mapping: Mapped["CalendarEventMapping | None"] = relationship(
        back_populates="scheduled_session",
        cascade="all, delete-orphan",
        uselist=False,
    )

    @property
    def external_provider(self) -> str | None:
        mapping = self.calendar_event_mapping
        return mapping.provider.value if mapping is not None else None

    @property
    def external_calendar_id(self) -> str | None:
        mapping = self.calendar_event_mapping
        return mapping.calendar_id if mapping is not None else None

    @property
    def external_event_id(self) -> str | None:
        mapping = self.calendar_event_mapping
        return mapping.external_event_id if mapping is not None else None

    @property
    def provider_etag(self) -> str | None:
        mapping = self.calendar_event_mapping
        return mapping.etag if mapping is not None else None


_IMMUTABLE_PLAN_FIELDS = {
    "user_id",
    "task_id",
    "plan_group_id",
    "source",
    "version",
    "timezone",
    "planning_window_start",
    "planning_window_end",
    "source_calendar_snapshot_at",
    "scheduler_version",
    "workflow_version",
    "idempotency_key",
    "confirmed_task_snapshot",
    "scheduling_preferences_snapshot",
    "busy_context_summary",
    "preview_metadata",
    "busy_sources_snapshot",
    "write_targets_snapshot",
    "calendar_selection_hash",
    "calendar_context_captured_at",
}
_IMMUTABLE_SESSION_FIELDS = {
    "plan_id",
    "task_id",
    "step_order",
    "title",
    "description",
    "start",
    "end",
    "duration_minutes",
    "order",
}
_IMMUTABLE_PLAN_STATUSES = {
    SchedulePlanStatus.confirmed,
    SchedulePlanStatus.obsolete,
    SchedulePlanStatus.revalidation_required,
    SchedulePlanStatus.applying,
    SchedulePlanStatus.applied,
    SchedulePlanStatus.partially_applied,
    SchedulePlanStatus.failed,
}


def _original_plan_status(plan: SchedulePlan) -> SchedulePlanStatus:
    history = inspect(plan).attrs.status.history
    if history.deleted:
        return SchedulePlanStatus(history.deleted[0])
    return plan.status


@event.listens_for(Session, "before_flush")
def _protect_confirmed_plan_content(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    for item in session.dirty:
        if isinstance(item, SchedulePlan):
            if _original_plan_status(item) not in _IMMUTABLE_PLAN_STATUSES:
                continue
            state = inspect(item)
            if any(
                state.attrs[field].history.has_changes()
                for field in _IMMUTABLE_PLAN_FIELDS
            ):
                raise SchedulePlanImmutableError(
                    "confirmed schedule plan content is immutable"
                )
        elif isinstance(item, ScheduledSession):
            plan = item.plan
            if _original_plan_status(plan) not in _IMMUTABLE_PLAN_STATUSES:
                continue
            state = inspect(item)
            allowed_external_changes = session.info.get(
                "external_calendar_session_time_updates", set()
            )
            if item.id in allowed_external_changes:
                changed_fields = {
                    field
                    for field in _IMMUTABLE_SESSION_FIELDS
                    if state.attrs[field].history.has_changes()
                }
                if changed_fields <= {"start", "end", "duration_minutes"}:
                    continue
            if any(
                state.attrs[field].history.has_changes()
                for field in _IMMUTABLE_SESSION_FIELDS
            ):
                raise SchedulePlanImmutableError(
                    "confirmed scheduled session content is immutable"
                )
