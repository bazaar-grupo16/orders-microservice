"""Observability primitives for correlation-aware structured logging."""

from __future__ import annotations

import contextvars
import os
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from pythonjsonlogger import jsonlogger
from starlette.middleware.base import BaseHTTPMiddleware

try:
    from ddtrace import tracer
except Exception:  # pragma: no cover - ddtrace may be absent in some environments
    tracer = None


correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id",
    default=None,
)

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"


def get_correlation_id() -> str | None:
    """Return the correlation id stored in the current context."""
    return correlation_id_var.get()


def set_correlation_id(correlation_id: str | None) -> contextvars.Token[str | None]:
    """Set the current correlation id and return a reset token."""
    return correlation_id_var.set(correlation_id)


def reset_correlation_id(token: contextvars.Token[str | None]) -> None:
    """Reset the correlation id context variable to a previous state."""
    correlation_id_var.reset(token)


def resolve_correlation_id(headers: Mapping[str, str]) -> str:
    """Get correlation id from inbound headers or create a new UUID."""
    request_id = headers.get(REQUEST_ID_HEADER)
    if request_id:
        request_id = request_id.strip()
        if request_id:
            return request_id

    correlation_id = headers.get(CORRELATION_ID_HEADER)
    if correlation_id:
        correlation_id = correlation_id.strip()
        if correlation_id:
            return correlation_id

    return str(uuid4())

def build_observability_headers() -> dict[str, str]:
        """Return outbound headers with correlation ids when available."""
        correlation_id = get_correlation_id()
        if not correlation_id:
            return {}

        return {
            REQUEST_ID_HEADER: correlation_id,
            CORRELATION_ID_HEADER: correlation_id,
        }


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagate correlation ids across request context and response headers."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        correlation_id = resolve_correlation_id(request.headers)
        token = set_correlation_id(correlation_id)

        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(token)

        # Return both headers for compatibility with upstream and clients.
        response.headers[REQUEST_ID_HEADER] = correlation_id
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response


def _datadog_span_context() -> tuple[str, str]:
    """Return Datadog trace/span ids as strings ("0" when unavailable)."""
    if tracer is None:
        return "0", "0"

    try:
        active_span = tracer.current_span()
    except Exception:
        return "0", "0"

    if active_span is None:
        return "0", "0"

    trace_id = str(getattr(active_span, "trace_id", 0) or 0)
    span_id = str(getattr(active_span, "span_id", 0) or 0)
    return trace_id, span_id


class ObservabilityJSONFormatter(jsonlogger.JsonFormatter):
    """JSON formatter that injects correlation and Datadog fields."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
            rename_fields={
                "asctime": "time",
                "levelname": "level",
                "name": "logger",
            },
        )

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: Any,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)

        correlation_id = get_correlation_id()
        trace_id, span_id = _datadog_span_context()

        log_record["correlation_id"] = correlation_id
        log_record["dd.trace_id"] = trace_id
        log_record["dd.span_id"] = span_id
        log_record["dd.service"] = os.getenv("DD_SERVICE")
        log_record["dd.version"] = os.getenv("DD_VERSION")
        log_record["dd.env"] = os.getenv("DD_ENV")
