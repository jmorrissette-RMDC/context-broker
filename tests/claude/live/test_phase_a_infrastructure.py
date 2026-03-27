"""
Phase A — Infrastructure integration tests.

Validates that the Context Broker Docker Compose stack is correctly deployed,
all services are healthy, and the MCP protocol layer functions correctly.

All tests run against the live stack at http://localhost:8081.
"""

import time
import uuid

import httpx
import pytest

from tests.claude.live.helpers import (
    docker_logs,
    docker_psql,
    extract_mcp_result,
    log_issue,
    mcp_call,
    mcp_call_raw,
)

pytestmark = pytest.mark.live


# ===================================================================
# A-01: GET /health
# ===================================================================


class TestHealthEndpoint:
    """A-01: Health endpoint returns 200 with dependency status."""

    def test_health_returns_200(self, http_client):
        """A-01a: GET /health returns 200 when the stack is healthy."""
        resp = http_client.get("/health")
        assert resp.status_code == 200, f"Health check failed: {resp.status_code}"

    def test_health_contains_postgres_status(self, http_client):
        """A-01b: Health response includes postgres/database status."""
        resp = http_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        body_str = str(body).lower()
        assert "database" in body or "postgres" in body_str, (
            f"No postgres/database status in health response: {body}"
        )

    def test_health_contains_neo4j_status(self, http_client):
        """A-01c: Health response includes neo4j status."""
        resp = http_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        body_str = str(body).lower()
        assert "neo4j" in body_str, (
            f"No neo4j status in health response: {body}"
        )


# ===================================================================
# A-02: GET /metrics
# ===================================================================


class TestMetricsEndpoint:
    """A-02: Metrics endpoint returns Prometheus-format data."""

    def test_metrics_returns_200(self, http_client):
        """A-02a: GET /metrics returns 200."""
        resp = http_client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_prometheus_format(self, http_client):
        """A-02b: Metrics response uses Prometheus exposition format (text/plain or openmetrics)."""
        resp = http_client.get("/metrics")
        content_type = resp.headers.get("content-type", "")
        assert any(
            ct in content_type for ct in ("text/plain", "text/", "openmetrics")
        ), f"Unexpected content-type for metrics: {content_type}"

    def test_metrics_contains_mcp_counters(self, http_client):
        """A-02c: Metrics include MCP-related counters after exercising the endpoint."""
        # Make an MCP call to ensure counters are populated
        mcp_call_raw(
            http_client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        resp = http_client.get("/metrics")
        text = resp.text
        assert (
            "mcp_request" in text.lower() or "context_broker" in text.lower()
        ), f"No MCP counters in metrics output (first 500 chars): {text[:500]}"

    def test_metrics_contains_help_and_type(self, http_client):
        """A-02d: Metrics include # HELP and # TYPE Prometheus annotations."""
        resp = http_client.get("/metrics")
        text = resp.text
        lines = text.splitlines()
        has_help = any(line.startswith("# HELP") for line in lines)
        has_type = any(line.startswith("# TYPE") for line in lines)
        assert has_help, "No # HELP annotations found in metrics"
        assert has_type, "No # TYPE annotations found in metrics"


# ===================================================================
# A-03: MCP protocol handshake
# ===================================================================


class TestMCPInitialize:
    """A-03: MCP initialize handshake."""

    def test_initialize_returns_protocol_version(self, http_client):
        """A-03a: MCP initialize returns protocolVersion 2024-11-05."""
        resp = mcp_call_raw(
            http_client,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"]["protocolVersion"] == "2024-11-05"

    def test_initialize_returns_capabilities(self, http_client):
        """A-03b: MCP initialize includes tools in capabilities."""
        resp = mcp_call_raw(
            http_client,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        body = resp.json()
        assert "tools" in body["result"]["capabilities"]
        assert body["result"]["serverInfo"]["name"] == "context-broker"


# ===================================================================
# A-04: MCP tools/list
# ===================================================================


class TestMCPToolsList:
    """A-04: MCP tools/list returns registered tools with schemas."""

    def test_tools_list_returns_tools(self, http_client):
        """A-04a: tools/list returns a non-empty list of tools with inputSchema."""
        resp = mcp_call_raw(
            http_client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert resp.status_code == 200
        tools = resp.json()["result"]["tools"]
        assert len(tools) > 0, "tools/list returned empty tool list"
        # Each tool must have name and inputSchema
        for tool in tools:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "inputSchema" in tool, f"Tool '{tool['name']}' missing 'inputSchema'"


# ===================================================================
# A-05: MCP error handling
# ===================================================================


class TestMCPErrorHandling:
    """A-05: MCP protocol error handling."""

    def test_unknown_method_returns_32601(self, http_client):
        """A-05a: Unknown method returns JSON-RPC -32601 (Method not found)."""
        resp = mcp_call_raw(
            http_client,
            {"jsonrpc": "2.0", "id": 1, "method": "nonexistent/method", "params": {}},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32601

    def test_malformed_json_returns_32700(self, http_client):
        """A-05b: Malformed JSON body returns JSON-RPC -32700 (Parse error)."""
        resp = http_client.post(
            "/mcp",
            content=b"this is not json{{{",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == -32700


# ===================================================================
# A-06: Sessionless tool call
# ===================================================================


class TestSessionlessToolCall:
    """A-06: Sessionless MCP tool calls work without SSE session."""

    def test_sessionless_tool_call_works(self, http_client):
        """A-06: POST /mcp with tools/call (no sessionId) returns a result directly."""
        resp = mcp_call(
            http_client,
            "conv_create_conversation",
            {"title": "sessionless-test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "result" in body
        assert "content" in body["result"]


# ===================================================================
# A-07: SSE session establishment
# ===================================================================


class TestSSESession:
    """A-07: SSE session establishment via GET /mcp."""

    def test_sse_returns_event_stream(self, http_client):
        """A-07: GET /mcp returns content-type text/event-stream with session data."""
        # Use httpx stream to verify the SSE connection starts
        with httpx.Client(base_url=http_client._base_url, timeout=10.0) as client:
            with client.stream("GET", "/mcp") as resp:
                assert resp.status_code == 200
                content_type = resp.headers.get("content-type", "")
                assert "text/event-stream" in content_type, (
                    f"Expected text/event-stream, got {content_type}"
                )
                # Read the first event (should contain sessionId)
                first_chunk = b""
                for chunk in resp.iter_bytes():
                    first_chunk += chunk
                    if b"\n\n" in first_chunk:
                        break
                first_text = first_chunk.decode("utf-8", errors="replace")
                assert "sessionId" in first_text, (
                    f"First SSE event should contain sessionId, got: {first_text[:200]}"
                )


# ===================================================================
# A-08: HTTP exception handlers
# ===================================================================


class TestHTTPExceptionHandlers:
    """A-08: Structured JSON error responses."""

    def test_http_exception_returns_json(self, http_client):
        """A-08a: Non-existent route returns structured JSON error."""
        resp = http_client.get("/nonexistent-route-for-testing")
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body or "error" in body or "message" in body

    def test_validation_error_returns_422(self, http_client):
        """A-08b: Invalid payload to a validated endpoint returns 422."""
        # POST to /v1/chat/completions with invalid payload (missing messages)
        resp = http_client.post(
            "/v1/chat/completions",
            json={"model": "context-broker"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body


# ===================================================================
# A-09: Package discovery — AE tools
# ===================================================================


class TestAEPackageDiscovery:
    """A-09: AE package discovered — conv_* tools appear in tools/list."""

    def test_ae_tools_present(self, http_client):
        """A-09: tools/list contains conv_* management tools from the AE package."""
        resp = mcp_call_raw(
            http_client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        tools = resp.json()["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        ae_tools = {
            "conv_create_conversation",
            "conv_store_message",
            "conv_retrieve_context",
            "conv_create_context_window",
            "conv_search",
            "conv_search_messages",
            "conv_get_history",
        }
        missing = ae_tools - tool_names
        assert not missing, f"AE tools missing from tools/list: {missing}"


# ===================================================================
# A-10: Package discovery — TE tools
# ===================================================================


class TestTEPackageDiscovery:
    """A-10: TE package discovered — imperator_chat appears in tools/list."""

    def test_te_tools_present(self, http_client):
        """A-10: tools/list contains imperator_chat from the TE package."""
        resp = mcp_call_raw(
            http_client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        tools = resp.json()["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        assert "imperator_chat" in tool_names, (
            f"imperator_chat not found in tools/list. Available: {sorted(tool_names)}"
        )


# ===================================================================
# A-11: Build types in tools/list schema
# ===================================================================


class TestBuildTypesInSchema:
    """A-11: Three build types appear in the get_context tool schema."""

    def test_three_build_types_listed(self, http_client):
        """A-11: get_context inputSchema enum contains passthrough, standard-tiered, knowledge-enriched."""
        resp = mcp_call_raw(
            http_client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        tools = resp.json()["result"]["tools"]
        get_context_tool = next(
            (t for t in tools if t["name"] == "get_context"), None
        )
        assert get_context_tool is not None, "get_context tool not found in tools/list"

        build_type_prop = get_context_tool["inputSchema"]["properties"]["build_type"]
        enum_values = build_type_prop.get("enum", [])
        expected = {"passthrough", "standard-tiered", "knowledge-enriched"}
        assert expected.issubset(set(enum_values)), (
            f"Expected build types {expected} in enum, got {enum_values}"
        )


# ===================================================================
# A-12: Imperator available
# ===================================================================


class TestImperatorAvailable:
    """A-12: Imperator responds to chat."""

    def test_imperator_responds_to_hello(self, http_client):
        """A-12: /v1/chat/completions with 'Hello' returns a non-empty response."""
        resp = http_client.post(
            "/v1/chat/completions",
            json={
                "model": "context-broker",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
            },
            timeout=120.0,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "choices" in body
        assert len(body["choices"]) > 0
        content = body["choices"][0]["message"]["content"]
        assert len(content) > 0, "Imperator returned empty response"


# ===================================================================
# A-13: Migrations ran
# ===================================================================


class TestMigrationsRan:
    """A-13: Schema migrations completed on startup."""

    def test_migration_log_message(self, http_client):
        """A-13: Docker logs contain migration completion message."""
        logs = docker_logs("context-broker-langgraph", lines=200)
        assert (
            "Schema migrations complete" in logs
            or "Schema is up to date" in logs
            or "migrations" in logs.lower()
        ), (
            f"No migration completion message found in container logs. "
            f"Last 200 chars: ...{logs[-200:]}"
        )


# ===================================================================
# A-14 / A-15: Postgres and Neo4j health checks
# ===================================================================


class TestDatabaseHealthChecks:
    """A-14/A-15: Individual database health checks pass."""

    def test_postgres_health_check(self, http_client):
        """A-14: Postgres is reachable and responds to queries."""
        result = docker_psql("SELECT 1")
        assert "1" in result, f"Postgres health check failed: {result}"

    def test_neo4j_health_check(self, http_client):
        """A-15: Neo4j is reported healthy in the /health response."""
        resp = http_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        body_str = str(body).lower()
        # Verify neo4j is not reported as down/unhealthy
        assert "neo4j" in body_str, "Neo4j not mentioned in health response"
        # If there's a structured status, check it's not 'down' or 'error'
        if isinstance(body, dict):
            for key, val in body.items():
                if "neo4j" in key.lower():
                    val_str = str(val).lower()
                    assert "down" not in val_str and "error" not in val_str, (
                        f"Neo4j health check reports unhealthy: {key}={val}"
                    )
