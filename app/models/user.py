import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.database import Base
from app.domain.timezones import validate_timezone

if TYPE_CHECKING:
    from app.models.calendar import CalendarConnection
    from app.models.task import Task
    from app.models.user_preferences import UserPreferences


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    preferences: Mapped["UserPreferences | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    calendar_connections: Mapped[list["CalendarConnection"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @validates("timezone")
    def validate_user_timezone(self, _key: str, value: str) -> str:
        return validate_timezone(value)
