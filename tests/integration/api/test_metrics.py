"""
Integration tests for metrics endpoints.

Tests run against a **real PostgreSQL** database (no DB mocking).
Only HTTP calls to external services (Cart, Inventory, Payments) are mocked
via ``respx`` when needed to seed data through the existing checkout flow.

Endpoints under test:
    GET /orders/admin/metrics/orders/summary
    GET /orders/admin/metrics/products/top-selling
    GET /orders/admin/metrics/categories/sales
    GET /orders/admin/metrics/orders/export
"""

import uuid
from datetime import datetime, timezone, timedelta

import httpx
import respx
from httpx import Response


# ── Helpers ───────────────────────────────────────────────────

_ADMIN_HEADERS = {"X-Is-Admin": "true"}


async def _get_metrics(
    client: httpx.AsyncClient,
    path: str,
    params: dict | None = None,
    *,
    headers: dict | None = None,
) -> Response:
    """GET a metrics endpoint with the admin header injected by default."""
    merged_headers = {**_ADMIN_HEADERS, **(headers or {})}
    return await client.get(path, params=params, headers=merged_headers)


_VALID_ADDRESS = {
    "street": "Av. Corrientes 1234",
    "city": "Buenos Aires",
    "state": "CABA",
    "zip_code": "C1043",
    "country": "Argentina",
}

_PREFERENCE_RESPONSE = {
    "preference_id": "mp-pref-metrics-test",
    "init_point": "https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id=mp-pref-metrics-test",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_range_params(days_back: int = 7) -> dict:
    """Return query params covering the last N days."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }


def _cart_response_with_category(user_id: str, product_id: str, category: str = "Electrónica") -> dict:
    return {
        "data": {
            "userId": user_id,
            "items": [
                {
                    "productId": product_id,
                    "title": "Laptop Gamer Pro",
                    "category": category,
                    "unitPrice": {"value": "1500.00", "updatedAt": _now()},
                    "quantity": 2,
                    "stock": 20,
                    "status": "active",
                    "addedAt": _now(),
                }
            ],
            "totalPrice": {"value": "3000.00", "updatedAt": _now()},
        }
    }


def _stock_response(product_id: str, quantity: int = 20) -> dict:
    return {"data": {product_id: quantity}}


async def _create_confirmed_order(client: httpx.AsyncClient, user_id: str, product_id: str, category: str = "Electrónica") -> dict:
    """Seed a CONFIRMADA order through the full checkout + webhook flow."""
    cart_payload = _cart_response_with_category(user_id, product_id, category)

    respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
        return_value=httpx.Response(200, json=cart_payload)
    )
    respx.post("http://inventory-service:8000/internal/products/stocks").mock(
        return_value=httpx.Response(200, json=_stock_response(product_id))
    )
    respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    respx.post("http://inventory-service:8000/internal/products/stock/commit").mock(
        return_value=httpx.Response(200, json={"data": {"order_id": "", "committed_products": []}})
    )
    respx.post("http://payments-service:8000/payments/preferences").mock(
        return_value=httpx.Response(201, json=_PREFERENCE_RESPONSE)
    )

    idem_key = str(uuid.uuid4())
    response = await client.post(
        "/orders",
        json={"shipping_address": _VALID_ADDRESS},
        headers={"X-User-Id": user_id, "Idempotency-Key": idem_key},
    )
    assert response.status_code == 201, f"Order creation failed: {response.text}"

    order_id = response.json()["order_id"]

    # Confirm payment via webhook
    respx.delete(f"http://cart-service:8000/cart/{user_id}").mock(
        return_value=httpx.Response(204)
    )
    webhook_resp = await client.post(
        f"/orders/{order_id}/payment-webhook",
        json={"status": "APPROVED", "transaction_id": str(uuid.uuid4())},
    )
    assert webhook_resp.status_code == 200, f"Webhook failed: {webhook_resp.text}"

    return response.json()


async def _create_pending_order(client: httpx.AsyncClient, user_id: str, product_id: str) -> dict:
    """Seed a PENDIENTE_DE_PAGO order without confirming payment."""
    cart_payload = _cart_response_with_category(user_id, product_id)

    respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
        return_value=httpx.Response(200, json=cart_payload)
    )
    respx.post("http://inventory-service:8000/internal/products/stocks").mock(
        return_value=httpx.Response(200, json=_stock_response(product_id))
    )
    respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    respx.post("http://payments-service:8000/payments/preferences").mock(
        return_value=httpx.Response(201, json=_PREFERENCE_RESPONSE)
    )

    response = await client.post(
        "/orders",
        json={"shipping_address": _VALID_ADDRESS},
        headers={"X-User-Id": user_id, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, f"Order creation failed: {response.text}"
    return response.json()


# ── Admin authorization ───────────────────────────────────────

class TestMetricsAdminAuth:
    """Metrics endpoints must require X-Is-Admin from the API Gateway."""

    async def test_non_admin_returns_403(self, client: httpx.AsyncClient) -> None:
        resp = await client.get(
            "/orders/admin/metrics/orders/summary",
            params=_date_range_params(),
            headers={"X-Is-Admin": "false"},
        )
        assert resp.status_code == 403

    async def test_missing_admin_header_returns_422(self, client: httpx.AsyncClient) -> None:
        resp = await client.get(
            "/orders/admin/metrics/orders/summary",
            params=_date_range_params(),
        )
        assert resp.status_code == 422


# ── Validation tests (no DB needed) ──────────────────────────

class TestDateRangeValidation:
    """Pydantic validators must reject invalid or oversized date ranges."""

    async def test_start_after_end_returns_422(self, client: httpx.AsyncClient) -> None:
        end = datetime.now(timezone.utc)
        start = end + timedelta(days=1)
        resp = await _get_metrics(
            client,
            "/orders/admin/metrics/orders/summary",
            params={
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        assert resp.status_code == 422
        errors = resp.json()
        # Pydantic validation error should mention the date range
        assert any(
            "start_date" in str(e).lower() or "posterior" in str(e).lower()
            for e in errors.get("detail", [])
        )

    async def test_range_exceeding_180_days_returns_422(self, client: httpx.AsyncClient) -> None:
        start = datetime.now(timezone.utc) - timedelta(days=200)
        end = datetime.now(timezone.utc)
        resp = await _get_metrics(
            client,
            "/orders/admin/metrics/orders/summary",
            params={
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        assert resp.status_code == 422

    async def test_missing_start_date_returns_422(self, client: httpx.AsyncClient) -> None:
        resp = await _get_metrics(
            client,
            "/orders/admin/metrics/orders/summary",
            params={"end_date": datetime.now(timezone.utc).isoformat()},
        )
        assert resp.status_code == 422

    async def test_missing_end_date_returns_422(self, client: httpx.AsyncClient) -> None:
        resp = await _get_metrics(
            client,
            "/orders/admin/metrics/orders/summary",
            params={"start_date": datetime.now(timezone.utc).isoformat()},
        )
        assert resp.status_code == 422

    async def test_invalid_interval_returns_422(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        params["interval"] = "year"  # not in Literal["day", "week", "month"]
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/summary", params=params)
        assert resp.status_code == 422

    async def test_valid_180_day_range_is_accepted(self, client: httpx.AsyncClient) -> None:
        start = datetime.now(timezone.utc) - timedelta(days=179)
        end = datetime.now(timezone.utc)
        resp = await _get_metrics(
            client,
            "/orders/admin/metrics/orders/summary",
            params={"start_date": start.isoformat(), "end_date": end.isoformat()},
        )
        assert resp.status_code == 200


# ── GET /orders/admin/metrics/orders/summary ────────────────────────

class TestOrdersSummaryEndpoint:
    """Tests for the /orders/admin/metrics/orders/summary endpoint."""

    async def test_returns_200_with_correct_schema_when_no_orders(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params(days_back=1)
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/summary", params=params)

        assert resp.status_code == 200
        body = resp.json()
        assert "summary" in body
        assert "distribution_by_status" in body
        assert "timeseries" in body
        assert isinstance(body["summary"]["total_orders_in_period"], int)
        assert isinstance(body["summary"]["total_amount_in_period"], float)
        assert isinstance(body["timeseries"], list)

    @respx.mock
    async def test_counts_confirmed_order_in_summary(self, client: httpx.AsyncClient) -> None:
        user_id = str(uuid.uuid4())
        product_id = str(uuid.uuid4())
        await _create_confirmed_order(client, user_id, product_id)

        params = _date_range_params(days_back=1)
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/summary", params=params)

        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["total_orders_in_period"] >= 1
        assert body["summary"]["total_amount_in_period"] > 0

    @respx.mock
    async def test_pending_orders_do_not_count_toward_revenue(self, client: httpx.AsyncClient) -> None:
        r = await _get_metrics(
            client,
            "/orders/admin/metrics/orders/summary",
            params=_date_range_params(days_back=1),
        )
        baseline_amount = r.json()["summary"]["total_amount_in_period"]

        user_id = str(uuid.uuid4())
        product_id = str(uuid.uuid4())
        await _create_pending_order(client, user_id, product_id)

        r2 = await _get_metrics(
            client,
            "/orders/admin/metrics/orders/summary",
            params=_date_range_params(days_back=1),
        )
        after = r2.json()

        assert after["distribution_by_status"].get("PENDIENTE_DE_PAGO", 0) >= 1
        assert after["summary"]["total_amount_in_period"] == baseline_amount

    @respx.mock
    async def test_distribution_by_status_contains_confirmada(self, client: httpx.AsyncClient) -> None:
        user_id = str(uuid.uuid4())
        product_id = str(uuid.uuid4())
        await _create_confirmed_order(client, user_id, product_id)

        params = _date_range_params(days_back=1)
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/summary", params=params)

        assert resp.status_code == 200
        distribution = resp.json()["distribution_by_status"]
        assert "CONFIRMADA" in distribution
        assert distribution["CONFIRMADA"] >= 1

    @respx.mock
    async def test_timeseries_has_entries_when_orders_exist(self, client: httpx.AsyncClient) -> None:
        user_id = str(uuid.uuid4())
        product_id = str(uuid.uuid4())
        await _create_confirmed_order(client, user_id, product_id)

        params = _date_range_params(days_back=7)
        params["interval"] = "day"
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/summary", params=params)

        assert resp.status_code == 200
        timeseries = resp.json()["timeseries"]
        assert len(timeseries) >= 1
        # Each item must conform to schema
        for entry in timeseries:
            assert "date" in entry
            assert "orders_count" in entry
            assert "amount" in entry

    async def test_timeseries_interval_week_returns_200(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params(days_back=30)
        params["interval"] = "week"
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/summary", params=params)
        assert resp.status_code == 200
        assert isinstance(resp.json()["timeseries"], list)

    async def test_timeseries_interval_month_returns_200(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params(days_back=90)
        params["interval"] = "month"
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/summary", params=params)
        assert resp.status_code == 200
        assert isinstance(resp.json()["timeseries"], list)

    async def test_request_id_propagated_in_response_header(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        request_id = str(uuid.uuid4())
        resp = await _get_metrics(
            client,
            "/orders/admin/metrics/orders/summary",
            params=params,
            headers={"X-Request-ID": request_id},
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-request-id") == request_id


# ── GET /orders/admin/metrics/products/top-selling ──────────────────

class TestTopSellingProductsEndpoint:
    """Tests for the /orders/admin/metrics/products/top-selling endpoint."""

    async def test_returns_200_with_empty_list_when_no_orders(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params(days_back=1)
        resp = await _get_metrics(client, "/orders/admin/metrics/products/top-selling", params=params)

        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert isinstance(body["items"], list)

    @respx.mock
    async def test_product_appears_in_ranking_after_confirmed_order(self, client: httpx.AsyncClient) -> None:
        user_id = str(uuid.uuid4())
        product_id = str(uuid.uuid4())
        await _create_confirmed_order(client, user_id, product_id)

        params = _date_range_params(days_back=1)
        resp = await _get_metrics(client, "/orders/admin/metrics/products/top-selling", params=params)

        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 1

        # Verify response schema
        item = items[0]
        assert "product_id" in item
        assert "product_name" in item
        assert "total_quantity_sold" in item
        assert "total_revenue" in item

    async def test_top_selling_respects_limit_param(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params(days_back=30)
        params["limit"] = 2
        resp = await _get_metrics(client, "/orders/admin/metrics/products/top-selling", params=params)

        assert resp.status_code == 200
        assert len(resp.json()["items"]) <= 2

    async def test_limit_exceeds_max_returns_422(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        params["limit"] = 200  # max is 100 per schema
        resp = await _get_metrics(client, "/orders/admin/metrics/products/top-selling", params=params)
        assert resp.status_code == 422

    async def test_limit_zero_returns_422(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        params["limit"] = 0
        resp = await _get_metrics(client, "/orders/admin/metrics/products/top-selling", params=params)
        assert resp.status_code == 422

    async def test_request_id_propagated_in_response_header(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        request_id = str(uuid.uuid4())
        resp = await _get_metrics(
            client,
            "/orders/admin/metrics/products/top-selling",
            params=params,
            headers={"X-Request-ID": request_id},
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-request-id") == request_id


# ── GET /orders/admin/metrics/categories/sales ──────────────────────

class TestCategorySalesEndpoint:
    """Tests for the /orders/admin/metrics/categories/sales endpoint."""

    async def test_returns_200_with_empty_list_when_no_orders(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params(days_back=1)
        resp = await _get_metrics(client, "/orders/admin/metrics/categories/sales", params=params)

        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert isinstance(body["items"], list)

    @respx.mock
    async def test_category_appears_after_confirmed_order(self, client: httpx.AsyncClient) -> None:
        user_id = str(uuid.uuid4())
        product_id = str(uuid.uuid4())
        await _create_confirmed_order(client, user_id, product_id, category="Electrónica")

        params = _date_range_params(days_back=1)
        resp = await _get_metrics(client, "/orders/admin/metrics/categories/sales", params=params)

        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 1

        # Verify schema
        item = next((i for i in items if i["category"] == "Electrónica"), None)
        assert item is not None
        assert "category" in item
        assert "orders_count" in item
        assert "total_amount" in item
        assert item["orders_count"] >= 1
        assert item["total_amount"] > 0

    async def test_request_id_propagated_in_response_header(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        request_id = str(uuid.uuid4())
        resp = await _get_metrics(
            client,
            "/orders/admin/metrics/categories/sales",
            params=params,
            headers={"X-Request-ID": request_id},
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-request-id") == request_id


# ── GET /orders/admin/metrics/orders/export ─────────────────────────

class TestExportEndpoint:
    """Tests for the /orders/admin/metrics/orders/export endpoint."""

    async def test_export_orders_returns_csv_content_type(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        params["metric_type"] = "orders"
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/export", params=params)

        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

    async def test_export_products_returns_csv_content_type(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        params["metric_type"] = "products"
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/export", params=params)

        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

    async def test_export_categories_returns_csv_content_type(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        params["metric_type"] = "categories"
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/export", params=params)

        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

    async def test_export_orders_has_correct_csv_headers(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        params["metric_type"] = "orders"
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/export", params=params)

        assert resp.status_code == 200
        first_line = resp.text.split("\r\n")[0]
        assert "Date" in first_line
        assert "Orders Count" in first_line
        assert "Amount" in first_line

    async def test_export_products_has_correct_csv_headers(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        params["metric_type"] = "products"
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/export", params=params)

        assert resp.status_code == 200
        first_line = resp.text.split("\r\n")[0]
        assert "Product ID" in first_line
        assert "Product Name" in first_line
        assert "Total Quantity Sold" in first_line
        assert "Total Revenue" in first_line

    async def test_export_categories_has_correct_csv_headers(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        params["metric_type"] = "categories"
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/export", params=params)

        assert resp.status_code == 200
        first_line = resp.text.split("\r\n")[0]
        assert "Category" in first_line
        assert "Orders Count" in first_line
        assert "Total Amount" in first_line

    async def test_export_invalid_metric_type_returns_422(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        params["metric_type"] = "invalid"
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/export", params=params)
        assert resp.status_code == 422

    @respx.mock
    async def test_export_includes_data_after_confirmed_order(self, client: httpx.AsyncClient) -> None:
        user_id = str(uuid.uuid4())
        product_id = str(uuid.uuid4())
        await _create_confirmed_order(client, user_id, product_id)

        params = _date_range_params(days_back=1)
        params["metric_type"] = "orders"
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/export", params=params)

        assert resp.status_code == 200
        lines = [line for line in resp.text.split("\r\n") if line]
        # At least header + 1 data row
        assert len(lines) >= 2

    async def test_export_content_disposition_header_present(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        params["metric_type"] = "orders"
        resp = await _get_metrics(client, "/orders/admin/metrics/orders/export", params=params)

        assert resp.status_code == 200
        disposition = resp.headers.get("content-disposition", "")
        assert "attachment" in disposition
        assert "metrics_orders.csv" in disposition

    async def test_export_date_range_validation_applies(self, client: httpx.AsyncClient) -> None:
        """CSV export must also enforce the 6-month limit."""
        start = datetime.now(timezone.utc) - timedelta(days=200)
        end = datetime.now(timezone.utc)
        resp = await _get_metrics(
            client,
            "/orders/admin/metrics/orders/export",
            params={
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "metric_type": "orders",
            },
        )
        assert resp.status_code == 422

    async def test_request_id_propagated_in_response_header(self, client: httpx.AsyncClient) -> None:
        params = _date_range_params()
        params["metric_type"] = "orders"
        request_id = str(uuid.uuid4())
        resp = await _get_metrics(
            client,
            "/orders/admin/metrics/orders/export",
            params=params,
            headers={"X-Request-ID": request_id},
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-request-id") == request_id
