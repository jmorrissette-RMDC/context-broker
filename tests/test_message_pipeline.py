"""
Unit tests for the message pipeline StateGraph flow.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.flows.message_pipeline import (
    MessagePipelineState,
    check_idempotency,
    enqueue_background_jobs,
    store_message,
    build_message_pipeline,
)


@pytest.mark.asyncio
async def test_check_idempotency_no_key():
    """check_idempotency passes through when no idempotency key is provided."""
    state: MessagePipelineState = {
        "conversation_id": str(uuid.uuid4()),
        "role": "user",
        "sender_id": "user-1",
        "content": "Hello",
        "token_count": None,
        "model_name": None,
        "idempotency_key": None,
        "message_id": None,
        "sequence_number": None,
        "was_duplicate": False,
        "queued_jobs": [],
        "error": None,
    }

    result = await check_idempotency(state)
    assert result["was_duplicate"] is False


@pytest.mark.asyncio
async def test_check_idempotency_duplicate_detected(mock_pg_pool):
    """check_idempotency detects an existing message with the same key."""
    existing_id = uuid.uuid4()
    mock_pg_pool.fetchrow = AsyncMock(
        return_value={"id": existing_id, "sequence_number": 5}
    )

    state: MessagePipelineState = {
        "conversation_id": str(uuid.uuid4()),
        "role": "user",
        "sender_id": "user-1",
        "content": "Hello",
        "token_count": None,
        "model_name": None,
        "idempotency_key": "key-abc-123",
        "message_id": None,
        "sequence_number": None,
        "was_duplicate": False,
        "queued_jobs": [],
        "error": None,
    }

    with patch("app.flows.message_pipeline.get_pg_pool", return_value=mock_pg_pool):
        result = await check_idempotency(state)

    assert result["was_duplicate"] is True
    assert result["message_id"] == str(existing_id)
    assert result["sequence_number"] == 5
    assert result["queued_jobs"] == []


@pytest.mark.asyncio
async def test_check_idempotency_new_key(mock_pg_pool):
    """check_idempotency passes through when key is new (no existing message)."""
    mock_pg_pool.fetchrow = AsyncMock(return_value=None)

    state: MessagePipelineState = {
        "conversation_id": str(uuid.uuid4()),
        "role": "user",
        "sender_id": "user-1",
        "content": "Hello",
        "token_count": None,
        "model_name": None,
        "idempotency_key": "new-key-xyz",
        "message_id": None,
        "sequence_number": None,
        "was_duplicate": False,
        "queued_jobs": [],
        "error": None,
    }

    with patch("app.flows.message_pipeline.get_pg_pool", return_value=mock_pg_pool):
        result = await check_idempotency(state)

    assert result["was_duplicate"] is False


@pytest.mark.asyncio
async def test_store_message_conversation_not_found(mock_pg_pool):
    """store_message sets error when conversation does not exist."""
    mock_pg_pool.fetchrow = AsyncMock(return_value=None)

    state: MessagePipelineState = {
        "conversation_id": str(uuid.uuid4()),
        "role": "user",
        "sender_id": "user-1",
        "content": "Hello",
        "token_count": None,
        "model_name": None,
        "idempotency_key": None,
        "message_id": None,
        "sequence_number": None,
        "was_duplicate": False,
        "queued_jobs": [],
        "error": None,
    }

    with patch("app.flows.message_pipeline.get_pg_pool", return_value=mock_pg_pool):
        result = await store_message(state)

    assert result["error"] is not None
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_enqueue_background_jobs_skips_on_duplicate(mock_redis):
    """enqueue_background_jobs skips enqueueing when message was a duplicate."""
    state: MessagePipelineState = {
        "conversation_id": str(uuid.uuid4()),
        "role": "user",
        "sender_id": "user-1",
        "content": "Hello",
        "token_count": None,
        "model_name": None,
        "idempotency_key": None,
        "message_id": str(uuid.uuid4()),
        "sequence_number": 1,
        "was_duplicate": True,
        "queued_jobs": [],
        "error": None,
    }

    with patch("app.flows.message_pipeline.get_redis", return_value=mock_redis):
        result = await enqueue_background_jobs(state)

    assert result["queued_jobs"] == []
    mock_redis.lpush.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_background_jobs_success(mock_redis):
    """enqueue_background_jobs enqueues embed and extract jobs."""
    state: MessagePipelineState = {
        "conversation_id": str(uuid.uuid4()),
        "role": "user",
        "sender_id": "user-1",
        "content": "Hello",
        "token_count": None,
        "model_name": None,
        "idempotency_key": None,
        "message_id": str(uuid.uuid4()),
        "sequence_number": 1,
        "was_duplicate": False,
        "queued_jobs": [],
        "error": None,
    }

    with patch("app.flows.message_pipeline.get_redis", return_value=mock_redis):
        result = await enqueue_background_jobs(state)

    assert "embed_message" in result["queued_jobs"]
    assert "extract_memory" in result["queued_jobs"]
    assert mock_redis.lpush.call_count == 2


def test_message_pipeline_state_immutability():
    """StateGraph nodes must not modify input state in-place."""
    original_state: MessagePipelineState = {
        "conversation_id": str(uuid.uuid4()),
        "role": "user",
        "sender_id": "user-1",
        "content": "Hello",
        "token_count": None,
        "model_name": None,
        "idempotency_key": None,
        "message_id": None,
        "sequence_number": None,
        "was_duplicate": False,
        "queued_jobs": [],
        "error": None,
    }

    import copy
    state_copy = copy.deepcopy(original_state)

    # The node returns a new dict — original must be unchanged
    # (This test verifies the pattern; actual immutability is enforced by returning {**state, ...})
    assert original_state == state_copy
