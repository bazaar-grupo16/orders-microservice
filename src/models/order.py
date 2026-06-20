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

from sqlalchemy import JSON, DateTime, Numeric, String, case, exists
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base
from src.core.utils import utc_now
from src.models.order_status import OrderStatus


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
        index=True,
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
        DateTime(timezone=True), default=utc_now, nullable=False, index=True,
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

    @hybrid_property
    def aggregated_status(self) -> str:
        """Compute aggregated logistic status from items in Python."""
        if not self.items:
            return self.status

        if self.status in (
            OrderStatus.PENDIENTE_DE_PAGO.value,
            OrderStatus.PAGO_RECHAZADO.value,
            OrderStatus.CANCELADA.value,
            OrderStatus.REEMBOLSO_EN_PROCESO.value,
            OrderStatus.REEMBOLSO_PROCESADO.value,
        ):
            return self.status

        item_statuses = {item.status for item in self.items}
        if len(item_statuses) == 1:
            single_status = item_statuses.pop()
            from src.models.order_status import ITEM_STATUS_TO_ORDER_STATUS
            return ITEM_STATUS_TO_ORDER_STATUS.get(single_status, self.status)

        from src.models.order_status import OrderItemLogisticStatus
        if OrderItemLogisticStatus.ENVIADO.value in item_statuses:
            return OrderStatus.ENVIADA.value
        if OrderItemLogisticStatus.EN_PREPARACION.value in item_statuses:
            return OrderStatus.EN_PREPARACION.value
        if OrderItemLogisticStatus.CONFIRMADO.value in item_statuses:
            return OrderStatus.CONFIRMADA.value
        if OrderItemLogisticStatus.ENTREGADO.value in item_statuses:
            return OrderStatus.ENTREGADA.value

        return self.status

    @aggregated_status.expression
    def aggregated_status(cls):
        """Compute aggregated logistic status from items in SQL."""
        from src.models.order_item import OrderItem

        has_enviado = exists().where(OrderItem.order_id == cls.id, OrderItem.status == "ENVIADO").correlate(cls)
        has_preparacion = exists().where(OrderItem.order_id == cls.id, OrderItem.status == "EN_PREPARACION").correlate(cls)
        has_confirmado = exists().where(OrderItem.order_id == cls.id, OrderItem.status == "CONFIRMADO").correlate(cls)
        has_entregado = exists().where(OrderItem.order_id == cls.id, OrderItem.status == "ENTREGADO").correlate(cls)

        return case(
            (
                cls.status.in_([
                    "PENDIENTE_DE_PAGO",
                    "PAGO_RECHAZADO",
                    "CANCELADA",
                    "REEMBOLSO_EN_PROCESO",
                    "REEMBOLSO_PROCESADO",
                ]),
                cls.status,
            ),
            (has_enviado, "ENVIADA"),
            (has_preparacion, "EN_PREPARACION"),
            (has_confirmado, "CONFIRMADA"),
            (has_entregado, "ENTREGADA"),
            else_=cls.status,
        )

    def __repr__(self) -> str:
        return f"<Order {self.id} status={self.status}>"
