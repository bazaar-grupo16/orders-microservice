"""
Integration tests for order cancellation flows.
"""

import uuid
import json

import httpx
import respx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.order_status import OrderItemLogisticStatus, OrderStatus
from src.repositories import order_repository


_VALID_ADDRESS = {
    "street": "Av. Corrientes 1234",
    "city": "Buenos Aires",
    "state": "CABA",
    "zip_code": "C1043",
    "country": "Argentina",
}


async def _create_order(
    db_session: AsyncSession,
    *,
    buyer_id: uuid.UUID,
    seller_id: uuid.UUID,
    status: str = OrderStatus.CONFIRMADA,
):
    return await order_repository.create_order(
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


async def _create_multi_item_order(
    db_session: AsyncSession,
    *,
    buyer_id: uuid.UUID,
    seller_ids: list[uuid.UUID],
    status: str = OrderStatus.CONFIRMADA,
):
    return await order_repository.create_order(
        db_session,
        user_id=buyer_id,
        idempotency_key=str(uuid.uuid4()),
        shipping_address=_VALID_ADDRESS,
        total_amount=3000.0,
        status=status,
        items=[
            {
                "product_id": uuid.uuid4(),
                "seller_id": seller_ids[0],
                "product_name": "Test Product A",
                "quantity": 1,
                "unit_price": 1500.0,
            },
            {
                "product_id": uuid.uuid4(),
                "seller_id": seller_ids[1],
                "product_name": "Test Product B",
                "quantity": 1,
                "unit_price": 1500.0,
            },
        ],
    )


def _mock_cancel_side_effects(order_id: uuid.UUID):
    def release_check(request):
        data = json.loads(request.read())
        assert data == {"order_id": str(order_id)}
        return httpx.Response(200)

    rollback_route = respx.post(
        "http://inventory-service:8000/internal/products/stock/release"
    ).mock(side_effect=release_check)
    item_rollback_route = respx.post(
        url__regex=r"http://inventory-service:8000/internal/products/[0-9a-fA-F\-]+/stock/release"
    ).mock(side_effect=release_check)
    refund_route = respx.post(
        f"http://payments-service:8000/payments/{order_id}/refund"
    ).mock(return_value=httpx.Response(200))
    respx.post(
        f"http://payments-service:8000/payments/{order_id}/cancel"
    ).mock(return_value=httpx.Response(200))
    return rollback_route, item_rollback_route, refund_route


class TestCancelOrderByBuyer:
    """Buyer can cancel an eligible confirmed order."""

    @pytest.mark.parametrize(
        ("order_status", "expect_rollback"),
        [
            (OrderStatus.PENDIENTE_DE_PAGO, True),
            (OrderStatus.CONFIRMADA, True),
            (OrderStatus.EN_PREPARACION, True),
        ],
    )
    @respx.mock
    async def test_cancels_order_in_allowed_states(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
        order_status: str,
        expect_rollback: bool,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = await _create_order(db_session, buyer_id=buyer_id, seller_id=seller_id, status=order_status)
        rollback_route, _, refund_route = _mock_cancel_side_effects(order.id)

        response = await client.post(
            f"/orders/{order.id}/cancel",
            headers={"X-User-Id": str(buyer_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.CANCELADA
        assert data["items"][0]["status"] == OrderItemLogisticStatus.CANCELADO
        assert any(event["new_status"] == OrderStatus.CANCELADA for event in data["status_history"])
        assert any(
            event["new_status"] == OrderItemLogisticStatus.CANCELADO
            for event in data["items"][0]["status_history"]
        )
        assert rollback_route.called == expect_rollback
        assert not refund_route.called

    @respx.mock
    async def test_rejects_cancellation_from_other_user(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = await _create_order(db_session, buyer_id=buyer_id, seller_id=seller_id)

        response = await client.post(
            f"/orders/{order.id}/cancel",
            headers={"X-User-Id": str(uuid.uuid4())},
        )

        assert response.status_code == 403
        assert response.json()["title"] == "Access Denied"

    @respx.mock
    async def test_cancels_multi_item_order_and_rolls_back_all_items(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id_1 = uuid.uuid4()
        seller_id_2 = uuid.uuid4()
        order = await _create_multi_item_order(
            db_session,
            buyer_id=buyer_id,
            seller_ids=[seller_id_1, seller_id_2],
            status=OrderStatus.CONFIRMADA,
        )
        product_ids = [str(item.product_id) for item in order.items]
        rollback_route, _, refund_route = _mock_cancel_side_effects(order.id)

        response = await client.post(
            f"/orders/{order.id}/cancel",
            headers={"X-User-Id": str(buyer_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.CANCELADA
        assert all(item["status"] == OrderItemLogisticStatus.CANCELADO for item in data["items"])
        assert rollback_route.called
        assert not refund_route.called
        assert any(event["new_status"] == OrderStatus.CANCELADA for event in data["status_history"])
        assert {item["product_id"] for item in data["items"]} == set(product_ids)

    @respx.mock
    async def test_cancelling_pending_order_cancells_payment(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = await _create_order(db_session, buyer_id=buyer_id, seller_id=seller_id, status=OrderStatus.PENDIENTE_DE_PAGO)
        rollback_route, _, refund_route = _mock_cancel_side_effects(order.id)

        cancel_payment_route = respx.post(
            f"http://payments-service:8000/payments/{order.id}/cancel"
        ).mock(return_value=httpx.Response(200))

        response = await client.post(
            f"/orders/{order.id}/cancel",
            headers={"X-User-Id": str(buyer_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.CANCELADA
        assert data["items"][0]["status"] == OrderItemLogisticStatus.CANCELADO
        assert any(event["new_status"] == OrderStatus.CANCELADA for event in data["status_history"])
        assert any(
            event["new_status"] == OrderItemLogisticStatus.CANCELADO
            for event in data["items"][0]["status_history"]
        )
        assert rollback_route.called
        assert not refund_route.called
        assert cancel_payment_route.called


class TestCancelOrderBySeller:
    """Seller can cancel a single confirmed item."""

    @respx.mock
    async def test_cancels_single_item_and_closes_order(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = await _create_order(db_session, buyer_id=buyer_id, seller_id=seller_id, status=OrderStatus.CONFIRMADA)
        item_id = order.items[0].id
        _, item_rollback_route, refund_route = _mock_cancel_side_effects(order.id)

        response = await client.post(
            f"/orders/sales/{order.id}/items/{item_id}/cancel",
            headers={"X-User-Id": str(seller_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.CANCELADA
        assert data["items"][0]["status"] == OrderItemLogisticStatus.CANCELADO
        assert item_rollback_route.called
        assert not refund_route.called
        assert any(event["new_status"] == OrderStatus.CANCELADA for event in data["status_history"])
        assert any(
            event["new_status"] == OrderItemLogisticStatus.CANCELADO
            for event in data["items"][0]["status_history"]
        )

    @respx.mock
    async def test_cancel_order_item_releases_stock_unconditionally(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = await _create_order(db_session, buyer_id=buyer_id, seller_id=seller_id, status=OrderStatus.CONFIRMADA)

        # Manually mutate order status to a non-eligible one for the old check (e.g., PENDIENTE_DE_PAGO)
        order.status = OrderStatus.PENDIENTE_DE_PAGO.value
        await db_session.commit()

        def release_check(request):
            data = json.loads(request.read())
            assert data == {"order_id": str(order.id)}
            return httpx.Response(200)

        rollback_route = respx.post(
            "http://inventory-service:8000/internal/products/stock/release"
        ).mock(side_effect=release_check)

        cancel_payment_route = respx.post(
            f"http://payments-service:8000/payments/{order.id}/cancel"
        ).mock(return_value=httpx.Response(200))

        response = await client.post(
            f"/orders/{order.id}/cancel",
            headers={"X-User-Id": str(buyer_id)},
        )

        assert response.status_code == 200
        assert rollback_route.called
        assert cancel_payment_route.called

    @respx.mock
    async def test_cancels_one_item_and_keeps_order_open_when_others_remain(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = await _create_multi_item_order(
            db_session,
            buyer_id=buyer_id,
            seller_ids=[seller_id, seller_id],
            status=OrderStatus.PENDIENTE_DE_PAGO,
        )
        item_id = order.items[0].id

        confirm_response = await client.patch(
            f"/orders/{order.id}/status",
            json={"status": OrderStatus.CONFIRMADA},
            headers={"X-User-Id": str(seller_id)},
        )
        assert confirm_response.status_code == 200

        def release_check(request):
            data = json.loads(request.read())
            assert data == {"order_id": str(order.id)}
            return httpx.Response(200)

        rollback_route = respx.post(
            f"http://inventory-service:8000/internal/products/{order.items[0].product_id}/stock/release"
        ).mock(side_effect=release_check)
        refund_route = respx.post(
            f"http://payments-service:8000/payments/{order.id}/refund"
        ).mock(return_value=httpx.Response(200))

        response = await client.post(
            f"/orders/sales/{order.id}/items/{item_id}/cancel",
            headers={"X-User-Id": str(seller_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.CONFIRMADA
        assert data["items"][0]["status"] == OrderItemLogisticStatus.CANCELADO
        assert data["items"][1]["status"] == OrderItemLogisticStatus.CONFIRMADO
        assert rollback_route.called
        assert not refund_route.called
        assert any(event["new_status"] == OrderItemLogisticStatus.CANCELADO for event in data["status_history"])

    @respx.mock
    async def test_partial_cancellation_keeps_complete_history(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = await _create_multi_item_order(
            db_session,
            buyer_id=buyer_id,
            seller_ids=[seller_id, seller_id],
            status=OrderStatus.PENDIENTE_DE_PAGO,
        )
        item_id = order.items[0].id

        confirm_response = await client.patch(
            f"/orders/{order.id}/status",
            json={"status": OrderStatus.CONFIRMADA},
            headers={"X-User-Id": str(seller_id)},
        )
        assert confirm_response.status_code == 200

        _, item_rollback_route, refund_route = _mock_cancel_side_effects(order.id)

        response = await client.post(
            f"/orders/sales/{order.id}/items/{item_id}/cancel",
            headers={"X-User-Id": str(seller_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.CONFIRMADA
        assert len(data["status_history"]) == 5
        assert any(event["new_status"] == OrderStatus.CONFIRMADA for event in data["status_history"])
        assert any(event["new_status"] == OrderItemLogisticStatus.CONFIRMADO for event in data["status_history"])
        assert any(event["new_status"] == OrderItemLogisticStatus.CANCELADO for event in data["status_history"])
        assert item_rollback_route.called
        assert not refund_route.called

    @respx.mock
    async def test_rejects_pending_order_item_cancellation_before_confirmation(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = await _create_order(
            db_session,
            buyer_id=buyer_id,
            seller_id=seller_id,
            status=OrderStatus.PENDIENTE_DE_PAGO,
        )
        item_id = order.items[0].id

        response = await client.post(
            f"/orders/sales/{order.id}/items/{item_id}/cancel",
            headers={"X-User-Id": str(seller_id)},
        )

        assert response.status_code == 409
        assert "before the order is confirmed" in response.json()["detail"]


class TestCancelOrderGuards:
    """Shipped orders cannot be cancelled."""

    async def test_returns_409_when_order_is_already_sent(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = await _create_order(db_session, buyer_id=buyer_id, seller_id=seller_id, status=OrderStatus.ENVIADA)

        response = await client.post(
            f"/orders/{order.id}/cancel",
            headers={"X-User-Id": str(buyer_id)},
        )

        assert response.status_code == 409


class TestCancelOrderWithRefund:
    """Cancelling a paid order triggers refund flow."""

    @respx.mock
    async def test_cancels_confirmed_order_and_requests_refund(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        pytest.skip("Ignored test: refunds not yet implemented")
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = await _create_order(db_session, buyer_id=buyer_id, seller_id=seller_id, status=OrderStatus.CONFIRMADA)
        item_id = order.items[0].id

        # Mock stock rollback
        rollback_route = respx.post(
            "http://inventory-service:8000/internal/products/stock/release"
        ).mock(return_value=httpx.Response(200))

        # Mock refund endpoint
        refund_route = respx.post(
            f"http://payments-service:8000/payments/{order.id}/refund"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "refund_id": "refund-123",
                    "order_id": str(order.id),
                    "order_item_id": str(item_id),
                    "status": "COMPLETED",
                    "amount": 1500.0,
                },
            )
        )

        response = await client.post(
            f"/orders/{order.id}/cancel",
            headers={"X-User-Id": str(buyer_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.REEMBOLSO_EN_PROCESO
        assert data["items"][0]["status"] == OrderItemLogisticStatus.REEMBOLSO_EN_PROCESO

        # Verify refund was called
        assert refund_route.called
        assert json.loads(refund_route.calls.last.request.content) == {"order_item_id": str(item_id)}

        # Verify stock rollback was called
        assert rollback_route.called

        # Verify status history contains refund events
        status_history = data["status_history"]
        assert any(event["new_status"] == OrderStatus.CANCELADA for event in status_history)
        assert any(event["new_status"] == OrderStatus.REEMBOLSO_EN_PROCESO for event in status_history)

        refund_webhook_response = await client.post(
            f"/orders/{order.id}/refund-webhook",
            json={
                "order_item_id": str(item_id),
                "refund_status": "approved",
            },
        )
        assert refund_webhook_response.status_code == 200

        detail_response = await client.get(
            f"/orders/{order.id}",
            headers={"X-User-Id": str(buyer_id)},
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["status"] == OrderStatus.REEMBOLSO_PROCESADO
        assert detail["items"][0]["status"] == OrderItemLogisticStatus.REEMBOLSO_PROCESADO
        assert any(event["new_status"] == OrderItemLogisticStatus.REEMBOLSO_PROCESADO for event in detail["status_history"])
        assert any(event["new_status"] == OrderStatus.REEMBOLSO_PROCESADO for event in detail["status_history"])

    @respx.mock
    async def test_cancels_confirmed_order_with_multiple_items_and_refunds_each(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        pytest.skip("Ignored test: refunds not yet implemented")
        """Verify refund is called once per item in a multi-item order."""
        buyer_id = uuid.uuid4()
        seller_id_1 = uuid.uuid4()
        seller_id_2 = uuid.uuid4()

        order = await order_repository.create_order(
            db_session,
            user_id=buyer_id,
            idempotency_key=str(uuid.uuid4()),
            shipping_address=_VALID_ADDRESS,
            total_amount=3000.0,
            status=OrderStatus.CONFIRMADA,
            items=[
                {
                    "product_id": uuid.uuid4(),
                    "seller_id": seller_id_1,
                    "product_name": "Product 1",
                    "quantity": 1,
                    "unit_price": 1500.0,
                },
                {
                    "product_id": uuid.uuid4(),
                    "seller_id": seller_id_2,
                    "product_name": "Product 2",
                    "quantity": 1,
                    "unit_price": 1500.0,
                },
            ],
        )
        item_ids = [item.id for item in order.items]

        # Mock stock rollback
        respx.post(
            "http://inventory-service:8000/internal/products/stock/release"
        ).mock(return_value=httpx.Response(200))

        # Mock refund endpoints for both items
        refund_route = respx.post(
            f"http://payments-service:8000/payments/{order.id}/refund"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "refund_id": "refund-456",
                    "order_id": str(order.id),
                    "order_item_id": "",  # Will be different per call
                    "status": "COMPLETED",
                    "amount": 1500.0,
                },
            )
        )

        response = await client.post(
            f"/orders/{order.id}/cancel",
            headers={"X-User-Id": str(buyer_id)},
        )

        assert response.status_code == 200
        data = response.json()

        # Verify order is in refund progress while the external refund is pending
        assert data["status"] == OrderStatus.REEMBOLSO_EN_PROCESO

        # Verify refund was called for each item
        assert refund_route.call_count == 2
        request_bodies = [json.loads(call.request.content) for call in refund_route.calls]
        assert request_bodies[0]["order_item_id"] == str(item_ids[0])
        assert request_bodies[1]["order_item_id"] == str(item_ids[1])

    @respx.mock
    async def test_cancellation_completes_even_if_refund_fails(
        self,
        client: httpx.AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        pytest.skip("Ignored test: refunds not yet implemented")
        """Best-effort: cancellation succeeds even if refund endpoint fails."""
        buyer_id = uuid.uuid4()
        seller_id = uuid.uuid4()
        order = await _create_order(db_session, buyer_id=buyer_id, seller_id=seller_id, status=OrderStatus.CONFIRMADA)

        # Mock stock rollback
        respx.post(
            "http://inventory-service:8000/internal/products/stock/release"
        ).mock(return_value=httpx.Response(200))

        # Mock refund endpoint to fail
        respx.post(
            f"http://payments-service:8000/payments/{order.id}/refund"
        ).mock(return_value=httpx.Response(500, json={"detail": "Service unavailable"}))

        response = await client.post(
            f"/orders/{order.id}/cancel",
            headers={"X-User-Id": str(buyer_id)},
        )

        assert response.status_code == 200
        data = response.json()

        # Order is cancelled but refund stays in progress (not fully processed)
        assert data["status"] == OrderStatus.REEMBOLSO_EN_PROCESO

        # Verify transition to REEMBOLSO_EN_PROCESO is recorded
        status_history = data["status_history"]
        assert any(event["new_status"] == OrderStatus.REEMBOLSO_EN_PROCESO for event in status_history)

        # REEMBOLSO_PROCESADO should NOT be present since refund failed
        assert not any(event["new_status"] == OrderStatus.REEMBOLSO_PROCESADO for event in status_history)
