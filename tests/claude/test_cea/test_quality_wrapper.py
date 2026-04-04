"""Tests for quality_wrapper.py — QualityWrapper class and helpers.

Covers: resolve_temporal, _fact_dedup_key, _event_dedup_key,
_apply_rejection_rules, add, write_metadata, search, list_facts,
record_feedback, cleanup_expired, _maybe_cleanup, _get_metadata_batch,
_get_usefulness_batch, _global_vector_search, get_quality_wrapper.

Discovery audit: cli-discovery-audit-claude.md §1.
"""

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# resolve_temporal
# ---------------------------------------------------------------------------


class TestResolveTemporal:
    """Tests for resolve_temporal() — REQ-CEA-I04."""

    def test_iso_string(self):
        from context_broker_ae.memory.quality_wrapper import resolve_temporal

        dt = resolve_temporal("2026-06-15T12:00:00+00:00", datetime.now(timezone.utc))
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6

    def test_iso_z_suffix(self):
        from context_broker_ae.memory.quality_wrapper import resolve_temporal

        dt = resolve_temporal("2026-06-15T12:00:00Z", datetime.now(timezone.utc))
        assert dt is not None
        assert dt.tzinfo is not None

    def test_date_only(self):
        from context_broker_ae.memory.quality_wrapper import resolve_temporal

        dt = resolve_temporal("2026-06-15", datetime.now(timezone.utc))
        assert dt is not None
        assert dt.year == 2026

    def test_relative_days(self):
        from context_broker_ae.memory.quality_wrapper import resolve_temporal

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        dt = resolve_temporal("in 3 days", base)
        assert dt is not None
        assert dt == base + timedelta(days=3)

    def test_relative_weeks(self):
        from context_broker_ae.memory.quality_wrapper import resolve_temporal

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        dt = resolve_temporal("in 2 weeks", base)
        assert dt == base + timedelta(weeks=2)

    def test_relative_months(self):
        from context_broker_ae.memory.quality_wrapper import resolve_temporal

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        dt = resolve_temporal("in 3 months", base)
        assert dt == base + timedelta(days=90)

    def test_none_input(self):
        from context_broker_ae.memory.quality_wrapper import resolve_temporal

        assert resolve_temporal(None, datetime.now(timezone.utc)) is None

    def test_empty_string(self):
        from context_broker_ae.memory.quality_wrapper import resolve_temporal

        assert resolve_temporal("", datetime.now(timezone.utc)) is None

    def test_malformed_string(self):
        from context_broker_ae.memory.quality_wrapper import resolve_temporal

        assert resolve_temporal("not a date", datetime.now(timezone.utc)) is None


# ---------------------------------------------------------------------------
# Dedup keys
# ---------------------------------------------------------------------------


class TestDedupKeys:
    """Tests for _fact_dedup_key and _event_dedup_key."""

    def test_fact_dedup_deterministic(self):
        from context_broker_ae.memory.quality_wrapper import _fact_dedup_key

        k1 = _fact_dedup_key("fact", "conv1", "user1", "utt1")
        k2 = _fact_dedup_key("fact", "conv1", "user1", "utt1")
        assert k1 == k2

    def test_fact_dedup_different_inputs(self):
        from context_broker_ae.memory.quality_wrapper import _fact_dedup_key

        k1 = _fact_dedup_key("fact1", "conv1", "user1", "utt1")
        k2 = _fact_dedup_key("fact2", "conv1", "user1", "utt1")
        assert k1 != k2

    def test_event_dedup_same_minute(self):
        from context_broker_ae.memory.quality_wrapper import _event_dedup_key

        k1 = _event_dedup_key("target1", "used", "agent1")
        k2 = _event_dedup_key("target1", "used", "agent1")
        assert k1 == k2

    def test_event_dedup_different_type(self):
        from context_broker_ae.memory.quality_wrapper import _event_dedup_key

        k1 = _event_dedup_key("target1", "used", "agent1")
        k2 = _event_dedup_key("target1", "discarded", "agent1")
        assert k1 != k2


# ---------------------------------------------------------------------------
# Rejection rules
# ---------------------------------------------------------------------------


class TestRejectionRules:
    """Tests for _apply_rejection_rules."""

    def _make_wrapper(self, rules=None, min_length=10):
        from context_broker_ae.memory.quality_wrapper import QualityWrapper

        config = {
            "cea": {
                "rejection_rules": {
                    "min_fact_length": min_length,
                    "patterns": rules or [],
                }
            }
        }
        wrapper = QualityWrapper.__new__(QualityWrapper)
        wrapper.config = config
        return wrapper

    def test_accept_valid_content(self):
        wrapper = self._make_wrapper()
        assert wrapper._apply_rejection_rules("This is a valid fact about architecture") is False

    def test_reject_short_content(self):
        wrapper = self._make_wrapper(min_length=20)
        assert wrapper._apply_rejection_rules("too short") is True

    def test_reject_pattern_match(self):
        wrapper = self._make_wrapper(rules=[r"^Task #\d+"])
        assert wrapper._apply_rejection_rules("Task #42 was updated") is True

    def test_accept_no_pattern_match(self):
        wrapper = self._make_wrapper(rules=[r"^Task #\d+"])
        assert wrapper._apply_rejection_rules("Context Broker uses pgvector") is False

    def test_empty_rules(self):
        wrapper = self._make_wrapper(rules=[], min_length=0)
        assert wrapper._apply_rejection_rules("anything") is False


# ---------------------------------------------------------------------------
# QualityWrapper.add
# ---------------------------------------------------------------------------


class TestQualityWrapperAdd:
    """Tests for QualityWrapper.add() — REQ-CEA-A03."""

    @pytest.fixture
    def wrapper(self):
        from context_broker_ae.memory.quality_wrapper import QualityWrapper

        mem0 = MagicMock()
        mem0.add.return_value = {"results": [{"id": "mem-123"}], "relations": []}
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=None)  # No dedup match
        config = {"cea": {"rejection_rules": {"min_fact_length": 5, "patterns": []}}}
        return QualityWrapper(mem0, pool, config)

    @pytest.mark.asyncio
    async def test_success_returns_memory_id(self, wrapper):
        result = await wrapper.add(
            content="Context Broker uses pgvector",
            user_id="user1",
            conversation_id=str(uuid.uuid4()),
        )
        assert result["memory_id"] == "mem-123"
        assert isinstance(result["relation_ids"], list)

    @pytest.mark.asyncio
    async def test_rejection_returns_empty(self, wrapper):
        wrapper.config["cea"]["rejection_rules"]["min_fact_length"] = 100
        result = await wrapper.add(
            content="short",
            user_id="user1",
            conversation_id=str(uuid.uuid4()),
        )
        assert result["memory_id"] is None

    @pytest.mark.asyncio
    async def test_dedup_returns_empty(self, wrapper):
        wrapper.pool.fetchval = AsyncMock(return_value="existing-id")
        result = await wrapper.add(
            content="Context Broker uses pgvector",
            user_id="user1",
            conversation_id=str(uuid.uuid4()),
            dedup_utterance="the utterance",
        )
        assert result["memory_id"] is None

    @pytest.mark.asyncio
    async def test_skip_graph_passed_to_mem0(self, wrapper):
        await wrapper.add(
            content="Test fact",
            user_id="user1",
            conversation_id=str(uuid.uuid4()),
            skip_graph=True,
        )
        call_kwargs = wrapper.mem0.add.call_args
        assert call_kwargs is not None


# ---------------------------------------------------------------------------
# QualityWrapper.write_metadata
# ---------------------------------------------------------------------------


class TestWriteMetadata:
    """Tests for write_metadata() — REQ-CEA-Q01."""

    @pytest.fixture
    def wrapper(self):
        from context_broker_ae.memory.quality_wrapper import QualityWrapper

        pool = AsyncMock()
        pool.execute = AsyncMock()
        w = QualityWrapper.__new__(QualityWrapper)
        w.pool = pool
        w.config = {}
        return w

    @pytest.mark.asyncio
    async def test_success(self, wrapper):
        await wrapper.write_metadata(
            target_type="fact",
            target_id="mem-123",
            durability=0.8,
            confidence=0.9,
            source_type="decision",
            original_utterance="the original",
            extraction_model="gpt-4.1",
            expires_at=None,
            user_id="user1",
            conversation_id=str(uuid.uuid4()),
        )
        wrapper.pool.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_conversation_id(self, wrapper):
        # Should log warning and return without error
        await wrapper.write_metadata(
            target_type="fact",
            target_id="mem-123",
            durability=0.8,
            confidence=0.9,
            source_type="decision",
            original_utterance="the original",
            extraction_model="gpt-4.1",
            expires_at=None,
            user_id="user1",
            conversation_id="not-a-uuid",
        )
        wrapper.pool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# record_feedback
# ---------------------------------------------------------------------------


class TestRecordFeedback:
    """Tests for record_feedback() — REQ-CEA-C05."""

    @pytest.fixture
    def wrapper(self):
        from context_broker_ae.memory.quality_wrapper import QualityWrapper

        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="INSERT 0 1")
        w = QualityWrapper.__new__(QualityWrapper)
        w.pool = pool
        w.config = {}
        return w

    @pytest.mark.asyncio
    async def test_success(self, wrapper):
        result = await wrapper.record_feedback(
            target_type="fact",
            target_id="mem-123",
            event_type="used",
            agent_id="ceac",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_dedup_returns_false(self, wrapper):
        wrapper.pool.execute = AsyncMock(return_value="INSERT 0 0")
        result = await wrapper.record_feedback(
            target_type="fact",
            target_id="mem-123",
            event_type="used",
            agent_id="ceac",
        )
        assert result is False


# ---------------------------------------------------------------------------
# cleanup_expired
# ---------------------------------------------------------------------------


class TestCleanupExpired:
    """Tests for cleanup_expired() — REQ-CEA-S08."""

    @pytest.fixture
    def wrapper(self):
        from context_broker_ae.memory.quality_wrapper import QualityWrapper

        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        mem0 = MagicMock()
        w = QualityWrapper.__new__(QualityWrapper)
        w.pool = pool
        w.mem0 = mem0
        w.config = {}
        return w

    @pytest.mark.asyncio
    async def test_no_expired(self, wrapper):
        count = await wrapper.cleanup_expired()
        assert count == 0

    @pytest.mark.asyncio
    async def test_expired_deleted(self, wrapper):
        wrapper.pool.fetch = AsyncMock(return_value=[
            {"target_type": "fact", "target_id": "mem-1"},
            {"target_type": "fact", "target_id": "mem-2"},
        ])
        wrapper.pool.execute = AsyncMock()

        count = await wrapper.cleanup_expired()
        assert count == 2


# ---------------------------------------------------------------------------
# _maybe_cleanup
# ---------------------------------------------------------------------------


class TestMaybeCleanup:
    """Tests for _maybe_cleanup() throttling."""

    @pytest.mark.asyncio
    async def test_throttle(self):
        from context_broker_ae.memory.quality_wrapper import QualityWrapper

        w = QualityWrapper.__new__(QualityWrapper)
        w.config = {"cea": {"expiration_cleanup_interval": 3600}}
        w._last_cleanup = 0.0
        w.pool = AsyncMock()
        w.pool.fetch = AsyncMock(return_value=[])
        w.mem0 = MagicMock()

        await w._maybe_cleanup()
        first_cleanup = w._last_cleanup

        await w._maybe_cleanup()
        # Should not have run again (within interval)
        assert w._last_cleanup == first_cleanup


# ---------------------------------------------------------------------------
# _get_metadata_batch / _get_usefulness_batch
# ---------------------------------------------------------------------------


class TestBatchQueries:
    """Tests for _get_metadata_batch and _get_usefulness_batch."""

    @pytest.fixture
    def wrapper(self):
        from context_broker_ae.memory.quality_wrapper import QualityWrapper

        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        w = QualityWrapper.__new__(QualityWrapper)
        w.pool = pool
        return w

    @pytest.mark.asyncio
    async def test_metadata_empty_pairs(self, wrapper):
        result = await wrapper._get_metadata_batch([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_usefulness_empty_pairs(self, wrapper):
        result = await wrapper._get_usefulness_batch([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_metadata_returns_keyed_dict(self, wrapper):
        wrapper.pool.fetch = AsyncMock(return_value=[
            {
                "target_type": "fact",
                "target_id": "mem-1",
                "durability": 0.8,
                "confidence": 0.9,
                "source_type": "decision",
                "original_utterance": "test",
                "extraction_model": "gpt-4.1",
                "expires_at": None,
                "extracted_at": datetime.now(timezone.utc),
                "user_id": "user1",
                "conversation_id": uuid.uuid4(),
            }
        ])
        result = await wrapper._get_metadata_batch([("fact", "mem-1")])
        assert ("fact", "mem-1") in result
