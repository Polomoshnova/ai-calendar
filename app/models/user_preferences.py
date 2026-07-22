import uuid
from datetime import datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, Time, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.database import Base
from app.domain.preferences import default_working_hours_json, parse_working_hours
from app.domain.tasks import PreferredTimeOfDay

if TYPE_CHECKING:
    from app.models.user import User


class UserPreferences(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint(
            "minimum_break_minutes >= 0",
            name="ck_user_preferences_nonnegative_break",
        ),
        CheckConstraint(
            "default_minimum_session_minutes > 0",
            name="ck_user_preferences_positive_default_session",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    working_hours: Mapped[dict[str, list[dict[str, str]]]] = mapped_column(
        JSONB, default=default_working_hours_json
    )
    preferred_task_time: Mapped[PreferredTimeOfDay] = mapped_column(
        Enum(PreferredTimeOfDay, name="preferred_time_of_day"),
        default=PreferredTimeOfDay.any,
    )
    minimum_break_minutes: Mapped[int] = mapped_column(Integer, default=0)
    no_deep_work_after: Mapped[time | None] = mapped_column(Time)
    default_minimum_session_minutes: Mapped[int] = mapped_column(Integer, default=15)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="preferences")

    @validates("working_hours")
    def validate_hours(
        self, _key: str, value: dict[str, list[dict[str, str]]]
    ) -> dict[str, list[dict[str, str]]]:
        parse_working_hours(value)
        return value
