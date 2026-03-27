"""
End-to-end pipeline tests (T-5.1, T-5.2, T-4.2).

Verifies the async processing pipeline by storing messages via MCP and
inspecting Postgres (via SSH + docker exec) for embeddings, dedup, and
context assembly.

Connects to:
  - MCP endpoint: http://192.168.1.110:8080/mcp
  - Postgres: via SSH to 192.168.1.110, docker exec into context-broker-postgres
"""

import subprocess
import time
import uuid

import httpx
import pytest

from tests.conftest_e2e import (
    BASE_URL,
    SSH_HOST,
    SSH_USER,
    extract_mcp_result,
    mcp_call,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pg_query(sql: str) -> str:
    """Run a SQL query against Postgres via SSH + docker exec."""
    result = subprocess.run(
        [
            "ssh",
            f"{SSH_USER}@{SSH_HOST}",
            f'docker exec context-broker-postgres psql -U context_broker -d context_broker -t -c "{sql}"',
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=60.0) as c:
        yield c


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
        has_embedding = False
        for _ in range(30):
            output = _pg_query(
                f"SELECT embedding IS NOT NULL AS has_vector "
                f"FROM conversation_messages WHERE id = '{message_id}'"
            )
            if "t" in output:
                has_embedding = True
                break
            time.sleep(1)

        assert (
            has_embedding
        ), f"Message {message_id} did not get an embedding vector within 30s"


# ===================================================================
# T-5.2: Pipeline intermediate outputs
# ===================================================================


class TestPipelineIntermediateOutputs:
    """Verify pipeline stages produce expected database state."""

    def test_message_stored_in_postgres(self, client):
        """Verify stored message exists in conversation_messages table."""
        conv_id = _create_conversation(client)
        cw_id = _create_context_window(client, conv_id)
        unique_content = f"DB check test {uuid.uuid4().hex}"
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

        output = _pg_query(
            f"SELECT content, role, sender "
            f"FROM conversation_messages WHERE id = '{message_id}'"
        )
        assert output, f"Message {message_id} not found in DB"
        assert unique_content in output, f"Content mismatch in DB row: {output}"
        assert "user" in output
        assert "e2e-pipeline" in output

    def test_conversation_exists_in_postgres(self, client):
        """Verify created conversation exists in conversations table."""
        conv_id = _create_conversation(client)

        output = _pg_query(f"SELECT id FROM conversations WHERE id = '{conv_id}'")
        assert conv_id in output, f"Conversation {conv_id} not found in DB: {output}"

    def test_context_window_exists_in_postgres(self, client):
        """Verify created context window exists in context_windows table."""
        conv_id = _create_conversation(client)
        cw_id = _create_context_window(client, conv_id)

        output = _pg_query(
            f"SELECT id, build_type FROM context_windows WHERE id = '{cw_id}'"
        )
        assert cw_id in output, f"Context window {cw_id} not found in DB: {output}"
        assert "passthrough" in output, f"build_type mismatch: {output}"


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
        assert (
            result2.get("was_collapsed") is True
            or result2.get("message_id") == first_id
        ), f"Second store was not collapsed: {result2}"


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
            assert isinstance(
                context, list
            ), f"Expected context to be a list, got {type(context)}"
