"""
Order request and response schemas.

``CreateOrderRequest`` intentionally omits ``user_id`` — it is extracted
from the ``X-User-Id`` header injected by NGINX after JWT validation.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.address import ShippingAddress


# ── Requests ──────────────────────────────────────────────────


class CreateOrderRequest(BaseModel):
    """Payload for ``POST /orders``.

    ``user_id`` is NOT accepted here — it comes from the
    ``X-User-Id`` header set by the API Gateway.
    """

    shipping_address: ShippingAddress


# ── Responses ─────────────────────────────────────────────────


class OrderItemResponse(BaseModel):
    """A single line-item within an order response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    seller_id: UUID | None = None
    product_name: str
    quantity: int
    unit_price: float
    status: str
    tracking_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrderStatusHistoryResponse(BaseModel):
    """A status transition event for order-level or item-level flows."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    item_id: UUID | None = None
    previous_status: str | None = Field(default=None, validation_alias="status_previous")
    new_status: str = Field(validation_alias="status_new")
    timestamp: datetime
    origin: str
    tracking_code: str | None = None


class OrderItemDetailResponse(OrderItemResponse):
    """Order item with item-specific status history."""

    status_history: list[OrderStatusHistoryResponse] = []


class OrderResponse(BaseModel):
    """Full order detail returned by ``GET /orders/{id}``"""

    model_config = ConfigDict(from_attributes=True)

    order_id: UUID
    user_id: UUID
    status: str
    shipping_address: dict
    total_amount: float
    tracking_code: str | None = None
    preference_id: str | None = None
    init_point: str | None = None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemDetailResponse] = []
    status_history: list[OrderStatusHistoryResponse] = []


class OrderCreatedResponse(BaseModel):
    """Response for ``POST /orders`` — 201 Created.

    Includes the MercadoPago ``init_point`` URL that the frontend must
    use to redirect the user to the payment screen.
    """

    order_id: UUID
    status: str
    preference_id: str | None = None
    init_point: str | None = None


class OrderListResponse(BaseModel):
    """Paginated list of orders."""

    orders: list[OrderResponse]
    total: int
    page: int
    size: int
