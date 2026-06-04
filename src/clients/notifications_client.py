"""Best-effort HTTP client for the Notifications microservice."""

import logging
from uuid import UUID

import httpx

from src.core.config import get_settings
from src.core.observability import build_observability_headers

logger = logging.getLogger(__name__)

_TIMEOUT = 3.0


def _base_url() -> str:
    """Return the configured notifications service URL."""
    return get_settings().NOTIFICATIONS_SERVICE_URL.rstrip("/")


def notify_order_status_changed(
    *,
    order_id: UUID,
    buyer_id: UUID,
    new_status: str,
    tracking_code: str | None = None,
) -> None:
    """Notify an order status change without breaking the main flow."""
    base_url = _base_url()
    if not base_url:
        return

    payload = {
        "order_id": str(order_id),
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
