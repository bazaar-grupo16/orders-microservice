"""Order creation workflow and stock helper utilities."""

import logging
from typing import Any
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from src.clients import cart_client, inventory_client, payments_client
from src.exceptions import InsufficientStockError, PaymentsServiceError, DuplicateRequest
from src.models.order import Order
from src.models.order_status import OrderStatus
from src.repositories import order_repository, idempotency_repository


logger = logging.getLogger(__name__)


async def create_order(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    idempotency_key: str,
    shipping_address: dict,
) -> Order:
    """Execute the Saga to create a new order and obtain a payment link."""
    order_id = uuid.uuid4()

    try:
        order_id = await _resolve_idempotency(db, idempotency_key, order_id)
    except DuplicateRequest as e:
        existing = await order_repository.get_by_idempotency_key(db, idempotency_key)
        if existing is None:
            raise e
        logger.info(
            "Idempotent hit: key=%s → order=%s init_point_stored=%s",
            idempotency_key, existing.id, bool(existing.init_point),
        )
        return existing

    cart_items = await cart_client.get_cart(user_id)
    
    await _reserve_stock(order_id, cart_items)

    order = await _create_order_in_db(db, order_id, user_id, idempotency_key, shipping_address, cart_items)

    order = await _create_order_payment_preference(db, order, idempotency_key)

    logger.info(
        "Order created: id=%s status=%s preference_id=%s",
        order.id, order.status, order.preference_id,
    )
    return order

async def _resolve_idempotency(db: AsyncSession, idempotency_key: str, order_id: uuid.UUID) -> uuid.UUID:
    existing = await idempotency_repository.get(db, idempotency_key)

    if existing is not None:
        logger.info(
            "Idempotent hit on get: key=%s -> order_id=%s",
            idempotency_key,
            existing.order_id,
        )
        raise DuplicateRequest(idempotency_key, existing.order_id)

    try:
        await idempotency_repository.create(db, idempotency_key, order_id)
    except IntegrityError:
        logger.warning("Race condition detected for idempotency key=%s", idempotency_key)
        await idempotency_repository.rollback(db)
        existing = await idempotency_repository.get(db, idempotency_key)
        if existing is not None:
            raise DuplicateRequest(idempotency_key, existing.order_id)
        raise

    return order_id



async def _reserve_stock(order_id: uuid.UUID, cart_items: list[dict[str, Any]]) -> None:
    try:
        await inventory_client.reserve_stock(order_id, cart_items)
    except InsufficientStockError:
        product_ids = [str(item["product_id"]) for item in cart_items]
        stock_levels = await _get_stock_levels(product_ids)
        insufficient_items = _build_insufficient_stock_items(cart_items, stock_levels)
        if insufficient_items:
            raise InsufficientStockError(
                "Insufficient stock for one or more cart items",
                items=insufficient_items,
            )
        raise

async def _create_order_in_db(
        db: AsyncSession,
        order_id: uuid.UUID,
        user_id: uuid.UUID,
        idempotency_key: str,
        shipping_address: dict,
        cart_items: list[dict[str, Any]],
) -> Order:
    total_amount = sum(item["unit_price"] * item["quantity"] for item in cart_items)

    order_items = [
        {
            "product_id": item["product_id"],
            "seller_id": item.get("seller_id"),
            "product_name": item["product_name"],
            "category": item.get("category", "Uncategorized"),
            "quantity": item["quantity"],
            "unit_price": item["unit_price"],
        }
        for item in cart_items
    ]

    try:
        return await order_repository.create_order(
            db,
            user_id=user_id,
            idempotency_key=idempotency_key,
            shipping_address=shipping_address,
            total_amount=total_amount,
            status=OrderStatus.PENDIENTE_DE_PAGO,
            items=order_items,
            order_id=order_id,
        )
    except Exception:
        logger.error("Order persistence failed — rolling back stock reservation")
        try:
            await inventory_client.release_stock(order_id)
        except Exception:
            logger.critical(
                "CRITICAL: Stock rollback failed for key=%s. Manual intervention required.",
                idempotency_key,
            )
        raise

async def _create_order_payment_preference(
        db: AsyncSession,
        order: Order,
        idempotency_key: str,
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
        preference = await payments_client.create_preference(
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
            await inventory_client.release_stock(order.id)
        except Exception:
            logger.critical(
                "CRITICAL: Stock rollback failed after Payments error for key=%s. Manual intervention required.",
                idempotency_key,
            )
        try:
            await order_repository.delete_order(db, order)
        except Exception:
            logger.critical(
                "CRITICAL: Order deletion failed after Payments error for key=%s order=%s. Manual intervention required.",
                idempotency_key, order.id,
            )
        raise

    return await order_repository.update_payment_preference(
        db,
        order,
        preference_id=preference["preference_id"],
        init_point=preference["init_point"],
    )

async def _get_stock_levels(product_ids: list[str]) -> dict[str, int] | None:
    try:
        return await inventory_client.get_stock_levels(product_ids)
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

