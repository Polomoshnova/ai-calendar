from datetime import UTC, datetime


def deadline_after_external_move(
    current_deadline: datetime | None,
    non_deleted_session_ends: list[datetime],
) -> datetime | None:
    values = [
        value
        for value in [current_deadline, *non_deleted_session_ends]
        if value is not None
    ]
    for value in values:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline policy requires timezone-aware datetimes")
    if not values:
        return None
    return max(values, key=lambda value: value.astimezone(UTC))
