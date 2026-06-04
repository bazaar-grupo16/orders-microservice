"""Expiration flow for abandoned pending-payment orders."""

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from src.clients import inventory_client
from src.core.config import get_settings
from src.core.utils import utc_now
from src.models.order import Order
from src.models.order_status import OrderStatus
from src.repositories import order_repository
from src.services.order_service.update import _propagate_status_to_items


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
    order = order_repository.update_status(
        db,
        order,
        OrderStatus.PAGO_RECHAZADO,
        origin="system",
    )
    _propagate_status_to_items(
        db,
        order,
        OrderStatus.PAGO_RECHAZADO.value,
        origin="system",
    )
    _rollback_stock_for_order(order)


def _rollback_stock_for_order(order: Order) -> None:
    payload = [
        {"product_id": str(item.product_id), "quantity": item.quantity}
        for item in order.items
    ]
    try:
        inventory_client.rollback_stock(payload)
    except Exception as exc:
        logger.error(
            "Stock rollback failed for expired pending payment order=%s: %s",
            order.id,
            exc,
        )
