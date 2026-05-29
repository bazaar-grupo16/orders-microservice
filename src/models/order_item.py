"""
SQLAlchemy model for the ``order_items`` table.

Each row represents a single line-item within an order, capturing a
snapshot of the product at purchase time (name, unit price, quantity).

OrderItem also tracks logistic state independently for each vendor's items.
"""

import uuid

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base
from src.core.utils import utc_now
from src.models.order_status import OrderItemLogisticStatus


class OrderItem(Base):
    """A single line-item belonging to an :class:`Order`."""

    __tablename__ = "order_items"

    # ── Primary key ───────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        default=uuid.uuid4, primary_key=True,
    )

    # ── Foreign key to Order ──────────────────────────
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Product snapshot ──────────────────────────────
    product_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    seller_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="Uncategorized")
    quantity: Mapped[int] = mapped_column(nullable=False)
    unit_price: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False,
    )

    # ── Logistic state (per item, per vendor) ────────
    status: Mapped[str] = mapped_column(
        String(30),
        default=OrderItemLogisticStatus.PENDIENTE_DE_PAGO.value,
        nullable=False,
    )

    # ── Tracking code (exclusive to item) ────────────
    tracking_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True, default=None,
    )

    # ── Timestamps ────────────────────────────────────
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False,
    )

    # ── Relationship back to Order ────────────────────
    order: Mapped["Order"] = relationship(back_populates="items")  # noqa: F821

    def __repr__(self) -> str:
        return f"<OrderItem {self.product_name} x{self.quantity} status={self.status}>"
