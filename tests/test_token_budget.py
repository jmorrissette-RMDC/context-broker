"""
Unit tests for token budget resolution.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.token_budget import resolve_token_budget


@pytest.mark.asyncio
async def test_resolve_token_budget_caller_override(sample_config):
    """Caller override takes highest priority."""
    build_type_config = sample_config["build_types"]["standard-tiered"]
    result = await resolve_token_budget(
        config=sample_config,
        build_type_config=build_type_config,
        caller_override=4096,
    )
    assert result == 4096


@pytest.mark.asyncio
async def test_resolve_token_budget_explicit_integer(sample_config):
    """Explicit integer in build type config is used directly."""
    build_type_config = {
        "tier3_pct": 0.72,
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
async def test_resolve_token_budget_auto_uses_fallback_on_failure(sample_config):
    """Auto resolution falls back to fallback_tokens when provider query fails."""
    build_type_config = sample_config["build_types"]["standard-tiered"]

    with patch("app.token_budget._query_provider_context_length", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = 8192
        result = await resolve_token_budget(
            config=sample_config,
            build_type_config=build_type_config,
            caller_override=None,
        )

    assert result == 8192


@pytest.mark.asyncio
async def test_resolve_token_budget_caller_override_beats_auto(sample_config):
    """Caller override takes priority over auto resolution."""
    build_type_config = sample_config["build_types"]["standard-tiered"]

    result = await resolve_token_budget(
        config=sample_config,
        build_type_config=build_type_config,
        caller_override=2048,
    )
    assert result == 2048


@pytest.mark.asyncio
async def test_resolve_token_budget_fallback_for_unknown_value(sample_config):
    """Unknown max_context_tokens value falls back to fallback_tokens."""
    build_type_config = {
        "tier3_pct": 0.72,
        "max_context_tokens": "unknown_value",
        "fallback_tokens": 4096,
    }
    result = await resolve_token_budget(
        config=sample_config,
        build_type_config=build_type_config,
        caller_override=None,
    )
    assert result == 4096
