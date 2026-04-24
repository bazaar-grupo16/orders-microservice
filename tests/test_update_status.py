"""
Integration tests for ``PATCH /orders/{id}/status``.
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
    status: str = OrderStatus.PENDIENTE_DE_PAGO,
) -> Order:
    return order_repository.create_order(
        db_session,
        user_id=uuid.uuid4(),
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

class TestUpdateOrderStatus:
    """Tests for ``PATCH /orders/{id}/status``."""

    def test_valid_transition_updates_status(
        self, client: TestClient, db_session: Session
    ) -> None:
        order = _create_db_order(db_session, OrderStatus.CONFIRMADA)

        response = client.patch(
            f"/orders/{order.id}/status",
            json={"status": OrderStatus.EN_PREPARACION},
            headers={"X-User-Id": "seller-id"},  # Assuming NGINX verified seller identity
        )

        assert response.status_code == 200
        data = response.json()
        assert data["order_id"] == str(order.id)
        assert data["status"] == OrderStatus.EN_PREPARACION

    def test_updates_tracking_code(
        self, client: TestClient, db_session: Session
    ) -> None:
        order = _create_db_order(db_session, OrderStatus.EN_PREPARACION)

        tracking = "TRACK-12345"
        response = client.patch(
            f"/orders/{order.id}/status",
            json={
                "status": OrderStatus.ENVIADA,
                "tracking_code": tracking,
            },
            headers={"X-User-Id": "seller-id"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.ENVIADA
        assert data["tracking_code"] == tracking

    def test_invalid_transition_returns_409(
        self, client: TestClient, db_session: Session
    ) -> None:
        order = _create_db_order(db_session, OrderStatus.PENDIENTE_DE_PAGO)

        response = client.patch(
            f"/orders/{order.id}/status",
            json={"status": OrderStatus.ENVIADA},
            headers={"X-User-Id": "seller-id"},
        )

        assert response.status_code == 409
        assert "Invalid State Transition" in response.json()["title"]

    def test_nonexistent_order_returns_404(
        self, client: TestClient
    ) -> None:
        fake_id = uuid.uuid4()

        response = client.patch(
            f"/orders/{fake_id}/status",
            json={"status": OrderStatus.EN_PREPARACION},
            headers={"X-User-Id": "seller-id"},
        )

        assert response.status_code == 404
        assert response.json()["title"] == "Order Not Found"
