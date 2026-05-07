"""
Payment webhook request schema.

Used by the Payments microservice to notify the result of a
payment attempt via ``POST /orders/{order_id}/payment-webhook``.
"""

from enum import StrEnum

from pydantic import BaseModel


class PaymentStatus(StrEnum):
    """Possible outcomes reported by the Payments service."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class PaymentWebhookRequest(BaseModel):
    """Payload for the payment webhook callback."""

    status: PaymentStatus
    transaction_id: str
