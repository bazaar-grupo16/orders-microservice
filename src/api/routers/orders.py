"""Orders API router."""

import logging
import uuid
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
    UpdateItemStatusRequest
)
from src.schemas.webhook import RefundWebhookRequest
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
    """Start the checkout Saga."""
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Idempotency-Key header",
        )

    try:
        val = uuid.UUID(idempotency_key)
        if val.version != 4:
            raise ValueError()
        key_str = str(val)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Idempotency-Key format. Must be a valid UUIDv4.",
        )

    order = order_service.create_order(
        db=db,
        user_id=user_id,
        idempotency_key=key_str,
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
    """Callback from the Payments service to resolve the Saga."""
    order = order_service.resolve_payment(
        db,
        order_id=order_id,
        payment_status=body.status,
        transaction_id=body.transaction_id,
    )
    return {"order_id": str(order.id), "status": order.status}


@router.post(
    "/{order_id}/cancel",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel an order as the buyer",
)
def cancel_order(
    order_id: UUID,
    db: DbSession,
    user_id: CurrentUserId,
) -> OrderResponse:
    order = order_service.cancel_order(
        db,
        order_id=order_id,
        user_id=user_id,
    )
    return _serialize_order(order_service, db, order)


@router.post(
    "/{order_id}/items/{item_id}/cancel",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel a single item as the buyer",
)
def cancel_order_item(
    order_id: UUID,
    item_id: UUID,
    db: DbSession,
    user_id: CurrentUserId,
) -> OrderResponse:
    order = order_service.cancel_order_item(
        db,
        order_id=order_id,
        item_id=item_id,
        user_id=user_id,
        by="buyer",
    )
    return _serialize_order(order_service, db, order)


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
    seller_id: UUID | None = None,
    page: int = 1,
    size: int = 20,
) -> OrderListResponse:
    """Retrieve a paginated list of orders belonging to the user."""
    orders, total = order_service.list_orders(
        db,
        user_id=user_id,
        status_filter=order_status,
        seller_id=seller_id,
        page=page,
        size=size,
    )

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


@router.post(
    "/{order_id}/items/{item_id}/confirm-delivery",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm delivery for a specific item by the buyer",
)
def confirm_item_delivery(
    order_id: UUID,
    item_id: UUID,
    db: DbSession,
    user_id: CurrentUserId,
) -> OrderResponse:
    """Confirm receipt of a single order item by the authenticated buyer."""
    order = order_service.confirm_item_delivery(
        db,
        order_id=order_id,
        item_id=item_id,
        user_id=user_id,
    )

    full_history = order_service.get_status_history(
        db,
        order_id=order_id,
    )
    history_by_item_id: dict[UUID, list] = {}
    for event in full_history:
        if event.item_id is None:
            continue
        history_by_item_id.setdefault(event.item_id, []).append(event)

    item_dicts = []
    for item in order.items:
        item_dict = item.__dict__.copy()
        item_dict["status_history"] = history_by_item_id.get(item.id, [])
        item_dicts.append(item_dict)

    order_dict = order.__dict__.copy()
    order_dict["order_id"] = order.id
    order_dict["items"] = item_dicts
    order_dict["status_history"] = full_history
    return OrderResponse.model_validate(order_dict)


@router.get(
    "/sales",
    response_model=OrderListResponse,
    status_code=status.HTTP_200_OK,
    summary="List sales for the current seller",
)
def list_sales(
    db: DbSession,
    user_id: CurrentUserId,
    order_status: str | None = None,
    page: int = 1,
    size: int = 20,
) -> OrderListResponse:
    """Retrieve paginated orders that contain items sold by the authenticated user."""
    orders, total = order_service.list_sales(
        db,
        seller_id=user_id,
        status_filter=order_status,
        page=page,
        size=size,
    )

    order_responses = []
    for order in orders:
        order_dict = order.__dict__.copy()
        order_dict["order_id"] = order.id

        filtered_items = []
        for item in order.items:
            if getattr(item, "seller_id", None) == user_id:
                item_dict = item.__dict__.copy()
                item_dict["product_id"] = item.product_id
                filtered_items.append(item_dict)

        order_dict["items"] = filtered_items
        order_responses.append(OrderResponse.model_validate(order_dict))

    return OrderListResponse(
        orders=order_responses,
        total=total,
        page=page,
        size=size,
    )


@router.get(
    "/sales/{order_id}",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a specific order for seller",
)
def get_seller_order(
    order_id: UUID,
    db: DbSession,
    user_id: CurrentUserId,
) -> OrderResponse:
    """Retrieve a single order by ID, showing only items sold by the user."""
    order = order_service.get_order_for_seller(
        db,
        order_id=order_id,
        seller_id=user_id,
    )
        
    seller_items = [item for item in order.items if item.seller_id == user_id]
    seller_history = order_service.get_status_history(
        db,
        order_id=order_id,
        seller_id=user_id,
    )

    history_by_item_id: dict[UUID, list] = {}
    for event in seller_history:
        if event.item_id is None:
            continue
        history_by_item_id.setdefault(event.item_id, []).append(event)

    seller_item_dicts = []
    for item in seller_items:
        item_dict = item.__dict__.copy()
        item_dict["status_history"] = history_by_item_id.get(item.id, [])
        seller_item_dicts.append(item_dict)
        
    order_dict = order.__dict__.copy()
    order_dict["order_id"] = order.id
    order_dict["items"] = seller_item_dicts
    order_dict["status_history"] = seller_history
    order_dict["total_amount"] = sum(item.unit_price * item.quantity for item in seller_items)
    return OrderResponse.model_validate(order_dict)


@router.patch(
    "/sales/{order_id}/items/{item_id}/status",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Update item logistic status (Seller)",
)
def update_item_status(
    order_id: UUID,
    item_id: UUID,
    body: UpdateItemStatusRequest,
    db: DbSession,
    user_id: CurrentUserId,
) -> OrderResponse:
    """Update the logistic status of an order item."""
    order = order_service.get_order_for_seller(
        db,
        order_id=order_id,
        seller_id=user_id,
    )
        
    item = next((i for i in order.items if i.id == item_id), None)
    if item is None or item.seller_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Item not found or not owned by seller",
        )
        
    order_service.update_item_status(
        db,
        item_id=item_id,
        new_status=body.status,
        tracking_code=body.tracking_code,
    )
        
    order = order_service.get_order_for_seller(
        db,
        order_id=order_id,
        seller_id=user_id,
    )
        
    order_dict = order.__dict__.copy()
    order_dict["order_id"] = order.id
    order_dict["items"] = [i for i in order.items if i.seller_id == user_id]
        
    logger.debug(
        "Item status updated: order=%s item=%s new_status=%s seller=%s",
        order_id, item_id, body.status, user_id,
    )
    return OrderResponse.model_validate(order_dict)


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
    """Retrieve a single order by ID."""
    order = order_service.get_order(
        db,
        order_id=order_id,
        user_id=user_id,
    )

    full_history = order_service.get_status_history(
        db,
        order_id=order_id,
    )
    history_by_item_id: dict[UUID, list] = {}
    for event in full_history:
        if event.item_id is None:
            continue
        history_by_item_id.setdefault(event.item_id, []).append(event)

    item_dicts = []
    for item in order.items:
        item_dict = item.__dict__.copy()
        item_dict["status_history"] = history_by_item_id.get(item.id, [])
        item_dicts.append(item_dict)
        
    order_dict = order.__dict__.copy()
    order_dict["order_id"] = order.id
    order_dict["items"] = item_dicts
    order_dict["status_history"] = full_history
    return OrderResponse.model_validate(order_dict)


@router.post(
    "/sales/{order_id}/items/{item_id}/cancel",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel a single item as the seller",
)
def cancel_order_item_as_seller(
    order_id: UUID,
    item_id: UUID,
    db: DbSession,
    user_id: CurrentUserId,
) -> OrderResponse:
    order = order_service.cancel_order_item(
        db,
        order_id=order_id,
        item_id=item_id,
        seller_id=user_id,
        by="seller",
    )
    return _serialize_order(order_service, db, order)


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
) -> OrderResponse:
    """Update the status of an order."""
    order = order_service.update_order_status(
        db,
        order_id=order_id,
        new_status=body.status,
        tracking_code=body.tracking_code,
    )
    return _serialize_order(order_service, db, order)


@router.post(
    "/{order_id}/refund-webhook",
    status_code=status.HTTP_200_OK,
    summary="Callback from Payments service when a refund is processed",
)
def refund_webhook(
    order_id: UUID,
    body: RefundWebhookRequest,
    db: DbSession,
) -> dict:
    """Callback from the Payments service to update item status after a refund."""
    order_service.resolve_refund(
        db,
        order_id=order_id,
        order_item_id=body.order_item_id,
        refund_status=body.refund_status,
    )
    return {"order_id": str(order_id), "order_item_id": body.order_item_id, "refund_status": body.refund_status}


def _serialize_order(service, db: DbSession, order) -> OrderResponse:
    full_history = service.get_status_history(db, order_id=order.id)
    history_by_item_id: dict[UUID, list] = {}
    for event in full_history:
        if event.item_id is None:
            continue
        history_by_item_id.setdefault(event.item_id, []).append(event)

    item_dicts = []
    for item in order.items:
        item_dict = item.__dict__.copy()
        item_dict["status_history"] = history_by_item_id.get(item.id, [])
        item_dicts.append(item_dict)

    order_dict = order.__dict__.copy()
    order_dict["order_id"] = order.id
    order_dict["items"] = item_dicts
    order_dict["status_history"] = full_history
    return OrderResponse.model_validate(order_dict)