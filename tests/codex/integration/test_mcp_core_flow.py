import uuid

import pytest

from tests.codex.conftest import extract_mcp_result, mcp_call, mcp_call_raw, wait_for_condition


@pytest.mark.integration
class TestMCPProtocol:
    def test_initialize(self, cb_client):
        resp = mcp_call_raw(
            cb_client,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"]["protocolVersion"]
        assert body["result"]["serverInfo"]["name"] == "context-broker"

    def test_tools_list_contains_core(self, cb_client):
        resp = mcp_call_raw(
            cb_client,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert resp.status_code == 200
        tools = resp.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        expected = {
            "get_context",
            "store_message",
            "search_messages",
            "search_knowledge",
            "conv_create_conversation",
            "conv_create_context_window",
            "conv_get_history",
            "mem_add",
            "mem_search",
            "metrics_get",
        }
        missing = expected - names
        assert not missing, f"Missing tools: {missing}"


@pytest.mark.integration
class TestCoreConversationFlow:
    def test_get_context_store_and_search(self, cb_client):
        resp = mcp_call(
            cb_client,
            "get_context",
            {"build_type": "sliding-window", "budget": 5000},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        conversation_id = result["conversation_id"]
        assert conversation_id

        unique_text = f"codex-message-{uuid.uuid4().hex[:10]}"
        resp = mcp_call(
            cb_client,
            "store_message",
            {
                "conversation_id": conversation_id,
                "role": "user",
                "sender": "codex",
                "content": unique_text,
            },
        )
        assert resp.status_code == 200
        store_result = extract_mcp_result(resp)
        assert store_result.get("message_id")

        def _search():
            search_resp = mcp_call(
                cb_client,
                "search_messages",
                {"query": unique_text, "conversation_id": conversation_id, "limit": 5},
            )
            if search_resp.status_code != 200:
                return False
            search_result = extract_mcp_result(search_resp)
            messages = search_result.get("messages") or []
            return any(unique_text in str(m) for m in messages)

        assert wait_for_condition(_search, timeout_seconds=20.0)

    def test_conversation_history(self, cb_client):
        conv_resp = mcp_call(cb_client, "conv_create_conversation", {})
        assert conv_resp.status_code == 200
        conv_id = extract_mcp_result(conv_resp)["conversation_id"]

        for idx in range(3):
            mcp_call(
                cb_client,
                "conv_store_message",
                {
                    "conversation_id": conv_id,
                    "role": "user",
                    "sender": "codex",
                    "content": f"history-{idx}",
                },
            )

        hist_resp = mcp_call(cb_client, "conv_get_history", {"conversation_id": conv_id})
        assert hist_resp.status_code == 200
        history = extract_mcp_result(hist_resp)
        assert len(history.get("messages", [])) >= 3

    def test_context_window_round_trip(self, cb_client):
        conv_resp = mcp_call(cb_client, "conv_create_conversation", {})
        conv_id = extract_mcp_result(conv_resp)["conversation_id"]

        cw_resp = mcp_call(
            cb_client,
            "conv_create_context_window",
            {
                "conversation_id": conv_id,
                "participant_id": "codex-participant",
                "build_type": "sliding-window",
            },
        )
        assert cw_resp.status_code == 200
        cw_id = extract_mcp_result(cw_resp)["context_window_id"]

        store_resp = mcp_call(
            cb_client,
            "conv_store_message",
            {
                "context_window_id": cw_id,
                "role": "user",
                "sender": "codex",
                "content": "context window message",
            },
        )
        assert store_resp.status_code == 200

        retrieve_resp = mcp_call(
            cb_client,
            "conv_retrieve_context",
            {"context_window_id": cw_id},
        )
        assert retrieve_resp.status_code == 200
        result = extract_mcp_result(retrieve_resp)
        assert "context" in result
