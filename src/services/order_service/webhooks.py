"""Payment and refund webhook handlers."""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.exceptions import OrderNotFoundError
from src.models.order import Order
from src.models.order_status import OrderItemLogisticStatus, OrderStatus
from src.models.state_machine import validate_item_transition, validate_transition
from src.repositories import order_repository
from src.repositories.outbox_repository import OutboxRepository
from src.schemas.webhook import PaymentStatus, RefundStatus, payment_status_to_order_status
from src.services.outbox_worker import process_order_outbox_synchronously




logger = logging.getLogger(__name__)


async def resolve_payment(
	db: AsyncSession,
	*,
	order_id: uuid.UUID,
	payment_status: PaymentStatus,
	transaction_id: str,
) -> Order:
	"""Process the payment webhook — step 6 of the Saga."""
	order = await order_repository.get_by_id(db, order_id)
	if order is None:
		raise OrderNotFoundError(str(order_id))

	target_status = payment_status_to_order_status(payment_status)
	if target_status is None:
		logger.error(
			"Ignoring unknown payment status: order=%s status=%s tx=%s",
			order_id, payment_status, transaction_id,
		)
		return order

	validate_transition(OrderStatus(order.status), target_status)

	if payment_status == PaymentStatus.APPROVED:
		await OutboxRepository.insert(db, "PAYMENT_SUCCESS", {
			"order_id": str(order.id),
			"user_id": str(order.user_id),
			"status": target_status.value,
		})
	else:
		await OutboxRepository.insert(db, "PAYMENT_FAILED", {
			"order_id": str(order.id),
			"status": target_status.value,
		})

	await db.commit()

	try:
		await process_order_outbox_synchronously(db, order.id)
	except Exception as exc:
		logger.warning("Inline outbox processing failed for order %s: %s", order.id, exc)

	return await order_repository.get_by_id(db, order_id)


async def resolve_refund(
	db: AsyncSession,
	*,
	order_id: uuid.UUID,
	order_item_id: uuid.UUID,
	refund_status: RefundStatus,
) -> None:
	"""Process a refund webhook for an order item."""
	order = await order_repository.get_by_id(db, order_id)
	if order is None:
		raise OrderNotFoundError(str(order_id))

	item = await order_repository.get_item_by_id(db, order_item_id)
	if item is None or item.order_id != order_id:
		raise OrderNotFoundError(f"OrderItem {order_item_id} not found for order {order_id}")

	if refund_status != RefundStatus.REFUND_APPROVED:
		logger.error(
			"Refund failed for order=%s item=%s: refund_status=%s",
			order_id, order_item_id, refund_status,
		)
		return

	new_status = OrderItemLogisticStatus.REEMBOLSO_PROCESADO
	previous_status = OrderItemLogisticStatus(item.status)
	validate_item_transition(previous_status, new_status)

	await order_repository.update_item_status(
		db,
		item,
		new_status.value,
		origin="webhook",
	)

	if all(
		OrderItemLogisticStatus(order_item.status) == OrderItemLogisticStatus.REEMBOLSO_PROCESADO
		for order_item in order.items
	):
		previous_order_status = OrderStatus(order.status)
		validate_transition(previous_order_status, OrderStatus.REEMBOLSO_PROCESADO)
		await order_repository.update_status(
			db,
			order,
			OrderStatus.REEMBOLSO_PROCESADO,
			origin="webhook",
		)