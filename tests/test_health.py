"""
Unit tests for the health check endpoint.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_all_healthy():
    """Health endpoint returns 200 when all services are healthy."""
    with (
        patch("app.routes.health.check_postgres_health", new_callable=AsyncMock, return_value=True),
        patch("app.routes.health.check_redis_health", new_callable=AsyncMock, return_value=True),
        patch("app.routes.health.check_neo4j_health", new_callable=AsyncMock, return_value=True),
    ):
        with TestClient(app) as client:
            response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "ok"
    assert data["cache"] == "ok"
    assert data["neo4j"] == "ok"


def test_health_endpoint_postgres_down():
    """Health endpoint returns 503 when PostgreSQL is unavailable."""
    with (
        patch("app.routes.health.check_postgres_health", new_callable=AsyncMock, return_value=False),
        patch("app.routes.health.check_redis_health", new_callable=AsyncMock, return_value=True),
        patch("app.routes.health.check_neo4j_health", new_callable=AsyncMock, return_value=True),
    ):
        with TestClient(app) as client:
            response = client.get("/health")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["database"] == "error"


def test_health_endpoint_neo4j_down_is_degraded():
    """Health endpoint returns 200 degraded when only Neo4j is unavailable."""
    with (
        patch("app.routes.health.check_postgres_health", new_callable=AsyncMock, return_value=True),
        patch("app.routes.health.check_redis_health", new_callable=AsyncMock, return_value=True),
        patch("app.routes.health.check_neo4j_health", new_callable=AsyncMock, return_value=False),
    ):
        with TestClient(app) as client:
            response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["neo4j"] == "degraded"
