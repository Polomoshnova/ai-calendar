import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from app.models.calendar_sync import ExternalChangeType, SyncStatus


class PullSyncOutcome(StrEnum):
    no_change = "no_change"
    change_detected = "change_detected"
    external_event_missing = "external_event_missing"
    provider_error = "provider_error"


class PullCalendarEventSynchronizationResult(BaseModel):
    mapping_id: uuid.UUID
    scheduled_session_id: uuid.UUID
    external_event_id: str
    connection_id: uuid.UUID
    calendar_id: str
    previous_sync_status: SyncStatus
    resulting_sync_status: SyncStatus
    outcome: PullSyncOutcome
    external_change_id: uuid.UUID | None = None
    change_kind: ExternalChangeType | None = None
    provider_updated_at: datetime | None = None
    etag: str | None = None
    error_code: str | None = None
    message: str | None = None
