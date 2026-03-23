"""
Pytest configuration and shared fixtures for Context Broker tests.

Provides mock infrastructure (asyncpg pool, Redis client), sample config,
and sample data factories used across all test modules.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ------------------------------------------------------------------
# Custom markers for e2e / integration tests
# ------------------------------------------------------------------


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "e2e: end-to-end tests against deployed system")
    config.addinivalue_line(
        "markers", "integration: integration tests requiring SSH or direct DB access"
    )

# ------------------------------------------------------------------
# Sample configuration
# ------------------------------------------------------------------


@pytest.fixture
def sample_config() -> dict:
    """Return a minimal valid configuration for testing."""
    return {
        "log_level": "DEBUG",
        "llm": {
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:14b",
            "api_key_env": "LLM_API_KEY",
        },
        "embeddings": {
            "base_url": "http://localhost:11434/v1",
            "model": "nomic-embed-text",
            "api_key_env": "EMBEDDINGS_API_KEY",
            "context_window_size": 3,
        },
        "reranker": {
            "provider": "none",
        },
        "build_types": {
            "passthrough": {
                "tier1_pct": 0.0,
                "tier2_pct": 0.0,
                "tier3_pct": 1.0,
                "max_context_tokens": 8192,
                "fallback_tokens": 4096,
            },
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
        "tuning": {
            "verbose_logging": False,
            "memory_half_lives": {
                "ephemeral": 3,
                "contextual": 14,
                "factual": 60,
                "historical": 365,
                "default": 30,
            },
        },
    }


# ------------------------------------------------------------------
# Mock infrastructure
# ------------------------------------------------------------------


@pytest.fixture
def mock_pg_pool():
    """Return a mock asyncpg connection pool.

    The pool supports acquire() as an async context manager that
    yields itself, plus the standard fetchrow/fetch/fetchval/execute
    coroutine methods.
    """
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)

    # Make pool.acquire() usable as `async with pool.acquire() as conn:`
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value=None)
    conn.transaction = MagicMock()
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=ctx)
    pool._conn = conn  # expose for test assertions
    return pool


@pytest.fixture
def mock_redis():
    """Return a mock async Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.exists = AsyncMock(return_value=0)
    redis.delete = AsyncMock(return_value=1)
    redis.lpush = AsyncMock(return_value=1)
    redis.rpop = AsyncMock(return_value=None)
    redis.zadd = AsyncMock(return_value=1)
    redis.ping = AsyncMock(return_value=True)
    return redis


# ------------------------------------------------------------------
# Sample data factories
# ------------------------------------------------------------------


@pytest.fixture
def sample_conversation_id() -> uuid.UUID:
    """Return a deterministic conversation UUID."""
    return uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


@pytest.fixture
def sample_context_window_id() -> uuid.UUID:
    """Return a deterministic context window UUID."""
    return uuid.UUID("11111111-2222-3333-4444-555555555555")


@pytest.fixture
def sample_message_id() -> uuid.UUID:
    """Return a deterministic message UUID."""
    return uuid.UUID("ffffffff-eeee-dddd-cccc-bbbbbbbbbbbb")


@pytest.fixture
def sample_store_message_data(sample_context_window_id) -> dict:
    """Return a valid StoreMessageInput dict (pre-validation)."""
    return {
        "context_window_id": str(sample_context_window_id),
        "role": "user",
        "sender": "user-1",
        "content": "Hello, this is a test message.",
    }


@pytest.fixture
def sample_message_pipeline_state(
    sample_context_window_id, sample_conversation_id
) -> dict:
    """Return a valid MessagePipelineState dict for node-level tests."""
    return {
        "context_window_id": str(sample_context_window_id),
        "conversation_id_input": None,
        "role": "user",
        "sender": "user-1",
        "recipient": None,
        "content": "Hello, this is a test message.",
        "model_name": None,
        "tool_calls": None,
        "tool_call_id": None,
        "message_id": None,
        "conversation_id": None,
        "sequence_number": None,
        "was_collapsed": False,
        "queued_jobs": [],
        "error": None,
    }


@pytest.fixture
def sample_memory() -> dict:
    """Return a sample memory dict as returned by Mem0."""
    return {
        "id": "mem-001",
        "content": "User prefers dark mode.",
        "category": "contextual",
        "created_at": datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
        "last_accessed": datetime(
            2026, 3, 20, 8, 0, 0, tzinfo=timezone.utc
        ).isoformat(),
        "user_id": "user-1",
    }


@pytest.fixture
def sample_memories() -> list[dict]:
    """Return a list of sample memories with different categories and ages."""
    return [
        {
            "id": "mem-recent",
            "content": "User is working on ContextBroker tests.",
            "category": "ephemeral",
            "created_at": datetime(
                2026, 3, 22, 12, 0, 0, tzinfo=timezone.utc
            ).isoformat(),
            "last_accessed": None,
            "user_id": "user-1",
        },
        {
            "id": "mem-old",
            "content": "User likes Python.",
            "category": "factual",
            "created_at": datetime(
                2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc
            ).isoformat(),
            "last_accessed": None,
            "user_id": "user-1",
        },
        {
            "id": "mem-medium",
            "content": "Project context for Rogers refactor.",
            "category": "contextual",
            "created_at": datetime(
                2026, 3, 10, 0, 0, 0, tzinfo=timezone.utc
            ).isoformat(),
            "last_accessed": None,
            "user_id": "user-1",
        },
    ]
