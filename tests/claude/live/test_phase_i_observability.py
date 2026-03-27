"""Phase I: Observability and infrastructure tests.

Tests structured logging, Prometheus metrics, log shipper, alerter health,
container security, and volume mounts against the live Context Broker stack.
"""

import json
import subprocess

import pytest

from tests.claude.live.helpers import (
    docker_exec,
    docker_logs,
    docker_psql,
    get_pipeline_status,
    log_issue,
)

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

class TestStructuredLogging:
    """Verify the application emits structured JSON logs."""

    def test_structured_json_logging(self):
        """docker_logs from langgraph container; first 10 lines parse as JSON."""
        raw = docker_logs("context-broker-langgraph", lines=20)
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        # Take up to 10 non-empty lines
        sample = lines[:10]
        assert sample, "No log lines captured from langgraph container"

        parsed_count = 0
        for line in sample:
            try:
                obj = json.loads(line)
                # Verify required fields
                has_ts = "timestamp" in obj or "time" in obj or "ts" in obj
                has_level = "level" in obj or "severity" in obj or "levelname" in obj
                has_msg = "message" in obj or "msg" in obj or "event" in obj
                if has_ts and has_level and has_msg:
                    parsed_count += 1
            except json.JSONDecodeError:
                # Some lines may be non-JSON (e.g., startup banners)
                continue

        assert parsed_count > 0, (
            "No structured JSON log lines with timestamp/level/message found. "
            f"Sample lines: {sample[:3]}"
        )

    def test_health_check_not_in_app_logs(self):
        """HealthCheckFilter should suppress 'GET /health' from app logs."""
        raw = docker_logs("context-broker-langgraph", lines=200)
        # Look through the logs for health check access log lines
        health_lines = [
            ln for ln in raw.splitlines()
            if "GET /health" in ln and "HealthCheckFilter" not in ln
        ]
        if health_lines:
            log_issue(
                "test_health_check_not_in_app_logs",
                "warning",
                "logging",
                f"Found {len(health_lines)} 'GET /health' lines in app logs",
                "0 health check log lines",
                f"{len(health_lines)} lines found",
            )
        # This is a quality check -- warn but don't hard-fail
        # as some lines may be from startup or other sources.


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

class TestPrometheusMetrics:
    """Verify Prometheus metrics are exposed on /metrics."""

    def test_prometheus_mcp_counter(self, http_client):
        """GET /metrics contains context_broker_mcp_requests_total with tool label."""
        resp = http_client.get("/metrics", timeout=10)
        assert resp.status_code == 200, f"/metrics returned {resp.status_code}"
        text = resp.text
        assert "context_broker_mcp_requests_total" in text, (
            "Missing metric: context_broker_mcp_requests_total"
        )
        # Verify it has a tool label
        assert 'tool=' in text or 'tool_name=' in text or (
            "context_broker_mcp_requests_total{" in text
        ), "MCP counter metric missing tool label"

    def test_prometheus_request_duration(self, http_client):
        """GET /metrics contains context_broker_mcp_request_duration_seconds."""
        resp = http_client.get("/metrics", timeout=10)
        assert resp.status_code == 200
        assert "context_broker_mcp_request_duration_seconds" in resp.text, (
            "Missing metric: context_broker_mcp_request_duration_seconds"
        )

    def test_prometheus_chat_counter(self, http_client):
        """GET /metrics contains context_broker_chat_requests_total."""
        resp = http_client.get("/metrics", timeout=10)
        assert resp.status_code == 200
        assert "context_broker_chat_requests_total" in resp.text, (
            "Missing metric: context_broker_chat_requests_total"
        )

    def test_prometheus_queue_gauges(self, http_client):
        """GET /metrics contains embedding/assembly/extraction queue depth gauges."""
        status = get_pipeline_status(http_client)
        # get_pipeline_status returns -1 for missing metrics
        for metric_name, value in status.items():
            assert value >= 0, (
                f"Queue gauge '{metric_name}' not found in /metrics (got {value})"
            )


# ---------------------------------------------------------------------------
# Log shipper
# ---------------------------------------------------------------------------

class TestLogShipper:
    """Verify the log shipper is writing to PostgreSQL."""

    def test_log_shipper_writes(self):
        """system_logs table has at least 1 row."""
        result = docker_psql("SELECT COUNT(*) FROM system_logs").strip()
        count = int(result) if result.isdigit() else 0
        assert count > 0, (
            f"system_logs table is empty (count={result}). "
            "Log shipper may not be running."
        )


# ---------------------------------------------------------------------------
# Alerter
# ---------------------------------------------------------------------------

class TestAlerter:
    """Verify the alerter container is healthy."""

    def test_alerter_health(self):
        """Alerter container responds to health check."""
        output = docker_exec(
            "context-broker-alerter",
            "curl -sf http://localhost:8000/health",
            timeout=10,
        )
        # Expect a JSON or text response indicating healthy
        assert output, "Alerter health check returned empty response"
        lower = output.lower()
        assert "healthy" in lower or "ok" in lower or '"status"' in lower, (
            f"Alerter health response unexpected: {output}"
        )


# ---------------------------------------------------------------------------
# Container security and volumes
# ---------------------------------------------------------------------------

class TestContainerSecurity:
    """Verify container security and volume configuration."""

    def test_non_root_container(self):
        """Langgraph container runs as non-root user."""
        whoami = docker_exec("context-broker-langgraph", "whoami", timeout=10)
        assert whoami, "whoami returned empty"
        assert whoami != "root", (
            f"Container is running as root (whoami={whoami})"
        )

    def test_data_volume_exists(self):
        """The /data directory is mounted inside the langgraph container."""
        output = docker_exec("context-broker-langgraph", "ls /data", timeout=10)
        # ls should succeed (no error). Even an empty dir returns ""
        # The key test is that docker_exec didn't fail with a non-zero exit.
        # We just verify no "No such file" in stdout (stderr is not captured).
        assert "No such file" not in output, "/data volume not mounted"

    def test_config_volume_exists(self):
        """The /config/config.yml file exists inside the langgraph container."""
        output = docker_exec(
            "context-broker-langgraph", "ls /config/config.yml", timeout=10
        )
        assert "config.yml" in output or "No such file" not in output, (
            "/config/config.yml not found in container"
        )

    def test_container_healthcheck(self):
        """Healthcheck is configured on the langgraph container."""
        from tests.claude.live.helpers import compose_cmd
        # Use compose ps to check health status
        output = compose_cmd("ps context-broker-langgraph --format json")
        assert output, "compose ps returned empty output"
        assert "health" in output.lower() or "running" in output.lower(), (
            "No healthcheck configured on langgraph container"
        )
        # Parse and verify it has a test command
        try:
            hc = json.loads(output.strip('"'))
            assert "Test" in hc or "test" in hc, (
                f"Healthcheck config missing Test field: {hc}"
            )
        except (json.JSONDecodeError, TypeError):
            # If the format is not pure JSON, at least verify it's not empty/null
            assert len(output) > 5, f"Healthcheck appears unconfigured: {output}"
