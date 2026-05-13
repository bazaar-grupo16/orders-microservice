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
    seller_id: uuid.UUID | None = None,
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
                "seller_id": seller_id,
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

    def test_filters_by_seller_id(
        self, client: TestClient, db_session: Session
    ) -> None:
        user_id = uuid.uuid4()
        seller_a = uuid.uuid4()
        seller_b = uuid.uuid4()

        _create_db_order(db_session, user_id, seller_id=seller_a)
        _create_db_order(db_session, user_id, seller_id=seller_a)
        _create_db_order(db_session, user_id, seller_id=seller_b)

        response = client.get(
            f"/orders?seller_id={seller_a}",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["orders"]) == 2
        assert all(
            any(item["seller_id"] == str(seller_a) for item in order["items"])
            for order in data["orders"]
        )

    def test_sales_endpoint_returns_only_seller_items(
        self, client: TestClient, db_session: Session
    ) -> None:
        buyer = uuid.uuid4()
        seller = uuid.uuid4()
        other_seller = uuid.uuid4()

        # Create an order that has items from seller and other_seller
        order_repository.create_order(
            db_session,
            user_id=buyer,
            idempotency_key=str(uuid.uuid4()),
            shipping_address=_VALID_ADDRESS,
            total_amount=3000.0,
            status=OrderStatus.CONFIRMADA,
            items=[
                {"product_id": uuid.uuid4(), "seller_id": seller, "product_name": "A", "quantity": 1, "unit_price": 1000.0},
                {"product_id": uuid.uuid4(), "seller_id": other_seller, "product_name": "B", "quantity": 1, "unit_price": 2000.0},
            ],
        )

        response = client.get(
            "/orders/sales",
            headers={"X-User-Id": str(seller)},
        )

        assert response.status_code == 200
        data = response.json()
        # Seller should see the order but only their item
        assert data["total"] == 1
        assert len(data["orders"]) == 1
        items = data["orders"][0]["items"]
        assert len(items) == 1
        assert items[0]["seller_id"] == str(seller)

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


class TestGetSellerOrder:
    """Tests for ``GET /orders/sales/{order_id}``."""

    def test_seller_can_get_order_with_their_items(
        self, client: TestClient, db_session: Session
    ) -> None:
        buyer = uuid.uuid4()
        seller = uuid.uuid4()

        order = _create_db_order(db_session, buyer, seller_id=seller)

        response = client.get(
            f"/orders/sales/{order.id}",
            headers={"X-User-Id": str(seller)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["order_id"] == str(order.id)
        assert len(data["items"]) == 1
        assert data["items"][0]["seller_id"] == str(seller)
        assert "status" in data["items"][0]  # Verify logistic status is present
        assert "tracking_code" in data["items"][0]

    def test_seller_cannot_get_order_without_their_items(
        self, client: TestClient, db_session: Session
    ) -> None:
        buyer = uuid.uuid4()
        seller_a = uuid.uuid4()
        seller_b = uuid.uuid4()

        order = _create_db_order(db_session, buyer, seller_id=seller_a)

        response = client.get(
            f"/orders/sales/{order.id}",
            headers={"X-User-Id": str(seller_b)},
        )

        assert response.status_code == 403
        assert "Access denied" in response.json()["detail"]

    def test_seller_sees_only_their_items(
        self, client: TestClient, db_session: Session
    ) -> None:
        buyer = uuid.uuid4()
        seller_a = uuid.uuid4()
        seller_b = uuid.uuid4()

        # Create order with items from both sellers
        order = order_repository.create_order(
            db_session,
            user_id=buyer,
            idempotency_key=str(uuid.uuid4()),
            shipping_address=_VALID_ADDRESS,
            total_amount=3000.0,
            status=OrderStatus.CONFIRMADA,
            items=[
                {"product_id": uuid.uuid4(), "seller_id": seller_a, "product_name": "A", "quantity": 1, "unit_price": 1000.0},
                {"product_id": uuid.uuid4(), "seller_id": seller_b, "product_name": "B", "quantity": 1, "unit_price": 2000.0},
            ],
        )

        response = client.get(
            f"/orders/sales/{order.id}",
            headers={"X-User-Id": str(seller_a)},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["seller_id"] == str(seller_a)
