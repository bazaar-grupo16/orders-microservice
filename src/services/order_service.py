"""
Order service — Saga orchestrator.

Contains the business logic for the checkout flow.  Coordinates
between the Cart, Inventory, Payments, and local persistence to
guarantee consistency through the Saga pattern.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from src.clients import cart_client, inventory_client, payments_client
from src.exceptions import (
    InsufficientStockError,
    OrderNotFoundError,
    OrderAccessDeniedError,
    PaymentsServiceError,
)
from src.models.order import Order
from src.models.order_item import OrderItem
from src.models.order_status import OrderItemLogisticStatus, OrderStatus
from src.models.state_machine import validate_item_transition, validate_transition
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
    """Execute the Saga to create a new order and obtain a payment link.

    Flow (Happy Path):
        1. Check idempotency → return existing order (with stored init_point) if duplicate.
        2. Fetch cart items from Cart service.
        3. Reserve stock in Inventory service.
        4. Persist order with status ``PENDIENTE_DE_PAGO``.
        5. Call Payments service to create MP preference and get ``init_point``.
        6. Persist ``preference_id`` and ``init_point`` on the order.
        7. Return the created order (with init_point populated).

    Compensation:
        - Step 3 fails: no rollback needed (nothing was reserved yet).
        - Step 4 (DB) fails after stock reserved: rollback stock reservation.
        - Step 5 (Payments) fails after order persisted: rollback stock +
          delete order from DB so the client can retry with the same
          Idempotency-Key.

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
        The newly created (or previously existing) order, always
        with ``init_point`` and ``preference_id`` populated.
    """
    # ── Step 1: Idempotency check ─────────────────────
    existing = order_repository.get_by_idempotency_key(db, idempotency_key)
    if existing is not None:
        logger.info(
            "Idempotent hit: key=%s → order=%s init_point_stored=%s",
            idempotency_key, existing.id, bool(existing.init_point),
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
            "seller_id": item.get("seller_id"),
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
            inventory_client.rollback_stock(reserve_payload)
        except Exception:
            logger.critical(
                "CRITICAL: Stock rollback failed for key=%s. "
                "Manual intervention required.",
                idempotency_key,
            )
        raise

    # ── Step 5: Create payment preference ─────────────
    # Build items in the shape expected by the Payments service
    # (PreferenceRequest.items: title, quantity, unit_price).
    preference_items = [
        {
            "title": item["product_name"],
            "quantity": item["quantity"],
            "unit_price": item["unit_price"],
        }
        for item in cart_items
    ]

    try:
        preference = payments_client.create_preference(
            order_id=str(order.id),
            items=preference_items,
            idempotency_key=idempotency_key,
        )
    except PaymentsServiceError:
        # Full compensation: Payments failed — undo stock and remove the order
        # so the client can safely retry with the same Idempotency-Key.
        logger.error(
            "Payments service failed — compensating: rollback stock + delete order=%s",
            order.id,
        )
        try:
            inventory_client.rollback_stock(reserve_payload)
        except Exception:
            logger.critical(
                "CRITICAL: Stock rollback failed after Payments error for key=%s. "
                "Manual intervention required.",
                idempotency_key,
            )
        try:
            order_repository.delete_order(db, order)
        except Exception:
            logger.critical(
                "CRITICAL: Order deletion failed after Payments error for key=%s order=%s. "
                "Manual intervention required.",
                idempotency_key, order.id,
            )
        raise

    # ── Step 6: Persist preference data on the order ──
    order = order_repository.update_payment_preference(
        db,
        order,
        preference_id=preference["preference_id"],
        init_point=preference["init_point"],
    )

    logger.info(
        "Order created: id=%s status=%s preference_id=%s",
        order.id, order.status, order.preference_id,
    )
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
        payload = [{"product_id": str(i.product_id), "quantity": i.quantity} for i in order.items]
        inventory_client.rollback_stock(payload)
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


def get_order_for_seller(
    db: Session,
    *,
    order_id: uuid.UUID,
    seller_id: uuid.UUID,
) -> Order:
    """Retrieve an order for a seller who has items in it.
    
    Does not validate that seller_id is the order owner, only that
    they have at least one item in the order.

    Raises
    ------
    OrderNotFoundError
        If the order does not exist.
    OrderAccessDeniedError
        If the seller has no items in the order.
    """
    order = order_repository.get_by_id(db, order_id)
    if order is None:
        raise OrderNotFoundError(str(order_id))
    
    # Verify seller has items in this order
    has_items = any(item.seller_id == seller_id for item in order.items)
    if not has_items:
        raise OrderAccessDeniedError()
    
    return order


def list_orders(
    db: Session,
    *,
    user_id: uuid.UUID,
    status_filter: str | None = None,
    seller_id: uuid.UUID | None = None,
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
        seller_id=seller_id,
        offset=offset,
        limit=size,
    )
    return list(orders), total


def list_sales(
    db: Session,
    *,
    seller_id: uuid.UUID,
    status_filter: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[Order], int]:
    """List orders that include items sold by *seller_id*.

    Returns full Order objects (with all items); the API layer will
    filter items to the specific seller before serialising the response.
    """
    offset = (page - 1) * size
    orders, total = order_repository.list_by_seller(
        db,
        seller_id=seller_id,
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
    
    _propagate_status_to_items(db, order, new_status)
    
    logger.info(
        "Order status updated: order=%s old_status=%s new_status=%s",
        order.id, order.status, new_status,
    )
    
    return order


def update_item_status(
    db: Session,
    *,
    item_id: uuid.UUID,
    new_status: str,
    tracking_code: str | None = None,
) -> OrderItem:
    """Update an order item's logistic status and optionally set tracking code.
    
    Parameters
    ----------
    item_id:
        UUID of the order item.
    new_status:
        New logistic status for the item.
    tracking_code:
        Optional tracking code to set on the item.
    
    Raises
    ------
    Exception
        If the item does not exist or transition is invalid.
    """
    item = order_repository.get_item_by_id(db, item_id)
    if item is None:
        raise OrderNotFoundError(f"OrderItem {item_id} not found")

    # Validate transition using item-specific state machine
    current_status = OrderItemLogisticStatus(item.status)
    target_status = OrderItemLogisticStatus(new_status)
    validate_item_transition(current_status, target_status)

    # Apply update
    item = order_repository.update_item_status(
        db, item, new_status, tracking_code=tracking_code
    )
    
    logger.debug(
        "Item status updated: item=%s old_status=%s new_status=%s tracking=%s",
        item.id, current_status, target_status, tracking_code is not None,
    )
    
    return item

def _propagate_status_to_items(
    db: Session,
    order: Order,
    new_status: str,
):
    if OrderStatus(new_status) == OrderStatus.CONFIRMADA:
        for item in order.items:
            order_repository.update_item_status(
                db, item, OrderItemLogisticStatus.CONFIRMADO.value
            )
    if OrderStatus(new_status) == OrderStatus.PAGO_RECHAZADO:
        for item in order.items:
            order_repository.update_item_status(
                db, item, OrderItemLogisticStatus.CANCELADO.value
            )