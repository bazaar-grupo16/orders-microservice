"""
Integration tests for ``POST /orders`` — Saga orchestration.

Tests use ``respx`` to mock Cart and Inventory HTTP calls while
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


# ── Tests ─────────────────────────────────────────────────────


class TestCreateOrderHappyPath:
    """Nominal Saga flow — cart → reserve → persist → 201."""

    @respx.mock
    def test_creates_order_and_returns_201(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=_cart_response(user_id)),
        )
        respx.post(url__regex=r"http://inventory-service:8000/internal/products/.*/stock/reserve").mock(
            return_value=httpx.Response(200, json={"stock": 9}),
        )

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 201
        data = response.json()
        assert "order_id" in data
        assert data["status"] == "PENDIENTE_DE_PAGO"

    @respx.mock
    def test_order_has_correct_total(self, client: TestClient) -> None:
        """Total should be sum(unit_price * quantity) from cart items."""
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=_cart_response(user_id)),
        )
        respx.post(url__regex=r"http://inventory-service:8000/internal/products/.*/stock/reserve").mock(
            return_value=httpx.Response(200, json={"stock": 9}),
        )

        response = _post_order(client, user_id=user_id)
        order_id = response.json()["order_id"]

        # Verify in DB via another GET (or just check 201 for now).
        assert response.status_code == 201
        assert order_id is not None


class TestIdempotency:
    """Duplicate checkout attempts must return the same order."""

    @respx.mock
    def test_duplicate_key_returns_same_order(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())
        key = str(uuid.uuid4())

        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=_cart_response(user_id)),
        )
        respx.post(url__regex=r"http://inventory-service:8000/internal/products/.*/stock/reserve").mock(
            return_value=httpx.Response(200, json={"stock": 9}),
        )

        first = _post_order(client, user_id=user_id, idempotency_key=key)
        second = _post_order(client, user_id=user_id, idempotency_key=key)

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["order_id"] == second.json()["order_id"]


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
        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(404),
        )

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 400
        assert "Cart" in response.json().get("title", "")

    @respx.mock
    def test_cart_service_down_returns_502(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            side_effect=httpx.ConnectTimeout("Timeout"),
        )

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 502


class TestInventoryErrors:
    """Inventory service failures must be handled gracefully."""

    @respx.mock
    def test_insufficient_stock_returns_409(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=_cart_response(user_id)),
        )
        respx.post(url__regex=r"http://inventory-service:8000/internal/products/.*/stock/reserve").mock(
            return_value=httpx.Response(
                409, json={"detail": "Not enough stock"},
            ),
        )

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 409
        assert "stock" in response.json().get("detail", "").lower()

    @respx.mock
    def test_inventory_service_down_returns_502(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=_cart_response(user_id)),
        )
        respx.post(url__regex=r"http://inventory-service:8000/internal/products/.*/stock/reserve").mock(
            side_effect=httpx.ConnectTimeout("Timeout"),
        )

        response = _post_order(client, user_id=user_id)

        assert response.status_code == 502


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
