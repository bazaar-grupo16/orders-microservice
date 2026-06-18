"""
Integration tests for the Order repository.

Tests run against a **real PostgreSQL** instance — no mocking.
Each test uses the ``db_session`` fixture which rolls back after
the test, ensuring full isolation.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.order_status import OrderStatus
from src.repositories import order_repository


# ── Helpers ───────────────────────────────────────────────────

def _sample_address() -> dict:
    return {
        "street": "Av. Corrientes 1234",
        "city": "Buenos Aires",
        "state": "CABA",
        "zip_code": "C1043",
        "country": "Argentina",
    }


def _sample_items() -> list[dict]:
    return [
        {
            "product_id": uuid.uuid4(),
            "product_name": "Laptop Gamer",
            "quantity": 1,
            "unit_price": 1500.00,
        },
        {
            "product_id": uuid.uuid4(),
            "product_name": "Mouse Inalámbrico",
            "quantity": 2,
            "unit_price": 25.50,
        },
    ]


async def _create_test_order(db: AsyncSession, **overrides) -> "Order":  # noqa: F821
    """Helper to create an order with sensible defaults."""
    defaults = {
        "user_id": uuid.uuid4(),
        "idempotency_key": str(uuid.uuid4()),
        "shipping_address": _sample_address(),
        "total_amount": 1551.00,
        "status": OrderStatus.PENDIENTE_DE_PAGO,
        "items": _sample_items(),
    }
    defaults.update(overrides)
    return await order_repository.create_order(db, **defaults)


# ── Tests ─────────────────────────────────────────────────────


class TestCreateOrder:
    """Tests for ``order_repository.create_order``."""

    async def test_creates_order_with_items(self, db_session: AsyncSession) -> None:
        order = await _create_test_order(db_session)

        assert order.id is not None
        assert order.status == OrderStatus.PENDIENTE_DE_PAGO
        assert len(order.items) == 2
        assert order.items[0].product_name == "Laptop Gamer"
        assert order.items[1].quantity == 2
        assert order.total_amount == 1551.00

    async def test_persists_shipping_address_as_json(self, db_session: AsyncSession) -> None:
        order = await _create_test_order(db_session)

        assert order.shipping_address["city"] == "Buenos Aires"
        assert order.shipping_address["country"] == "Argentina"

    async def test_generates_uuid_primary_key(self, db_session: AsyncSession) -> None:
        order = await _create_test_order(db_session)
        assert isinstance(order.id, uuid.UUID)

    async def test_sets_timestamps(self, db_session: AsyncSession) -> None:
        order = await _create_test_order(db_session)
        assert order.created_at is not None
        assert order.updated_at is not None


class TestGetById:
    """Tests for ``order_repository.get_by_id``."""

    async def test_returns_order_when_found(self, db_session: AsyncSession) -> None:
        created = await _create_test_order(db_session)
        found = await order_repository.get_by_id(db_session, created.id)

        assert found is not None
        assert found.id == created.id
        assert found.status == created.status

    async def test_returns_none_when_not_found(self, db_session: AsyncSession) -> None:
        result = await order_repository.get_by_id(db_session, uuid.uuid4())
        assert result is None

    async def test_eagerly_loads_items(self, db_session: AsyncSession) -> None:
        created = await _create_test_order(db_session)
        found = await order_repository.get_by_id(db_session, created.id)

        assert found is not None
        assert len(found.items) == 2


class TestGetByIdempotencyKey:
    """Tests for ``order_repository.get_by_idempotency_key``."""

    async def test_returns_order_when_key_exists(self, db_session: AsyncSession) -> None:
        key = str(uuid.uuid4())
        created = await _create_test_order(db_session, idempotency_key=key)
        found = await order_repository.get_by_idempotency_key(db_session, key)

        assert found is not None
        assert found.id == created.id

    async def test_returns_none_when_key_not_found(self, db_session: AsyncSession) -> None:
        result = await order_repository.get_by_idempotency_key(
            db_session, str(uuid.uuid4()),
        )
        assert result is None

    async def test_duplicate_key_raises_integrity_error(self, db_session: AsyncSession) -> None:
        key = str(uuid.uuid4())
        await _create_test_order(db_session, idempotency_key=key)

        with pytest.raises(IntegrityError):
            await _create_test_order(db_session, idempotency_key=key)

        # Must rollback after IntegrityError to keep session usable.
        await db_session.rollback()


class TestListByUser:
    """Tests for ``order_repository.list_by_user``."""

    async def test_returns_orders_for_user(self, db_session: AsyncSession) -> None:
        user_id = uuid.uuid4()
        await _create_test_order(db_session, user_id=user_id)
        await _create_test_order(db_session, user_id=user_id)
        # Another user's order — should not appear.
        await _create_test_order(db_session, user_id=uuid.uuid4())

        orders, total = await order_repository.list_by_user(db_session, user_id)

        assert total == 2
        assert len(orders) == 2
        assert all(o.user_id == user_id for o in orders)

    async def test_filters_by_status(self, db_session: AsyncSession) -> None:
        user_id = uuid.uuid4()
        await _create_test_order(db_session, user_id=user_id)
        confirmed = await _create_test_order(db_session, user_id=user_id)
        await order_repository.update_status(
            db_session, confirmed, OrderStatus.CONFIRMADA,
        )

        orders, total = await order_repository.list_by_user(
            db_session, user_id, status_filter=OrderStatus.CONFIRMADA,
        )

        assert total == 1
        assert orders[0].status == OrderStatus.CONFIRMADA

    async def test_pagination_offset_and_limit(self, db_session: AsyncSession) -> None:
        user_id = uuid.uuid4()
        for _ in range(5):
            await _create_test_order(db_session, user_id=user_id)

        orders, total = await order_repository.list_by_user(
            db_session, user_id, offset=2, limit=2,
        )

        assert total == 5
        assert len(orders) == 2

    async def test_returns_empty_for_unknown_user(self, db_session: AsyncSession) -> None:
        orders, total = await order_repository.list_by_user(
            db_session, uuid.uuid4(),
        )
        assert total == 0
        assert orders == []


class TestUpdateStatus:
    """Tests for ``order_repository.update_status``."""

    async def test_updates_status(self, db_session: AsyncSession) -> None:
        order = await _create_test_order(db_session)

        updated = await order_repository.update_status(
            db_session, order, OrderStatus.CONFIRMADA,
        )

        assert updated.status == OrderStatus.CONFIRMADA

    async def test_updates_updated_at_timestamp(self, db_session: AsyncSession) -> None:
        order = await _create_test_order(db_session)
        original_updated_at = order.updated_at

        updated = await order_repository.update_status(
            db_session, order, OrderStatus.CONFIRMADA,
        )

        assert updated.updated_at >= original_updated_at


class TestUpdateTrackingCode:
    """Tests for ``order_repository.update_tracking_code`` (deprecated - tracking moved to OrderItem)."""

    async def test_sets_tracking_code_on_order(self, db_session: AsyncSession) -> None:
        """Tracking code is now per-item, but order still has old field for migrations."""
        from src.models.order_status import OrderItemLogisticStatus
        order = await _create_test_order(db_session)
        
        # Verify order item has tracking_code (per-item now)
        item = order.items[0]
        assert item.tracking_code is None
        
        updated_item = await order_repository.update_item_status(
            db_session, item, OrderItemLogisticStatus.ENVIADO, tracking_code="TRACK-12345-AR"
        )
        
        assert updated_item.tracking_code == "TRACK-12345-AR"
        assert updated_item.status == OrderItemLogisticStatus.ENVIADO
