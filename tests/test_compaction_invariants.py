"""
Compaction v2 invariant tests.

Verifies the deadband compaction design invariants using mocked database
calls.  Each test class targets one specific invariant from the compaction
design document.

Imports under test:
  - context_broker_ae.build_types.tier_scaling
  - context_broker_ae.build_types.standard_tiered
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from context_broker_ae.build_types.tier_scaling import (
    calculate_tier1_ceiling,
    extract_deadband_config,
    should_trigger_compaction,
)
from context_broker_ae.build_types.standard_tiered import (
    calculate_compaction_state,
    route_after_compact_tier1,
    run_full_compaction,
)


# ------------------------------------------------------------------
# Constants and helpers
# ------------------------------------------------------------------

STANDARD_BUDGET = 1_000_000
WINDOW_ID = "22222222-2222-2222-2222-222222222222"
CONV_ID = "11111111-1111-1111-1111-111111111111"


def _base_config() -> dict:
    """Standard tiered-summary build type config with deadband settings."""
    return {
        "tier1_floor_pct": 0.20,
        "tier2_chunk_pct": 0.02,
        "tier3_pct": 0.02,
        "tier2_min_chunks": 3,
        "tier2_max_chunks": 6,
        "tier3_header_pct": 0.0025,
        "effective_utilization": 0.85,
    }


def _make_message(seq, content="Hello", role="user", sender="alice", token_count=None):
    """Create a message dict matching the schema used by standard_tiered."""
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


def _assembly_config() -> dict:
    """Full application config suitable for assembly state dicts."""
    return {
        "llm": {
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:14b",
            "api_key_env": "LLM_API_KEY",
        },
        "build_types": {
            "tiered-summary": _base_config(),
        },
        "tuning": {
            "chunk_size": 5,
            "tokens_per_message_estimate": 150,
            "consolidation_threshold": 3,
            "consolidation_keep_recent": 2,
        },
    }


class _AsyncContextManager:
    """Helper for mocking async context managers (async with)."""
    def __init__(self, return_value):
        self._return_value = return_value

    async def __aenter__(self):
        return self._return_value

    async def __aexit__(self, *args):
        return False


def _mock_pg_pool():
    """Create a mock asyncpg pool with acquire/transaction context managers."""
    pool = MagicMock()
    conn = MagicMock()  # conn methods that return context managers must be MagicMock

    # pool.acquire() and conn.transaction() return async context managers synchronously
    pool.acquire.return_value = _AsyncContextManager(conn)
    conn.transaction.return_value = _AsyncContextManager(None)

    # async methods on conn (execute, fetch, etc.)
    conn.execute = AsyncMock()

    # async methods on pool (fetch, fetchval, fetchrow, execute)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()

    return pool, conn


# ------------------------------------------------------------------
# Invariant 1: Tier 1 floor reset
# ------------------------------------------------------------------


class TestTier1FloorReset:
    """After compaction, tier 1 should not drop below floor (20% of budget).

    calculate_compaction_state walks backward from newest messages.  When
    enough messages exist to exceed the floor, all of them should appear in
    tier1_messages and their cumulative tokens should be >= floor.
    """

    @pytest.mark.asyncio
    async def test_tier1_above_floor_when_enough_messages(self):
        """Tier 1 messages consume at least tier1_floor_pct * budget tokens."""
        pool, conn = _mock_pg_pool()
        config = _assembly_config()
        bt_config = config["build_types"]["tiered-summary"]
        db = extract_deadband_config(bt_config)
        floor_tokens = int(STANDARD_BUDGET * db["tier1_floor_pct"])

        # Create enough messages so tier 1 fills well past the floor.
        # 300 messages * 1000 tokens = 300K > 200K floor, < 710K ceiling.
        messages = [
            _make_message(i, content="x" * 4000, token_count=1000)
            for i in range(300)
        ]

        # DB: 0 active tier 2 chunks, no existing summaries
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetch = AsyncMock(return_value=[])

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": config,
            "build_type_config": bt_config,
            "max_token_budget": STANDARD_BUDGET,
            "all_messages": messages,
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ):
            result = await calculate_compaction_state(state)

        tier1_tokens = sum(m["token_count"] for m in result["tier1_messages"])
        assert tier1_tokens >= floor_tokens, (
            f"Tier 1 tokens ({tier1_tokens}) below floor ({floor_tokens})"
        )

    @pytest.mark.asyncio
    async def test_tier1_respects_floor_with_large_messages(self):
        """Even with large messages, tier 1 fills to at least floor tokens."""
        pool, conn = _mock_pg_pool()
        config = _assembly_config()
        bt_config = config["build_types"]["tiered-summary"]
        db = extract_deadband_config(bt_config)
        floor_tokens = int(STANDARD_BUDGET * db["tier1_floor_pct"])

        # 50 large messages at 10K tokens each = 500K total.
        # All fit within ceiling (710K), so all should be in tier 1.
        messages = [
            _make_message(i, content="x" * 40000, token_count=10000)
            for i in range(50)
        ]

        pool.fetchval = AsyncMock(return_value=0)
        pool.fetch = AsyncMock(return_value=[])

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": config,
            "build_type_config": bt_config,
            "max_token_budget": STANDARD_BUDGET,
            "all_messages": messages,
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ):
            result = await calculate_compaction_state(state)

        tier1_tokens = sum(m["token_count"] for m in result["tier1_messages"])
        assert tier1_tokens >= floor_tokens


# ------------------------------------------------------------------
# Invariant 2: Tier 2 max chunk cap
# ------------------------------------------------------------------


class TestTier2MaxChunkCap:
    """tier2_at_max flag should be True when active tier 2 count >= tier2_max_chunks."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "active_count, expected_at_max",
        [
            (0, False),
            (3, False),   # tier2_min_chunks
            (5, False),
            (6, True),    # tier2_max_chunks
            (7, True),    # over max
        ],
    )
    async def test_tier2_at_max_flag(self, active_count, expected_at_max):
        """tier2_at_max reflects whether active tier 2 count >= max_chunks."""
        pool, conn = _mock_pg_pool()
        config = _assembly_config()
        bt_config = config["build_types"]["tiered-summary"]

        # Enough messages that older_messages is non-empty (triggers tier2_at_max calc).
        # 200 messages at 5000 tokens each = 1M total — exceeds ceiling, forces a split.
        messages = [
            _make_message(i, content="x" * 20000, token_count=5000)
            for i in range(200)
        ]

        # fetchval for tier 2 count
        pool.fetchval = AsyncMock(return_value=active_count)
        # fetch for existing tier 2 summaries — return empty (no max_summarized_seq)
        pool.fetch = AsyncMock(return_value=[])

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": config,
            "build_type_config": bt_config,
            "max_token_budget": STANDARD_BUDGET,
            "all_messages": messages,
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ):
            result = await calculate_compaction_state(state)

        assert result["tier2_at_max"] is expected_at_max, (
            f"Expected tier2_at_max={expected_at_max} for {active_count} active chunks"
        )


# ------------------------------------------------------------------
# Invariant 3: Full compaction cadence
# ------------------------------------------------------------------


class TestFullCompactionCadence:
    """route_after_compact_tier1 routes to full compaction when tier 2 is at max."""

    def test_routes_to_full_compaction_when_at_max(self):
        """When tier2_at_max=True, route returns 'run_full_compaction'."""
        state = {"tier2_at_max": True, "error": None}
        assert route_after_compact_tier1(state) == "run_full_compaction"

    def test_routes_to_finalize_when_not_at_max(self):
        """When tier2_at_max=False, route returns 'finalize_assembly'."""
        state = {"tier2_at_max": False, "error": None}
        assert route_after_compact_tier1(state) == "finalize_assembly"

    def test_routes_to_release_lock_on_error(self):
        """When there is an error, route returns 'release_assembly_lock'."""
        state = {"tier2_at_max": True, "error": "Something went wrong"}
        assert route_after_compact_tier1(state) == "release_assembly_lock"


# ------------------------------------------------------------------
# Invariant 4: Tier 3 header persistence
# ------------------------------------------------------------------


class TestTier3HeaderPersistence:
    """After run_full_compaction, two tier 3 rows should exist:
    one with seq 0-0 (historical header) and one with the consolidated range.
    """

    @pytest.mark.asyncio
    async def test_two_tier3_rows_after_full_compaction(self):
        """Full compaction inserts a header row (0-0) and a consolidated range row."""
        pool, conn = _mock_pg_pool()
        config = _assembly_config()
        bt_config = config["build_types"]["tiered-summary"]

        # Simulate 6 active tier 2 chunks (at max)
        active_t2 = [
            {
                "id": uuid.uuid4(),
                "summary_text": f"Summary of chunk {i}",
                "summarizes_from_seq": i * 20 + 1,
                "summarizes_to_seq": (i + 1) * 20,
                "message_count": 20,
            }
            for i in range(6)
        ]

        # pool.fetch returns active tier 2 chunks
        pool.fetch = AsyncMock(return_value=active_t2)
        # pool.fetchrow returns existing tier 3 (or None for first compaction)
        pool.fetchrow = AsyncMock(return_value=None)

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            side_effect=[
                MagicMock(content="Consolidated recent archival text."),
                MagicMock(content="Historical header: conversation started 2026-03-01."),
            ]
        )

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": config,
            "build_type_config": bt_config,
            "lock_key": f"assembly_in_progress:{WINDOW_ID}",
            "lock_token": "tok",
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ), patch(
            "context_broker_ae.build_types.standard_tiered.get_chat_model",
            return_value=mock_llm,
        ), patch(
            "context_broker_ae.build_types.standard_tiered.async_load_prompt",
            new_callable=AsyncMock,
            return_value="Consolidate archival summaries:",
        ):
            result = await run_full_compaction(state)

        assert result["tier3_summary"] is not None

        # Verify two INSERT calls on the connection: header (0,0) and consolidated range
        execute_calls = conn.execute.call_args_list
        insert_calls = [
            c for c in execute_calls
            if "INSERT INTO conversation_summaries" in str(c)
        ]
        assert len(insert_calls) == 2, (
            f"Expected 2 tier 3 INSERT calls, got {len(insert_calls)}"
        )

        # First INSERT: historical header — SQL contains hardcoded seq 0, 0
        header_sql = insert_calls[0][0][0]  # first positional arg is the SQL string
        assert "3, 0, 0, 0" in header_sql, "Header INSERT should have tier=3, from_seq=0, to_seq=0, count=0"

        # Second INSERT: consolidated range covering the chunk span
        # call_args[0] = (sql, $1=conv_id, $2=window_id, $3=text, $4=from_seq, $5=to_seq, $6=count, $7=model)
        range_call_args = insert_calls[1][0]
        # keep_recent = tier2_min_chunks - 1 = 2, so consolidate first 4 chunks
        assert range_call_args[4] == active_t2[0]["summarizes_from_seq"]
        assert range_call_args[5] == active_t2[3]["summarizes_to_seq"]

    @pytest.mark.asyncio
    async def test_existing_tier3_header_included_in_consolidation(self):
        """When a tier 3 header already exists, it is fed into the LLM for update."""
        pool, conn = _mock_pg_pool()
        config = _assembly_config()
        bt_config = config["build_types"]["tiered-summary"]

        active_t2 = [
            {
                "id": uuid.uuid4(),
                "summary_text": f"Summary of chunk {i}",
                "summarizes_from_seq": i * 20 + 1,
                "summarizes_to_seq": (i + 1) * 20,
                "message_count": 20,
            }
            for i in range(6)
        ]

        pool.fetch = AsyncMock(return_value=active_t2)
        # Existing tier 3 header
        pool.fetchrow = AsyncMock(return_value={
            "summary_text": "Existing header: started 2026-03-01, topic=testing."
        })

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            side_effect=[
                MagicMock(content="Updated recent archival."),
                MagicMock(content="Updated header with new topics."),
            ]
        )

        state = {
            "context_window_id": WINDOW_ID,
            "conversation_id": CONV_ID,
            "config": config,
            "build_type_config": bt_config,
            "lock_key": f"assembly_in_progress:{WINDOW_ID}",
            "lock_token": "tok",
        }

        with patch(
            "context_broker_ae.build_types.standard_tiered.get_pg_pool",
            return_value=pool,
        ), patch(
            "context_broker_ae.build_types.standard_tiered.get_chat_model",
            return_value=mock_llm,
        ), patch(
            "context_broker_ae.build_types.standard_tiered.async_load_prompt",
            new_callable=AsyncMock,
            return_value="Consolidate archival summaries:",
        ):
            result = await run_full_compaction(state)

        assert result["tier3_summary"] is not None

        # The second LLM call (header update) should receive the existing header
        header_llm_call = mock_llm.ainvoke.call_args_list[1]
        header_input = header_llm_call[0][0][1].content  # HumanMessage content
        assert "Existing historical header" in header_input


# ------------------------------------------------------------------
# Invariant 5: Dynamic ceiling cross-check
# ------------------------------------------------------------------


class TestDynamicCeiling:
    """When tier 2 has fewer chunks, the tier 1 ceiling should be higher.

    This cross-checks the invariant also tested in test_tier_scaling.py
    but uses the full deadband config extraction path.
    """

    def test_ceiling_higher_with_fewer_chunks(self):
        """ceiling(3 chunks) > ceiling(6 chunks) for the same budget."""
        db = extract_deadband_config(_base_config())
        ceiling_3 = calculate_tier1_ceiling(STANDARD_BUDGET, db, current_tier2_chunks=3)
        ceiling_6 = calculate_tier1_ceiling(STANDARD_BUDGET, db, current_tier2_chunks=6)
        assert ceiling_3 > ceiling_6

    def test_ceiling_difference_equals_displaced_chunk_tokens(self):
        """The difference equals the token cost of the displaced chunks."""
        db = extract_deadband_config(_base_config())
        ceiling_3 = calculate_tier1_ceiling(STANDARD_BUDGET, db, current_tier2_chunks=3)
        ceiling_6 = calculate_tier1_ceiling(STANDARD_BUDGET, db, current_tier2_chunks=6)
        # 3 fewer chunks * 2% each * 1M = 60K
        expected_diff = int(STANDARD_BUDGET * db["tier2_chunk_pct"] * 3)
        assert ceiling_3 - ceiling_6 == expected_diff

    def test_ceiling_monotonically_decreases_with_more_chunks(self):
        """Ceiling should decrease (or stay same) as chunk count increases."""
        db = extract_deadband_config(_base_config())
        prev_ceiling = calculate_tier1_ceiling(STANDARD_BUDGET, db, current_tier2_chunks=0)
        for n in range(1, 8):
            ceiling = calculate_tier1_ceiling(STANDARD_BUDGET, db, current_tier2_chunks=n)
            assert ceiling <= prev_ceiling, (
                f"Ceiling at {n} chunks ({ceiling}) > ceiling at {n-1} chunks ({prev_ceiling})"
            )
            prev_ceiling = ceiling


# ------------------------------------------------------------------
# Invariant 6: Compaction trigger
# ------------------------------------------------------------------


class TestCompactionTrigger:
    """should_trigger_compaction returns True when tier 1 tokens exceed ceiling."""

    def test_triggers_above_ceiling(self):
        """Tokens above ceiling triggers compaction."""
        db = extract_deadband_config(_base_config())
        ceiling = calculate_tier1_ceiling(STANDARD_BUDGET, db)
        assert should_trigger_compaction(ceiling + 1, STANDARD_BUDGET, db) is True

    def test_no_trigger_below_ceiling(self):
        """Tokens below ceiling does not trigger compaction."""
        db = extract_deadband_config(_base_config())
        ceiling = calculate_tier1_ceiling(STANDARD_BUDGET, db)
        assert should_trigger_compaction(ceiling - 1, STANDARD_BUDGET, db) is False

    def test_no_trigger_at_exact_ceiling(self):
        """Exactly at ceiling does not trigger (requires exceeding, not equal)."""
        db = extract_deadband_config(_base_config())
        ceiling = calculate_tier1_ceiling(STANDARD_BUDGET, db)
        assert should_trigger_compaction(ceiling, STANDARD_BUDGET, db) is False

    def test_trigger_respects_dynamic_ceiling(self):
        """Compaction trigger uses dynamic ceiling based on current chunk count."""
        db = extract_deadband_config(_base_config())
        ceiling_3 = calculate_tier1_ceiling(STANDARD_BUDGET, db, current_tier2_chunks=3)
        ceiling_6 = calculate_tier1_ceiling(STANDARD_BUDGET, db, current_tier2_chunks=6)

        # A token count between the two ceilings: triggers at 6 chunks, not at 3
        mid_tokens = (ceiling_3 + ceiling_6) // 2 + 1
        assert should_trigger_compaction(mid_tokens, STANDARD_BUDGET, db, current_tier2_chunks=6) is True
        assert should_trigger_compaction(mid_tokens, STANDARD_BUDGET, db, current_tier2_chunks=3) is False

    def test_zero_tokens_never_triggers(self):
        """Zero tier 1 usage never triggers compaction."""
        db = extract_deadband_config(_base_config())
        assert should_trigger_compaction(0, STANDARD_BUDGET, db) is False
