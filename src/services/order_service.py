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
from src.models.state_machine import InvalidStateTransitionError, validate_item_transition, validate_transition
from src.repositories import order_repository
from src.schemas.webhook import PaymentStatus, RefundStatus, payment_status_to_order_status

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
        product_ids = [str(item["product_id"]) for item in cart_items]
        stock_levels = _get_stock_levels(product_ids)
        insufficient_items = _build_insufficient_stock_items(cart_items, stock_levels)
        if insufficient_items:
            raise InsufficientStockError(
                "Insufficient stock for one or more cart items",
                items=insufficient_items,
            )
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
        order_repository.record_status_change(
            db,
            order_id=order.id,
            item_id=None,
            previous_status=None,
            new_status=OrderStatus.PENDIENTE_DE_PAGO.value,
            origin="system",
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
    # (PreferenceRequest.items: title, quantity, unit_price, line_item_id).
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

    target_status = payment_status_to_order_status(payment_status)
    if target_status is None:
        logger.error(
            "Ignoring unknown payment status: order=%s status=%s tx=%s",
            order_id, payment_status, transaction_id,
        )
        return order

    # Validate state machine transition.
    validate_transition(OrderStatus(order.status), target_status)

    previous_status = order.status

    # Update order status.
    update_order_status(
        db,
        order_id=order.id,
        new_status=target_status.value,
        tracking_code=transaction_id,  # Store transaction ID as tracking code for audit
    )

    order = order_repository.update_status(db, order, target_status)
    order_repository.record_status_change(
        db,
        order_id=order.id,
        item_id=None,
        previous_status=previous_status,
        new_status=target_status.value,
        origin="webhook",
    )
    logger.info(
        "Payment resolved: order=%s status=%s tx=%s",
        order_id, target_status, transaction_id,
    )



    # ── Post-resolution side effects ──────────────────
    if payment_status == PaymentStatus.APPROVED:
        _on_payment_success(order)
    else:
        _on_payment_failed(order)

    return order


def cancel_order(
    db: Session,
    *,
    order_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> Order:
    """Cancel an order as buyer or seller."""
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

    order = order_repository.update_status(db, order, OrderStatus.CANCELADA)
    order_repository.record_status_change(
        db,
        order_id=order.id,
        item_id=None,
        previous_status=previous_status.value,
        new_status=OrderStatus.CANCELADA.value,
        origin="buyer",
    )
    _propagate_status_to_items(db, order, OrderStatus.CANCELADA.value, origin="buyer")

    if previous_status in {OrderStatus.CONFIRMADA, OrderStatus.EN_PREPARACION}:
        _rollback_stock_for_items(order.items)

    # ── Refund flow if order was paid ─────────────────────
    if previous_status != OrderStatus.PENDIENTE_DE_PAGO:
        _request_refunds_for_order(db, order)

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

    # if order_status is PENDIENTE_DE_PAGO, cancelling an item is equivalent to cancelling the whole order, so delegate to cancel_order
    if OrderStatus(order.status) == OrderStatus.PENDIENTE_DE_PAGO:
        return cancel_order(db, order_id=order_id, user_id=user_id)

    item = order_repository.get_item_by_id(db, item_id)
    if item is None or item.order_id != order_id:
        raise OrderNotFoundError(f"OrderItem {item_id} not found for order {order_id}")
    if by == "seller" and item.seller_id != seller_id:
        raise OrderAccessDeniedError()

    previous_status = OrderItemLogisticStatus(item.status)
    validate_item_transition(previous_status, OrderItemLogisticStatus.CANCELADO)
    order_previous_status = order.status

    order_repository.update_item_status(
        db,
        item,
        OrderItemLogisticStatus.CANCELADO.value,
    )
    order_repository.record_status_change(
        db,
        order_id=order.id,
        item_id=item.id,
        previous_status=previous_status.value,
        new_status=OrderItemLogisticStatus.CANCELADO.value,
        origin=by,
    )

    if OrderStatus(order.status) in {OrderStatus.CONFIRMADA, OrderStatus.EN_PREPARACION}:
        _rollback_stock_for_items([item])

    all_items_cancelled = all(
        order_item.status == OrderItemLogisticStatus.CANCELADO.value
        for order_item in order.items
    )

    if all_items_cancelled:
        order = order_repository.update_status(db, order, OrderStatus.CANCELADA)
        order_repository.record_status_change(
            db,
            order_id=order.id,
            item_id=None,
            previous_status=order_previous_status,
            new_status=OrderStatus.CANCELADA.value,
            origin=by,
        )
        _propagate_status_to_items(db, order, OrderStatus.CANCELADA.value, origin=by)

        _request_refunds_for_order(db, order)
    elif OrderStatus(order.status) in {OrderStatus.CONFIRMADA, OrderStatus.EN_PREPARACION}:
        _request_refund_for_item(db, str(order.id), item)

    return order_repository.get_by_id(db, order_id)

def _request_refund_for_item(db: Session, order_id: str, item: OrderItem) -> None:
    if item.status != OrderItemLogisticStatus.CANCELADO.value:
        return  # Only refund cancelled items
"""     response = payments_client.request_refund(
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
    logger.info(
        "Refund requested successfully: order=%s item=%s refund_id=%s",
        order_id, item.id, refund_id,
    )
    # Record refund event in status history with refund_id tracking
    order_repository.record_status_change(
        db,
        order_id=order_id,
        item_id=item.id,
        previous_status=OrderItemLogisticStatus.CANCELADO.value,
        new_status=OrderItemLogisticStatus.REEMBOLSO_EN_PROCESO.value,
        origin="payments",
        tracking_code=refund_id,
    ) """

def _request_refunds_for_order(db: Session, order: Order) -> None:
    """Request refunds for all items in a cancelled order (best effort).
    
    Transitions order: CANCELADA → REEMBOLSO_EN_PROCESO → REEMBOLSO_PROCESADO.
    If any refund fails, errors are logged but do not block the operation.
    """
    if order.status != OrderStatus.CANCELADA.value:
        return


    # ── Step 1: Request refund for each item ────────────────
    refund_errors = []
    for item in order.items:
        try:
            _request_refund_for_item(db, str(order.id), item)
        except PaymentsServiceError as e:
            logger.error(
                "Refund failed for order=%s item=%s: %s",
                order.id, item.id, str(e),
            )
            refund_errors.append((str(item.id), str(e)))

    # ── Step 2: Transition to REEMBOLSO_EN_PROCESO ──────────
    try:
        order = order_repository.update_status(db, order, OrderStatus.REEMBOLSO_EN_PROCESO)
        order_repository.record_status_change(
            db,
            order_id=order.id,
            item_id=None,
            previous_status=OrderStatus.CANCELADA.value,
            new_status=OrderStatus.REEMBOLSO_EN_PROCESO.value,
            origin="system",
        )
        logger.info("Order refund initiated: order=%s status=%s", order.id, OrderStatus.REEMBOLSO_EN_PROCESO)
    except Exception as e:
        logger.error("Failed to transition to REEMBOLSO_EN_PROCESO for order=%s: %s", order.id, e)
        return

def resolve_refund(
    db: Session,
    *,
    order_id: uuid.UUID,
    order_item_id: uuid.UUID,
    refund_status: RefundStatus,
) -> None:
    """Process a refund webhook for an order item."""
    order = order_repository.get_by_id(db, order_id)
    if order is None:
        raise OrderNotFoundError(str(order_id))

    item = order_repository.get_item_by_id(db, order_item_id)
    if item is None or item.order_id != order_id:
        raise OrderNotFoundError(f"OrderItem {order_item_id} not found for order {order_id}")

    if refund_status != RefundStatus.REFUND_APPROVED:
        # TODO - handle refund failure case (e.g. transition back to CANCELADO or flag for manual review)
        # (no implementado porque está fuera de los C.A.)
        logger.error(
            "Refund failed for order=%s item=%s: refund_status=%s",
            order_id, order_item_id, refund_status,
        )
        return

    new_status = OrderItemLogisticStatus.REEMBOLSO_PROCESADO
    previous_status = OrderItemLogisticStatus(item.status)
    validate_item_transition(previous_status, new_status)

    order_repository.update_item_status(
        db,
        item,
        new_status.value,
    )
    order_repository.record_status_change(
        db,
        order_id=order_id,
        item_id=order_item_id,
        previous_status=previous_status.value,
        new_status=new_status.value,
        origin="webhook",
    )

    if all(
        OrderItemLogisticStatus(order_item.status) == OrderItemLogisticStatus.REEMBOLSO_PROCESADO
        for order_item in order.items
    ):
        previous_order_status = OrderStatus(order.status)
        validate_transition(previous_order_status, OrderStatus.REEMBOLSO_PROCESADO)
        order_repository.update_status(db, order, OrderStatus.REEMBOLSO_PROCESADO)
        order_repository.record_status_change(
            db,
            order_id=order_id,
            item_id=None,
            previous_status=previous_order_status.value,
            new_status=OrderStatus.REEMBOLSO_PROCESADO.value,
            origin="webhook",
        )
    logger.info(
        "Refund status updated: order=%s item=%s status=%s",
        order_id, order_item_id, new_status.value,
    )


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
    origin: str = "seller"
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

    current_status = OrderStatus(order.status)
    target_status = OrderStatus(new_status)

    # Validate transition
    validate_transition(current_status, target_status)

    # Apply tracking code if provided
    if tracking_code is not None:
        order = order_repository.update_tracking_code(db, order, tracking_code)

    # Apply status update
    order = order_repository.update_status(db, order, target_status)
    order_repository.record_status_change(
        db,
        order_id=order.id,
        item_id=None,
        previous_status=current_status.value,
        new_status=target_status.value,
        origin=origin,
        tracking_code=tracking_code,
    )

    _propagate_status_to_items(db, order, target_status.value, origin=origin)
    
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
    order_repository.record_status_change(
        db,
        order_id=item.order_id,
        item_id=item.id,
        previous_status=current_status.value,
        new_status=target_status.value,
        origin="seller",
        tracking_code=tracking_code,
    )
    
    logger.debug(
        "Item status updated: item=%s old_status=%s new_status=%s tracking=%s",
        item.id, current_status, target_status, tracking_code is not None,
    )
    
    return item

def confirm_item_delivery(
    db: Session,
    *,
    order_id: uuid.UUID,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Order:
    """Confirm delivery for a single order item by the buyer.

    Validates ownership and that the item is in ``ENVIADO`` before
    transitioning it to ``ENTREGADO`` and recording a history event
    with origin "buyer".
    """
    order = order_repository.get_by_id(db, order_id)
    if order is None:
        raise OrderNotFoundError(str(order_id))
    if order.user_id != user_id:
        raise OrderAccessDeniedError()

    item = order_repository.get_item_by_id(db, item_id)
    if item is None or item.order_id != order_id:
        raise OrderNotFoundError(f"OrderItem {item_id} not found for order {order_id}")

    current_status = OrderItemLogisticStatus(item.status)
    target_status = OrderItemLogisticStatus.ENTREGADO

    # Only allow transition ENVIADO -> ENTREGADO
    validate_item_transition(current_status, target_status)

    # Apply update
    order_repository.update_item_status(
        db,
        item,
        target_status.value,
    )
    order_repository.record_status_change(
        db,
        order_id=order.id,
        item_id=item.id,
        previous_status=current_status.value,
        new_status=target_status.value,
        origin="buyer",
    )

    logger.info("Item delivery confirmed: order=%s buyer=%s item=%s", order.id, user_id, item.id)
    # Return fresh order state
    return order_repository.get_by_id(db, order_id)

def _propagate_status_to_items(
    db: Session,
    order: Order,
    new_status: str,
    origin: str,
):
    if OrderStatus(new_status) == OrderStatus.CONFIRMADA:
        for item in order.items:
            previous_status = item.status
            order_repository.update_item_status(
                db, item, OrderItemLogisticStatus.CONFIRMADO.value
            )
            order_repository.record_status_change(
                db,
                order_id=order.id,
                item_id=item.id,
                previous_status=previous_status,
                new_status=OrderItemLogisticStatus.CONFIRMADO.value,
                origin=origin,
            )
    if OrderStatus(new_status) == OrderStatus.PAGO_RECHAZADO:
        for item in order.items:
            previous_status = item.status
            order_repository.update_item_status(
                db, item, OrderItemLogisticStatus.CANCELADO.value
            )
            order_repository.record_status_change(
                db,
                order_id=order.id,
                item_id=item.id,
                previous_status=previous_status,
                new_status=OrderItemLogisticStatus.CANCELADO.value,
                origin=origin,
            )
    if OrderStatus(new_status) == OrderStatus.CANCELADA:
        for item in order.items:
            if item.status == OrderItemLogisticStatus.CANCELADO.value:
                continue
            previous_status = item.status
            order_repository.update_item_status(
                db, item, OrderItemLogisticStatus.CANCELADO.value
            )
            order_repository.record_status_change(
                db,
                order_id=order.id,
                item_id=item.id,
                previous_status=previous_status,
                new_status=OrderItemLogisticStatus.CANCELADO.value,
                origin=origin,
            )


def _rollback_stock_for_items(items: list[OrderItem]) -> None:
    try:
        payload = [
            {"product_id": str(item.product_id), "quantity": item.quantity}
            for item in items
        ]
        inventory_client.rollback_stock(payload)
    except Exception:
        logger.error("Stock rollback failed for cancelled order item(s).")


def get_status_history(
    db: Session,
    *,
    order_id: uuid.UUID,
    seller_id: uuid.UUID | None = None,
) -> list:
    """Return status transition history for an order.

    If ``seller_id`` is provided, only item events for that seller are returned.
    """
    history = order_repository.get_status_history(
        db,
        order_id=order_id,
        seller_id=seller_id,
    )
    return list(history)