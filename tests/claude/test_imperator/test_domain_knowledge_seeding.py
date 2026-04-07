"""Tests for seed_knowledge.py — domain knowledge seeding."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from context_broker_te.seed_knowledge import SEED_ARTICLES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool():
    """Return an AsyncMock that behaves like an asyncpg pool."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock()
    return pool


@pytest.fixture
def mock_embeddings_model():
    """Return a mock embeddings model that produces dummy vectors."""
    model = AsyncMock()
    model.aembed_documents = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    return model


@pytest.fixture
def mock_config():
    return {"embeddings": {"provider": "test", "model": "test-embed"}}


def _make_mock_ctx(mock_pool, mock_config=None, mock_embeddings_model=None):
    """Build a mock TEContext for seed_knowledge tests."""
    ctx = MagicMock()
    ctx.get_pool.return_value = mock_pool
    if mock_config is not None:
        ctx.async_load_config = AsyncMock(return_value=mock_config)
    if mock_embeddings_model is not None:
        ctx.get_embeddings_model.return_value = mock_embeddings_model
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_inserts_articles_when_table_is_empty(
    mock_pool, mock_embeddings_model, mock_config
):
    """seed_domain_knowledge() inserts articles when table is empty."""
    mock_pool.fetchval = AsyncMock(return_value=0)
    ctx = _make_mock_ctx(mock_pool, mock_config, mock_embeddings_model)

    with patch("context_broker_te._ctx.get_ctx", return_value=ctx):
        from context_broker_te.seed_knowledge import seed_domain_knowledge

        count = await seed_domain_knowledge()

    assert count == len(SEED_ARTICLES)
    assert mock_pool.execute.await_count == len(SEED_ARTICLES)


@pytest.mark.asyncio
async def test_seed_skips_when_table_has_data(mock_pool):
    """seed_domain_knowledge() skips when table already has data."""
    mock_pool.fetchval = AsyncMock(return_value=42)
    ctx = _make_mock_ctx(mock_pool)

    with patch("context_broker_te._ctx.get_ctx", return_value=ctx):
        from context_broker_te.seed_knowledge import seed_domain_knowledge

        count = await seed_domain_knowledge()

    assert count == 0
    mock_pool.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_embeds_articles_using_configured_model(
    mock_pool, mock_embeddings_model, mock_config
):
    """seed_domain_knowledge() embeds articles using configured embedding model."""
    mock_pool.fetchval = AsyncMock(return_value=0)
    ctx = _make_mock_ctx(mock_pool, mock_config, mock_embeddings_model)

    with patch("context_broker_te._ctx.get_ctx", return_value=ctx):
        from context_broker_te.seed_knowledge import seed_domain_knowledge

        await seed_domain_knowledge()

    # aembed_documents should have been called once per article
    assert mock_embeddings_model.aembed_documents.await_count == len(SEED_ARTICLES)


@pytest.mark.asyncio
async def test_seed_handles_embedding_failure_gracefully(mock_pool, mock_config):
    """seed_domain_knowledge() falls back to insert without embedding on failure."""
    mock_pool.fetchval = AsyncMock(return_value=0)

    failing_model = AsyncMock()
    failing_model.aembed_documents = AsyncMock(side_effect=RuntimeError("embed fail"))

    ctx = _make_mock_ctx(mock_pool, mock_config, failing_model)

    with patch("context_broker_te._ctx.get_ctx", return_value=ctx):
        from context_broker_te.seed_knowledge import seed_domain_knowledge

        count = await seed_domain_knowledge()

    # All articles should still be seeded via the fallback path
    assert count == len(SEED_ARTICLES)


@pytest.mark.asyncio
async def test_seed_returns_zero_when_table_not_ready(mock_pool):
    """seed_domain_knowledge() returns 0 when domain_information table does not exist."""
    import asyncpg
    mock_pool.fetchval = AsyncMock(side_effect=asyncpg.PostgresError("relation does not exist"))
    ctx = _make_mock_ctx(mock_pool)

    with patch("context_broker_te._ctx.get_ctx", return_value=ctx):
        from context_broker_te.seed_knowledge import seed_domain_knowledge

        count = await seed_domain_knowledge()

    assert count == 0


@pytest.mark.asyncio
async def test_seed_articles_all_have_source_seed():
    """All SEED_ARTICLES have source='seed'."""
    for article in SEED_ARTICLES:
        assert article["source"] == "seed"
        assert len(article["content"]) > 0
