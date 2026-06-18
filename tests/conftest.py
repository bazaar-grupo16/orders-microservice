"""
Shared pytest fixtures for the test suite.

Tests run against a **real PostgreSQL** database provided by
``docker-compose.test.yml``.  Connection details come from the
same environment variables the application uses in production
(DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME).

**No mocking of the database is used.**

Async strategy
--------------
All fixtures that touch the database use the async SQLAlchemy engine.
The ``event_loop`` fixture is scoped to the session so that async
session-scoped fixtures share the same loop as function-scoped ones.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import Base, SessionLocal, engine
from src.main import app


# ── Event loop ───────────────────────────────────────────────
# pytest-asyncio requires an event_loop fixture at the widest scope that
# async fixtures use.  We set it to "session" so session-scoped async
# fixtures share the same loop as function-scoped ones.

@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the entire test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


# ── Database setup ───────────────────────────────────────────


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_database() -> AsyncIterator[None]:
    """Create all tables before the session and tear them down after."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture()
async def db_session() -> AsyncIterator[AsyncSession]:
    """Provide a transactional async database session that rolls back after each test.

    This ensures test isolation: every test starts with a clean state
    without needing to truncate tables manually.
    """
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()



# ── HTTP client ──────────────────────────────────────────────


@pytest_asyncio.fixture()
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Return an ``AsyncClient`` bound to the FastAPI application."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


