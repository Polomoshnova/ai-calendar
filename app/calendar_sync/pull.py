import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.calendar_integration.errors import (
    CalendarAuthorizationError,
    CalendarEventNotFoundError,
    CalendarIntegrationError,
    CalendarValidationError,
)
from app.calendar_integration.models import CalendarEventSnapshot
from app.calendar_integration.protocols import (
    CalendarOAuthClient,
    CalendarProvider,
    TokenCipher,
)
from app.calendar_integration.service import connection_credentials
from app.calendar_sync.pull_schemas import (
    PullCalendarEventSynchronizationResult,
    PullSyncOutcome,
)
from app.models.calendar import CalendarConnection, CalendarConnectionStatus
from app.models.calendar_sync import (
    CalendarEventMapping,
    ExternalCalendarChange,
    ExternalChangeType,
    SyncStatus,
)
from app.schedule_plans.models import ScheduledSession


class CalendarEventMappingNotFoundError(CalendarValidationError):
    code = "calendar_event_mapping_not_found"


def _snapshot_dict(snapshot: CalendarEventSnapshot) -> dict[str, Any]:
    return snapshot.model_dump(mode="json")


def _state(snapshot: CalendarEventSnapshot) -> dict[str, Any]:
    return {
        "external_event_id": snapshot.external_event_id,
        "calendar_id": snapshot.calendar_id,
        "exists": snapshot.exists,
        "cancelled": snapshot.cancelled,
        "start": snapshot.start.isoformat() if snapshot.start else None,
        "end": snapshot.end.isoformat() if snapshot.end else None,
        "timezone": snapshot.timezone,
    }


def _hash(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _baseline(mapping: CalendarEventMapping) -> CalendarEventSnapshot:
    if mapping.last_synced_snapshot is not None:
        try:
            return CalendarEventSnapshot.model_validate(mapping.last_synced_snapshot)
        except ValidationError as exc:
            raise CalendarValidationError(
                "Calendar event mapping has an invalid synchronization snapshot"
            ) from exc
    scheduled_session = mapping.scheduled_session
    return CalendarEventSnapshot(
        external_event_id=mapping.external_event_id,
        calendar_id=mapping.calendar_id,
        exists=mapping.sync_status is not SyncStatus.externally_deleted,
        cancelled=False,
        start=scheduled_session.start,
        end=scheduled_session.end,
        timezone=scheduled_session.plan.timezone,
        etag=mapping.etag,
        provider_updated_at=mapping.provider_updated_at,
        provider_status="confirmed",
    )


def _change_kind(
    previous: CalendarEventSnapshot,
    current: CalendarEventSnapshot,
) -> ExternalChangeType | None:
    if _state(previous) == _state(current):
        return None
    if previous.exists and (not current.exists or current.cancelled):
        return ExternalChangeType.deleted
    if (not previous.exists or previous.cancelled) and current.exists:
        return ExternalChangeType.created
    if previous.start != current.start or previous.end != current.end:
        return ExternalChangeType.moved
    return ExternalChangeType.updated


def _load_owned_mapping(
    session: Session,
    mapping_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> CalendarEventMapping:
    statement = (
        select(CalendarEventMapping)
        .join(CalendarEventMapping.scheduled_session)
        .join(ScheduledSession.plan)
        .where(
            CalendarEventMapping.id == mapping_id,
            ScheduledSession.plan.has(user_id=user_id),
        )
    )
    if for_update:
        statement = statement.with_for_update()
    mapping = session.scalar(statement)
    if mapping is None:
        raise CalendarEventMappingNotFoundError("Calendar event mapping not found")
    return mapping


def _result(
    mapping: CalendarEventMapping,
    *,
    previous_status: SyncStatus,
    outcome: PullSyncOutcome,
    change: ExternalCalendarChange | None = None,
    current: CalendarEventSnapshot | None = None,
    error: CalendarIntegrationError | None = None,
) -> PullCalendarEventSynchronizationResult:
    return PullCalendarEventSynchronizationResult(
        mapping_id=mapping.id,
        scheduled_session_id=mapping.scheduled_session_id,
        external_event_id=mapping.external_event_id,
        connection_id=mapping.calendar_connection_id,
        calendar_id=mapping.calendar_id,
        previous_sync_status=previous_status,
        resulting_sync_status=mapping.sync_status,
        outcome=outcome,
        external_change_id=change.id if change else None,
        change_kind=change.change_type if change else None,
        provider_updated_at=(
            current.provider_updated_at if current else mapping.provider_updated_at
        ),
        etag=current.etag if current else mapping.etag,
        error_code=error.code if error else None,
        message=error.message if error else None,
    )


async def pull_calendar_event(
    session: Session,
    *,
    user_id: uuid.UUID,
    mapping_id: uuid.UUID,
    provider: CalendarProvider,
    oauth_client: CalendarOAuthClient,
    cipher: TokenCipher,
    now: datetime | None = None,
) -> PullCalendarEventSynchronizationResult:
    current_time = now or datetime.now(UTC)
    mapping = _load_owned_mapping(session, mapping_id, user_id)
    previous_status = mapping.sync_status
    if not mapping.calendar_id.strip() or not mapping.external_event_id.strip():
        raise CalendarValidationError(
            "Calendar event mapping has no deterministic external identity"
        )
    connection = session.get(CalendarConnection, mapping.calendar_connection_id)
    if connection is None or connection.user_id != user_id:
        raise CalendarValidationError("Mapped calendar connection is unavailable")
    if connection.status is not CalendarConnectionStatus.active:
        raise CalendarValidationError("Mapped calendar connection is not active")
    try:
        credentials = await connection_credentials(
            session,
            connection,
            cipher=cipher,
            oauth_client=oauth_client,
        )
        session.commit()
        try:
            current = await provider.get_event(
                credentials,
                calendar_id=mapping.calendar_id,
                external_event_id=mapping.external_event_id,
            )
        except CalendarAuthorizationError:
            refreshed_connection = session.get(
                CalendarConnection, mapping.calendar_connection_id
            )
            assert refreshed_connection is not None
            credentials = await connection_credentials(
                session,
                refreshed_connection,
                cipher=cipher,
                oauth_client=oauth_client,
                force_refresh=True,
            )
            session.commit()
            current = await provider.get_event(
                credentials,
                calendar_id=mapping.calendar_id,
                external_event_id=mapping.external_event_id,
            )
        if (
            current.calendar_id != mapping.calendar_id
            or current.external_event_id != mapping.external_event_id
        ):
            raise CalendarValidationError(
                "Provider returned a different calendar event identity"
            )
    except CalendarEventNotFoundError:
        current = CalendarEventSnapshot(
            external_event_id=mapping.external_event_id,
            calendar_id=mapping.calendar_id,
            exists=False,
            cancelled=False,
            provider_status="not_found",
        )
    except CalendarIntegrationError as exc:
        session.rollback()
        locked = _load_owned_mapping(session, mapping_id, user_id, for_update=True)
        locked.sync_status = SyncStatus.failed
        locked.last_sync_attempt_at = current_time
        locked.sync_error_code = exc.code
        locked.sync_error_message = exc.message
        session.commit()
        return _result(
            locked,
            previous_status=previous_status,
            outcome=PullSyncOutcome.provider_error,
            error=exc,
        )

    session.rollback()
    locked = _load_owned_mapping(session, mapping_id, user_id, for_update=True)
    previous = _baseline(locked)
    kind = _change_kind(previous, current)
    change: ExternalCalendarChange | None = None
    transition_hash: str | None = None
    if kind is not None:
        transition_hash = _hash(
            {
                "previous": _state(previous),
                "current": _snapshot_dict(current),
            }
        )
        change = ExternalCalendarChange(
            mapping_id=locked.id,
            change_type=kind,
            provider_timestamp=current.provider_updated_at,
            detected_at=current_time,
            old_values=_snapshot_dict(previous),
            new_values=_snapshot_dict(current),
            transition_hash=transition_hash,
        )
        session.add(change)
    locked.last_synced_snapshot = _snapshot_dict(current)
    locked.last_synced_snapshot_hash = _hash(_state(current))
    locked.etag = current.etag
    locked.provider_updated_at = current.provider_updated_at
    locked.sync_status = (
        SyncStatus.externally_deleted
        if not current.exists or current.cancelled
        else SyncStatus.synced
    )
    locked.last_sync_attempt_at = current_time
    locked.last_synced_at = current_time
    locked.sync_error_code = None
    locked.sync_error_message = None
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        locked = _load_owned_mapping(session, mapping_id, user_id)
        if transition_hash is not None:
            change = session.scalar(
                select(ExternalCalendarChange).where(
                    ExternalCalendarChange.mapping_id == mapping_id,
                    ExternalCalendarChange.transition_hash == transition_hash,
                )
            )
    return _result(
        locked,
        previous_status=previous_status,
        outcome=(
            PullSyncOutcome.no_change
            if kind is None
            else (
                PullSyncOutcome.external_event_missing
                if kind is ExternalChangeType.deleted
                else PullSyncOutcome.change_detected
            )
        ),
        change=change,
        current=current,
    )
