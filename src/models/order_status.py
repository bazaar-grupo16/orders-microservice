"""
Order status enumeration.

Defines every valid state an order can be in, following the lifecycle
specified in the user stories:

Nominal flow:
    PENDIENTE_DE_PAGO → CONFIRMADA → EN_PREPARACION → ENVIADA → ENTREGADA

Exception states:
    PAGO_RECHAZADO, CANCELADA, REEMBOLSO_EN_PROCESO, REEMBOLSO_PROCESADO
"""

from enum import StrEnum


class OrderStatus(StrEnum):
    """All valid order states.  Uses ``StrEnum`` so the value is the
    string itself, which is what gets stored in the database column."""

    # ── Nominal flow ──────────────────────────────────
    PENDIENTE_DE_PAGO = "PENDIENTE_DE_PAGO"
    CONFIRMADA = "CONFIRMADA"
    EN_PREPARACION = "EN_PREPARACION"
    ENVIADA = "ENVIADA"
    ENTREGADA = "ENTREGADA"

    # ── Exception states ──────────────────────────────
    PAGO_RECHAZADO = "PAGO_RECHAZADO"
    CANCELADA = "CANCELADA"
    REEMBOLSO_EN_PROCESO = "REEMBOLSO_EN_PROCESO"
    REEMBOLSO_PROCESADO = "REEMBOLSO_PROCESADO"
