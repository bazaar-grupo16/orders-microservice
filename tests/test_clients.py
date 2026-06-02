"""
Tests for the Cart, Inventory, and Payments HTTP clients.

Uses ``respx`` to mock outgoing HTTP requests — no real services needed.
The database is NOT involved in these tests.
"""

import uuid

import httpx
import pytest
import respx

from src.clients import cart_client, inventory_client, payments_client
from src.exceptions import CartEmptyError, ExternalServiceError, InsufficientStockError, PaymentsServiceError


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
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(200, json=cart_response),
        )

        result = cart_client.get_cart(user_id)

        assert len(result) == 1
        # Verify normalisation from camelCase to snake_case.
        assert result[0]["product_id"] == product_id
        assert result[0]["product_name"] == "Laptop Gamer"
        assert result[0]["category"] == "Uncategorized"
        assert result[0]["quantity"] == 1
        assert result[0]["unit_price"] == 1500.00

    @respx.mock
    def test_normalises_category_when_present(self) -> None:
        user_id = str(uuid.uuid4())
        product_id = str(uuid.uuid4())
        cart_response = {
            "data": {
                "userId": user_id,
                "items": [
                    {
                        "productId": product_id,
                        "title": "Laptop Gamer",
                        "category": "Electrónica",
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
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(200, json=cart_response),
        )

        result = cart_client.get_cart(user_id)

        assert result[0]["category"] == "Electrónica"

    @respx.mock
    def test_raises_cart_empty_on_404(self) -> None:
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
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
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(200, json=cart_response),
        )

        with pytest.raises(CartEmptyError):
            cart_client.get_cart(user_id)

    @respx.mock
    def test_raises_external_error_on_500(self) -> None:
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(500, json={"detail": "Internal error"}),
        )

        with pytest.raises(ExternalServiceError) as exc_info:
            cart_client.get_cart(user_id)
        assert exc_info.value.service == "cart-service"

    @respx.mock
    def test_raises_external_error_on_timeout(self) -> None:
        user_id = str(uuid.uuid4())
        respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
            side_effect=httpx.ConnectTimeout("Timeout"),
        )

        with pytest.raises(ExternalServiceError):
            cart_client.get_cart(user_id)


class TestDeleteCart:
    """Tests for ``cart_client.delete_cart``."""

    @respx.mock
    def test_succeeds_on_204(self) -> None:
        user_id = str(uuid.uuid4())
        respx.delete(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(204),
        )

        # Should not raise.
        cart_client.delete_cart(user_id)

    @respx.mock
    def test_succeeds_on_200(self) -> None:
        user_id = str(uuid.uuid4())
        respx.delete(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(200),
        )

        cart_client.delete_cart(user_id)

    @respx.mock
    def test_raises_external_error_on_failure(self) -> None:
        user_id = str(uuid.uuid4())
        respx.delete(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(500),
        )

        with pytest.raises(ExternalServiceError):
            cart_client.delete_cart(user_id)


# ── Inventory Client ──────────────────────────────────────────


class TestReserveStock:
    """Tests for ``inventory_client.reserve_stock``."""

    @respx.mock
    def test_succeeds_on_200(self) -> None:
        p1 = str(uuid.uuid4())
        p2 = str(uuid.uuid4())
        items = [
            {"product_id": p1, "quantity": 2},
            {"product_id": p2, "quantity": 1},
        ]
        
        respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
            return_value=httpx.Response(200, json={"data": []}),
        )

        inventory_client.reserve_stock(items)

    @respx.mock
    def test_raises_insufficient_stock_on_409(self) -> None:
        p1 = str(uuid.uuid4())
        items = [{"product_id": p1, "quantity": 999}]
        respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
            return_value=httpx.Response(409, json={"detail": "Not enough stock for product X"}),
        )

        with pytest.raises(InsufficientStockError) as exc_info:
            inventory_client.reserve_stock(items)
        assert "Not enough stock" in exc_info.value.detail

    @respx.mock
    def test_raises_external_error_on_500(self) -> None:
        p1 = str(uuid.uuid4())
        items = [{"product_id": p1, "quantity": 1}]
        respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
            return_value=httpx.Response(500),
        )

        with pytest.raises(ExternalServiceError):
            inventory_client.reserve_stock(items)

    @respx.mock
    def test_raises_external_error_on_timeout(self) -> None:
        p1 = str(uuid.uuid4())
        items = [{"product_id": p1, "quantity": 1}]
        respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
            side_effect=httpx.ConnectTimeout("Timeout"),
        )

        with pytest.raises(ExternalServiceError):
            inventory_client.reserve_stock(items)

    @respx.mock
    def test_does_not_release_on_batch_insufficient_stock(self) -> None:
        p1 = str(uuid.uuid4())
        p2 = str(uuid.uuid4())
        items = [
            {"product_id": p1, "quantity": 2},
            {"product_id": p2, "quantity": 1},
        ]
        
        release_mock = respx.post("http://inventory-service:8000/internal/products/stock/release").mock(
            return_value=httpx.Response(200),
        )
        respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
            return_value=httpx.Response(409, json={"detail": "Insufficient stock"}),
        )

        with pytest.raises(InsufficientStockError):
            inventory_client.reserve_stock(items)
            
        assert not release_mock.called


class TestCommitStock:
    """Tests for ``inventory_client.commit_stock``."""

    def test_does_nothing(self) -> None:
        # Should not raise any error
        inventory_client.commit_stock("res-123")


class TestRollbackStock:
    """Tests for ``inventory_client.rollback_stock``."""

    @respx.mock
    def test_succeeds_on_200(self) -> None:
        p1 = str(uuid.uuid4())
        respx.post("http://inventory-service:8000/internal/products/stock/release").mock(
            return_value=httpx.Response(200),
        )

        inventory_client.rollback_stock([{"product_id": p1, "quantity": 1}])

    @respx.mock
    def test_logs_error_on_failure_but_continues(self) -> None:
        p1 = str(uuid.uuid4())
        respx.post("http://inventory-service:8000/internal/products/stock/release").mock(
            return_value=httpx.Response(500),
        )

        # Should not raise exception
        inventory_client.rollback_stock([{"product_id": p1, "quantity": 1}])


# ── Payments Client ────────────────────────────────────────────────


class TestPaymentsClient:
    """Tests for ``payments_client.create_preference``."""

    _ITEMS = [
        {"title": "Laptop Gamer", "quantity": 1, "unit_price": 1500.00},
        {"title": "Mouse Inalámbrico", "quantity": 2, "unit_price": 25.50},
    ]
    _PREFERENCE_RESPONSE = {
        "preference_id": "mp-pref-abc123",
        "init_point": "https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id=mp-pref-abc123",
    }

    @respx.mock
    def test_returns_preference_on_201(self) -> None:
        """Happy path: Payments returns 201 with preference_id and init_point."""
        order_id = str(uuid.uuid4())
        idem_key = str(uuid.uuid4())

        route = respx.post("http://payments-service:8000/payments/preferences").mock(
            return_value=httpx.Response(201, json=self._PREFERENCE_RESPONSE),
        )

        result = payments_client.create_preference(
            order_id=order_id,
            items=self._ITEMS,
            idempotency_key=idem_key,
        )

        assert route.called
        assert result["preference_id"] == "mp-pref-abc123"
        assert result["init_point"].startswith("https://")

    @respx.mock
    def test_sends_correct_idempotency_header(self) -> None:
        """The Idempotency-Key header must be forwarded verbatim."""
        order_id = str(uuid.uuid4())
        idem_key = str(uuid.uuid4())

        route = respx.post("http://payments-service:8000/payments/preferences").mock(
            return_value=httpx.Response(201, json=self._PREFERENCE_RESPONSE),
        )

        payments_client.create_preference(
            order_id=order_id,
            items=self._ITEMS,
            idempotency_key=idem_key,
        )

        assert route.calls.last.request.headers["Idempotency-Key"] == idem_key

    @respx.mock
    def test_raises_payments_error_on_400(self) -> None:
        """A 400 from Payments must raise PaymentsServiceError."""
        order_id = str(uuid.uuid4())
        respx.post("http://payments-service:8000/payments/preferences").mock(
            return_value=httpx.Response(400, json={"detail": "Bad request"}),
        )

        with pytest.raises(PaymentsServiceError) as exc_info:
            payments_client.create_preference(
                order_id=order_id,
                items=self._ITEMS,
                idempotency_key=str(uuid.uuid4()),
            )
        assert "Bad request" in exc_info.value.detail

    @respx.mock
    def test_raises_payments_error_on_422(self) -> None:
        """A 422 (validation error from Payments) must raise PaymentsServiceError."""
        order_id = str(uuid.uuid4())
        respx.post("http://payments-service:8000/payments/preferences").mock(
            return_value=httpx.Response(422, json={"detail": "Unprocessable entity"}),
        )

        with pytest.raises(PaymentsServiceError):
            payments_client.create_preference(
                order_id=order_id,
                items=self._ITEMS,
                idempotency_key=str(uuid.uuid4()),
            )

    @respx.mock
    def test_raises_payments_error_on_502(self) -> None:
        """A 502 (MP gateway error) must raise PaymentsServiceError."""
        order_id = str(uuid.uuid4())
        respx.post("http://payments-service:8000/payments/preferences").mock(
            return_value=httpx.Response(502, json={"detail": "Bad gateway"}),
        )

        with pytest.raises(PaymentsServiceError) as exc_info:
            payments_client.create_preference(
                order_id=order_id,
                items=self._ITEMS,
                idempotency_key=str(uuid.uuid4()),
            )
        assert "Bad gateway" in exc_info.value.detail

    @respx.mock
    def test_raises_payments_error_on_timeout(self) -> None:
        """A network timeout must raise PaymentsServiceError (not propagate httpx exception)."""
        order_id = str(uuid.uuid4())
        respx.post("http://payments-service:8000/payments/preferences").mock(
            side_effect=httpx.ConnectTimeout("Timeout"),
        )

        with pytest.raises(PaymentsServiceError) as exc_info:
            payments_client.create_preference(
                order_id=order_id,
                items=self._ITEMS,
                idempotency_key=str(uuid.uuid4()),
            )
        assert "unreachable" in exc_info.value.detail.lower()
