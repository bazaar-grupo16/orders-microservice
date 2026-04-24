"""
Integration tests for ``GET /orders`` and ``GET /orders/{id}``.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.order import Order
from src.models.order_status import OrderStatus
from src.repositories import order_repository


# ── Helpers ───────────────────────────────────────────────────

_VALID_ADDRESS = {
    "street": "Av. Corrientes 1234",
    "city": "Buenos Aires",
    "state": "CABA",
    "zip_code": "C1043",
    "country": "Argentina",
}


def _create_db_order(
    db_session: Session,
    user_id: uuid.UUID,
    status: str = OrderStatus.PENDIENTE_DE_PAGO,
) -> Order:
    return order_repository.create_order(
        db_session,
        user_id=user_id,
        idempotency_key=str(uuid.uuid4()),
        shipping_address=_VALID_ADDRESS,
        total_amount=1500.0,
        status=status,
        items=[
            {
                "product_id": uuid.uuid4(),
                "product_name": "Test Product",
                "quantity": 1,
                "unit_price": 1500.0,
            }
        ],
    )


# ── Tests ─────────────────────────────────────────────────────

class TestGetOrder:
    """Tests for ``GET /orders/{id}``."""

    def test_returns_order_for_owner(
        self, client: TestClient, db_session: Session
    ) -> None:
        user_id = uuid.uuid4()
        order = _create_db_order(db_session, user_id)

        response = client.get(
            f"/orders/{order.id}",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["order_id"] == str(order.id)
        assert data["user_id"] == str(user_id)
        assert len(data["items"]) == 1

    def test_returns_403_for_other_user(
        self, client: TestClient, db_session: Session
    ) -> None:
        owner_id = uuid.uuid4()
        order = _create_db_order(db_session, owner_id)

        other_user_id = uuid.uuid4()
        response = client.get(
            f"/orders/{order.id}",
            headers={"X-User-Id": str(other_user_id)},
        )

        assert response.status_code == 403
        assert response.json()["title"] == "Access Denied"

    def test_returns_404_for_nonexistent_order(
        self, client: TestClient
    ) -> None:
        user_id = uuid.uuid4()
        fake_id = uuid.uuid4()

        response = client.get(
            f"/orders/{fake_id}",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 404
        assert response.json()["title"] == "Order Not Found"


class TestListOrders:
    """Tests for ``GET /orders``."""

    def test_returns_user_orders(
        self, client: TestClient, db_session: Session
    ) -> None:
        user_id = uuid.uuid4()
        _create_db_order(db_session, user_id)
        _create_db_order(db_session, user_id)
        
        # Other user's order
        _create_db_order(db_session, uuid.uuid4())

        response = client.get(
            "/orders",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["orders"]) == 2
        assert all(o["user_id"] == str(user_id) for o in data["orders"])

    def test_filters_by_status(
        self, client: TestClient, db_session: Session
    ) -> None:
        user_id = uuid.uuid4()
        _create_db_order(db_session, user_id, OrderStatus.PENDIENTE_DE_PAGO)
        _create_db_order(db_session, user_id, OrderStatus.CONFIRMADA)
        _create_db_order(db_session, user_id, OrderStatus.CONFIRMADA)

        response = client.get(
            "/orders?order_status=CONFIRMADA",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["orders"]) == 2
        assert all(o["status"] == OrderStatus.CONFIRMADA for o in data["orders"])

    def test_pagination(
        self, client: TestClient, db_session: Session
    ) -> None:
        user_id = uuid.uuid4()
        for _ in range(5):
            _create_db_order(db_session, user_id)

        response = client.get(
            "/orders?page=2&size=2",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert data["page"] == 2
        assert data["size"] == 2
        assert len(data["orders"]) == 2

    def test_returns_empty_list_when_no_orders(
        self, client: TestClient
    ) -> None:
        user_id = uuid.uuid4()

        response = client.get(
            "/orders",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["orders"] == []
