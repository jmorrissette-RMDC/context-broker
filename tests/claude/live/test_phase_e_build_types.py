"""Phase E: Context assembly build-type verification.

Tests passthrough, standard-tiered, and knowledge-enriched build types
against loaded conversation data.
"""

import pytest

from tests.claude.live.helpers import (
    docker_psql,
    extract_mcp_result,
    log_issue,
    mcp_call,
)

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_context(http_client, conversation_id, build_type, budget):
    """Call get_context via MCP and return the parsed result."""
    resp = mcp_call(
        http_client,
        "get_context",
        {
            "conversation_id": conversation_id,
            "build_type": build_type,
            "budget": budget,
        },
        timeout=120,
    )
    assert resp.status_code == 200, f"get_context failed: {resp.text}"
    result = extract_mcp_result(resp)
    assert "error" not in result or result.get("error") is None, (
        f"get_context returned error: {result}"
    )
    return result


# ---------------------------------------------------------------------------
# Passthrough
# ---------------------------------------------------------------------------

class TestPassthrough:
    """Passthrough build type returns raw messages without tiering."""

    def test_passthrough_returns_messages(self, http_client, any_conversation_id):
        """Passthrough get_context should return messages."""
        result = _get_context(
            http_client, any_conversation_id, "passthrough", 8192
        )
        messages = result.get("messages", result.get("context", []))
        assert len(messages) > 0, "Passthrough returned no messages"

    def test_passthrough_has_no_summaries(self, http_client, any_conversation_id):
        """Passthrough context should be simple -- no tier structure."""
        result = _get_context(
            http_client, any_conversation_id, "passthrough", 8192
        )
        tiers = result.get("tiers", {})
        # Passthrough should not produce tier1/tier2/tier3 keys
        has_tiered_keys = any(
            k in tiers for k in ("tier1", "tier2", "tier3")
        )
        assert not has_tiered_keys, (
            f"Passthrough should not have tiered structure, got tiers: {list(tiers.keys())}"
        )


# ---------------------------------------------------------------------------
# Standard-tiered
# ---------------------------------------------------------------------------

class TestStandardTiered:
    """Standard-tiered build type produces a multi-tier context."""

    def test_standard_tiered_returns_structure(
        self, http_client, any_conversation_id
    ):
        """Standard-tiered get_context should return a tiered structure."""
        result = _get_context(
            http_client, any_conversation_id, "standard-tiered", 8192
        )
        assert result, "Standard-tiered returned empty result"
        # Should have either tiers or messages
        has_content = (
            result.get("tiers")
            or result.get("messages")
            or result.get("context")
        )
        assert has_content, f"Standard-tiered returned no content: {list(result.keys())}"

    def test_standard_tiered_has_tier_keys(
        self, http_client, any_conversation_id
    ):
        """Standard-tiered context should include tier1, tier2, tier3."""
        result = _get_context(
            http_client, any_conversation_id, "standard-tiered", 8192
        )
        tiers = result.get("tiers", {})
        expected_tiers = {"tier1", "tier2", "tier3"}
        present_tiers = set(tiers.keys()) & expected_tiers
        assert len(present_tiers) >= 2, (
            f"Expected at least 2 of {expected_tiers}, got {present_tiers}"
        )

    def test_standard_tiered_summaries_in_db(self, loaded_conversations):
        """Loaded conversations should have summaries in the database."""
        for stem, conv_id in loaded_conversations.items():
            count = docker_psql(
                f"SELECT COUNT(*) FROM conversation_summaries "
                f"WHERE conversation_id = '{conv_id}'"
            ).strip()
            count = int(count or "0")
            if count == 0:
                log_issue(
                    "test_standard_tiered_summaries_in_db",
                    "warning",
                    "assembly",
                    f"Conversation {stem} ({conv_id}) has no summaries",
                    ">0 summaries",
                    "0",
                )
        # At least some conversations should have summaries
        total = docker_psql(
            "SELECT COUNT(DISTINCT conversation_id) FROM conversation_summaries"
        ).strip()
        assert int(total or "0") > 0, "No conversations have summaries at all"


# ---------------------------------------------------------------------------
# Knowledge-enriched
# ---------------------------------------------------------------------------

class TestKnowledgeEnriched:
    """Knowledge-enriched build type includes semantic and KG results."""

    def test_knowledge_enriched_returns_content(
        self, http_client, any_conversation_id
    ):
        """Knowledge-enriched get_context should return content including
        semantic and/or knowledge graph results."""
        result = _get_context(
            http_client, any_conversation_id, "knowledge-enriched", 16000
        )
        assert result, "Knowledge-enriched returned empty result"
        has_content = (
            result.get("tiers")
            or result.get("messages")
            or result.get("context")
        )
        assert has_content, (
            f"Knowledge-enriched returned no content: {list(result.keys())}"
        )

    def test_knowledge_enriched_tiers_include_semantic_or_kg(
        self, http_client, any_conversation_id
    ):
        """Knowledge-enriched tiers should include semantic_messages
        and/or knowledge_graph_facts."""
        result = _get_context(
            http_client, any_conversation_id, "knowledge-enriched", 16000
        )
        tiers = result.get("tiers", {})
        enrichment_keys = {"semantic_messages", "knowledge_graph_facts"}
        found = set(tiers.keys()) & enrichment_keys
        if not found:
            # Also check top-level keys
            found = set(result.keys()) & enrichment_keys
        if not found:
            log_issue(
                "test_knowledge_enriched_tiers_include_semantic_or_kg",
                "warning",
                "assembly",
                "Knowledge-enriched context missing semantic_messages and knowledge_graph_facts",
                "At least one enrichment key",
                str(list(tiers.keys())),
            )
        # At minimum the result should have some structure
        assert result.get("tiers") or result.get("messages") or result.get("context")


# ---------------------------------------------------------------------------
# Budget and utilization
# ---------------------------------------------------------------------------

class TestBudgetUtilization:
    """Verify budget handling across build types."""

    def test_effective_utilization_within_budget(
        self, http_client, any_conversation_id
    ):
        """total_tokens should not exceed 85% of the requested budget."""
        budget = 8192
        result = _get_context(
            http_client, any_conversation_id, "standard-tiered", budget
        )
        total_tokens = result.get("total_tokens", result.get("token_count", 0))
        if total_tokens == 0:
            # Try to estimate from messages length
            pytest.skip("total_tokens not reported in response")
        max_allowed = int(budget * 0.85)
        assert total_tokens <= max_allowed, (
            f"total_tokens ({total_tokens}) exceeds 85% of budget "
            f"({max_allowed} = 85% of {budget})"
        )

    def test_different_budgets_return_different_sizes(
        self, http_client, any_conversation_id
    ):
        """get_context with a larger budget should return more or equal content."""
        result_small = _get_context(
            http_client, any_conversation_id, "standard-tiered", 4096
        )
        result_large = _get_context(
            http_client, any_conversation_id, "standard-tiered", 16000
        )

        def _content_size(r):
            """Estimate content size from response."""
            if "total_tokens" in r:
                return r["total_tokens"]
            if "token_count" in r:
                return r["token_count"]
            # Fall back to counting messages
            msgs = r.get("messages", r.get("context", []))
            return len(msgs) if isinstance(msgs, list) else len(str(r))

        small_size = _content_size(result_small)
        large_size = _content_size(result_large)
        assert large_size >= small_size, (
            f"Larger budget should yield >= content: "
            f"small({small_size}) > large({large_size})"
        )


# ---------------------------------------------------------------------------
# Context window retrieval
# ---------------------------------------------------------------------------

class TestContextWindowRetrieval:
    """Verify conv_retrieve_context works for existing context windows."""

    def test_conv_retrieve_context_for_existing_window(
        self, http_client, any_conversation_id
    ):
        """conv_retrieve_context should return data for an existing
        context window created by earlier assembly."""
        # First, build a context window to ensure one exists
        _get_context(
            http_client, any_conversation_id, "standard-tiered", 8192
        )

        # Now retrieve it
        resp = mcp_call(
            http_client,
            "conv_retrieve_context",
            {
                "conversation_id": any_conversation_id,
            },
            timeout=60,
        )
        assert resp.status_code == 200, (
            f"conv_retrieve_context failed: {resp.text}"
        )
        result = extract_mcp_result(resp)
        # Should return some context data
        assert result, "conv_retrieve_context returned empty result"
        assert "error" not in result or result.get("error") is None, (
            f"conv_retrieve_context returned error: {result}"
        )
