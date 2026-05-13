"""
SQLAlchemy model for the ``orders`` table.

Each row represents a single purchase transaction.  The ``idempotency_key``
column carries a UNIQUE constraint so the database itself rejects duplicate
checkout attempts — defence-in-depth beyond the application-level check.

The Order holds the FINANCIAL state (payment tracking). Logistic state
(shipping, delivery) is tracked per-item in OrderItem.status.
The `status` column in Order represents the consolidated financial state,
not the logistic state. The logistic state is derived from items.
"""

import uuid

from sqlalchemy import JSON, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base
from src.core.utils import utc_now
from src.models.order_status import OrderItemLogisticStatus, OrderStatus


class Order(Base):
    """Persistent order aggregate root.
    
    Holds financial state (payment tracking) and coordinates multi-vendor logistics.
    Logistic state is aggregated from OrderItem.status values.
    """

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

    def get_aggregated_status(self) -> OrderItemLogisticStatus | None:
        """Compute aggregated logistic status from items.
        
        Returns None if no items exist. Otherwise returns the most restrictive state:
        - Else if any item is EN_PREPARACION → EN_PREPARACION
        - Else if all are ENVIADA → ENVIADA
        - Else if all are ENTREGADA → ENTREGADA
        - If any item is CANCELADA, consider the rest of the order for logistic status.
        - If all are CANCELADA → CANCELADA
        """
        if not self.items:
            return None

        item_statuses = {item.status for item in self.items}

        if OrderItemLogisticStatus.CANCELADO in item_statuses:
            if len(item_statuses) == 1:
                return OrderItemLogisticStatus.CANCELADO
            item_statuses.discard(OrderItemLogisticStatus.CANCELADO)
        if OrderItemLogisticStatus.EN_PREPARACION in item_statuses:
            return OrderItemLogisticStatus.EN_PREPARACION
        if OrderItemLogisticStatus.ENVIADO in item_statuses:
            return OrderItemLogisticStatus.ENVIADO
        return OrderItemLogisticStatus.ENTREGADO

    def __repr__(self) -> str:
        return f"<Order {self.id} status={self.status}>"
