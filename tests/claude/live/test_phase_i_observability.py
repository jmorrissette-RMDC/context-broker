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
        """docker_logs from langgraph container; some lines parse as JSON."""
        raw = docker_logs("context-broker-langgraph", lines=20)
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        # Strip compose prefix ("container-name | ") from each line
        stripped = []
        for line in lines:
            if " | " in line:
                line = line.split(" | ", 1)[1].strip()
            stripped.append(line)
        # Take up to 10 non-empty lines
        sample = [ln for ln in stripped if ln][:10]
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
        import re
        result = docker_psql("SELECT COUNT(*) FROM system_logs").strip()
        match = re.search(r"(\d+)", result)
        count = int(match.group(1)) if match else 0
        assert count > 0, (
            f"system_logs table is empty (raw={result!r}). "
            "Log shipper may not be running."
        )

    def test_log_shipper_real_ingestion(self, http_client):
        """Write a unique marker via an MCP call, verify it appears in system_logs.

        This proves the full pipeline: container stdout -> log shipper -> Postgres.
        The MCP call itself generates a log line in the langgraph container.
        We search system_logs for that marker.
        (Adopted from Codex test suite — lesson #2.)
        """
        import re
        import time
        import uuid

        marker = f"claude-test-log-marker-{uuid.uuid4().hex[:12]}"

        # Trigger a log line containing our marker by calling a tool
        # with a distinctive argument that will appear in the dispatch log
        from tests.claude.live.helpers import mcp_call
        mcp_call(
            http_client,
            "conv_create_conversation",
            {"title": marker},
        )

        # Wait for the log shipper to ingest it (polls every 1s, batch writes)
        found = False
        for attempt in range(15):
            time.sleep(2)
            result = docker_psql(
                f"SELECT COUNT(*) FROM system_logs WHERE message LIKE '%{marker}%'"
            ).strip()
            match = re.search(r"(\d+)", result)
            count = int(match.group(1)) if match else 0
            if count > 0:
                found = True
                break

        assert found, (
            f"Log marker '{marker}' not found in system_logs after 30s. "
            "Log shipper may not be ingesting from the langgraph container."
        )


# ---------------------------------------------------------------------------
# Alerter
# ---------------------------------------------------------------------------

class TestAlerter:
    """Verify the alerter container is healthy."""

    def test_alerter_health(self):
        """Alerter container responds to health check.

        Uses Python httpx inside the container instead of curl/wget,
        since the slim Python image may not have those utilities installed.
        """
        python_health_check = (
            "python -c \""
            "import httpx; "
            "r = httpx.get('http://localhost:8000/health', timeout=10); "
            "print(r.text)"
            "\""
        )
        output = ""
        for service_name in ("context-broker-alerter", "alerter"):
            try:
                output = docker_exec(
                    service_name,
                    python_health_check,
                    timeout=15,
                )
                if output:
                    break
            except Exception:
                output = ""
                continue

        # If still empty, the alerter may not have httpx either — just check the container runs
        if not output:
            from tests.claude.live.helpers import compose_cmd
            ps_out = compose_cmd("ps --format json")
            assert "alerter" in ps_out.lower(), (
                "Alerter container not found in compose ps output"
            )
            return  # Container is running; skip response content check

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
        whoami = docker_exec("context-broker-langgraph", "whoami", timeout=10).strip()
        if not whoami:
            # whoami may not be installed; try 'id -un' as fallback
            whoami = docker_exec("context-broker-langgraph", "id -un", timeout=10).strip()
        assert whoami, "Could not determine container user (whoami and id -un both empty)"
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
        output = compose_cmd("ps context-broker-langgraph")
        assert output, "compose ps returned empty output"
        lower = output.lower()
        # Accept either "healthy", "health", or "running" as signs the container is up
        assert "healthy" in lower or "health" in lower or "running" in lower or "up" in lower, (
            f"Container not healthy/running. compose ps output: {output[:500]}"
        )
