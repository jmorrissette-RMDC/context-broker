"""Tests for cea_extraction_flow.py — CEAs extraction StateGraph.

Covers: search_existing_facts, run_extraction_llm, dispatch_results,
handle_error, _should_dispatch, build_cea_extraction_flow.

Discovery audit: cli-discovery-audit-claude.md §2.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_state(**overrides):
    state = {
        "content": "user (alice): We decided to use pgvector for embedding storage",
        "tier2_context": "Discussion about vector database selection",
        "tier3_context": "Architecture planning for the Context Broker memory subsystem",
        "conversation_id": "conv-123",
        "existing_facts": [],
        "extraction_output": None,
        "dispatch_results": {},
        "config": {
            "cea": {
                "pre_extraction_fact_limit": 20,
                "retrieval_scope": "conversation",
            },
            "extraction": {"model": "gpt-4.1"},
        },
        "error": None,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# search_existing_facts
# ---------------------------------------------------------------------------


class TestSearchExistingFacts:
    """Tests for search_existing_facts node."""

    @pytest.mark.asyncio
    async def test_conversation_scope(self):
        from context_broker_ae.cea_extraction_flow import search_existing_facts

        with patch(
            "context_broker_ae.memory.quality_wrapper.get_quality_wrapper",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_wrapper = AsyncMock()
            mock_wrapper.search = AsyncMock(return_value={"vector_facts": [], "graph_relations": []})
            mock_get.return_value = mock_wrapper

            result = await search_existing_facts(_base_state())
            assert "existing_facts" in result
            assert isinstance(result["existing_facts"], list)

    @pytest.mark.asyncio
    async def test_search_failure_returns_empty(self):
        from context_broker_ae.cea_extraction_flow import search_existing_facts

        with patch(
            "context_broker_ae.memory.quality_wrapper.get_quality_wrapper",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_wrapper = AsyncMock()
            mock_wrapper.search = AsyncMock(side_effect=RuntimeError("DB down"))
            mock_get.return_value = mock_wrapper

            result = await search_existing_facts(_base_state())
            assert result["existing_facts"] == []


# ---------------------------------------------------------------------------
# run_extraction_llm
# ---------------------------------------------------------------------------


class TestRunExtractionLLM:
    """Tests for run_extraction_llm node."""

    @pytest.mark.asyncio
    async def test_valid_json_response(self):
        from context_broker_ae.cea_extraction_flow import run_extraction_llm

        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "facts": [
                {
                    "content": "Uses pgvector for vector storage",
                    "durability": 0.9,
                    "confidence": 0.85,
                    "source_type": "decision",
                    "relationship": "NEW",
                    "user_id": "alice",
                    "original_utterance": "We decided to use pgvector",
                }
            ]
        })

        with patch("context_broker_ae.cea_extraction_flow.get_chat_model") as mock_llm_fn, \
             patch("context_broker_ae.cea_extraction_flow.async_load_prompt", new_callable=AsyncMock) as mock_prompt:
            mock_llm = AsyncMock()
            mock_llm.ainvoke = AsyncMock(return_value=mock_response)
            mock_llm_fn.return_value = mock_llm
            mock_prompt.return_value = "Extract facts: {current_date} {existing_facts} {content} {tier2_context} {tier3_context}"

            result = await run_extraction_llm(_base_state())
            assert result["extraction_output"] is not None
            assert len(result["extraction_output"]["facts"]) == 1

    @pytest.mark.asyncio
    async def test_invalid_json_sets_error(self):
        from context_broker_ae.cea_extraction_flow import run_extraction_llm

        mock_response = MagicMock()
        mock_response.content = "not valid json {"

        with patch("context_broker_ae.cea_extraction_flow.get_chat_model") as mock_llm_fn, \
             patch("context_broker_ae.cea_extraction_flow.async_load_prompt", new_callable=AsyncMock) as mock_prompt:
            mock_llm = AsyncMock()
            mock_llm.ainvoke = AsyncMock(return_value=mock_response)
            mock_llm_fn.return_value = mock_llm
            mock_prompt.return_value = "Extract: {current_date} {existing_facts} {content} {tier2_context} {tier3_context}"

            result = await run_extraction_llm(_base_state())
            assert result["extraction_output"] is None
            assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_markdown_fenced_json(self):
        from context_broker_ae.cea_extraction_flow import run_extraction_llm

        mock_response = MagicMock()
        mock_response.content = '```json\n{"facts": []}\n```'

        with patch("context_broker_ae.cea_extraction_flow.get_chat_model") as mock_llm_fn, \
             patch("context_broker_ae.cea_extraction_flow.async_load_prompt", new_callable=AsyncMock) as mock_prompt:
            mock_llm = AsyncMock()
            mock_llm.ainvoke = AsyncMock(return_value=mock_response)
            mock_llm_fn.return_value = mock_llm
            mock_prompt.return_value = "Extract: {current_date} {existing_facts} {content} {tier2_context} {tier3_context}"

            result = await run_extraction_llm(_base_state())
            assert result["extraction_output"] is not None
            assert result["extraction_output"]["facts"] == []


# ---------------------------------------------------------------------------
# dispatch_results
# ---------------------------------------------------------------------------


class TestDispatchResults:
    """Tests for dispatch_results node."""

    @pytest.mark.asyncio
    async def test_new_fact_dispatched(self):
        from context_broker_ae.cea_extraction_flow import dispatch_results

        extraction = {
            "facts": [{
                "content": "Uses pgvector",
                "durability": 0.9,
                "confidence": 0.8,
                "source_type": "decision",
                "relationship": "NEW",
                "user_id": "alice",
                "original_utterance": "We decided to use pgvector",
            }]
        }

        with patch(
            "context_broker_ae.memory.quality_wrapper.get_quality_wrapper",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_wrapper = AsyncMock()
            mock_wrapper.add = AsyncMock(return_value={"memory_id": "mem-1", "relation_ids": []})
            mock_wrapper.write_metadata = AsyncMock()
            mock_get.return_value = mock_wrapper

            state = _base_state(extraction_output=extraction)
            result = await dispatch_results(state)
            assert result["dispatch_results"]["new"] == 1
            mock_wrapper.add.assert_called_once()
            mock_wrapper.write_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_duplicate_skipped(self):
        from context_broker_ae.cea_extraction_flow import dispatch_results

        extraction = {
            "facts": [{
                "content": "duplicate fact",
                "relationship": "DUPLICATE",
            }]
        }

        with patch(
            "context_broker_ae.memory.quality_wrapper.get_quality_wrapper",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_wrapper = AsyncMock()
            mock_get.return_value = mock_wrapper

            state = _base_state(extraction_output=extraction)
            result = await dispatch_results(state)
            assert result["dispatch_results"]["duplicate"] == 1
            mock_wrapper.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_extraction_output(self):
        from context_broker_ae.cea_extraction_flow import dispatch_results

        state = _base_state(extraction_output=None)
        result = await dispatch_results(state)
        assert result["dispatch_results"]["new"] == 0


# ---------------------------------------------------------------------------
# handle_error
# ---------------------------------------------------------------------------


class TestHandleError:
    """Tests for handle_error node."""

    @pytest.mark.asyncio
    async def test_logs_and_returns(self):
        from context_broker_ae.cea_extraction_flow import handle_error

        state = _base_state(error="LLM call failed")
        result = await handle_error(state)
        assert "error" in result["dispatch_results"]


# ---------------------------------------------------------------------------
# _should_dispatch
# ---------------------------------------------------------------------------


class TestShouldDispatch:
    """Tests for _should_dispatch routing."""

    def test_routes_to_error_on_error(self):
        from context_broker_ae.cea_extraction_flow import _should_dispatch

        state = _base_state(error="something broke")
        assert _should_dispatch(state) == "handle_error"

    def test_routes_to_dispatch_on_output(self):
        from context_broker_ae.cea_extraction_flow import _should_dispatch

        state = _base_state(extraction_output={"facts": []})
        assert _should_dispatch(state) == "dispatch_results"

    def test_routes_to_error_on_no_output(self):
        from context_broker_ae.cea_extraction_flow import _should_dispatch

        state = _base_state(extraction_output=None)
        assert _should_dispatch(state) == "handle_error"


# ---------------------------------------------------------------------------
# build_cea_extraction_flow (singleton)
# ---------------------------------------------------------------------------


class TestBuildExtractionFlow:
    """Tests for build_cea_extraction_flow singleton."""

    def test_returns_compiled_graph(self):
        from context_broker_ae.cea_extraction_flow import build_cea_extraction_flow

        flow = build_cea_extraction_flow()
        assert flow is not None

    def test_singleton(self):
        from context_broker_ae.cea_extraction_flow import build_cea_extraction_flow

        f1 = build_cea_extraction_flow()
        f2 = build_cea_extraction_flow()
        assert f1 is f2
