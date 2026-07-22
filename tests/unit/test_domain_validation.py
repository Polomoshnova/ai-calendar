from datetime import UTC, datetime

import pytest

from app.domain.tasks import validate_task
from app.domain.timezones import validate_timezone


def test_duration_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        validate_task(0, None, None)


def test_earliest_start_must_be_before_deadline() -> None:
    instant = datetime(2026, 7, 22, tzinfo=UTC)

    with pytest.raises(ValueError, match="must be before"):
        validate_task(30, instant, instant)


def test_valid_iana_timezone() -> None:
    assert validate_timezone("Europe/Warsaw") == "Europe/Warsaw"


def test_invalid_iana_timezone() -> None:
    with pytest.raises(ValueError, match="valid IANA timezone"):
        validate_timezone("Mars/Olympus_Mons")
