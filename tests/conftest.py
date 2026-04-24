"""
Shared pytest fixtures for the test suite.

Tests run against a **real PostgreSQL** database provided by
``docker-compose.test.yml``.  Connection details come from the
same environment variables the application uses in production
(DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME).

**No mocking of the database is used.**
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.core.database import Base, SessionLocal, engine
from src.main import app


@pytest.fixture(scope="session", autouse=True)
def _setup_database():
    """Create all tables before the session and tear them down after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session() -> Session:
    """Provide a transactional database session that rolls back after each test.

    This ensures test isolation: every test starts with a clean state
    without needing to truncate tables manually.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client() -> TestClient:
    """Return a ``TestClient`` bound to the FastAPI application."""
    return TestClient(app)
