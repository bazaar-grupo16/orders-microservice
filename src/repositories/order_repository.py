"""
Repository layer for Order persistence.

Pure data-access operations — **no business logic**.  Validation of
state transitions, ownership, or idempotency rules belongs in the
service layer.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models.order import Order
from src.models.order_item import OrderItem


def create_order(
    db: Session,
    *,
    user_id: uuid.UUID,
    idempotency_key: str,
    shipping_address: dict,
    total_amount: float,
    status: str,
    items: list[dict],
) -> Order:
    """Persist a new order with its line-items.

    Parameters
    ----------
    items:
        List of dicts with keys ``product_id``, ``product_name``,
        ``quantity``, ``unit_price``.
    """
    order = Order(
        user_id=user_id,
        idempotency_key=idempotency_key,
        shipping_address=shipping_address,
        total_amount=total_amount,
        status=status,
    )
    for item_data in items:
        order.items.append(OrderItem(**item_data))

    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def get_by_id(db: Session, order_id: uuid.UUID) -> Order | None:
    """Return an order by primary key, or ``None`` if not found."""
    return db.get(Order, order_id)


def get_by_idempotency_key(db: Session, key: str) -> Order | None:
    """Look up an order by its idempotency key."""
    return db.query(Order).filter(Order.idempotency_key == key).first()


def list_by_user(
    db: Session,
    user_id: uuid.UUID,
    *,
    status_filter: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Order], int]:
    """Return a paginated list of orders for a given user.

    Returns
    -------
    tuple[Sequence[Order], int]
        The page of orders and the total count (for pagination metadata).
    """
    query = db.query(Order).filter(Order.user_id == user_id)
    if status_filter:
        query = query.filter(Order.status == status_filter)

    total = query.with_entities(func.count(Order.id)).scalar() or 0
    orders = (
        query
        .order_by(Order.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return orders, total


def update_status(db: Session, order: Order, new_status: str) -> Order:
    """Update the order's status and persist."""
    order.status = new_status
    db.commit()
    db.refresh(order)
    return order


def update_tracking_code(db: Session, order: Order, tracking_code: str) -> Order:
    """Set the tracking code on an order."""
    order.tracking_code = tracking_code
    db.commit()
    db.refresh(order)
    return order


def update_payment_preference(
    db: Session,
    order: Order,
    *,
    preference_id: str,
    init_point: str,
) -> Order:
    """Persist the MercadoPago preference data on the order.

    Called immediately after a successful ``POST /payments/preferences``
    response so that the ``init_point`` is stored and can be returned
    on subsequent idempotent requests without calling Payments again.
    """
    order.preference_id = preference_id
    order.init_point = init_point
    db.commit()
    db.refresh(order)
    return order


def delete_order(db: Session, order: Order) -> None:
    """Hard-delete an order from the database.

    Used exclusively for Saga compensation: if the Payments service call
    fails after the order has already been persisted, this function removes
    it so the client can retry the full flow using the same
    ``Idempotency-Key``.
    """
    db.delete(order)
    db.commit()
