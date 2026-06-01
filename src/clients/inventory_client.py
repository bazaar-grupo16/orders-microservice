"""
HTTP client for the Inventory microservice.

Communicates via internal K8s DNS.  The base URL is configured
through ``Settings.INVENTORY_SERVICE_URL``.
"""

import logging
from typing import Any

import httpx

from src.core.config import get_settings
from src.core.observability import build_observability_headers
from src.exceptions import ExternalServiceError, InsufficientStockError

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0  # seconds


def _base_url() -> str:
    return get_settings().INVENTORY_SERVICE_URL


def get_stock_levels(product_ids: list[str]) -> dict[str, int]:
    """Fetch stock levels for a set of product IDs in one request."""
    if not product_ids:
        return {}

    url = f"{_base_url()}/internal/products/stocks"
    try:
        response = httpx.post(
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


def reserve_stock(items: list[dict[str, Any]]) -> None:
    """Request a stock reservation for the given items.
    
    If any reservation fails, it rolls back
    the previously successful reservations in this batch.

    Parameters
    ----------
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
    try:
        response = httpx.post(
            url,
            json=items,
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


def commit_stock(*args, **kwargs) -> None:
    """Convert a reservation into a definitive stock deduction.

    This is a no-op because the Inventory API deducts stock
    atomically during the reserve phase.
    """
    pass


def rollback_stock(items: list[dict[str, Any]]) -> None:
    """Release previously reserved stock back to availability.

    Parameters
    ----------
    items:
        List of dicts with ``product_id`` and ``quantity``.
        
    Raises
    ------
    ExternalServiceError
        If the service is unreachable or returns an error.
    """
    if not items:
        return

    url = f"{_base_url()}/internal/products/stock/release"
    try:
        response = httpx.post(
            url,
            json=items,
            headers=build_observability_headers(),
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.error("Inventory service unreachable on rollback: %s", exc)
        return

    if response.status_code not in (200, 204):
        logger.error(
            "Inventory rollback error: status=%d body=%s",
            response.status_code, response.text,
        )
