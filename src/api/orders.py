"""
Orders API router.

All endpoints require the ``X-User-Id`` header which is injected by
NGINX Ingress after JWT validation.
"""

import logging

from fastapi import APIRouter, Header, HTTPException, status

from src.core.dependencies import CurrentUserId, DbSession
from src.schemas import CreateOrderRequest, OrderCreatedResponse
from src.services import order_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post(
    "",
    response_model=OrderCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new order (Saga orchestration)",
)
def create_order(
    body: CreateOrderRequest,
    db: DbSession,
    user_id: CurrentUserId,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> OrderCreatedResponse:
    """Start the checkout Saga.

    1. Check idempotency key.
    2. Fetch the user's cart.
    3. Reserve stock in Inventory.
    4. Persist order as ``PENDIENTE_DE_PAGO``.

    The ``Idempotency-Key`` header is **required** — a UUID v4 generated
    by the mobile app when the user taps "Pay".
    """
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Idempotency-Key header",
        )

    order = order_service.create_order(
        db,
        user_id=user_id,
        idempotency_key=idempotency_key,
        shipping_address=body.shipping_address.model_dump(),
    )

    return OrderCreatedResponse(
        order_id=order.id,
        status=order.status,
    )
