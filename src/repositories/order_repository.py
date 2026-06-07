"""
Repository layer for Order persistence.

Pure data-access operations — **no business logic**.  Validation of
state transitions, ownership, or idempotency rules belongs in the
service layer.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import exists, func
from sqlalchemy.orm import Session

from src.models.order import Order
from src.models.order_item import OrderItem
from src.models.order_status_history import OrderStatusHistory

def create_order(
    db: Session,
    *,
    user_id: uuid.UUID,
    idempotency_key: str,
    shipping_address: dict,
    total_amount: float,
    status: str,
    items: list[dict],
    order_id: uuid.UUID | None = None,
) -> Order:
    """Persist a new order with its line-items.

    Parameters
    ----------
    items:
        List of dicts with keys ``product_id``, ``product_name``,
        ``quantity``, ``unit_price``.
    """
    order_kwargs = {
        "user_id": user_id,
        "idempotency_key": idempotency_key,
        "shipping_address": shipping_address,
        "total_amount": total_amount,
        "status": status,
    }
    if order_id is not None:
        order_kwargs["id"] = order_id

    order = Order(**order_kwargs)
    for item_data in items:
        order.items.append(OrderItem(**item_data))

    db.add(order)
    db.flush()

    create_status_change(
        db,
        order_id=order.id,
        item_id=None,
        previous_status=None,
        new_status=status,
        origin="buyer",
    )

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
    seller_id: uuid.UUID | None = None,
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
    if seller_id is not None:
        has_seller_items = exists().where(
            OrderItem.order_id == Order.id,
            OrderItem.seller_id == seller_id,
        )
        query = query.filter(has_seller_items)

    total = query.with_entities(func.count(Order.id)).scalar() or 0
    orders = (
        query
        .order_by(Order.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return orders, total


def list_all(
    db: Session,
    *,
    status_filter: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Order], int]:
    """Return a paginated list of all orders in the system."""
    query = db.query(Order)
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


def list_expired_pending_payment_orders(
    db: Session,
    *,
    cutoff: datetime,
    limit: int,
) -> Sequence[Order]:
    """Return pending-payment orders older than the expiration cutoff."""
    return (
        db.query(Order)
        .filter(Order.status == "PENDIENTE_DE_PAGO")
        .filter(Order.created_at <= cutoff)
        .order_by(Order.created_at.asc())
        .limit(limit)
        .all()
    )


def list_by_seller(
    db: Session,
    seller_id: uuid.UUID,
    *,
    status_filter: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Order], int]:
    """Return a paginated list of orders that contain at least one
    item sold by *seller_id*.

    This does not alter the items on the returned Order objects; the
    caller may filter items to the specific seller when serialising.
    """
    query = db.query(Order)
    if status_filter:
        query = query.filter(Order.status == status_filter)

    has_seller_items = exists().where(
        OrderItem.order_id == Order.id,
        OrderItem.seller_id == seller_id,
    )
    query = query.filter(has_seller_items)

    total = query.with_entities(func.count(Order.id)).scalar() or 0
    orders = (
        query
        .order_by(Order.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return orders, total


def update_status(
    db: Session,
    order: Order,
    new_status: str,
    *,
    origin: str = "system",
    tracking_code: str | None = None,
) -> Order:
    """Update the order's status and persist its transition history."""
    previous_status = order.status.value if hasattr(order.status, "value") else order.status
    new_status_value = new_status.value if hasattr(new_status, "value") else new_status
    order.status = new_status_value
    create_status_change(
        db,
        order_id=order.id,
        item_id=None,
        previous_status=previous_status,
        new_status=new_status_value,
        origin=origin,
        tracking_code=tracking_code,
    )
    db.commit()
    db.refresh(order)
    return order


def update_tracking_code(db: Session, order: Order, tracking_code: str) -> Order:
    """Set the tracking code on an order."""
    order.tracking_code = tracking_code
    db.commit()
    db.refresh(order)
    return order


def get_item_by_id(db: Session, item_id: uuid.UUID) -> OrderItem | None:
    """Return an order item by primary key, or ``None`` if not found."""
    return db.get(OrderItem, item_id)


def update_item_status(
    db: Session,
    item: OrderItem,
    new_status: str,
    tracking_code: str | None = None,
    *,
    origin: str = "system",
) -> OrderItem:
    """Update the item's logistic status, persist its history, and optionally set tracking code."""
    previous_status = item.status.value if hasattr(item.status, "value") else item.status
    new_status_value = new_status.value if hasattr(new_status, "value") else new_status
    item.status = new_status_value
    if tracking_code is not None:
        item.tracking_code = tracking_code
    create_status_change(
        db,
        order_id=item.order_id,
        item_id=item.id,
        previous_status=previous_status,
        new_status=new_status_value,
        origin=origin,
        tracking_code=tracking_code,
    )
    db.commit()
    db.refresh(item)
    return item


def get_items_by_order_and_seller(db: Session, order_id: uuid.UUID, seller_id: uuid.UUID) -> Sequence[OrderItem]:
    """Return all items from a given order sold by a specific seller."""
    return (
        db.query(OrderItem)
        .filter(OrderItem.order_id == order_id, OrderItem.seller_id == seller_id)
        .all()
    )


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

def create_status_change(
    db: Session,
    *,
    order_id: uuid.UUID,
    item_id: uuid.UUID | None,
    previous_status: str | None,
    new_status: str,
    origin: str,
    tracking_code: str | None = None,
) -> OrderStatusHistory:
    """Build and attach a status transition record without persisting it yet."""
    history = OrderStatusHistory(
        order_id=order_id,
        item_id=item_id,
        status_previous=previous_status,
        status_new=new_status,
        origin=origin,
        tracking_code=tracking_code,
    )
    db.add(history)
    return history

def get_status_history(
    db: Session,
    *,
    order_id: uuid.UUID,
    item_id: uuid.UUID | None = None,
    seller_id: uuid.UUID | None = None,
) -> Sequence[OrderStatusHistory]:
    """Return transition history for an order, sorted by timestamp ASC.

    If ``seller_id`` is provided, only events for items sold by that seller
    are returned (order-level events with ``item_id`` null are excluded).
    """
    query = db.query(OrderStatusHistory).filter(OrderStatusHistory.order_id == order_id)

    if item_id is not None:
        query = query.filter(OrderStatusHistory.item_id == item_id)

    if seller_id is not None:
        query = query.join(
            OrderItem,
            OrderItem.id == OrderStatusHistory.item_id,
        ).filter(OrderItem.seller_id == seller_id)

    return query.order_by(OrderStatusHistory.timestamp.asc()).all()


def delete_order(db: Session, order: Order) -> None:
    """Hard-delete an order from the database.

    Used exclusively for Saga compensation: if the Payments service call
    fails after the order has already been persisted, this function removes
    it so the client can retry the full flow using the same
    ``Idempotency-Key``.
    """
    db.delete(order)
    db.commit()
