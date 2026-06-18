"""Background worker for pending-payment order expiration."""

import asyncio
import logging

from src.core.config import get_settings
from src.core.database import SessionLocal
from src.services.order_service.expiration import expire_pending_payment_orders


logger = logging.getLogger(__name__)


def start_pending_payment_expiration_worker() -> asyncio.Task | None:
    """Start the expiration worker when enabled by configuration."""
    settings = get_settings()
    if not settings.PENDING_PAYMENT_EXPIRATION_ENABLED:
        logger.info("Pending payment expiration worker is disabled")
        return None

    return asyncio.create_task(_run_pending_payment_expiration_loop())


async def stop_pending_payment_expiration_worker(task: asyncio.Task | None) -> None:
    """Cancel the worker and wait until it finishes."""
    if task is None:
        return

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        logger.info("Pending payment expiration worker stopped")


async def _run_pending_payment_expiration_loop() -> None:
    settings = get_settings()
    interval_seconds = settings.PENDING_PAYMENT_EXPIRATION_INTERVAL_SECONDS
    logger.info(
        "Pending payment expiration worker started: every %ss",
        interval_seconds,
    )

    while True:
        try:
            async with SessionLocal() as db:
                await expire_pending_payment_orders(db)
        except Exception as exc:
            logger.error("Pending payment expiration worker failed: %s", exc)

        await asyncio.sleep(interval_seconds)

