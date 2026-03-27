"""Tests for memory_search_flow.py — decay scoring, degraded mode, search results."""

import math
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from context_broker_ae.memory_scoring import (
    DEFAULT_HALF_LIVES,
    filter_and_rank_memories,
    score_memory,
)
from context_broker_ae.memory_search_flow import (
    retrieve_memory_context,
    search_memory_graph,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_config(sample_config):
    """Config for memory tests."""
    cfg = dict(sample_config)
    cfg.setdefault("tuning", {})
    cfg["tuning"]["memory_recency_window_days"] = 7
    cfg["tuning"]["memory_recency_boost_factor"] = 1.3
    return cfg


def _make_memory(content, category="factual", age_days=0, last_accessed_days=None):
    """Helper to create a memory dict."""
    now = datetime.now(timezone.utc)
    created = now - timedelta(days=age_days)
    mem = {
        "id": str(uuid.uuid4()),
        "memory": content,
        "category": category,
        "created_at": created.isoformat(),
    }
    if last_accessed_days is not None:
        mem["last_accessed"] = (now - timedelta(days=last_accessed_days)).isoformat()
    return mem


# ---------------------------------------------------------------------------
# M-22: Decay scoring applied at retrieval time
# ---------------------------------------------------------------------------

class TestDecayScoring:
    """M-22: Half-life decay scoring weights memories by freshness."""

    def test_new_memory_scores_near_one(self, memory_config):
        """A brand-new memory should score close to 1.0."""
        mem = _make_memory("fresh fact", category="factual", age_days=0)
        score = score_memory(mem, memory_config)
        assert score > 0.95

    def test_one_half_life_scores_half(self, memory_config):
        """A memory exactly one half-life old should score ~0.5."""
        half_life = DEFAULT_HALF_LIVES["factual"]  # 60 days
        mem = _make_memory("aging fact", category="factual", age_days=half_life)
        score = score_memory(mem, memory_config)
        assert score == pytest.approx(0.5, abs=0.05)

    def test_two_half_lives_scores_quarter(self, memory_config):
        """A memory two half-lives old should score ~0.25."""
        half_life = DEFAULT_HALF_LIVES["factual"]  # 60 days
        mem = _make_memory("old fact", category="factual", age_days=half_life * 2)
        score = score_memory(mem, memory_config)
        assert score == pytest.approx(0.25, abs=0.05)

    def test_ephemeral_decays_faster(self, memory_config):
        """Ephemeral memories (half-life=3d) decay much faster than factual (60d)."""
        age = 10
        eph = _make_memory("mood", category="ephemeral", age_days=age)
        fact = _make_memory("fact", category="factual", age_days=age)

        eph_score = score_memory(eph, memory_config)
        fact_score = score_memory(fact, memory_config)

        assert eph_score < fact_score

    def test_recently_accessed_gets_boost(self, memory_config):
        """A memory accessed within recency window gets a boost."""
        mem_boosted = _make_memory("boosted", category="factual", age_days=30, last_accessed_days=1)
        mem_not_boosted = _make_memory("not boosted", category="factual", age_days=30)

        score_b = score_memory(mem_boosted, memory_config)
        score_nb = score_memory(mem_not_boosted, memory_config)

        assert score_b > score_nb

    def test_filter_and_rank_removes_stale(self, memory_config):
        """Memories below min_score threshold are filtered out."""
        memories = [
            _make_memory("fresh", category="factual", age_days=0),
            _make_memory("ancient", category="ephemeral", age_days=365),
        ]

        result = filter_and_rank_memories(memories, memory_config, min_score=0.1)

        # Fresh factual should survive; ancient ephemeral (3d half-life, 365d old) should not
        assert len(result) == 1
        assert result[0]["memory"] == "fresh"

    def test_filter_and_rank_sorts_by_score(self, memory_config):
        """Results are sorted by confidence_score descending."""
        memories = [
            _make_memory("old", category="factual", age_days=90),
            _make_memory("new", category="factual", age_days=1),
            _make_memory("mid", category="factual", age_days=30),
        ]

        result = filter_and_rank_memories(memories, memory_config, min_score=0.0)

        scores = [m["confidence_score"] for m in result]
        assert scores == sorted(scores, reverse=True)
        assert result[0]["memory"] == "new"

    def test_unknown_age_gets_neutral_score(self, memory_config):
        """A memory with no created_at gets a neutral 0.5 score."""
        mem = {"id": "1", "memory": "no date", "category": "factual"}
        score = score_memory(mem, memory_config)
        assert score == 0.5


# ---------------------------------------------------------------------------
# Degraded mode when Neo4j/Mem0 unavailable
# ---------------------------------------------------------------------------

class TestDegradedMode:
    """Graceful degradation when Mem0/Neo4j is unavailable."""

    @pytest.mark.asyncio
    async def test_mem0_client_none_returns_degraded(self, memory_config):
        """When get_mem0_client returns None, degraded=True with empty results."""
        state = {
            "query": "test query",
            "user_id": "user1",
            "limit": 10,
            "config": memory_config,
        }

        with patch(
            "context_broker_ae.memory_search_flow.get_mem0_client",
            new_callable=AsyncMock,
            return_value=None,
            create=True,
        ) as mock_client:
            # Patch the import inside the function
            with patch.dict("sys.modules", {
                "context_broker_ae.memory.mem0_client": MagicMock(
                    get_mem0_client=AsyncMock(return_value=None)
                )
            }):
                result = await search_memory_graph(state)

        assert result["degraded"] is True
        assert result["memories"] == []
        assert result["relations"] == []

    @pytest.mark.asyncio
    async def test_mem0_connection_error_returns_degraded(self, memory_config):
        """When Mem0 raises ConnectionError, returns degraded=True."""
        state = {
            "query": "test query",
            "user_id": "user1",
            "limit": 10,
            "config": memory_config,
        }

        mock_module = MagicMock()
        mock_module.get_mem0_client = AsyncMock(side_effect=ConnectionError("neo4j down"))

        with patch.dict("sys.modules", {
            "context_broker_ae.memory.mem0_client": mock_module,
        }):
            result = await search_memory_graph(state)

        assert result["degraded"] is True
        assert result["memories"] == []
        assert "neo4j down" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_mem0_runtime_error_returns_degraded(self, memory_config):
        """When Mem0 raises RuntimeError, returns degraded=True, not an exception."""
        state = {
            "query": "test",
            "user_id": "user1",
            "limit": 10,
            "config": memory_config,
        }

        mock_module = MagicMock()
        mock_module.get_mem0_client = AsyncMock(side_effect=RuntimeError("mem0 broken"))

        with patch.dict("sys.modules", {
            "context_broker_ae.memory.mem0_client": mock_module,
        }):
            result = await search_memory_graph(state)

        assert result["degraded"] is True


# ---------------------------------------------------------------------------
# Search returns memories and relations
# ---------------------------------------------------------------------------

class TestSearchResults:
    """Memory search returns structured memories and relations."""

    @pytest.mark.asyncio
    async def test_search_returns_memories_and_relations(self, memory_config):
        """When Mem0 returns dict with results and relations, both are preserved."""
        mock_mem0 = MagicMock()
        mock_mem0.search.return_value = {
            "results": [
                _make_memory("fact A", category="factual", age_days=1),
                _make_memory("fact B", category="factual", age_days=2),
            ],
            "relations": [
                {"source": "A", "target": "B", "relation": "related_to"},
            ],
        }

        mock_module = MagicMock()
        mock_module.get_mem0_client = AsyncMock(return_value=mock_mem0)

        state = {
            "query": "test",
            "user_id": "user1",
            "limit": 10,
            "config": memory_config,
        }

        with patch.dict("sys.modules", {
            "context_broker_ae.memory.mem0_client": mock_module,
        }):
            result = await search_memory_graph(state)

        assert result["degraded"] is False
        assert len(result["memories"]) == 2
        assert len(result["relations"]) == 1
        # Memories should have confidence_score from filter_and_rank_memories
        assert all("confidence_score" in m for m in result["memories"])

    @pytest.mark.asyncio
    async def test_search_handles_list_response(self, memory_config):
        """When Mem0 returns a plain list (not dict), memories are extracted."""
        mock_mem0 = MagicMock()
        mock_mem0.search.return_value = [
            _make_memory("fact A", category="factual", age_days=1),
        ]

        mock_module = MagicMock()
        mock_module.get_mem0_client = AsyncMock(return_value=mock_mem0)

        state = {
            "query": "test",
            "user_id": "user1",
            "limit": 10,
            "config": memory_config,
        }

        with patch.dict("sys.modules", {
            "context_broker_ae.memory.mem0_client": mock_module,
        }):
            result = await search_memory_graph(state)

        assert result["degraded"] is False
        assert len(result["memories"]) == 1
        assert result["relations"] == []


# ---------------------------------------------------------------------------
# Memory context flow returns formatted context text
# ---------------------------------------------------------------------------

class TestMemoryContextFlow:
    """retrieve_memory_context formats memories into context text."""

    @pytest.mark.asyncio
    async def test_context_text_formatted(self, memory_config):
        """Context text includes header and bullet-pointed facts."""
        mock_mem0 = MagicMock()
        mock_mem0.search.return_value = {
            "results": [
                _make_memory("User prefers dark mode", category="factual", age_days=1),
                _make_memory("User is a developer", category="factual", age_days=2),
            ],
        }

        mock_module = MagicMock()
        mock_module.get_mem0_client = AsyncMock(return_value=mock_mem0)

        state = {
            "query": "user preferences",
            "user_id": "user1",
            "limit": 10,
            "config": memory_config,
        }

        with patch.dict("sys.modules", {
            "context_broker_ae.memory.mem0_client": mock_module,
        }):
            result = await retrieve_memory_context(state)

        assert result["degraded"] is False
        assert "Relevant knowledge about this context:" in result["context_text"]
        assert "- User prefers dark mode" in result["context_text"]
        assert "- User is a developer" in result["context_text"]

    @pytest.mark.asyncio
    async def test_empty_memories_returns_empty_context(self, memory_config):
        """When no memories found, context_text is empty string."""
        mock_mem0 = MagicMock()
        mock_mem0.search.return_value = {"results": []}

        mock_module = MagicMock()
        mock_module.get_mem0_client = AsyncMock(return_value=mock_mem0)

        state = {
            "query": "nonexistent topic",
            "user_id": "user1",
            "limit": 10,
            "config": memory_config,
        }

        with patch.dict("sys.modules", {
            "context_broker_ae.memory.mem0_client": mock_module,
        }):
            result = await retrieve_memory_context(state)

        assert result["context_text"] == ""
        assert result["memories"] == []

    @pytest.mark.asyncio
    async def test_context_degraded_returns_empty(self, memory_config):
        """When Mem0 is unavailable, context_text is empty and degraded=True."""
        mock_module = MagicMock()
        mock_module.get_mem0_client = AsyncMock(return_value=None)

        state = {
            "query": "test",
            "user_id": "user1",
            "limit": 10,
            "config": memory_config,
        }

        with patch.dict("sys.modules", {
            "context_broker_ae.memory.mem0_client": mock_module,
        }):
            result = await retrieve_memory_context(state)

        assert result["degraded"] is True
        assert result["context_text"] == ""
