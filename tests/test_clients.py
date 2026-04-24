"""
Tests for the Cart and Inventory HTTP clients.

Uses ``respx`` to mock outgoing HTTP requests — no real services needed.
The database is NOT involved in these tests.
"""

import uuid

import httpx
import pytest
import respx

from src.clients import cart_client, inventory_client
from src.exceptions import CartEmptyError, ExternalServiceError, InsufficientStockError


# ── Cart Client ───────────────────────────────────────────────


class TestGetCart:
    """Tests for ``cart_client.get_cart``."""

    @respx.mock
    def test_returns_normalised_items_on_success(self) -> None:
        user_id = str(uuid.uuid4())
        product_id = str(uuid.uuid4())
        cart_response = {
            "data": {
                "userId": user_id,
                "items": [
                    {
                        "productId": product_id,
                        "title": "Laptop Gamer",
                        "unitPrice": {"value": "1500.00", "updatedAt": "2026-04-24T19:00:00Z"},
                        "quantity": 1,
                        "stock": 10,
                        "status": "active",
                        "addedAt": "2026-04-24T19:00:00Z",
                    },
                ],
                "totalPrice": {"value": "1500.00", "updatedAt": "2026-04-24T19:00:00Z"},
            },
        }
        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=cart_response),
        )

        result = cart_client.get_cart(user_id)

        assert len(result) == 1
        # Verify normalisation from camelCase to snake_case.
        assert result[0]["product_id"] == product_id
        assert result[0]["product_name"] == "Laptop Gamer"
        assert result[0]["quantity"] == 1
        assert result[0]["unit_price"] == 1500.00

    @respx.mock
    def test_raises_cart_empty_on_404(self) -> None:
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(404),
        )

        with pytest.raises(CartEmptyError):
            cart_client.get_cart(user_id)

    @respx.mock
    def test_raises_cart_empty_on_empty_items(self) -> None:
        user_id = str(uuid.uuid4())
        cart_response = {
            "data": {
                "userId": user_id,
                "items": [],
                "totalPrice": {"value": "0.00", "updatedAt": "2026-04-24T19:00:00Z"},
            },
        }
        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=cart_response),
        )

        with pytest.raises(CartEmptyError):
            cart_client.get_cart(user_id)

    @respx.mock
    def test_raises_external_error_on_500(self) -> None:
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(500, json={"detail": "Internal error"}),
        )

        with pytest.raises(ExternalServiceError) as exc_info:
            cart_client.get_cart(user_id)
        assert exc_info.value.service == "cart-service"

    @respx.mock
    def test_raises_external_error_on_timeout(self) -> None:
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            side_effect=httpx.ConnectTimeout("Timeout"),
        )

        with pytest.raises(ExternalServiceError):
            cart_client.get_cart(user_id)


class TestDeleteCart:
    """Tests for ``cart_client.delete_cart``."""

    @respx.mock
    def test_succeeds_on_204(self) -> None:
        user_id = str(uuid.uuid4())
        respx.delete(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(204),
        )

        # Should not raise.
        cart_client.delete_cart(user_id)

    @respx.mock
    def test_succeeds_on_200(self) -> None:
        user_id = str(uuid.uuid4())
        respx.delete(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200),
        )

        cart_client.delete_cart(user_id)

    @respx.mock
    def test_raises_external_error_on_failure(self) -> None:
        user_id = str(uuid.uuid4())
        respx.delete(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(500),
        )

        with pytest.raises(ExternalServiceError):
            cart_client.delete_cart(user_id)


# ── Inventory Client ──────────────────────────────────────────


class TestReserveStock:
    """Tests for ``inventory_client.reserve_stock``."""

    @respx.mock
    def test_returns_reservation_on_success(self) -> None:
        items = [{"product_id": str(uuid.uuid4()), "quantity": 2}]
        reservation = {"reservation_id": "res-123"}
        respx.post("http://inventory-service:8000/inventory/reserve").mock(
            return_value=httpx.Response(200, json=reservation),
        )

        result = inventory_client.reserve_stock(items)

        assert result["reservation_id"] == "res-123"

    @respx.mock
    def test_raises_insufficient_stock_on_409(self) -> None:
        items = [{"product_id": str(uuid.uuid4()), "quantity": 999}]
        respx.post("http://inventory-service:8000/inventory/reserve").mock(
            return_value=httpx.Response(
                409, json={"detail": "Not enough stock for product X"},
            ),
        )

        with pytest.raises(InsufficientStockError) as exc_info:
            inventory_client.reserve_stock(items)
        assert "Not enough stock" in exc_info.value.detail

    @respx.mock
    def test_raises_external_error_on_500(self) -> None:
        items = [{"product_id": str(uuid.uuid4()), "quantity": 1}]
        respx.post("http://inventory-service:8000/inventory/reserve").mock(
            return_value=httpx.Response(500),
        )

        with pytest.raises(ExternalServiceError):
            inventory_client.reserve_stock(items)

    @respx.mock
    def test_raises_external_error_on_timeout(self) -> None:
        items = [{"product_id": str(uuid.uuid4()), "quantity": 1}]
        respx.post("http://inventory-service:8000/inventory/reserve").mock(
            side_effect=httpx.ConnectTimeout("Timeout"),
        )

        with pytest.raises(ExternalServiceError):
            inventory_client.reserve_stock(items)


class TestCommitStock:
    """Tests for ``inventory_client.commit_stock``."""

    @respx.mock
    def test_succeeds_on_200(self) -> None:
        respx.post("http://inventory-service:8000/inventory/commit").mock(
            return_value=httpx.Response(200),
        )

        inventory_client.commit_stock("res-123")

    @respx.mock
    def test_raises_external_error_on_failure(self) -> None:
        respx.post("http://inventory-service:8000/inventory/commit").mock(
            return_value=httpx.Response(500),
        )

        with pytest.raises(ExternalServiceError):
            inventory_client.commit_stock("res-123")


class TestRollbackStock:
    """Tests for ``inventory_client.rollback_stock``."""

    @respx.mock
    def test_succeeds_on_200(self) -> None:
        respx.post("http://inventory-service:8000/inventory/rollback").mock(
            return_value=httpx.Response(200),
        )

        inventory_client.rollback_stock("res-123")

    @respx.mock
    def test_raises_external_error_on_failure(self) -> None:
        respx.post("http://inventory-service:8000/inventory/rollback").mock(
            return_value=httpx.Response(500),
        )

        with pytest.raises(ExternalServiceError):
            inventory_client.rollback_stock("res-123")
