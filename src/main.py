"""
Orders Microservice — Application entry-point.

Creates the FastAPI application, registers routers and error handlers,
and configures structured logging.  Database tables are created on
startup via the ORM metadata (suitable for development; use Alembic
migrations in production).
"""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from src.api.error_handlers import register_error_handlers
from src.api.health import router as health_router
from src.api.routers.orders import router as orders_router
from src.api.routers.admin_orders import router as admin_orders_router
from src.api.routers.metrics import router as admin_metrics_router
from src.core.config import get_settings
from src.core.database import Base, engine, wait_for_db
from src.core.logging import setup_logging
from src.core.observability import CorrelationIdMiddleware
from src.core.telemetry import initialize_prometheus_metrics, router as metrics_router
from src.models import Order, OrderItem, OrderStatusHistory, OutboxEvent  # noqa: F401 — register tables
from src.services.order_service.expiration_worker import (
    start_pending_payment_expiration_worker,
    stop_pending_payment_expiration_worker,
)
from src.services.outbox_worker import (
    start_outbox_worker,
    stop_outbox_worker,
)


settings = get_settings()
logger = logging.getLogger(__name__)

initialize_prometheus_metrics()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown lifecycle hook."""
    setup_logging()
    logger.info("Starting %s", settings.APP_NAME)
    # Wait for the database before creating tables.
    wait_for_db()
    # Create tables (dev convenience — use Alembic in production).
    Base.metadata.create_all(bind=engine)
    expiration_worker = start_pending_payment_expiration_worker()
    outbox_worker = start_outbox_worker()
    yield
    await stop_outbox_worker(outbox_worker)
    await stop_pending_payment_expiration_worker(expiration_worker)
    logger.info("Shutting down %s", settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)
app.add_middleware(CorrelationIdMiddleware)

# ── Error handlers ───────────────────────────────────
register_error_handlers(app)

# ── Routers ──────────────────────────────────────────
app.include_router(health_router)
app.include_router(orders_router)
app.include_router(admin_orders_router)
app.include_router(metrics_router)
app.include_router(admin_metrics_router)
