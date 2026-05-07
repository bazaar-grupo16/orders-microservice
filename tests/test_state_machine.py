"""
Tests for the order state machine.

Validates every allowed transition and confirms that invalid ones
are rejected with ``InvalidStateTransitionError``.
"""

import pytest

from src.models.order_status import OrderStatus
from src.models.state_machine import (
    VALID_TRANSITIONS,
    InvalidStateTransitionError,
    validate_transition,
)


class TestValidTransitions:
    """Every transition defined in VALID_TRANSITIONS must succeed."""

    @pytest.mark.parametrize(
        "current, target",
        [
            # ── Nominal flow ──────────────────────────
            (OrderStatus.PENDIENTE_DE_PAGO, OrderStatus.CONFIRMADA),
            (OrderStatus.CONFIRMADA, OrderStatus.EN_PREPARACION),
            (OrderStatus.EN_PREPARACION, OrderStatus.ENVIADA),
            (OrderStatus.ENVIADA, OrderStatus.ENTREGADA),
            # ── Payment failure ───────────────────────
            (OrderStatus.PENDIENTE_DE_PAGO, OrderStatus.PAGO_RECHAZADO),
            # ── Cancellation from various states ──────
            (OrderStatus.PENDIENTE_DE_PAGO, OrderStatus.CANCELADA),
            (OrderStatus.CONFIRMADA, OrderStatus.CANCELADA),
            (OrderStatus.EN_PREPARACION, OrderStatus.CANCELADA),
            # ── Refund flow ───────────────────────────
            (OrderStatus.CANCELADA, OrderStatus.REEMBOLSO_EN_PROCESO),
            (OrderStatus.REEMBOLSO_EN_PROCESO, OrderStatus.REEMBOLSO_PROCESADO),
        ],
    )
    def test_valid_transition_does_not_raise(
        self, current: OrderStatus, target: OrderStatus,
    ) -> None:
        # Should not raise any exception.
        validate_transition(current, target)


class TestInvalidTransitions:
    """Transitions that are NOT in VALID_TRANSITIONS must raise."""

    @pytest.mark.parametrize(
        "current, target",
        [
            # ── Cannot skip steps ─────────────────────
            (OrderStatus.PENDIENTE_DE_PAGO, OrderStatus.EN_PREPARACION),
            (OrderStatus.PENDIENTE_DE_PAGO, OrderStatus.ENVIADA),
            (OrderStatus.PENDIENTE_DE_PAGO, OrderStatus.ENTREGADA),
            (OrderStatus.CONFIRMADA, OrderStatus.ENVIADA),
            (OrderStatus.CONFIRMADA, OrderStatus.ENTREGADA),
            (OrderStatus.EN_PREPARACION, OrderStatus.ENTREGADA),
            # ── Cannot go backwards ───────────────────
            (OrderStatus.CONFIRMADA, OrderStatus.PENDIENTE_DE_PAGO),
            (OrderStatus.ENVIADA, OrderStatus.EN_PREPARACION),
            (OrderStatus.ENTREGADA, OrderStatus.ENVIADA),
            # ── Terminal states have no outgoing ──────
            (OrderStatus.ENTREGADA, OrderStatus.CONFIRMADA),
            (OrderStatus.PAGO_RECHAZADO, OrderStatus.CONFIRMADA),
            (OrderStatus.REEMBOLSO_PROCESADO, OrderStatus.CANCELADA),
            # ── Cannot cancel after shipment ──────────
            (OrderStatus.ENVIADA, OrderStatus.CANCELADA),
            (OrderStatus.ENTREGADA, OrderStatus.CANCELADA),
            # ── Self-transitions ──────────────────────
            (OrderStatus.CONFIRMADA, OrderStatus.CONFIRMADA),
            (OrderStatus.PENDIENTE_DE_PAGO, OrderStatus.PENDIENTE_DE_PAGO),
        ],
    )
    def test_invalid_transition_raises(
        self, current: OrderStatus, target: OrderStatus,
    ) -> None:
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            validate_transition(current, target)
        assert exc_info.value.current == current
        assert exc_info.value.target == target


class TestTransitionMapCompleteness:
    """Every OrderStatus member must have an entry in VALID_TRANSITIONS."""

    def test_all_statuses_are_covered(self) -> None:
        for status in OrderStatus:
            assert status in VALID_TRANSITIONS, (
                f"OrderStatus.{status.name} is missing from VALID_TRANSITIONS"
            )
