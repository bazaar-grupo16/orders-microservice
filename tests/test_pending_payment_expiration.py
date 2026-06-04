"""Tests for pending-payment order expiration."""

from datetime import timedelta
import uuid

import httpx
import respx
from sqlalchemy.orm import Session

from src.core.utils import utc_now
from src.models.order_status import OrderItemLogisticStatus, OrderStatus
from src.repositories import order_repository
from src.services.order_service.expiration import expire_pending_payment_orders


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
    status: str = OrderStatus.PENDIENTE_DE_PAGO.value,
    age_minutes: int = 60,
):
    order = order_repository.create_order(
        db_session,
        user_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        shipping_address=_VALID_ADDRESS,
        total_amount=100.0,
        status=status,
        items=[
            {
                "product_id": uuid.uuid4(),
                "seller_id": uuid.uuid4(),
                "product_name": "Test Product",
                "quantity": 2,
                "unit_price": 50.0,
            }
        ],
    )
    order.created_at = utc_now() - timedelta(minutes=age_minutes)
    db_session.commit()
    db_session.refresh(order)
    return order


@respx.mock
def test_expires_old_pending_payment_order(db_session: Session) -> None:
    order = _create_order(db_session)
    rollback_route = respx.post(
        "http://inventory-service:8000/internal/products/stock/release"
    ).mock(return_value=httpx.Response(200))

    expired_count = expire_pending_payment_orders(db_session)

    db_session.refresh(order)
    assert expired_count == 1
    assert order.status == OrderStatus.PAGO_RECHAZADO.value
    assert order.items[0].status == OrderItemLogisticStatus.CANCELADO.value
    assert rollback_route.called


@respx.mock
def test_does_not_expire_recent_pending_payment_order(db_session: Session) -> None:
    order = _create_order(db_session, age_minutes=5)
    rollback_route = respx.post(
        "http://inventory-service:8000/internal/products/stock/release"
    ).mock(return_value=httpx.Response(200))

    expired_count = expire_pending_payment_orders(db_session)

    db_session.refresh(order)
    assert expired_count == 0
    assert order.status == OrderStatus.PENDIENTE_DE_PAGO.value
    assert not rollback_route.called


@respx.mock
def test_does_not_expire_confirmed_order(db_session: Session) -> None:
    order = _create_order(
        db_session,
        status=OrderStatus.CONFIRMADA.value,
        age_minutes=60,
    )
    rollback_route = respx.post(
        "http://inventory-service:8000/internal/products/stock/release"
    ).mock(return_value=httpx.Response(200))

    expired_count = expire_pending_payment_orders(db_session)

    db_session.refresh(order)
    assert expired_count == 0
    assert order.status == OrderStatus.CONFIRMADA.value
    assert not rollback_route.called


@respx.mock
def test_inventory_failure_does_not_break_expiration(db_session: Session) -> None:
    order = _create_order(db_session)
    rollback_route = respx.post(
        "http://inventory-service:8000/internal/products/stock/release"
    ).mock(return_value=httpx.Response(500))

    expired_count = expire_pending_payment_orders(db_session)

    db_session.refresh(order)
    assert expired_count == 1
    assert order.status == OrderStatus.PAGO_RECHAZADO.value
    assert rollback_route.called
