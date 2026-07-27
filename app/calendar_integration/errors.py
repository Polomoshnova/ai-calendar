from typing import Any


class CalendarIntegrationError(Exception):
    code = "calendar_integration_error"
    provider = "google"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "provider": self.provider,
            "message": self.message,
        }


class CalendarConfigurationError(CalendarIntegrationError):
    code = "calendar_not_configured"


class CalendarConnectionNotFoundError(CalendarIntegrationError):
    code = "calendar_connection_not_found"


class CalendarAuthorizationError(CalendarIntegrationError):
    code = "calendar_authorization_failed"


class CalendarReconnectRequiredError(CalendarIntegrationError):
    code = "calendar_reconnect_required"


class CalendarProviderError(CalendarIntegrationError):
    code = "calendar_provider_error"


class CalendarUnavailableError(CalendarProviderError):
    code = "calendar_provider_unavailable"


class CalendarRateLimitError(CalendarProviderError):
    code = "calendar_rate_limited"


class CalendarValidationError(CalendarIntegrationError):
    code = "calendar_validation_error"


class CalendarSelectionError(CalendarIntegrationError):
    code = "calendar_selection_error"
