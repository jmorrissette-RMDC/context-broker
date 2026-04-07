"""
End-to-end resilience tests (T-8.1, T-7.1).

Verifies system resilience and state persistence against the deployed
system at http://192.168.1.110:8080.

These tests do NOT stop containers — they verify observable resilience
behaviors from the client side.
"""

import time
import uuid

import httpx
import pytest

from tests.conftest_e2e import (
    BASE_URL,
    extract_mcp_result,
    mcp_call,
)

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=60.0) as c:
        yield c


# ===================================================================
# T-8.1: Graceful degradation / timeout resilience
# ===================================================================


class TestResilience:
    """Verify system responds within timeout even under load."""

    def test_health_returns_within_timeout(self, client):
        """Health endpoint responds within 10 seconds."""
        start = time.monotonic()
        resp = client.get("/health")
        elapsed = time.monotonic() - start

        assert resp.status_code in (200, 503), f"Unexpected status: {resp.status_code}"
        assert elapsed < 10.0, f"Health took {elapsed:.1f}s — expected < 10s"

    def test_mcp_responds_under_load(self, client):
        """MCP endpoint responds to concurrent-ish tool calls."""
        # Send several calls in sequence and verify all respond in time
        for i in range(5):
            start = time.monotonic()
            resp = mcp_call(client, "conv_search", {"limit": 1})
            elapsed = time.monotonic() - start
            assert resp.status_code == 200, f"Call {i} failed: {resp.text}"
            assert elapsed < 30.0, f"Call {i} took {elapsed:.1f}s"

    def test_metrics_available_under_load(self, client):
        """Metrics endpoint remains responsive after multiple operations."""
        # Do some work first
        for _ in range(3):
            mcp_call(
                client,
                "conv_create_conversation",
                {"title": f"resilience-{uuid.uuid4().hex[:6]}"},
            )
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert len(resp.text) > 0


# ===================================================================
# T-7.1: State persistence
# ===================================================================


class TestStatePersistence:
    """Verify data persists through create-store-retrieve cycle."""

    def test_store_and_retrieve_persists(self, client):
        """Create conversation, store messages, retrieve — all data present."""
        # Create
        conv_id_resp = mcp_call(
            client,
            "conv_create_conversation",
            {"title": f"persist-{uuid.uuid4().hex[:8]}"},
        )
        assert conv_id_resp.status_code == 200
        conv_id = extract_mcp_result(conv_id_resp)["conversation_id"]

        # Store multiple messages
        message_contents = []
        for i in range(3):
            content = f"Persistence test message {i} - {uuid.uuid4().hex[:8]}"
            message_contents.append(content)
            resp = mcp_call(
                client,
                "conv_store_message",
                {
                    "conversation_id": conv_id,
                    "role": "user",
                    "sender": "e2e-persist",
                    "content": content,
                },
            )
            assert resp.status_code == 200

        # Retrieve history
        history_resp = mcp_call(
            client,
            "conv_get_history",
            {"conversation_id": conv_id},
        )
        assert history_resp.status_code == 200
        result = extract_mcp_result(history_resp)
        assert "messages" in result
        assert len(result["messages"]) >= 3

        # Verify each stored message appears in history
        retrieved_contents = [m.get("content", "") for m in result["messages"]]
        for content in message_contents:
            assert (
                content in retrieved_contents
            ), f"Message '{content}' not found in history"

    def test_context_window_persists(self, client):
        """Context window persists and is retrievable after creation."""
        conv_id = extract_mcp_result(
            mcp_call(
                client,
                "conv_create_conversation",
                {"title": f"cw-persist-{uuid.uuid4().hex[:8]}"},
            )
        )["conversation_id"]

        cw_resp = mcp_call(
            client,
            "conv_create_context_window",
            {
                "conversation_id": conv_id,
                "participant_id": "persist-test",
                "build_type": "sliding-window",
            },
        )
        assert cw_resp.status_code == 200
        cw_id = extract_mcp_result(cw_resp)["context_window_id"]

        # Search for it
        search_resp = mcp_call(
            client,
            "conv_search_context_windows",
            {"context_window_id": cw_id},
        )
        assert search_resp.status_code == 200
        result = extract_mcp_result(search_resp)
        assert len(result["context_windows"]) == 1

    def test_conversation_searchable_after_creation(self, client):
        """Created conversation appears in search results."""
        unique_flow = f"persist-flow-{uuid.uuid4().hex[:8]}"
        conv_resp = mcp_call(
            client,
            "conv_create_conversation",
            {
                "title": "Searchable Persistence Test",
                "flow_id": unique_flow,
            },
        )
        assert conv_resp.status_code == 200
        conv_id = extract_mcp_result(conv_resp)["conversation_id"]

        # Search by flow_id
        search_resp = mcp_call(
            client,
            "conv_search",
            {"flow_id": unique_flow, "limit": 5},
        )
        assert search_resp.status_code == 200
        result = extract_mcp_result(search_resp)
        conv_ids = [
            c.get("id", c.get("conversation_id", "")) for c in result["conversations"]
        ]
        assert (
            conv_id in conv_ids
        ), f"Conversation {conv_id} not found in search by flow_id={unique_flow}"
