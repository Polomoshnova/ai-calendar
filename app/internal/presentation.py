from datetime import datetime
from zoneinfo import ZoneInfo


def to_local_iso(value: datetime, timezone: str) -> str:
    """Convert an aware instant to an ISO string in an IANA timezone."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(ZoneInfo(timezone)).isoformat()
