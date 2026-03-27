"""Tests for packages/context-broker-te/src/context_broker_te/tools/operational.py.

Covers: store_domain_info, search_domain_info, extract_domain_knowledge,
search_domain_knowledge.

All functions use local imports (from app.config import ...) so we patch
at the source module (app.config) level.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from context_broker_te.tools.operational import (
    extract_domain_knowledge,
    search_domain_info,
    search_domain_knowledge,
    store_domain_info,
)


@pytest.fixture
def mock_config():
    return {
        "embeddings": {
            "model": "nomic-embed-text",
            "embedding_dims": 768,
            "base_url": "http://localhost:11434/v1",
        }
    }


@pytest.fixture
def mock_pool():
    pool = AsyncMock()
    return pool


@pytest.fixture
def mock_emb_model():
    model = AsyncMock()
    model.aembed_query.return_value = [0.1, 0.2, 0.3]
    return model


# ── store_domain_info ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_domain_info_success(mock_pool, mock_config, mock_emb_model):
    """Embeds content and inserts into domain_information table."""
    mock_pool.execute = AsyncMock()

    with patch("app.config.async_load_config", return_value=mock_config), \
         patch("context_broker_te.tools.operational.get_pg_pool", return_value=mock_pool), \
         patch("app.config.get_embeddings_model", return_value=mock_emb_model):
        result = await store_domain_info.ainvoke({"content": "Deployment uses blue-green strategy"})

    assert "Stored domain information" in result
    mock_emb_model.aembed_query.assert_called_once()
    mock_pool.execute.assert_called_once()
    sql = mock_pool.execute.call_args[0][0]
    assert "INSERT INTO domain_information" in sql


@pytest.mark.asyncio
async def test_store_domain_info_returns_error_on_failure(mock_pool, mock_config, mock_emb_model):
    """Returns error string on DB failure."""
    mock_pool.execute = AsyncMock(side_effect=asyncpg.PostgresError("insert failed"))

    with patch("app.config.async_load_config", return_value=mock_config), \
         patch("context_broker_te.tools.operational.get_pg_pool", return_value=mock_pool), \
         patch("app.config.get_embeddings_model", return_value=mock_emb_model):
        result = await store_domain_info.ainvoke({"content": "test content"})

    assert "Failed to store domain information" in result


@pytest.mark.asyncio
async def test_store_domain_info_custom_source(mock_pool, mock_config, mock_emb_model):
    """Uses custom source parameter."""
    mock_pool.execute = AsyncMock()

    with patch("app.config.async_load_config", return_value=mock_config), \
         patch("context_broker_te.tools.operational.get_pg_pool", return_value=mock_pool), \
         patch("app.config.get_embeddings_model", return_value=mock_emb_model):
        result = await store_domain_info.ainvoke({"content": "test", "source": "manual"})

    call_args = mock_pool.execute.call_args[0]
    assert call_args[3] == "manual"


# ── search_domain_info ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_domain_info_returns_results(mock_pool, mock_config, mock_emb_model):
    """Vector search with similarity scores."""
    now = datetime(2025, 1, 15, tzinfo=timezone.utc)
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda s, k: {
        "content": "Blue-green deployments",
        "source": "imperator",
        "created_at": now,
        "similarity": 0.95,
    }[k]
    mock_pool.fetch = AsyncMock(return_value=[mock_row])

    with patch("app.config.async_load_config", return_value=mock_config), \
         patch("context_broker_te.tools.operational.get_pg_pool", return_value=mock_pool), \
         patch("app.config.get_embeddings_model", return_value=mock_emb_model):
        result = await search_domain_info.ainvoke({"query": "deployment strategy"})

    assert "Found 1 relevant" in result
    assert "0.95" in result


@pytest.mark.asyncio
async def test_search_domain_info_no_results(mock_pool, mock_config, mock_emb_model):
    """Returns 'no results' message when empty."""
    mock_pool.fetch = AsyncMock(return_value=[])

    with patch("app.config.async_load_config", return_value=mock_config), \
         patch("context_broker_te.tools.operational.get_pg_pool", return_value=mock_pool), \
         patch("app.config.get_embeddings_model", return_value=mock_emb_model):
        result = await search_domain_info.ainvoke({"query": "nonexistent"})

    assert "No domain information found" in result


# ── extract_domain_knowledge ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_domain_knowledge_processes_pending(mock_pool, mock_config):
    """Processes pending entries through Mem0."""
    mock_mem0 = MagicMock()
    mock_mem0.add.return_value = {"results": []}

    entry_id = uuid.uuid4()
    row = MagicMock()
    row.__getitem__ = lambda s, k: {"id": entry_id, "content": "fact"}[k]
    mock_pool.fetchval = AsyncMock(return_value=True)  # table exists
    mock_pool.fetch = AsyncMock(return_value=[row])

    with patch("app.config.async_load_config", return_value=mock_config), \
         patch("context_broker_te.tools.operational.get_pg_pool", return_value=mock_pool), \
         patch("context_broker_te.domain_mem0.get_domain_mem0", return_value=mock_mem0):
        result = await extract_domain_knowledge.ainvoke({"content": ""})

    assert "Extracted knowledge from 1/1" in result


@pytest.mark.asyncio
async def test_extract_domain_knowledge_specific_content(mock_pool, mock_config):
    """Handles specific content extraction."""
    mock_mem0 = MagicMock()
    mock_mem0.add.return_value = {"results": ["extracted"]}

    with patch("app.config.async_load_config", return_value=mock_config), \
         patch("context_broker_te.tools.operational.get_pg_pool", return_value=mock_pool), \
         patch("context_broker_te.domain_mem0.get_domain_mem0", return_value=mock_mem0):
        result = await extract_domain_knowledge.ainvoke({"content": "specific fact to extract"})

    assert "Extracted knowledge from provided content" in result


@pytest.mark.asyncio
async def test_extract_domain_knowledge_mem0_unavailable(mock_config):
    """Returns error when Mem0 is unavailable."""
    with patch("app.config.async_load_config", return_value=mock_config), \
         patch("context_broker_te.domain_mem0.get_domain_mem0", return_value=None):
        result = await extract_domain_knowledge.ainvoke({"content": ""})

    assert "not available" in result


@pytest.mark.asyncio
async def test_extract_domain_knowledge_no_pending(mock_pool, mock_config):
    """Returns message when no pending entries exist."""
    mock_mem0 = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)  # table exists
    mock_pool.fetch = AsyncMock(return_value=[])  # no pending

    with patch("app.config.async_load_config", return_value=mock_config), \
         patch("context_broker_te.tools.operational.get_pg_pool", return_value=mock_pool), \
         patch("context_broker_te.domain_mem0.get_domain_mem0", return_value=mock_mem0):
        result = await extract_domain_knowledge.ainvoke({"content": ""})

    assert "No pending" in result


# ── search_domain_knowledge ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_domain_knowledge_returns_results(mock_config):
    """Queries domain Mem0 and returns formatted results."""
    mock_mem0 = MagicMock()
    mock_mem0.search.return_value = {
        "results": [
            {"memory": "System uses PostgreSQL 15"},
            {"memory": "Redis is cache layer"},
        ]
    }

    with patch("app.config.async_load_config", return_value=mock_config), \
         patch("context_broker_te.domain_mem0.get_domain_mem0", return_value=mock_mem0):
        result = await search_domain_knowledge.ainvoke({"query": "database"})

    assert "Found 2 domain knowledge" in result
    assert "PostgreSQL" in result
    assert "Redis" in result


@pytest.mark.asyncio
async def test_search_domain_knowledge_no_results(mock_config):
    """Returns 'no results' message when empty."""
    mock_mem0 = MagicMock()
    mock_mem0.search.return_value = {"results": []}

    with patch("app.config.async_load_config", return_value=mock_config), \
         patch("context_broker_te.domain_mem0.get_domain_mem0", return_value=mock_mem0):
        result = await search_domain_knowledge.ainvoke({"query": "nonexistent"})

    assert "No domain knowledge found" in result


@pytest.mark.asyncio
async def test_search_domain_knowledge_mem0_unavailable(mock_config):
    """Returns unavailable message when Mem0 is None."""
    with patch("app.config.async_load_config", return_value=mock_config), \
         patch("context_broker_te.domain_mem0.get_domain_mem0", return_value=None):
        result = await search_domain_knowledge.ainvoke({"query": "test"})

    assert "not available" in result
