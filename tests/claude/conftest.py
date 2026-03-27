"""Shared fixtures for the Claude test suite."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "live: integration tests against deployed test stack")


@pytest.fixture
def sample_config():
    """Minimal valid configuration for testing."""
    return {
        "database": {"pool_min_size": 1, "pool_max_size": 5},
        "embeddings": {
            "base_url": "http://localhost:11434/v1",
            "model": "nomic-embed-text",
            "embedding_dims": 768,
            "api_key_env": "",
        },
        "log_embeddings": {
            "base_url": "http://localhost:11434/v1",
            "model": "nomic-embed-text",
            "embedding_dims": 768,
        },
        "llm": {
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:7b",
            "api_key_env": "",
        },
        "imperator": {
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:7b",
            "api_key_env": "",
        },
        "summarization": {
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:7b",
        },
        "extraction": {
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:7b",
        },
        "build_types": {
            "passthrough": {"tier1_pct": 0, "tier2_pct": 0, "tier3_pct": 1.0},
            "standard-tiered": {
                "tier1_pct": 0.1,
                "tier2_pct": 0.3,
                "tier3_pct": 0.6,
            },
            "knowledge-enriched": {
                "tier1_pct": 0.05,
                "tier2_pct": 0.15,
                "tier3_pct": 0.40,
                "semantic_retrieval_pct": 0.25,
                "knowledge_graph_pct": 0.15,
            },
        },
        "tuning": {
            "verbose_logging": False,
            "worker_poll_interval_seconds": 2,
            "embedding_batch_size": 50,
            "extraction_timeout_seconds": 600,
            "assembly_timeout_seconds": 600,
            "embedding_timeout_seconds": 300,
            "trigger_threshold_percent": 0.1,
            "scheduler_poll_interval_seconds": 60,
        },
        "log_level": "INFO",
    }


@pytest.fixture
def mock_pg_pool():
    """Mock asyncpg connection pool."""
    pool = AsyncMock()
    conn = AsyncMock()

    # Make acquire() work as async context manager
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    # Transaction context manager
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)

    return pool, conn


@pytest.fixture
def sample_conversation_id():
    return uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def sample_context_window_id():
    return uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def sample_message_id():
    return uuid.UUID("33333333-3333-3333-3333-333333333333")
