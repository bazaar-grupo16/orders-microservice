"""HTTP clients for communicating with sibling microservices."""

from src.clients import cart_client, inventory_client, notifications_client, payments_client

__all__ = ["cart_client", "inventory_client", "notifications_client", "payments_client"]
