"""Order creation workflow and stock helper utilities."""

import logging
from typing import Any
import uuid

from sqlalchemy.orm import Session

from src.clients import cart_client, inventory_client, payments_client
from src.exceptions import InsufficientStockError, PaymentsServiceError
from src.models.order import Order
from src.models.order_status import OrderStatus
from src.repositories import order_repository


logger = logging.getLogger(__name__)


def create_order(
    db: Session,
    *,
    user_id: uuid.UUID,
    idempotency_key: str,
    shipping_address: dict,
) -> Order:
    """Execute the Saga to create a new order and obtain a payment link."""
    existing = order_repository.get_by_idempotency_key(db, idempotency_key)
    if existing is not None:
        logger.info(
            "Idempotent hit: key=%s → order=%s init_point_stored=%s",
            idempotency_key, existing.id, bool(existing.init_point),
        )
        return existing

    cart_items = cart_client.get_cart(user_id)
    
    reserve_payload = _reserve_stock(cart_items)

    order = _create_order_in_db(db, user_id, idempotency_key, shipping_address, cart_items, reserve_payload)

    order = _create_order_payment_preference(db, order, idempotency_key, reserve_payload)

    logger.info(
        "Order created: id=%s status=%s preference_id=%s",
        order.id, order.status, order.preference_id,
    )
    return order


def _reserve_stock(cart_items: list[dict[str, Any]]):
    reserve_payload = [
        {"product_id": item["product_id"], "quantity": item["quantity"]}
        for item in cart_items
    ]
    try:
        inventory_client.reserve_stock(reserve_payload)
    except InsufficientStockError:
        product_ids = [str(item["product_id"]) for item in cart_items]
        stock_levels = _get_stock_levels(product_ids)
        insufficient_items = _build_insufficient_stock_items(cart_items, stock_levels)
        if insufficient_items:
            raise InsufficientStockError(
                "Insufficient stock for one or more cart items",
                items=insufficient_items,
            )
        raise
    return reserve_payload

def _create_order_in_db(
        db: Session,
        user_id: uuid.UUID,
        idempotency_key: str,
        shipping_address: dict,
        cart_items: list[dict[str, Any]],
        reserve_payload: list[dict[str, Any]],
) -> Order:
    total_amount = sum(item["unit_price"] * item["quantity"] for item in cart_items)

    order_items = [
        {
            "product_id": item["product_id"],
            "seller_id": item.get("seller_id"),
            "product_name": item["product_name"],
            "quantity": item["quantity"],
            "unit_price": item["unit_price"],
        }
        for item in cart_items
    ]

    try:
        return order_repository.create_order(
            db,
            user_id=user_id,
            idempotency_key=idempotency_key,
            shipping_address=shipping_address,
            total_amount=total_amount,
            status=OrderStatus.PENDIENTE_DE_PAGO,
            items=order_items,
        )
    except Exception:
        logger.error("Order persistence failed — rolling back stock reservation")
        try:
            inventory_client.rollback_stock(reserve_payload)
        except Exception:
            logger.critical(
                "CRITICAL: Stock rollback failed for key=%s. Manual intervention required.",
                idempotency_key,
            )
        raise

def _create_order_payment_preference(
        db: Session,
        order: Order,
        idempotency_key: str,
        reserve_payload: list[dict[str, Any]],
) -> Order:
    preference_items = [
        {
            "title": str(item.product_name),
            "quantity": str(item.quantity),
            "unit_price": str(item.unit_price),
            "line_item_id": str(item.id),
        }
        for item in order.items
    ]

    try:
        preference = payments_client.create_preference(
            order_id=str(order.id),
            items=preference_items,
            idempotency_key=idempotency_key,
        )
    except PaymentsServiceError:
        logger.error(
            "Payments service failed — compensating: rollback stock + delete order=%s",
            order.id,
        )
        try:
            inventory_client.rollback_stock(reserve_payload)
        except Exception:
            logger.critical(
                "CRITICAL: Stock rollback failed after Payments error for key=%s. Manual intervention required.",
                idempotency_key,
            )
        try:
            order_repository.delete_order(db, order)
        except Exception:
            logger.critical(
                "CRITICAL: Order deletion failed after Payments error for key=%s order=%s. Manual intervention required.",
                idempotency_key, order.id,
            )
        raise

    return order_repository.update_payment_preference(
        db,
        order,
        preference_id=preference["preference_id"],
        init_point=preference["init_point"],
    )

def _get_stock_levels(product_ids: list[str]) -> dict[str, int] | None:
    try:
        return inventory_client.get_stock_levels(product_ids)
    except Exception:
        return None


def _build_insufficient_stock_items(
    cart_items: list[dict],
    stock_levels: dict[str, int] | None,
) -> list[dict]:
    if stock_levels is None:
        return []

    insufficient_items: list[dict] = []
    for item in cart_items:
        product_id = str(item["product_id"])
        requested = int(item["quantity"])
        available = int(stock_levels.get(product_id, 0))
        if available < requested:
            insufficient_items.append(
                {
                    "product_id": product_id,
                    "product_name": item.get("product_name"),
                    "requested": requested,
                    "available": available,
                }
            )
    return insufficient_items