"""SQLAlchemy model for persisted order/item status transitions."""

import uuid

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base
from src.core.utils import utc_now


class OrderStatusHistory(Base):
    """Historical event for a status transition.

    Stores both financial (order-level) and logistic (item-level) changes.
    For financial changes, ``item_id`` is ``None``.
    """

    __tablename__ = "order_status_history"

    id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    status_previous: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status_new: Mapped[str] = mapped_column(String(30), nullable=False)
    timestamp: Mapped[str] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    tracking_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
