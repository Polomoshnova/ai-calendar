import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base
from app.schedule_plans.models import SchedulePlan


class SchedulePlanRevalidationStatus(StrEnum):
    pending = "pending"
    valid = "valid"
    conflict = "conflict"
    provider_partial_failure = "provider_partial_failure"
    provider_failure = "provider_failure"
    invalid_plan_state = "invalid_plan_state"


class SchedulePlanRevalidation(Base):
    __tablename__ = "schedule_plan_revalidations"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "request_id",
            name="uq_schedule_plan_revalidations_plan_request",
        ),
        Index(
            "ix_schedule_plan_revalidations_checked_at",
            "checked_at",
        ),
        Index(
            "ix_schedule_plan_revalidations_valid_until",
            "valid_until",
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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calendar_connections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[SchedulePlanRevalidationStatus] = mapped_column(
        Enum(
            SchedulePlanRevalidationStatus,
            name="schedule_plan_revalidation_status",
        ),
        nullable=False,
        index=True,
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    planning_window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    planning_window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_snapshot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_busy_interval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    merged_busy_interval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    conflicting_session_count: Mapped[int] = mapped_column(Integer, nullable=False)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    sessions_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    plan: Mapped[SchedulePlan] = relationship(back_populates="revalidations")
