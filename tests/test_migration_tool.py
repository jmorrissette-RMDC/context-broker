"""Tests for the embedding migration tool (§4.21).

Unit tests for dry run mode and input validation.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestMigrationToolDryRun:
    """T-21.1: Migration tool dry run."""

    @pytest.mark.asyncio
    async def test_dry_run_shows_preview(self):
        from context_broker_te.tools.admin import migrate_embeddings

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=100)

        mock_ctx = MagicMock()  # get_ctx() is sync — use MagicMock not AsyncMock
        mock_ctx.get_pool.return_value = mock_pool
        mock_ctx.async_load_config = AsyncMock(return_value={
            "embeddings": {"model": "old-model", "embedding_dims": 768}
        })

        with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx):
            result = await migrate_embeddings.ainvoke(
                {"new_model": "new-model", "new_dims": 1536, "confirm": False}
            )

        assert "DRY RUN" in result
        assert "old-model" in result
        assert "new-model" in result
        assert "1536" in result
        assert "confirm=true" in result

    @pytest.mark.asyncio
    async def test_dry_run_does_not_modify(self):
        from context_broker_te.tools.admin import migrate_embeddings

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.execute = AsyncMock()

        mock_ctx = MagicMock()  # get_ctx() is sync
        mock_ctx.get_pool.return_value = mock_pool
        mock_ctx.async_load_config = AsyncMock(return_value={
            "embeddings": {"model": "m", "embedding_dims": 768}
        })

        with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx):
            await migrate_embeddings.ainvoke(
                {"new_model": "new", "new_dims": 1536, "confirm": False}
            )

        # execute should not be called in dry run
        mock_pool.execute.assert_not_called()
