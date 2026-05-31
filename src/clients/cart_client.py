"""
HTTP client for the Cart microservice.

Communicates via internal K8s DNS.  The base URL is configured
through ``Settings.CART_SERVICE_URL``.

The Cart service returns camelCase JSON with nested structures.
This client normalises the response into snake_case dicts for
internal consumption.
"""

import logging
from decimal import Decimal
from typing import Any

import httpx

from src.core.config import get_settings
from src.exceptions import CartEmptyError, ExternalServiceError

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0  # seconds


def _base_url() -> str:
    return get_settings().CART_SERVICE_URL


def _normalise_cart_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a camelCase cart item into the snake_case format
    used internally by the Orders service.

    Cart response shape per item::

        {
            "productId": "uuid",
            "title": "Product Name",
            "category": "Electronics",
            "unitPrice": {"value": "350.00", "updatedAt": "..."},
            "quantity": 1,
            "stock": 4,
            "status": "active",
            "addedAt": "..."
        }
    """
    return {
        "product_id": raw["productId"],
        "product_name": raw["title"],
        "category": raw.get("category", "Uncategorized"),
        "seller_id": raw.get("sellerId"),  # Optional field
        "quantity": raw["quantity"],
        "unit_price": float(Decimal(raw["unitPrice"]["value"])),
    }


def get_cart(user_id: str) -> list[dict[str, Any]]:
    """Fetch the user's cart items (normalised to snake_case).

    Parameters
    ----------
    user_id:
        UUID string of the cart owner.

    Returns
    -------
    list[dict]
        List of normalised cart items with ``product_id``,
        ``product_name``, ``category``, ``quantity``, and ``unit_price`` (float).

    Raises
    ------
    CartEmptyError
        If the cart has no items.
    ExternalServiceError
        If the Cart service is unreachable or returns an unexpected error.
    """
    user_id = str(user_id)
    url = f"{_base_url()}/cart/{user_id}"
    try:
        response = httpx.get(url, timeout=_TIMEOUT, headers={"X-User-ID": user_id})
    except httpx.HTTPError as exc:
        logger.error("Cart service unreachable: %s", exc)
        raise ExternalServiceError("cart-service", "Service unreachable")

    if response.status_code == 404:
        raise CartEmptyError()

    if response.status_code != 200:
        logger.error(
            "Cart service error: status=%d body=%s",
            response.status_code, response.text,
        )
        raise ExternalServiceError(
            "cart-service",
            f"Unexpected status {response.status_code}",
        )

    data = response.json().get("data", {})
    raw_items = data.get("items", [])
    if not raw_items:
        raise CartEmptyError()

    return [_normalise_cart_item(item) for item in raw_items]


def delete_cart(user_id: str) -> None:
    """Clear the user's cart after a successful checkout.

    Raises
    ------
    ExternalServiceError
        If the Cart service is unreachable or returns an error.
    """
    user_id = str(user_id)
    url = f"{_base_url()}/cart/{user_id}"
    try:
        response = httpx.delete(url, timeout=_TIMEOUT, headers={"X-User-ID": user_id})
    except httpx.HTTPError as exc:
        logger.error("Cart service unreachable on delete: %s", exc)
        raise ExternalServiceError("cart-service", "Service unreachable")

    if response.status_code not in (200, 204):
        logger.error(
            "Cart delete failed: status=%d body=%s",
            response.status_code, response.text,
        )
        raise ExternalServiceError(
            "cart-service",
            f"Failed to clear cart: status {response.status_code}",
        )
