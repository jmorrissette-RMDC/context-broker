"""Tests for the change_inference tool.

Unit tests for listing models and switching inference slots.
"""

import pytest
from unittest.mock import AsyncMock, patch, mock_open


MOCK_CATALOG = {
    "imperator": {
        "openai": [
            {
                "model": "gpt-4.1",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "context": 1048576,
                "notes": "Flagship.",
            }
        ],
        "ollama": [
            {
                "model": "qwen2.5:7b",
                "base_url": "http://context-broker-ollama:11434/v1",
                "api_key_env": "",
                "context": 32768,
                "notes": "Default local.",
            }
        ],
    },
    "summarization": {
        "openai": [
            {
                "model": "gpt-4.1-mini",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "context": 1048576,
                "notes": "Fast.",
            }
        ],
    },
    "embeddings": {
        "openai": [
            {
                "model": "text-embedding-3-small",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "embedding_dims": 1536,
                "notes": "Good quality.",
            }
        ],
    },
}


class TestChangeInferenceList:
    """Listing available models for a slot."""

    @pytest.mark.asyncio
    async def test_invalid_slot_rejected(self):
        from context_broker_te.tools.admin import change_inference

        result = await change_inference.ainvoke({"slot": "invalid"})
        assert "Invalid slot" in result

    @pytest.mark.asyncio
    async def test_list_shows_available_models(self):
        from context_broker_te.tools.admin import change_inference

        with (
            patch(
                "context_broker_te.tools.admin._load_inference_models",
                return_value=MOCK_CATALOG,
            ),
            patch(
                "app.config.async_load_config",
                new_callable=AsyncMock,
                return_value={"imperator": {"model": "qwen2.5:7b"}},
            ),
        ):
            result = await change_inference.ainvoke({"slot": "imperator"})

        assert "gpt-4.1" in result
        assert "qwen2.5:7b" in result
        assert "current" in result

    @pytest.mark.asyncio
    async def test_list_empty_catalog(self):
        from context_broker_te.tools.admin import change_inference

        with patch(
            "context_broker_te.tools.admin._load_inference_models",
            return_value={},
        ):
            result = await change_inference.ainvoke({"slot": "imperator"})

        assert "No models in catalog" in result


class TestChangeInferenceSwitch:
    """Switching inference models."""

    @pytest.mark.asyncio
    async def test_model_not_in_catalog(self):
        from context_broker_te.tools.admin import change_inference

        with patch(
            "context_broker_te.tools.admin._load_inference_models",
            return_value=MOCK_CATALOG,
        ):
            result = await change_inference.ainvoke(
                {"slot": "imperator", "provider": "openai", "model": "nonexistent"}
            )

        assert "not found in catalog" in result

    @pytest.mark.asyncio
    async def test_endpoint_test_failure_blocks_switch(self):
        from context_broker_te.tools.admin import change_inference

        with (
            patch(
                "context_broker_te.tools.admin._load_inference_models",
                return_value=MOCK_CATALOG,
            ),
            patch(
                "context_broker_te.tools.admin._test_endpoint",
                new_callable=AsyncMock,
                return_value="Connection refused",
            ),
        ):
            result = await change_inference.ainvoke(
                {"slot": "imperator", "provider": "openai", "model": "gpt-4.1"}
            )

        assert "Endpoint test failed" in result
        assert "not switched" in result

    @pytest.mark.asyncio
    async def test_embeddings_warns_about_migration(self):
        from context_broker_te.tools.admin import change_inference

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=5000)

        with (
            patch(
                "context_broker_te.tools.admin._load_inference_models",
                return_value=MOCK_CATALOG,
            ),
            patch(
                "context_broker_te.tools.admin._test_endpoint",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("context_broker_te.tools.admin.get_pg_pool", return_value=mock_pool),
            patch(
                "app.config.async_load_config",
                new_callable=AsyncMock,
                return_value={
                    "embeddings": {"model": "old-model", "embedding_dims": 768}
                },
            ),
        ):
            result = await change_inference.ainvoke(
                {
                    "slot": "embeddings",
                    "provider": "openai",
                    "model": "text-embedding-3-small",
                }
            )

        assert "EMBEDDING CHANGE" in result
        assert "5000" in result
        assert "migrate_embeddings" in result

    @pytest.mark.asyncio
    async def test_successful_switch_writes_config(self):
        from context_broker_te.tools.admin import change_inference

        fake_config = "summarization:\n  model: old-model\n  base_url: http://old\n"

        with (
            patch(
                "context_broker_te.tools.admin._load_inference_models",
                return_value=MOCK_CATALOG,
            ),
            patch(
                "context_broker_te.tools.admin._test_endpoint",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("builtins.open", mock_open(read_data=fake_config)),
            patch("yaml.dump") as mock_dump,
        ):
            result = await change_inference.ainvoke(
                {
                    "slot": "summarization",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                }
            )

        assert "Switched summarization to gpt-4.1-mini" in result
        assert "config.yml" in result
        mock_dump.assert_called_once()
