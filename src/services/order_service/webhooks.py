"""Payment and refund webhook handlers."""

import logging
import uuid

from sqlalchemy.orm import Session

from src.clients import cart_client, inventory_client
from src.exceptions import OrderNotFoundError
from src.models.order import Order
from src.models.order_status import OrderItemLogisticStatus, OrderStatus
from src.models.state_machine import validate_item_transition, validate_transition
from src.repositories import order_repository
from src.schemas.webhook import PaymentStatus, RefundStatus, payment_status_to_order_status
from src.services.order_service.update import update_order_status


logger = logging.getLogger(__name__)


def resolve_payment(
	db: Session,
	*,
	order_id: uuid.UUID,
	payment_status: PaymentStatus,
	transaction_id: str,
) -> Order:
	"""Process the payment webhook — step 6 of the Saga."""
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

	validate_transition(OrderStatus(order.status), target_status)

	update_order_status(
		db,
		order_id=order.id,
		new_status=target_status.value,
		origin="webhook",
	)

	if payment_status == PaymentStatus.APPROVED:
		_on_payment_success(order)
	else:
		_on_payment_failed(order)

	return order_repository.get_by_id(db, order_id)


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
		origin="webhook",
	)

	if all(
		OrderItemLogisticStatus(order_item.status) == OrderItemLogisticStatus.REEMBOLSO_PROCESADO
		for order_item in order.items
	):
		previous_order_status = OrderStatus(order.status)
		validate_transition(previous_order_status, OrderStatus.REEMBOLSO_PROCESADO)
		order_repository.update_status(
			db,
			order,
			OrderStatus.REEMBOLSO_PROCESADO,
			origin="webhook",
		)


def _on_payment_success(order: Order) -> None:
	"""Side effects after a successful payment (best effort)."""
	try:
		inventory_client.commit_stock(order.idempotency_key)
	except Exception:
		logger.error(
			"Stock commit failed for order=%s. Manual intervention required.",
			order.id,
		)

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
