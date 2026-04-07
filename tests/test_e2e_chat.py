"""
End-to-end OpenAI-compatible chat endpoint tests (T-6.2).

Tests /v1/chat/completions with streaming and non-streaming modes
against the deployed system at http://192.168.1.110:8080.
"""

import json

import httpx
import pytest

from tests.conftest_e2e import BASE_URL

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=120.0) as c:
        yield c


# ===================================================================
# Non-streaming
# ===================================================================


class TestChatNonStreaming:
    """Test non-streaming /v1/chat/completions responses."""

    def test_valid_completion(self, client):
        """Non-streaming request returns a valid OpenAI-format completion."""
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "context-broker",
                "messages": [
                    {
                        "role": "user",
                        "content": "Say the word 'hello' and nothing else.",
                    }
                ],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        # Verify OpenAI response structure
        assert body["object"] == "chat.completion"
        assert "id" in body
        assert "created" in body
        assert "choices" in body
        assert len(body["choices"]) >= 1
        choice = body["choices"][0]
        assert choice["message"]["role"] == "assistant"
        assert isinstance(choice["message"]["content"], str)
        assert choice["finish_reason"] == "stop"
        assert "usage" in body

    def test_system_and_user_messages(self, client):
        """Request with system + user messages succeeds."""
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "context-broker",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {
                        "role": "user",
                        "content": "What is 2+2? Reply with just the number.",
                    },
                ],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["message"]["content"] is not None


# ===================================================================
# Streaming
# ===================================================================


class TestChatStreaming:
    """Test streaming /v1/chat/completions responses (SSE)."""

    def test_streaming_returns_sse_chunks(self, client):
        """Streaming request returns SSE data chunks ending with [DONE]."""
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "context-broker",
                "messages": [{"role": "user", "content": "Say 'hi' and nothing else."}],
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

            chunks = []
            saw_done = False
            for line in resp.iter_lines():
                if not line:
                    continue
                if line == "data: [DONE]":
                    saw_done = True
                    break
                if line.startswith("data: "):
                    chunk_data = json.loads(line[6:])
                    chunks.append(chunk_data)

            assert saw_done, "Stream did not end with [DONE]"
            assert len(chunks) >= 1, "Expected at least one data chunk"

            # Verify chunk structure
            for chunk in chunks:
                assert chunk["object"] == "chat.completion.chunk"
                assert "choices" in chunk
                assert len(chunk["choices"]) >= 1

            # Last content chunk should have finish_reason="stop"
            last_chunk = chunks[-1]
            assert last_chunk["choices"][0]["finish_reason"] == "stop"


# ===================================================================
# Error handling
# ===================================================================


class TestChatErrors:
    """Test error responses from /v1/chat/completions."""

    def test_invalid_json(self, client):
        """Malformed JSON returns 400."""
        resp = client.post(
            "/v1/chat/completions",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body

    def test_missing_messages(self, client):
        """Missing messages field returns 422."""
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "context-broker"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "error" in body

    def test_empty_messages(self, client):
        """Empty messages list returns 422."""
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "context-broker", "messages": []},
        )
        assert resp.status_code == 422

    def test_no_user_message(self, client):
        """Messages without a user role returns 400."""
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "context-broker",
                "messages": [{"role": "system", "content": "System only, no user."}],
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body

    def test_invalid_role(self, client):
        """Invalid message role returns 422."""
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "context-broker",
                "messages": [{"role": "invalid_role", "content": "Bad role"}],
            },
        )
        assert resp.status_code == 422
