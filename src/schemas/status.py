"""
Order status update request schema.

Used by sellers to advance an order through the fulfilment flow
via ``PATCH /orders/{order_id}/status``.
"""

from pydantic import BaseModel

from src.models.order_status import OrderStatus


class UpdateStatusRequest(BaseModel):
    """Payload for ``PATCH /orders/{order_id}/status``."""

    status: OrderStatus
    tracking_code: str | None = None
