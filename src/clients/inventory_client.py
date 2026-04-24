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
    reserved_items = []
    
    for item in items:
        product_id = item["product_id"]
        quantity = item["quantity"]
        url = f"{_base_url()}/internal/products/{product_id}/stock/reserve"
        
        try:
            response = httpx.post(url, json={"quantity": quantity}, timeout=_TIMEOUT)
        except httpx.HTTPError as exc:
            logger.error("Inventory service unreachable on reserve: %s", exc)
            rollback_stock(reserved_items)
            raise ExternalServiceError("inventory-service", "Service unreachable")

        if response.status_code == 409:
            rollback_stock(reserved_items)
            # Default to a generic detail if none provided
            try:
                detail = response.json().get("detail", "Insufficient stock")
            except Exception:
                detail = "Insufficient stock"
            raise InsufficientStockError(detail)

        if response.status_code not in (200, 201):
            rollback_stock(reserved_items)
            logger.error(
                "Inventory reserve error: status=%d body=%s",
                response.status_code, response.text,
            )
            raise ExternalServiceError(
                "inventory-service",
                f"Reserve failed: status {response.status_code}",
            )

        reserved_items.append({"product_id": product_id, "quantity": quantity})


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
    for item in items:
        product_id = item["product_id"]
        quantity = item["quantity"]
        url = f"{_base_url()}/internal/products/{product_id}/stock/release"
        try:
            response = httpx.post(
                url, json={"quantity": quantity}, timeout=_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.error("Inventory service unreachable on rollback for %s: %s", product_id, exc)
            continue

        if response.status_code not in (200, 204):
            logger.error(
                "Inventory rollback error for %s: status=%d body=%s",
                product_id, response.status_code, response.text,
            )
