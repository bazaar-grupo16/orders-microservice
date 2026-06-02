"""Query-parameter schemas for admin order endpoints."""

from pydantic import BaseModel, Field

from src.models.order_status import OrderStatus


class AdminListOrdersQueryParams(BaseModel):
    page: int = Field(default=1, ge=1, description="Número de página")
    size: int = Field(default=20, ge=1, le=100, description="Tamaño de página")
    order_status: OrderStatus | None = Field(
        default=None,
        description="Filtrar por estado de la orden",
    )
