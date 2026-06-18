"""Repository layer for OutboxEvent persistence and atomic state transitions."""

import uuid
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.utils import utc_now
from src.models.outbox_event import OutboxEvent, OutboxStatus


class OutboxRepository:
    """Atomic data access operations for transaction outbox events.
    
    Guarantees thread-safe, multi-replica claim/process mechanics using
    row-level database locks.
    """

    @staticmethod
    async def insert(session: AsyncSession, event_type: str, payload: dict) -> OutboxEvent:
        """Insert a pending outbox event inside the active session transaction."""
        event = OutboxEvent(
            event_type=event_type,
            payload=payload,
            status=OutboxStatus.PENDING.value,
            attempts=0,
        )
        session.add(event)
        await session.flush()
        return event

    @staticmethod
    async def claim_batch(session: AsyncSession, batch_size: int) -> list[OutboxEvent]:
        """Claim a batch of PENDING events atomically using FOR UPDATE SKIP LOCKED.
        
        Also recovers events stuck in PROCESSING state for more than 5 minutes 
        (e.g., if a worker replica crashed or died).
        """
        from datetime import timedelta
        stuck_cutoff = utc_now() - timedelta(minutes=5)

        stmt = (
            select(OutboxEvent)
            .where(
                or_(
                    OutboxEvent.status == OutboxStatus.PENDING.value,
                    and_(
                        OutboxEvent.status == OutboxStatus.PROCESSING.value,
                        OutboxEvent.locked_at <= stuck_cutoff
                    )
                )
            )
            .order_by(OutboxEvent.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(stmt)
        events = list(result.scalars().all())
        now = utc_now()
        for event in events:
            event.status = OutboxStatus.PROCESSING.value
            event.locked_at = now
        await session.commit()
        return events

    @staticmethod
    async def lock_event(session: AsyncSession, event_id: uuid.UUID) -> OutboxEvent | None:
        """Lock a single outbox event row FOR UPDATE for isolation during HTTP calls."""
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .with_for_update()
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def mark_done(session: AsyncSession, event_id: uuid.UUID) -> None:
        """Mark an event as successfully completed and commit the transaction."""
        event = await session.get(OutboxEvent, event_id)
        if event:
            event.status = OutboxStatus.DONE.value
            event.processed_at = utc_now()
            await session.commit()

    @staticmethod
    async def mark_failed(session: AsyncSession, event_id: uuid.UUID, max_attempts: int = 5) -> None:
        """Record a failure attempt.
        
        If attempts < max_attempts, resets to PENDING to retry,
        otherwise marks status as FAILED. Commits the transaction.
        """
        event = await session.get(OutboxEvent, event_id)
        if event:
            event.attempts += 1
            if event.attempts < max_attempts:
                event.status = OutboxStatus.PENDING.value
            else:
                event.status = OutboxStatus.FAILED.value
            await session.commit()

