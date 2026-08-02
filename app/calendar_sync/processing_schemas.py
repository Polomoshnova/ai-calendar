import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.calendar_sync import ExternalChangeProcessingStatus


class ConsistencyFindingResult(BaseModel):
    code: str
    severity: str
    details: dict[str, str] = Field(default_factory=dict)


class ProcessExternalCalendarChangeResult(BaseModel):
    external_change_id: uuid.UUID
    mapping_id: uuid.UUID
    scheduled_session_id: uuid.UUID
    schedule_plan_id: uuid.UUID
    task_id: uuid.UUID
    previous_processing_status: ExternalChangeProcessingStatus
    resulting_processing_status: ExternalChangeProcessingStatus
    actions_applied: list[str]
    previous_session_start: datetime
    previous_session_end: datetime
    resulting_session_start: datetime
    resulting_session_end: datetime
    previous_deadline: datetime | None
    resulting_deadline: datetime | None
    deadline_extended: bool
    external_event_missing: bool
    consistency_findings: list[ConsistencyFindingResult]
    already_processed: bool = False
