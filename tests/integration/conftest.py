import os
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import get_db
from app.main import app
from app.models import Task, User, UserPreferences


def require_test_database_url() -> str:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        raise pytest.UsageError(
            "Integration tests require TEST_DATABASE_URL; DATABASE_URL is never used."
        )

    url = make_url(database_url)
    database_name = url.database or ""
    is_test_database = database_name.startswith("test_") or database_name.endswith(
        "_test"
    )
    if not is_test_database:
        raise pytest.UsageError(
            "TEST_DATABASE_URL must target a database whose name starts with "
            f"'test_' or ends with '_test'; got {database_name!r}."
        )
    if not url.drivername.startswith("postgresql"):
        raise pytest.UsageError("Integration tests require a PostgreSQL database.")

    return database_url


test_engine = create_engine(require_test_database_url(), pool_pre_ping=True)
TestSessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    with TestSessionLocal() as session:
        session.execute(delete(Task))
        session.execute(delete(UserPreferences))
        session.execute(delete(User))
        session.commit()
        yield session
        session.rollback()
        session.execute(delete(Task))
        session.execute(delete(UserPreferences))
        session.execute(delete(User))
        session.commit()


@pytest.fixture
def user(db_session: Session) -> User:
    user = User(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        email="owner@example.com",
        timezone="Europe/Warsaw",
    )
    user.preferences = UserPreferences()
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
