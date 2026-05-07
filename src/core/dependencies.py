"""
FastAPI dependencies for dependency injection.

Provides database sessions and user identity extraction.
"""

from collections.abc import Generator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from src.core.database import SessionLocal


def _get_db() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session and ensure it is closed afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
DbSession = Annotated[Session, Depends(_get_db)]
CurrentUserId = Annotated[UUID, Depends(_get_current_user_id)]
