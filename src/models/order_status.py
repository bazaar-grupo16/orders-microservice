"""
Order status enumeration.

Defines every valid state an order can be in, following the lifecycle
specified in the user stories:

Order (Financial) nominal flow:
    PENDIENTE_DE_PAGO → CONFIRMADA → [handled by vendor] → [order consolidated]

Exception states:
    PAGO_RECHAZADO, CANCELADA, REEMBOLSO_EN_PROCESO, REEMBOLSO_PROCESADO

OrderItem (Logistic) nominal flow per vendor:
    EN_PREPARACION → ENVIADA → ENTREGADA
    
Exception state:
    CANCELADA
"""

from enum import StrEnum


class OrderStatus(StrEnum):
    """All valid order states.  Uses ``StrEnum`` so the value is the
    string itself, which is what gets stored in the database column.
    
    These represent the financial state of the order, not the logistics state.
    """

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


# Order states that represent a successful payment and count toward revenue metrics.
REVENUE_ELIGIBLE_STATUSES: tuple[str, ...] = (
    OrderStatus.CONFIRMADA.value,
    OrderStatus.EN_PREPARACION.value,
    OrderStatus.ENVIADA.value,
    OrderStatus.ENTREGADA.value,
    OrderStatus.REEMBOLSO_EN_PROCESO.value,
    OrderStatus.REEMBOLSO_PROCESADO.value,
)


class OrderItemLogisticStatus(StrEnum):
    """Logistic state of an individual order item, per vendor.
    
    Separate from the order's financial status (OrderStatus).
    Each item can be in a different logistic state independently.
    """

    PENDIENTE_DE_PAGO = "PENDIENTE_DE_PAGO"
    CONFIRMADO = "CONFIRMADO"
    EN_PREPARACION = "EN_PREPARACION"
    ENVIADO = "ENVIADO"
    ENTREGADO = "ENTREGADO"
    CANCELADO = "CANCELADO"
    REEMBOLSO_EN_PROCESO = "REEMBOLSO_EN_PROCESO"
    REEMBOLSO_PROCESADO = "REEMBOLSO_PROCESADO"

ITEM_STATUS_TO_ORDER_STATUS: dict[str, str] = {
    OrderItemLogisticStatus.PENDIENTE_DE_PAGO.value: OrderStatus.PENDIENTE_DE_PAGO.value,
    OrderItemLogisticStatus.CONFIRMADO.value: OrderStatus.CONFIRMADA.value,
    OrderItemLogisticStatus.EN_PREPARACION.value: OrderStatus.EN_PREPARACION.value,
    OrderItemLogisticStatus.ENVIADO.value: OrderStatus.ENVIADA.value,
    OrderItemLogisticStatus.ENTREGADO.value: OrderStatus.ENTREGADA.value,
    OrderItemLogisticStatus.CANCELADO.value: OrderStatus.CANCELADA.value,
    OrderItemLogisticStatus.REEMBOLSO_EN_PROCESO.value: OrderStatus.REEMBOLSO_EN_PROCESO.value,
    OrderItemLogisticStatus.REEMBOLSO_PROCESADO.value: OrderStatus.REEMBOLSO_PROCESADO.value,
}