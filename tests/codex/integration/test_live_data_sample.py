import uuid

import pytest

from tests.codex.conftest import extract_mcp_result, mcp_call, wait_for_condition


@pytest.mark.integration
class TestLiveDataSample:
    def test_load_sample_conversation(self, cb_client, sample_phase1_messages):
        resp = mcp_call(
            cb_client,
            "get_context",
            {"build_type": "sliding-window", "budget": 8000},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        conv_id = result["conversation_id"]

        messages = []
        for msg in sample_phase1_messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            role = msg.get("role")
            sender = msg.get("sender") or "codex"
            if not content or not role:
                continue
            messages.append({"content": content, "role": role, "sender": sender})
            if len(messages) >= 5:
                break

        assert messages, "No usable messages found in sample dataset"

        for msg in messages:
            store_resp = mcp_call(
                cb_client,
                "store_message",
                {
                    "conversation_id": conv_id,
                    "role": msg["role"],
                    "sender": msg["sender"],
                    "content": msg["content"],
                },
            )
            assert store_resp.status_code == 200

        needle = None
        for msg in messages:
            content = msg["content"]
            if isinstance(content, str) and len(content) >= 12:
                needle = content[:24]
                break
        if not needle:
            needle = f"codex-{uuid.uuid4().hex[:8]}"

        def _search():
            search_resp = mcp_call(
                cb_client,
                "search_messages",
                {"query": needle, "conversation_id": conv_id, "limit": 10},
            )
            if search_resp.status_code != 200:
                return False
            search_result = extract_mcp_result(search_resp)
            messages_found = search_result.get("messages") or []
            return any(needle in str(m) for m in messages_found)

        assert wait_for_condition(_search, timeout_seconds=30.0)
