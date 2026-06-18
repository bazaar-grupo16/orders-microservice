"""Order and order-item status update operations."""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.clients import notifications_client
from src.exceptions import OrderAccessDeniedError, OrderNotFoundError
from src.models.order import Order
from src.models.order_item import OrderItem
from src.models.order_status import OrderItemLogisticStatus, OrderStatus
from src.models.state_machine import validate_item_transition, validate_item_transition_bool, validate_transition
from src.repositories import order_repository


logger = logging.getLogger(__name__)


async def update_order_status(
	db: AsyncSession,
	*,
	order_id: uuid.UUID,
	new_status: str,
	tracking_code: str | None = None,
	origin: str = "seller",
) -> Order:
	"""Update an order's status and optionally its tracking code."""
	order = await order_repository.get_by_id(db, order_id)
	if order is None:
		raise OrderNotFoundError(str(order_id))

	current_status = OrderStatus(order.status)
	target_status = OrderStatus(new_status)
	validate_transition(current_status, target_status)

	if tracking_code is not None:
		order = await order_repository.update_tracking_code(db, order, tracking_code)

	order = await order_repository.update_status(
		db,
		order,
		target_status,
		origin=origin,
		tracking_code=tracking_code,
	)

	await _propagate_status_to_items(db, order, target_status.value, origin=origin)
	await _notify_order_status_changed(order, target_status.value, tracking_code)

	return order


async def update_item_status(
	db: AsyncSession,
	*,
	item_id: uuid.UUID,
	new_status: str,
	tracking_code: str | None = None,
) -> OrderItem:
	"""Update an order item's logistic status and optionally set tracking code."""
	item = await order_repository.get_item_by_id(db, item_id)
	if item is None:
		raise OrderNotFoundError(f"OrderItem {item_id} not found")

	current_status = OrderItemLogisticStatus(item.status)
	target_status = OrderItemLogisticStatus(new_status)
	validate_item_transition(current_status, target_status)

	item = await order_repository.update_item_status(
		db,
		item,
		new_status,
		tracking_code=tracking_code,
		origin="seller",
	)

	await _notify_order_item_status_changed(db, item, new_status, tracking_code)

	logger.debug(
		"Item status updated: item=%s old_status=%s new_status=%s tracking=%s",
		item.id, current_status, target_status, tracking_code is not None,
	)

	return item


async def confirm_item_delivery(
	db: AsyncSession,
	*,
	order_id: uuid.UUID,
	item_id: uuid.UUID,
	user_id: uuid.UUID,
) -> Order:
	"""Confirm delivery for a single order item by the buyer."""
	order = await order_repository.get_by_id(db, order_id)
	if order is None:
		raise OrderNotFoundError(str(order_id))
	if order.user_id != user_id:
		raise OrderAccessDeniedError()

	item = await order_repository.get_item_by_id(db, item_id)
	if item is None or item.order_id != order_id:
		raise OrderNotFoundError(f"OrderItem {item_id} not found for order {order_id}")

	current_status = OrderItemLogisticStatus(item.status)
	target_status = OrderItemLogisticStatus.ENTREGADO
	validate_item_transition(current_status, target_status)

	await order_repository.update_item_status(
		db,
		item,
		target_status.value,
		origin="buyer",
	)

	logger.info("Item delivery confirmed: order=%s buyer=%s item=%s", order.id, user_id, item.id)
	return await order_repository.get_by_id(db, order_id)


async def _notify_order_status_changed(
	order: Order,
	new_status: str,
	tracking_code: str | None = None,
) -> None:
	"""Notify order status changes without affecting order persistence."""
	try:
		await notifications_client.notify_order_status_changed(
			order_id=order.id,
			order_items=order.items,
			buyer_id=order.user_id,
			new_status=new_status,
			tracking_code=tracking_code,
		)
	except Exception as exc:
		logger.warning(
			"Unexpected notifications client error: order=%s status=%s exc=%s",
			order.id,
			new_status,
			exc,
		)

async def _notify_order_item_status_changed(
	db: AsyncSession,
	item: OrderItem,
	new_status: str,
	tracking_code: str | None = None,
) -> None:
	"""Notify order item status changes without affecting order persistence."""
	order = await order_repository.get_by_id(db, item.order_id)
	try:
		await notifications_client.notify_order_item_status_changed(
			item=item,
			new_status=new_status,
			buyer_id=order.user_id,
			tracking_code=tracking_code,
		)
	except Exception as exc:
		logger.warning(
			"Unexpected notifications client error: order=%s item=%s status=%s exc=%s",
			item.order_id,
			item.id,
			new_status,
			exc,
)


async def _propagate_status_to_items(
	db: AsyncSession,
	order: Order,
	new_status: str,
	origin: str,
) -> None:
	target_order_status = OrderStatus(new_status)
	if target_order_status == OrderStatus.CONFIRMADA:
		for item in order.items:
			await order_repository.update_item_status(
				db,
				item,
				OrderItemLogisticStatus.CONFIRMADO.value,
				origin=origin,
			)
	elif target_order_status == OrderStatus.PAGO_RECHAZADO:
		for item in order.items:
			await order_repository.update_item_status(
				db,
				item,
				OrderItemLogisticStatus.CANCELADO.value,
				origin=origin,
			)
	elif target_order_status == OrderStatus.CANCELADA:
		for item in order.items:
			if validate_item_transition_bool(
				OrderItemLogisticStatus(item.status),
				OrderItemLogisticStatus.CANCELADO,
			):
				await order_repository.update_item_status(
					db,
					item,
					OrderItemLogisticStatus.CANCELADO.value,
					origin=origin,
				)

