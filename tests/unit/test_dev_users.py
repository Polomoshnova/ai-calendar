from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.internal.dev_users import get_or_create_dev_user
from app.internal.dev_users_router import DevUserCreateRequest
from app.models import User


def test_request_normalizes_valid_email_and_accepts_iana_timezone() -> None:
    request = DevUserCreateRequest(
        email="  User.Name@Example.COM  ",
        timezone="Europe/Warsaw",
    )

    assert str(request.email) == "user.name@example.com"
    assert request.timezone == "Europe/Warsaw"


@pytest.mark.parametrize("email", ["", "not-an-email", "user@"])
def test_request_rejects_invalid_email(email: str) -> None:
    with pytest.raises(ValidationError):
        DevUserCreateRequest(email=email)


def test_request_rejects_invalid_timezone() -> None:
    with pytest.raises(ValidationError, match="valid IANA timezone"):
        DevUserCreateRequest(
            email="user@example.com",
            timezone="Mars/Olympus_Mons",
        )


def test_duplicate_insert_race_returns_existing_user() -> None:
    existing = User(email="user@example.com", timezone="Europe/Warsaw")
    session = Mock(spec=Session)
    session.scalar.side_effect = [None, existing]
    session.commit.side_effect = IntegrityError(
        "duplicate email",
        params={},
        orig=Exception("unique violation"),
    )

    result = get_or_create_dev_user(
        session,
        email="User@Example.com",
        timezone="Europe/Madrid",
    )

    assert result.user is existing
    assert result.created is False
    session.rollback.assert_called_once_with()
    session.refresh.assert_not_called()
