"""Tests for standard_tiered.py — budget guards, partial failure, lookback, tiers, locks."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from context_broker_ae.build_types.standard_tiered import (
    acquire_assembly_lock,
    calculate_tier_boundaries,
    finalize_assembly,
    load_messages,
    release_assembly_lock,
    summarize_message_chunks,
)
from context_broker_ae.build_types.tier_scaling import scale_tier_percentages


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WINDOW_ID = "22222222-2222-2222-2222-222222222222"
CONV_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def assembly_config(sample_config):
    """Config for assembly tests with standard-tiered build type."""
    cfg = dict(sample_config)
    cfg["build_types"]["standard-tiered"] = {
        "tier1_pct": 0.08,
        "tier2_pct": 0.20,
        "tier3_pct": 0.72,
        "initial_lookback_multiplier": 3,
        "effective_utilization": 0.85,
    }
    cfg.setdefault("tuning", {})
    cfg["tuning"]["chunk_size"] = 5
    cfg["tuning"]["tokens_per_message_estimate"] = 150
    cfg["tuning"]["consolidation_threshold"] = 3
    cfg["tuning"]["consolidation_keep_recent"] = 2
    return cfg


@pytest.fixture
def build_type_config(assembly_config):
    """Standard-tiered build type config."""
    return assembly_config["build_types"]["standard-tiered"]


def _make_message(seq, content="Hello", role="user", sender="alice", token_count=None):
    """Helper to create a message dict."""
    tc = token_count if token_count is not None else max(1, len(content) // 4)
    return {
        "id": uuid.uuid4(),
        "role": role,
        "sender": sender,
        "content": content,
        "sequence_number": seq,
        "token_count": tc,
        "created_at": datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# R7-M11: Budget guard — prevents summary insertion exceeding allocation
# ---------------------------------------------------------------------------

class TestBudgetGuard:
    """R7-M11: Stop inserting tier 2 summaries when cumulative tokens exceed budget."""

    @pytest.mark.asyncio
    async def test_budget_guard_stops_insertion(self, assembly_config, mock_pg_pool):
        """When cumulative summary tokens exceed tier1+tier2 budget, stop inserting."""
        pool, conn = mock_pg_pool

        # Create chunks that would exceed the budget
        # max_token_budget=8192, tier1_pct=0.08, tier2_pct=0.20
        # summary_budget = 8192 * (0.08 + 0.20) = 2293 (for 200 msgs, scaling applies)
        chunks = []
        for i in range(10):
            chunk = [_make_message(j, content="x" * 2000) for j in range(i * 5, i * 5 + 5)]
            chunks.append(chunk)

        # LLM returns long summaries that will exceed budget
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="S" * 4000)

        # fetchval for idempotency check returns None (no existing summary)
        pool.fetchval.return_value = None
        pool.execute.return_value = None

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": assembly_config,
            "build_type_config": assembly_config["build_types"]["standard-tiered"],
            "chunks": chunks,
            "all_messages": [_make_message(i) for i in range(200)],
            "max_token_budget": 8192,
            "lock_key": f"assembly_in_progress:{WINDOW_ID}",
            "lock_token": "tok",
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_chat_model",
            return_value=mock_llm,
        ), patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ), patch(
            "context_broker_ae.build_types.standard_tiered.async_load_prompt",
            new_callable=AsyncMock,
            return_value="Summarize:",
        ):
            result = await summarize_message_chunks(state)

        # Should have stopped before processing all 10 chunks
        inserted_count = len(result["tier2_summaries"])
        assert inserted_count < 10, f"Expected budget guard to stop early, but got {inserted_count}"

    @pytest.mark.asyncio
    async def test_budget_guard_allows_within_budget(self, assembly_config, mock_pg_pool):
        """Summaries within budget are all inserted."""
        pool, conn = mock_pg_pool

        # Small chunks with short summaries
        chunks = [
            [_make_message(j, content="short") for j in range(i * 5, i * 5 + 5)]
            for i in range(2)
        ]

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="Brief summary.")

        pool.fetchval.return_value = None
        pool.execute.return_value = None

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": assembly_config,
            "build_type_config": assembly_config["build_types"]["standard-tiered"],
            "chunks": chunks,
            "all_messages": [_make_message(i) for i in range(200)],
            "max_token_budget": 8192,
            "lock_key": f"assembly_in_progress:{WINDOW_ID}",
            "lock_token": "tok",
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_chat_model",
            return_value=mock_llm,
        ), patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ), patch(
            "context_broker_ae.build_types.standard_tiered.async_load_prompt",
            new_callable=AsyncMock,
            return_value="Summarize:",
        ):
            result = await summarize_message_chunks(state)

        assert len(result["tier2_summaries"]) == 2


# ---------------------------------------------------------------------------
# M-09: Partial failure tracking
# ---------------------------------------------------------------------------

class TestPartialFailureTracking:
    """M-09: Skip last_assembled_at update if errors occurred."""

    @pytest.mark.asyncio
    async def test_finalize_skips_update_on_errors(self, assembly_config, mock_pg_pool):
        """When had_errors=True, last_assembled_at is NOT updated."""
        pool, _ = mock_pg_pool

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": assembly_config,
            "had_errors": True,
            "assembly_start_time": None,
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ):
            await finalize_assembly(state)

        pool.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_finalize_updates_on_success(self, assembly_config, mock_pg_pool):
        """When had_errors is False/absent, last_assembled_at is updated."""
        pool, _ = mock_pg_pool

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": assembly_config,
            "had_errors": False,
            "assembly_start_time": None,
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ):
            await finalize_assembly(state)

        pool.execute.assert_called_once()
        call_sql = pool.execute.call_args[0][0]
        assert "last_assembled_at" in call_sql


# ---------------------------------------------------------------------------
# D-09: Initial assembly lookback
# ---------------------------------------------------------------------------

class TestInitialLookback:
    """D-09: First assembly looks further back for initial summaries."""

    @pytest.mark.asyncio
    async def test_first_assembly_uses_lookback_multiplier(
        self, assembly_config, mock_pg_pool
    ):
        """When no summaries exist, adaptive_limit is expanded by lookback multiplier."""
        pool, _ = mock_pg_pool

        # fetchval for summary count returns 0 (no existing summaries)
        pool.fetchval.return_value = 0
        pool.fetch.return_value = []

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": assembly_config,
            "build_type_config": assembly_config["build_types"]["standard-tiered"],
            "max_token_budget": 8192,
            "window": {"id": WINDOW_ID},
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ):
            await load_messages(state)

        # The LIMIT passed to the query should be larger than default
        fetch_call = pool.fetch.call_args
        limit_arg = fetch_call[0][2]  # third positional arg is the LIMIT

        # Default adaptive_limit = max(50, tier3_budget // 150)
        # tier3_budget = 8192 * 0.72 = 5898, adaptive_limit = max(50, 39) = 50
        # With lookback: lookback_tokens = 8192 * 3 = 24576, 24576//150 = 163
        assert limit_arg >= 100, f"Expected expanded lookback limit, got {limit_arg}"

    @pytest.mark.asyncio
    async def test_subsequent_assembly_normal_limit(
        self, assembly_config, mock_pg_pool
    ):
        """When summaries already exist, normal adaptive limit is used."""
        pool, _ = mock_pg_pool

        # fetchval for summary count returns >0
        pool.fetchval.return_value = 5
        pool.fetch.return_value = []

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": assembly_config,
            "build_type_config": assembly_config["build_types"]["standard-tiered"],
            "max_token_budget": 8192,
            "window": {"id": WINDOW_ID},
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ):
            await load_messages(state)

        fetch_call = pool.fetch.call_args
        limit_arg = fetch_call[0][2]
        # Normal adaptive_limit = max(50, 5898 // 150) = 50
        assert limit_arg == 50


# ---------------------------------------------------------------------------
# Tier boundary calculation
# ---------------------------------------------------------------------------

class TestTierBoundaryCalculation:
    """Correct split of messages into tier1/tier2/tier3 ranges."""

    @pytest.mark.asyncio
    async def test_tier3_fills_from_newest(self, assembly_config, mock_pg_pool):
        """Tier 3 walks backward from newest messages until budget is full."""
        pool, _ = mock_pg_pool

        # 20 messages, each 100 tokens
        messages = [_make_message(i, content="x" * 400, token_count=100) for i in range(20)]

        # tier3_budget = 8192 * 0.72 = ~5898 (for 200 msgs with scaling)
        # But for 20 msgs, short conversation scaling applies — tier3 gets more
        pool.fetch.return_value = []  # no existing t2 summaries

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": assembly_config,
            "build_type_config": assembly_config["build_types"]["standard-tiered"],
            "max_token_budget": 8192,
            "all_messages": messages,
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ):
            result = await calculate_tier_boundaries(state)

        # All 20 messages * 100 tokens = 2000 tokens, well within budget
        assert len(result["tier3_messages"]) == 20
        assert len(result["older_messages"]) == 0

    @pytest.mark.asyncio
    async def test_older_messages_excluded_from_tier3(self, assembly_config, mock_pg_pool):
        """Messages that don't fit in tier 3 budget go to older_messages."""
        pool, _ = mock_pg_pool

        # 100 messages, each 200 tokens = 20000 total tokens
        messages = [_make_message(i, content="x" * 800, token_count=200) for i in range(100)]

        pool.fetch.return_value = []  # no existing t2 summaries

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": assembly_config,
            "build_type_config": assembly_config["build_types"]["standard-tiered"],
            "max_token_budget": 8192,
            "all_messages": messages,
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ):
            result = await calculate_tier_boundaries(state)

        # Not all messages should fit in tier 3
        assert len(result["tier3_messages"]) < 100
        assert len(result["older_messages"]) > 0
        # tier3 + older should equal total
        assert len(result["tier3_messages"]) + len(result["older_messages"]) == 100

    def test_tier_scaling_short_conversation(self):
        """Short conversations boost tier3 at expense of tier1/tier2."""
        config = {"tier1_pct": 0.08, "tier2_pct": 0.20, "tier3_pct": 0.72}
        scaled = scale_tier_percentages(config, message_count=10)
        assert scaled["tier3_pct"] > 0.72
        assert scaled["tier1_pct"] < 0.08
        assert scaled["tier2_pct"] < 0.20

    def test_tier_scaling_long_conversation(self):
        """Long conversations boost tier1/tier2 at expense of tier3."""
        config = {"tier1_pct": 0.08, "tier2_pct": 0.20, "tier3_pct": 0.72}
        scaled = scale_tier_percentages(config, message_count=1500)
        assert scaled["tier3_pct"] < 0.72
        assert scaled["tier1_pct"] > 0.08
        assert scaled["tier2_pct"] > 0.20


# ---------------------------------------------------------------------------
# Advisory lock acquire and release
# ---------------------------------------------------------------------------

class TestAdvisoryLock:
    """Advisory lock acquire and release around assembly."""

    @pytest.mark.asyncio
    async def test_lock_acquired_successfully(self, assembly_config, mock_pg_pool):
        """When advisory lock is acquired, lock_acquired=True."""
        pool, _ = mock_pg_pool
        pool.fetchval.return_value = True  # pg_try_advisory_lock returns True

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": assembly_config,
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ):
            result = await acquire_assembly_lock(state)

        assert result["lock_acquired"] is True
        assert result["lock_token"] is not None

    @pytest.mark.asyncio
    async def test_lock_not_acquired(self, assembly_config, mock_pg_pool):
        """When advisory lock is held by another, lock_acquired=False."""
        pool, _ = mock_pg_pool
        pool.fetchval.return_value = False  # pg_try_advisory_lock returns False

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": assembly_config,
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ):
            result = await acquire_assembly_lock(state)

        assert result["lock_acquired"] is False

    @pytest.mark.asyncio
    async def test_lock_released(self, assembly_config, mock_pg_pool):
        """Release calls pg_advisory_unlock when lock was acquired."""
        pool, _ = mock_pg_pool

        state = {
            "context_window_id": WINDOW_ID,
            "config": assembly_config,
            "lock_key": f"assembly_in_progress:{WINDOW_ID}",
            "lock_acquired": True,
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ):
            await release_assembly_lock(state)

        pool.execute.assert_called_once()
        call_sql = pool.execute.call_args[0][0]
        assert "pg_advisory_unlock" in call_sql

    @pytest.mark.asyncio
    async def test_invalid_uuid_fails_gracefully(self, assembly_config):
        """Invalid UUID returns error without crashing."""
        state = {
            "context_window_id": "not-a-uuid",
            "conversation_id": CONV_ID,
            "config": assembly_config,
        }

        result = await acquire_assembly_lock(state)
        assert result["lock_acquired"] is False
        assert "Invalid UUID" in result.get("error", "")
