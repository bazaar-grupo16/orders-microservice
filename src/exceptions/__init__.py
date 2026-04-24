"""
Domain exception types for the Orders microservice.

Each exception maps to a specific HTTP status code and is translated
to an ``application/problem+json`` response by the error handlers
registered in :pymod:`src.api.error_handlers`.
"""


class OrderNotFoundError(Exception):
    """Raised when an order cannot be found by its ID."""

    def __init__(self, order_id: str) -> None:
        self.order_id = order_id
        super().__init__(f"Order not found: {order_id}")


class OrderAccessDeniedError(Exception):
    """Raised when a user tries to access an order they do not own."""

    def __init__(self) -> None:
        super().__init__("Access denied to this order")


class InsufficientStockError(Exception):
    """Raised when the Inventory service reports insufficient stock (409)."""

    def __init__(self, detail: str = "Insufficient stock to complete the order") -> None:
        self.detail = detail
        super().__init__(detail)


class CartEmptyError(Exception):
    """Raised when the user's cart is empty at checkout time."""

    def __init__(self) -> None:
        super().__init__("Cart is empty")


class ExternalServiceError(Exception):
    """Raised when communication with an external service fails."""

    def __init__(self, service: str, detail: str) -> None:
        self.service = service
        self.detail = detail
        super().__init__(f"{service}: {detail}")
