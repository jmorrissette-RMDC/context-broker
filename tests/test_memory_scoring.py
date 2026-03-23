"""
Unit tests for memory confidence scoring (app.flows.memory_scoring).

Covers half-life decay math, category-based scoring, filter/rank logic,
zero half-life safety, and input immutability.
"""

import copy
import math
from datetime import datetime, timedelta, timezone

import pytest

from app.flows.memory_scoring import (
    DEFAULT_HALF_LIVES,
    filter_and_rank_memories,
    score_memory,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_memory(
    category: str = "default",
    age_days: float = 0.0,
    last_accessed_days_ago: float | None = None,
) -> dict:
    """Create a memory dict with a created_at relative to now."""
    now = datetime.now(timezone.utc)
    created = now - timedelta(days=age_days)
    mem = {
        "id": f"mem-{category}-{age_days}",
        "content": f"Test memory ({category}, {age_days}d old)",
        "category": category,
        "created_at": created.isoformat(),
        "last_accessed": None,
        "user_id": "user-1",
    }
    if last_accessed_days_ago is not None:
        mem["last_accessed"] = (now - timedelta(days=last_accessed_days_ago)).isoformat()
    return mem


def _config_with_half_lives(overrides: dict | None = None) -> dict:
    """Return a config dict with tuning.memory_half_lives."""
    half_lives = dict(DEFAULT_HALF_LIVES)
    if overrides:
        half_lives.update(overrides)
    return {"tuning": {"memory_half_lives": half_lives}}


# ------------------------------------------------------------------
# score_memory
# ------------------------------------------------------------------


class TestScoreMemory:
    """Tests for the score_memory function."""

    def test_brand_new_memory_scores_near_one(self):
        """A memory created just now should score very close to 1.0."""
        mem = _make_memory(category="factual", age_days=0.0)
        config = _config_with_half_lives()
        score = score_memory(mem, config)
        assert score > 0.99

    def test_one_half_life_scores_half(self):
        """A memory aged exactly one half-life should score ~0.5."""
        half_life = DEFAULT_HALF_LIVES["factual"]  # 60 days
        mem = _make_memory(category="factual", age_days=half_life)
        config = _config_with_half_lives()
        score = score_memory(mem, config)
        assert abs(score - 0.5) < 0.02  # Allow small float tolerance

    def test_two_half_lives_scores_quarter(self):
        """A memory aged two half-lives should score ~0.25."""
        half_life = DEFAULT_HALF_LIVES["contextual"]  # 14 days
        mem = _make_memory(category="contextual", age_days=half_life * 2)
        config = _config_with_half_lives()
        score = score_memory(mem, config)
        assert abs(score - 0.25) < 0.02

    def test_half_life_decay_math(self):
        """Verify the exponential decay formula: score = 0.5^(age/half_life)."""
        age_days = 10
        half_life = 30
        config = _config_with_half_lives({"default": half_life})
        mem = _make_memory(category="default", age_days=age_days)
        score = score_memory(mem, config)
        expected = math.pow(0.5, age_days / half_life)
        assert abs(score - expected) < 0.01

    def test_ephemeral_decays_faster_than_historical(self):
        """Ephemeral memories (3d half-life) decay faster than historical (365d)."""
        age = 10  # 10 days old
        config = _config_with_half_lives()
        ephemeral_score = score_memory(_make_memory("ephemeral", age), config)
        historical_score = score_memory(_make_memory("historical", age), config)
        assert ephemeral_score < historical_score

    def test_unknown_category_uses_default(self):
        """An unrecognized category falls back to the 'default' half-life."""
        config = _config_with_half_lives()
        mem = _make_memory(category="made_up_category", age_days=30)
        score = score_memory(mem, config)
        # default half-life is 30, so at 30 days should be ~0.5
        assert abs(score - 0.5) < 0.02

    def test_missing_created_at_returns_neutral(self):
        """A memory with no created_at returns 0.5 (neutral score)."""
        mem = {"category": "factual", "created_at": None}
        config = _config_with_half_lives()
        score = score_memory(mem, config)
        assert score == 0.5

    def test_recently_accessed_boost(self):
        """A recently accessed memory gets a 30% boost."""
        age = 30
        config = _config_with_half_lives()
        # Not accessed
        mem_no_access = _make_memory("default", age_days=age)
        score_no_access = score_memory(mem_no_access, config)

        # Accessed 2 days ago (within 7-day boost window)
        mem_accessed = _make_memory("default", age_days=age, last_accessed_days_ago=2)
        score_accessed = score_memory(mem_accessed, config)

        assert score_accessed > score_no_access
        # Boost should be ~1.3x (capped at 1.0)
        assert abs(score_accessed / score_no_access - 1.3) < 0.05

    def test_old_access_no_boost(self):
        """A memory last accessed > 7 days ago does not get a boost."""
        age = 30
        config = _config_with_half_lives()
        mem_no_access = _make_memory("default", age_days=age)
        score_no_access = score_memory(mem_no_access, config)

        mem_old_access = _make_memory("default", age_days=age, last_accessed_days_ago=10)
        score_old_access = score_memory(mem_old_access, config)

        assert abs(score_no_access - score_old_access) < 0.01

    def test_zero_half_life_does_not_crash(self):
        """A zero half-life in config does not cause division by zero (clamped to 1)."""
        config = {"tuning": {"memory_half_lives": {"ephemeral": 0, "default": 30}}}
        mem = _make_memory("ephemeral", age_days=5)
        # Should not raise ZeroDivisionError
        score = score_memory(mem, config)
        assert 0.0 <= score <= 1.0

    def test_negative_half_life_clamped(self):
        """A negative half-life is clamped to 1, not crashing."""
        config = {"tuning": {"memory_half_lives": {"contextual": -5, "default": 30}}}
        mem = _make_memory("contextual", age_days=1)
        score = score_memory(mem, config)
        assert 0.0 <= score <= 1.0

    def test_uses_config_half_lives_over_defaults(self):
        """Config-provided half-lives override the module defaults."""
        # Override factual to 1 day instead of 60
        config = _config_with_half_lives({"factual": 1})
        mem = _make_memory("factual", age_days=1)
        score = score_memory(mem, config)
        # With 1-day half-life at 1 day old, should be ~0.5
        assert abs(score - 0.5) < 0.05

    def test_string_created_at_parsed(self):
        """created_at as ISO string is parsed correctly."""
        mem = {
            "category": "default",
            "created_at": "2026-03-23T00:00:00+00:00",
        }
        config = _config_with_half_lives()
        score = score_memory(mem, config)
        assert 0.0 <= score <= 1.0

    def test_datetime_created_at_accepted(self):
        """created_at as a datetime object is also accepted."""
        mem = {
            "category": "default",
            "created_at": datetime.now(timezone.utc),
        }
        config = _config_with_half_lives()
        score = score_memory(mem, config)
        assert score > 0.99

    def test_does_not_mutate_input(self):
        """score_memory must not modify the input memory dict."""
        mem = _make_memory("factual", age_days=10)
        original = copy.deepcopy(mem)
        config = _config_with_half_lives()
        score_memory(mem, config)
        assert mem == original


# ------------------------------------------------------------------
# filter_and_rank_memories
# ------------------------------------------------------------------


class TestFilterAndRankMemories:
    """Tests for the filter_and_rank_memories function."""

    def test_filters_below_threshold(self):
        """Memories scoring below min_score are excluded."""
        config = _config_with_half_lives()
        memories = [
            _make_memory("ephemeral", age_days=0),   # fresh, high score
            _make_memory("ephemeral", age_days=100),  # very old, low score (3d half-life)
        ]
        result = filter_and_rank_memories(memories, config, min_score=0.1)
        assert len(result) == 1
        assert result[0]["id"].startswith("mem-ephemeral-0")

    def test_ranked_by_score_descending(self):
        """Results are sorted by confidence_score descending."""
        config = _config_with_half_lives()
        memories = [
            _make_memory("factual", age_days=50),   # older
            _make_memory("factual", age_days=1),    # newer
            _make_memory("factual", age_days=10),   # middle
        ]
        result = filter_and_rank_memories(memories, config, min_score=0.0)
        scores = [m["confidence_score"] for m in result]
        assert scores == sorted(scores, reverse=True)

    def test_confidence_score_added(self):
        """Each returned memory has a confidence_score field."""
        config = _config_with_half_lives()
        memories = [_make_memory("default", age_days=5)]
        result = filter_and_rank_memories(memories, config)
        assert "confidence_score" in result[0]
        assert 0.0 <= result[0]["confidence_score"] <= 1.0

    def test_empty_input_returns_empty(self):
        """An empty input list returns an empty list."""
        config = _config_with_half_lives()
        result = filter_and_rank_memories([], config)
        assert result == []

    def test_does_not_mutate_input_list(self):
        """filter_and_rank_memories must not modify the input list or its dicts."""
        config = _config_with_half_lives()
        memories = [
            _make_memory("factual", age_days=5),
            _make_memory("contextual", age_days=10),
        ]
        original = copy.deepcopy(memories)
        filter_and_rank_memories(memories, config)
        assert memories == original

    def test_all_filtered_returns_empty(self):
        """If all memories fall below threshold, returns empty list."""
        config = _config_with_half_lives()
        memories = [_make_memory("ephemeral", age_days=300)]  # 3d half-life, 300 days old
        result = filter_and_rank_memories(memories, config, min_score=0.5)
        assert result == []
