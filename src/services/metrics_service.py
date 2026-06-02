import csv
import io
import logging
from datetime import datetime

from src.core.observability import get_correlation_id
from src.repositories.metrics_repository import MetricsRepository
from src.schemas.metrics import (
    CategorySalesResponse,
    ExportMetricsQueryParams,
    OrderMetricSummary,
    OrderSummaryMetricResponse,
    OrderSummaryQueryParams,
    OrderTimeseriesData,
    TopSellingProductsResponse,
    TopSellingQueryParams,
)

logger = logging.getLogger(__name__)


class MetricsService:
    """Service layer for computing metrics."""

    def __init__(self, repository: MetricsRepository):
        self.repository = repository

    def get_orders_summary(self, params: OrderSummaryQueryParams) -> OrderSummaryMetricResponse:
        req_id = get_correlation_id()
        logger.info(
            "Calculando orders summary",
            extra={"request_id": req_id, "params": params.model_dump(mode="json")}
        )

        total_orders, total_amount = self.repository.get_orders_summary_totals(
            params.start_date, params.end_date
        )
        distribution = self.repository.get_orders_distribution_by_status(
            params.start_date, params.end_date
        )
        timeseries_data = self.repository.get_orders_timeseries(
            params.start_date, params.end_date, params.interval
        )

        return OrderSummaryMetricResponse(
            summary=OrderMetricSummary(
                total_orders_in_period=total_orders,
                total_amount_in_period=total_amount,
            ),
            distribution_by_status=distribution,
            timeseries=[OrderTimeseriesData(**t) for t in timeseries_data],
        )

    def get_top_selling_products(self, params: TopSellingQueryParams) -> TopSellingProductsResponse:
        req_id = get_correlation_id()
        logger.info(
            "Calculando top selling products",
            extra={"request_id": req_id, "params": params.model_dump(mode="json")}
        )

        items = self.repository.get_top_selling_products(
            params.start_date, params.end_date, params.limit
        )
        return TopSellingProductsResponse(items=items)

    def get_category_sales(self, start_date: datetime, end_date: datetime) -> CategorySalesResponse:
        req_id = get_correlation_id()
        logger.info(
            "Calculando category sales",
            extra={"request_id": req_id, "start_date": start_date.isoformat(), "end_date": end_date.isoformat()}
        )

        items = self.repository.get_category_sales(start_date, end_date)
        return CategorySalesResponse(items=items)

    def export_metrics_csv(self, params: ExportMetricsQueryParams) -> str:
        req_id = get_correlation_id()
        logger.info(
            "Exportando metricas a CSV para tipo: %s",
            params.metric_type,
            extra={"request_id": req_id, "params": params.model_dump(mode="json")}
        )

        output = io.StringIO()
        writer = csv.writer(output)

        if params.metric_type == "orders":
            timeseries_data = self.repository.get_orders_timeseries(
                params.start_date, params.end_date, "day"
            )
            writer.writerow(["Date", "Orders Count", "Amount"])
            for row in timeseries_data:
                writer.writerow([row["date"], row["orders_count"], row["amount"]])

        elif params.metric_type == "products":
            items = self.repository.get_top_selling_products(
                params.start_date, params.end_date, 1000
            )
            writer.writerow(["Product ID", "Product Name", "Total Quantity Sold", "Total Revenue"])
            for row in items:
                writer.writerow([
                    row["product_id"],
                    row["product_name"],
                    row["total_quantity_sold"],
                    row["total_revenue"],
                ])

        elif params.metric_type == "categories":
            items = self.repository.get_category_sales(
                params.start_date, params.end_date
            )
            writer.writerow(["Category", "Orders Count", "Total Amount"])
            for row in items:
                writer.writerow([row["category"], row["orders_count"], row["total_amount"]])

        return output.getvalue()
