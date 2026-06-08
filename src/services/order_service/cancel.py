"""Order cancellation and refund-orchestration operations."""

import logging
import uuid

from sqlalchemy.orm import Session

from src.exceptions import (
    OrderAccessDeniedError,
    OrderNotFoundError,
)
from src.models.order import Order
from src.models.order_item import OrderItem
from src.models.order_status import OrderItemLogisticStatus, OrderStatus
from src.models.state_machine import (
    InvalidStateTransitionError,
    validate_item_transition,
    validate_transition,
)
from src.repositories import order_repository
from src.repositories.outbox_repository import OutboxRepository
from src.services.order_service.update import _propagate_status_to_items
from src.services.outbox_worker import process_order_outbox_synchronously

logger = logging.getLogger(__name__)



def cancel_order(
    db: Session,
    *,
    order_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> Order:
    """Cancel an order as buyer."""
    order = order_repository.get_by_id(db, order_id)
    if order is None:
        raise OrderNotFoundError(str(order_id))

    if user_id is None or order.user_id != user_id:
        raise OrderAccessDeniedError()

    for item in order.items:
        try:
            validate_item_transition(OrderItemLogisticStatus(item.status), OrderItemLogisticStatus.CANCELADO)
        except InvalidStateTransitionError:
            raise InvalidStateTransitionError(
                current=item.status,
                target=OrderItemLogisticStatus.CANCELADO.value,
                detail="There are items that cannot be cancelled. Please cancel desired items individually.",
            )

    previous_status = OrderStatus(order.status)
    validate_transition(previous_status, OrderStatus.CANCELADA)

    # Insert outbox events for compensating actions in the same transaction
    OutboxRepository.insert(db, "RELEASE_STOCK", {"order_id": str(order.id)})
    if previous_status == OrderStatus.PENDIENTE_DE_PAGO:
        OutboxRepository.insert(db, "CANCEL_PAYMENT", {"order_id": str(order.id)})


    order = order_repository.update_status(
        db,
        order,
        OrderStatus.CANCELADA,
        origin="buyer",
    )
    _propagate_status_to_items(db, order, OrderStatus.CANCELADA.value, origin="buyer")

    try:
        process_order_outbox_synchronously(db, order.id)
    except Exception as exc:
        logger.warning("Inline outbox processing failed for order %s: %s", order.id, exc)

    return order_repository.get_by_id(db, order_id)


def cancel_order_item(
    db: Session,
    *,
    order_id: uuid.UUID,
    item_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    seller_id: uuid.UUID | None = None,
    by: str = "buyer",
) -> Order:
    """Cancel a single order item and close the order if nothing remains active."""
    order = order_repository.get_by_id(db, order_id)
    if order is None:
        raise OrderNotFoundError(str(order_id))

    if by == "buyer":
        if user_id is None or order.user_id != user_id:
            raise OrderAccessDeniedError()
    elif by == "seller":
        if seller_id is None or not any(item.seller_id == seller_id for item in order.items):
            raise OrderAccessDeniedError()
    else:
        raise ValueError(f"Unsupported cancellation actor: {by}")

    if OrderStatus(order.status) == OrderStatus.PENDIENTE_DE_PAGO:
        if by == "buyer":
            return cancel_order(db, order_id=order_id, user_id=user_id)
        else:
            raise InvalidStateTransitionError(
                current=order.status,
                target=OrderStatus.CANCELADA.value,
                detail="Cannot cancel an item before the order is confirmed. Please wait for the buyer to confirm the order first.",
                )

    item = order_repository.get_item_by_id(db, item_id)
    if item is None or item.order_id != order_id:
        raise OrderNotFoundError(f"OrderItem {item_id} not found for order {order_id}")
    if by == "seller" and item.seller_id != seller_id:
        raise OrderAccessDeniedError()

    previous_item_status = OrderItemLogisticStatus(item.status)
    previous_order_status = OrderStatus(order.status)
    validate_item_transition(previous_item_status, OrderItemLogisticStatus.CANCELADO)

    # Insert outbox event for item-specific stock release in the same transaction
    OutboxRepository.insert(db, "RELEASE_STOCK", {
        "order_id": str(order.id),
        "product_id": str(item.product_id),
    })


    order_repository.update_item_status(
        db,
        item,
        OrderItemLogisticStatus.CANCELADO.value,
        origin=by,
    )

    all_items_cancelled = all(
        order_item.status == OrderItemLogisticStatus.CANCELADO.value
        for order_item in order.items
    )

    if all_items_cancelled:
        order = order_repository.update_status(
            db,
            order,
            OrderStatus.CANCELADA,
            origin=by,
        )

    if previous_order_status in {OrderStatus.CONFIRMADA, OrderStatus.EN_PREPARACION}:
        _request_refund_for_item(db, str(order.id), item)

    try:
        process_order_outbox_synchronously(db, order.id)
    except Exception as exc:
        logger.warning("Inline outbox processing failed for order %s: %s", order.id, exc)

    return order_repository.get_by_id(db, order_id)


def _request_refund_for_item(db: Session, order_id: str, item: OrderItem) -> None:
    if item.status != OrderItemLogisticStatus.CANCELADO.value:
        return
"""
    response = payments_client.request_refund(
        order_id=order_id,
        order_item_id=str(item.id),
    )
    refund_id = response.get("refund_id")
    order_repository.update_item_status(
        db,
        item,
        OrderItemLogisticStatus.REEMBOLSO_EN_PROCESO.value,
        tracking_code=refund_id,
    ) 
"""


def _request_refunds_for_order(db: Session, order: Order) -> None:
    """Request refunds for all items in a cancelled order (best effort)."""
    if order.status != OrderStatus.CANCELADA.value:
        return
""" 
    refund_errors = []
    for item in order.items:
        try:
            _request_refund_for_item(db, str(order.id), item)
        except PaymentsServiceError as exc:
            logger.error(
                "Refund failed for order=%s item=%s: %s",
                order.id, item.id, str(exc),
            )
            refund_errors.append((str(item.id), str(exc)))

    try:
        order = order_repository.update_status(
            db,
            order,
            OrderStatus.REEMBOLSO_EN_PROCESO,
            origin="system",
        )
        logger.info("Order refund initiated: order=%s status=%s", order.id, OrderStatus.REEMBOLSO_EN_PROCESO)
    except Exception as exc:
        logger.error("Failed to transition to REEMBOLSO_EN_PROCESO for order=%s: %s", order.id, exc)
        return 
"""