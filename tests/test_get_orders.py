"""
Integration tests for ``GET /orders`` and ``GET /orders/{id}``.
"""

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.order import Order
from src.models.order_status import OrderStatus
from src.models.order_status_history import OrderStatusHistory
from src.repositories import order_repository


# ── Helpers ───────────────────────────────────────────────────

_VALID_ADDRESS = {
    "street": "Av. Corrientes 1234",
    "city": "Buenos Aires",
    "state": "CABA",
    "zip_code": "C1043",
    "country": "Argentina",
}


async def _create_db_order(
    db_session: AsyncSession,
    user_id: uuid.UUID,
    status: str = OrderStatus.PENDIENTE_DE_PAGO,
    seller_id: uuid.UUID | None = None,
) -> Order:
    return await order_repository.create_order(
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

    async def test_returns_order_for_owner(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        user_id = uuid.uuid4()
        order = await _create_db_order(db_session, user_id)

        response = await client.get(
            f"/orders/{order.id}",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["order_id"] == str(order.id)
        assert data["user_id"] == str(user_id)
        assert len(data["items"]) == 1

    async def test_returns_403_for_other_user(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        owner_id = uuid.uuid4()
        order = await _create_db_order(db_session, owner_id)

        other_user_id = uuid.uuid4()
        response = await client.get(
            f"/orders/{order.id}",
            headers={"X-User-Id": str(other_user_id)},
        )

        assert response.status_code == 403
        assert response.json()["title"] == "Access Denied"

    async def test_returns_404_for_nonexistent_order(
        self, client: httpx.AsyncClient
    ) -> None:
        user_id = uuid.uuid4()
        fake_id = uuid.uuid4()

        response = await client.get(
            f"/orders/{fake_id}",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 404
        assert response.json()["title"] == "Order Not Found"

    async def test_includes_persisted_status_history_with_timestamps(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        user_id = uuid.uuid4()
        order = await _create_db_order(db_session, user_id)
        item = order.items[0]

        await _record_status_change(
            db_session,
            order_id=order.id,
            item_id=item.id,
            previous_status="PENDIENTE_DE_PAGO",
            new_status="CONFIRMADO",
            origin="webhook",
        )

        first_response = await client.get(
            f"/orders/{order.id}",
            headers={"X-User-Id": str(user_id)},
        )
        second_response = await client.get(
            f"/orders/{order.id}",
            headers={"X-User-Id": str(user_id)},
        )

        assert first_response.status_code == 200
        assert second_response.status_code == 200

        first_data = first_response.json()
        second_data = second_response.json()

        assert len(first_data["status_history"]) == 2
        assert first_data["status_history"] == second_data["status_history"]

        first_ts = first_data["status_history"][0]["timestamp"]
        second_ts = first_data["status_history"][1]["timestamp"]
        assert first_ts <= second_ts
        assert first_data["status_history"][0]["origin"] == "buyer"
        assert first_data["status_history"][1]["origin"] == "webhook"

        assert len(first_data["items"][0]["status_history"]) == 1
        assert first_data["items"][0]["status_history"][0]["new_status"] == "CONFIRMADO"


class TestListOrders:
    """Tests for ``GET /orders``."""

    async def test_returns_user_orders(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        user_id = uuid.uuid4()
        await _create_db_order(db_session, user_id)
        await _create_db_order(db_session, user_id)

        # Other user's order
        await _create_db_order(db_session, uuid.uuid4())

        response = await client.get(
            "/orders",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["orders"]) == 2
        assert all(o["user_id"] == str(user_id) for o in data["orders"])

    async def test_filters_by_status(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        user_id = uuid.uuid4()
        await _create_db_order(db_session, user_id, OrderStatus.PENDIENTE_DE_PAGO)
        await _create_db_order(db_session, user_id, OrderStatus.CONFIRMADA)
        await _create_db_order(db_session, user_id, OrderStatus.CONFIRMADA)

        response = await client.get(
            "/orders?order_status=CONFIRMADA",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["orders"]) == 2
        assert all(o["status"] == OrderStatus.CONFIRMADA for o in data["orders"])

    async def test_filters_by_seller_id(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        user_id = uuid.uuid4()
        seller_a = uuid.uuid4()
        seller_b = uuid.uuid4()

        await _create_db_order(db_session, user_id, seller_id=seller_a)
        await _create_db_order(db_session, user_id, seller_id=seller_a)
        await _create_db_order(db_session, user_id, seller_id=seller_b)

        response = await client.get(
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

    async def test_sales_endpoint_returns_only_seller_items(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        buyer = uuid.uuid4()
        seller = uuid.uuid4()
        other_seller = uuid.uuid4()

        # Create an order that has items from seller and other_seller
        await order_repository.create_order(
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

        response = await client.get(
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

    async def test_pagination(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        user_id = uuid.uuid4()
        for _ in range(5):
            await _create_db_order(db_session, user_id)

        response = await client.get(
            "/orders?page=2&size=2",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert data["page"] == 2
        assert data["size"] == 2
        assert len(data["orders"]) == 2

    async def test_returns_empty_list_when_no_orders(
        self, client: httpx.AsyncClient
    ) -> None:
        user_id = uuid.uuid4()

        response = await client.get(
            "/orders",
            headers={"X-User-Id": str(user_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["orders"] == []


class TestGetSellerOrder:
    """Tests for ``GET /orders/sales/{order_id}``."""

    async def test_seller_can_get_order_with_their_items(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        buyer = uuid.uuid4()
        seller = uuid.uuid4()

        order = await _create_db_order(db_session, buyer, seller_id=seller)

        response = await client.get(
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

    async def test_seller_cannot_get_order_without_their_items(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        buyer = uuid.uuid4()
        seller_a = uuid.uuid4()
        seller_b = uuid.uuid4()

        order = await _create_db_order(db_session, buyer, seller_id=seller_a)

        response = await client.get(
            f"/orders/sales/{order.id}",
            headers={"X-User-Id": str(seller_b)},
        )

        assert response.status_code == 403
        assert "Access denied" in response.json()["detail"]

    async def test_seller_sees_only_their_items(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        buyer = uuid.uuid4()
        seller_a = uuid.uuid4()
        seller_b = uuid.uuid4()

        # Create order with items from both sellers
        order = await order_repository.create_order(
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

        response = await client.get(
            f"/orders/sales/{order.id}",
            headers={"X-User-Id": str(seller_a)},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["seller_id"] == str(seller_a)

    async def test_seller_history_includes_only_their_item_events(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        buyer = uuid.uuid4()
        seller_a = uuid.uuid4()
        seller_b = uuid.uuid4()

        order = await order_repository.create_order(
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

        item_a = next(item for item in order.items if item.seller_id == seller_a)
        item_b = next(item for item in order.items if item.seller_id == seller_b)

        await _record_status_change(
            db_session,
            order_id=order.id,
            item_id=None,
            previous_status="PENDIENTE_DE_PAGO",
            new_status="CONFIRMADA",
            origin="webhook",
        )
        await _record_status_change(
            db_session,
            order_id=order.id,
            item_id=item_a.id,
            previous_status="CONFIRMADO",
            new_status="EN_PREPARACION",
            origin="seller",
        )
        await _record_status_change(
            db_session,
            order_id=order.id,
            item_id=item_b.id,
            previous_status="CONFIRMADO",
            new_status="EN_PREPARACION",
            origin="seller",
        )

        response = await client.get(
            f"/orders/sales/{order.id}",
            headers={"X-User-Id": str(seller_a)},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["status_history"]) == 1
        assert data["status_history"][0]["item_id"] == str(item_a.id)
        assert data["status_history"][0]["new_status"] == "EN_PREPARACION"

        assert len(data["items"]) == 1
        assert len(data["items"][0]["status_history"]) == 1
        assert data["items"][0]["status_history"][0]["item_id"] == str(item_a.id)


async def _record_status_change(
    db: AsyncSession,
    *,
    order_id: uuid.UUID,
    item_id: uuid.UUID | None,
    previous_status: str | None,
    new_status: str,
    origin: str,
    tracking_code: str | None = None,
) -> OrderStatusHistory:
    """Backward-compatible alias for ``create_status_change``."""
    history = await order_repository.create_status_change(
        db,
        order_id=order_id,
        item_id=item_id,
        previous_status=previous_status,
        new_status=new_status,
        origin=origin,
        tracking_code=tracking_code,
    )
    await db.commit()
    return history