"""Tests for seed_knowledge.py — domain knowledge seeding."""

from unittest.mock import AsyncMock, patch

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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_inserts_articles_when_table_is_empty(
    mock_pool, mock_embeddings_model, mock_config
):
    """seed_domain_knowledge() inserts articles when table is empty."""
    mock_pool.fetchval = AsyncMock(return_value=0)

    with (
        patch("app.database.get_pg_pool", return_value=mock_pool),
        patch("app.config.async_load_config", AsyncMock(return_value=mock_config)),
        patch(
            "app.config.get_embeddings_model", return_value=mock_embeddings_model
        ),
    ):
        from context_broker_te.seed_knowledge import seed_domain_knowledge

        count = await seed_domain_knowledge()

    assert count == len(SEED_ARTICLES)
    assert mock_pool.execute.await_count == len(SEED_ARTICLES)


@pytest.mark.asyncio
async def test_seed_skips_when_table_has_data(mock_pool):
    """seed_domain_knowledge() skips when table already has data."""
    mock_pool.fetchval = AsyncMock(return_value=42)

    with patch("app.database.get_pg_pool", return_value=mock_pool):
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

    with (
        patch("app.database.get_pg_pool", return_value=mock_pool),
        patch("app.config.async_load_config", AsyncMock(return_value=mock_config)),
        patch(
            "app.config.get_embeddings_model",
            return_value=mock_embeddings_model,
        ),
    ):
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

    # The first pool.execute (with embedding) will raise because
    # aembed_documents fails, triggering the except block which tries
    # a fallback insert without embedding.
    with (
        patch("app.database.get_pg_pool", return_value=mock_pool),
        patch("app.config.async_load_config", AsyncMock(return_value=mock_config)),
        patch("app.config.get_embeddings_model", return_value=failing_model),
    ):
        from context_broker_te.seed_knowledge import seed_domain_knowledge

        count = await seed_domain_knowledge()

    # All articles should still be seeded via the fallback path
    assert count == len(SEED_ARTICLES)


@pytest.mark.asyncio
async def test_seed_returns_zero_when_table_not_ready(mock_pool):
    """seed_domain_knowledge() returns 0 when domain_information table does not exist."""
    mock_pool.fetchval = AsyncMock(side_effect=Exception("relation does not exist"))

    with patch("app.database.get_pg_pool", return_value=mock_pool):
        from context_broker_te.seed_knowledge import seed_domain_knowledge

        count = await seed_domain_knowledge()

    assert count == 0


@pytest.mark.asyncio
async def test_seed_articles_all_have_source_seed():
    """All SEED_ARTICLES have source='seed'."""
    for article in SEED_ARTICLES:
        assert article["source"] == "seed"
        assert len(article["content"]) > 0
