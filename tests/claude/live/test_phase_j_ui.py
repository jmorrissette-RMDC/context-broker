"""
Phase J -- UI component tests.

Tests the MADClient class (ui/mad_client.py) directly against the live test
stack and the extract_artifacts() helper as a pure function.

The test stack omits the Gradio UI container, so we cannot test Gradio event
handlers or the rendered UI here. The Gradio app (ui/app.py) executes side
effects at import time (loads config, builds gr.Blocks), making it
un-importable without a running MAD. Browser/Playwright testing is required
for full UI coverage.

All tests run against the live stack at http://192.168.1.110:8081.
"""

import sys
import uuid
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import MADClient and extract_artifacts from the ui package
# ---------------------------------------------------------------------------

_ui_dir = str(Path(__file__).resolve().parents[3] / "ui")
if _ui_dir not in sys.path:
    sys.path.insert(0, _ui_dir)

from mad_client import MADClient  # noqa: E402

# extract_artifacts lives in app.py, but importing app.py triggers Gradio
# initialisation. Re-implement the regex extraction inline so we can test
# the logic without importing app.py.
import re  # noqa: E402

_CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)


def extract_artifacts(text: str) -> str:
    """Mirror of app.extract_artifacts -- pure function, no Gradio dependency."""
    blocks = _CODE_BLOCK_RE.findall(text)
    if not blocks:
        return ""
    parts = []
    for lang, code in blocks:
        lang = lang or "text"
        parts.append(f"**{lang}:**\n```{lang}\n{code.strip()}\n```")
    return "\n\n".join(parts)


from tests.claude.live.helpers import BASE_URL, DOCKER_HOST  # noqa: E402

pytestmark = pytest.mark.live

# ---------------------------------------------------------------------------
# Shared helper -- a MADClient pointed at the test stack
# ---------------------------------------------------------------------------

TEST_BASE_URL = f"http://{DOCKER_HOST}:8081"


def _client() -> MADClient:
    return MADClient("test", TEST_BASE_URL)


# ===================================================================
# J-01 .. J-09: MADClient async tests
# ===================================================================


class TestMADClient:
    """J-01 .. J-09: MADClient talking to the live test stack."""

    @pytest.mark.asyncio
    async def test_mad_client_health(self, http_client):
        """J-01: health() returns a dict containing 'status'."""
        client = _client()
        result = await client.health()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "status" in result, f"No 'status' key in health response: {result}"

    @pytest.mark.asyncio
    async def test_mad_client_list_conversations(self, http_client):
        """J-02: list_conversations() returns a list of dicts with 'id'."""
        client = _client()
        convs = await client.list_conversations()
        assert isinstance(convs, list), f"Expected list, got {type(convs)}"
        if convs:
            assert "id" in convs[0], f"First conversation missing 'id': {convs[0]}"

    @pytest.mark.asyncio
    async def test_mad_client_create_conversation(self, http_client):
        """J-03: create_conversation() returns dict with 'conversation_id'."""
        client = _client()
        title = f"ui-test-{uuid.uuid4().hex[:8]}"
        result = await client.create_conversation(title)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "conversation_id" in result, (
            f"No 'conversation_id' in create result: {result}"
        )

    @pytest.mark.asyncio
    async def test_mad_client_get_history(self, http_client):
        """J-04: Create conv, store a message via MCP, then get_history()."""
        client = _client()
        title = f"ui-hist-{uuid.uuid4().hex[:8]}"
        created = await client.create_conversation(title)
        conv_id = created["conversation_id"]

        # Store a message via the low-level _mcp_call
        await client._mcp_call(
            "store_message",
            {
                "conversation_id": conv_id,
                "role": "user",
                "content": "Hello from UI test",
                "sender": "test-runner",
            },
        )

        messages = await client.get_history(conv_id)
        assert isinstance(messages, list), f"Expected list, got {type(messages)}"
        assert len(messages) >= 1, "Expected at least 1 message in history"
        contents = [m.get("content", "") for m in messages]
        assert any("Hello from UI test" in c for c in contents), (
            f"Stored message not found in history: {contents}"
        )

    @pytest.mark.asyncio
    async def test_mad_client_delete_conversation(self, http_client):
        """J-05: Create conv, delete it, verify returns True."""
        client = _client()
        title = f"ui-del-{uuid.uuid4().hex[:8]}"
        created = await client.create_conversation(title)
        conv_id = created["conversation_id"]

        result = await client.delete_conversation(conv_id)
        assert result is True, f"delete_conversation returned {result}, expected True"

    @pytest.mark.asyncio
    async def test_mad_client_get_context_info(self, http_client):
        """J-06: get_context_info() returns a dict (may be empty)."""
        client = _client()
        # Use a freshly created conversation -- context info may be empty
        created = await client.create_conversation(f"ui-ctx-{uuid.uuid4().hex[:8]}")
        conv_id = created["conversation_id"]

        result = await client.get_context_info(conv_id)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"

    @pytest.mark.asyncio
    async def test_mad_client_query_logs(self, http_client):
        """J-07: query_logs(limit=5) returns a list."""
        client = _client()
        entries = await client.query_logs(limit=5)
        assert isinstance(entries, list), f"Expected list, got {type(entries)}"

    @pytest.mark.asyncio
    async def test_mad_client_chat_stream(self, http_client):
        """J-08: chat_stream() yields non-empty content chunks."""
        client = _client()
        chunks = []
        async for chunk in client.chat_stream(
            [{"role": "user", "content": "Say hello"}]
        ):
            chunks.append(chunk)

        full_response = "".join(chunks)
        assert len(full_response) > 0, "chat_stream produced no content"

    @pytest.mark.asyncio
    async def test_mad_client_mcp_call(self, http_client):
        """J-09: _mcp_call('metrics_get', {}) returns dict with 'metrics'."""
        client = _client()
        result = await client._mcp_call("metrics_get", {})
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "metrics" in result, f"No 'metrics' key in result: {result}"


# ===================================================================
# J-10 .. J-12: extract_artifacts pure-function tests
# ===================================================================


class TestExtractArtifacts:
    """J-10 .. J-12: extract_artifacts() as a pure function."""

    def test_extract_artifacts_with_code(self):
        """J-10: Text containing a python code block produces formatted output."""
        text = "Here is some code:\n```python\nprint('hi')\n```\nDone."
        result = extract_artifacts(text)
        assert result != "", "Expected non-empty artifact extraction"
        assert "python" in result, "Expected 'python' language label"
        assert "print('hi')" in result, "Expected code content preserved"
        assert result.startswith("**python:**"), (
            f"Expected formatted block header, got: {result[:40]}"
        )

    def test_extract_artifacts_no_code(self):
        """J-11: Text without code blocks returns empty string."""
        text = "This is a plain text response with no code blocks at all."
        result = extract_artifacts(text)
        assert result == "", f"Expected empty string, got: {result!r}"

    def test_extract_artifacts_multiple_blocks(self):
        """J-12: Text with two code blocks extracts both."""
        text = (
            "First block:\n```python\nx = 1\n```\n"
            "Second block:\n```json\n{\"a\": 1}\n```\n"
        )
        result = extract_artifacts(text)
        assert "python" in result, "Missing python block"
        assert "json" in result, "Missing json block"
        assert "x = 1" in result, "Missing python code content"
        assert '{"a": 1}' in result, "Missing json code content"
        # Should have two formatted sections separated by blank line
        assert result.count("**") >= 4, (
            f"Expected at least 2 bold headers (4 **), got {result.count('**')}"
        )
