"""Admin order read operations."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.exceptions import OrderNotFoundError
from src.models.order import Order
from src.repositories import order_repository


async def list_all_orders(
    db: AsyncSession,
    *,
    status_filter: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[Order], int]:
    """Return a paginated list of all orders (admin only)."""
    offset = (page - 1) * size
    orders, total = await order_repository.list_all(
        db,
        status_filter=status_filter,
        offset=offset,
        limit=size,
    )
    return list(orders), total


async def get_order_as_admin(
    db: AsyncSession,
    *,
    order_id: uuid.UUID,
) -> Order:
    """Retrieve any order by ID without ownership checks."""
    order = await order_repository.get_by_id(db, order_id)
    if order is None:
        raise OrderNotFoundError(str(order_id))
    return order

