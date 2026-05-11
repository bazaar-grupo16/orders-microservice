"""
SQLAlchemy model for the ``order_items`` table.

Each row represents a single line-item within an order, capturing a
snapshot of the product at purchase time (name, unit price, quantity).
"""

import uuid

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base


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
    quantity: Mapped[int] = mapped_column(nullable=False)
    unit_price: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False,
    )

    # ── Relationship back to Order ────────────────────
    order: Mapped["Order"] = relationship(back_populates="items")  # noqa: F821

    def __repr__(self) -> str:
        return f"<OrderItem {self.product_name} x{self.quantity}>"
