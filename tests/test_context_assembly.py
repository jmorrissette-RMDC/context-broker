"""
Unit tests for the context assembly StateGraph flow.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.flows.context_assembly import (
    ContextAssemblyState,
    acquire_assembly_lock,
    calculate_tier_boundaries,
    finalize_assembly,
    release_assembly_lock,
)


@pytest.mark.asyncio
async def test_acquire_assembly_lock_success(mock_redis):
    """acquire_assembly_lock acquires the lock when not already held."""
    mock_redis.set = AsyncMock(return_value=True)

    state: ContextAssemblyState = {
        "context_window_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
        "build_type": "standard-tiered",
        "config": {},
        "window": None,
        "build_type_config": None,
        "max_token_budget": 8192,
        "all_messages": [],
        "tier3_messages": [],
        "older_messages": [],
        "chunks": [],
        "tier2_summaries": [],
        "tier1_summary": None,
        "lock_key": "",
        "lock_acquired": False,
        "error": None,
    }

    with patch("app.flows.context_assembly.get_redis", return_value=mock_redis):
        result = await acquire_assembly_lock(state)

    assert result["lock_acquired"] is True
    assert result["lock_key"] == f"assembly_in_progress:{state['context_window_id']}"


@pytest.mark.asyncio
async def test_acquire_assembly_lock_already_held(mock_redis):
    """acquire_assembly_lock skips gracefully when lock is already held."""
    mock_redis.set = AsyncMock(return_value=False)  # nx=True returns False if key exists

    state: ContextAssemblyState = {
        "context_window_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
        "build_type": "standard-tiered",
        "config": {},
        "window": None,
        "build_type_config": None,
        "max_token_budget": 8192,
        "all_messages": [],
        "tier3_messages": [],
        "older_messages": [],
        "chunks": [],
        "tier2_summaries": [],
        "tier1_summary": None,
        "lock_key": "",
        "lock_acquired": False,
        "error": None,
    }

    with patch("app.flows.context_assembly.get_redis", return_value=mock_redis):
        result = await acquire_assembly_lock(state)

    assert result["lock_acquired"] is False


@pytest.mark.asyncio
async def test_calculate_tier_boundaries_all_fit_in_tier3():
    """calculate_tier_boundaries puts all messages in tier 3 when they fit."""
    messages = [
        {"sequence_number": i, "content": f"Message {i}", "token_count": 10}
        for i in range(1, 6)
    ]

    state: ContextAssemblyState = {
        "context_window_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
        "build_type": "standard-tiered",
        "config": {},
        "window": None,
        "build_type_config": {"tier3_pct": 0.72},
        "max_token_budget": 10000,  # Large budget — all messages fit
        "all_messages": messages,
        "tier3_messages": [],
        "older_messages": [],
        "chunks": [],
        "tier2_summaries": [],
        "tier1_summary": None,
        "lock_key": "",
        "lock_acquired": True,
        "error": None,
    }

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])

    with patch("app.flows.context_assembly.get_pg_pool", return_value=mock_pool):
        result = await calculate_tier_boundaries(state)

    assert len(result["tier3_messages"]) == 5
    assert result["chunks"] == []  # No chunks needed


@pytest.mark.asyncio
async def test_release_assembly_lock_cleans_up(mock_redis):
    """release_assembly_lock deletes the Redis lock key."""
    lock_key = f"assembly_in_progress:{uuid.uuid4()}"

    state: ContextAssemblyState = {
        "context_window_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
        "build_type": "standard-tiered",
        "config": {},
        "window": None,
        "build_type_config": None,
        "max_token_budget": 8192,
        "all_messages": [],
        "tier3_messages": [],
        "older_messages": [],
        "chunks": [],
        "tier2_summaries": [],
        "tier1_summary": None,
        "lock_key": lock_key,
        "lock_acquired": True,
        "error": None,
    }

    with patch("app.flows.context_assembly.get_redis", return_value=mock_redis):
        await release_assembly_lock(state)

    mock_redis.delete.assert_called_once_with(lock_key)


@pytest.mark.asyncio
async def test_release_assembly_lock_skips_when_not_acquired(mock_redis):
    """release_assembly_lock does nothing when lock was not acquired."""
    state: ContextAssemblyState = {
        "context_window_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
        "build_type": "standard-tiered",
        "config": {},
        "window": None,
        "build_type_config": None,
        "max_token_budget": 8192,
        "all_messages": [],
        "tier3_messages": [],
        "older_messages": [],
        "chunks": [],
        "tier2_summaries": [],
        "tier1_summary": None,
        "lock_key": "some-key",
        "lock_acquired": False,  # Not acquired
        "error": None,
    }

    with patch("app.flows.context_assembly.get_redis", return_value=mock_redis):
        await release_assembly_lock(state)

    mock_redis.delete.assert_not_called()
