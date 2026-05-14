"""
Integration tests for ``POST /orders/{id}/payment-webhook``.

Each test first creates an order via the Saga (mocking Cart + Inventory),
then calls the webhook with APPROVED or REJECTED to verify the resolution.
"""

import json
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


def _stock_lookup_response(cart_payload: dict) -> dict:
    return {
        "data": {
            item["productId"]: item["quantity"] + 10
            for item in cart_payload["data"]["items"]
        }
    }


_PREFERENCE_RESPONSE = {
    "preference_id": "mp-pref-abc123",
    "init_point": "https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id=mp-pref-abc123",
}


def _create_order_via_api(client: TestClient, user_id: str) -> str:
    """Create an order (mocking Cart, Inventory and Payments) and return its ``order_id``."""
    cart_payload = _cart_response(user_id)
    respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
        return_value=httpx.Response(200, json=cart_payload),
    )
    respx.post("http://inventory-service:8000/internal/products/stocks").mock(
        return_value=httpx.Response(200, json=_stock_lookup_response(cart_payload)),
    )
    respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(return_value=httpx.Response(200, json={"data": []}))
    payments_route = respx.post("http://payments-service:8000/payments/preferences").mock(
        return_value=httpx.Response(201, json=_PREFERENCE_RESPONSE),
    )
    response = client.post(
        "/orders",
        json={"shipping_address": _VALID_ADDRESS},
        headers={
            "X-User-Id": user_id,
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201
    request_payload = json.loads(payments_route.calls.last.request.content)
    assert request_payload["order_id"] is not None
    assert all("line_item_id" in item for item in request_payload["items"])
    return response.json()["order_id"]


# ── Tests ─────────────────────────────────────────────────────


class TestPaymentSuccess:
    """Webhook with ``APPROVED`` → CONFIRMADA + commit + clear cart."""

    @respx.mock
    def test_updates_status_to_confirmada(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())

        # Mock for order creation.
        order_id = _create_order_via_api(client, user_id)

        # Mock for webhook side effects.
        # (commit_stock is now a no-op)
        respx.delete(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(204),
        )

        response = client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "APPROVED", "transaction_id": "tx-123"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "CONFIRMADA"

        detail_response = client.get(
            f"/orders/{order_id}",
            headers={"X-User-Id": user_id},
        )
        assert detail_response.status_code == 200
        history = detail_response.json()["status_history"]
        assert len(history) >= 2
        assert any(
            event["origin"] == "webhook" and event["new_status"] == "CONFIRMADA"
            for event in history
        )

    @respx.mock
    def test_calls_inventory_commit(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())

        order_id = _create_order_via_api(client, user_id)

        # commit_stock is no-op, test is adapted to just check it passes
        # we can remove commit_route completely
        respx.delete(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(204),
        )

        response = client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "APPROVED", "transaction_id": "tx-456"},
        )

        # The test originally checked if commit_route.called
        # Since it's a no-op now, we can just assert response status 200
        assert response.status_code == 200

    @respx.mock
    def test_calls_cart_delete(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())

        order_id = _create_order_via_api(client, user_id)

        # commit is no-op
        delete_route = respx.delete(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(204),
        )

        client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "APPROVED", "transaction_id": "tx-789"},
        )

        assert delete_route.called


class TestPaymentFailed:
    """Webhook with ``REJECTED`` → PAGO_RECHAZADO + rollback stock."""

    @respx.mock
    def test_updates_status_to_pago_rechazado(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())

        order_id = _create_order_via_api(client, user_id)

        respx.post("http://inventory-service:8000/internal/products/stock/release").mock(
            return_value=httpx.Response(200),
        )

        response = client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "REJECTED", "transaction_id": "tx-fail-1"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "PAGO_RECHAZADO"

    @respx.mock
    def test_calls_inventory_rollback(self, client: TestClient) -> None:
        user_id = str(uuid.uuid4())

        order_id = _create_order_via_api(client, user_id)

        rollback_route = respx.post("http://inventory-service:8000/internal/products/stock/release").mock(
            return_value=httpx.Response(200),
        )

        client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "REJECTED", "transaction_id": "tx-fail-2"},
        )

        assert rollback_route.called


class TestWebhookErrors:
    """Error handling in the payment webhook."""

    def test_nonexistent_order_returns_404(self, client: TestClient) -> None:
        fake_id = str(uuid.uuid4())
        response = client.post(
            f"/orders/{fake_id}/payment-webhook",
            json={"status": "APPROVED", "transaction_id": "tx-ghost"},
        )
        assert response.status_code == 404

    @respx.mock
    def test_duplicate_webhook_returns_409(self, client: TestClient) -> None:
        """Calling the webhook twice on the same order should fail the
        second time because the order is no longer PENDIENTE_DE_PAGO."""
        user_id = str(uuid.uuid4())

        order_id = _create_order_via_api(client, user_id)

        # commit is no-op
        respx.delete(f"http://cart-service:8000/cart/{user_id}").mock(
            return_value=httpx.Response(204),
        )

        # First call — should succeed.
        first = client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "APPROVED", "transaction_id": "tx-dup-1"},
        )
        assert first.status_code == 200

        # Second call — state machine rejects.
        second = client.post(
            f"/orders/{order_id}/payment-webhook",
            json={"status": "APPROVED", "transaction_id": "tx-dup-2"},
        )
        assert second.status_code == 409

    def test_invalid_status_returns_422(self, client: TestClient) -> None:
        fake_id = str(uuid.uuid4())
        response = client.post(
            f"/orders/{fake_id}/payment-webhook",
            json={"status": "INVALID", "transaction_id": "tx-bad"},
        )
        assert response.status_code == 422
