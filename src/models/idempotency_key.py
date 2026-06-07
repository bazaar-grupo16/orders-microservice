"""SQLAlchemy model and hooks for the ``idempotency_keys`` table."""

import logging
from sqlalchemy import Column, String, DateTime, UUID, event
from sqlalchemy.orm import object_session

from src.core.database import Base
from src.core.utils import utc_now
from src.models.order import Order

logger = logging.getLogger(__name__)


class IdempotencyKey(Base):
    """ORM model representing an idempotency key and its associated pre-generated order_id."""

    __tablename__ = "idempotency_keys"

    key = Column(String, primary_key=True)
    order_id = Column(UUID, nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)


@event.listens_for(Order, "before_insert")
def set_order_id_from_idempotency_key(mapper, connection, target: Order) -> None:
    """SQLAlchemy hook to set the Order ID from the pre-generated ID in the idempotency keys table."""
    if target.idempotency_key:
        session = object_session(target)
        if session is not None:
            # Query the db/session for the corresponding IdempotencyKey
            row = session.query(IdempotencyKey).filter(IdempotencyKey.key == target.idempotency_key).first()
            if row is not None:
                logger.info(
                    "Setting Order ID %s from idempotency key %s",
                    row.order_id,
                    target.idempotency_key,
                )
                target.id = row.order_id


@event.listens_for(Order, "before_delete")
def delete_idempotency_key_on_order_delete(mapper, connection, target: Order) -> None:
    """SQLAlchemy hook to delete the IdempotencyKey row when its Order is deleted (Saga compensation)."""
    if target.idempotency_key:
        session = object_session(target)
        if session is not None:
            logger.info(
                "Deleting idempotency key %s because order %s is being deleted",
                target.idempotency_key,
                target.id,
            )
            # Use query.delete() inside the session to clean up the key
            session.query(IdempotencyKey).filter(IdempotencyKey.key == target.idempotency_key).delete()
