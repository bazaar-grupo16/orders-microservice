"""FastAPI dependencies for admin order endpoints."""

from typing import Annotated, TypeVar

from fastapi import Header, HTTPException, Query, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from src.models.order_status import OrderStatus
from src.schemas.admin_orders import AdminListOrdersQueryParams

T = TypeVar("T", bound=BaseModel)


def _parse_query_params(model: type[T], **values: object) -> T:
    """Parse query params into a Pydantic model and return HTTP 422 on failure."""
    try:
        return model(**values)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def require_admin_role(
    x_is_admin: Annotated[bool, Header(alias="X-Is-Admin")],
) -> None:
    """Ensure the caller has administrator privileges."""
    if not x_is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden - El usuario no tiene permisos de administrador",
        )


def get_admin_list_orders_query_params(
    page: Annotated[int, Query(description="Número de página")] = 1,
    size: Annotated[int, Query(description="Tamaño de página")] = 20,
    order_status: Annotated[
        OrderStatus | None,
        Query(description="Filtrar por estado de la orden"),
    ] = None,
) -> AdminListOrdersQueryParams:
    return _parse_query_params(
        AdminListOrdersQueryParams,
        page=page,
        size=size,
        order_status=order_status,
    )
