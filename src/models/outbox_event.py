"""SQLAlchemy model for the ``outbox_events`` table.

Tracks messages/events that need to be published to external services
under transactional guarantees.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base


class OutboxStatus(StrEnum):
    """Execution status for outbox events."""
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


class OutboxEvent(Base):
    """Persistent transactional outbox event.
    
    Guarantees reliable stock release and payment cancellation.
    """

    __tablename__ = "outbox_events"

    # ── Primary key ───────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        default=uuid.uuid4, primary_key=True,
    )

    # ── Core fields ───────────────────────────────────
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True,
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        default=OutboxStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False,
    )

    # ── Timestamps ────────────────────────────────────
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )

    def __repr__(self) -> str:
        return f"<OutboxEvent {self.id} type={self.event_type} status={self.status}>"
