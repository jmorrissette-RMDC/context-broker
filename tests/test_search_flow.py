"""
Unit tests for the search StateGraph flows.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.flows.search_flow import (
    ConversationSearchState,
    MessageSearchState,
    embed_conversation_query,
    hybrid_search_messages,
    rerank_results,
)


@pytest.mark.asyncio
async def test_embed_conversation_query_no_query(sample_config):
    """embed_conversation_query skips embedding when no query is provided."""
    state: ConversationSearchState = {
        "query": None,
        "limit": 10,
        "offset": 0,
        "config": sample_config,
        "query_embedding": None,
        "results": [],
        "error": None,
    }

    result = await embed_conversation_query(state)
    assert result["query_embedding"] is None


@pytest.mark.asyncio
async def test_embed_conversation_query_failure_is_non_fatal(sample_config):
    """embed_conversation_query handles embedding failures gracefully."""
    state: ConversationSearchState = {
        "query": "What was discussed?",
        "limit": 10,
        "offset": 0,
        "config": sample_config,
        "query_embedding": None,
        "results": [],
        "error": None,
    }

    with patch("app.flows.search_flow.OpenAIEmbeddings") as mock_embeddings_class:
        mock_instance = AsyncMock()
        mock_instance.aembed_query = AsyncMock(side_effect=Exception("API error"))
        mock_embeddings_class.return_value = mock_instance

        result = await embed_conversation_query(state)

    # Failure is non-fatal — falls back to text search
    assert result["query_embedding"] is None


@pytest.mark.asyncio
async def test_rerank_results_with_provider_none(sample_config):
    """rerank_results returns top candidates when reranker is disabled."""
    candidates = [
        {"id": str(uuid.uuid4()), "content": f"Message {i}", "score": float(i)}
        for i in range(20)
    ]

    state: MessageSearchState = {
        "query": "test query",
        "conversation_id": None,
        "limit": 5,
        "config": {**sample_config, "reranker": {"provider": "none"}},
        "query_embedding": None,
        "candidates": candidates,
        "reranked_results": [],
        "error": None,
    }

    result = await rerank_results(state)
    assert len(result["reranked_results"]) == 5


@pytest.mark.asyncio
async def test_rerank_results_empty_candidates(sample_config):
    """rerank_results handles empty candidate list gracefully."""
    state: MessageSearchState = {
        "query": "test query",
        "conversation_id": None,
        "limit": 10,
        "config": sample_config,
        "query_embedding": None,
        "candidates": [],
        "reranked_results": [],
        "error": None,
    }

    result = await rerank_results(state)
    assert result["reranked_results"] == []
