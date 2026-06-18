"""Order read/query operations."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.exceptions import OrderAccessDeniedError, OrderNotFoundError
from src.models.order import Order
from src.repositories import order_repository


async def get_order(
	db: AsyncSession,
	*,
	order_id: uuid.UUID,
	user_id: uuid.UUID,
) -> Order:
	"""Retrieve a single order, enforcing ownership."""
	order = await order_repository.get_by_id(db, order_id)
	if order is None:
		raise OrderNotFoundError(str(order_id))
	if order.user_id != user_id:
		raise OrderAccessDeniedError()
	return order


async def get_order_for_seller(
	db: AsyncSession,
	*,
	order_id: uuid.UUID,
	seller_id: uuid.UUID,
) -> Order:
	"""Retrieve an order for a seller who has items in it."""
	order = await order_repository.get_by_id(db, order_id)
	if order is None:
		raise OrderNotFoundError(str(order_id))

	has_items = any(item.seller_id == seller_id for item in order.items)
	if not has_items:
		raise OrderAccessDeniedError()

	return order


async def list_orders(
	db: AsyncSession,
	*,
	user_id: uuid.UUID,
	status_filter: str | None = None,
	seller_id: uuid.UUID | None = None,
	page: int = 1,
	size: int = 20,
) -> tuple[list[Order], int]:
	"""Return a paginated list of orders belonging to the user."""
	offset = (page - 1) * size
	orders, total = await order_repository.list_by_user(
		db,
		user_id,
		status_filter=status_filter,
		seller_id=seller_id,
		offset=offset,
		limit=size,
	)
	return list(orders), total


async def list_sales(
	db: AsyncSession,
	*,
	seller_id: uuid.UUID,
	status_filter: str | None = None,
	page: int = 1,
	size: int = 20,
) -> tuple[list[Order], int]:
	"""List orders that include items sold by *seller_id*."""
	offset = (page - 1) * size
	orders, total = await order_repository.list_by_seller(
		db,
		seller_id=seller_id,
		status_filter=status_filter,
		offset=offset,
		limit=size,
	)
	return list(orders), total


async def get_status_history(
	db: AsyncSession,
	*,
	order_id: uuid.UUID,
	seller_id: uuid.UUID | None = None,
) -> list:
	"""Return status transition history for an order."""
	history = await order_repository.get_status_history(
		db,
		order_id=order_id,
		seller_id=seller_id,
	)
	return list(history)

