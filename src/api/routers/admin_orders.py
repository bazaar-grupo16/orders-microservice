"""Admin orders API router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.dependencies.admin_orders import (
    get_admin_list_orders_query_params,
    require_admin_role,
)
from src.core.dependencies import DbSession
from src.schemas.admin_orders import AdminListOrdersQueryParams
from src.schemas.order import OrderListResponse, OrderResponse
from src.services import order_service

router = APIRouter(
    prefix="orders/admin/orders",
    tags=["Admin Orders"],
    dependencies=[Depends(require_admin_role)],
)


@router.get(
    "",
    response_model=OrderListResponse,
    status_code=status.HTTP_200_OK,
    summary="Listar todas las órdenes (Administrador)",
)
def list_all_orders_admin(
    db: DbSession,
    params: Annotated[AdminListOrdersQueryParams, Depends(get_admin_list_orders_query_params)],
) -> OrderListResponse:
    status_filter = (
        params.order_status.value if params.order_status is not None else None
    )
    orders, total = order_service.list_all_orders(
        db,
        status_filter=status_filter,
        page=params.page,
        size=params.size,
    )

    order_responses = []
    for order in orders:
        order_dict = order.__dict__.copy()
        order_dict["order_id"] = order.id
        order_responses.append(OrderResponse.model_validate(order_dict))

    return OrderListResponse(
        orders=order_responses,
        total=total,
        page=params.page,
        size=params.size,
    )


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Obtener el detalle de cualquier orden",
)
def get_order_detail_admin(
    order_id: UUID,
    db: DbSession,
) -> OrderResponse:
    order = order_service.get_order_as_admin(db, order_id=order_id)

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
