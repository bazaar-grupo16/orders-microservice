"""
Integration tests for ``POST /orders/{id}/payment-webhook``.

Each test first creates an order via the Saga (mocking Cart + Inventory),
then calls the webhook with SUCCESS or FAILED to verify the resolution.
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


def _cart_response(user_id: str) -> dict:
    """Build a realistic Cart service response."""
    return {
        "data": {
            "userId": user_id,
            "items": [
                {
                    "productId": str(uuid.uuid4()),
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


def _create_order_via_api(client: TestClient, user_id: str) -> str:
    """Create an order and return its ``order_id``."""
    response = client.post(
        "/orders",
        json={"shipping_address": _VALID_ADDRESS},
        headers={
            "X-User-Id": user_id,
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201
    return response.json()["order_id"]


# ── Tests ─────────────────────────────────────────────────────


class TestPaymentSuccess:
    """Webhook with ``SUCCESS`` → CONFIRMADA + commit + clear cart."""

    @respx.mock
    def test_updates_status_to_confirmada(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())

        # Mock for order creation.
        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=_cart_response(user_id)),
        )
        respx.post("http://inventory-service:8000/inventory/reserve").mock(
            return_value=httpx.Response(200, json={"reservation_id": "res-1"}),
        )

        order_id = _create_order_via_api(client, user_id)

        # Mock for webhook side effects.
        respx.post("http://inventory-service:8000/inventory/commit").mock(
            return_value=httpx.Response(200),
        )
        respx.delete(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(204),
        )

        response = client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "SUCCESS", "transaction_id": "tx-123"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "CONFIRMADA"

    @respx.mock
    def test_calls_inventory_commit(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())

        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=_cart_response(user_id)),
        )
        respx.post("http://inventory-service:8000/inventory/reserve").mock(
            return_value=httpx.Response(200, json={"reservation_id": "res-2"}),
        )

        order_id = _create_order_via_api(client, user_id)

        commit_route = respx.post("http://inventory-service:8000/inventory/commit").mock(
            return_value=httpx.Response(200),
        )
        respx.delete(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(204),
        )

        client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "SUCCESS", "transaction_id": "tx-456"},
        )

        assert commit_route.called

    @respx.mock
    def test_calls_cart_delete(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())

        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=_cart_response(user_id)),
        )
        respx.post("http://inventory-service:8000/inventory/reserve").mock(
            return_value=httpx.Response(200, json={"reservation_id": "res-3"}),
        )

        order_id = _create_order_via_api(client, user_id)

        respx.post("http://inventory-service:8000/inventory/commit").mock(
            return_value=httpx.Response(200),
        )
        delete_route = respx.delete(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(204),
        )

        client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "SUCCESS", "transaction_id": "tx-789"},
        )

        assert delete_route.called


class TestPaymentFailed:
    """Webhook with ``FAILED`` → PAGO_RECHAZADO + rollback stock."""

    @respx.mock
    def test_updates_status_to_pago_rechazado(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())

        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=_cart_response(user_id)),
        )
        respx.post("http://inventory-service:8000/inventory/reserve").mock(
            return_value=httpx.Response(200, json={"reservation_id": "res-4"}),
        )

        order_id = _create_order_via_api(client, user_id)

        respx.post("http://inventory-service:8000/inventory/rollback").mock(
            return_value=httpx.Response(200),
        )

        response = client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "FAILED", "transaction_id": "tx-fail-1"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "PAGO_RECHAZADO"

    @respx.mock
    def test_calls_inventory_rollback(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())

        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=_cart_response(user_id)),
        )
        respx.post("http://inventory-service:8000/inventory/reserve").mock(
            return_value=httpx.Response(200, json={"reservation_id": "res-5"}),
        )

        order_id = _create_order_via_api(client, user_id)

        rollback_route = respx.post("http://inventory-service:8000/inventory/rollback").mock(
            return_value=httpx.Response(200),
        )

        client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "FAILED", "transaction_id": "tx-fail-2"},
        )

        assert rollback_route.called


class TestWebhookErrors:
    """Error handling in the payment webhook."""

    def test_nonexistent_order_returns_404(self, client: TestClient) -> None:
        fake_id = str(uuid.uuid4())
        response = client.post(
            f"/orders/{fake_id}/payment-webhook",
            json={"status": "SUCCESS", "transaction_id": "tx-ghost"},
        )
        assert response.status_code == 404

    @respx.mock
    def test_duplicate_webhook_returns_409(self, client: TestClient) -> None:
        """Calling the webhook twice on the same order should fail the
        second time because the order is no longer PENDIENTE_DE_PAGO."""
        user_id = str(uuid.uuid4())

        respx.get(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(200, json=_cart_response(user_id)),
        )
        respx.post("http://inventory-service:8000/inventory/reserve").mock(
            return_value=httpx.Response(200, json={"reservation_id": "res-6"}),
        )

        order_id = _create_order_via_api(client, user_id)

        respx.post("http://inventory-service:8000/inventory/commit").mock(
            return_value=httpx.Response(200),
        )
        respx.delete(f"http://cart-service:8000/carts/{user_id}").mock(
            return_value=httpx.Response(204),
        )

        # First call — should succeed.
        first = client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "SUCCESS", "transaction_id": "tx-dup-1"},
        )
        assert first.status_code == 200

        # Second call — state machine rejects.
        second = client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "SUCCESS", "transaction_id": "tx-dup-2"},
        )
        assert second.status_code == 409

    def test_invalid_status_returns_422(self, client: TestClient) -> None:
        fake_id = str(uuid.uuid4())
        response = client.post(
            f"/orders/{fake_id}/payment-webhook",
            json={"status": "INVALID", "transaction_id": "tx-bad"},
        )
        assert response.status_code == 422
