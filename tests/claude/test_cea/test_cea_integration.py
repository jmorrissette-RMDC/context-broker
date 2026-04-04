"""Tests for CEA integration points across modules.

Covers: compact_tier1 CEA branch, init_context_node CEAc branch,
stategraph_registry TE flows, register.py changes, knowledge_enriched
budget change, backward-compat shim findings (F-01, F-02).

Discovery audit: cli-discovery-audit-claude.md §5.
"""

import importlib

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# compact_tier1 — CEA extraction branch (standard_tiered.py)
# ---------------------------------------------------------------------------


class TestCompactTier1CeaBranch:
    """Tests for CEA extraction invocation in compact_tier1."""

    def test_cea_extraction_import_exists(self):
        """CEA extraction flow module should be importable."""
        from context_broker_ae.cea_extraction_flow import build_cea_extraction_flow

        assert build_cea_extraction_flow is not None

    def test_cea_extraction_flow_compiles(self):
        """build_cea_extraction_flow should return a compiled graph."""
        from context_broker_ae.cea_extraction_flow import build_cea_extraction_flow

        flow = build_cea_extraction_flow()
        assert flow is not None

    def test_cea_extraction_catches_import_error(self):
        """If CEA extraction module is unavailable, compact_tier1 should handle ImportError."""
        # The integration point catches (ImportError, RuntimeError, ValueError, OSError)
        # Verify the exception types are correct by checking the source
        import inspect
        from context_broker_ae.build_types.standard_tiered import compact_tier1

        source = inspect.getsource(compact_tier1)
        assert "ImportError" in source
        assert "RuntimeError" in source
        assert "cea_extraction_flow" in source

    def test_cea_state_keys_match_flow(self):
        """State keys passed to CEAs flow should match flow's expected state."""
        import inspect
        from context_broker_ae.build_types.standard_tiered import compact_tier1

        source = inspect.getsource(compact_tier1)
        # Verify all required state keys are passed
        required_keys = [
            '"content"', '"tier2_context"', '"tier3_context"',
            '"conversation_id"', '"existing_facts"', '"extraction_output"',
            '"dispatch_results"', '"config"', '"error"',
        ]
        for key in required_keys:
            assert key in source, f"Missing state key in compact_tier1 CEA invocation: {key}"


# ---------------------------------------------------------------------------
# init_context_node — CEAc branch (imperator_flow.py)
# ---------------------------------------------------------------------------


class TestInitContextNodeCeacBranch:
    """Tests for CEAc enrichment in init_context_node."""

    def test_ceac_branch_exists_in_source(self):
        """init_context_node should contain CEAc enrichment branch."""
        import inspect
        from context_broker_te.imperator_flow import init_context_node

        source = inspect.getsource(init_context_node)
        assert "cea" in source
        assert "ceac" in source
        assert "build_ceac_enrichment_flow" in source

    def test_ceac_catches_errors_gracefully(self):
        """CEAc branch should catch errors and continue without enrichment."""
        import inspect
        from context_broker_te.imperator_flow import init_context_node

        source = inspect.getsource(init_context_node)
        # Should catch same exception types as compact_tier1
        assert "ImportError" in source
        assert "RuntimeError" in source

    def test_ceac_builds_search_fn(self):
        """CEAc branch should build _search_fn that wraps knowledge_search dispatch."""
        import inspect
        from context_broker_te.imperator_flow import init_context_node

        source = inspect.getsource(init_context_node)
        assert "knowledge_search" in source
        assert "_search_fn" in source

    def test_ceac_builds_feedback_fn(self):
        """CEAc branch should build _feedback_fn that wraps knowledge_feedback dispatch."""
        import inspect
        from context_broker_te.imperator_flow import init_context_node

        source = inspect.getsource(init_context_node)
        assert "knowledge_feedback" in source
        assert "_feedback_fn" in source


# ---------------------------------------------------------------------------
# stategraph_registry — TE flows scanning
# ---------------------------------------------------------------------------


class TestTeFlowsScanning:
    """Tests for TE package flows dict scanning."""

    def test_te_register_exports_flows(self):
        """TE register() should return a dict with 'flows' key."""
        from context_broker_te.register import register

        reg = register()
        assert "flows" in reg
        assert "ceac_enrichment" in reg["flows"]

    def test_te_register_tools_required(self):
        """TE register() should list knowledge_search and knowledge_feedback as required tools."""
        from context_broker_te.register import register

        reg = register()
        assert "knowledge_search" in reg["tools_required"]
        assert "knowledge_feedback" in reg["tools_required"]


# ---------------------------------------------------------------------------
# knowledge_enriched.py — RAG budget reservation removed
# ---------------------------------------------------------------------------


class TestKnowledgeEnrichedBudget:
    """Tests for knowledge-enriched build type budget change."""

    def test_no_rag_budget_reservation_in_source(self):
        """ke_load_recent_messages should not reserve budget for semantic/KG retrieval."""
        import inspect
        from context_broker_ae.build_types.knowledge_enriched import ke_load_recent_messages

        source = inspect.getsource(ke_load_recent_messages)
        # Should NOT contain semantic_retrieval_pct or knowledge_graph_pct
        assert "semantic_retrieval_pct" not in source
        assert "knowledge_graph_pct" not in source


# ---------------------------------------------------------------------------
# F-01: Broken retrieval_flow.py backward-compat shim
# ---------------------------------------------------------------------------


class TestRetrievalFlowShim:
    """Tests for F-01: retrieval_flow.py imports removed functions."""

    def test_retrieval_flow_import_fails(self):
        """Importing retrieval_flow.py should fail because ke_inject_* were removed.

        This test documents F-01: the backward-compat shim imports
        ke_inject_semantic_retrieval and ke_inject_knowledge_graph which
        no longer exist in knowledge_enriched.py.
        """
        with pytest.raises(ImportError):
            # Force fresh import to catch the broken import
            import context_broker_ae.retrieval_flow
            importlib.reload(context_broker_ae.retrieval_flow)


# ---------------------------------------------------------------------------
# F-02: Broken context_assembly.py backward-compat shim
# ---------------------------------------------------------------------------


class TestContextAssemblyShim:
    """Tests for F-02: context_assembly.py imports renamed functions."""

    def test_context_assembly_import_fails(self):
        """Importing context_assembly.py should fail because functions were renamed.

        This test documents F-02: the backward-compat shim imports
        calculate_tier_boundaries, consolidate_archival_summary,
        summarize_message_chunks which were renamed to
        calculate_compaction_state, run_full_compaction, compact_tier1.
        """
        with pytest.raises(ImportError):
            import context_broker_ae.context_assembly
            importlib.reload(context_broker_ae.context_assembly)
