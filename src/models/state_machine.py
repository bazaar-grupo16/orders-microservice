"""
Order state machine — defines and enforces valid transitions.

The transition map is the single source of truth for the order lifecycle.
Any code that mutates ``Order.status`` must go through
:func:`validate_transition` to guarantee consistency.

Separate transition rules for financial order states (OrderStatus) and
logistic item states (OrderItemLogisticStatus).
"""

from src.models.order_status import OrderItemLogisticStatus, OrderStatus


# ── Valid transitions for Order (Financial) ───────────────────────
# Key   = current status
# Value = set of statuses the order may transition to.
VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDIENTE_DE_PAGO: {
        OrderStatus.CONFIRMADA,
        OrderStatus.CANCELADA,
        OrderStatus.PAGO_RECHAZADO,
    },
    OrderStatus.CONFIRMADA: {
        OrderStatus.EN_PREPARACION,
        OrderStatus.CANCELADA,
    },
    OrderStatus.EN_PREPARACION: {
        OrderStatus.ENVIADA,
        OrderStatus.CANCELADA,
    },
    OrderStatus.ENVIADA: {
        OrderStatus.ENTREGADA,
    },
    # ── Terminal states (no outgoing transitions) ─────
    OrderStatus.ENTREGADA: set(),
    OrderStatus.PAGO_RECHAZADO: set(),
    OrderStatus.CANCELADA: {
        OrderStatus.REEMBOLSO_EN_PROCESO,
    },
    OrderStatus.REEMBOLSO_EN_PROCESO: {
        OrderStatus.REEMBOLSO_PROCESADO,
    },
    OrderStatus.REEMBOLSO_PROCESADO: set(),
}


# ── Valid transitions for OrderItem (Logistic) ─────────────────────
ITEM_VALID_TRANSITIONS: dict[OrderItemLogisticStatus, set[OrderItemLogisticStatus]] = {
    
    OrderItemLogisticStatus.PENDIENTE_DE_PAGO: {
        OrderItemLogisticStatus.CONFIRMADO,
        OrderItemLogisticStatus.CANCELADO,
    },
    OrderItemLogisticStatus.CONFIRMADO: {
        OrderItemLogisticStatus.EN_PREPARACION,
        OrderItemLogisticStatus.CANCELADO,
    },
    OrderItemLogisticStatus.EN_PREPARACION: {
        OrderItemLogisticStatus.ENVIADO,
        OrderItemLogisticStatus.CANCELADO,
    },
    OrderItemLogisticStatus.ENVIADO: {
        OrderItemLogisticStatus.ENTREGADO,
    },
    OrderItemLogisticStatus.ENTREGADO: set(),
    OrderItemLogisticStatus.CANCELADO: set(),
}


class InvalidStateTransitionError(Exception):
    """Raised when an order transition violates the state machine rules."""

    def __init__(self, current: OrderStatus | OrderItemLogisticStatus, target: OrderStatus | OrderItemLogisticStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid transition: {current.value} → {target.value}"
        )


def validate_transition(current: OrderStatus, target: OrderStatus) -> None:
    """Raise :class:`InvalidStateTransitionError` if *target* is not
    reachable from *current* according to the state machine.

    Parameters
    ----------
    current:
        The order's present status.
    target:
        The desired new status.

    Raises
    ------
    InvalidStateTransitionError
        If the transition is not allowed.
    """
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStateTransitionError(current, target)


def validate_item_transition(current: OrderItemLogisticStatus, target: OrderItemLogisticStatus) -> None:
    """Raise :class:`InvalidStateTransitionError` if *target* is not
    reachable from *current* for logistic item states.

    Parameters
    ----------
    current:
        The item's present logistic status.
    target:
        The desired new logistic status.

    Raises
    ------
    InvalidStateTransitionError
        If the transition is not allowed.
    """
    allowed = ITEM_VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStateTransitionError(current, target)
