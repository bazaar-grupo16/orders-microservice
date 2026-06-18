"""
FastAPI dependencies for dependency injection.

Provides database sessions and user identity extraction.
"""

from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import SessionLocal


async def _get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a SQLAlchemy session and ensure it is closed afterwards."""
    async with SessionLocal() as db:
        yield db



def _get_current_user_id(
    x_user_id: str | None = Header(None),
) -> UUID:
    """Extract and validate the user ID from the ``X-User-Id`` header.

    This header is injected by NGINX Ingress after successfully
    validating the JWT against the ``auth-service``.  The microservice
    does NOT re-validate the token — that is the gateway's job.

    In integration tests, the header is sent directly by the test client.

    Raises
    ------
    HTTPException (401)
        If the header is missing or not a valid UUID.
    """
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-User-Id header",
        )
    try:
        return UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-User-Id header: not a valid UUID",
        )


# ── Re-usable annotated dependencies ─────────────────────────
DbSession = Annotated[AsyncSession, Depends(_get_db)]
CurrentUserId = Annotated[UUID, Depends(_get_current_user_id)]

