"""
Global exception handlers for FastAPI.

Translates domain exceptions into RFC 7807 ``application/problem+json``
responses.  Registered on the FastAPI application instance at startup.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.exceptions import (
    CartEmptyError,
    ExternalServiceError,
    InsufficientStockError,
    OrderAccessDeniedError,
    OrderNotFoundError,
    PaymentsServiceError,
    DuplicateRequest,
    InvalidCartItemError,
)
from src.models.state_machine import InvalidStateTransitionError

logger = logging.getLogger(__name__)

_PROBLEM_JSON = "application/problem+json"


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    """Build a Problem+JSON response."""
    return JSONResponse(
        status_code=status,
        content={
            "type": "about:blank",
            "title": title,
            "status": status,
            "detail": detail,
        },
        media_type=_PROBLEM_JSON,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach all domain-exception handlers to the FastAPI app."""

    @app.exception_handler(OrderNotFoundError)
    async def _order_not_found(
        _request: Request, exc: OrderNotFoundError,
    ) -> JSONResponse:
        return _problem(404, "Order Not Found", str(exc))

    @app.exception_handler(OrderAccessDeniedError)
    async def _order_access_denied(
        _request: Request, exc: OrderAccessDeniedError,
    ) -> JSONResponse:
        return _problem(403, "Access Denied", str(exc))

    @app.exception_handler(InvalidStateTransitionError)
    async def _invalid_transition(
        _request: Request, exc: InvalidStateTransitionError,
    ) -> JSONResponse:
        return _problem(409, "Invalid State Transition", str(exc))

    @app.exception_handler(InsufficientStockError)
    async def _insufficient_stock(
        _request: Request, exc: InsufficientStockError,
    ) -> JSONResponse:
        content = {
            "type": "about:blank",
            "title": "Insufficient Stock",
            "status": 409,
            "detail": exc.detail,
        }
        if exc.items:
            content["items"] = exc.items
        return JSONResponse(status_code=409, content=content, media_type=_PROBLEM_JSON)

    @app.exception_handler(CartEmptyError)
    async def _cart_empty(
        _request: Request, exc: CartEmptyError,
    ) -> JSONResponse:
        return _problem(400, "Cart Empty", str(exc))

    @app.exception_handler(ExternalServiceError)
    async def _external_service(
        _request: Request, exc: ExternalServiceError,
    ) -> JSONResponse:
        logger.error(
            "External service failure: service=%s detail=%s",
            exc.service, exc.detail,
        )
        return _problem(502, "External Service Error", exc.detail)

    @app.exception_handler(PaymentsServiceError)
    async def _payments_service(
        _request: Request, exc: PaymentsServiceError,
    ) -> JSONResponse:
        logger.error("Payments service failure: detail=%s", exc.detail)
        return _problem(
            502,
            "Payment Gateway Error",
            "The payment service is temporarily unavailable. Please try again.",
        )

    @app.exception_handler(DuplicateRequest)
    async def duplicate_request_handler(_request: Request, exc: DuplicateRequest) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "type": "about:blank",
                "title": "Duplicate Request",
                "status": 409,
                "detail": "An order with this idempotency key already exists.",
                "instance": "/orders",
                "order_id": str(exc.order_id),
            },
            media_type=_PROBLEM_JSON,
        )

    @app.exception_handler(InvalidCartItemError)
    async def _invalid_cart_item(
        _request: Request, exc: InvalidCartItemError,
    ) -> JSONResponse:
        return _problem(400, "Invalid Cart Item", str(exc))
