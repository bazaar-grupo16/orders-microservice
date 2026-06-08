"""Repository layer for OutboxEvent persistence and atomic state transitions."""

import uuid
from sqlalchemy.orm import Session
from src.core.utils import utc_now
from src.models.outbox_event import OutboxEvent, OutboxStatus


class OutboxRepository:
    """Atomic data access operations for transaction outbox events.
    
    Guarantees thread-safe, multi-replica claim/process mechanics using
    row-level database locks.
    """

    @staticmethod
    def insert(session: Session, event_type: str, payload: dict) -> OutboxEvent:
        """Insert a pending outbox event inside the active session transaction."""
        event = OutboxEvent(
            event_type=event_type,
            payload=payload,
            status=OutboxStatus.PENDING.value,
            attempts=0,
        )
        session.add(event)
        session.flush()
        return event

    @staticmethod
    def claim_batch(session: Session, batch_size: int) -> list[OutboxEvent]:
        """Claim a batch of PENDING events atomically using FOR UPDATE SKIP LOCKED.
        
        Updates their status to PROCESSING and locked_at to the current time,
        then commits to make the claims visible and release the short-lived locks.
        """
        events = (
            session.query(OutboxEvent)
            .filter(OutboxEvent.status == OutboxStatus.PENDING.value)
            .order_by(OutboxEvent.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .all()
        )
        now = utc_now()
        for event in events:
            event.status = OutboxStatus.PROCESSING.value
            event.locked_at = now
        session.commit()
        return events

    @staticmethod
    def lock_event(session: Session, event_id: uuid.UUID) -> OutboxEvent | None:
        """Lock a single outbox event row FOR UPDATE for isolation during HTTP calls."""
        return (
            session.query(OutboxEvent)
            .filter(OutboxEvent.id == event_id)
            .with_for_update()
            .first()
        )

    @staticmethod
    def mark_done(session: Session, event_id: uuid.UUID) -> None:
        """Mark an event as successfully completed and commit the transaction."""
        event = session.get(OutboxEvent, event_id)
        if event:
            event.status = OutboxStatus.DONE.value
            event.processed_at = utc_now()
            session.commit()

    @staticmethod
    def mark_failed(session: Session, event_id: uuid.UUID, max_attempts: int = 5) -> None:
        """Record a failure attempt.
        
        If attempts < max_attempts, resets to PENDING to retry,
        otherwise marks status as FAILED. Commits the transaction.
        """
        event = session.get(OutboxEvent, event_id)
        if event:
            event.attempts += 1
            if event.attempts < max_attempts:
                event.status = OutboxStatus.PENDING.value
            else:
                event.status = OutboxStatus.FAILED.value
            session.commit()
