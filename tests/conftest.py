"""
Pytest configuration and shared fixtures for Context Broker tests.
"""

import asyncio
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_config() -> dict:
    """Return a minimal valid configuration for testing."""
    return {
        "log_level": "DEBUG",
        "llm": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "LLM_API_KEY",
        },
        "embeddings": {
            "base_url": "https://api.openai.com/v1",
            "model": "text-embedding-3-small",
            "api_key_env": "EMBEDDINGS_API_KEY",
            "context_window_size": 3,
        },
        "reranker": {
            "provider": "none",
        },
        "build_types": {
            "standard-tiered": {
                "tier1_pct": 0.08,
                "tier2_pct": 0.20,
                "tier3_pct": 0.72,
                "max_context_tokens": "auto",
                "fallback_tokens": 8192,
            },
            "knowledge-enriched": {
                "tier1_pct": 0.05,
                "tier2_pct": 0.15,
                "tier3_pct": 0.50,
                "knowledge_graph_pct": 0.15,
                "semantic_retrieval_pct": 0.15,
                "max_context_tokens": "auto",
                "fallback_tokens": 16000,
            },
        },
        "imperator": {
            "build_type": "standard-tiered",
            "max_context_tokens": "auto",
            "admin_tools": False,
        },
    }


@pytest.fixture
def mock_pg_pool():
    """Return a mock asyncpg pool."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=pool)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def mock_redis():
    """Return a mock Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.exists = AsyncMock(return_value=0)
    redis.delete = AsyncMock(return_value=1)
    redis.lpush = AsyncMock(return_value=1)
    redis.rpop = AsyncMock(return_value=None)
    redis.ping = AsyncMock(return_value=True)
    return redis
