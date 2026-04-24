"""
Shipping address schema.

Validates the structure of the delivery address provided during checkout.
Stored as a JSON blob in the ``orders.shipping_address`` column.
"""

from pydantic import BaseModel, ConfigDict


class ShippingAddress(BaseModel):
    """Structured shipping address for an order."""

    model_config = ConfigDict(frozen=True)

    street: str
    city: str
    state: str
    zip_code: str
    country: str
