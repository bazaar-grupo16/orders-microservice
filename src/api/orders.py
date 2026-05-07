"""
Orders API router.

All endpoints require the ``X-User-Id`` header which is injected by
NGINX Ingress after JWT validation — except the payment webhook which
is called internally by the Payments service.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status

from src.core.dependencies import CurrentUserId, DbSession
from src.schemas import (
    CreateOrderRequest,
    OrderCreatedResponse,
    OrderListResponse,
    OrderResponse,
    PaymentWebhookRequest,
    UpdateStatusRequest,
)
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
        preference_id=order.preference_id,
        init_point=order.init_point,
    )


@router.post(
    "/{order_id}/payment-webhook",
    status_code=status.HTTP_200_OK,
    summary="Payment webhook — Saga resolution",
)
def payment_webhook(
    order_id: UUID,
    body: PaymentWebhookRequest,
    db: DbSession,
) -> dict:
    """Callback from the Payments service to resolve the Saga.

    This is an **internal endpoint** — called by the Payments service
    over the K8s internal network.  No ``X-User-Id`` header is required.

    - ``SUCCESS`` → ``CONFIRMADA`` + commit stock + clear cart.
    - ``FAILED``  → ``PAGO_RECHAZADO`` + rollback stock (compensation).
    """
    order = order_service.resolve_payment(
        db,
        order_id=order_id,
        payment_status=body.status,
        transaction_id=body.transaction_id,
    )

    return {"order_id": str(order.id), "status": order.status}


@router.get(
    "",
    response_model=OrderListResponse,
    status_code=status.HTTP_200_OK,
    summary="List orders for the current user",
)
def list_orders(
    db: DbSession,
    user_id: CurrentUserId,
    order_status: str | None = None,
    page: int = 1,
    size: int = 20,
) -> OrderListResponse:
    """Retrieve a paginated list of orders belonging to the user."""
    orders, total = order_service.list_orders(
        db,
        user_id=user_id,
        status_filter=order_status,
        page=page,
        size=size,
    )

    # Convert SQLAlchemy Order models to OrderResponse schemas
    # Note: Pydantic from_attributes handles the conversion, but we need to map id -> order_id
    order_responses = []
    for order in orders:
        order_dict = order.__dict__.copy()
        order_dict["order_id"] = order.id
        order_responses.append(OrderResponse.model_validate(order_dict))

    return OrderListResponse(
        orders=order_responses,
        total=total,
        page=page,
        size=size,
    )


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a specific order",
)
def get_order(
    order_id: UUID,
    db: DbSession,
    user_id: CurrentUserId,
) -> OrderResponse:
    """Retrieve a single order by ID.
    
    The order must belong to the authenticated user.
    """
    order = order_service.get_order(
        db,
        order_id=order_id,
        user_id=user_id,
    )
    
    order_dict = order.__dict__.copy()
    order_dict["order_id"] = order.id
    return OrderResponse.model_validate(order_dict)


@router.patch(
    "/{order_id}/status",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Update order status (Seller)",
)
def update_order_status(
    order_id: UUID,
    body: UpdateStatusRequest,
    db: DbSession,
    # NOTE: In a real system, NGINX might validate seller roles or we'd check it here.
    # We are keeping it simple per the plan.
) -> OrderResponse:
    """Update the status of an order.
    
    This endpoint allows sellers to transition an order through its
    fulfillment lifecycle (e.g., CONFIRMADA -> EN_PREPARACION).
    """
    order = order_service.update_order_status(
        db,
        order_id=order_id,
        new_status=body.status,
        tracking_code=body.tracking_code,
    )
    
    order_dict = order.__dict__.copy()
    order_dict["order_id"] = order.id
    return OrderResponse.model_validate(order_dict)
