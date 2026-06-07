"""Repository layer for IdempotencyKey persistence."""

import uuid
from sqlalchemy.orm import Session
from src.models.idempotency_key import IdempotencyKey


def get(db: Session, key: str) -> IdempotencyKey | None:
    """Look up an idempotency key row by its key.

    Parameters
    ----------
    db: Session
        The database session.
    key: str
        The idempotency key (UUIDv4 sent by the client).

    Returns
    -------
    IdempotencyKey | None
        The idempotency key record if found, else None.
    """
    return db.query(IdempotencyKey).filter(IdempotencyKey.key == key).first()


def create(db: Session, key: str, order_id: uuid.UUID) -> IdempotencyKey:
    """Insert a new idempotency key record.

    Does not handle IntegrityError internally; lets it propagate to the caller
    so that race conditions can be caught and handled at the controller level.

    Parameters
    ----------
    db: Session
        The database session.
    key: str
        The idempotency key (UUIDv4 sent by the client).
    order_id: UUID
        The pre-generated order ID.

    Returns
    -------
    IdempotencyKey
        The newly created idempotency key record.
    """
    row = IdempotencyKey(key=key, order_id=order_id)
    db.add(row)
    db.flush()  # Use flush to trigger database constraint evaluation and propagate IntegrityError immediately
    return row


def rollback(db: Session) -> None:
    """Roll back the current transaction."""
    db.rollback()

