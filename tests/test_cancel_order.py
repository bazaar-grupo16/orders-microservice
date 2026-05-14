"""
Integration tests for order cancellation flows.
"""

import uuid

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.order_status import OrderItemLogisticStatus, OrderStatus
from src.repositories import order_repository


_VALID_ADDRESS = {
    "street": "Av. Corrientes 1234",
    "city": "Buenos Aires",
    "state": "CABA",
    "zip_code": "C1043",
    "country": "Argentina",
}


def _create_order(
    db_session: Session,
    *,
    buyer_id: uuid.UUID,
    seller_id: uuid.UUID,
    status: str = OrderStatus.CONFIRMADA,
):
    return order_repository.create_order(
        db_session,
        user_id=buyer_id,
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


class TestCancelOrderByBuyer:
    """Buyer can cancel an eligible confirmed order."""

    @respx.mock
    def test_cancels_order_and_rolls_back_stock(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = _create_order(db_session, buyer_id=buyer_id, seller_id=seller_id)

        rollback_route = respx.post(
            "http://inventory-service:8000/internal/products/stock/release"
        ).mock(return_value=httpx.Response(200))

        response = client.post(
            f"/orders/{order.id}/cancel",
            headers={"X-User-Id": str(buyer_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.CANCELADA
        assert data["items"][0]["status"] == OrderItemLogisticStatus.CANCELADO
        assert rollback_route.called
        assert any(event["new_status"] == OrderStatus.CANCELADA for event in data["status_history"])


class TestCancelOrderBySeller:
    """Seller can cancel a single confirmed item."""

    @respx.mock
    def test_cancels_single_item_and_closes_order(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = _create_order(db_session, buyer_id=buyer_id, seller_id=seller_id, status=OrderStatus.PENDIENTE_DE_PAGO)
        item_id = order.items[0].id

        confirm_response = client.patch(
            f"/orders/{order.id}/status",
            json={"status": OrderStatus.CONFIRMADA},
            headers={"X-User-Id": str(seller_id)},
        )
        assert confirm_response.status_code == 200

        rollback_route = respx.post(
            "http://inventory-service:8000/internal/products/stock/release"
        ).mock(return_value=httpx.Response(200))

        response = client.post(
            f"/orders/sales/{order.id}/items/{item_id}/cancel",
            headers={"X-User-Id": str(seller_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.CANCELADA
        assert data["items"][0]["status"] == OrderItemLogisticStatus.CANCELADO
        assert rollback_route.called
        assert any(event["new_status"] == OrderItemLogisticStatus.CANCELADO for event in data["status_history"])


class TestCancelOrderGuards:
    """Shipped orders cannot be cancelled."""

    def test_returns_409_when_order_is_already_sent(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = _create_order(db_session, buyer_id=buyer_id, seller_id=seller_id, status=OrderStatus.ENVIADA)

        response = client.post(
            f"/orders/{order.id}/cancel",
            headers={"X-User-Id": str(buyer_id)},
        )

        assert response.status_code == 409
