"""Integration tests for idempotency key handling in ``POST /orders``."""

import uuid
import concurrent.futures
import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.order import Order
from src.models.idempotency_key import IdempotencyKey

# ── Helpers ───────────────────────────────────────────────────

_VALID_ADDRESS = {
    "street": "Av. Corrientes 1234",
    "city": "Buenos Aires",
    "state": "CABA",
    "zip_code": "C1043",
    "country": "Argentina",
}

_PREFERENCE_RESPONSE = {
    "preference_id": "mp-pref-abc123",
    "init_point": "https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id=mp-pref-abc123",
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
                }
            ],
            "totalPrice": {"value": "1500.00", "updatedAt": "2026-04-24T19:00:00Z"},
        },
    }


def _stock_lookup_response(cart_payload: dict) -> dict:
    """Build a batch stock lookup response from a cart payload."""
    return {
        "data": {
            item["productId"]: item["quantity"] + 10
            for item in cart_payload["data"]["items"]
        }
    }


def _post_order(
    client: TestClient,
    *,
    user_id: str,
    idempotency_key: str | None = None,
    body: dict | None = None,
) -> httpx.Response:
    """Helper to POST /orders with default headers."""
    if body is None:
        body = {"shipping_address": _VALID_ADDRESS}
    headers = {"X-User-Id": user_id}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return client.post(
        "/orders",
        json=body,
        headers=headers,
    )


def _mock_happy_path(user_id: str) -> None:
    """Register respx mocks for the full Saga happy path."""
    cart_payload = _cart_response(user_id)
    respx.get(f"http://cart-service:8000/cart/{user_id}").mock(
        return_value=httpx.Response(200, json=cart_payload),
    )
    respx.post("http://inventory-service:8000/internal/products/stocks").mock(
        return_value=httpx.Response(200, json=_stock_lookup_response(cart_payload)),
    )
    respx.post("http://inventory-service:8000/internal/products/stock/reserve").mock(
        return_value=httpx.Response(200, json={"data": []}),
    )
    respx.post("http://payments-service:8000/payments/preferences").mock(
        return_value=httpx.Response(201, json=_PREFERENCE_RESPONSE),
    )


# ── Tests ─────────────────────────────────────────────────────


@respx.mock
def test_duplicate_request_returns_201(client: TestClient, db_session: Session) -> None:
    """Sending POST /orders twice with the same Idempotency-Key returns 201 Created and the same order on the second request."""
    user_id = str(uuid.uuid4())
    key = str(uuid.uuid4())

    _mock_happy_path(user_id)

    # First request creates order
    first = _post_order(client, user_id=user_id, idempotency_key=key)
    assert first.status_code == 201
    order_id = first.json()["order_id"]

    # Second request with same key returns 201 with same order_id
    second = _post_order(client, user_id=user_id, idempotency_key=key)
    assert second.status_code == 201
    assert second.json()["order_id"] == order_id


def test_duplicate_request_no_order_returns_409(client: TestClient, db_session: Session) -> None:
    """If key exists in idempotency_keys but no order exists in orders table, return 409 Conflict."""
    user_id = str(uuid.uuid4())
    key = str(uuid.uuid4())
    order_id = uuid.uuid4()

    # Manually insert key in idempotency_keys
    db_session.add(IdempotencyKey(key=key, order_id=order_id))
    db_session.commit()

    # POST /orders with the same key
    response = _post_order(client, user_id=user_id, idempotency_key=key)
    assert response.status_code == 409
    assert response.headers["Content-Type"] == "application/problem+json"

    data = response.json()
    assert data["type"] == "about:blank"
    assert data["title"] == "Duplicate Request"
    assert data["status"] == 409
    assert data["detail"] == "An order with this idempotency key already exists."
    assert data["instance"] == "/orders"
    assert data["order_id"] == str(order_id)

@respx.mock
def test_duplicate_request_high_volume(client: TestClient, db_session: Session) -> None:
    """Sending multiple POST /orders requests with the same key results in exactly one order."""
    user_id = str(uuid.uuid4())
    key = str(uuid.uuid4())

    _mock_happy_path(user_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_post_order, client, user_id=user_id, idempotency_key=key) for _ in range(10)]
        responses = [f.result() for f in futures]

    assert all(r.status_code == 201 for r in responses)

    order_ids = [r.json()["order_id"] for r in responses]
    assert len(set(order_ids)) == 1


def test_missing_idempotency_key_returns_400(client: TestClient) -> None:
    """Sending POST /orders with missing Idempotency-Key header returns 400."""
    user_id = str(uuid.uuid4())
    response = _post_order(client, user_id=user_id, idempotency_key=None)
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing Idempotency-Key header"


def test_invalid_idempotency_key_returns_400(client: TestClient) -> None:
    """Sending POST /orders with invalid (non-UUID) Idempotency-Key header returns 400."""
    user_id = str(uuid.uuid4())
    response = _post_order(client, user_id=user_id, idempotency_key="not-a-uuid")
    assert response.status_code == 400
    assert "Invalid Idempotency-Key format" in response.json()["detail"]


@respx.mock
def test_different_keys_create_different_orders(client: TestClient) -> None:
    """Sending requests with different keys and the same body creates different orders."""
    user_id = str(uuid.uuid4())
    key1 = str(uuid.uuid4())
    key2 = str(uuid.uuid4())

    _mock_happy_path(user_id)

    first = _post_order(client, user_id=user_id, idempotency_key=key1)
    second = _post_order(client, user_id=user_id, idempotency_key=key2)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["order_id"] != second.json()["order_id"]


@respx.mock
def test_race_condition_only_one_order_created(client: TestClient, db_session: Session) -> None:
    """Two concurrent threads sending requests with the same key result in exactly one order."""
    user_id = str(uuid.uuid4())
    key = str(uuid.uuid4())

    _mock_happy_path(user_id)

    def send_request():
        return _post_order(client, user_id=user_id, idempotency_key=key)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(send_request) for _ in range(2)]
        responses = [f.result() for f in concurrent.futures.as_completed(futures)]

    status_codes = [r.status_code for r in responses]
    assert 201 in status_codes
    assert all(code in (201, 409) for code in status_codes)

    order_ids = [r.json()["order_id"] for r in responses]
    assert len(set(order_ids)) == 1

    # Exactly one row exists in idempotency_keys
    db_session.rollback()  # Refresh session
    keys = db_session.query(IdempotencyKey).filter(IdempotencyKey.key == key).all()
    assert len(keys) == 1

    # Exactly one row exists in orders
    orders = db_session.query(Order).filter(Order.idempotency_key == key).all()
    assert len(orders) == 1
    assert str(orders[0].id) == str(keys[0].order_id)

