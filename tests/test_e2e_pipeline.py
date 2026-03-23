"""
End-to-end pipeline tests (T-5.1, T-5.2, T-4.2).

Verifies the async processing pipeline by storing messages via MCP and
inspecting Postgres directly for embeddings, dedup, and context assembly.

Connects to:
  - MCP endpoint: http://192.168.1.110:8080/mcp
  - Postgres: 192.168.1.110:5432 (context_broker/contextbroker123)
"""

import asyncio
import json
import time
import uuid

import httpx
import pytest

from tests.conftest_e2e import (
    BASE_URL,
    PG_DATABASE,
    PG_HOST,
    PG_PASSWORD,
    PG_PORT,
    PG_USER,
    extract_mcp_result,
    mcp_call,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=60.0) as c:
        yield c


@pytest.fixture(scope="module")
def event_loop():
    """Module-scoped event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


async def _get_pg_connection():
    """Create a direct asyncpg connection to Postgres."""
    import asyncpg

    return await asyncpg.connect(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASSWORD,
        database=PG_DATABASE,
    )


def _create_conversation(client: httpx.Client) -> str:
    resp = mcp_call(
        client,
        "conv_create_conversation",
        {"title": f"pipeline-e2e-{uuid.uuid4().hex[:8]}"},
    )
    assert resp.status_code == 200
    return extract_mcp_result(resp)["conversation_id"]


def _create_context_window(client: httpx.Client, conv_id: str) -> str:
    resp = mcp_call(
        client,
        "conv_create_context_window",
        {
            "conversation_id": conv_id,
            "participant_id": f"pipeline-{uuid.uuid4().hex[:6]}",
            "build_type": "passthrough",
        },
    )
    assert resp.status_code == 200
    return extract_mcp_result(resp)["context_window_id"]


# ===================================================================
# T-5.1: End-to-end pipeline execution
# ===================================================================


class TestPipelineEmbedding:
    """Verify that storing a message triggers embedding generation."""

    def test_message_gets_embedding(self, client):
        """Store a message, wait, verify embedding vector exists in Postgres."""
        conv_id = _create_conversation(client)
        cw_id = _create_context_window(client, conv_id)

        unique_content = f"Pipeline embedding test {uuid.uuid4().hex}"
        resp = mcp_call(
            client,
            "conv_store_message",
            {
                "context_window_id": cw_id,
                "role": "user",
                "sender": "e2e-pipeline",
                "content": unique_content,
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        message_id = result["message_id"]
        assert message_id is not None

        # Poll Postgres for the embedding (async pipeline may take a few seconds)
        async def check_embedding():
            conn = await _get_pg_connection()
            try:
                # Wait up to 30 seconds for the embedding to appear
                for _ in range(30):
                    row = await conn.fetchrow(
                        "SELECT vector IS NOT NULL AS has_vector "
                        "FROM conversation_messages WHERE id = $1",
                        uuid.UUID(message_id),
                    )
                    if row and row["has_vector"]:
                        return True
                    await asyncio.sleep(1)
                return False
            finally:
                await conn.close()

        has_embedding = asyncio.run(check_embedding())
        assert has_embedding, (
            f"Message {message_id} did not get an embedding vector within 30s"
        )


# ===================================================================
# T-5.2: Pipeline intermediate outputs
# ===================================================================


class TestPipelineIntermediateOutputs:
    """Verify pipeline stages produce expected database state."""

    def test_message_stored_in_postgres(self, client):
        """Verify stored message exists in conversation_messages table."""
        conv_id = _create_conversation(client)
        unique_content = f"DB check test {uuid.uuid4().hex}"
        resp = mcp_call(
            client,
            "conv_store_message",
            {
                "conversation_id": conv_id,
                "role": "user",
                "sender": "e2e-pipeline",
                "content": unique_content,
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        message_id = result["message_id"]

        async def verify():
            conn = await _get_pg_connection()
            try:
                row = await conn.fetchrow(
                    "SELECT id, content, role, sender "
                    "FROM conversation_messages WHERE id = $1",
                    uuid.UUID(message_id),
                )
                assert row is not None, f"Message {message_id} not found in DB"
                assert row["content"] == unique_content
                assert row["role"] == "user"
                assert row["sender"] == "e2e-pipeline"
            finally:
                await conn.close()

        asyncio.run(verify())

    def test_conversation_exists_in_postgres(self, client):
        """Verify created conversation exists in conversations table."""
        conv_id = _create_conversation(client)

        async def verify():
            conn = await _get_pg_connection()
            try:
                row = await conn.fetchrow(
                    "SELECT id FROM conversations WHERE id = $1",
                    uuid.UUID(conv_id),
                )
                assert row is not None, f"Conversation {conv_id} not found in DB"
            finally:
                await conn.close()

        asyncio.run(verify())

    def test_context_window_exists_in_postgres(self, client):
        """Verify created context window exists in context_windows table."""
        conv_id = _create_conversation(client)
        cw_id = _create_context_window(client, conv_id)

        async def verify():
            conn = await _get_pg_connection()
            try:
                row = await conn.fetchrow(
                    "SELECT id, build_type FROM context_windows WHERE id = $1",
                    uuid.UUID(cw_id),
                )
                assert row is not None, f"Context window {cw_id} not found in DB"
                assert row["build_type"] == "passthrough"
            finally:
                await conn.close()

        asyncio.run(verify())


# ===================================================================
# T-4.2: Idempotency / consecutive duplicate detection
# ===================================================================


class TestDuplicateDetection:
    """Verify consecutive duplicate message handling."""

    def test_consecutive_duplicate_collapsed(self, client):
        """Store same message twice consecutively; verify was_collapsed on second."""
        conv_id = _create_conversation(client)
        cw_id = _create_context_window(client, conv_id)

        duplicate_content = f"Duplicate test {uuid.uuid4().hex[:8]}"
        args = {
            "context_window_id": cw_id,
            "role": "user",
            "sender": "e2e-dedup",
            "content": duplicate_content,
        }

        # First store
        resp1 = mcp_call(client, "conv_store_message", args)
        assert resp1.status_code == 200
        result1 = extract_mcp_result(resp1)
        assert result1["message_id"] is not None
        first_id = result1["message_id"]

        # Second store — same content, same context window
        resp2 = mcp_call(client, "conv_store_message", args)
        assert resp2.status_code == 200
        result2 = extract_mcp_result(resp2)

        # The system should detect the duplicate — was_collapsed should be True
        # or the message_id should be the same (idempotent)
        assert result2.get("was_collapsed") is True or result2.get("message_id") == first_id, (
            f"Second store was not collapsed: {result2}"
        )


# ===================================================================
# Context retrieval format
# ===================================================================


class TestContextRetrievalFormat:
    """Verify context retrieval returns structured messages array."""

    def test_retrieve_context_has_messages_structure(self, client):
        """Retrieved context contains structured messages array."""
        conv_id = _create_conversation(client)
        cw_id = _create_context_window(client, conv_id)

        # Store some messages
        for i in range(3):
            mcp_call(
                client,
                "conv_store_message",
                {
                    "context_window_id": cw_id,
                    "role": "user" if i % 2 == 0 else "assistant",
                    "sender": "e2e-user" if i % 2 == 0 else "e2e-assistant",
                    "content": f"Context format test message {i}",
                },
            )

        # Retrieve context
        resp = mcp_call(
            client,
            "conv_retrieve_context",
            {"context_window_id": cw_id},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)

        assert "context" in result, f"No 'context' key in result: {result.keys()}"
        assert "assembly_status" in result
        # Context should be a list (messages array) or None if no assembly yet
        context = result["context"]
        if context is not None:
            assert isinstance(context, list), (
                f"Expected context to be a list, got {type(context)}"
            )
