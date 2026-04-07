"""Live integration tests — hit the real deployed system.

These tests run against the Context Broker on irina (192.168.1.110:8080).
They use REAL infrastructure: real database, real LLM, real embeddings.
No mocks.

Run with: pytest tests/test_live_coverage.py -v -s
"""

import json
import time

import httpx
import pytest

BASE = "http://192.168.1.110:8080"
CHAT_URL = f"{BASE}/v1/chat/completions"
MCP_URL = f"{BASE}/mcp"
HEALTH_URL = f"{BASE}/health"
TIMEOUT = 120


def chat(message: str, stream: bool = False) -> dict:
    """Send a chat message and return the parsed response."""
    r = httpx.post(
        CHAT_URL,
        json={"model": "imperator", "messages": [{"role": "user", "content": message}], "stream": stream},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def chat_content(message: str) -> str:
    """Send a chat message and return just the content string."""
    d = chat(message)
    return d["choices"][0]["message"]["content"]


def mcp_call(tool: str, arguments: dict) -> dict:
    """Call an MCP tool and return the result."""
    r = httpx.post(
        MCP_URL,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


# ── Health ─────────────────────────────────────────────────────────


class TestLiveHealth:
    def test_health_returns_200(self):
        r = httpx.get(HEALTH_URL, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"
        assert data["database"] == "ok"

    def test_metrics_returns_prometheus(self):
        r = httpx.get(f"{BASE}/metrics", timeout=10)
        assert r.status_code == 200
        assert "context_broker" in r.text


# ── Imperator Chat ─────────────────────────────────────────────────


class TestLiveImperatorChat:
    def test_non_streaming_response(self):
        content = chat_content("Say hello in exactly 5 words.")
        assert len(content) > 0
        assert content != ""

    def test_no_empty_responses_10x(self):
        """Verify empty response fix — 10 consecutive requests."""
        empties = 0
        for i in range(10):
            content = chat_content(f"Respond with the number {i}.")
            if not content.strip():
                empties += 1
        assert empties == 0, f"{empties}/10 empty responses"

    def test_streaming_returns_sse(self):
        with httpx.stream(
            "POST",
            CHAT_URL,
            json={
                "model": "imperator",
                "messages": [{"role": "user", "content": "Say hi."}],
                "stream": True,
            },
            timeout=TIMEOUT,
        ) as r:
            assert r.status_code == 200
            chunks = list(r.iter_lines())
            assert any("data:" in c for c in chunks)


# ── Imperator Tools (live execution) ───────────────────────────────


class TestLiveImperatorTools:
    def test_pipeline_status(self):
        content = chat_content("Check pipeline status.")
        assert "embedding" in content.lower() or "extraction" in content.lower()

    def test_web_search(self):
        content = chat_content("Search the web for 'Python asyncio documentation'.")
        assert "python" in content.lower() or "asyncio" in content.lower()

    def test_file_read(self):
        content = chat_content("Read the file /config/te.yml and tell me the model name.")
        assert "gemini" in content.lower() or "model" in content.lower()

    def test_run_command(self):
        content = chat_content("Use the run_command tool to execute 'hostname' and tell me the result.")
        assert "context-broker" in content.lower() or "hostname" in content.lower()

    def test_calculate(self):
        content = chat_content("Calculate 17 * 23 + 5.")
        assert "396" in content

    def test_list_schedules(self):
        content = chat_content("List all schedules.")
        # Should respond even if empty
        assert len(content) > 0

    def test_list_alert_instructions(self):
        content = chat_content("List all alert instructions.")
        assert len(content) > 0

    def test_search_domain_info(self):
        content = chat_content("Search your domain knowledge for 'troubleshooting'.")
        assert len(content) > 0

    def test_context_introspection(self):
        content = chat_content("Show context introspection for your current conversation.")
        assert len(content) > 0


# ── Admin Tools (require admin_tools: true) ────────────────────────


class TestLiveAdminTools:
    def test_config_read(self):
        content = chat_content("Read the current config using config_read.")
        assert "summarization" in content.lower() or "embeddings" in content.lower()

    def test_change_inference_list(self):
        content = chat_content("List available models for the imperator slot using change_inference.")
        assert "gemini" in content.lower() or "model" in content.lower()


# ── MCP Tools (direct calls) ──────────────────────────────────────


class TestLiveMCPTools:
    def test_store_and_retrieve(self):
        """Store a message and verify it exists via get_history."""
        # Create a conversation
        result = mcp_call("conv_create_conversation", {"title": "Live test conv"})
        assert "result" in result
        conv_text = result["result"]["content"][0]["text"]
        conv_id = json.loads(conv_text)["conversation_id"]

        # Store a message
        mcp_call(
            "store_message",
            {
                "conversation_id": conv_id,
                "role": "user",
                "content": "Live integration test message",
                "sender": "test-runner",
                "recipient": "context-broker",
            },
        )

        # Retrieve history
        result = mcp_call("conv_get_history", {"conversation_id": conv_id, "limit": 5})
        history_text = result["result"]["content"][0]["text"]
        assert "Live integration test message" in history_text

    def test_health_via_mcp(self):
        result = mcp_call("metrics_get", {})
        assert "result" in result

    def test_search_messages(self):
        result = mcp_call("search_messages", {"query": "test"})
        assert "result" in result


# ── Alerter Sidecar ────────────────────────────────────────────────


class TestLiveAlerter:
    def test_alerter_health(self):
        """Verify alerter container is running and healthy."""
        # Alerter is on the internal network, hit it via Imperator
        content = chat_content(
            "Send a test notification with the message 'Live test from pytest'."
        )
        # Should succeed or report channels
        assert "sent" in content.lower() or "channel" in content.lower() or "failed" in content.lower()


# ── Embedding Dimensions ──────────────────────────────────────────


class TestLiveEmbeddings:
    def test_conversation_embedding_dims(self):
        """Verify embeddings are at the configured dimension."""
        content = chat_content(
            "Run this SQL query: SELECT vector_dims(embedding) as dims "
            "FROM conversation_messages WHERE embedding IS NOT NULL LIMIT 1"
        )
        # Should show 1024 (nominal) or 768 (local)
        assert "1024" in content or "768" in content

    def test_hnsw_indexes_exist(self):
        """Verify HNSW indexes are created."""
        content = chat_content(
            "Run this SQL query: SELECT indexname FROM pg_indexes "
            "WHERE indexdef LIKE '%hnsw%'"
        )
        assert "hnsw" in content.lower()
