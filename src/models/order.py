"""
SQLAlchemy model for the ``orders`` table.

Each row represents a single purchase transaction.  The ``idempotency_key``
column carries a UNIQUE constraint so the database itself rejects duplicate
checkout attempts — defence-in-depth beyond the application-level check.
"""

import uuid

from sqlalchemy import JSON, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base
from src.core.utils import utc_now
from src.models.order_status import OrderStatus


class Order(Base):
    """Persistent order aggregate root."""

    __tablename__ = "orders"

    # ── Primary key ───────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        default=uuid.uuid4, primary_key=True,
    )

    # ── Core fields ───────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(30),
        default=OrderStatus.PENDIENTE_DE_PAGO,
        nullable=False,
    )
    shipping_address: Mapped[dict] = mapped_column(JSON, nullable=False)
    total_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=0,
    )

    # ── Idempotency ───────────────────────────────────
    idempotency_key: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True,
    )

    # ── Optional tracking ─────────────────────────────
    tracking_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True, default=None,
    )

    # ── Payment preference (populated after Payments service call) ───
    preference_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, default=None,
    )
    init_point: Mapped[str | None] = mapped_column(
        String(2048), nullable=True, default=None,
    )

    # ── Timestamps ────────────────────────────────────
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False,
    )

    # ── Relationships ─────────────────────────────────
    items: Mapped[list["OrderItem"]] = relationship(  # noqa: F821
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Order {self.id} status={self.status}>"
