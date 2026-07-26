import logging

from fastapi import APIRouter, HTTPException

from app.internal.ai_intake_router import AIIntakeGateway
from app.internal.dependencies import InternalToolsEnabled
from app.workflows.errors import (
    WorkflowAIError,
    WorkflowConfirmationError,
    WorkflowError,
    WorkflowSchedulingError,
    WorkflowValidationError,
)
from app.workflows.models import (
    TaskToSchedulePreviewRequest,
    TaskToSchedulePreviewResponse,
)
from app.workflows.task_to_schedule_preview import (
    execute_task_to_schedule_preview,
)

router = APIRouter(prefix="/internal", tags=["internal-workflows"])
logger = logging.getLogger(__name__)


def _log_failure(error: WorkflowError) -> None:
    logger.warning(
        "Task-to-schedule workflow failed",
        extra={
            "workflow_version": "task-to-schedule-preview.v1",
            "stage": str(error.stage),
            "status": "failed",
            "error_code": error.code,
        },
    )


@router.post(
    "/api/workflows/task-to-schedule-preview",
    response_model=TaskToSchedulePreviewResponse,
)
def task_to_schedule_preview(
    data: TaskToSchedulePreviewRequest,
    _enabled: InternalToolsEnabled,
    gateway: AIIntakeGateway,
) -> TaskToSchedulePreviewResponse:
    try:
        return execute_task_to_schedule_preview(data, ai_gateway=gateway)
    except WorkflowConfirmationError as exc:
        _log_failure(exc)
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except WorkflowValidationError as exc:
        _log_failure(exc)
        status_code = (
            422
            if exc.code
            in {
                "missing_confirmed_duration",
                "invalid_confirmed_duration",
                "invalid_confirmed_priority",
                "invalid_confirmed_preferred_time",
            }
            else 400
        )
        raise HTTPException(status_code=status_code, detail=exc.to_detail()) from exc
    except WorkflowAIError as exc:
        _log_failure(exc)
        raise HTTPException(status_code=502, detail=exc.to_detail()) from exc
    except WorkflowSchedulingError as exc:
        _log_failure(exc)
        raise HTTPException(status_code=500, detail=exc.to_detail()) from exc
