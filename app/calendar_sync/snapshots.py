import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.calendar import CalendarProviderName


class StrictSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BusySourceSnapshot(StrictSnapshot):
    connection_id: uuid.UUID
    provider: CalendarProviderName
    provider_account_id: str | None = None
    calendar_id: str = Field(min_length=1)
    captured_at: datetime | None = None

    @field_validator("captured_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("captured_at must be timezone-aware")
        return value


class SessionWriteTargetSnapshot(StrictSnapshot):
    scheduled_session_id: uuid.UUID
    connection_id: uuid.UUID
    provider: CalendarProviderName
    provider_account_id: str | None = None
    calendar_id: str = Field(min_length=1)


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonicalize(nested)
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_canonicalize(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
        )
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="json"))
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value)
    return value


def calendar_context_hash(
    *,
    busy_sources: list[BusySourceSnapshot],
    write_targets: list[SessionWriteTargetSnapshot],
) -> str:
    payload = _canonicalize(
        {
            "busy_sources": busy_sources,
            "write_targets": write_targets,
        }
    )
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
