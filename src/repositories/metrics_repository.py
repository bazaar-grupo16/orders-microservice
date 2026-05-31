from datetime import date, datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.models.order import Order
from src.models.order_item import OrderItem
from src.models.order_status import REVENUE_ELIGIBLE_STATUSES


class MetricsRepository:
    """Repository for executing database-level aggregations for metrics."""

    def __init__(self, session: Session):
        self.session = session

    def get_orders_summary_totals(
        self, start_date: datetime, end_date: datetime
    ) -> tuple[int, float]:
        """Return total order count and revenue from confirmed orders in a period."""
        date_filter = (
            Order.created_at >= start_date,
            Order.created_at <= end_date,
        )
        revenue_amount = case(
            (Order.status.in_(REVENUE_ELIGIBLE_STATUSES), Order.total_amount),
            else_=0,
        )

        query = select(
            func.count(Order.id).label("total_orders"),
            func.coalesce(func.sum(revenue_amount), 0).label("total_amount"),
        ).where(*date_filter)
        result = self.session.execute(query)
        row = result.first()
        if not row:
            return 0, 0.0
        return row.total_orders, float(row.total_amount)

    def get_orders_distribution_by_status(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, int]:
        """Calcula la cantidad de órdenes agrupadas por estado."""
        query = select(
            Order.status,
            func.count(Order.id).label("status_count")
        ).where(
            Order.created_at >= start_date,
            Order.created_at <= end_date
        ).group_by(Order.status)

        result = self.session.execute(query)
        return {row.status: row.status_count for row in result.all()}

    def get_orders_timeseries(
        self, start_date: datetime, end_date: datetime, interval: str
    ) -> list[dict[str, Any]]:
        """Calcula la serie de tiempo agrupando por intervalo (day, week, month)."""
        time_bucket = func.date_trunc(interval, Order.created_at).label("time_bucket")
        revenue_amount = case(
            (Order.status.in_(REVENUE_ELIGIBLE_STATUSES), Order.total_amount),
            else_=0,
        )

        query = select(
            time_bucket,
            func.count(Order.id).label("orders_count"),
            func.coalesce(func.sum(revenue_amount), 0).label("amount")
        ).where(
            Order.created_at >= start_date,
            Order.created_at <= end_date
        ).group_by(time_bucket).order_by(time_bucket.asc())

        result = self.session.execute(query)

        timeseries = []
        for row in result.all():
            bucket_date = row.time_bucket.date() if isinstance(row.time_bucket, datetime) else row.time_bucket
            timeseries.append({
                "date": bucket_date,
                "orders_count": row.orders_count,
                "amount": float(row.amount)
            })
        return timeseries

    def get_top_selling_products(
        self, start_date: datetime, end_date: datetime, limit: int
    ) -> list[dict[str, Any]]:
        """Devuelve los productos más vendidos en un rango de fechas."""
        query = select(
            OrderItem.product_id,
            OrderItem.product_name,
            func.sum(OrderItem.quantity).label("total_quantity_sold"),
            func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price), 0).label("total_revenue")
        ).join(
            Order, Order.id == OrderItem.order_id
        ).where(
            Order.created_at >= start_date,
            Order.created_at <= end_date,
            Order.status.in_(REVENUE_ELIGIBLE_STATUSES),
        ).group_by(
            OrderItem.product_id,
            OrderItem.product_name
        ).order_by(
            func.sum(OrderItem.quantity).desc()
        ).limit(limit)

        result = self.session.execute(query)
        return [
            {
                "product_id": row.product_id,
                "product_name": row.product_name,
                "total_quantity_sold": row.total_quantity_sold,
                "total_revenue": float(row.total_revenue)
            }
            for row in result.all()
        ]

    def get_category_sales(
        self, start_date: datetime, end_date: datetime
    ) -> list[dict[str, Any]]:
        """Devuelve las métricas de ventas agrupadas por categoría."""
        query = select(
            OrderItem.category,
            func.count(func.distinct(Order.id)).label("orders_count"),
            func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price), 0).label("total_amount")
        ).join(
            Order, Order.id == OrderItem.order_id
        ).where(
            Order.created_at >= start_date,
            Order.created_at <= end_date,
            Order.status.in_(REVENUE_ELIGIBLE_STATUSES),
        ).group_by(
            OrderItem.category
        ).order_by(
            func.sum(OrderItem.quantity * OrderItem.unit_price).desc()
        )

        result = self.session.execute(query)
        return [
            {
                "category": row.category,
                "orders_count": row.orders_count,
                "total_amount": float(row.total_amount)
            }
            for row in result.all()
        ]
