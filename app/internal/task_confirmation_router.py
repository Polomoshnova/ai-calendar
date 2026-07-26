from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.ai_intake.types import TaskDraftV2
from app.internal.dependencies import InternalToolsEnabled
from app.task_confirmation.errors import ConfirmationError
from app.task_confirmation.models import (
    ConfirmationResult,
    DraftReview,
)
from app.task_confirmation.service import apply_review

router = APIRouter(prefix="/internal", include_in_schema=False)


class TaskDraftConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: TaskDraftV2
    review: DraftReview


@router.post("/api/task-drafts/confirm", response_model=ConfirmationResult)
def confirm_task_draft(
    data: TaskDraftConfirmRequest,
    _enabled: InternalToolsEnabled,
) -> ConfirmationResult:
    try:
        return apply_review(data.draft, data.review)
    except ConfirmationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
