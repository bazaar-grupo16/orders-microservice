"""FastAPI dependencies for metrics query-parameter validation."""

from datetime import datetime
from typing import Annotated, Literal, TypeVar

from fastapi import Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from src.schemas.metrics import (
    DateRangeParams,
    ExportMetricsQueryParams,
    OrderSummaryQueryParams,
    TopSellingQueryParams,
)

T = TypeVar("T", bound=BaseModel)


def _parse_query_params(model: type[T], **values: object) -> T:
    """Parse query params into a Pydantic model and return HTTP 422 on failure."""
    try:
        return model(**values)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def get_date_range_params(
    start_date: Annotated[datetime, Query(..., description="Fecha de inicio del reporte")],
    end_date: Annotated[datetime, Query(..., description="Fecha de fin del reporte")],
) -> DateRangeParams:
    return _parse_query_params(
        DateRangeParams,
        start_date=start_date,
        end_date=end_date,
    )


def get_order_summary_query_params(
    start_date: Annotated[datetime, Query(..., description="Fecha de inicio del reporte")],
    end_date: Annotated[datetime, Query(..., description="Fecha de fin del reporte")],
    interval: Annotated[
        Literal["day", "week", "month"],
        Query(description="Intervalo de la serie de tiempo"),
    ] = "day",
) -> OrderSummaryQueryParams:
    return _parse_query_params(
        OrderSummaryQueryParams,
        start_date=start_date,
        end_date=end_date,
        interval=interval,
    )


def get_top_selling_query_params(
    start_date: Annotated[datetime, Query(..., description="Fecha de inicio del reporte")],
    end_date: Annotated[datetime, Query(..., description="Fecha de fin del reporte")],
    limit: Annotated[int, Query(ge=1, le=100, description="Límite de resultados")] = 10,
) -> TopSellingQueryParams:
    return _parse_query_params(
        TopSellingQueryParams,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


def get_export_metrics_query_params(
    start_date: Annotated[datetime, Query(..., description="Fecha de inicio del reporte")],
    end_date: Annotated[datetime, Query(..., description="Fecha de fin del reporte")],
    metric_type: Annotated[
        Literal["orders", "products", "categories"],
        Query(..., description="Tipo de métrica a exportar"),
    ],
) -> ExportMetricsQueryParams:
    return _parse_query_params(
        ExportMetricsQueryParams,
        start_date=start_date,
        end_date=end_date,
        metric_type=metric_type,
    )
