import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.calendar import CalendarProviderName

if TYPE_CHECKING:
    from app.models.calendar import CalendarConnection
    from app.schedule_plans.models import ScheduledSession


class SyncStatus(StrEnum):
    pending = "pending"
    synced = "synced"
    failed = "failed"
    externally_deleted = "externally_deleted"


class ExternalChangeType(StrEnum):
    created = "created"
    updated = "updated"
    moved = "moved"
    deleted = "deleted"


class ConsistencyStatus(StrEnum):
    consistent = "consistent"
    inconsistent = "inconsistent"


class ConsistencyIssueCode(StrEnum):
    session_overlap = "session_overlap"
    minimum_break_violation = "minimum_break_violation"
    invalid_session_order = "invalid_session_order"
    latest_session_after_deadline = "latest_session_after_deadline"
    externally_deleted_session = "externally_deleted_session"


class CalendarEventMapping(Base):
    __tablename__ = "calendar_event_mappings"
    __table_args__ = (
        UniqueConstraint(
            "scheduled_session_id",
            name="uq_calendar_event_mappings_scheduled_session",
        ),
        UniqueConstraint(
            "calendar_connection_id",
            "calendar_id",
            "external_event_id",
            name="uq_calendar_event_mappings_external_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scheduled_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scheduled_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    calendar_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calendar_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[CalendarProviderName] = mapped_column(
        Enum(CalendarProviderName, name="calendar_provider"),
        nullable=False,
    )
    provider_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calendar_id: Mapped[str] = mapped_column(Text, nullable=False)
    external_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sync_status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus, name="calendar_event_sync_status"),
        nullable=False,
        default=SyncStatus.pending,
        index=True,
    )
    last_sync_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sync_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sync_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    last_synced_snapshot_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
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

    scheduled_session: Mapped["ScheduledSession"] = relationship(
        back_populates="calendar_event_mapping"
    )
    calendar_connection: Mapped["CalendarConnection"] = relationship(
        back_populates="event_mappings"
    )
    changes: Mapped[list["ExternalCalendarChange"]] = relationship(
        back_populates="mapping",
        cascade="all, delete-orphan",
    )


class ExternalCalendarChange(Base):
    __tablename__ = "external_calendar_changes"
    __table_args__ = (
        UniqueConstraint(
            "mapping_id",
            "transition_hash",
            name="uq_external_calendar_changes_mapping_transition",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    mapping_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calendar_event_mappings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    change_type: Mapped[ExternalChangeType] = mapped_column(
        Enum(ExternalChangeType, name="external_calendar_change_type"),
        nullable=False,
        index=True,
    )
    provider_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    old_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    new_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    transition_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    mapping: Mapped[CalendarEventMapping] = relationship(back_populates="changes")
