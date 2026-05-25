"""
Health-check endpoints.

**/livez** and **/readyz** have **completely separate logic** as required
by the observability specification:

- ``/livez``  → verifies the HTTP process is alive (no external calls).
- ``/readyz`` → verifies critical dependencies (database and sibling
  microservices) are reachable.
"""

import logging

import httpx
from fastapi import APIRouter, Response, status

from src.core.config import get_settings
from src.core.database import check_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])

_READYZ_TIMEOUT = 3.0


def _check_readyz_endpoint(service_name: str, base_url: str) -> bool:
    """Check that a sibling microservice exposes a healthy ``/readyz`` endpoint."""
    try:
        response = httpx.get(f"{base_url}/readyz", timeout=_READYZ_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.error("%s readiness check failed: %s", service_name, exc)
        return False

    if response.status_code != 200:
        logger.error(
            "%s readiness check failed: status=%d body=%s",
            service_name,
            response.status_code,
            response.text,
        )
        return False

    return True


@router.get(
    "/livez",
    status_code=status.HTTP_200_OK,
    summary="Liveness probe",
    description="Returns HTTP 200 when the process is alive. No external checks.",
)
async def livez() -> dict[str, str]:
    """Liveness probe — always returns 200 if the server is up."""
    return {"status": "alive"}


@router.get(
    "/readyz",
    summary="Readiness probe",
    description=(
        "Attempts a lightweight database query. "
        "Returns HTTP 200 if successful, HTTP 503 otherwise."
    ),
)
async def readyz(response: Response) -> dict[str, str]:
    """Readiness probe — checks database connectivity and sibling services."""
    if check_db_connection():
        settings = get_settings()
        dependencies = (
            ("Cart service", settings.CART_SERVICE_URL),
            ("Inventory service", settings.INVENTORY_SERVICE_URL),
            ("Payments service", settings.PAYMENTS_SERVICE_URL),
        )

        for service_name, base_url in dependencies:
            if not _check_readyz_endpoint(service_name, base_url):
                response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
                return {"status": "unavailable"}

        return {"status": "ready"}

    logger.error("Readiness check failed: database unreachable")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "unavailable"}
