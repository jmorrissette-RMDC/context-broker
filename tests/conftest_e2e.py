"""
Shared fixtures for end-to-end and integration tests.

These tests run against the ACTUAL deployed Context Broker on irina
(192.168.1.110:8080). They are not mocked.

Run with:  pytest tests/test_e2e_*.py tests/test_integration_*.py -m "e2e or integration"
"""

import uuid

import httpx
import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "http://192.168.1.110:8080"
MCP_URL = f"{BASE_URL}/mcp"
CHAT_URL = f"{BASE_URL}/v1/chat/completions"
HEALTH_URL = f"{BASE_URL}/health"
METRICS_URL = f"{BASE_URL}/metrics"

# Direct Postgres connection (exposed on irina's host network)
PG_HOST = "192.168.1.110"
PG_PORT = 5432
PG_USER = "context_broker"
PG_PASSWORD = "contextbroker123"
PG_DATABASE = "context_broker"

# SSH target for container inspection
SSH_HOST = "192.168.1.110"
SSH_USER = "aristotle9"


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def http_client():
    """Session-scoped httpx client with generous timeout."""
    with httpx.Client(base_url=BASE_URL, timeout=180.0) as client:
        yield client


@pytest.fixture(scope="session")
def async_http_client():
    """Session-scoped async httpx client."""
    import asyncio

    client = httpx.AsyncClient(base_url=BASE_URL, timeout=180.0)
    yield client
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(client.aclose())


# ---------------------------------------------------------------------------
# MCP helper
# ---------------------------------------------------------------------------


def mcp_call(
    client: httpx.Client,
    tool_name: str,
    arguments: dict,
    *,
    method: str = "tools/call",
    request_id: int = 1,
) -> httpx.Response:
    """Send an MCP JSON-RPC tool call and return the raw response."""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    return client.post("/mcp", json=payload)


def mcp_call_raw(
    client: httpx.Client,
    payload: dict,
) -> httpx.Response:
    """Send a raw JSON-RPC payload to the MCP endpoint."""
    return client.post("/mcp", json=payload)


def extract_mcp_result(response: httpx.Response) -> dict:
    """Extract the parsed result from an MCP JSON-RPC response.

    The MCP endpoint wraps results in content[0].text as JSON string.
    This helper parses it back to a dict.
    """
    import json

    body = response.json()
    if "error" in body and body["error"] is not None:
        return body
    text = body["result"]["content"][0]["text"]
    return json.loads(text)


# ---------------------------------------------------------------------------
# Conversation factory
# ---------------------------------------------------------------------------


@pytest.fixture
def create_conversation(http_client):
    """Factory fixture: creates a fresh conversation and returns its ID."""
    created_ids = []

    def _create(title: str = "e2e-test") -> str:
        resp = mcp_call(
            http_client,
            "conv_create_conversation",
            {"title": f"{title}-{uuid.uuid4().hex[:8]}"},
        )
        assert resp.status_code == 200, f"Failed to create conversation: {resp.text}"
        result = extract_mcp_result(resp)
        conv_id = result["conversation_id"]
        created_ids.append(conv_id)
        return conv_id

    yield _create
    # No teardown needed — test data in the live system is fine


@pytest.fixture
def create_context_window(http_client):
    """Factory fixture: creates a context window for a conversation."""

    def _create(
        conversation_id: str,
        participant_id: str = "e2e-test-participant",
        build_type: str = "sliding-window",
        max_tokens: int | None = None,
    ) -> str:
        args = {
            "conversation_id": conversation_id,
            "participant_id": participant_id,
            "build_type": build_type,
        }
        if max_tokens is not None:
            args["max_tokens"] = max_tokens
        resp = mcp_call(http_client, "conv_create_context_window", args)
        assert resp.status_code == 200, f"Failed to create context window: {resp.text}"
        result = extract_mcp_result(resp)
        return result["context_window_id"]

    return _create


@pytest.fixture
def store_message(http_client):
    """Factory fixture: stores a message in a conversation."""

    def _store(
        *,
        context_window_id: str | None = None,
        conversation_id: str | None = None,
        role: str = "user",
        sender: str = "e2e-test",
        content: str = "Hello from e2e test",
    ) -> dict:
        args = {"role": role, "sender": sender, "content": content}
        if context_window_id:
            args["context_window_id"] = context_window_id
        if conversation_id:
            args["conversation_id"] = conversation_id
        resp = mcp_call(http_client, "conv_store_message", args)
        assert resp.status_code == 200, f"Failed to store message: {resp.text}"
        return extract_mcp_result(resp)

    return _store
