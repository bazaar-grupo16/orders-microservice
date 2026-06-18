"""Background worker and helper utilities for processing transactional outbox events."""

import asyncio
import logging
import uuid


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.clients import cart_client, inventory_client, payments_client
from src.core.config import get_settings
from src.core.database import SessionLocal
from src.repositories.outbox_repository import OutboxRepository
from src.models.outbox_event import OutboxEvent, OutboxStatus

logger = logging.getLogger(__name__)


async def handle_release_stock(payload: dict) -> None:
    """Handle the RELEASE_STOCK outbox event action.
    
    Releases the reserved stock (order or item).
    """
    order_id = payload.get("order_id")
    product_id = payload.get("product_id")

    if product_id:
        await inventory_client.release_stock_for_item(
            uuid.UUID(order_id),
            uuid.UUID(product_id),
        )
    else:
        await inventory_client.release_stock(uuid.UUID(order_id))


async def handle_payment_success(db: AsyncSession, payload: dict) -> None:
    """Handle the PAYMENT_SUCCESS outbox event action.
    
    Updates order status, commits stock, and deletes the buyer's cart.
    """
    order_id = payload.get("order_id")
    user_id = payload.get("user_id")
    status = payload.get("status")

    if status:
        from src.repositories import order_repository
        order = await order_repository.get_by_id(db, uuid.UUID(order_id))
        if order and order.status != status:
            from src.services.order_service.update import update_order_status
            await update_order_status(db, order_id=order.id, new_status=status, origin="webhook")

    await inventory_client.commit_stock(uuid.UUID(order_id))

    if user_id:
        await cart_client.delete_cart(str(user_id))


async def handle_payment_failed(db: AsyncSession, payload: dict) -> None:
    """Handle the PAYMENT_FAILED outbox event action.
    
    Updates order status, then releases the reserved stock for the order.
    """
    order_id = payload.get("order_id")
    status = payload.get("status")

    if status:
        from src.repositories import order_repository
        order = await order_repository.get_by_id(db, uuid.UUID(order_id))
        if order and order.status != status:
            from src.services.order_service.update import update_order_status
            await update_order_status(db, order_id=order.id, new_status=status, origin="webhook")

    await inventory_client.release_stock(uuid.UUID(order_id))


async def handle_cancel_payment(payload: dict) -> None:
    """Handle the CANCEL_PAYMENT outbox event action."""
    order_id = payload.get("order_id")
    await payments_client.cancel_pending_payment(str(order_id))


def start_outbox_worker() -> asyncio.Task | None:
    """Start the outbox worker task if enabled by configuration."""
    settings = get_settings()
    if not settings.OUTBOX_WORKER_ENABLED:
        logger.info("Transactional outbox worker is disabled")
        return None

    return asyncio.create_task(_run_outbox_loop())


async def stop_outbox_worker(task: asyncio.Task | None) -> None:
    """Cancel the worker task and wait until it completes."""
    if task is None:
        return

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        logger.info("Transactional outbox worker stopped")


async def _run_outbox_loop() -> None:
    settings = get_settings()
    interval_seconds = settings.OUTBOX_WORKER_INTERVAL_SECONDS
    batch_size = settings.OUTBOX_WORKER_BATCH_SIZE
    max_attempts = settings.OUTBOX_MAX_ATTEMPTS

    logger.info(
        "Transactional outbox worker started: interval=%ds, batch_size=%d, max_attempts=%d",
        interval_seconds,
        batch_size,
        max_attempts,
    )

    while True:
        try:
            # 1. Claim batch of pending outbox events (sets status=PROCESSING and commits)
            async with SessionLocal() as db:
                events = await OutboxRepository.claim_batch(db, batch_size)

            # 2. Process each claimed event in its own locked transaction
            for event in events:
                await _process_event(event.id, max_attempts)

        except Exception as exc:
            logger.error("Transactional outbox worker iteration failed: %s", exc)

        await asyncio.sleep(interval_seconds)


async def _process_event(event_id: uuid.UUID, max_attempts: int) -> None:
    """Lock a single event, dispatch the compensating HTTP request, and update its status."""
    async with SessionLocal() as db:
        try:
            # Acquire row lock FOR UPDATE inside a transaction
            event = await OutboxRepository.lock_event(db, event_id)
            if not event:
                return

            # Double-check status under lock to prevent concurrent double-processing
            if event.status != OutboxStatus.PROCESSING.value:
                return

            logger.info("Processing outbox event: id=%s type=%s", event.id, event.event_type)

            # Dispatch HTTP call based on event_type.
            if event.event_type == "RELEASE_STOCK":
                await handle_release_stock(event.payload)
            elif event.event_type == "PAYMENT_SUCCESS":
                await handle_payment_success(db, event.payload)
            elif event.event_type == "PAYMENT_FAILED":
                await handle_payment_failed(db, event.payload)
            elif event.event_type == "CANCEL_PAYMENT":
                await handle_cancel_payment(event.payload)
            else:
                logger.error("Unknown outbox event type: %s", event.event_type)
                # Treat unknown events as permanently failed
                await OutboxRepository.mark_failed(db, event_id, max_attempts=1)
                return

            # On HTTP success (no exception raised): mark event as DONE
            await OutboxRepository.mark_done(db, event_id)
            logger.info("Outbox event processed successfully: id=%s", event_id)

        except Exception as exc:
            logger.error("Failed to process outbox event id=%s: %s", event_id, exc)
            # On error: increment attempts, reset status to PENDING or set status to FAILED
            try:
                await OutboxRepository.mark_failed(db, event_id, max_attempts)
            except Exception as rollback_exc:
                logger.error("Failed to mark outbox event as failed: %s", rollback_exc)


async def process_order_outbox_synchronously(db: AsyncSession, order_id: uuid.UUID) -> None:
    """Best-effort immediate execution of pending outbox events for an order.
    
    Used to satisfy instant integration test expectations and provide immediate
    dispatch in nominal success cases.
    """
    settings = get_settings()
    max_attempts = settings.OUTBOX_MAX_ATTEMPTS
    order_id_str = str(order_id)

    # Fetch pending events
    stmt = select(OutboxEvent).where(OutboxEvent.status == OutboxStatus.PENDING.value)
    result = await db.execute(stmt)
    events = result.scalars().all()
    events_to_process = [e for e in events if e.payload.get("order_id") == order_id_str]

    for event in events_to_process:
        try:
            # Lock the event row inside a transaction
            locked_event = await OutboxRepository.lock_event(db, event.id)
            if not locked_event or locked_event.status != OutboxStatus.PENDING.value:
                continue

            # Mark as PROCESSING and commit to release lock and hide from other workers
            locked_event.status = OutboxStatus.PROCESSING.value
            await db.commit()

            # Dispatch synchronous client call -> now async!
            if locked_event.event_type == "RELEASE_STOCK":
                await handle_release_stock(locked_event.payload)
            elif locked_event.event_type == "PAYMENT_SUCCESS":
                await handle_payment_success(db, locked_event.payload)
            elif locked_event.event_type == "PAYMENT_FAILED":
                await handle_payment_failed(db, locked_event.payload)
            elif locked_event.event_type == "CANCEL_PAYMENT":
                await handle_cancel_payment(locked_event.payload)
            else:
                await OutboxRepository.mark_failed(db, locked_event.id, max_attempts=1)
                continue

            await OutboxRepository.mark_done(db, locked_event.id)

        except Exception as exc:
            logger.warning(
                "Inline processing failed for outbox event %s: %s",
                event.id,
                exc,
            )
            try:
                await OutboxRepository.mark_failed(db, event.id, max_attempts)
            except Exception as mark_exc:
                logger.error("Failed to mark outbox event as failed: %s", mark_exc)

