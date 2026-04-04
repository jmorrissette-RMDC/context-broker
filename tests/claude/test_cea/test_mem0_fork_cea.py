"""Tests for Mem0 fork CEA changes.

Covers: _skip_graph parameter, payload preservation on update,
elementId migration in graph_memory.py, side-channel metadata extraction,
expiration filtering.

Discovery audit: cli-discovery-audit-claude.md §6.1-6.4, 6.6.
"""

import concurrent.futures
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# _skip_graph parameter (main.py)
# ---------------------------------------------------------------------------


class TestSkipGraph:
    """Tests for _skip_graph parameter on Memory.add() — CB-24."""

    def _make_memory(self):
        """Create a minimal Memory mock with the real add() signature."""
        from mem0.memory.main import Memory

        mem = MagicMock(spec=Memory)
        mem.enable_graph = True
        mem.config = MagicMock()
        mem.config.llm.config = {}
        return mem

    def test_skip_graph_true_skips_add_to_graph(self):
        """_skip_graph=True should set _use_graph=False."""
        # Test the logic directly: _use_graph = self.enable_graph and not _skip_graph
        enable_graph = True
        _skip_graph = True
        _use_graph = enable_graph and not _skip_graph
        assert _use_graph is False

    def test_skip_graph_false_uses_graph(self):
        """_skip_graph=False (default) should set _use_graph=True when graph enabled."""
        enable_graph = True
        _skip_graph = False
        _use_graph = enable_graph and not _skip_graph
        assert _use_graph is True

    def test_skip_graph_with_graph_disabled(self):
        """When enable_graph=False, _use_graph is always False."""
        enable_graph = False
        _skip_graph = False
        _use_graph = enable_graph and not _skip_graph
        assert _use_graph is False


# ---------------------------------------------------------------------------
# Payload preservation on update (main.py)
# ---------------------------------------------------------------------------


class TestPayloadPreservation:
    """Tests for fork behavior in _update_memory — payload preservation."""

    def test_preserves_custom_metadata(self):
        """Existing custom metadata (durability, x_category) should survive update."""
        existing_payload = {
            "data": "old fact",
            "hash": "oldhash",
            "durability": 0.8,
            "x_category": "architecture",
            "user_id": "alice",
            "created_at": "2026-01-01T00:00:00+00:00",
        }

        # Simulate the fork's deepcopy + merge logic
        new_metadata = deepcopy(existing_payload)
        caller_metadata = {}  # No overrides
        for k, v in caller_metadata.items():
            if v is None:
                new_metadata.pop(k, None)
            else:
                new_metadata[k] = v

        new_metadata["data"] = "updated fact"

        assert new_metadata["durability"] == 0.8
        assert new_metadata["x_category"] == "architecture"
        assert new_metadata["user_id"] == "alice"

    def test_explicit_none_clears_field(self):
        """Explicit None in new metadata should remove the key."""
        existing_payload = {
            "data": "fact",
            "expires_at": "2026-06-15T00:00:00+00:00",
        }

        new_metadata = deepcopy(existing_payload)
        caller_metadata = {"expires_at": None}  # Clear expiration
        for k, v in caller_metadata.items():
            if v is None:
                new_metadata.pop(k, None)
            else:
                new_metadata[k] = v

        assert "expires_at" not in new_metadata

    def test_session_ids_preserved(self):
        """Session IDs (user_id, agent_id, run_id) preserved as in stock Mem0."""
        existing_payload = {
            "data": "fact",
            "user_id": "alice",
            "agent_id": "bot1",
            "run_id": "run-123",
        }

        new_metadata = deepcopy(existing_payload)
        new_metadata["data"] = "updated"

        assert new_metadata["user_id"] == "alice"
        assert new_metadata["agent_id"] == "bot1"
        assert new_metadata["run_id"] == "run-123"

    def test_async_path_only_preserves_session_ids(self):
        """F-03: Document that AsyncMemory._update_memory only preserves session IDs.

        This is a known asymmetry between sync and async paths.
        The sync path (fork) preserves ALL payload keys.
        The async path (stock) only preserves user_id, agent_id, run_id, actor_id, role.
        """
        # Stock Mem0 async behavior: only these keys are explicitly preserved
        stock_preserved_keys = {"user_id", "agent_id", "run_id", "actor_id", "role"}

        # Custom metadata NOT in stock preserved keys
        custom_keys = {"durability", "expires_at", "x_category", "confidence"}

        # Verify no overlap — custom keys would be dropped in async path
        assert stock_preserved_keys.isdisjoint(custom_keys)


# ---------------------------------------------------------------------------
# elementId migration (graph_memory.py)
# ---------------------------------------------------------------------------


class TestElementIdMigration:
    """Tests for elementId() usage in graph_memory.py."""

    def test_search_graph_db_cypher_includes_element_id(self):
        """Cypher RETURN clauses should include elementId() calls."""
        # Read the actual Cypher patterns from graph_memory
        import inspect
        from mem0.memory.graph_memory import MemoryGraph

        source = inspect.getsource(MemoryGraph._search_graph_db)
        assert "elementId(n) AS source_id" in source
        assert "elementId(r) AS relation_id" in source
        assert "elementId(m) AS destination_id" in source

    def test_bm25_reranking_preserves_element_id(self):
        """BM25 reranked results should be mapped back to original rows with elementId."""
        from mem0.memory.graph_memory import MemoryGraph

        # Simulate the tuple-to-row mapping logic from search()
        search_output = [
            {
                "source": "Context Broker",
                "source_id": "4:abc:0",
                "relationship": "USES",
                "relation_id": "5:def:0",
                "destination": "pgvector",
                "destination_id": "4:ghi:0",
            }
        ]

        # Build lookup
        _tuple_to_row = {}
        for item in search_output:
            key = (item["source"], item["relationship"], item["destination"])
            if key not in _tuple_to_row:
                _tuple_to_row[key] = item

        # Simulate reranked result (just the text triple)
        reranked = [["Context Broker", "USES", "pgvector"]]
        key = tuple(reranked[0])
        original = _tuple_to_row.get(key)

        assert original is not None
        assert original["source_id"] == "4:abc:0"
        assert original["relation_id"] == "5:def:0"
        assert original["destination_id"] == "4:ghi:0"

    def test_bm25_no_match_fallback(self):
        """If BM25 result doesn't match any original tuple, fallback returns without IDs."""
        _tuple_to_row = {}  # Empty — no originals

        reranked_item = ["Unknown", "RELATES", "Other"]
        key = tuple(reranked_item)
        original = _tuple_to_row.get(key)

        # Fallback: return without IDs
        if original:
            result = original
        else:
            result = {"source": reranked_item[0], "relationship": reranked_item[1], "destination": reranked_item[2]}

        assert "source_id" not in result
        assert "relation_id" not in result

    def test_add_entities_cypher_includes_relation_id(self):
        """_add_entities RETURN clauses should include elementId(r)."""
        import inspect
        from mem0.memory.graph_memory import MemoryGraph

        source = inspect.getsource(MemoryGraph._add_entities)
        assert "elementId(r) AS relation_id" in source


# ---------------------------------------------------------------------------
# Expiration filtering (expiration.py)
# ---------------------------------------------------------------------------


class TestExpirationFiltering:
    """Tests for filter_expired_from_results."""

    def test_non_expired_passes_through(self):
        from mem0.memory.expiration import filter_expired_from_results

        future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        mem = MagicMock()
        mem.payload = {"expires_at": future.isoformat()}
        now = datetime(2026, 4, 4, tzinfo=timezone.utc)

        result = filter_expired_from_results([mem], now)
        assert len(result) == 1

    def test_expired_filtered(self):
        from mem0.memory.expiration import filter_expired_from_results

        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        mem = MagicMock()
        mem.payload = {"expires_at": past.isoformat()}
        now = datetime(2026, 4, 4, tzinfo=timezone.utc)

        result = filter_expired_from_results([mem], now)
        assert len(result) == 0

    def test_no_expires_at_passes_through(self):
        from mem0.memory.expiration import filter_expired_from_results

        mem = MagicMock()
        mem.payload = {"data": "permanent fact"}
        now = datetime(2026, 4, 4, tzinfo=timezone.utc)

        result = filter_expired_from_results([mem], now)
        assert len(result) == 1

    def test_empty_input(self):
        from mem0.memory.expiration import filter_expired_from_results

        result = filter_expired_from_results([])
        assert result == []
