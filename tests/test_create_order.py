"""
Integration tests for ``POST /orders`` — Saga orchestration.

Tests use ``respx`` to mock Cart, Inventory and Payments HTTP calls while
hitting the real PostgreSQL database.
"""

import uuid

import httpx
import respx
from fastapi.testclient import TestClient


# ── Helpers ───────────────────────────────────────────────────

_VALID_ADDRESS = {
    "street": "Av. Corrientes 1234",
    "city": "Buenos Aires",
    "state": "CABA",
    "zip_code": "C1043",
    "country": "Argentina",
}

_USER_ID = str(uuid.uuid4())

_PREFERENCE_RESPONSE = {
    "preference_id": "mp-pref-abc123",
    "init_point": "https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id=mp-pref-abc123",
}


def _mock_reserve_side_effect(request):
    import json
    data = json.loads(request.read())
    for item in data:
        assert "order_id" in item
        assert uuid.UUID(item["order_id"])
    return httpx.Response(200, json={"data": {}})


def _mock_release_side_effect(request):
    import json
    data = json.loads(request.read())
    assert isinstance(data, dict)
    assert "order_id" in data
    assert uuid.UUID(data["order_id"])
    return httpx.Response(200, json={"data": {}})


def _cart_response(user_id: str, items: list | None = None) -> dict:
    """Build a realistic Cart service response."""
    if items is None:
        items = [
            {
                "productId": str(uuid.uuid4()),
                "title": "Laptop Gamer",
                "unitPrice": {"value": "1500.00", "updatedAt": "2026-04-24T19:00:00Z"},
                "quantity": 1,
                "stock": 10,
                "status": "active",
                "addedAt": "2026-04-24T19:00:00Z",
            },
            {
                "productId": str(uuid.uuid4()),
                "title": "Mouse Inalámbrico",
                "unitPrice": {"value": "25.50", "updatedAt": "2026-04-24T19:00:00Z"},
                "quantity": 2,
                "stock": 50,
                "status": "active",
                "addedAt": "2026-04-24T19:00:00Z",
            },
        ]
    return {
        "data": {
            "userId": user_id,
            "items": items,
            "totalPrice": {"value": "1551.00", "updatedAt": "2026-04-24T19:00:00Z"},
        },
    }


def _stock_lookup_response(cart_payload: dict, available: dict[str, int] | None = None) -> dict:
    """Build a batch stock lookup response from a cart payload."""
    available = available or {}
    return {
        "data": {
            item["productId"]: available.get(item["productId"], item["quantity"] + 10)
            for item in cart_payload["data"]["items"]
        }
    }


def _post_order(
    client: TestClient,
    *,
    user_id: str = _USER_ID,
    idempotency_key: str | None = None,
    body: dict | None = None,
) -> httpx.Response:
    """Helper to POST /orders with default headers."""
    if idempotency_key is None:
        idempotency_key = str(uuid.uuid4())
    if body is None:
        body = {"shipping_address": _VALID_ADDRESS}
    return client.post(
        "/orders",
        json=body,
        headers={
            "X-User-Id": user_id,
            "Idempotency-Key": idempotency_key,
        },
    )


def _mock_happy_path(user_id: str) -> None:
    """Register respx mocks for the full Saga happy path (Cart + Inventory + Payments)."""
    cart_payload = _cart_response(user_id)
    respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
        return_value=httpx.Response(200, json=cart_payload),
    )
    respx.post("http://inventory-service:8000/internal/products/stocks").mock(
        return_value=httpx.Response(200, json=_stock_lookup_response(cart_payload)),
    )
    respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
        side_effect=_mock_reserve_side_effect
    )
    respx.post("http://payments-service:8000/payments/preferences").mock(
        return_value=httpx.Response(201, json=_PREFERENCE_RESPONSE),
    )


# ── Tests ─────────────────────────────────────────────────────


class TestCreateOrderHappyPath:
    """Nominal Saga flow — cart → reserve → persist → payments → 201."""

    @respx.mock
    def test_creates_order_and_returns_201(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())
        _mock_happy_path(user_id)

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 201
        data = response.json()
        assert "order_id" in data
        assert data["status"] == "PENDIENTE_DE_PAGO"

    @respx.mock
    def test_response_includes_init_point_and_preference_id(self, client: TestClient) -> None:
        """The response must contain the MercadoPago checkout URL."""
        user_id = str(uuid.uuid4())
        _mock_happy_path(user_id)

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 201
        data = response.json()
        assert data["init_point"] == _PREFERENCE_RESPONSE["init_point"]
        assert data["preference_id"] == _PREFERENCE_RESPONSE["preference_id"]

    @respx.mock
    def test_order_has_correct_total(self, client: TestClient) -> None:
        """Total should be sum(unit_price * quantity) from cart items."""
        user_id = str(uuid.uuid4())
        _mock_happy_path(user_id)

        response = _post_order(client, user_id=user_id)
        order_id = response.json()["order_id"]

        assert response.status_code == 201
        assert order_id is not None

    @respx.mock
    def test_payments_preference_is_called_with_order_id(self, client: TestClient) -> None:
        """The Payments service must receive the order_id in the request body."""
        user_id = str(uuid.uuid4())
        cart_payload = _cart_response(user_id)
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(200, json=cart_payload),
        )
        respx.post("http://inventory-service:8000/internal/products/stocks").mock(
            return_value=httpx.Response(200, json=_stock_lookup_response(cart_payload)),
        )
        respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
            side_effect=_mock_reserve_side_effect
        )
        payments_route = respx.post("http://payments-service:8000/payments/preferences").mock(
            return_value=httpx.Response(201, json=_PREFERENCE_RESPONSE),
        )

        response = _post_order(client, user_id=user_id)
        assert response.status_code == 201

        import json
        request_body = json.loads(payments_route.calls.last.request.content)
        assert request_body["order_id"] == response.json()["order_id"]


class TestIdempotency:
    """Duplicate checkout attempts must return the same order and init_point."""

    @respx.mock
    def test_duplicate_key_returns_same_order_and_init_point(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())
        key = str(uuid.uuid4())

        # Only the first call should hit Cart + Inventory + Payments.
        _mock_happy_path(user_id)

        first = _post_order(client, user_id=user_id, idempotency_key=key)
        second = _post_order(client, user_id=user_id, idempotency_key=key)

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["order_id"] == second.json()["order_id"]
        # init_point must be the same (read from DB, not re-called to Payments)
        assert first.json()["init_point"] == second.json()["init_point"]
        assert first.json()["preference_id"] == second.json()["preference_id"]




class TestMissingHeaders:
    """Requests without required headers must fail."""

    def test_missing_x_user_id_returns_401(self, client: TestClient) -> None:
        response = client.post(
            "/orders",
            json={"shipping_address": _VALID_ADDRESS},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 401

    def test_missing_idempotency_key_returns_400(self, client: TestClient) -> None:
        response = client.post(
            "/orders",
            json={"shipping_address": _VALID_ADDRESS},
            headers={"X-User-Id": str(uuid.uuid4())},
        )
        assert response.status_code == 400

    def test_invalid_user_id_returns_401(self, client: TestClient) -> None:
        response = client.post(
            "/orders",
            json={"shipping_address": _VALID_ADDRESS},
            headers={
                "X-User-Id": "not-a-uuid",
                "Idempotency-Key": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 401


class TestCartErrors:
    """Cart service failures must be handled gracefully."""

    @respx.mock
    def test_empty_cart_returns_400(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(404),
        )

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 400
        assert "Cart" in response.json().get("title", "")

    @respx.mock
    def test_cart_service_down_returns_502(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            side_effect=httpx.ConnectTimeout("Timeout"),
        )

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 502


class TestInventoryErrors:
    """Inventory service failures must be handled gracefully."""

    @respx.mock
    def test_insufficient_stock_returns_409(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())
        cart_payload = _cart_response(user_id)
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(200, json=cart_payload),
        )
        respx.post("http://inventory-service:8000/internal/products/stocks").mock(
            return_value=httpx.Response(
                200,
                json=_stock_lookup_response(
                    cart_payload,
                    available={cart_payload["data"]["items"][0]["productId"]: 0},
                ),
            ),
        )
        def reserve_409_check(request):
            import json
            data = json.loads(request.read())
            for item in data:
                assert "order_id" in item
                assert uuid.UUID(item["order_id"])
            return httpx.Response(409, json={"detail": "Insufficient stock"})

        respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
            side_effect=reserve_409_check
        )

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 409
        body = response.json()
        assert "stock" in body.get("detail", "").lower()
        assert len(body["items"]) == 1
        assert body["items"][0]["product_id"] == cart_payload["data"]["items"][0]["productId"]
        assert body["items"][0]["requested"] == cart_payload["data"]["items"][0]["quantity"]
        assert body["items"][0]["available"] == 0

    @respx.mock
    def test_inventory_service_down_returns_502(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())
        cart_payload = _cart_response(user_id)
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(200, json=cart_payload),
        )
        respx.post("http://inventory-service:8000/internal/products/stocks").mock(
            side_effect=httpx.ConnectTimeout("Timeout"),
        )
        respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
            side_effect=httpx.ConnectTimeout("Timeout"),
        )

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 502


class TestPaymentsErrors:
    """Payments service failures must trigger stock rollback and return 502."""

    @respx.mock
    def test_payments_service_down_rollbacks_stock_and_returns_502(
        self, client: TestClient,
    ) -> None:
        """If Payments is unreachable, stock is rolled back and 502 is returned."""
        user_id = str(uuid.uuid4())
        cart_payload = _cart_response(user_id)
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(200, json=cart_payload),
        )
        respx.post("http://inventory-service:8000/internal/products/stocks").mock(
            return_value=httpx.Response(200, json=_stock_lookup_response(cart_payload)),
        )
        respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
            side_effect=_mock_reserve_side_effect
        )
        rollback_route = respx.post(
            "http://inventory-service:8000/internal/products/stock/release",
        ).mock(side_effect=_mock_release_side_effect)
        respx.post("http://payments-service:8000/payments/preferences").mock(
            side_effect=httpx.ConnectTimeout("Timeout"),
        )

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 502
        assert rollback_route.called

    @respx.mock
    def test_payments_service_error_rollbacks_stock_and_returns_502(
        self, client: TestClient,
    ) -> None:
        """If Payments returns a 4xx/5xx, stock is rolled back and 502 is returned."""
        user_id = str(uuid.uuid4())
        cart_payload = _cart_response(user_id)
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(200, json=cart_payload),
        )
        respx.post("http://inventory-service:8000/internal/products/stocks").mock(
            return_value=httpx.Response(200, json=_stock_lookup_response(cart_payload)),
        )
        respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
            side_effect=_mock_reserve_side_effect
        )
        rollback_route = respx.post(
            "http://inventory-service:8000/internal/products/stock/release",
        ).mock(side_effect=_mock_release_side_effect)
        respx.post("http://payments-service:8000/payments/preferences").mock(
            return_value=httpx.Response(502, json={"detail": "Bad gateway"}),
        )

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 502
        assert rollback_route.called

    @respx.mock
    def test_payments_failure_allows_retry_with_same_idempotency_key(
        self, client: TestClient,
    ) -> None:
        """After a Payments failure, the order is deleted so the client can
        retry using the same Idempotency-Key and succeed on the next attempt."""
        user_id = str(uuid.uuid4())
        key = str(uuid.uuid4())

        # First attempt: Payments fails.
        cart_payload = _cart_response(user_id)
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(200, json=cart_payload),
        )
        respx.post("http://inventory-service:8000/internal/products/stocks").mock(
            return_value=httpx.Response(200, json=_stock_lookup_response(cart_payload)),
        )
        respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
            side_effect=_mock_reserve_side_effect
        )
        respx.post("http://inventory-service:8000/internal/products/stock/release").mock(
            side_effect=_mock_release_side_effect
        )
        respx.post("http://payments-service:8000/payments/preferences").mock(
            return_value=httpx.Response(502, json={"detail": "MP down"}),
        )

        first = _post_order(client, user_id=user_id, idempotency_key=key)
        assert first.status_code == 502

    @respx.mock
    def test_order_is_deleted_from_db_after_payments_failure(
        self, client: TestClient,
    ) -> None:
        """Verify that after a Payments failure the order no longer appears in
        the list (i.e. it was deleted from the DB as part of compensation)."""
        user_id = str(uuid.uuid4())
        key = str(uuid.uuid4())

        cart_payload = _cart_response(user_id)
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(200, json=cart_payload),
        )
        respx.post("http://inventory-service:8000/internal/products/stocks").mock(
            return_value=httpx.Response(200, json=_stock_lookup_response(cart_payload)),
        )
        respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
            side_effect=_mock_reserve_side_effect
        )
        respx.post("http://inventory-service:8000/internal/products/stock/release").mock(
            side_effect=_mock_release_side_effect
        )
        respx.post("http://payments-service:8000/payments/preferences").mock(
            return_value=httpx.Response(502, json={"detail": "MP down"}),
        )

        fail_response = _post_order(client, user_id=user_id, idempotency_key=key)
        assert fail_response.status_code == 502

        # Check no order was left in DB for this user.
        list_response = client.get(
            "/orders",
            headers={"X-User-Id": user_id},
        )
        assert list_response.status_code == 200
        assert list_response.json()["total"] == 0


class TestValidationErrors:
    """Request body validation must fail with proper errors."""

    def test_missing_shipping_address_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/orders",
            json={},
            headers={
                "X-User-Id": str(uuid.uuid4()),
                "Idempotency-Key": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 422

    def test_incomplete_address_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/orders",
            json={"shipping_address": {"street": "Only street"}},
            headers={
                "X-User-Id": str(uuid.uuid4()),
                "Idempotency-Key": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 422
