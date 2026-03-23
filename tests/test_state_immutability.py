"""
Unit tests for StateGraph state immutability (test plan section 4.11).

For key node and routing functions, verify that the input state dict
is not mutated by the function call. This ensures LangGraph's state
management is not corrupted by side effects.
"""

import copy

import pytest

from app.flows.message_pipeline import route_after_store
from app.flows.build_types.tier_scaling import scale_tier_percentages
from app.flows.memory_scoring import filter_and_rank_memories, score_memory


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _assert_state_unchanged(original: dict, after: dict, fn_name: str):
    """Assert that two dicts are identical, providing a clear error message."""
    assert original == after, (
        f"{fn_name} mutated its input state. "
        f"Diff: expected {original}, got {after}"
    )


# ------------------------------------------------------------------
# route_after_store (message pipeline routing)
# ------------------------------------------------------------------


class TestRouteAfterStoreImmutability:
    """Verify route_after_store does not mutate its input state."""

    def test_no_error_no_collapse(self):
        """Normal path: route to enqueue_background_jobs without mutation."""
        state = {
            "context_window_id": "abc",
            "conversation_id_input": None,
            "role": "user",
            "sender": "user-1",
            "recipient": None,
            "content": "Hello",
            "model_name": None,
            "tool_calls": None,
            "tool_call_id": None,
            "message_id": "msg-1",
            "conversation_id": "conv-1",
            "sequence_number": 1,
            "was_collapsed": False,
            "queued_jobs": [],
            "error": None,
        }
        original = copy.deepcopy(state)
        route_after_store(state)
        _assert_state_unchanged(original, state, "route_after_store")

    def test_with_error(self):
        """Error path: routes to END without mutation."""
        state = {
            "error": "Something went wrong",
            "was_collapsed": False,
        }
        original = copy.deepcopy(state)
        route_after_store(state)
        _assert_state_unchanged(original, state, "route_after_store (error)")

    def test_with_collapse(self):
        """Collapse path: routes to END without mutation."""
        state = {
            "error": None,
            "was_collapsed": True,
        }
        original = copy.deepcopy(state)
        route_after_store(state)
        _assert_state_unchanged(original, state, "route_after_store (collapse)")


# ------------------------------------------------------------------
# scale_tier_percentages (tier scaling)
# ------------------------------------------------------------------


class TestScaleTierPercentagesImmutability:
    """Verify scale_tier_percentages does not mutate its input."""

    def test_short_conversation(self):
        """Short conversation scaling does not mutate input."""
        config = {
            "tier1_pct": 0.08,
            "tier2_pct": 0.20,
            "tier3_pct": 0.72,
            "fallback_tokens": 8192,
        }
        original = copy.deepcopy(config)
        scale_tier_percentages(config, 10)
        _assert_state_unchanged(original, config, "scale_tier_percentages (short)")

    def test_long_conversation(self):
        """Long conversation scaling does not mutate input."""
        config = {
            "tier1_pct": 0.08,
            "tier2_pct": 0.20,
            "tier3_pct": 0.72,
        }
        original = copy.deepcopy(config)
        scale_tier_percentages(config, 1000)
        _assert_state_unchanged(original, config, "scale_tier_percentages (long)")

    def test_medium_conversation(self):
        """Medium conversation (no-op) does not mutate input."""
        config = {
            "tier1_pct": 0.08,
            "tier2_pct": 0.20,
            "tier3_pct": 0.72,
        }
        original = copy.deepcopy(config)
        scale_tier_percentages(config, 200)
        _assert_state_unchanged(original, config, "scale_tier_percentages (medium)")


# ------------------------------------------------------------------
# score_memory (memory scoring)
# ------------------------------------------------------------------


class TestScoreMemoryImmutability:
    """Verify score_memory does not mutate its input memory dict."""

    def test_standard_memory(self):
        """Scoring a standard memory does not mutate the input."""
        from datetime import datetime, timezone
        mem = {
            "id": "mem-1",
            "content": "Test",
            "category": "factual",
            "created_at": datetime(2026, 3, 1, tzinfo=timezone.utc).isoformat(),
            "last_accessed": datetime(2026, 3, 20, tzinfo=timezone.utc).isoformat(),
        }
        config = {"tuning": {"memory_half_lives": {"factual": 60, "default": 30}}}
        original = copy.deepcopy(mem)
        score_memory(mem, config)
        _assert_state_unchanged(original, mem, "score_memory")

    def test_memory_without_dates(self):
        """Scoring a memory with missing dates does not mutate."""
        mem = {"category": "default", "created_at": None}
        config = {"tuning": {}}
        original = copy.deepcopy(mem)
        score_memory(mem, config)
        _assert_state_unchanged(original, mem, "score_memory (no dates)")


# ------------------------------------------------------------------
# filter_and_rank_memories
# ------------------------------------------------------------------


class TestFilterAndRankImmutability:
    """Verify filter_and_rank_memories does not mutate its input list or dicts."""

    def test_input_list_not_mutated(self):
        """The original list is not modified (no in-place sort, no dict mutation)."""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        memories = [
            {
                "id": "mem-1",
                "content": "Test A",
                "category": "factual",
                "created_at": (now - timedelta(days=5)).isoformat(),
                "last_accessed": None,
            },
            {
                "id": "mem-2",
                "content": "Test B",
                "category": "ephemeral",
                "created_at": (now - timedelta(days=100)).isoformat(),
                "last_accessed": None,
            },
        ]
        config = {"tuning": {"memory_half_lives": {"factual": 60, "ephemeral": 3, "default": 30}}}
        original = copy.deepcopy(memories)
        filter_and_rank_memories(memories, config)
        _assert_state_unchanged(original, memories, "filter_and_rank_memories")

    def test_individual_dicts_not_mutated(self):
        """Individual memory dicts do not gain a confidence_score key."""
        from datetime import datetime, timezone
        mem = {
            "id": "mem-1",
            "content": "Test",
            "category": "default",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_accessed": None,
        }
        memories = [mem]
        config = {"tuning": {}}
        filter_and_rank_memories(memories, config)
        # The original dict should NOT have confidence_score added
        assert "confidence_score" not in mem
