"""
Unit tests for the memory extraction StateGraph flow.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.flows.memory_extraction import (
    MemoryExtractionState,
    acquire_extraction_lock,
    build_extraction_text,
    fetch_unextracted_messages,
    mark_messages_extracted,
    release_extraction_lock,
)


@pytest.mark.asyncio
async def test_acquire_extraction_lock_success(mock_redis):
    """acquire_extraction_lock acquires the lock when not held."""
    mock_redis.set = AsyncMock(return_value=True)

    state: MemoryExtractionState = {
        "conversation_id": str(uuid.uuid4()),
        "config": {},
        "messages": [],
        "user_id": "",
        "extraction_text": "",
        "selected_message_ids": [],
        "lock_key": "",
        "lock_acquired": False,
        "extracted_count": 0,
        "error": None,
    }

    with patch("app.flows.memory_extraction.get_redis", return_value=mock_redis):
        result = await acquire_extraction_lock(state)

    assert result["lock_acquired"] is True


@pytest.mark.asyncio
async def test_acquire_extraction_lock_already_held(mock_redis):
    """acquire_extraction_lock skips when lock is already held."""
    mock_redis.set = AsyncMock(return_value=False)

    state: MemoryExtractionState = {
        "conversation_id": str(uuid.uuid4()),
        "config": {},
        "messages": [],
        "user_id": "",
        "extraction_text": "",
        "selected_message_ids": [],
        "lock_key": "",
        "lock_acquired": False,
        "extracted_count": 0,
        "error": None,
    }

    with patch("app.flows.memory_extraction.get_redis", return_value=mock_redis):
        result = await acquire_extraction_lock(state)

    assert result["lock_acquired"] is False


@pytest.mark.asyncio
async def test_fetch_unextracted_messages_empty(mock_pg_pool):
    """fetch_unextracted_messages returns empty list when no messages need extraction."""
    mock_pg_pool.fetch = AsyncMock(return_value=[])

    state: MemoryExtractionState = {
        "conversation_id": str(uuid.uuid4()),
        "config": {},
        "messages": [],
        "user_id": "",
        "extraction_text": "",
        "selected_message_ids": [],
        "lock_key": "some-lock",
        "lock_acquired": True,
        "extracted_count": 0,
        "error": None,
    }

    with patch("app.flows.memory_extraction.get_pg_pool", return_value=mock_pg_pool):
        result = await fetch_unextracted_messages(state)

    assert result["messages"] == []
    assert result["extracted_count"] == 0


@pytest.mark.asyncio
async def test_build_extraction_text_respects_budget():
    """build_extraction_text limits text to the character budget."""
    # Create messages that together exceed the budget
    long_content = "x" * 50_000
    messages = [
        {
            "id": str(uuid.uuid4()),
            "role": "user",
            "sender_id": "user-1",
            "content": long_content,
            "sequence_number": i,
        }
        for i in range(1, 4)
    ]

    state: MemoryExtractionState = {
        "conversation_id": str(uuid.uuid4()),
        "config": {},
        "messages": messages,
        "user_id": "user-1",
        "extraction_text": "",
        "selected_message_ids": [],
        "lock_key": "some-lock",
        "lock_acquired": True,
        "extracted_count": 0,
        "error": None,
    }

    result = await build_extraction_text(state)

    # Should not exceed the max_chars budget
    assert len(result["extraction_text"]) <= 90_000 + 100  # small buffer for formatting
    assert len(result["selected_message_ids"]) >= 1


@pytest.mark.asyncio
async def test_mark_messages_extracted(mock_pg_pool):
    """mark_messages_extracted updates the database correctly."""
    message_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    state: MemoryExtractionState = {
        "conversation_id": str(uuid.uuid4()),
        "config": {},
        "messages": [],
        "user_id": "user-1",
        "extraction_text": "some text",
        "selected_message_ids": message_ids,
        "lock_key": "some-lock",
        "lock_acquired": True,
        "extracted_count": 0,
        "error": None,
    }

    with patch("app.flows.memory_extraction.get_pg_pool", return_value=mock_pg_pool):
        result = await mark_messages_extracted(state)

    assert result["extracted_count"] == 2
    mock_pg_pool.execute.assert_called_once()


@pytest.mark.asyncio
async def test_mark_messages_extracted_skips_on_error(mock_pg_pool):
    """mark_messages_extracted skips when there's an error in state."""
    state: MemoryExtractionState = {
        "conversation_id": str(uuid.uuid4()),
        "config": {},
        "messages": [],
        "user_id": "user-1",
        "extraction_text": "",
        "selected_message_ids": [str(uuid.uuid4())],
        "lock_key": "some-lock",
        "lock_acquired": True,
        "extracted_count": 0,
        "error": "Extraction failed",
    }

    with patch("app.flows.memory_extraction.get_pg_pool", return_value=mock_pg_pool):
        result = await mark_messages_extracted(state)

    # Should return state unchanged when error is present
    assert result["error"] == "Extraction failed"
    mock_pg_pool.execute.assert_not_called()
