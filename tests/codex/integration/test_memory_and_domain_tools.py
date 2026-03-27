import json
import re
import uuid

import pytest

from tests.codex.conftest import extract_mcp_result, mcp_call, wait_for_condition
from tests.codex.utils.remote_docker import psql_query


def _chat_content(cb_client, message: str) -> str:
    payload = {
        "model": "imperator",
        "messages": [{"role": "user", "content": message}],
        "stream": False,
    }
    resp = cb_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["choices"][0]["message"]["content"]


@pytest.mark.integration
class TestMemoryAndKnowledge:
    def test_mem_add_and_search(self, cb_client):
        user_id = f"codex-user-{uuid.uuid4().hex[:8]}"
        content = f"User likes spicy ramen ({uuid.uuid4().hex[:6]}). Remember this preference."

        add_resp = mcp_call(
            cb_client,
            "mem_add",
            {"content": content, "user_id": user_id},
        )
        assert add_resp.status_code == 200
        add_result = extract_mcp_result(add_resp)
        assert not add_result.get("degraded", False), f"mem_add degraded: {add_result}"
        add_relations = []
        add_memory_id = None
        if isinstance(add_result, dict):
            result_obj = add_result.get("result") or {}
            add_relations = result_obj.get("relations", []) or []
            results = result_obj.get("results", []) or []
            if results and isinstance(results, list) and isinstance(results[0], dict):
                add_memory_id = results[0].get("id")
        assert isinstance(add_relations, list)

        def _search():
            search_resp = mcp_call(
                cb_client,
                "mem_search",
                {"query": "spicy ramen", "user_id": user_id, "limit": 5},
            )
            if search_resp.status_code != 200:
                return False
            search_result = extract_mcp_result(search_resp)
            relations = search_result.get("relations") or []
            if not relations and search_result.get("memories"):
                return any(content in str(m) for m in search_result.get("memories", []))
            if add_relations:
                return any(r in relations for r in add_relations)
            return False

        assert wait_for_condition(_search, timeout_seconds=120.0)

    def test_mem_get_context(self, cb_client):
        user_id = f"codex-user-{uuid.uuid4().hex[:8]}"
        content = f"Preference: likes dark mode {uuid.uuid4().hex[:6]}"
        add_resp = mcp_call(cb_client, "mem_add", {"content": content, "user_id": user_id})
        assert add_resp.status_code == 200

        def _ctx():
            ctx_resp = mcp_call(
                cb_client,
                "mem_get_context",
                {"query": "dark mode", "user_id": user_id, "limit": 3},
            )
            if ctx_resp.status_code != 200:
                return False
            ctx_result = extract_mcp_result(ctx_resp)
            return "context" in ctx_result and len(ctx_result.get("memories", [])) >= 1

        assert wait_for_condition(_ctx, timeout_seconds=30.0)

    def test_search_knowledge(self, cb_client):
        user_id = f"codex-user-{uuid.uuid4().hex[:8]}"
        content = f"Codex knowledge {uuid.uuid4().hex}"
        add_resp = mcp_call(cb_client, "mem_add", {"content": content, "user_id": user_id})
        assert add_resp.status_code == 200

        search_resp = mcp_call(
            cb_client,
            "search_knowledge",
            {"query": "Codex knowledge", "user_id": user_id, "limit": 5},
        )
        assert search_resp.status_code == 200
        result = extract_mcp_result(search_resp)
        assert "memories" in result

    def test_mem_list_and_delete(self, cb_client):
        user_id = f"codex-user-{uuid.uuid4().hex[:8]}"
        content = f"User likes spicy ramen ({uuid.uuid4().hex[:6]}). Remember this preference."
        add_resp = mcp_call(cb_client, "mem_add", {"content": content, "user_id": user_id})
        assert add_resp.status_code == 200
        add_result = extract_mcp_result(add_resp)

        def _list():
            list_resp = mcp_call(cb_client, "mem_list", {"user_id": user_id, "limit": 5})
            if list_resp.status_code != 200:
                return []
            list_result = extract_mcp_result(list_resp)
            if isinstance(list_result, dict) and "memories" in list_result:
                return list_result["memories"]
            if isinstance(list_result, list):
                return list_result
            return []

        memories = _list()
        assert isinstance(memories, list)

        memory_id = None
        if isinstance(add_result, dict):
            result_obj = add_result.get("result") or {}
            if isinstance(result_obj, dict):
                results = result_obj.get("results", []) or []
                if results and isinstance(results, list) and isinstance(results[0], dict):
                    memory_id = results[0].get("id")
                if not memory_id:
                    memory_id = result_obj.get("id") or result_obj.get("memory_id")
                if not memory_id:
                    relations = result_obj.get("relations") or []
                    if relations:
                        memory_id = relations[0].get("target")

        if not memory_id:
            search_resp = mcp_call(
                cb_client,
                "mem_search",
                {"query": content, "user_id": user_id, "limit": 5},
            )
            if search_resp.status_code == 200:
                search_result = extract_mcp_result(search_resp)
                relations = search_result.get("relations") or []
                if relations:
                    memory_id = relations[0].get("target")

        if not memory_id:
            sql = (
                "SELECT id FROM mem0_memories "
                f"WHERE payload->>'user_id' = '{user_id}' "
                "ORDER BY id DESC LIMIT 1"
            )
            output = _chat_content(
                cb_client,
                "Use db_query to run this SQL and return only the raw output:\n"
                + sql,
            )
            match = re.search(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                output,
                re.IGNORECASE,
            )
            if match:
                memory_id = match.group(0)

        if memory_id:
            delete_resp = mcp_call(cb_client, "mem_delete", {"memory_id": memory_id})
            assert delete_resp.status_code == 200
            delete_result = extract_mcp_result(delete_resp)
            assert "deleted" in str(delete_result).lower()
        else:
            pytest.fail("mem_add did not return a memory id/target for deletion")

    def test_mem0_embedding_persisted(self, cb_client):
        user_id = f"codex-user-{uuid.uuid4().hex[:8]}"
        content = f"User likes espresso ({uuid.uuid4().hex[:6]}). Remember this preference."
        add_resp = mcp_call(cb_client, "mem_add", {"content": content, "user_id": user_id})
        assert add_resp.status_code == 200

        escaped_content = content.replace("'", "''")

        columns_output = psql_query(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'mem0_memories' ORDER BY column_name;"
        )
        columns = {line.strip() for line in columns_output.splitlines() if line.strip()}
        if "memory" in columns:
            content_expr = "memory"
            content_predicate = f"{content_expr} = '{escaped_content}'"
        elif "content" in columns:
            content_expr = "content"
            content_predicate = f"{content_expr} = '{escaped_content}'"
        elif "text" in columns:
            content_expr = "text"
            content_predicate = f"{content_expr} = '{escaped_content}'"
        elif "document" in columns:
            content_expr = "document"
            content_predicate = f"{content_expr} = '{escaped_content}'"
        elif "payload" in columns:
            content_expr = "payload->>'user_id'"
            content_predicate = f"{content_expr} = '{user_id}'"
        else:
            pytest.fail("mem0_memories has no recognizable content column to validate")

        def _get_row_id() -> str:
            out = psql_query(
                "SELECT id FROM mem0_memories WHERE " + content_predicate + " ORDER BY id DESC LIMIT 1;"
            )
            return out.strip()

        assert wait_for_condition(lambda: bool(_get_row_id()), timeout_seconds=120, interval=3)
        row_id = _get_row_id()
        assert row_id

        payload_out = psql_query(
            "SELECT payload FROM mem0_memories WHERE id = '" + row_id + "';"
        ).strip()
        try:
            payload = json.loads(payload_out) if payload_out else {}
        except json.JSONDecodeError:
            payload = {}
        if payload:
            data_field = payload.get("data") or payload.get("memory") or payload.get("content")
            if isinstance(data_field, str):
                assert content[:20] in data_field or data_field in content

        if "embedding" in columns:
            out = psql_query(
                "SELECT embedding IS NOT NULL FROM mem0_memories WHERE id = '" + row_id + "';"
            )
            assert out.strip().lower() in {"t", "true"}
        elif "vector" in columns:
            out = psql_query(
                "SELECT vector IS NOT NULL FROM mem0_memories WHERE id = '" + row_id + "';"
            )
            assert out.strip().lower() in {"t", "true"}
        elif "payload" in columns:
            out = psql_query(
                "SELECT (payload->'embedding' IS NOT NULL OR payload->'vector' IS NOT NULL) FROM mem0_memories WHERE id = '"
                + row_id
                + "';"
            )
            assert out.strip().lower() in {"t", "true"}
        else:
            pytest.fail("mem0_memories has no embedding/vector/payload columns to validate")
