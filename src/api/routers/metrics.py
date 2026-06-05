from typing import Annotated

from fastapi import APIRouter, Depends, Response

from src.api.dependencies.admin_orders import require_admin_role
from src.api.dependencies.metrics import (
    get_date_range_params,
    get_export_metrics_query_params,
    get_order_summary_query_params,
    get_top_selling_query_params,
)
from src.core.dependencies import DbSession
from src.repositories.metrics_repository import MetricsRepository
from src.schemas.metrics import (
    CategorySalesResponse,
    DateRangeParams,
    ExportMetricsQueryParams,
    OrderSummaryMetricResponse,
    OrderSummaryQueryParams,
    TopSellingProductsResponse,
    TopSellingQueryParams,
)
from src.services.metrics_service import MetricsService

router = APIRouter(
    prefix="/orders/admin/metrics",
    tags=["Metrics"],
    dependencies=[Depends(require_admin_role)],
)


def get_metrics_service(db: DbSession) -> MetricsService:
    repository = MetricsRepository(db)
    return MetricsService(repository)


MetricsServiceDep = Annotated[MetricsService, Depends(get_metrics_service)]


@router.get(
    "/orders/summary",
    response_model=OrderSummaryMetricResponse,
    summary="Resumen financiero y de órdenes",
)
def get_orders_summary(
    params: Annotated[OrderSummaryQueryParams, Depends(get_order_summary_query_params)],
    service: MetricsServiceDep,
) -> OrderSummaryMetricResponse:
    return service.get_orders_summary(params)


@router.get(
    "/products/top-selling",
    response_model=TopSellingProductsResponse,
    summary="Ranking de productos más vendidos",
)
def get_top_selling_products(
    params: Annotated[TopSellingQueryParams, Depends(get_top_selling_query_params)],
    service: MetricsServiceDep,
) -> TopSellingProductsResponse:
    return service.get_top_selling_products(params)


@router.get(
    "/categories/sales",
    response_model=CategorySalesResponse,
    summary="Métricas de ventas por categoría",
)
def get_category_sales(
    params: Annotated[DateRangeParams, Depends(get_date_range_params)],
    service: MetricsServiceDep,
) -> CategorySalesResponse:
    return service.get_category_sales(params.start_date, params.end_date)


@router.get(
    "/orders/export",
    summary="Exportar una sección de métricas a CSV",
    description=(
        "Descarga inmediata de un CSV según `metric_type`. "
        "No genera un único archivo con todo el panel: el backoffice debe "
        "invocar este endpoint una vez por tipo (`orders`, `products`, `categories`) "
        "usando el mismo rango de fechas.\n\n"
        "**Contenido por tipo:**\n"
        "- `orders`: serie temporal diaria (fecha, cantidad de órdenes, monto). "
        "No incluye totales del período ni distribución por estado.\n"
        "- `products`: ranking de productos (hasta 1000 filas).\n"
        "- `categories`: ventas agrupadas por categoría.\n\n"
        "Las métricas de usuarios registrados pertenecen al servicio de usuarios."
    ),
    response_class=Response,
)
def export_orders_metrics(
    params: Annotated[ExportMetricsQueryParams, Depends(get_export_metrics_query_params)],
    service: MetricsServiceDep,
) -> Response:
    csv_content = service.export_metrics_csv(params)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=metrics_{params.metric_type}.csv"
        }
    )
