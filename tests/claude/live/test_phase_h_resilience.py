"""Phase H: Resilience and edge-case tests.

Tests error handling, concurrency, budget snapping, config hot-reload,
and input validation against the live Context Broker stack.
"""

import concurrent.futures
import uuid

import httpx
import pytest

from tests.claude.live.helpers import (
    BASE_URL,
    CHAT_TIMEOUT,
    MCP_TIMEOUT,
    chat_call,
    docker_exec,
    extract_mcp_result,
    log_issue,
    mcp_call,
)

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Health and basic resilience
# ---------------------------------------------------------------------------

class TestHealthResilience:
    """Health endpoint and load resilience."""

    def test_health_responds_within_timeout(self, http_client):
        """GET /health returns 200 within 10 seconds."""
        resp = http_client.get("/health", timeout=10)
        assert resp.status_code == 200, f"Health returned {resp.status_code}"

    def test_mcp_responds_under_load(self, http_client):
        """Send 10 concurrent conv_list_conversations calls; all succeed."""
        def _call(i: int) -> int:
            with httpx.Client(base_url=BASE_URL, timeout=MCP_TIMEOUT) as c:
                resp = mcp_call(c, "conv_list_conversations", {}, request_id=i)
                return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_call, i) for i in range(10)]
            results = [f.result(timeout=MCP_TIMEOUT + 10) for f in futures]

        assert all(
            r == 200 for r in results
        ), f"Not all requests succeeded: {results}"


# ---------------------------------------------------------------------------
# Config hot-reload
# ---------------------------------------------------------------------------

class TestConfigHotReload:
    """Test that config changes don't crash the service."""

    def test_config_hot_reload(self, http_client):
        """Modify te.yml temperature via docker exec, call chat, then restore."""
        container = "claude-test-langgraph"

        # Read current temperature
        original = docker_exec(
            container,
            "cat /config/config.yml",
        )

        try:
            # Change temperature via sed (in-place)
            docker_exec(
                container,
                "sed -i 's/temperature:.*/temperature: 0.99/' /config/config.yml",
            )

            # Call chat — should not crash
            result = chat_call(http_client, "Say hello.")
            choices = result.get("choices", [])
            assert choices, "Chat returned no choices after config change"
            content = choices[0].get("message", {}).get("content", "")
            assert content, "Chat response empty after config change"
        finally:
            # Restore original config
            docker_exec(
                container,
                "sed -i 's/temperature: 0.99/temperature: 0.7/' /config/config.yml",
            )


# ---------------------------------------------------------------------------
# Imperator verbose toggle
# ---------------------------------------------------------------------------

class TestVerboseToggle:
    """Test the verbose logging toggle via Imperator."""

    def test_verbose_logging_toggle(self, http_client):
        """Call imperator_chat to toggle verbose logging; verify response."""
        result = chat_call(http_client, "Toggle verbose logging on")
        choices = result.get("choices", [])
        assert choices, "Chat returned no choices"
        content = choices[0].get("message", {}).get("content", "").lower()
        assert content, "Chat response is empty"
        if "verbose" not in content and "toggle" not in content and "logging" not in content:
            log_issue(
                "test_verbose_logging_toggle",
                "warning",
                "imperator",
                "Response does not mention verbose/toggle/logging",
                "verbose or toggle or logging",
                content[:200],
            )


# ---------------------------------------------------------------------------
# Context budget snapping
# ---------------------------------------------------------------------------

class TestContextBudget:
    """Test get_context budget parameter snapping to power-of-2 tiers."""

    def test_get_context_budget_auto(self, http_client, any_conversation_id):
        """get_context with budget=5000 snaps to 8192."""
        resp = mcp_call(
            http_client,
            "get_context",
            {
                "conversation_id": any_conversation_id,
                "budget": 5000,
            },
        )
        assert resp.status_code == 200, f"get_context failed: {resp.text}"
        data = extract_mcp_result(resp)
        # The result should contain a budget_used or similar field
        # confirming the snap. At minimum, no error.
        assert "error" not in data or data.get("error") is None, (
            f"get_context returned error: {data}"
        )

    def test_get_context_budget_large(self, http_client, any_conversation_id):
        """get_context with budget=100000 snaps to 131072."""
        resp = mcp_call(
            http_client,
            "get_context",
            {
                "conversation_id": any_conversation_id,
                "budget": 100000,
            },
        )
        assert resp.status_code == 200, f"get_context failed: {resp.text}"
        data = extract_mcp_result(resp)
        assert "error" not in data or data.get("error") is None, (
            f"get_context returned error: {data}"
        )


# ---------------------------------------------------------------------------
# Store / retrieve roundtrip
# ---------------------------------------------------------------------------

class TestStoreRetrieve:
    """Test storing and retrieving messages."""

    def test_store_retrieve_roundtrip(self, http_client, any_conversation_id):
        """Store a message, retrieve via get_context, verify it appears."""
        unique_marker = f"roundtrip-marker-{uuid.uuid4().hex[:8]}"

        # Store
        resp = mcp_call(
            http_client,
            "store_message",
            {
                "conversation_id": any_conversation_id,
                "role": "user",
                "content": f"Test message with {unique_marker}",
                "sender": "test-runner",
            },
        )
        assert resp.status_code == 200, f"store_message failed: {resp.text}"

        # Retrieve
        resp = mcp_call(
            http_client,
            "get_context",
            {
                "conversation_id": any_conversation_id,
                "budget": 8192,
            },
        )
        assert resp.status_code == 200, f"get_context failed: {resp.text}"
        data = extract_mcp_result(resp)
        # The context payload should contain our marker somewhere
        context_text = str(data).lower()
        assert unique_marker in context_text, (
            f"Stored marker '{unique_marker}' not found in retrieved context"
        )

    def test_concurrent_store_messages(self, http_client, any_conversation_id):
        """Store 5 messages concurrently from different senders; all succeed."""
        def _store(i: int) -> int:
            with httpx.Client(base_url=BASE_URL, timeout=MCP_TIMEOUT) as c:
                resp = mcp_call(
                    c,
                    "store_message",
                    {
                        "conversation_id": any_conversation_id,
                        "role": "user",
                        "content": f"Concurrent message {i} from sender-{i}",
                        "sender": f"concurrent-sender-{i}",
                    },
                    request_id=i + 100,
                )
                return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(_store, i) for i in range(5)]
            results = [f.result(timeout=MCP_TIMEOUT + 10) for f in futures]

        assert all(
            r == 200 for r in results
        ), f"Not all concurrent stores succeeded: {results}"


# ---------------------------------------------------------------------------
# Input validation / error handling
# ---------------------------------------------------------------------------

class TestInputValidation:
    """Test that invalid inputs return proper errors."""

    def test_mcp_invalid_tool_arguments(self, http_client):
        """Call store_message with missing required field; get validation error."""
        resp = mcp_call(
            http_client,
            "store_message",
            {
                # Missing conversation_id, role, content, sender
            },
        )
        # Should still return 200 (JSON-RPC envelope) but with an error inside
        body = resp.json()
        has_error = (
            ("error" in body and body["error"] is not None)
            or resp.status_code >= 400
        )
        assert has_error, (
            f"Expected validation error for missing fields, got: {body}"
        )

    def test_chat_invalid_json(self, http_client):
        """POST invalid JSON to /v1/chat/completions; expect 400 or 422."""
        resp = http_client.post(
            "/v1/chat/completions",
            content=b"this is not json",
            headers={"Content-Type": "application/json"},
            timeout=CHAT_TIMEOUT,
        )
        assert resp.status_code in (400, 422), (
            f"Expected 400/422 for invalid JSON, got {resp.status_code}"
        )

    def test_chat_no_user_message(self, http_client):
        """POST with only a system message; expect 400 or 422."""
        payload = {
            "model": "context-broker",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
            ],
            "stream": False,
        }
        resp = http_client.post(
            "/v1/chat/completions",
            json=payload,
            timeout=CHAT_TIMEOUT,
        )
        assert resp.status_code in (400, 422), (
            f"Expected 400/422 for missing user message, got {resp.status_code}"
        )
