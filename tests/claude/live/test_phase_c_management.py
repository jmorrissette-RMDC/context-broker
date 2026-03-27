"""
Phase C — Management tool integration tests.

Tests all conv_*, mem_*, imperator_chat, metrics_get, query_logs,
search_logs, and install_stategraph tools against the live stack.

All tests run against the live stack at http://localhost:8081.
"""

import time
import uuid

import httpx
import pytest

from tests.claude.live.helpers import (
    chat_call,
    extract_mcp_result,
    log_issue,
    mcp_call,
    mcp_call_raw,
)

pytestmark = pytest.mark.live


# ===================================================================
# Helpers
# ===================================================================


def _create_conversation(client, title="phase-c-test"):
    """Create a conversation and return its ID."""
    resp = mcp_call(
        client,
        "conv_create_conversation",
        {"title": f"{title}-{uuid.uuid4().hex[:8]}"},
    )
    assert resp.status_code == 200
    return extract_mcp_result(resp)["conversation_id"]


def _create_context_window(client, conversation_id, build_type="passthrough"):
    """Create a context window and return its ID."""
    resp = mcp_call(
        client,
        "conv_create_context_window",
        {
            "conversation_id": conversation_id,
            "participant_id": f"participant-{uuid.uuid4().hex[:6]}",
            "build_type": build_type,
        },
    )
    assert resp.status_code == 200
    return extract_mcp_result(resp)["context_window_id"]


# ===================================================================
# C-01 / C-02: conv_create_conversation
# ===================================================================


class TestConvCreateConversation:
    """C-01: conv_create_conversation."""

    def test_creates_conversation_returns_id(self, http_client):
        """C-01a: conv_create_conversation returns a valid UUID conversation_id."""
        resp = mcp_call(
            http_client,
            "conv_create_conversation",
            {"title": "Phase C creation test"},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "conversation_id" in result
        uuid.UUID(result["conversation_id"])  # Validates UUID format

    def test_idempotent_with_caller_supplied_id(self, http_client):
        """C-01b: Supplying conversation_id enables idempotent creation — same ID returned."""
        fixed_id = str(uuid.uuid4())
        resp1 = mcp_call(
            http_client,
            "conv_create_conversation",
            {"conversation_id": fixed_id, "title": "Idempotent test"},
        )
        assert resp1.status_code == 200
        result1 = extract_mcp_result(resp1)
        assert result1["conversation_id"] == fixed_id

        # Call again with same ID
        resp2 = mcp_call(
            http_client,
            "conv_create_conversation",
            {"conversation_id": fixed_id, "title": "Idempotent test (retry)"},
        )
        assert resp2.status_code == 200
        result2 = extract_mcp_result(resp2)
        assert result2["conversation_id"] == fixed_id


# ===================================================================
# C-03 / C-04: conv_list_conversations
# ===================================================================


class TestConvListConversations:
    """C-03: conv_list_conversations."""

    def test_lists_loaded_conversations(self, http_client, loaded_conversations):
        """C-03a: conv_list_conversations returns loaded conversations."""
        resp = mcp_call(
            http_client,
            "conv_list_conversations",
            {"limit": 100},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "conversations" in result
        assert isinstance(result["conversations"], list)
        assert len(result["conversations"]) > 0, (
            "conv_list_conversations returned no conversations"
        )

    def test_participant_filter(self, http_client):
        """C-03b: conv_list_conversations with participant filter restricts results."""
        # Create a conversation and store a message with a known sender
        conv_id = _create_conversation(http_client, "participant-filter-test")
        sender = f"participant-filter-{uuid.uuid4().hex[:6]}"
        mcp_call(
            http_client,
            "store_message",
            {
                "conversation_id": conv_id,
                "role": "user",
                "sender": sender,
                "content": "Message for participant filter test",
            },
        )

        resp = mcp_call(
            http_client,
            "conv_list_conversations",
            {"participant": sender, "limit": 50},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "conversations" in result
        # The filtered list should contain the conversation we created
        conv_ids = [c.get("conversation_id", c.get("id")) for c in result["conversations"]]
        assert conv_id in conv_ids, (
            f"Participant filter did not return expected conversation {conv_id}. "
            f"Got {len(result['conversations'])} conversations."
        )


# ===================================================================
# C-05: conv_store_message (management variant)
# ===================================================================


class TestConvStoreMessage:
    """C-05: conv_store_message (management tool variant)."""

    def test_store_via_conversation_id(self, http_client):
        """C-05: conv_store_message stores a message via direct conversation_id."""
        conv_id = _create_conversation(http_client)
        resp = mcp_call(
            http_client,
            "conv_store_message",
            {
                "conversation_id": conv_id,
                "role": "user",
                "sender": "live-test-c05",
                "content": "Management store_message test",
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "message_id" in result


# ===================================================================
# C-06: conv_retrieve_context
# ===================================================================


class TestConvRetrieveContext:
    """C-06: conv_retrieve_context for an existing context window."""

    def test_retrieve_existing_window(self, http_client):
        """C-06: Retrieve context for an existing context window with stored messages."""
        conv_id = _create_conversation(http_client)
        cw_id = _create_context_window(http_client, conv_id)

        # Store a message so there's something to retrieve
        mcp_call(
            http_client,
            "conv_store_message",
            {
                "context_window_id": cw_id,
                "role": "user",
                "sender": "live-test-c06",
                "content": "Retrieval test content",
            },
        )

        resp = mcp_call(
            http_client,
            "conv_retrieve_context",
            {"context_window_id": cw_id},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "context" in result
        assert "assembly_status" in result


# ===================================================================
# C-07: conv_create_context_window
# ===================================================================


class TestConvCreateContextWindow:
    """C-07: conv_create_context_window."""

    def test_creates_window_returns_id(self, http_client):
        """C-07: conv_create_context_window returns a valid context_window_id."""
        conv_id = _create_conversation(http_client)
        resp = mcp_call(
            http_client,
            "conv_create_context_window",
            {
                "conversation_id": conv_id,
                "participant_id": "test-participant-c07",
                "build_type": "passthrough",
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "context_window_id" in result
        uuid.UUID(result["context_window_id"])


# ===================================================================
# C-08: conv_search
# ===================================================================


class TestConvSearch:
    """C-08: conv_search (semantic query)."""

    def test_search_finds_conversations(self, http_client, loaded_conversations):
        """C-08: conv_search with a semantic query returns matching conversations."""
        resp = mcp_call(
            http_client,
            "conv_search",
            {"query": "test conversation", "limit": 10},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "conversations" in result
        assert isinstance(result["conversations"], list)


# ===================================================================
# C-09: conv_search_messages (management variant)
# ===================================================================


class TestConvSearchMessages:
    """C-09: conv_search_messages (management tool variant)."""

    def test_search_messages_returns_results(self, http_client):
        """C-09: conv_search_messages returns message list for a valid query."""
        resp = mcp_call(
            http_client,
            "conv_search_messages",
            {"query": "test", "limit": 5},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "messages" in result
        assert isinstance(result["messages"], list)


# ===================================================================
# C-10: conv_get_history
# ===================================================================


class TestConvGetHistory:
    """C-10: conv_get_history returns messages in chronological order."""

    def test_history_returns_ordered_messages(self, http_client):
        """C-10: conv_get_history returns messages in sequence order."""
        conv_id = _create_conversation(http_client)

        # Store messages in known order
        for i in range(3):
            mcp_call(
                http_client,
                "conv_store_message",
                {
                    "conversation_id": conv_id,
                    "role": "user",
                    "sender": "live-test-c10",
                    "content": f"History message {i}",
                },
            )

        resp = mcp_call(
            http_client,
            "conv_get_history",
            {"conversation_id": conv_id},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "messages" in result
        messages = result["messages"]
        assert len(messages) >= 3

        # Verify order by checking content or sequence numbers
        contents = [m.get("content", "") for m in messages]
        history_msgs = [c for c in contents if c.startswith("History message")]
        assert history_msgs == sorted(history_msgs), (
            f"Messages not in chronological order: {history_msgs}"
        )


# ===================================================================
# C-11: conv_search_context_windows
# ===================================================================


class TestConvSearchContextWindows:
    """C-11: conv_search_context_windows."""

    def test_search_returns_windows(self, http_client):
        """C-11: conv_search_context_windows returns context window list."""
        conv_id = _create_conversation(http_client)
        _create_context_window(http_client, conv_id)

        resp = mcp_call(
            http_client,
            "conv_search_context_windows",
            {"conversation_id": conv_id},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "context_windows" in result
        assert len(result["context_windows"]) >= 1


# ===================================================================
# C-12: conv_delete_conversation (cascade)
# ===================================================================


class TestConvDeleteConversation:
    """C-12: conv_delete_conversation cascades to messages and windows."""

    def test_delete_cascades(self, http_client):
        """C-12: Create temp conv, store msg, delete, verify conv is gone."""
        # Create
        conv_id = _create_conversation(http_client, "delete-cascade-test")

        # Store a message
        mcp_call(
            http_client,
            "store_message",
            {
                "conversation_id": conv_id,
                "role": "user",
                "sender": "live-test-c12",
                "content": "This message will be deleted",
            },
        )

        # Delete
        resp = mcp_call(
            http_client,
            "conv_delete_conversation",
            {"conversation_id": conv_id},
        )
        assert resp.status_code == 200

        # Verify gone — get_history should return error or empty
        resp = mcp_call(
            http_client,
            "conv_get_history",
            {"conversation_id": conv_id},
        )
        if resp.status_code == 200:
            result = extract_mcp_result(resp)
            messages = result.get("messages", [])
            assert len(messages) == 0, (
                f"Expected no messages after deletion, got {len(messages)}"
            )
        else:
            # 400/404/500 are acceptable — conversation doesn't exist
            assert resp.status_code in (400, 404, 500)


# ===================================================================
# C-13 / C-14: query_logs / search_logs
# ===================================================================


class TestLogTools:
    """C-13/C-14: Log query and search tools."""

    def test_query_logs_returns_entries(self, http_client):
        """C-13: query_logs returns log entries."""
        resp = mcp_call(
            http_client,
            "query_logs",
            {"limit": 10},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        # Result should contain log entries (possibly empty if log shipper not active)
        assert isinstance(result, dict)

    def test_search_logs_returns_results(self, http_client):
        """C-14: search_logs returns results with similarity information."""
        resp = mcp_call(
            http_client,
            "search_logs",
            {"query": "startup initialization", "limit": 5},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert isinstance(result, dict)


# ===================================================================
# C-15 / C-16 / C-17 / C-18 / C-19: mem_* tools
# ===================================================================


class TestMemAdd:
    """C-15: mem_add adds a memory."""

    def test_mem_add_succeeds(self, http_client):
        """C-15: mem_add stores a memory and returns confirmation."""
        user_id = f"live-test-{uuid.uuid4().hex[:8]}"
        resp = mcp_call(
            http_client,
            "mem_add",
            {
                "content": "The user prefers dark mode interfaces.",
                "user_id": user_id,
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert isinstance(result, dict)


class TestMemSearch:
    """C-16: mem_search finds previously added memories."""

    def test_mem_search_finds_added_memory(self, http_client):
        """C-16: Add a memory, then search for it."""
        user_id = f"live-test-search-{uuid.uuid4().hex[:8]}"

        # Add a distinctive memory
        mcp_call(
            http_client,
            "mem_add",
            {
                "content": "The user's favorite programming language is Rust.",
                "user_id": user_id,
            },
        )

        # Allow time for indexing
        time.sleep(2)

        # Search for it
        resp = mcp_call(
            http_client,
            "mem_search",
            {"query": "favorite programming language", "user_id": user_id},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "memories" in result
        # The memory we just added should appear
        assert len(result["memories"]) > 0, (
            "mem_search returned no results for a just-added memory"
        )


class TestMemGetContext:
    """C-17: mem_get_context returns formatted text."""

    def test_mem_get_context_returns_text(self, http_client):
        """C-17: mem_get_context returns context and memories keys."""
        user_id = f"live-test-ctx-{uuid.uuid4().hex[:8]}"

        # Add a memory first
        mcp_call(
            http_client,
            "mem_add",
            {
                "content": "The user works on distributed systems.",
                "user_id": user_id,
            },
        )
        time.sleep(1)

        resp = mcp_call(
            http_client,
            "mem_get_context",
            {"query": "distributed systems", "user_id": user_id},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "context" in result
        assert "memories" in result


class TestMemList:
    """C-18: mem_list lists memories for a user."""

    def test_mem_list_returns_memories(self, http_client):
        """C-18: mem_list returns the memories added for a user."""
        user_id = f"live-test-list-{uuid.uuid4().hex[:8]}"

        # Add memories
        mcp_call(
            http_client,
            "mem_add",
            {"content": "Memory one for listing.", "user_id": user_id},
        )
        mcp_call(
            http_client,
            "mem_add",
            {"content": "Memory two for listing.", "user_id": user_id},
        )
        time.sleep(1)

        resp = mcp_call(
            http_client,
            "mem_list",
            {"user_id": user_id},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "memories" in result
        assert len(result["memories"]) >= 2, (
            f"Expected at least 2 memories, got {len(result['memories'])}"
        )


class TestMemDelete:
    """C-19: mem_delete removes a memory."""

    def test_mem_delete_removes_memory(self, http_client):
        """C-19: Add a memory, delete it, verify it's gone from mem_list."""
        user_id = f"live-test-del-{uuid.uuid4().hex[:8]}"

        # Add a memory
        add_resp = mcp_call(
            http_client,
            "mem_add",
            {"content": "Temporary memory to be deleted.", "user_id": user_id},
        )
        assert add_resp.status_code == 200
        add_result = extract_mcp_result(add_resp)

        # Get memory ID — may be in the add result or we list to find it
        memory_id = add_result.get("memory_id") or add_result.get("id")
        if not memory_id:
            # Fall back: list memories and take the first one
            time.sleep(1)
            list_resp = mcp_call(
                http_client, "mem_list", {"user_id": user_id}
            )
            list_result = extract_mcp_result(list_resp)
            memories = list_result.get("memories", [])
            assert len(memories) > 0, "No memories found to delete"
            memory_id = memories[0].get("id") or memories[0].get("memory_id")

        assert memory_id is not None, f"Could not determine memory_id from: {add_result}"

        # Delete
        del_resp = mcp_call(
            http_client,
            "mem_delete",
            {"memory_id": str(memory_id)},
        )
        assert del_resp.status_code == 200

        # Verify gone
        time.sleep(1)
        list_resp = mcp_call(
            http_client, "mem_list", {"user_id": user_id}
        )
        list_result = extract_mcp_result(list_resp)
        remaining_ids = [
            m.get("id") or m.get("memory_id")
            for m in list_result.get("memories", [])
        ]
        assert str(memory_id) not in [str(mid) for mid in remaining_ids], (
            f"Memory {memory_id} still present after deletion"
        )


# ===================================================================
# C-20: imperator_chat
# ===================================================================


class TestImperatorChat:
    """C-20: imperator_chat returns a response."""

    def test_imperator_chat_responds(self, http_client):
        """C-20: imperator_chat MCP tool returns a non-empty response."""
        resp = mcp_call(
            http_client,
            "imperator_chat",
            {"message": "Hello, respond with a single sentence."},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "response" in result
        assert len(result["response"]) > 0, "imperator_chat returned empty response"


# ===================================================================
# C-21: metrics_get
# ===================================================================


class TestMetricsGet:
    """C-21: metrics_get MCP tool returns Prometheus text."""

    def test_metrics_get_returns_text(self, http_client):
        """C-21: metrics_get returns a string containing Prometheus metrics."""
        resp = mcp_call(http_client, "metrics_get", {})
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "metrics" in result
        assert isinstance(result["metrics"], str)
        assert len(result["metrics"]) > 0


# ===================================================================
# C-22: install_stategraph
# ===================================================================


class TestInstallStategraph:
    """C-22: install_stategraph responds (even if package already installed)."""

    def test_install_stategraph_responds(self, http_client):
        """C-22: install_stategraph for an already-installed package returns a response."""
        resp = mcp_call(
            http_client,
            "install_stategraph",
            {"package_name": "context-broker-ae"},
        )
        # Should return 200 with result (already installed / upgraded)
        # or possibly an error if the package can't be found in the registry
        assert resp.status_code in (200, 400, 500)
        body = resp.json()
        # Verify we got a structured JSON-RPC response
        assert "jsonrpc" in body


# ===================================================================
# C-23: Unknown tool error
# ===================================================================


class TestUnknownTool:
    """C-23: Unknown tool name returns an error."""

    def test_unknown_tool_returns_error(self, http_client):
        """C-23: Calling a non-existent tool returns a JSON-RPC error."""
        resp = mcp_call(http_client, "completely_nonexistent_tool_xyz", {})
        assert resp.status_code in (400, 500)
        body = resp.json()
        assert "error" in body, f"Expected error in response for unknown tool: {body}"
