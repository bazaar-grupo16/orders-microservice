"""
Error response schema — RFC 7807 Problem Details.

All error responses from the API use ``application/problem+json``
content type as specified in the OpenAPI contract.
"""

from pydantic import BaseModel


class StockProblemItem(BaseModel):
    """A cart item that could not be fulfilled due to stock shortage."""

    product_id: str
    product_name: str | None = None
    requested: int
    available: int


class ErrorResponse(BaseModel):
    """RFC 7807 Problem Details for HTTP APIs."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    items: list[StockProblemItem] | None = None
