import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.ai_intake.factory import AIIntakeConfigurationError, get_ai_gateway
from app.ai_intake.gateway import (
    AIGateway,
    AIProviderError,
    InvalidAIOutputError,
)
from app.ai_intake.types import TaskDraft
from app.internal.dependencies import InternalToolsEnabled

router = APIRouter(prefix="/internal", include_in_schema=False)
logger = logging.getLogger(__name__)


class TaskDraftAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=10_000)


def require_ai_gateway() -> AIGateway:
    try:
        return get_ai_gateway()
    except AIIntakeConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


AIIntakeGateway = Annotated[AIGateway, Depends(require_ai_gateway)]


@router.post("/api/task-drafts/analyze", response_model=TaskDraft)
def analyze_task_draft(
    data: TaskDraftAnalyzeRequest,
    _enabled: InternalToolsEnabled,
    gateway: AIIntakeGateway,
) -> TaskDraft:
    try:
        return gateway.analyze(data.text)
    except InvalidAIOutputError as exc:
        logger.exception(
            "AI intake validation failed", extra={"category": exc.category}
        )
        raise HTTPException(
            status_code=502, detail="AI returned invalid output"
        ) from exc
    except AIProviderError as exc:
        logger.exception("AI intake provider failed", extra={"category": exc.category})
        raise HTTPException(
            status_code=502, detail="AI provider request failed"
        ) from exc
