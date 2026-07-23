from datetime import UTC, datetime
from typing import Any

from app.internal.schemas import ReviewExportPayload, ReviewExportRequest


def build_export_payload(
    request: ReviewExportRequest, *, exported_at: datetime | None = None
) -> ReviewExportPayload:
    inputs = request.normalized_inputs
    review = request.review
    return ReviewExportPayload(
        user_timezone=inputs.user_timezone,
        planning_window=inputs.planning_window.model_dump(mode="json"),
        preferences_used=inputs.preferences_used.model_dump(mode="json"),
        tasks=inputs.tasks,
        busy_intervals=inputs.busy_intervals,
        generated_preview_result=request.generated_preview_result,
        score=review.score,
        verdict=review.verdict,
        notes=review.notes,
        observed_problems=review.observed_problems,
        exported_at=exported_at or datetime.now(UTC),
    )


def contains_secret_fields(value: Any) -> bool:
    forbidden = {"password", "secret", "token", "database_url", "environment"}
    if isinstance(value, dict):
        return any(
            str(key).lower() in forbidden or contains_secret_fields(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_secret_fields(item) for item in value)
    return False
