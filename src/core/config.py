"""
Application settings loaded from environment variables.

Uses pydantic-settings so every value comes from the environment (or a .env
file during local development).  **No credentials are ever hard-coded.**
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised configuration for the microservice."""

    # ── Application ──────────────────────────────────
    APP_NAME: str = "orders-service"
    LOG_LEVEL: str = "info"

    # ── Database ─────────────────────────────────────
    DB_HOST: str
    DB_PORT: int = 5432
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

    # ── External Services (internal K8s URLs) ────────
    CART_SERVICE_URL: str
    INVENTORY_SERVICE_URL: str
    NOTIFICATIONS_SERVICE_URL: str = ""
    PAYMENTS_SERVICE_URL: str

    # ── Pending payment expiration ───────────────────
    PENDING_PAYMENT_EXPIRATION_ENABLED: bool = True
    PENDING_PAYMENT_EXPIRATION_MINUTES: int = 30
    PENDING_PAYMENT_EXPIRATION_BATCH_SIZE: int = 50
    PENDING_PAYMENT_EXPIRATION_INTERVAL_SECONDS: int = 300

    # ── Transactional Outbox ─────────────────────────
    OUTBOX_WORKER_ENABLED: bool = True
    OUTBOX_WORKER_INTERVAL_SECONDS: int = 30
    OUTBOX_WORKER_BATCH_SIZE: int = 20
    OUTBOX_MAX_ATTEMPTS: int = 5


    # ── Pydantic-settings config ─────────────────────
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    @property
    def database_url(self) -> str:
        """Build the PostgreSQL connection DSN from individual fields."""
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )



@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()  # type: ignore[call-arg]
