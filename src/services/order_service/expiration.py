"""Expiration flow for abandoned pending-payment orders."""

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.utils import utc_now
from src.models.order import Order
from src.models.order_status import OrderStatus
from src.repositories import order_repository
from src.schemas.webhook import PaymentStatus
from src.services.order_service.webhooks import resolve_payment


logger = logging.getLogger(__name__)


def expire_pending_payment_orders(db: Session) -> int:
    """Expire old pending-payment orders and release their reserved stock."""
    settings = get_settings()
    cutoff = utc_now() - timedelta(minutes=settings.PENDING_PAYMENT_EXPIRATION_MINUTES)
    orders = order_repository.list_expired_pending_payment_orders(
        db,
        cutoff=cutoff,
        limit=settings.PENDING_PAYMENT_EXPIRATION_BATCH_SIZE,
    )

    expired_count = 0
    for order in orders:
        if OrderStatus(order.status) != OrderStatus.PENDIENTE_DE_PAGO:
            continue

        _expire_order(db, order)
        expired_count += 1

    if expired_count:
        logger.info("Expired %d pending payment order(s)", expired_count)
    return expired_count


def _expire_order(db: Session, order: Order) -> None:
    logger.info("Expiring pending payment order=%s", order.id)
    resolve_payment(
        db,
        order_id=order.id,
        payment_status=PaymentStatus.CANCELED,
        transaction_id="expiration_task",
    )