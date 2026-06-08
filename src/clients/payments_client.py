"""
HTTP client for the Payments microservice.

Communicates via internal K8s DNS.  The base URL is configured
through ``Settings.PAYMENTS_SERVICE_URL``.

Implements the single operation required by the Saga orchestrator:
``POST /payments/preferences`` — creates a MercadoPago preference
and returns the ``init_point`` URL to redirect the user to MP Checkout.

Contract source: ``.agents/payments_openapi.json``
"""

import logging
from typing import Any

import httpx

from src.core.config import get_settings
from src.core.observability import build_observability_headers
from src.exceptions import PaymentsServiceError

logger = logging.getLogger(__name__)

# Slightly higher than Cart/Inventory because MP's API can be slower.
_TIMEOUT = 15.0  # seconds


def _base_url() -> str:
    return get_settings().PAYMENTS_SERVICE_URL


def create_preference(
    *,
    order_id: str,
    items: list[dict[str, Any]],
    idempotency_key: str,
) -> dict[str, str]:
    """Create a MercadoPago payment preference via the Payments microservice.

    Sends ``POST /payments/preferences`` with the ``Idempotency-Key`` header
    and the order details.  Returns the preference data needed by the frontend
    to redirect the user to the MP Checkout flow.

    Parameters
    ----------
    order_id:
        UUID string of the order created by the Saga orchestrator.
    items:
        List of line items to charge.  Each dict must contain:
        ``title`` (str), ``quantity`` (int), ``unit_price`` (float).
    idempotency_key:
        The same UUID the mobile app generated for this checkout attempt.
        Forwarded as the ``Idempotency-Key`` header so the Payments service
        can deduplicate requests on its own side.

    Returns
    -------
    dict
        ``{"preference_id": str, "init_point": str}`` as defined in
        ``PreferenceResponse`` of the Payments OpenAPI contract.

    Raises
    ------
    PaymentsServiceError
        If the Payments service is unreachable, times out, or returns a
        non-201 status (400, 422, 502, or any unexpected code).
    """
    url = f"{_base_url()}/payments/preferences"
    payload = {"order_id": order_id, "items": items}
    headers = {"Idempotency-Key": idempotency_key, **build_observability_headers()}

    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.error("Payments service unreachable: %s", exc)
        raise PaymentsServiceError("Payments service unreachable") from exc

    if response.status_code == 201:
        data = response.json()
        logger.info(
            "Preference created: order=%s preference_id=%s",
            order_id, data.get("preference_id"),
        )
        return data

    # All non-201 responses are treated as failures — the Saga will compensate.
    try:
        detail = response.json().get("detail", f"Unexpected status {response.status_code}")
    except Exception:
        detail = f"Unexpected status {response.status_code}"

    logger.error(
        "Payments service error: status=%d order=%s detail=%s",
        response.status_code, order_id, detail,
    )
    raise PaymentsServiceError(detail)


def request_refund(
    *,
    order_id: str,
    order_item_id: str,
) -> dict[str, str]:
    """Request a refund for a single order item via the Payments microservice.

    Sends ``POST /payments/{order_id}/refund`` with the item ID.
    Returns the refund response data from the Payments service.

    Parameters
    ----------
    order_id:
        UUID string of the order.
    order_item_id:
        UUID string of the order item (line_item_id) to refund.

    Returns
    -------
    dict
        ``{"refund_id": str, "order_id": str, "order_item_id": str, ...}``
        as defined in ``RefundResponse`` of the Payments OpenAPI contract.

    Raises
    ------
    PaymentsServiceError
        If the Payments service is unreachable, times out, or returns a
        non-200 status.
    """
    url = f"{_base_url()}/payments/{order_id}/refund"
    payload = {"order_item_id": order_item_id}

    try:
        response = httpx.post(
            url,
            json=payload,
            headers=build_observability_headers(),
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.error("Payments service unreachable for refund: order=%s item=%s exc=%s", order_id, order_item_id, exc)
        raise PaymentsServiceError("Payments service unreachable") from exc

    if response.status_code == 200:
        data = response.json()
        logger.info(
            "Refund requested: order=%s item=%s refund_id=%s",
            order_id, order_item_id, data.get("refund_id"),
        )
        return data

    # Non-200 responses are treated as failures.
    try:
        detail = response.json().get("detail", f"Unexpected status {response.status_code}")
    except Exception:
        detail = f"Unexpected status {response.status_code}"

    logger.error(
        "Payments refund error: status=%d order=%s item=%s detail=%s",
        response.status_code, order_id, order_item_id, detail,
    )
    raise PaymentsServiceError(detail)

def cancel_pending_payment(order_id: str) -> None:
    """Cancel a pending payment via the Payments microservice."""
    url = f"{_base_url()}/payments/{order_id}/cancel"

    try:
        response = httpx.post(
            url,
            headers=build_observability_headers(),
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.error("Payments service unreachable for cancellation: order=%s exc=%s", order_id, exc)
        raise PaymentsServiceError("Payments service unreachable") from exc

    if response.status_code != 200:
        try:
            detail = response.json().get("detail", f"Unexpected status {response.status_code}")
        except Exception:
            detail = f"Unexpected status {response.status_code}"

        logger.error(
            "Payments cancellation error: status=%d order=%s detail=%s",
            response.status_code, order_id, detail,
        )
        raise PaymentsServiceError(detail)

    logger.info(
        "Pending payment cancelled: order=%s",
        order_id,
    )