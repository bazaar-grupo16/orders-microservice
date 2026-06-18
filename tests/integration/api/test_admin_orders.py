"""
Integration tests for admin order endpoints.

Tests run against a **real PostgreSQL** database (no DB mocking).

Endpoints under test:
    GET /orders/admin/orders
    GET /orders/admin/orders/{order_id}
"""

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.order import Order
from src.models.order_status import OrderStatus
from src.models.order_status_history import OrderStatusHistory
from src.repositories import order_repository

_ADMIN_HEADERS = {"X-Is-Admin": "true"}

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
    order = await order_repository.create_order(
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
    await db_session.commit()
    return order


async def _record_status_change(
    db: AsyncSession,
    *,
    order_id: uuid.UUID,
    item_id: uuid.UUID | None,
    previous_status: str | None,
    new_status: str,
    origin: str,
) -> OrderStatusHistory:
    history = await order_repository.create_status_change(
        db,
        order_id=order_id,
        item_id=item_id,
        previous_status=previous_status,
        new_status=new_status,
        origin=origin,
    )
    await db.commit()
    return history


# ── GET /orders/admin/orders ────────────────────────────────────────

class TestListAdminOrders:
    """Tests for GET /orders/admin/orders."""

    async def test_admin_sees_orders_from_all_users(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.get("/orders/admin/orders", headers=_ADMIN_HEADERS)
        baseline = r.json()["total"]

        await _create_db_order(db_session, uuid.uuid4())
        await _create_db_order(db_session, uuid.uuid4())
        await _create_db_order(db_session, uuid.uuid4())

        response = await client.get(f"/orders/admin/orders?size={baseline + 10}", headers=_ADMIN_HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == baseline + 3
        assert len(data["orders"]) == baseline + 3

    async def test_filters_by_order_status(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        status = OrderStatus.CONFIRMADA
        r = await client.get(
            f"/orders/admin/orders?order_status={status}",
            headers=_ADMIN_HEADERS,
        )
        baseline = r.json()["total"]

        await _create_db_order(db_session, uuid.uuid4(), OrderStatus.PENDIENTE_DE_PAGO)
        await _create_db_order(db_session, uuid.uuid4(), status)
        await _create_db_order(db_session, uuid.uuid4(), status)

        response = await client.get(
            f"/orders/admin/orders?order_status={status}&size={baseline + 10}",
            headers=_ADMIN_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == baseline + 2
        assert len(data["orders"]) == baseline + 2
        assert all(o["status"] == status for o in data["orders"])

    async def test_pagination(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        status = OrderStatus.REEMBOLSO_EN_PROCESO
        r = await client.get(
            f"/orders/admin/orders?order_status={status}",
            headers=_ADMIN_HEADERS,
        )
        baseline = r.json()["total"]

        for _ in range(5):
            await _create_db_order(db_session, uuid.uuid4(), status=status)

        response = await client.get(
            f"/orders/admin/orders?order_status={status}&page=2&size=2",
            headers=_ADMIN_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == baseline + 5
        assert data["page"] == 2
        assert data["size"] == 2
        assert len(data["orders"]) == 2

    async def test_returns_empty_list_when_no_orders(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(
            "/orders/admin/orders?order_status=REEMBOLSO_PROCESADO",
            headers=_ADMIN_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["orders"] == []

    async def test_non_admin_returns_403(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/orders/admin/orders",
            headers={"X-Is-Admin": "false"},
        )

        assert response.status_code == 403
        assert "administrador" in response.json()["detail"].lower()

    async def test_missing_admin_header_returns_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/orders/admin/orders")

        assert response.status_code == 422

    async def test_invalid_order_status_returns_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/orders/admin/orders?order_status=INVALIDO",
            headers=_ADMIN_HEADERS,
        )

        assert response.status_code == 422

    async def test_page_zero_returns_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/orders/admin/orders?page=0",
            headers=_ADMIN_HEADERS,
        )

        assert response.status_code == 422

    async def test_size_zero_returns_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/orders/admin/orders?size=0",
            headers=_ADMIN_HEADERS,
        )

        assert response.status_code == 422


# ── GET /orders/admin/orders/{order_id} ─────────────────────────────

class TestGetAdminOrderDetail:
    """Tests for GET /orders/admin/orders/{order_id}."""

    async def test_admin_can_get_any_order(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        owner_id = uuid.uuid4()
        order = await _create_db_order(db_session, owner_id)

        response = await client.get(
            f"/orders/admin/orders/{order.id}",
            headers=_ADMIN_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["order_id"] == str(order.id)
        assert data["user_id"] == str(owner_id)
        assert len(data["items"]) == 1

    async def test_includes_status_history(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        owner_id = uuid.uuid4()
        order = await _create_db_order(db_session, owner_id)
        item = order.items[0]

        await _record_status_change(
            db_session,
            order_id=order.id,
            item_id=item.id,
            previous_status="PENDIENTE_DE_PAGO",
            new_status="CONFIRMADO",
            origin="webhook",
        )

        response = await client.get(
            f"/orders/admin/orders/{order.id}",
            headers=_ADMIN_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["status_history"]) == 2
        assert len(data["items"][0]["status_history"]) == 1
        assert data["items"][0]["status_history"][0]["new_status"] == "CONFIRMADO"

    async def test_nonexistent_order_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        fake_id = uuid.uuid4()

        response = await client.get(
            f"/orders/admin/orders/{fake_id}",
            headers=_ADMIN_HEADERS,
        )

        assert response.status_code == 404
        assert response.json()["title"] == "Order Not Found"

    async def test_non_admin_returns_403(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        order = await _create_db_order(db_session, uuid.uuid4())

        response = await client.get(
            f"/orders/admin/orders/{order.id}",
            headers={"X-Is-Admin": "false"},
        )

        assert response.status_code == 403

    async def test_missing_admin_header_returns_422(
        self, client: httpx.AsyncClient, db_session: AsyncSession
    ) -> None:
        order = await _create_db_order(db_session, uuid.uuid4())

        response = await client.get(f"/orders/admin/orders/{order.id}")

        assert response.status_code == 422

    async def test_malformed_uuid_returns_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/orders/admin/orders/not-a-uuid",
            headers=_ADMIN_HEADERS,
        )

        assert response.status_code == 422
