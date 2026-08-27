import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import DatabaseSession
from app.internal.dependencies import InternalToolsEnabled
from app.planner import PlannerReadService, PlannerUserNotFoundError
from app.planner.schemas import PlannerResponse

router = APIRouter(prefix="/internal/api", tags=["internal-planner"])


def utc_now() -> datetime:
    return datetime.now(UTC)


@router.get("/planner", response_model=PlannerResponse)
def read_planner(
    user_id: uuid.UUID,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
    days: Annotated[int, Query(ge=1, le=31)] = 14,
) -> PlannerResponse:
    try:
        return PlannerReadService(session).read(
            user_id=user_id,
            days=days,
            now=utc_now(),
        )
    except PlannerUserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
