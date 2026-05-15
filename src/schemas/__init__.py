"""Pydantic schemas for the Orders microservice."""

from src.schemas.address import ShippingAddress
from src.schemas.error import ErrorResponse
from src.schemas.order import (
    CreateOrderRequest,
    OrderCreatedResponse,
    OrderItemDetailResponse,
    OrderItemResponse,
    OrderListResponse,
    OrderResponse,
    OrderStatusHistoryResponse,
)
from src.schemas.status import UpdateStatusRequest, UpdateItemStatusRequest
from src.schemas.webhook import PaymentStatus, PaymentWebhookRequest

__all__ = [
    "CreateOrderRequest",
    "ErrorResponse",
    "OrderCreatedResponse",
    "OrderItemDetailResponse",
    "OrderItemResponse",
    "OrderListResponse",
    "OrderResponse",
    "OrderStatusHistoryResponse",
    "PaymentStatus",
    "PaymentWebhookRequest",
    "ShippingAddress",
    "UpdateStatusRequest",
    "UpdateItemStatusRequest"
]
