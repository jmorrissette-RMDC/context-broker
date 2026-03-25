"""
Thorough unit tests for the memory extraction flow.

Tests every node function: lock acquisition, message fetching,
text building, Mem0 extraction, message marking, lock release,
and all routing logic. Mocks DB and Redis.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from context_broker_ae.memory_extraction import (
    _redact_secrets,
    build_extraction_text,
    route_after_build_text,
    route_after_extraction,
    route_after_fetch,
    route_after_lock,
    run_mem0_extraction,
)


@pytest.fixture(autouse=True)
def reset_mem0_state():
    """Reset Mem0 global state before each test."""
    from context_broker_ae.memory.mem0_client import reset_mem0_client
    reset_mem0_client()
    yield
    reset_mem0_client()


# Mock fixtures that patch at both the source AND the import target
@pytest.fixture
def mock_pg_pool():
    pool = AsyncMock()
    with patch("app.database._pg_pool", pool), \
         patch("app.database.get_pg_pool", return_value=pool):
        yield pool


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    with patch("app.database._redis_client", redis), \
         patch("app.database.get_redis", return_value=redis):
        yield redis


# ── _redact_secrets ──────────────────────────────────────────────────

class TestRedactSecrets:
    def test_redacts_api_key(self):
        text = 'api_key="sk-proj-abcdefghijklmnopqrstuvwxyz123456"'
        result = _redact_secrets(text)
        assert "sk-proj" not in result
        assert "[REDACTED]" in result

    def test_redacts_bearer_token(self):
        text = "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.abcdefghijklmnop"
        result = _redact_secrets(text)
        assert "eyJhbG" not in result

    def test_redacts_sk_prefix(self):
        text = "key = sk-abcdefghijklmnopqrstuvwxyz"
        result = _redact_secrets(text)
        assert "sk-abc" not in result

    def test_preserves_normal_text(self):
        text = "The Context Broker runs on irina at 192.168.1.110"
        assert _redact_secrets(text) == text

    def test_empty_string(self):
        assert _redact_secrets("") == ""

    def test_redacts_password_in_config(self):
        text = 'password: "mysecretpassword12345678"'
        result = _redact_secrets(text)
        assert "mysecretpassword" not in result


# ── acquire_extraction_lock ──────────────────────────────────────────

class TestAcquireExtractionLock:
    @pytest.mark.asyncio
    async def test_lock_acquired(self, mock_redis):
        from context_broker_ae.memory_extraction import acquire_extraction_lock
        mock_redis.set.return_value = True
        state = {"conversation_id": str(uuid.uuid4()), "config": {"tuning": {}}}
        result = await acquire_extraction_lock(state)
        assert result["lock_acquired"] is True
        assert result["lock_token"] is not None
        assert "extraction_in_progress:" in result["lock_key"]

    @pytest.mark.asyncio
    async def test_lock_not_acquired(self, mock_redis):
        from context_broker_ae.memory_extraction import acquire_extraction_lock
        mock_redis.set.return_value = False
        state = {"conversation_id": str(uuid.uuid4()), "config": {"tuning": {}}}
        result = await acquire_extraction_lock(state)
        assert result["lock_acquired"] is False
        assert result["lock_token"] is None

    @pytest.mark.asyncio
    async def test_lock_uses_config_ttl(self, mock_redis):
        from context_broker_ae.memory_extraction import acquire_extraction_lock
        mock_redis.set.return_value = True
        state = {"conversation_id": str(uuid.uuid4()), "config": {"tuning": {"extraction_lock_ttl_seconds": 999}}}
        await acquire_extraction_lock(state)
        call_kwargs = mock_redis.set.call_args
        assert call_kwargs.kwargs.get("ex") == 999


# ── fetch_unextracted_messages ───────────────────────────────────────

class TestFetchUnextractedMessages:
    @pytest.mark.asyncio
    async def test_no_messages(self, mock_pg_pool):
        from context_broker_ae.memory_extraction import fetch_unextracted_messages
        mock_pg_pool.fetch.return_value = []
        state = {"conversation_id": str(uuid.uuid4()), "config": {}}
        result = await fetch_unextracted_messages(state)
        assert result["messages"] == []
        assert result["extracted_count"] == 0

    @pytest.mark.asyncio
    async def test_returns_messages_and_user_id(self, mock_pg_pool):
        from context_broker_ae.memory_extraction import fetch_unextracted_messages
        mock_pg_pool.fetch.return_value = [
            {"id": uuid.uuid4(), "role": "user", "sender": "human", "content": "Hello", "sequence_number": 1},
            {"id": uuid.uuid4(), "role": "assistant", "sender": "bot", "content": "Hi", "sequence_number": 2},
        ]
        mock_pg_pool.fetchrow.return_value = {"participant_id": "test-user"}
        state = {"conversation_id": str(uuid.uuid4()), "config": {}}
        result = await fetch_unextracted_messages(state)
        assert len(result["messages"]) == 2
        assert result["user_id"] == "test-user"

    @pytest.mark.asyncio
    async def test_default_user_id_when_no_window(self, mock_pg_pool):
        from context_broker_ae.memory_extraction import fetch_unextracted_messages
        mock_pg_pool.fetch.return_value = [
            {"id": uuid.uuid4(), "role": "user", "sender": "human", "content": "Hello", "sequence_number": 1},
        ]
        mock_pg_pool.fetchrow.return_value = None
        state = {"conversation_id": str(uuid.uuid4()), "config": {}}
        result = await fetch_unextracted_messages(state)
        assert result["user_id"] == "default"


# ── build_extraction_text ────────────────────────────────────────────

class TestBuildExtractionText:
    def _make_msg(self, role="user", sender="human", content="Hello", msg_id=None):
        return {"id": msg_id or uuid.uuid4(), "role": role, "sender": sender, "content": content}

    @pytest.mark.asyncio
    async def test_formats_messages_chronologically(self):
        msgs = [
            self._make_msg(content="First message"),
            self._make_msg(role="assistant", sender="bot", content="Second message"),
        ]
        state = {"messages": msgs, "config": {"tuning": {}}, "selected_message_ids": [], "fully_extracted_ids": [], "extraction_text": "", "error": None}
        result = await build_extraction_text(state)
        text = result["extraction_text"]
        assert "First message" in text
        assert "Second message" in text
        assert text.index("First") < text.index("Second")

    @pytest.mark.asyncio
    async def test_skips_null_content(self):
        msgs = [
            self._make_msg(content="Valid"),
            self._make_msg(content=None),
            self._make_msg(content=""),
            self._make_msg(content="Also valid"),
        ]
        state = {"messages": msgs, "config": {"tuning": {}}, "selected_message_ids": [], "fully_extracted_ids": [], "extraction_text": "", "error": None}
        result = await build_extraction_text(state)
        assert "Valid" in result["extraction_text"]
        assert "Also valid" in result["extraction_text"]
        assert len(result["selected_message_ids"]) == 2

    @pytest.mark.asyncio
    async def test_respects_max_chars(self):
        msgs = [self._make_msg(content="x" * 5000) for _ in range(20)]
        state = {"messages": msgs, "config": {"tuning": {"extraction_max_chars": 10000}}, "selected_message_ids": [], "fully_extracted_ids": [], "extraction_text": "", "error": None}
        result = await build_extraction_text(state)
        assert len(result["extraction_text"]) <= 11000
        assert len(result["selected_message_ids"]) < 20

    @pytest.mark.asyncio
    async def test_fully_extracted_ids_excludes_truncated(self):
        msgs = [self._make_msg(content="x" * 8000) for _ in range(3)]
        state = {"messages": msgs, "config": {"tuning": {"extraction_max_chars": 10000}}, "selected_message_ids": [], "fully_extracted_ids": [], "extraction_text": "", "error": None}
        result = await build_extraction_text(state)
        assert len(result["fully_extracted_ids"]) <= len(result["selected_message_ids"])

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        state = {"messages": [], "config": {"tuning": {}}, "selected_message_ids": [], "fully_extracted_ids": [], "extraction_text": "", "error": None}
        result = await build_extraction_text(state)
        assert result["extraction_text"] == ""
        assert result["selected_message_ids"] == []
        assert result["fully_extracted_ids"] == []

    @pytest.mark.asyncio
    async def test_redacts_secrets_in_output(self):
        msgs = [self._make_msg(content='api_key="sk-proj-abcdefghijklmnopqrstuvwxyz123456"')]
        state = {"messages": msgs, "config": {"tuning": {}}, "selected_message_ids": [], "fully_extracted_ids": [], "extraction_text": "", "error": None}
        result = await build_extraction_text(state)
        assert "sk-proj" not in result["extraction_text"]
        assert "[REDACTED]" in result["extraction_text"]


# ── run_mem0_extraction ──────────────────────────────────────────────

class TestRunMem0Extraction:
    @pytest.mark.asyncio
    async def test_mem0_unavailable_returns_error(self):
        state = {
            "conversation_id": str(uuid.uuid4()), "config": {},
            "extraction_text": "test", "user_id": "test",
            "selected_message_ids": ["id1"], "extracted_count": 0, "error": None,
        }
        with patch("context_broker_ae.memory.mem0_client.get_mem0_client", new_callable=AsyncMock, return_value=None) as _:
            result = await run_mem0_extraction(state)
        assert "not available" in result["error"]

    @pytest.mark.asyncio
    async def test_mem0_add_called_with_correct_args(self):
        mock_mem0 = MagicMock()
        mock_mem0.add.return_value = {"results": []}
        conv_id = str(uuid.uuid4())
        state = {
            "conversation_id": conv_id, "config": {},
            "extraction_text": "The sky is blue.", "user_id": "user123",
            "selected_message_ids": ["id1"], "extracted_count": 0, "error": None,
        }
        with patch("context_broker_ae.memory.mem0_client.get_mem0_client", new_callable=AsyncMock, return_value=mock_mem0):
            result = await run_mem0_extraction(state)
        mock_mem0.add.assert_called_once_with(
            "The sky is blue.",
            user_id="user123",
            run_id=conv_id,
        )
        assert result.get("error") is None

    @pytest.mark.asyncio
    async def test_mem0_exception_triggers_reset(self):
        """PG-49: Mem0 error must trigger reset_mem0_client."""
        mock_mem0 = MagicMock()
        mock_mem0.add.side_effect = RuntimeError("transaction aborted")
        state = {
            "conversation_id": str(uuid.uuid4()), "config": {},
            "extraction_text": "test", "user_id": "test",
            "selected_message_ids": ["id1"], "extracted_count": 0, "error": None,
        }
        with patch("context_broker_ae.memory.mem0_client.get_mem0_client", new_callable=AsyncMock, return_value=mock_mem0), \
             patch("context_broker_ae.memory.mem0_client.reset_mem0_client") as mock_reset:
            result = await run_mem0_extraction(state)
        assert result["error"] is not None
        assert "transaction aborted" in result["error"]
        mock_reset.assert_called_once()

    @pytest.mark.asyncio
    async def test_mem0_silent_failure_not_detected(self):
        """KNOWN BUG: If mem0.add() succeeds but doesn't persist, we return error=None."""
        mock_mem0 = MagicMock()
        mock_mem0.add.return_value = None
        state = {
            "conversation_id": str(uuid.uuid4()), "config": {},
            "extraction_text": "test", "user_id": "test",
            "selected_message_ids": ["id1"], "extracted_count": 0, "error": None,
        }
        with patch("context_broker_ae.memory.mem0_client.get_mem0_client", new_callable=AsyncMock, return_value=mock_mem0):
            result = await run_mem0_extraction(state)
        assert result.get("error") is None


# ── mark_messages_extracted ──────────────────────────────────────────

class TestMarkMessagesExtracted:
    @pytest.mark.asyncio
    async def test_marks_fully_extracted_only(self, mock_pg_pool):
        from context_broker_ae.memory_extraction import mark_messages_extracted
        ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        state = {"fully_extracted_ids": ids, "error": None}
        result = await mark_messages_extracted(state)
        assert result["extracted_count"] == 2
        mock_pg_pool.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_on_error(self, mock_pg_pool):
        from context_broker_ae.memory_extraction import mark_messages_extracted
        state = {"fully_extracted_ids": ["id1"], "error": "previous error"}
        result = await mark_messages_extracted(state)
        assert result == {}
        mock_pg_pool.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_list_returns_zero(self, mock_pg_pool):
        from context_broker_ae.memory_extraction import mark_messages_extracted
        state = {"fully_extracted_ids": [], "error": None}
        result = await mark_messages_extracted(state)
        assert result["extracted_count"] == 0
        mock_pg_pool.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_extracted_count_matches_fully_extracted(self, mock_pg_pool):
        """extracted_count must equal len(fully_extracted_ids), NOT len(selected_message_ids)."""
        from context_broker_ae.memory_extraction import mark_messages_extracted
        state = {
            "fully_extracted_ids": [str(uuid.uuid4())],
            "selected_message_ids": [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())],
            "error": None,
        }
        result = await mark_messages_extracted(state)
        assert result["extracted_count"] == 1


# ── release_extraction_lock ──────────────────────────────────────────

class TestReleaseExtractionLock:
    @pytest.mark.asyncio
    async def test_releases_when_acquired(self, mock_redis):
        from context_broker_ae.memory_extraction import release_extraction_lock
        mock_redis.eval.return_value = 1
        state = {"lock_key": "test-lock", "lock_token": "test-token", "lock_acquired": True}
        result = await release_extraction_lock(state)
        assert result == {}
        mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_not_acquired(self, mock_redis):
        from context_broker_ae.memory_extraction import release_extraction_lock
        state = {"lock_key": "test-lock", "lock_token": None, "lock_acquired": False}
        result = await release_extraction_lock(state)
        mock_redis.eval.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_token(self, mock_redis):
        from context_broker_ae.memory_extraction import release_extraction_lock
        state = {"lock_key": "test-lock", "lock_token": None, "lock_acquired": True}
        result = await release_extraction_lock(state)
        mock_redis.eval.assert_not_called()


# ── Routing functions ────────────────────────────────────────────────

class TestRouting:
    def test_route_after_lock_acquired(self):
        assert route_after_lock({"lock_acquired": True}) == "fetch_unextracted_messages"

    def test_route_after_lock_not_acquired(self):
        from langgraph.graph import END
        assert route_after_lock({"lock_acquired": False}) == END

    def test_route_after_fetch_with_messages(self):
        assert route_after_fetch({"messages": [{"id": 1}]}) == "build_extraction_text"

    def test_route_after_fetch_no_messages(self):
        assert route_after_fetch({"messages": []}) == "release_extraction_lock"

    def test_route_after_build_text_success(self):
        assert route_after_build_text({"error": None}) == "run_mem0_extraction"

    def test_route_after_build_text_error(self):
        assert route_after_build_text({"error": "some error"}) == "release_extraction_lock"

    def test_route_after_extraction_success(self):
        assert route_after_extraction({"error": None}) == "mark_messages_extracted"

    def test_route_after_extraction_error(self):
        assert route_after_extraction({"error": "failed"}) == "release_extraction_lock"
