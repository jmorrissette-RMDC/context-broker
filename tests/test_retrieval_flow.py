"""
Unit tests for the context retrieval StateGraph flow.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.flows.retrieval_flow import (
    RetrievalState,
    assemble_context_text,
    load_summaries,
    load_window,
)


@pytest.mark.asyncio
async def test_load_window_not_found(mock_pg_pool, sample_config):
    """load_window sets error when context window does not exist."""
    mock_pg_pool.fetchrow = AsyncMock(return_value=None)

    state: RetrievalState = {
        "context_window_id": str(uuid.uuid4()),
        "config": sample_config,
        "window": None,
        "build_type_config": None,
        "conversation_id": None,
        "max_token_budget": 0,
        "tier1_summary": None,
        "tier2_summaries": [],
        "recent_messages": [],
        "semantic_messages": [],
        "knowledge_graph_facts": [],
        "assembly_status": "pending",
        "context_text": None,
        "context_tiers": None,
        "total_tokens_used": 0,
        "error": None,
    }

    with patch("app.flows.retrieval_flow.get_pg_pool", return_value=mock_pg_pool):
        result = await load_window(state)

    assert result["error"] is not None
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_assemble_context_text_all_tiers():
    """assemble_context_text includes all tiers when present."""
    state: RetrievalState = {
        "context_window_id": str(uuid.uuid4()),
        "config": {},
        "window": {"participant_id": "user-1"},
        "build_type_config": {"tier3_pct": 0.72},
        "conversation_id": str(uuid.uuid4()),
        "max_token_budget": 8192,
        "tier1_summary": "This is the archival summary.",
        "tier2_summaries": ["Chunk summary 1.", "Chunk summary 2."],
        "recent_messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "sender_id": "user-1",
                "content": "Hello there",
                "sequence_number": 10,
                "token_count": 5,
            }
        ],
        "semantic_messages": [],
        "knowledge_graph_facts": [],
        "assembly_status": "ready",
        "context_text": None,
        "context_tiers": None,
        "total_tokens_used": 0,
        "error": None,
    }

    result = await assemble_context_text(state)

    assert result["context_text"] is not None
    assert "<archival_summary>" in result["context_text"]
    assert "This is the archival summary." in result["context_text"]
    assert "<chunk_summaries>" in result["context_text"]
    assert "Chunk summary 1." in result["context_text"]
    assert "<recent_messages>" in result["context_text"]
    assert "Hello there" in result["context_text"]


@pytest.mark.asyncio
async def test_assemble_context_text_empty_state():
    """assemble_context_text handles empty state gracefully."""
    state: RetrievalState = {
        "context_window_id": str(uuid.uuid4()),
        "config": {},
        "window": None,
        "build_type_config": {},
        "conversation_id": str(uuid.uuid4()),
        "max_token_budget": 8192,
        "tier1_summary": None,
        "tier2_summaries": [],
        "recent_messages": [],
        "semantic_messages": [],
        "knowledge_graph_facts": [],
        "assembly_status": "ready",
        "context_text": None,
        "context_tiers": None,
        "total_tokens_used": 0,
        "error": None,
    }

    result = await assemble_context_text(state)

    assert result["context_text"] == ""
    assert result["context_tiers"] is not None


@pytest.mark.asyncio
async def test_assemble_context_text_with_knowledge_graph():
    """assemble_context_text includes knowledge graph facts when present."""
    state: RetrievalState = {
        "context_window_id": str(uuid.uuid4()),
        "config": {},
        "window": {"participant_id": "user-1"},
        "build_type_config": {"knowledge_graph_pct": 0.15},
        "conversation_id": str(uuid.uuid4()),
        "max_token_budget": 16000,
        "tier1_summary": None,
        "tier2_summaries": [],
        "recent_messages": [],
        "semantic_messages": [],
        "knowledge_graph_facts": ["User prefers Python.", "User works on AI projects."],
        "assembly_status": "ready",
        "context_text": None,
        "context_tiers": None,
        "total_tokens_used": 0,
        "error": None,
    }

    result = await assemble_context_text(state)

    assert "<knowledge_graph>" in result["context_text"]
    assert "User prefers Python." in result["context_text"]
    assert "User works on AI projects." in result["context_text"]
