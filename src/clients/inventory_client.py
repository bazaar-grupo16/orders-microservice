"""
HTTP client for the Inventory microservice.

Communicates via internal K8s DNS.  The base URL is configured
through ``Settings.INVENTORY_SERVICE_URL``.
"""

import logging
from typing import Any

import httpx

from src.core.config import get_settings
from src.exceptions import ExternalServiceError, InsufficientStockError

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0  # seconds


def _base_url() -> str:
    return get_settings().INVENTORY_SERVICE_URL


def reserve_stock(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Request a stock reservation for the given items.

    Parameters
    ----------
    items:
        List of dicts with ``product_id`` and ``quantity``.

    Returns
    -------
    dict
        The reservation response from Inventory (includes ``reservation_id``).

    Raises
    ------
    InsufficientStockError
        If Inventory returns 409 Conflict (not enough stock).
    ExternalServiceError
        If the service is unreachable or returns an unexpected error.
    """
    url = f"{_base_url()}/inventory/reserve"
    payload = {"items": items}

    try:
        response = httpx.post(url, json=payload, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.error("Inventory service unreachable on reserve: %s", exc)
        raise ExternalServiceError("inventory-service", "Service unreachable")

    if response.status_code == 409:
        detail = response.json().get("detail", "Insufficient stock")
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


def commit_stock(reservation_id: str) -> None:
    """Convert a reservation into a definitive stock deduction.

    Raises
    ------
    ExternalServiceError
        If the service is unreachable or returns an error.
    """
    url = f"{_base_url()}/inventory/commit"
    try:
        response = httpx.post(
            url, json={"reservation_id": reservation_id}, timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.error("Inventory service unreachable on commit: %s", exc)
        raise ExternalServiceError("inventory-service", "Service unreachable")

    if response.status_code not in (200, 204):
        logger.error(
            "Inventory commit error: status=%d body=%s",
            response.status_code, response.text,
        )
        raise ExternalServiceError(
            "inventory-service",
            f"Commit failed: status {response.status_code}",
        )


def rollback_stock(reservation_id: str) -> None:
    """Release a previously reserved stock back to availability.

    Raises
    ------
    ExternalServiceError
        If the service is unreachable or returns an error.
    """
    url = f"{_base_url()}/inventory/rollback"
    try:
        response = httpx.post(
            url, json={"reservation_id": reservation_id}, timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.error("Inventory service unreachable on rollback: %s", exc)
        raise ExternalServiceError("inventory-service", "Service unreachable")

    if response.status_code not in (200, 204):
        logger.error(
            "Inventory rollback error: status=%d body=%s",
            response.status_code, response.text,
        )
        raise ExternalServiceError(
            "inventory-service",
            f"Rollback failed: status {response.status_code}",
        )
