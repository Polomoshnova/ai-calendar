import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.backlog.domain import BacklogOrigin, BacklogReason, BacklogStatus
from app.core.database import Base

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import User


class BacklogEntry(Base):
    __tablename__ = "backlog_entries"
    __table_args__ = (
        CheckConstraint(
            "remaining_duration_minutes >= 0",
            name="ck_backlog_entries_nonnegative_remaining",
        ),
        CheckConstraint(
            "scheduling_attempt_count >= 0",
            name="ck_backlog_entries_nonnegative_attempt_count",
        ),
        CheckConstraint(
            "(status IN ('active', 'deferred') AND "
            "remaining_duration_minutes > 0 AND resolved_at IS NULL) OR "
            "(status = 'resolved' AND remaining_duration_minutes = 0 "
            "AND resolved_at IS NOT NULL) OR "
            "(status = 'cancelled' AND remaining_duration_minutes = 0 "
            "AND resolved_at IS NULL)",
            name="ck_backlog_entries_status_resolution",
        ),
        CheckConstraint(
            "status <> 'deferred' OR deferred_until IS NOT NULL "
            "OR next_review_at IS NOT NULL",
            name="ck_backlog_entries_deferred_review",
        ),
        CheckConstraint(
            "reason <> 'other' OR (note IS NOT NULL AND length(trim(note)) > 0)",
            name="ck_backlog_entries_other_note",
        ),
        Index(
            "uq_backlog_entries_open_task",
            "task_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'deferred')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[BacklogStatus] = mapped_column(
        Enum(BacklogStatus, name="backlog_entry_status"),
        nullable=False,
        default=BacklogStatus.active,
        index=True,
    )
    origin: Mapped[BacklogOrigin] = mapped_column(
        Enum(BacklogOrigin, name="backlog_entry_origin"), nullable=False
    )
    reason: Mapped[BacklogReason] = mapped_column(
        Enum(BacklogReason, name="backlog_entry_reason"), nullable=False
    )
    remaining_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_scheduling_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduling_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    deferred_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    task: Mapped["Task"] = relationship(back_populates="backlog_entries")
    user: Mapped["User"] = relationship(back_populates="backlog_entries")
