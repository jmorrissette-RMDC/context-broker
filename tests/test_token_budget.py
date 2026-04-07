"""
Unit tests for token budget resolution (app.token_budget).

Covers the priority chain: caller override > explicit integer > auto query > fallback.
All HTTP calls to LLM providers are mocked.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.token_budget import resolve_token_budget

# ------------------------------------------------------------------
# Priority chain
# ------------------------------------------------------------------


class TestResolveTokenBudget:
    """Tests for resolve_token_budget priority logic."""

    @pytest.mark.asyncio
    async def test_caller_override_takes_priority(self, sample_config):
        """Caller override is used even when build type has an explicit integer."""
        build_type_config = {
            "max_context_tokens": 32768,
            "fallback_tokens": 8192,
        }
        result = await resolve_token_budget(
            config=sample_config,
            build_type_config=build_type_config,
            caller_override=4096,
        )
        assert result == 4096

    @pytest.mark.asyncio
    async def test_caller_override_beats_auto(self, sample_config):
        """Caller override takes priority over auto resolution."""
        build_type_config = sample_config["build_types"]["tiered-summary"]
        result = await resolve_token_budget(
            config=sample_config,
            build_type_config=build_type_config,
            caller_override=2048,
        )
        assert result == 2048

    @pytest.mark.asyncio
    async def test_caller_override_zero_ignored(self, sample_config):
        """A caller_override of 0 is treated as not provided."""
        build_type_config = {
            "max_context_tokens": 16000,
            "fallback_tokens": 8192,
        }
        result = await resolve_token_budget(
            config=sample_config,
            build_type_config=build_type_config,
            caller_override=0,
        )
        assert result == 16000

    @pytest.mark.asyncio
    async def test_caller_override_negative_ignored(self, sample_config):
        """A negative caller_override is treated as not provided."""
        build_type_config = {
            "max_context_tokens": 16000,
            "fallback_tokens": 8192,
        }
        result = await resolve_token_budget(
            config=sample_config,
            build_type_config=build_type_config,
            caller_override=-1,
        )
        assert result == 16000

    @pytest.mark.asyncio
    async def test_explicit_integer_used(self, sample_config):
        """Explicit integer in build type config is used when no caller override."""
        build_type_config = {
            "max_context_tokens": 32768,
            "fallback_tokens": 8192,
        }
        result = await resolve_token_budget(
            config=sample_config,
            build_type_config=build_type_config,
            caller_override=None,
        )
        assert result == 32768

    @pytest.mark.asyncio
    async def test_auto_queries_provider(self, sample_config):
        """Auto mode queries the provider model list endpoint."""
        build_type_config = sample_config["build_types"]["tiered-summary"]
        assert build_type_config["max_context_tokens"] == "auto"

        with patch(
            "app.token_budget._query_provider_context_length",
            new_callable=AsyncMock,
        ) as mock_query:
            mock_query.return_value = 131072
            result = await resolve_token_budget(
                config=sample_config,
                build_type_config=build_type_config,
                caller_override=None,
            )

        mock_query.assert_awaited_once_with(sample_config, 8192)
        assert result == 131072

    @pytest.mark.asyncio
    async def test_auto_fallback_on_provider_failure(self, sample_config):
        """Auto mode falls back to fallback_tokens when provider query fails."""
        build_type_config = sample_config["build_types"]["tiered-summary"]

        with patch(
            "app.token_budget._query_provider_context_length",
            new_callable=AsyncMock,
        ) as mock_query:
            mock_query.return_value = (
                8192  # _query_provider returns fallback on failure
            )
            result = await resolve_token_budget(
                config=sample_config,
                build_type_config=build_type_config,
                caller_override=None,
            )

        assert result == 8192

    @pytest.mark.asyncio
    async def test_unrecognized_value_uses_fallback(self, sample_config):
        """An unrecognized max_context_tokens string falls back to fallback_tokens."""
        build_type_config = {
            "max_context_tokens": "banana",
            "fallback_tokens": 4096,
        }
        result = await resolve_token_budget(
            config=sample_config,
            build_type_config=build_type_config,
            caller_override=None,
        )
        assert result == 4096

    @pytest.mark.asyncio
    async def test_missing_max_context_tokens_defaults_to_auto(self, sample_config):
        """When max_context_tokens is absent, defaults to 'auto' behavior."""
        build_type_config = {
            "fallback_tokens": 6000,
        }
        with patch(
            "app.token_budget._query_provider_context_length",
            new_callable=AsyncMock,
        ) as mock_query:
            mock_query.return_value = 6000
            result = await resolve_token_budget(
                config=sample_config,
                build_type_config=build_type_config,
                caller_override=None,
            )

        assert result == 6000

    @pytest.mark.asyncio
    async def test_default_fallback_tokens(self, sample_config):
        """When fallback_tokens is not specified, defaults to 8192."""
        build_type_config = {
            "max_context_tokens": "weird_string",
        }
        result = await resolve_token_budget(
            config=sample_config,
            build_type_config=build_type_config,
            caller_override=None,
        )
        assert result == 8192


# ------------------------------------------------------------------
# _query_provider_context_length (via resolve_token_budget)
# ------------------------------------------------------------------


class TestQueryProviderIntegration:
    """Tests for the auto-query path through the real _query_provider_context_length."""

    @pytest.mark.asyncio
    async def test_provider_returns_context_length(self, sample_config):
        """Successful provider query returns the model's context_length."""
        build_type_config = {
            "max_context_tokens": "auto",
            "fallback_tokens": 8192,
        }

        mock_request = httpx.Request("GET", "http://localhost:11434/v1/models")
        mock_response = httpx.Response(
            200,
            json={
                "data": [
                    {"id": "qwen2.5:14b", "context_length": 131072},
                    {"id": "other-model", "context_length": 4096},
                ]
            },
            request=mock_request,
        )

        with patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await resolve_token_budget(
                config=sample_config,
                build_type_config=build_type_config,
                caller_override=None,
            )

        assert result == 131072

    @pytest.mark.asyncio
    async def test_provider_model_not_found(self, sample_config):
        """Falls back when the configured model is not in the provider's list."""
        build_type_config = {
            "max_context_tokens": "auto",
            "fallback_tokens": 8192,
        }

        mock_request = httpx.Request("GET", "http://localhost:11434/v1/models")
        mock_response = httpx.Response(
            200,
            json={"data": [{"id": "other-model", "context_length": 4096}]},
            request=mock_request,
        )

        with patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await resolve_token_budget(
                config=sample_config,
                build_type_config=build_type_config,
                caller_override=None,
            )

        assert result == 8192

    @pytest.mark.asyncio
    async def test_provider_http_error(self, sample_config):
        """Falls back on HTTP error from provider."""
        build_type_config = {
            "max_context_tokens": "auto",
            "fallback_tokens": 5000,
        }

        with patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("connection refused"),
        ):
            result = await resolve_token_budget(
                config=sample_config,
                build_type_config=build_type_config,
                caller_override=None,
            )

        assert result == 5000

    @pytest.mark.asyncio
    async def test_provider_not_configured(self):
        """Falls back when LLM provider is not configured."""
        config = {"llm": {}}
        build_type_config = {
            "max_context_tokens": "auto",
            "fallback_tokens": 3000,
        }
        result = await resolve_token_budget(
            config=config,
            build_type_config=build_type_config,
            caller_override=None,
        )
        assert result == 3000
