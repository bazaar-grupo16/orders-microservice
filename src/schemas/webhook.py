"""
Payment webhook request schema.

Used by the Payments microservice to notify the result of a
payment attempt via ``POST /orders/{order_id}/payment-webhook``.
"""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from src.models.order_status import OrderStatus


class PaymentStatus(StrEnum):
    """Possible outcomes reported by the Payments service."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    REFUNDED = "REFUNDED"


def payment_status_to_order_status(payment_status: PaymentStatus) -> OrderStatus | None:
    """Translate a Payments status into the local financial order status."""

    if payment_status == PaymentStatus.APPROVED:
        return OrderStatus.CONFIRMADA
    if payment_status in {PaymentStatus.REJECTED, PaymentStatus.CANCELED, PaymentStatus.REFUNDED}:
        return OrderStatus.PAGO_RECHAZADO
    return None


class PaymentWebhookRequest(BaseModel):
    """Payload for the payment webhook callback."""

    status: PaymentStatus
    transaction_id: str

class RefundStatus(StrEnum):
    """Possible refund outcomes reported by the Payments service."""

    REFUND_APPROVED = "approved"
    REFUND_REJECTED = "rejected"
    REFUND_CANCELED = "canceled"

class RefundWebhookRequest(BaseModel):
    """Payload for the refund webhook callback."""

    order_item_id: UUID
    refund_status: RefundStatus
    
    
