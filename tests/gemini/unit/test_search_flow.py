import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
from context_broker_ae.search_flow import (
    _rerank_via_api, embed_conversation_query, search_conversations_db,
    embed_message_query, hybrid_search_messages, rerank_results
)

@pytest.mark.asyncio
async def test_rerank_via_api():
    config = {"reranker": {"base_url": "http://reranker", "model": "test-model", "top_n": 2}}
    candidates = [{"content": "doc1"}, {"content": "doc2"}, {"content": "doc3"}]
    
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.8}
        ]
    }
    
    with patch("httpx.AsyncClient") as MockClient:
        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_resp
        MockClient.return_value.__aenter__.return_value = mock_client_instance
        
        results = await _rerank_via_api("query", candidates, config)
        assert len(results) == 2
        assert results[0]["content"] == "doc3"
        assert results[0]["rerank_score"] == 0.9
        assert results[1]["content"] == "doc1"
        assert results[1]["rerank_score"] == 0.8

@pytest.mark.asyncio
async def test_embed_conversation_query_success():
    state = {"query": "test query", "config": {}}
    mock_model = MagicMock()
    mock_model.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    with patch("context_broker_ae.search_flow.get_embeddings_model", return_value=mock_model):
        result = await embed_conversation_query(state)
        assert result["query_embedding"] == [0.1, 0.2, 0.3]

@pytest.mark.asyncio
async def test_embed_conversation_query_fail():
    state = {"query": "test query", "config": {}}
    mock_model = MagicMock()
    mock_model.aembed_query = AsyncMock(side_effect=ValueError("fail"))
    with patch("context_broker_ae.search_flow.get_embeddings_model", return_value=mock_model):
        result = await embed_conversation_query(state)
        assert result["query_embedding"] is None
        assert "warning" in result

@pytest.mark.asyncio
async def test_search_conversations_db_no_embedding():
    state = {"query_embedding": None, "limit": 10, "offset": 0, "config": {}}
    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[{"id": 1, "title": "Conv 1"}])
    with patch("context_broker_ae.search_flow.get_pg_pool", return_value=mock_pool):
        result = await search_conversations_db(state)
        assert len(result["results"]) == 1
        assert result["results"][0]["id"] == "1"
        mock_pool.fetch.assert_called_once()

@pytest.mark.asyncio
async def test_search_conversations_db_with_filters():
    state = {"query_embedding": [0.1], "limit": 10, "offset": 0, "sender": "user1", "date_from": "2023-01-01T00:00:00Z", "config": {}}
    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    with patch("context_broker_ae.search_flow.get_pg_pool", return_value=mock_pool):
        await search_conversations_db(state)
        mock_pool.fetch.assert_called_once()
        args = mock_pool.fetch.call_args[0]
        # Should contain vector, limit, offset, sender, date
        assert "[0.1]" in args[1]
        assert "user1" in args

@pytest.mark.asyncio
async def test_hybrid_search_messages_success():
    state = {
        "query": "test", "query_embedding": [0.1], "limit": 10, "config": {},
        "conversation_id": "123e4567-e89b-12d3-a456-426614174000"
    }
    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[
        {"id": 1, "conversation_id": state["conversation_id"], "content": "hello", "score": 1.5, "created_at": datetime(2023, 1, 1, tzinfo=timezone.utc)}
    ])
    with patch("context_broker_ae.search_flow.get_pg_pool", return_value=mock_pool):
        result = await hybrid_search_messages(state)
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["id"] == "1"

@pytest.mark.asyncio
async def test_hybrid_search_messages_invalid_date():
    state = {"query": "test", "limit": 10, "date_from": "invalid-date", "config": {}}
    mock_pool = MagicMock()
    with patch("context_broker_ae.search_flow.get_pg_pool", return_value=mock_pool):
        result = await hybrid_search_messages(state)
        assert "error" in result
        assert "Invalid date format" in result["error"]

@pytest.mark.asyncio
async def test_rerank_results_api():
    state = {
        "query": "test", "limit": 2, "candidates": [{"content": "a"}, {"content": "b"}],
        "config": {"reranker": {"provider": "api"}}
    }
    with patch("context_broker_ae.search_flow._rerank_via_api", AsyncMock(return_value=[{"content": "b"}, {"content": "a"}])):
        result = await rerank_results(state)
        assert result["reranked_results"][0]["content"] == "b"

@pytest.mark.asyncio
async def test_rerank_results_none():
    state = {
        "query": "test", "limit": 1, "candidates": [{"content": "a"}, {"content": "b"}],
        "config": {"reranker": {"provider": "none"}}
    }
    result = await rerank_results(state)
    assert len(result["reranked_results"]) == 1
    assert result["reranked_results"][0]["content"] == "a"
