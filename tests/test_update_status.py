"""
Integration tests for ``PATCH /orders/{id}/status`` and item status updates.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.order import Order
from src.models.order_status import OrderItemLogisticStatus, OrderStatus
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


class TestUpdateItemStatus:
    """Tests for ``PATCH /orders/sales/{order_id}/items/{item_id}/status``."""

    def _confirm_order(self, client: TestClient, order_id: uuid.UUID) -> None:
        response = client.patch(
            f"/orders/{order_id}/status",
            json={"status": OrderStatus.CONFIRMADA},
        )
        assert response.status_code == 200

    def test_seller_can_update_item_status(
        self, client: TestClient, db_session: Session
    ) -> None:
        buyer = uuid.uuid4()
        seller = uuid.uuid4()

        order = order_repository.create_order(
            db_session,
            user_id=buyer,
            idempotency_key=str(uuid.uuid4()),
            shipping_address=_VALID_ADDRESS,
            total_amount=1500.0,
            status=OrderStatus.PENDIENTE_DE_PAGO,
            items=[
                {
                    "product_id": uuid.uuid4(),
                    "seller_id": seller,
                    "product_name": "Test Product",
                    "quantity": 1,
                    "unit_price": 1500.0,
                }
            ],
        )
        item_id = order.items[0].id

        self._confirm_order(client, order.id)

        response = client.patch(
            f"/orders/sales/{order.id}/items/{item_id}/status",
            json={"status": OrderStatus.EN_PREPARACION},
            headers={"X-User-Id": str(seller)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["items"][0]["status"] == OrderItemLogisticStatus.EN_PREPARACION
        assert data["aggregated_status"] == OrderStatus.EN_PREPARACION

    def test_seller_can_set_tracking_code_on_item(
        self, client: TestClient, db_session: Session
    ) -> None:
        buyer = uuid.uuid4()
        seller = uuid.uuid4()

        order = order_repository.create_order(
            db_session,
            user_id=buyer,
            idempotency_key=str(uuid.uuid4()),
            shipping_address=_VALID_ADDRESS,
            total_amount=1500.0,
            status=OrderStatus.PENDIENTE_DE_PAGO,
            items=[
                {
                    "product_id": uuid.uuid4(),
                    "seller_id": seller,
                    "product_name": "Test Product",
                    "quantity": 1,
                    "unit_price": 1500.0,
                }
            ],
        )
        item_id = order.items[0].id

        self._confirm_order(client, order.id)

        tracking = "TRACK-98765"
        response = client.patch(
            f"/orders/sales/{order.id}/items/{item_id}/status",
            json={
                "status": OrderStatus.EN_PREPARACION,
                "tracking_code": tracking,
            },
            headers={"X-User-Id": str(seller)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["items"][0]["status"] == OrderItemLogisticStatus.EN_PREPARACION
        assert data["items"][0]["tracking_code"] == tracking
        assert data["aggregated_status"] == OrderStatus.EN_PREPARACION

    def test_seller_cannot_update_other_seller_item(
        self, client: TestClient, db_session: Session
    ) -> None:
        buyer = uuid.uuid4()
        seller_a = uuid.uuid4()
        seller_b = uuid.uuid4()

        order = order_repository.create_order(
            db_session,
            user_id=buyer,
            idempotency_key=str(uuid.uuid4()),
            shipping_address=_VALID_ADDRESS,
            total_amount=1500.0,
            status=OrderStatus.PENDIENTE_DE_PAGO,
            items=[
                {
                    "product_id": uuid.uuid4(),
                    "seller_id": seller_a,
                    "product_name": "Test Product",
                    "quantity": 1,
                    "unit_price": 1500.0,
                }
            ],
        )
        item_id = order.items[0].id

        self._confirm_order(client, order.id)

        response = client.patch(
            f"/orders/sales/{order.id}/items/{item_id}/status",
            json={"status": OrderStatus.EN_PREPARACION},
            headers={"X-User-Id": str(seller_b)},
        )

        assert response.status_code == 403
        assert "Access denied to this order" == response.json()["detail"]

    def test_invalid_item_transition_returns_409(
        self, client: TestClient, db_session: Session
    ) -> None:
        buyer = uuid.uuid4()
        seller = uuid.uuid4()

        order = order_repository.create_order(
            db_session,
            user_id=buyer,
            idempotency_key=str(uuid.uuid4()),
            shipping_address=_VALID_ADDRESS,
            total_amount=1500.0,
            status=OrderStatus.PENDIENTE_DE_PAGO,
            items=[
                {
                    "product_id": uuid.uuid4(),
                    "seller_id": seller,
                    "product_name": "Test Product",
                    "quantity": 1,
                    "unit_price": 1500.0,
                }
            ],
        )
        item_id = order.items[0].id

        self._confirm_order(client, order.id)

        # EN_PREPARACION -> ENTREGADA is not a valid transition
        response = client.patch(
            f"/orders/sales/{order.id}/items/{item_id}/status",
            json={"status": OrderStatus.PENDIENTE_DE_PAGO},
            headers={"X-User-Id": str(seller)},
        )

        assert response.status_code == 409
        assert response.json()["title"] == "Invalid State Transition"

    def test_nonexistent_item_returns_403(
        self, client: TestClient, db_session: Session
    ) -> None:
        buyer = uuid.uuid4()
        seller = uuid.uuid4()
        fake_item_id = uuid.uuid4()

        order = order_repository.create_order(
            db_session,
            user_id=buyer,
            idempotency_key=str(uuid.uuid4()),
            shipping_address=_VALID_ADDRESS,
            total_amount=1500.0,
            status=OrderStatus.PENDIENTE_DE_PAGO,
            items=[
                {
                    "product_id": uuid.uuid4(),
                    "seller_id": seller,
                    "product_name": "Test Product",
                    "quantity": 1,
                    "unit_price": 1500.0,
                }
            ],
        )

        self._confirm_order(client, order.id)

        response = client.patch(
            f"/orders/sales/{order.id}/items/{fake_item_id}/status",
            json={"status": OrderStatus.EN_PREPARACION},
            headers={"X-User-Id": str(seller)},
        )

        assert response.status_code == 403
        assert "not found or not owned" in response.json()["detail"]


class TestConfirmDelivery:
    """Tests for ``PATCH /orders/{order_id}/confirm-delivery``."""

    def test_buyer_can_confirm_delivered_items(
        self, client: TestClient, db_session: Session
    ) -> None:
        buyer = uuid.uuid4()
        seller = uuid.uuid4()

        order = order_repository.create_order(
            db_session,
            user_id=buyer,
            idempotency_key=str(uuid.uuid4()),
            shipping_address=_VALID_ADDRESS,
            total_amount=1500.0,
            status=OrderStatus.CONFIRMADA,
            items=[
                {
                    "product_id": uuid.uuid4(),
                    "seller_id": seller,
                    "product_name": "Test Product",
                    "quantity": 1,
                    "unit_price": 1500.0,
                }
            ],
        )
        order_repository.update_item_status(
            db_session,
            order.items[0],
            OrderItemLogisticStatus.ENVIADO.value,
        )

        item_id = order.items[0].id
        response = client.post(
            f"/orders/{order.id}/items/{item_id}/confirm-delivery",
            headers={"X-User-Id": str(buyer)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["order_id"] == str(order.id)
        assert data["aggregated_status"] == OrderStatus.ENTREGADA
        assert data["items"][0]["status"] == OrderItemLogisticStatus.ENTREGADO
        assert data["items"][0]["status_history"][0]["origin"] == "buyer"
        assert data["items"][0]["status_history"][0]["new_status"] == OrderItemLogisticStatus.ENTREGADO

    def test_buyer_cannot_confirm_when_item_is_not_sent_yet(
        self, client: TestClient, db_session: Session
    ) -> None:
        buyer = uuid.uuid4()
        seller = uuid.uuid4()

        order = order_repository.create_order(
            db_session,
            user_id=buyer,
            idempotency_key=str(uuid.uuid4()),
            shipping_address=_VALID_ADDRESS,
            total_amount=1500.0,
            status=OrderStatus.CONFIRMADA,
            items=[
                {
                    "product_id": uuid.uuid4(),
                    "seller_id": seller,
                    "product_name": "Test Product",
                    "quantity": 1,
                    "unit_price": 1500.0,
                }
            ],
        )

        fake_item = order.items[0].id
        response = client.post(
            f"/orders/{order.id}/items/{fake_item}/confirm-delivery",
            headers={"X-User-Id": str(buyer)},
        )

        assert response.status_code == 409
        assert response.json()["title"] == "Invalid State Transition"

    def test_buyer_cannot_confirm_other_users_order(
        self, client: TestClient, db_session: Session
    ) -> None:
        buyer = uuid.uuid4()
        other_user = uuid.uuid4()
        seller = uuid.uuid4()

        order = order_repository.create_order(
            db_session,
            user_id=buyer,
            idempotency_key=str(uuid.uuid4()),
            shipping_address=_VALID_ADDRESS,
            total_amount=1500.0,
            status=OrderStatus.CONFIRMADA,
            items=[
                {
                    "product_id": uuid.uuid4(),
                    "seller_id": seller,
                    "product_name": "Test Product",
                    "quantity": 1,
                    "unit_price": 1500.0,
                }
            ],
        )
        order_repository.update_item_status(
            db_session,
            order.items[0],
            OrderItemLogisticStatus.ENVIADO.value,
        )

        item_id = order.items[0].id
        response = client.post(
            f"/orders/{order.id}/items/{item_id}/confirm-delivery",
            headers={"X-User-Id": str(other_user)},
        )

        assert response.status_code == 403
        assert response.json()["title"] == "Access Denied"
