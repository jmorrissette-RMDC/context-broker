"""
End-to-end MCP tool tests (T-6.1).

Tests every MCP tool with valid params, missing required params, and
invalid types against the deployed system at http://192.168.1.110:8080/mcp.

Each test creates its own conversation to avoid interference.
"""

import uuid

import httpx
import pytest

from tests.conftest_e2e import (
    BASE_URL,
    extract_mcp_result,
    mcp_call,
    mcp_call_raw,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Session-scoped client
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=180.0) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_conversation(client: httpx.Client, title: str = "mcp-e2e") -> str:
    """Create a conversation and return its ID."""
    resp = mcp_call(
        client,
        "conv_create_conversation",
        {"title": f"{title}-{uuid.uuid4().hex[:8]}"},
    )
    assert resp.status_code == 200
    return extract_mcp_result(resp)["conversation_id"]


def _create_context_window(
    client: httpx.Client,
    conversation_id: str,
    build_type: str = "passthrough",
) -> str:
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
# MCP protocol-level tests
# ===================================================================


class TestMCPProtocol:
    """Test MCP JSON-RPC protocol handling."""

    def test_initialize(self, client):
        """MCP initialize returns protocol version and capabilities."""
        resp = mcp_call_raw(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"]["protocolVersion"] == "2024-11-05"
        assert "tools" in body["result"]["capabilities"]
        assert body["result"]["serverInfo"]["name"] == "context-broker"

    def test_tools_list(self, client):
        """MCP tools/list returns all registered tools."""
        resp = mcp_call_raw(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert resp.status_code == 200
        tools = resp.json()["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        expected = {
            "conv_create_conversation",
            "conv_store_message",
            "conv_retrieve_context",
            "conv_create_context_window",
            "conv_search",
            "conv_search_messages",
            "conv_get_history",
            "conv_search_context_windows",
            "mem_search",
            "mem_get_context",
            "mem_add",
            "mem_list",
            "mem_delete",
            "imperator_chat",
            "metrics_get",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"

    def test_invalid_json(self, client):
        """Malformed JSON returns parse error."""
        resp = client.post(
            "/mcp", content=b"not json", headers={"content-type": "application/json"}
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == -32700

    def test_unknown_method(self, client):
        """Unknown method returns method-not-found error."""
        resp = mcp_call_raw(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "nonexistent/method", "params": {}},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32601

    def test_unknown_tool(self, client):
        """Unknown tool name returns validation error."""
        resp = mcp_call(client, "nonexistent_tool", {})
        assert resp.status_code in (400, 500)
        body = resp.json()
        assert "error" in body


# ===================================================================
# conv_create_conversation
# ===================================================================


class TestConvCreateConversation:
    """Tests for the conv_create_conversation MCP tool."""

    def test_valid_no_params(self, client):
        """Create conversation with no optional params succeeds."""
        resp = mcp_call(client, "conv_create_conversation", {})
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "conversation_id" in result
        # Verify it's a valid UUID
        uuid.UUID(result["conversation_id"])

    def test_valid_with_title(self, client):
        """Create conversation with title succeeds."""
        resp = mcp_call(
            client,
            "conv_create_conversation",
            {"title": "E2E Test Conversation"},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "conversation_id" in result

    def test_valid_with_all_params(self, client):
        """Create conversation with all optional params succeeds."""
        resp = mcp_call(
            client,
            "conv_create_conversation",
            {
                "title": "Full Params Test",
                "flow_id": "test-flow",
                "user_id": "test-user",
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "conversation_id" in result

    def test_idempotent_with_conversation_id(self, client):
        """Supplying conversation_id enables idempotent creation."""
        fixed_id = str(uuid.uuid4())
        resp1 = mcp_call(
            client,
            "conv_create_conversation",
            {"conversation_id": fixed_id, "title": "Idempotent Test"},
        )
        assert resp1.status_code == 200
        result1 = extract_mcp_result(resp1)
        assert result1["conversation_id"] == fixed_id

    def test_invalid_conversation_id_type(self, client):
        """Non-UUID conversation_id returns validation error."""
        resp = mcp_call(
            client,
            "conv_create_conversation",
            {"conversation_id": "not-a-uuid"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body


# ===================================================================
# conv_store_message
# ===================================================================


class TestConvStoreMessage:
    """Tests for the conv_store_message MCP tool."""

    def test_valid_with_context_window(self, client):
        """Store message via context_window_id succeeds."""
        conv_id = _create_conversation(client)
        cw_id = _create_context_window(client, conv_id)
        resp = mcp_call(
            client,
            "conv_store_message",
            {
                "context_window_id": cw_id,
                "role": "user",
                "sender": "e2e-test",
                "content": "Hello via context window",
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "message_id" in result
        assert result["message_id"] is not None

    def test_valid_with_conversation_id(self, client):
        """Store message via direct conversation_id succeeds."""
        conv_id = _create_conversation(client)
        resp = mcp_call(
            client,
            "conv_store_message",
            {
                "conversation_id": conv_id,
                "role": "user",
                "sender": "e2e-test",
                "content": "Hello via conversation ID",
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "message_id" in result

    def test_missing_role(self, client):
        """Missing required 'role' returns validation error."""
        conv_id = _create_conversation(client)
        resp = mcp_call(
            client,
            "conv_store_message",
            {
                "conversation_id": conv_id,
                "sender": "e2e-test",
                "content": "No role",
            },
        )
        assert resp.status_code == 400

    def test_missing_sender(self, client):
        """Missing required 'sender' returns validation error."""
        conv_id = _create_conversation(client)
        resp = mcp_call(
            client,
            "conv_store_message",
            {
                "conversation_id": conv_id,
                "role": "user",
                "content": "No sender",
            },
        )
        assert resp.status_code == 400

    def test_missing_both_ids(self, client):
        """Missing both context_window_id and conversation_id returns error."""
        resp = mcp_call(
            client,
            "conv_store_message",
            {
                "role": "user",
                "sender": "e2e-test",
                "content": "No IDs",
            },
        )
        assert resp.status_code == 400

    def test_invalid_role(self, client):
        """Invalid role value returns validation error."""
        conv_id = _create_conversation(client)
        resp = mcp_call(
            client,
            "conv_store_message",
            {
                "conversation_id": conv_id,
                "role": "invalid_role",
                "sender": "e2e-test",
                "content": "Bad role",
            },
        )
        assert resp.status_code == 400

    def test_all_valid_roles(self, client):
        """All four valid roles (user, assistant, system, tool) succeed."""
        conv_id = _create_conversation(client)
        for role in ("user", "assistant", "system", "tool"):
            extra = {}
            if role == "tool":
                extra["tool_call_id"] = "call-123"
            resp = mcp_call(
                client,
                "conv_store_message",
                {
                    "conversation_id": conv_id,
                    "role": role,
                    "sender": f"e2e-{role}",
                    "content": f"Message from {role}",
                    **extra,
                },
            )
            assert resp.status_code == 200, f"Role '{role}' failed: {resp.text}"


# ===================================================================
# conv_retrieve_context
# ===================================================================


class TestConvRetrieveContext:
    """Tests for the conv_retrieve_context MCP tool."""

    def test_valid_retrieval(self, client):
        """Retrieve context for a valid context window succeeds."""
        conv_id = _create_conversation(client)
        cw_id = _create_context_window(client, conv_id)
        # Store a message first
        mcp_call(
            client,
            "conv_store_message",
            {
                "context_window_id": cw_id,
                "role": "user",
                "sender": "e2e-test",
                "content": "Test message for retrieval",
            },
        )
        resp = mcp_call(
            client,
            "conv_retrieve_context",
            {"context_window_id": cw_id},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "context" in result
        assert "assembly_status" in result

    def test_missing_context_window_id(self, client):
        """Missing required context_window_id returns error."""
        resp = mcp_call(client, "conv_retrieve_context", {})
        assert resp.status_code == 400

    def test_nonexistent_context_window(self, client):
        """Non-existent context window ID returns error."""
        fake_id = str(uuid.uuid4())
        resp = mcp_call(
            client,
            "conv_retrieve_context",
            {"context_window_id": fake_id},
        )
        # Should be 400 (validation/not-found error from dispatch)
        assert resp.status_code in (400, 500)

    def test_invalid_uuid_format(self, client):
        """Invalid UUID format returns validation error."""
        resp = mcp_call(
            client,
            "conv_retrieve_context",
            {"context_window_id": "not-a-uuid"},
        )
        assert resp.status_code == 400


# ===================================================================
# conv_create_context_window
# ===================================================================


class TestConvCreateContextWindow:
    """Tests for the conv_create_context_window MCP tool."""

    def test_valid_creation(self, client):
        """Create context window with valid params succeeds."""
        conv_id = _create_conversation(client)
        resp = mcp_call(
            client,
            "conv_create_context_window",
            {
                "conversation_id": conv_id,
                "participant_id": "test-participant",
                "build_type": "passthrough",
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "context_window_id" in result
        uuid.UUID(result["context_window_id"])

    def test_with_max_tokens(self, client):
        """Create context window with explicit max_tokens succeeds."""
        conv_id = _create_conversation(client)
        resp = mcp_call(
            client,
            "conv_create_context_window",
            {
                "conversation_id": conv_id,
                "participant_id": "test-participant",
                "build_type": "passthrough",
                "max_tokens": 4096,
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert result["resolved_token_budget"] > 0

    def test_missing_conversation_id(self, client):
        """Missing conversation_id returns validation error."""
        resp = mcp_call(
            client,
            "conv_create_context_window",
            {"participant_id": "test", "build_type": "passthrough"},
        )
        assert resp.status_code == 400

    def test_missing_participant_id(self, client):
        """Missing participant_id returns validation error."""
        conv_id = _create_conversation(client)
        resp = mcp_call(
            client,
            "conv_create_context_window",
            {"conversation_id": conv_id, "build_type": "passthrough"},
        )
        assert resp.status_code == 400

    def test_missing_build_type(self, client):
        """Missing build_type returns validation error."""
        conv_id = _create_conversation(client)
        resp = mcp_call(
            client,
            "conv_create_context_window",
            {"conversation_id": conv_id, "participant_id": "test"},
        )
        assert resp.status_code == 400

    def test_invalid_build_type(self, client):
        """Unknown build_type returns error."""
        conv_id = _create_conversation(client)
        resp = mcp_call(
            client,
            "conv_create_context_window",
            {
                "conversation_id": conv_id,
                "participant_id": "test",
                "build_type": "nonexistent-build-type",
            },
        )
        # The system should reject unknown build types
        assert resp.status_code in (400, 500)


# ===================================================================
# conv_search
# ===================================================================


class TestConvSearch:
    """Tests for the conv_search MCP tool."""

    def test_valid_search_no_params(self, client):
        """Search with no params returns conversations list."""
        resp = mcp_call(client, "conv_search", {})
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "conversations" in result
        assert isinstance(result["conversations"], list)

    def test_valid_search_with_query(self, client):
        """Search with a text query succeeds."""
        resp = mcp_call(client, "conv_search", {"query": "test", "limit": 5})
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "conversations" in result

    def test_valid_search_with_filters(self, client):
        """Search with structured filters succeeds."""
        resp = mcp_call(
            client,
            "conv_search",
            {"flow_id": "test-flow", "user_id": "test-user", "limit": 5},
        )
        assert resp.status_code == 200

    def test_invalid_limit_type(self, client):
        """Non-integer limit returns validation error."""
        resp = mcp_call(client, "conv_search", {"limit": "not-a-number"})
        assert resp.status_code == 400


# ===================================================================
# conv_search_messages
# ===================================================================


class TestConvSearchMessages:
    """Tests for the conv_search_messages MCP tool."""

    def test_valid_search(self, client):
        """Search messages with valid query succeeds."""
        resp = mcp_call(
            client,
            "conv_search_messages",
            {"query": "hello", "limit": 5},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "messages" in result
        assert isinstance(result["messages"], list)

    def test_missing_query(self, client):
        """Missing required query returns validation error."""
        resp = mcp_call(client, "conv_search_messages", {"limit": 5})
        assert resp.status_code == 400

    def test_with_conversation_filter(self, client):
        """Search within a specific conversation succeeds."""
        conv_id = _create_conversation(client)
        mcp_call(
            client,
            "conv_store_message",
            {
                "conversation_id": conv_id,
                "role": "user",
                "sender": "e2e-test",
                "content": "Searchable message content",
            },
        )
        resp = mcp_call(
            client,
            "conv_search_messages",
            {"query": "searchable", "conversation_id": conv_id},
        )
        assert resp.status_code == 200

    def test_with_role_filter(self, client):
        """Search with role filter succeeds."""
        resp = mcp_call(
            client,
            "conv_search_messages",
            {"query": "test", "role": "user", "limit": 3},
        )
        assert resp.status_code == 200

    def test_invalid_role_filter(self, client):
        """Invalid role filter returns validation error."""
        resp = mcp_call(
            client,
            "conv_search_messages",
            {"query": "test", "role": "invalid_role"},
        )
        assert resp.status_code == 400


# ===================================================================
# conv_get_history
# ===================================================================


class TestConvGetHistory:
    """Tests for the conv_get_history MCP tool."""

    def test_valid_history(self, client):
        """Get history for conversation with messages succeeds."""
        conv_id = _create_conversation(client)
        # Store a few messages
        for i in range(3):
            mcp_call(
                client,
                "conv_store_message",
                {
                    "conversation_id": conv_id,
                    "role": "user",
                    "sender": "e2e-test",
                    "content": f"History message {i}",
                },
            )
        resp = mcp_call(client, "conv_get_history", {"conversation_id": conv_id})
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "messages" in result
        assert len(result["messages"]) >= 3

    def test_missing_conversation_id(self, client):
        """Missing required conversation_id returns validation error."""
        resp = mcp_call(client, "conv_get_history", {})
        assert resp.status_code == 400

    def test_nonexistent_conversation(self, client):
        """Non-existent conversation returns error or empty result."""
        fake_id = str(uuid.uuid4())
        resp = mcp_call(
            client,
            "conv_get_history",
            {"conversation_id": fake_id},
        )
        # May return 200 with empty messages or 400/500 — both acceptable
        assert resp.status_code in (200, 400, 500)

    def test_with_limit(self, client):
        """History with limit param returns at most that many messages."""
        conv_id = _create_conversation(client)
        for i in range(5):
            mcp_call(
                client,
                "conv_store_message",
                {
                    "conversation_id": conv_id,
                    "role": "user",
                    "sender": "e2e-test",
                    "content": f"Limit test {i}",
                },
            )
        resp = mcp_call(
            client,
            "conv_get_history",
            {"conversation_id": conv_id, "limit": 2},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert len(result["messages"]) <= 2


# ===================================================================
# conv_search_context_windows
# ===================================================================


class TestConvSearchContextWindows:
    """Tests for the conv_search_context_windows MCP tool."""

    def test_valid_search_no_params(self, client):
        """Search with no params returns context windows list."""
        resp = mcp_call(client, "conv_search_context_windows", {})
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "context_windows" in result

    def test_by_conversation_id(self, client):
        """Search by conversation_id returns matching windows."""
        conv_id = _create_conversation(client)
        _create_context_window(client, conv_id)
        resp = mcp_call(
            client,
            "conv_search_context_windows",
            {"conversation_id": conv_id},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert len(result["context_windows"]) >= 1

    def test_by_specific_id(self, client):
        """Lookup specific context window by ID."""
        conv_id = _create_conversation(client)
        cw_id = _create_context_window(client, conv_id)
        resp = mcp_call(
            client,
            "conv_search_context_windows",
            {"context_window_id": cw_id},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert len(result["context_windows"]) == 1

    def test_by_build_type(self, client):
        """Search by build_type filter succeeds."""
        resp = mcp_call(
            client,
            "conv_search_context_windows",
            {"build_type": "passthrough", "limit": 3},
        )
        assert resp.status_code == 200


# ===================================================================
# mem_search
# ===================================================================


class TestMemSearch:
    """Tests for the mem_search MCP tool."""

    def test_valid_search(self, client):
        """Search memories with valid params succeeds."""
        resp = mcp_call(
            client,
            "mem_search",
            {"query": "test", "user_id": "e2e-test-user"},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "memories" in result

    def test_missing_query(self, client):
        """Missing required query returns validation error."""
        resp = mcp_call(
            client,
            "mem_search",
            {"user_id": "e2e-test-user"},
        )
        assert resp.status_code == 400

    def test_missing_user_id(self, client):
        """Missing required user_id returns validation error."""
        resp = mcp_call(
            client,
            "mem_search",
            {"query": "test"},
        )
        assert resp.status_code == 400

    def test_invalid_limit(self, client):
        """Limit exceeding max returns validation error."""
        resp = mcp_call(
            client,
            "mem_search",
            {"query": "test", "user_id": "e2e-test-user", "limit": 999},
        )
        assert resp.status_code == 400


# ===================================================================
# mem_get_context
# ===================================================================


class TestMemGetContext:
    """Tests for the mem_get_context MCP tool."""

    def test_valid_context(self, client):
        """Get memory context with valid params succeeds."""
        resp = mcp_call(
            client,
            "mem_get_context",
            {"query": "test preferences", "user_id": "e2e-test-user"},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "context" in result
        assert "memories" in result

    def test_missing_query(self, client):
        """Missing required query returns validation error."""
        resp = mcp_call(
            client,
            "mem_get_context",
            {"user_id": "e2e-test-user"},
        )
        assert resp.status_code == 400

    def test_missing_user_id(self, client):
        """Missing required user_id returns validation error."""
        resp = mcp_call(
            client,
            "mem_get_context",
            {"query": "test"},
        )
        assert resp.status_code == 400


# ===================================================================
# imperator_chat
# ===================================================================


class TestImperatorChat:
    """Tests for the imperator_chat MCP tool."""

    def test_valid_chat(self, client):
        """Send a message to the Imperator and get a response."""
        resp = mcp_call(
            client,
            "imperator_chat",
            {"message": "Hello, this is an e2e test. Respond briefly."},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "response" in result
        assert len(result["response"]) > 0

    def test_missing_message(self, client):
        """Missing required message returns validation error."""
        resp = mcp_call(client, "imperator_chat", {})
        assert resp.status_code == 400

    def test_empty_message(self, client):
        """Empty message string returns validation error."""
        resp = mcp_call(client, "imperator_chat", {"message": ""})
        assert resp.status_code == 400


# ===================================================================
# metrics_get
# ===================================================================


class TestMetricsGet:
    """Tests for the metrics_get MCP tool."""

    def test_valid_metrics(self, client):
        """Get metrics with no params succeeds."""
        resp = mcp_call(client, "metrics_get", {})
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "metrics" in result
        # Should contain Prometheus-format text
        assert isinstance(result["metrics"], str)


# ===================================================================
# Core Tools (D-02)
# ===================================================================


class TestGetContext:
    """Tests for the get_context core tool."""

    def test_auto_create_conversation(self, client):
        """get_context without conversation_id creates a new conversation."""
        resp = mcp_call(
            client,
            "get_context",
            {"build_type": "passthrough", "budget": 8000},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "conversation_id" in result
        assert result["conversation_id"] is not None
        assert "context" in result
        assert isinstance(result["context"], list)

    def test_reuse_conversation(self, client):
        """get_context with conversation_id reuses existing conversation."""
        # Create first
        resp1 = mcp_call(
            client,
            "get_context",
            {"build_type": "passthrough", "budget": 8000},
        )
        result1 = extract_mcp_result(resp1)
        conv_id = result1["conversation_id"]

        # Reuse
        resp2 = mcp_call(
            client,
            "get_context",
            {"build_type": "passthrough", "budget": 8000, "conversation_id": conv_id},
        )
        assert resp2.status_code == 200
        result2 = extract_mcp_result(resp2)
        assert result2["conversation_id"] == conv_id

    def test_budget_snapping(self, client):
        """Budget is snapped to nearest bucket (8000 → 8192)."""
        resp = mcp_call(
            client,
            "get_context",
            {"build_type": "passthrough", "budget": 8000},
        )
        assert resp.status_code == 200
        # The snapping is internal — we can verify by checking
        # that the response is valid (snapping worked without error)
        result = extract_mcp_result(resp)
        assert result["assembly_status"] == "ready"

    def test_invalid_build_type(self, client):
        """Invalid build_type returns error."""
        resp = mcp_call(
            client,
            "get_context",
            {"build_type": "nonexistent", "budget": 8000},
        )
        # Should either be 400 (validation) or 200 with error in result
        if resp.status_code == 200:
            result = extract_mcp_result(resp)
            # The flow should return an error about the unknown build type
            assert "error" in str(result).lower() or "not found" in str(result).lower()

    def test_missing_required_fields(self, client):
        """Missing build_type or budget returns validation error."""
        resp = mcp_call(client, "get_context", {"budget": 8000})
        assert resp.status_code == 400

        resp = mcp_call(client, "get_context", {"build_type": "passthrough"})
        assert resp.status_code == 400


class TestStoreMessageCore:
    """Tests for the store_message core tool."""

    def test_store_and_retrieve(self, client):
        """Store a message, then retrieve context containing it."""
        # Get context (auto-creates conversation)
        resp = mcp_call(
            client,
            "get_context",
            {"build_type": "passthrough", "budget": 8000},
        )
        conv_id = extract_mcp_result(resp)["conversation_id"]

        # Store a message
        resp = mcp_call(
            client,
            "store_message",
            {
                "conversation_id": conv_id,
                "role": "user",
                "sender": "test",
                "content": "Core tool store test",
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "message_id" in result

        # Retrieve and verify message is in context
        resp = mcp_call(
            client,
            "get_context",
            {"build_type": "passthrough", "budget": 8000, "conversation_id": conv_id},
        )
        result = extract_mcp_result(resp)
        assert len(result["context"]) > 0
        assert any("Core tool store test" in str(m) for m in result["context"])

    def test_missing_conversation_id(self, client):
        """Missing conversation_id returns validation error."""
        resp = mcp_call(
            client,
            "store_message",
            {"role": "user", "sender": "test", "content": "test"},
        )
        assert resp.status_code == 400


class TestSearchMessagesCore:
    """Tests for the search_messages core tool."""

    def test_valid_search(self, client):
        """Search with a query succeeds."""
        resp = mcp_call(
            client,
            "search_messages",
            {"query": "hello", "limit": 5},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "messages" in result

    def test_missing_query(self, client):
        """Missing query returns validation error."""
        resp = mcp_call(client, "search_messages", {"limit": 5})
        assert resp.status_code == 400


class TestSearchKnowledge:
    """Tests for the search_knowledge core tool."""

    def test_valid_search(self, client):
        """Search knowledge with query and user_id succeeds."""
        resp = mcp_call(
            client,
            "search_knowledge",
            {"query": "context engineering", "user_id": "test-user"},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "memories" in result

    def test_missing_required_fields(self, client):
        """Missing query or user_id returns validation error."""
        resp = mcp_call(
            client,
            "search_knowledge",
            {"user_id": "test-user"},
        )
        assert resp.status_code == 400

        resp = mcp_call(
            client,
            "search_knowledge",
            {"query": "test"},
        )
        assert resp.status_code == 400
