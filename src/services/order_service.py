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
from src.exceptions import (
    InsufficientStockError,
    OrderNotFoundError,
    OrderAccessDeniedError,
)
from src.models.order import Order
from src.models.order_status import OrderStatus
from src.models.state_machine import validate_transition
from src.repositories import order_repository
from src.schemas.webhook import PaymentStatus

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


def resolve_payment(
    db: Session,
    *,
    order_id: uuid.UUID,
    payment_status: PaymentStatus,
    transaction_id: str,
) -> Order:
    """Process the payment webhook — Paso 6 of the Saga.

    **SUCCESS path:**
        1. Validate transition PENDIENTE_DE_PAGO → CONFIRMADA.
        2. Update order status.
        3. Commit stock reservation (Inventory).
        4. Clear the user's cart (Cart).

    **FAILED path:**
        1. Validate transition PENDIENTE_DE_PAGO → PAGO_RECHAZADO.
        2. Update order status.
        3. Rollback stock reservation (Inventory) — compensation.

    Parameters
    ----------
    order_id:
        UUID of the order being resolved.
    payment_status:
        ``SUCCESS`` or ``FAILED`` from the Payments service.
    transaction_id:
        External transaction reference for audit.

    Raises
    ------
    OrderNotFoundError
        If the order does not exist.
    InvalidStateTransitionError
        If the order is not in ``PENDIENTE_DE_PAGO`` state.
    """
    order = order_repository.get_by_id(db, order_id)
    if order is None:
        raise OrderNotFoundError(str(order_id))

    if payment_status == PaymentStatus.SUCCESS:
        target_status = OrderStatus.CONFIRMADA
    else:
        target_status = OrderStatus.PAGO_RECHAZADO

    # Validate state machine transition.
    validate_transition(OrderStatus(order.status), target_status)

    # Update order status.
    order = order_repository.update_status(db, order, target_status)
    logger.info(
        "Payment resolved: order=%s status=%s tx=%s",
        order_id, target_status, transaction_id,
    )

    # ── Post-resolution side effects ──────────────────
    if payment_status == PaymentStatus.SUCCESS:
        _on_payment_success(order)
    else:
        _on_payment_failed(order)

    return order


def _on_payment_success(order: Order) -> None:
    """Side effects after a successful payment (best effort)."""
    # Commit the stock reservation.
    try:
        inventory_client.commit_stock(order.idempotency_key)
    except Exception:
        logger.error(
            "Stock commit failed for order=%s. Manual intervention required.",
            order.id,
        )

    # Clear the user's cart.
    try:
        cart_client.delete_cart(str(order.user_id))
    except Exception:
        logger.error(
            "Cart clear failed for user=%s after order=%s.",
            order.user_id, order.id,
        )


def _on_payment_failed(order: Order) -> None:
    """Compensation after a failed payment (best effort)."""
    try:
        inventory_client.rollback_stock(order.idempotency_key)
    except Exception:
        logger.error(
            "Stock rollback failed for order=%s. Manual intervention required.",
            order.id,
        )


def get_order(
    db: Session,
    *,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Order:
    """Retrieve a single order, enforcing ownership.

    Raises
    ------
    OrderNotFoundError
        If the order does not exist.
    OrderAccessDeniedError
        If the order belongs to another user.
    """
    order = order_repository.get_by_id(db, order_id)
    if order is None:
        raise OrderNotFoundError(str(order_id))
    if order.user_id != user_id:
        raise OrderAccessDeniedError()
    return order


def list_orders(
    db: Session,
    *,
    user_id: uuid.UUID,
    status_filter: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[Order], int]:
    """Return a paginated list of orders for the authenticated user.

    Parameters
    ----------
    page:
        1-based page number.
    size:
        Number of orders per page.

    Returns
    -------
    tuple[list[Order], int]
        The page of orders and the total count.
    """
    offset = (page - 1) * size
    orders, total = order_repository.list_by_user(
        db,
        user_id,
        status_filter=status_filter,
        offset=offset,
        limit=size,
    )
    return list(orders), total


def update_order_status(
    db: Session,
    *,
    order_id: uuid.UUID,
    new_status: str,
    tracking_code: str | None = None,
) -> Order:
    """Update an order's status and optionally its tracking code.
    
    This is typically called by a Seller or internal admin.
    
    Raises
    ------
    OrderNotFoundError
        If the order does not exist.
    InvalidStateTransitionError
        If the transition is not allowed by the state machine.
    """
    order = order_repository.get_by_id(db, order_id)
    if order is None:
        raise OrderNotFoundError(str(order_id))

    # Validate transition
    validate_transition(OrderStatus(order.status), OrderStatus(new_status))

    # Apply tracking code if provided
    if tracking_code is not None:
        order = order_repository.update_tracking_code(db, order, tracking_code)

    # Apply status update
    order = order_repository.update_status(db, order, OrderStatus(new_status))
    
    logger.info(
        "Order status updated: order=%s old_status=%s new_status=%s",
        order.id, order.status, new_status,
    )
    
    return order
