"""
HTTP client for the Inventory microservice.

Communicates via internal K8s DNS.  The base URL is configured
through ``Settings.INVENTORY_SERVICE_URL``.
"""

import logging
import uuid
from typing import Any

import httpx

from src.core.config import get_settings
from src.core.observability import build_observability_headers
from src.exceptions import ExternalServiceError, InsufficientStockError

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0  # seconds


def _base_url() -> str:
    return get_settings().INVENTORY_SERVICE_URL


async def get_stock_levels(product_ids: list[str]) -> dict[str, int]:
    """Fetch stock levels for a set of product IDs in one request."""
    if not product_ids:
        return {}

    url = f"{_base_url()}/internal/products/stocks"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={"product_ids": product_ids},
                headers=build_observability_headers(),
                timeout=_TIMEOUT,
            )
    except httpx.HTTPError as exc:
        logger.error("Inventory service unreachable on stock lookup: %s", exc)
        raise ExternalServiceError("inventory-service", "Service unreachable")

    if response.status_code != 200:
        logger.error(
            "Inventory stock lookup error: status=%d body=%s",
            response.status_code, response.text,
        )
        raise ExternalServiceError(
            "inventory-service",
            f"Stock lookup failed: status {response.status_code}",
        )

    payload = response.json().get("data", {})
    return {str(product_id): int(quantity) for product_id, quantity in payload.items()}


async def reserve_stock(order_id: uuid.UUID, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Request a stock reservation for the given items under order_id.
    
    If any reservation fails, it rolls back
    the previously successful reservations in this batch.

    Parameters
    ----------
    order_id:
        The pre-generated order ID.
    items:
        List of dicts with ``product_id`` and ``quantity``.

    Raises
    ------
    InsufficientStockError
        If Inventory returns 409 Conflict (not enough stock).
    ExternalServiceError
        If the service is unreachable or returns an unexpected error.
    """
    url = f"{_base_url()}/internal/products/stock/reserve"
    payload = [
        {
            "product_id": str(item["product_id"]),
            "quantity": item["quantity"],
            "order_id": str(order_id),
            "seller_id": item.get("seller_id"),
            "product_name": item.get("product_name"),
        }
        for item in items
    ]
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                headers=build_observability_headers(),
                timeout=_TIMEOUT,
            )
    except httpx.HTTPError as exc:
        logger.error("Inventory service unreachable on reserve: %s", exc)
        raise ExternalServiceError("inventory-service", "Service unreachable")

    if response.status_code == 409:
        try:
            detail = response.json().get("detail", "Insufficient stock")
        except Exception:
            detail = "Insufficient stock"
        raise InsufficientStockError(detail)

    if response.status_code not in (200, 201):
        logger.error(
            "Inventory reserve error: status=%d body=%s",
            response.status_code, response.text,
        )
        raise ExternalServiceError(
            "inventory-service",
            f"Reserve failed: status {response.status_code}",
        )
    return response.json()


async def commit_stock(order_id: uuid.UUID) -> dict[str, Any]:
    """Convert a reservation into a definitive stock deduction.

    Parameters
    ----------
    order_id:
        The pre-generated order ID.

    Raises
    ------
    ExternalServiceError
        If the service is unreachable or returns an unexpected error.
    """
    url = f"{_base_url()}/internal/products/stock/commit"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={"order_id": str(order_id)},
                headers=build_observability_headers(),
                timeout=_TIMEOUT,
            )
    except httpx.HTTPError as exc:
        logger.error("Inventory service unreachable on commit: %s", exc)
        raise ExternalServiceError("inventory-service", "Service unreachable")

    if response.status_code == 404:
        logger.warning("Reservation not found for order_id=%s (already committed or expired)", order_id)
        return {}

    if response.status_code != 200:
        logger.error(
            "Inventory commit error: status=%d body=%s",
            response.status_code, response.text,
        )
        raise ExternalServiceError(
            "inventory-service",
            f"Commit failed: status {response.status_code}",
        )
    return response.json()


async def release_stock(order_id: uuid.UUID) -> dict[str, Any]:
    """Release previously reserved stock back to availability.

    Parameters
    ----------
    order_id:
        The pre-generated order ID.
        
    Raises
    ------
    ExternalServiceError
        If the service is unreachable or returns an error.
    """
    url = f"{_base_url()}/internal/products/stock/release"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={"order_id": str(order_id)},
                headers=build_observability_headers(),
                timeout=_TIMEOUT,
            )
    except httpx.HTTPError as exc:
        logger.error("Inventory service unreachable on release: %s", exc)
        raise ExternalServiceError("inventory-service", "Service unreachable")

    if response.status_code not in (200, 204):
        logger.error(
            "Inventory release error: status=%d body=%s",
            response.status_code, response.text,
        )
        raise ExternalServiceError(
            "inventory-service",
            f"Release failed: status {response.status_code}",
        )
    return response.json() if response.status_code == 200 else {}


async def release_stock_for_item(order_id: uuid.UUID, product_id: uuid.UUID) -> None:
    url = f"{_base_url()}/internal/products/{str(product_id)}/stock/release"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={"order_id": str(order_id)},
                headers=build_observability_headers(),
                timeout=_TIMEOUT,
            )
    except httpx.HTTPError as exc:
        logger.error("Inventory service unreachable on release: %s", exc)
        raise ExternalServiceError("inventory-service", "Service unreachable")

    if response.status_code not in (200, 204):
        logger.error(
            "Inventory release error: status=%d body=%s",
            response.status_code, response.text,
        )
        raise ExternalServiceError(
            "inventory-service",
            f"Release failed: status {response.status_code}",
        )

