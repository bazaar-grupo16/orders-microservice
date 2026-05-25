"""
Tests for the health-check endpoints ``/livez`` and ``/readyz``.

These tests run against a real PostgreSQL instance — **no mocking**.
The database is provided by ``docker-compose.test.yml``.

The sibling microservices are mocked with ``respx`` because this service
only needs to verify that their ``/readyz`` endpoints respond successfully.
"""

import httpx
import respx
from fastapi.testclient import TestClient

from src.core.config import get_settings


def _mock_dependency_readyz() -> None:
    settings = get_settings()
    respx.get(f"{settings.CART_SERVICE_URL}/readyz").mock(
        return_value=httpx.Response(200, json={"status": "ready"}),
    )
    respx.get(f"{settings.INVENTORY_SERVICE_URL}/readyz").mock(
        return_value=httpx.Response(200, json={"status": "ready"}),
    )
    respx.get(f"{settings.PAYMENTS_SERVICE_URL}/readyz").mock(
        return_value=httpx.Response(200, json={"status": "ready"}),
    )


class TestLivez:
    """``/livez`` must always return 200 without any external call."""

    def test_returns_200(self, client: TestClient) -> None:
        response = client.get("/livez")
        assert response.status_code == 200

    def test_body_contains_alive(self, client: TestClient) -> None:
        body = client.get("/livez").json()
        assert body == {"status": "alive"}


class TestReadyz:
    """``/readyz`` validates the database plus sibling service readiness."""

    @respx.mock
    def test_returns_200_when_all_dependencies_are_up(self, client: TestClient) -> None:
        """With the DB and service mocks available, /readyz must return 200."""
        _mock_dependency_readyz()

        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    @respx.mock
    def test_returns_503_when_a_dependency_is_down(self, client: TestClient) -> None:
        _mock_dependency_readyz()
        settings = get_settings()
        respx.get(f"{settings.PAYMENTS_SERVICE_URL}/readyz").mock(
            return_value=httpx.Response(503, json={"status": "unavailable"}),
        )

        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}
