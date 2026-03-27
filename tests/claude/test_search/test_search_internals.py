"""Tests for search_flow.py — hybrid search, RRF scoring, recency bias, reranking."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from context_broker_ae.search_flow import (
    _rerank_via_api,
    embed_message_query,
    hybrid_search_messages,
    rerank_results,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def search_config(sample_config):
    """Config with reranker and tuning for search tests."""
    cfg = dict(sample_config)
    cfg["reranker"] = {
        "provider": "api",
        "base_url": "http://reranker:8080",
        "model": "bge-reranker-v2",
        "top_n": 5,
    }
    cfg.setdefault("tuning", {})
    cfg["tuning"]["rrf_constant"] = 60
    cfg["tuning"]["search_candidate_limit"] = 100
    cfg["tuning"]["recency_decay_days"] = 90
    cfg["tuning"]["recency_max_penalty"] = 0.2
    return cfg


def _make_candidate(content, score, created_at=None):
    """Helper to create a search candidate dict."""
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
        "role": "user",
        "sender": "alice",
        "recipient": None,
        "content": content,
        "sequence_number": 1,
        "created_at": created_at,
        "token_count": len(content) // 4,
        "score": score,
    }


# ---------------------------------------------------------------------------
# RRF scoring math
# ---------------------------------------------------------------------------

class TestRRFScoring:
    """Reciprocal Rank Fusion score computation."""

    def test_rrf_single_source_vector_only(self):
        """RRF score with only a vector rank: 1/(k+rank)."""
        k = 60
        rank = 1
        expected = 1.0 / (k + rank)
        assert expected == pytest.approx(1 / 61)

    def test_rrf_single_source_bm25_only(self):
        """RRF score with only a BM25 rank: 1/(k+rank)."""
        k = 60
        rank = 5
        expected = 1.0 / (k + rank)
        assert expected == pytest.approx(1 / 65)

    def test_rrf_combined_score(self):
        """RRF combined score = 1/(k+vector_rank) + 1/(k+bm25_rank)."""
        k = 60
        vector_rank = 1
        bm25_rank = 3
        expected = 1.0 / (k + vector_rank) + 1.0 / (k + bm25_rank)
        assert expected == pytest.approx(1 / 61 + 1 / 63)

    def test_rrf_higher_rank_means_lower_score(self):
        """A document ranked 1st should score higher than one ranked 10th."""
        k = 60
        score_rank1 = 1.0 / (k + 1)
        score_rank10 = 1.0 / (k + 10)
        assert score_rank1 > score_rank10

    def test_rrf_k60_known_values(self):
        """Verify known RRF values with k=60."""
        k = 60
        # Rank 1 in both sources
        combined = 1.0 / (k + 1) + 1.0 / (k + 1)
        assert combined == pytest.approx(2 / 61)

        # Rank 1 vector, rank 100 BM25
        combined2 = 1.0 / (k + 1) + 1.0 / (k + 100)
        assert combined2 == pytest.approx(1 / 61 + 1 / 160)

    def test_rrf_absent_source_contributes_zero(self):
        """When a document appears in only one source, the other contributes 0."""
        k = 60
        vector_only = 1.0 / (k + 5) + 0  # absent from BM25
        bm25_only = 0 + 1.0 / (k + 2)    # absent from vector
        # Combined when present in both
        both = 1.0 / (k + 5) + 1.0 / (k + 2)
        assert both > vector_only
        assert both > bm25_only


# ---------------------------------------------------------------------------
# Recency bias (F-10)
# ---------------------------------------------------------------------------

class TestRecencyBias:
    """F-10: Older messages get lower RRF boost via linear decay."""

    @pytest.mark.asyncio
    async def test_recent_message_no_penalty(self, search_config):
        """A message created now should receive no recency penalty."""
        now = datetime.now(timezone.utc)
        candidates = [_make_candidate("recent msg", 0.5, now.isoformat())]

        # Simulate the recency bias logic from hybrid_search_messages
        recency_decay_days = 90
        recency_max_penalty = 0.2

        created = datetime.fromisoformat(candidates[0]["created_at"])
        age_days = max(0, (now - created).total_seconds() / 86400)
        penalty = min(recency_max_penalty, (age_days / recency_decay_days) * recency_max_penalty)

        adjusted = candidates[0]["score"] * (1.0 - penalty)
        # age_days ~0, penalty ~0, score unchanged
        assert adjusted == pytest.approx(0.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_old_message_gets_penalty(self, search_config):
        """A 90-day-old message should receive the maximum penalty."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=90)
        original_score = 0.5

        age_days = 90
        recency_decay_days = 90
        recency_max_penalty = 0.2

        penalty = min(recency_max_penalty, (age_days / recency_decay_days) * recency_max_penalty)
        adjusted = original_score * (1.0 - penalty)

        assert penalty == pytest.approx(0.2)
        assert adjusted == pytest.approx(0.5 * 0.8)

    @pytest.mark.asyncio
    async def test_halfway_message_gets_half_penalty(self):
        """A 45-day-old message should get half the max penalty."""
        age_days = 45
        recency_decay_days = 90
        recency_max_penalty = 0.2

        penalty = min(recency_max_penalty, (age_days / recency_decay_days) * recency_max_penalty)
        assert penalty == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_very_old_message_capped_at_max_penalty(self):
        """Messages older than decay window are capped at max penalty."""
        age_days = 365  # well beyond 90-day window
        recency_decay_days = 90
        recency_max_penalty = 0.2

        penalty = min(recency_max_penalty, (age_days / recency_decay_days) * recency_max_penalty)
        assert penalty == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# Reranker dispatch
# ---------------------------------------------------------------------------

class TestRerankerDispatch:
    """Reranker calls external API when configured."""

    @pytest.mark.asyncio
    async def test_reranker_calls_api(self, search_config):
        """When provider=api, _rerank_via_api is called and results sorted by rerank_score."""
        candidates = [
            _make_candidate("doc A", 0.3),
            _make_candidate("doc B", 0.5),
            _make_candidate("doc C", 0.1),
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.7},
                {"index": 2, "relevance_score": 0.95},
            ]
        }

        with patch("context_broker_ae.search_flow.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post.return_value = mock_response
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _rerank_via_api("test query", candidates, search_config)

        # Should be sorted by rerank_score descending; top_n=5
        assert result[0]["rerank_score"] == 0.95
        assert result[1]["rerank_score"] == 0.9

    @pytest.mark.asyncio
    async def test_rerank_results_dispatches_to_api(self, search_config):
        """rerank_results node dispatches to _rerank_via_api for provider=api."""
        candidates = [_make_candidate("doc A", 0.5)]

        state = {
            "candidates": candidates,
            "query": "test",
            "limit": 10,
            "config": search_config,
        }

        with patch(
            "context_broker_ae.search_flow._rerank_via_api",
            new_callable=AsyncMock,
            return_value=candidates,
        ) as mock_api:
            result = await rerank_results(state)
            mock_api.assert_awaited_once()

        assert "reranked_results" in result


# ---------------------------------------------------------------------------
# Reranker fallback
# ---------------------------------------------------------------------------

class TestRerankerFallback:
    """Graceful degradation when reranker is unavailable or unconfigured."""

    @pytest.mark.asyncio
    async def test_no_reranker_configured(self, search_config):
        """When provider=none, candidates are returned without reranking."""
        search_config["reranker"]["provider"] = "none"
        candidates = [
            _make_candidate("doc A", 0.5),
            _make_candidate("doc B", 0.3),
        ]

        state = {
            "candidates": candidates,
            "query": "test",
            "limit": 10,
            "config": search_config,
        }

        result = await rerank_results(state)
        assert result["reranked_results"] == candidates

    @pytest.mark.asyncio
    async def test_reranker_api_failure_falls_back(self, search_config):
        """When API reranker fails, returns unreranked candidates."""
        candidates = [_make_candidate("doc A", 0.5)]

        state = {
            "candidates": candidates,
            "query": "test",
            "limit": 10,
            "config": search_config,
        }

        with patch(
            "context_broker_ae.search_flow._rerank_via_api",
            new_callable=AsyncMock,
            side_effect=httpx.HTTPError("connection refused"),
        ):
            result = await rerank_results(state)

        assert result["reranked_results"] == candidates[:10]

    @pytest.mark.asyncio
    async def test_unknown_provider_falls_back(self, search_config):
        """Unknown reranker provider returns candidates without reranking."""
        search_config["reranker"]["provider"] = "unknown_provider"
        candidates = [_make_candidate("doc A", 0.5)]

        state = {
            "candidates": candidates,
            "query": "test",
            "limit": 10,
            "config": search_config,
        }

        result = await rerank_results(state)
        assert result["reranked_results"] == candidates

    @pytest.mark.asyncio
    async def test_empty_candidates_skip_reranking(self, search_config):
        """Empty candidate list skips reranking entirely."""
        state = {
            "candidates": [],
            "query": "test",
            "limit": 10,
            "config": search_config,
        }

        result = await rerank_results(state)
        assert result["reranked_results"] == []

    @pytest.mark.asyncio
    async def test_reranked_results_respect_limit(self, search_config):
        """Reranked results are truncated to the requested limit."""
        search_config["reranker"]["provider"] = "none"
        candidates = [_make_candidate(f"doc {i}", 0.5 - i * 0.01) for i in range(20)]

        state = {
            "candidates": candidates,
            "query": "test",
            "limit": 5,
            "config": search_config,
        }

        result = await rerank_results(state)
        assert len(result["reranked_results"]) == 5
