"""
Order service — Saga orchestrator.

Contains the business logic for the checkout flow.  Coordinates
between the Cart, Inventory, and local persistence to guarantee
consistency through the Saga pattern.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from src.clients import cart_client, inventory_client
from src.exceptions import InsufficientStockError
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
    """Execute the Saga to create a new order.

    Flow (Happy Path):
        1. Check idempotency → return existing order if duplicate.
        2. Fetch cart items from Cart service.
        3. Reserve stock in Inventory service.
        4. Persist order with status ``PENDIENTE_DE_PAGO``.
        5. Return the created order.

    Compensation:
        If persistence fails after stock was reserved, rollback the
        reservation in Inventory.

    Note: Cart clearing and stock commit happen later in the
    payment webhook (Paso 6 of the Saga).

    Parameters
    ----------
    db:
        SQLAlchemy session.
    user_id:
        The authenticated user's UUID (from ``X-User-Id``).
    idempotency_key:
        UUID string from the ``Idempotency-Key`` header.
    shipping_address:
        Validated address dict.

    Returns
    -------
    Order
        The newly created (or previously existing) order.
    """
    # ── Step 1: Idempotency check ─────────────────────
    existing = order_repository.get_by_idempotency_key(db, idempotency_key)
    if existing is not None:
        logger.info(
            "Idempotent hit: key=%s → order=%s", idempotency_key, existing.id,
        )
        return existing

    # ── Step 2: Fetch cart ────────────────────────────
    cart_items = cart_client.get_cart(str(user_id))

    # ── Step 3: Reserve stock ─────────────────────────
    reserve_payload = [
        {"product_id": item["product_id"], "quantity": item["quantity"]}
        for item in cart_items
    ]
    try:
        inventory_client.reserve_stock(reserve_payload)
    except InsufficientStockError:
        raise  # Propagate — no compensation needed (nothing reserved yet).

    # ── Step 4: Persist order ─────────────────────────
    total_amount = sum(
        item["unit_price"] * item["quantity"] for item in cart_items
    )

    order_items = [
        {
            "product_id": item["product_id"],
            "product_name": item["product_name"],
            "quantity": item["quantity"],
            "unit_price": item["unit_price"],
        }
        for item in cart_items
    ]

    try:
        order = order_repository.create_order(
            db,
            user_id=user_id,
            idempotency_key=idempotency_key,
            shipping_address=shipping_address,
            total_amount=total_amount,
            status=OrderStatus.PENDIENTE_DE_PAGO,
            items=order_items,
        )
    except Exception:
        # Compensation: rollback the stock reservation if DB fails.
        logger.error(
            "Order persistence failed — rolling back stock reservation",
        )
        try:
            inventory_client.rollback_stock(idempotency_key)
        except Exception:
            logger.critical(
                "CRITICAL: Stock rollback failed for key=%s. "
                "Manual intervention required.",
                idempotency_key,
            )
        raise

    logger.info("Order created: id=%s status=%s", order.id, order.status)
    return order
