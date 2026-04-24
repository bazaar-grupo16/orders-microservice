"""
Error response schema — RFC 7807 Problem Details.

All error responses from the API use ``application/problem+json``
content type as specified in the OpenAPI contract.
"""

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """RFC 7807 Problem Details for HTTP APIs."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str
