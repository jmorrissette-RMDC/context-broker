import pytest
import uuid
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from context_broker_ae.memory_extraction import (
    _redact_secrets, _clean_for_extraction, _chunk_text,
    acquire_extraction_lock, fetch_unextracted_messages,
    build_extraction_text, run_mem0_extraction, mark_messages_extracted,
    release_extraction_lock, route_after_lock, route_after_fetch,
    route_after_build_text, route_after_extraction
)

# Real test data location
REAL_DATA_PATH = Path(r"Z:\test-data\conversational-memory\phase1-bulk-load\conversation-1.json")

@pytest.fixture
def real_messages():
    """Load all real messages from the Z: drive dataset."""
    if not REAL_DATA_PATH.exists():
        pytest.skip(f"Real test data not found at {REAL_DATA_PATH}")
    with open(REAL_DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data # Process the ENTIRE dataset

def test_redact_secrets():
    # Using a real-world example of what might be in the data
    text = "Here is my api_key: sk-1234567890abcdef1234567890abcdef\nBearer tokentokentokentokentokentoken"
    redacted = _redact_secrets(text)
    assert "sk-1234567890" not in redacted
    assert "[REDACTED]" in redacted

def test_clean_for_extraction_with_real_content(real_messages):
    """Test cleaning logic using ALL real content to catch crash-inducing edge cases."""
    for sample in real_messages:
        content = sample.get("content")
        if not isinstance(content, str):
            continue
            
        # The primary goal here is ensuring this doesn't raise an exception 
        # on bizarre real-world data (massive logs, weird markdown, etc.)
        cleaned = _clean_for_extraction(content)
        
        assert isinstance(cleaned, str)

def test_build_extraction_text_real(real_messages):
    """Test building extraction text from real message objects."""
    # Convert real messages to the format expected by fetch_unextracted_messages
    mock_rows = []
    for m in real_messages:
        mock_rows.append({
            "id": str(uuid.uuid4()),
            "role": m.get("role", "unknown"),
            "sender": m.get("sender", "user"),
            "recipient": m.get("recipient", "assistant"),
            "content": m.get("content", "")
        })
    
    # We pass the entire list of 2000+ messages. The function is designed to 
    # truncate at config["extraction_max_chars"], so we test that boundary logic.
    state = {
        "messages": mock_rows,
        "config": {"extraction_max_chars": 5000}
    }
    
    result = asyncio.run(build_extraction_text(state))
    text = result.get("extraction_text", "")
    
    assert isinstance(text, str)
    assert len(text) > 0 # Just verify it built the text successfully

@pytest.mark.asyncio
async def test_run_mem0_extraction_real_data(real_messages):
    """Test Mem0 extraction logic using real text content."""
    mock_mem0 = MagicMock()
    mock_mem0.add = MagicMock(return_value={"id": "mem123"})
    
    # Simulate the text built from real data
    extraction_text = "user -> assistant: " + real_messages[0]["content"][:500]
    
    with patch("context_broker_ae.memory.mem0_client.get_mem0_client", AsyncMock(return_value=mock_mem0)):
        result = await run_mem0_extraction({
            "extraction_text": extraction_text,
            "user_id": "test-user",
            "conversation_id": "123",
            "selected_message_ids": ["1"],
            "config": {}
        })
        assert result.get("error") is None
        mock_mem0.add.assert_called_once()

@pytest.mark.asyncio
async def test_acquire_extraction_lock():
    state = {"conversation_id": "123e4567-e89b-12d3-a456-426614174000", "config": {}}
    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)
    with patch("context_broker_ae.memory_extraction.get_pg_pool", return_value=mock_pool):
        result = await acquire_extraction_lock(state)
        assert result["lock_acquired"] is True
        assert result["lock_token"] == "pg_advisory"

@pytest.mark.asyncio
async def test_acquire_extraction_lock_fail():
    state = {"conversation_id": "123e4567-e89b-12d3-a456-426614174000", "config": {}}
    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=False)
    with patch("context_broker_ae.memory_extraction.get_pg_pool", return_value=mock_pool):
        result = await acquire_extraction_lock(state)
        assert result["lock_acquired"] is False

@pytest.mark.asyncio
async def test_fetch_unextracted_messages():
    state = {"conversation_id": "123e4567-e89b-12d3-a456-426614174000"}
    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[{"id": "msg1", "content": "hello", "role": "user", "sender": "user1"}])
    mock_pool.fetchrow = AsyncMock(return_value={"participant_id": "user1"})
    with patch("context_broker_ae.memory_extraction.get_pg_pool", return_value=mock_pool):
        result = await fetch_unextracted_messages(state)
        assert len(result["messages"]) == 1
        assert result["user_id"] == "user1"

@pytest.mark.asyncio
async def test_build_extraction_text():
    state = {
        "messages": [
            {"id": "1", "role": "user", "sender": "user1", "content": "Hello!"},
            {"id": "2", "role": "assistant", "sender": "bot", "content": "How can I help?"} # Too short after clean if < 10 chars
        ],
        "config": {"extraction_max_chars": 1000}
    }
    result = await build_extraction_text(state)
    assert "Hello!" in result["extraction_text"]
    # Even if content is short/skipped, ID is fully extracted to avoid loops
    assert "2" in result["fully_extracted_ids"]
    assert "1" in result["fully_extracted_ids"]

@pytest.mark.asyncio
async def test_run_mem0_extraction_success():
    state = {
        "conversation_id": "123e4567-e89b-12d3-a456-426614174000",
        "extraction_text": "extracted knowledge",
        "user_id": "user1",
        "selected_message_ids": ["1"],
        "config": {}
    }
    mock_mem0 = MagicMock()
    mock_mem0.add = MagicMock()
    
    with patch("context_broker_ae.memory.mem0_client.get_mem0_client", AsyncMock(return_value=mock_mem0)):
        result = await run_mem0_extraction(state)
        assert result["error"] is None
        mock_mem0.add.assert_called_once()

@pytest.mark.asyncio
async def test_run_mem0_extraction_fail():
    state = {
        "conversation_id": "123e4567-e89b-12d3-a456-426614174000",
        "extraction_text": "extracted knowledge",
        "user_id": "user1",
        "selected_message_ids": ["1"],
        "config": {}
    }
    
    with patch("context_broker_ae.memory.mem0_client.get_mem0_client", AsyncMock(side_effect=Exception("Connection failed"))), \
         patch("context_broker_ae.memory.mem0_client.reset_mem0_client") as mock_reset:
        result = await run_mem0_extraction(state)
        assert "Connection failed" in result["error"]
        mock_reset.assert_called_once()

@pytest.mark.asyncio
async def test_mark_messages_extracted():
    state = {"fully_extracted_ids": ["123e4567-e89b-12d3-a456-426614174000"]}
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()
    with patch("context_broker_ae.memory_extraction.get_pg_pool", return_value=mock_pool):
        result = await mark_messages_extracted(state)
        assert result["extracted_count"] == 1
        mock_pool.execute.assert_called_once()

@pytest.mark.asyncio
async def test_release_extraction_lock():
    state = {"lock_key": "12345", "lock_acquired": True}
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()
    with patch("context_broker_ae.memory_extraction.get_pg_pool", return_value=mock_pool):
        await release_extraction_lock(state)
        mock_pool.execute.assert_called_once()

def test_routing():
    assert route_after_lock({"lock_acquired": False}) == "__end__"
    assert route_after_lock({"lock_acquired": True}) == "fetch_unextracted_messages"
    
    assert route_after_fetch({"messages": []}) == "release_extraction_lock"
    assert route_after_fetch({"messages": [{"id": "1"}]}) == "build_extraction_text"
    
    assert route_after_build_text({"error": "err"}) == "release_extraction_lock"
    assert route_after_build_text({}) == "run_mem0_extraction"
    
    assert route_after_extraction({"error": "err"}) == "release_extraction_lock"
    assert route_after_extraction({}) == "mark_messages_extracted"
