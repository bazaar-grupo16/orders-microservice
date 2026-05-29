from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


# -- Request Validation Schemas --

class DateRangeParams(BaseModel):
    start_date: datetime = Field(..., description="Fecha de inicio del reporte")
    end_date: datetime = Field(..., description="Fecha de fin del reporte")

    @model_validator(mode='after')
    def validate_date_range(self) -> 'DateRangeParams':
        if self.start_date > self.end_date:
            raise ValueError("start_date no puede ser posterior a end_date")
        
        # Validar máximo 6 meses (aprox 180 días)
        max_end_date = self.start_date + timedelta(days=180)
        if self.end_date > max_end_date:
            raise ValueError("El rango de fechas no puede exceder los 6 meses (180 días)")
            
        return self


class OrderSummaryQueryParams(DateRangeParams):
    interval: Literal["day", "week", "month"] = Field(
        default="day", description="Intervalo de la serie de tiempo"
    )


class TopSellingQueryParams(DateRangeParams):
    limit: int = Field(default=10, ge=1, le=100, description="Límite de resultados")


class ExportMetricsQueryParams(DateRangeParams):
    metric_type: Literal["orders", "products", "categories"] = Field(
        ..., description="Tipo de métrica a exportar"
    )


# -- Response Schemas --

class OrderMetricSummary(BaseModel):
    total_orders_in_period: int
    total_amount_in_period: float


class OrderTimeseriesData(BaseModel):
    date: date
    orders_count: int
    amount: float


class OrderSummaryMetricResponse(BaseModel):
    summary: OrderMetricSummary
    distribution_by_status: dict[str, int] = Field(
        description="Diccionario clave-valor con estado y cantidad (ej: 'CONFIRMADA': 100)"
    )
    timeseries: list[OrderTimeseriesData]


class TopSellingProductItem(BaseModel):
    product_id: UUID
    product_name: str
    total_quantity_sold: int
    total_revenue: float
    
    model_config = ConfigDict(from_attributes=True)


class TopSellingProductsResponse(BaseModel):
    items: list[TopSellingProductItem]


class CategorySalesItem(BaseModel):
    category: str
    orders_count: int
    total_amount: float
    
    model_config = ConfigDict(from_attributes=True)


class CategorySalesResponse(BaseModel):
    items: list[CategorySalesItem]
