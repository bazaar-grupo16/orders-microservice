"""Pydantic schemas for the Orders microservice."""

from src.schemas.address import ShippingAddress
from src.schemas.error import ErrorResponse
from src.schemas.order import (
    CreateOrderRequest,
    OrderCreatedResponse,
    OrderItemResponse,
    OrderListResponse,
    OrderResponse,
)
from src.schemas.status import UpdateStatusRequest
from src.schemas.webhook import PaymentStatus, PaymentWebhookRequest

__all__ = [
    "CreateOrderRequest",
    "ErrorResponse",
    "OrderCreatedResponse",
    "OrderItemResponse",
    "OrderListResponse",
    "OrderResponse",
    "PaymentStatus",
    "PaymentWebhookRequest",
    "ShippingAddress",
    "UpdateStatusRequest",
]
