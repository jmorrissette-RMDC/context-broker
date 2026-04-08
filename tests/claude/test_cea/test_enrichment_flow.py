"""Tests for cea_enrichment_flow.py — CEAc enrichment StateGraph.

Covers: ranking functions (_compute_memory_quality, _compute_trustworthiness,
_rank_results), decide_search, execute_search, evaluate_and_rank,
assemble_context, record_feedback, _should_iterate, build_ceac_enrichment_flow.

Discovery audit: cli-discovery-audit-claude.md §3.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Ranking helpers
# ---------------------------------------------------------------------------


class TestComputeMemoryQuality:
    """Tests for _compute_memory_quality — REQ-CEA-C06."""

    def test_zero_feedback_returns_durability(self):
        from context_broker_te.cea_enrichment_flow import _compute_memory_quality

        result = _compute_memory_quality(0.8, {"total_events": 0}, {})
        assert result == 0.8

    def test_high_positive_feedback(self):
        from context_broker_te.cea_enrichment_flow import _compute_memory_quality

        usefulness = {"total_events": 20, "used": 18, "discarded": 2}
        result = _compute_memory_quality(0.5, usefulness, {"cea": {"ranking": {"usefulness_crossover_events": 10}}})
        # With 20 events (2x crossover), usefulness dominates
        assert result > 0.5  # Should shift toward usefulness score

    def test_negative_feedback(self):
        from context_broker_te.cea_enrichment_flow import _compute_memory_quality

        usefulness = {"total_events": 10, "used": 0, "discarded": 5, "contradicted": 5}
        result = _compute_memory_quality(0.8, usefulness, {"cea": {"ranking": {"usefulness_crossover_events": 10}}})
        assert result < 0.8  # Negative feedback pulls down


class TestComputeTrustworthiness:
    """Tests for _compute_trustworthiness — REQ-CEA-Q02."""

    def test_default_weights(self):
        from context_broker_te.cea_enrichment_flow import _compute_trustworthiness

        assert _compute_trustworthiness(1.0, "decision", {}) == 1.0
        assert _compute_trustworthiness(1.0, "observation", {}) == 0.8
        assert _compute_trustworthiness(1.0, "speculation", {}) == 0.5

    def test_custom_weight(self):
        from context_broker_te.cea_enrichment_flow import _compute_trustworthiness

        config = {"cea": {"source_type_weights": {"decision": 0.5}}}
        assert _compute_trustworthiness(1.0, "decision", config) == 0.5

    def test_unknown_source_type(self):
        from context_broker_te.cea_enrichment_flow import _compute_trustworthiness

        result = _compute_trustworthiness(1.0, "unknown_type", {})
        assert result == 0.7  # Fallback


class TestRankResults:
    """Tests for _rank_results — REQ-CEA-C03/C04."""

    def test_sorted_by_score(self):
        from context_broker_te.cea_enrichment_flow import _rank_results

        results = [
            {"score": 0.5, "_metadata": {"durability": 0.5, "confidence": 1.0, "source_type": "observation"}, "_usefulness": {"total_events": 0}},
            {"score": 0.9, "_metadata": {"durability": 0.9, "confidence": 1.0, "source_type": "decision"}, "_usefulness": {"total_events": 0}},
        ]
        ranked = _rank_results(results, {})
        assert ranked[0]["score"] == 0.9

    def test_below_threshold_filtered(self):
        from context_broker_te.cea_enrichment_flow import _rank_results

        results = [
            {"score": 0.9, "_metadata": {"durability": 0.5, "confidence": 0.01, "source_type": "speculation"}, "_usefulness": {"total_events": 0}},
        ]
        config = {"cea": {"ranking": {"trustworthiness_min_threshold": 0.1}}}
        ranked = _rank_results(results, config)
        assert len(ranked) == 0  # 0.5 * 0.01 = 0.005 < 0.1

    def test_empty_results(self):
        from context_broker_te.cea_enrichment_flow import _rank_results

        assert _rank_results([], {}) == []

    def test_exploration_mixing(self):
        from context_broker_te.cea_enrichment_flow import _rank_results

        scored = [
            {"score": 0.9, "_metadata": {"durability": 0.9, "confidence": 1.0, "source_type": "decision"}, "_usefulness": {"total_events": 10, "used": 10}},
        ]
        underexplored = [
            {"score": 0.5, "_metadata": {"durability": 0.5, "confidence": 0.5, "source_type": "observation"}, "_usefulness": {"total_events": 1}},
        ]
        config = {"cea": {"ranking": {"exploration_rate": 0.5, "exploration_min_feedback": 3}}}
        ranked = _rank_results(scored + underexplored, config)
        assert len(ranked) == 2  # Both included (exploration mixing)


# ---------------------------------------------------------------------------
# StateGraph nodes
# ---------------------------------------------------------------------------


class TestDecideSearch:
    """Tests for decide_search node."""

    @pytest.mark.asyncio
    async def test_first_iteration_uses_query(self):
        from context_broker_te.cea_enrichment_flow import decide_search

        state = {
            "query": "architecture decisions",
            "search_queries": [],
            "search_results": [],
            "iteration_count": 0,
            "tiers": [],
            "config": {},
        }
        result = await decide_search(state)
        assert result["query"] == "architecture decisions"

    @pytest.mark.asyncio
    async def test_first_iteration_extracts_from_tiers(self):
        from context_broker_te.cea_enrichment_flow import decide_search

        state = {
            "query": "",
            "search_queries": [],
            "search_results": [],
            "iteration_count": 0,
            "tiers": [{"role": "user", "content": "Tell me about Docker deployment"}],
            "config": {},
        }
        result = await decide_search(state)
        assert "Docker" in result["query"]


class TestExecuteSearch:
    """Tests for execute_search node."""

    @pytest.mark.asyncio
    async def test_success(self):
        from context_broker_te.cea_enrichment_flow import execute_search

        search_fn = AsyncMock(return_value={"vector_facts": [{"id": "f1", "memory": "fact"}], "graph_relations": []})
        state = {
            "query": "architecture",
            "search_fn": search_fn,
            "user_id": "user1",
            "search_results": [],
            "iteration_count": 0,
            "config": {"cea": {"ceac": {"max_memories": 50}}},
        }
        result = await execute_search(state)
        assert len(result["search_results"]) == 1
        assert result["iteration_count"] == 1

    @pytest.mark.asyncio
    async def test_empty_query_skips(self):
        from context_broker_te.cea_enrichment_flow import execute_search

        state = {
            "query": "",
            "search_fn": AsyncMock(),
            "search_results": [],
            "iteration_count": 0,
            "config": {"cea": {"ceac": {"max_memories": 50}}},
        }
        result = await execute_search(state)
        assert result["iteration_count"] == 1
        state["search_fn"].assert_not_called()

    @pytest.mark.asyncio
    async def test_no_search_fn(self):
        from context_broker_te.cea_enrichment_flow import execute_search

        state = {
            "query": "test",
            "search_fn": None,
            "search_results": [],
            "iteration_count": 0,
            "config": {"cea": {"ceac": {"max_memories": 50}}},
        }
        result = await execute_search(state)
        assert result["iteration_count"] == 1

    @pytest.mark.asyncio
    async def test_dedup_across_iterations(self):
        from context_broker_te.cea_enrichment_flow import execute_search

        search_fn = AsyncMock(return_value={"vector_facts": [{"id": "f1", "memory": "fact"}], "graph_relations": []})
        state = {
            "query": "test",
            "search_fn": search_fn,
            "user_id": None,
            "search_results": [{"id": "f1", "memory": "fact"}],  # Already have f1
            "iteration_count": 1,
            "config": {"cea": {"ceac": {"max_memories": 50}}},
        }
        result = await execute_search(state)
        assert len(result["search_results"]) == 1  # No duplicate


# ---------------------------------------------------------------------------
# _should_iterate
# ---------------------------------------------------------------------------


class TestShouldIterate:
    """Tests for _should_iterate routing."""

    def test_max_iterations(self):
        from context_broker_te.cea_enrichment_flow import _should_iterate

        state = {"iteration_count": 3, "query": "test", "config": {"cea": {"ceac": {"max_iterations": 3}}}}
        assert _should_iterate(state) == "assemble_context"

    def test_empty_query_done(self):
        from context_broker_te.cea_enrichment_flow import _should_iterate

        state = {"iteration_count": 1, "query": "", "config": {"cea": {"ceac": {"max_iterations": 3}}}}
        assert _should_iterate(state) == "assemble_context"

    def test_iteration_gt_0_refines(self):
        from context_broker_te.cea_enrichment_flow import _should_iterate

        state = {"iteration_count": 1, "query": "refined query", "search_results": [], "config": {"cea": {"ceac": {"max_iterations": 3}}}}
        assert _should_iterate(state) == "decide_search"


# ---------------------------------------------------------------------------
# build_ceac_enrichment_flow (singleton)
# ---------------------------------------------------------------------------


class TestBuildEnrichmentFlow:
    """Tests for build_ceac_enrichment_flow."""

    def test_returns_compiled_graph(self):
        from context_broker_te.cea_enrichment_flow import build_ceac_enrichment_flow

        flow = build_ceac_enrichment_flow()
        assert flow is not None


# ---------------------------------------------------------------------------
# CEAc refactor tests (#551)
# ---------------------------------------------------------------------------


class TestRecordFeedbackConcurrent:
    """M1: record_feedback dispatches events concurrently via asyncio.gather."""

    @pytest.mark.asyncio
    async def test_concurrent_dispatch(self):
        """10 events with 0.1s delay each should complete in ~0.1s, not 1s."""
        from context_broker_te.cea_enrichment_flow import record_feedback

        call_count = 0

        async def slow_feedback(**kwargs):
            nonlocal call_count
            await asyncio.sleep(0.1)
            call_count += 1
            return True

        events = [
            {"target_type": "fact", "target_id": f"id-{i}", "event_type": "used"}
            for i in range(10)
        ]
        state = {"feedback_events": events, "feedback_fn": slow_feedback}

        t0 = time.monotonic()
        await record_feedback(state)
        elapsed = time.monotonic() - t0

        assert call_count == 10
        assert elapsed < 0.5, f"Expected concurrent (~0.1s), got {elapsed:.2f}s (sequential would be ~1s)"


class TestRecordFeedbackPartialFailure:
    """M2: One event failure does not cancel others."""

    @pytest.mark.asyncio
    async def test_partial_failure_isolates(self):
        from context_broker_te.cea_enrichment_flow import record_feedback

        call_log = []

        async def failing_feedback(**kwargs):
            tid = kwargs.get("target_id", "")
            if tid == "id-2":
                raise RuntimeError("deliberate failure")
            call_log.append(tid)
            return True

        events = [
            {"target_type": "fact", "target_id": f"id-{i}", "event_type": "used"}
            for i in range(5)
        ]
        state = {"feedback_events": events, "feedback_fn": failing_feedback}
        await record_feedback(state)

        assert "id-2" not in call_log
        assert len(call_log) == 4, f"Expected 4 successes, got {call_log}"


class TestTemplateFnInjected:
    """M3: assemble_context uses injected template_fn."""

    @pytest.mark.asyncio
    async def test_template_fn_called(self):
        from context_broker_te.cea_enrichment_flow import assemble_context

        template_fn = AsyncMock(return_value="FACTS: {vector_facts}\nRELS: {graph_relations}")

        state = {
            "ranked_results": [
                {"memory": "test fact", "_score": 0.9, "id": "f1"},
            ],
            "search_results": [
                {"memory": "test fact", "_score": 0.9, "id": "f1"},
            ],
            "query": "test query",
            "template_fn": template_fn,
            "config": {"cea": {"ceac": {"max_token_budget": 8000}}},
        }
        result = await assemble_context(state)

        template_fn.assert_called_once_with("cea_output")
        assert "FACTS:" in result["enriched_context"]


class TestTemplateFnFallback:
    """M4: assemble_context uses fallback when template_fn is missing."""

    @pytest.mark.asyncio
    async def test_no_template_fn_uses_fallback(self):
        from context_broker_te.cea_enrichment_flow import assemble_context

        state = {
            "ranked_results": [
                {"memory": "test fact", "_score": 0.9, "id": "f1"},
            ],
            "search_results": [
                {"memory": "test fact", "_score": 0.9, "id": "f1"},
            ],
            "query": "test query",
            "config": {"cea": {"ceac": {"max_token_budget": 8000}}},
        }
        result = await assemble_context(state)

        assert "## Retrieved Knowledge" in result["enriched_context"]
        assert "Facts" in result["enriched_context"]

    @pytest.mark.asyncio
    async def test_template_fn_failure_uses_fallback(self):
        """M4b: template_fn raises — fallback used, no crash."""
        from context_broker_te.cea_enrichment_flow import assemble_context

        async def broken_template(name):
            raise IOError("template file missing")

        state = {
            "ranked_results": [],
            "search_results": [],
            "query": "test",
            "template_fn": broken_template,
            "config": {"cea": {"ceac": {"max_token_budget": 8000}}},
        }
        result = await assemble_context(state)
        assert "## Retrieved Knowledge" in result["enriched_context"]


class TestExecuteSearchDoesNotMutateState:
    """M5: execute_search returns new list, does not mutate input."""

    @pytest.mark.asyncio
    async def test_state_list_unchanged(self):
        from context_broker_te.cea_enrichment_flow import execute_search

        original_results = [{"id": "existing-1", "memory": "old"}]
        original_copy = list(original_results)

        async def mock_search(query, user_id=None, limit=10, **kw):
            return {"vector_facts": [{"id": "new-1", "memory": "new"}], "graph_relations": []}

        state = {
            "query": "test",
            "user_id": "u1",
            "search_fn": mock_search,
            "search_results": original_results,
            "iteration_count": 0,
            "config": {"cea": {"ceac": {"max_memories": 50}}},
        }
        result = await execute_search(state)

        assert original_results == original_copy, "execute_search mutated the input list"
        assert len(result["search_results"]) == 2


class TestCeacEmptyKnowledgeStore:
    """M6: CEAc handles zero search results gracefully."""

    @pytest.mark.asyncio
    async def test_empty_results_no_crash(self):
        from context_broker_te.cea_enrichment_flow import execute_search, evaluate_and_rank, assemble_context

        async def empty_search(query, user_id=None, limit=10, **kw):
            return {"vector_facts": [], "graph_relations": []}

        search_state = {
            "query": "nonexistent topic",
            "user_id": "u1",
            "search_fn": empty_search,
            "search_results": [],
            "iteration_count": 0,
            "config": {"cea": {"ceac": {"max_memories": 50}}},
        }
        search_result = await execute_search(search_state)
        assert search_result["search_results"] == []

        rank_state = {
            "search_results": [],
            "config": {"cea": {"ceac": {"max_token_budget": 8000}, "ranking": {}}},
        }
        rank_result = await evaluate_and_rank(rank_state)
        assert rank_result["ranked_results"] == []

        assemble_state = {
            "ranked_results": [],
            "search_results": [],
            "query": "test",
            "config": {"cea": {"ceac": {"max_token_budget": 8000}}},
        }
        assemble_result = await assemble_context(assemble_state)
        assert assemble_result["enriched_context"]  # should have fallback content


class TestCeacUserIdPassedToSearch:
    """M7: search_fn receives user_id from state."""

    @pytest.mark.asyncio
    async def test_user_id_forwarded(self):
        from context_broker_te.cea_enrichment_flow import execute_search

        received_user_id = None

        async def capture_search(query, user_id=None, limit=10, **kw):
            nonlocal received_user_id
            received_user_id = user_id
            return {"vector_facts": [], "graph_relations": []}

        state = {
            "query": "test",
            "user_id": "specific-user-42",
            "search_fn": capture_search,
            "search_results": [],
            "iteration_count": 0,
            "config": {"cea": {"ceac": {"max_memories": 50}}},
        }
        await execute_search(state)
        assert received_user_id == "specific-user-42"
