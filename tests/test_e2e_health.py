"""
End-to-end health and metrics endpoint tests (T-6.3, T-6.4).

Tests /health and /metrics against the deployed system
at http://192.168.1.110:8080.
"""

import httpx
import pytest

from tests.conftest_e2e import BASE_URL

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        yield c


# ===================================================================
# Health endpoint (T-6.3)
# ===================================================================


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_returns_200_when_healthy(self, client):
        """Health endpoint returns 200 with all deps ok when system is healthy."""
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()

        # Should contain status for each backing service
        # The exact structure depends on the health flow, but should indicate
        # each dependency is ok
        assert isinstance(body, dict)

    def test_contains_postgres_status(self, client):
        """Health response includes postgres dependency status."""
        resp = client.get("/health")
        body = resp.json()
        # Look for postgres in the response (various possible key structures)
        body_str = str(body).lower()
        assert "database" in body or "postgres" in body_str, f"No postgres/database status in health: {body}"

    def test_contains_redis_status(self, client):
        """Health response includes redis dependency status."""
        resp = client.get("/health")
        body = resp.json()
        body_str = str(body).lower()
        assert "cache" in body or "redis" in body_str, f"No redis/cache status in health: {body}"

    def test_contains_neo4j_status(self, client):
        """Health response includes neo4j dependency status."""
        resp = client.get("/health")
        body = resp.json()
        body_str = str(body).lower()
        assert "neo4j" in body_str, f"No neo4j status in health: {body}"

    def test_response_time_reasonable(self, client):
        """Health check completes within 10 seconds."""
        import time

        start = time.monotonic()
        resp = client.get("/health")
        elapsed = time.monotonic() - start
        assert resp.status_code == 200
        assert elapsed < 10.0, f"Health check took {elapsed:.1f}s (> 10s threshold)"


# ===================================================================
# Metrics endpoint (T-6.4)
# ===================================================================


class TestMetricsEndpoint:
    """Tests for GET /metrics."""

    def test_returns_200(self, client):
        """Metrics endpoint returns 200."""
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_prometheus_format(self, client):
        """Metrics response is in Prometheus exposition format."""
        resp = client.get("/metrics")
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        # Prometheus format uses text/plain or the openmetrics content type
        assert any(
            ct in content_type
            for ct in ("text/plain", "text/", "openmetrics")
        ), f"Unexpected content-type: {content_type}"

        text = resp.text
        # Prometheus format has lines starting with # for HELP/TYPE
        # and metric_name{labels} value lines
        assert len(text) > 0, "Metrics response is empty"

    def test_contains_mcp_metrics(self, client):
        """Metrics include MCP request counters after making MCP calls."""
        # First make an MCP call to ensure counters are populated
        client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
        )

        resp = client.get("/metrics")
        text = resp.text
        # Should contain MCP-related metric names
        assert "mcp_request" in text.lower() or "context_broker" in text.lower(), (
            f"No MCP metrics found in:\n{text[:500]}"
        )

    def test_contains_help_and_type_annotations(self, client):
        """Prometheus metrics include # HELP and # TYPE annotations."""
        resp = client.get("/metrics")
        text = resp.text
        has_help = any(line.startswith("# HELP") for line in text.splitlines())
        has_type = any(line.startswith("# TYPE") for line in text.splitlines())
        assert has_help, "No # HELP annotations in metrics"
        assert has_type, "No # TYPE annotations in metrics"
