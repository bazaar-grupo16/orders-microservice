"""Best-effort HTTP client for the Notifications microservice."""

import logging
from uuid import UUID

import httpx

from src.core.config import get_settings
from src.core.observability import build_observability_headers
from src.models.order_item import OrderItem

logger = logging.getLogger(__name__)

_TIMEOUT = 3.0


def _base_url() -> str:
    """Return the configured notifications service URL."""
    return get_settings().NOTIFICATIONS_SERVICE_URL.rstrip("/")


def notify_order_status_changed(
    *,
    order_id: UUID,
    order_items: list[OrderItem],
    buyer_id: UUID,
    new_status: str,
    tracking_code: str | None = None,
) -> None:
    """Notify an order status change without breaking the main flow."""
    base_url = _base_url()
    if not base_url:
        return

    identifier = order_items[0].product_name
    if len(order_items) == 2:
        identifier = f"{order_items[0].product_name} y {order_items[1].product_name}"
    elif len(order_items) > 1:
        identifier += f" +{len(order_items) - 1} items"

    payload = {
        "order_id": identifier,
        "buyer_id": str(buyer_id),
        "new_status": new_status,
    }
    if tracking_code is not None:
        payload["tracking_code"] = tracking_code

    try:
        response = httpx.post(
            f"{base_url}/notifications/order-status-changed",
            json=payload,
            headers=build_observability_headers(),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning(
            "Notifications service failed for order status change: "
            "order=%s status=%s exc=%s",
            order_id,
            new_status,
            exc,
        )

def notify_order_item_status_changed(
    item: OrderItem,
    new_status: str,
    buyer_id: UUID | None = None,
    tracking_code: str | None = None,
) -> None:
    """Notify an order item status change without breaking the main flow."""
    base_url = _base_url()
    if not base_url:
        return

    payload = {
        "order_id": str(item.order_id),
        "item": str(item.product_name),
        "buyer_id": str(buyer_id),
        "new_status": new_status,
    }
    if tracking_code is not None:
        payload["tracking_code"] = tracking_code
    
    try:
        response = httpx.post(
            f"{base_url}/notifications/order-item-status-changed",
            json=payload,
            headers=build_observability_headers(),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning(
            "Notifications service failed for order item status change: "
            "order=%s status=%s exc=%s",
            item.order_id,
            new_status,
            exc,
        )