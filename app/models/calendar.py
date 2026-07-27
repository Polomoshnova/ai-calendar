import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class CalendarProviderName(StrEnum):
    google = "google"


class CalendarConnectionStatus(StrEnum):
    active = "active"
    expired = "expired"
    revoked = "revoked"
    error = "error"


class CalendarConnection(Base):
    __tablename__ = "calendar_connections"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", name="uq_calendar_connection_user_provider"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[CalendarProviderName] = mapped_column(
        Enum(CalendarProviderName, name="calendar_provider")
    )
    provider_account_id: Mapped[str | None] = mapped_column(String(255))
    provider_account_email: Mapped[str | None] = mapped_column(String(320))
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[CalendarConnectionStatus] = mapped_column(
        Enum(CalendarConnectionStatus, name="calendar_connection_status"),
        default=CalendarConnectionStatus.active,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100))

    user: Mapped["User"] = relationship(back_populates="calendar_connections")
    selections: Mapped[list["CalendarSelection"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"CalendarConnection(id={self.id!r}, user_id={self.user_id!r}, "
            f"provider={self.provider!r}, status={self.status!r})"
        )


class CalendarSelection(Base):
    __tablename__ = "calendar_selections"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "external_calendar_id",
            name="uq_calendar_selection_connection_external",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calendar_connections.id", ondelete="CASCADE"),
        index=True,
    )
    external_calendar_id: Mapped[str] = mapped_column(String(1024))
    display_name: Mapped[str] = mapped_column(String(500))
    timezone: Mapped[str | None] = mapped_column(String(64))
    primary: Mapped[bool] = mapped_column(Boolean, default=False)
    include_in_availability: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    connection: Mapped[CalendarConnection] = relationship(back_populates="selections")


class CalendarOAuthState(Base):
    __tablename__ = "calendar_oauth_states"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[CalendarProviderName] = mapped_column(
        Enum(CalendarProviderName, name="calendar_provider"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
