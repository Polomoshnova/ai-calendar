from typing import Any


class WorkflowError(Exception):
    def __init__(self, code: str, stage: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.message = message

    def to_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
        }


class WorkflowValidationError(WorkflowError):
    pass


class WorkflowAIError(WorkflowError):
    pass


class WorkflowConfirmationError(WorkflowError):
    pass


class WorkflowSchedulingError(WorkflowError):
    pass
