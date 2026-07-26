class ConfirmationError(Exception):
    """Base error for pure task confirmation failures."""


class MissingConfirmationError(ConfirmationError):
    pass


class InvalidConfirmationError(ConfirmationError):
    pass


class UnknownReviewFieldError(ConfirmationError):
    pass


class DuplicateStepReviewError(ConfirmationError):
    pass


class StepNotFoundError(ConfirmationError):
    pass
