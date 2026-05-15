"""
Order request and response schemas.

``CreateOrderRequest`` intentionally omits ``user_id`` — it is extracted
from the ``X-User-Id`` header injected by NGINX after JWT validation.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from src.models.order_status import ITEM_STATUS_TO_ORDER_STATUS, OrderItemLogisticStatus, OrderStatus
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

    @computed_field
    @property
    def aggregated_status(self) -> str | None:
        """Compute aggregated logistic status from items.
        
        Returns None if no items exist. Otherwise returns the most restrictive state:
        - If any item is EN_PREPARACION → EN_PREPARACION
        - If any item is ENVIADO → ENVIADA
        - If all are ENTREGADA → ENTREGADA
        - If all are CANCELADA → CANCELADA
        """
        if not self.items:
            return None

        if self.status in (
            OrderStatus.PENDIENTE_DE_PAGO, OrderStatus.PAGO_RECHAZADO, OrderStatus.CANCELADA,
            OrderStatus.REEMBOLSO_EN_PROCESO, OrderStatus.REEMBOLSO_PROCESADO
            ):
            return self.status
        
        item_statuses = {item.status for item in self.items}

        
        if len(item_statuses) == 1:
            single_status = item_statuses.pop()
            return ITEM_STATUS_TO_ORDER_STATUS.get(single_status, None)
        
        if OrderItemLogisticStatus.ENVIADO.value in item_statuses:
            return OrderStatus.ENVIADA.value
        if OrderItemLogisticStatus.EN_PREPARACION.value in item_statuses:
            return OrderStatus.EN_PREPARACION.value
        if OrderItemLogisticStatus.CONFIRMADO.value in item_statuses:
            return OrderStatus.CONFIRMADA.value
        if OrderItemLogisticStatus.ENTREGADO.value in item_statuses:
            return OrderStatus.ENTREGADA.value
        
        majority_status = max(item_statuses, key=lambda s: list(ITEM_STATUS_TO_ORDER_STATUS.keys()).index(s))
        return ITEM_STATUS_TO_ORDER_STATUS.get(majority_status, None)


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
