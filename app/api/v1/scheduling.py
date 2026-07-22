from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import DatabaseSession
from app.schemas.scheduling import SchedulePreviewRequest, SchedulePreviewResponse
from app.services.scheduling import PreviewUserNotFoundError, preview_schedule

router = APIRouter(prefix="/scheduling", tags=["scheduling"])


@router.post("/preview", response_model=SchedulePreviewResponse)
def schedule_preview(
    data: SchedulePreviewRequest, session: DatabaseSession
) -> SchedulePreviewResponse:
    try:
        preview = preview_schedule(
            session=session,
            user_id=data.user_id,
            planning_window=data.planning_window.to_domain(),
            busy_intervals=tuple(item.to_domain() for item in data.busy_intervals),
        )
    except PreviewUserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from exc

    return SchedulePreviewResponse.from_domain(
        preview.planning_window,
        preview.free_intervals,
        preview.result,
    )
