"""Domain models for the Orders microservice."""

from src.models.order import Order
from src.models.order_item import OrderItem
from src.models.order_status_history import OrderStatusHistory
from src.models.order_status import OrderStatus
from src.models.idempotency_key import IdempotencyKey
from src.models.outbox_event import OutboxEvent
from src.models.state_machine import (
    VALID_TRANSITIONS,
    InvalidStateTransitionError,
    validate_transition,
)

__all__ = [
    "Order",
    "OrderItem",
    "OrderStatusHistory",
    "OrderStatus",
    "IdempotencyKey",
    "OutboxEvent",
    "VALID_TRANSITIONS",
    "InvalidStateTransitionError",
    "validate_transition",
]

