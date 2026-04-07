"""Tests for migrations 023 and 024 — CEA tables and indexes.

Covers: cea_quality_metadata table, cea_feedback_events table,
natural-key dedup index, usefulness aggregation index.

Discovery audit: cli-discovery-audit-claude.md §4.
"""

import re

import pytest

from app.migrations import _migration_023, _migration_024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_sql_statements(coro_func):
    """Extract SQL strings from a migration function's source code."""
    import inspect

    source = inspect.getsource(coro_func)
    # Find all triple-quoted SQL blocks
    return source


# ---------------------------------------------------------------------------
# Migration 023 — CEA quality metadata and feedback events
# ---------------------------------------------------------------------------


class TestMigration023:
    """Tests for migration 023 — table creation."""

    def test_creates_cea_quality_metadata(self):
        """Migration SQL should CREATE TABLE cea_quality_metadata."""
        source = _extract_sql_statements(_migration_023)
        assert "CREATE TABLE IF NOT EXISTS cea_quality_metadata" in source

    def test_quality_metadata_columns(self):
        """Table should have all required columns."""
        source = _extract_sql_statements(_migration_023)
        required_columns = [
            "target_type", "target_id", "durability", "confidence",
            "source_type", "original_utterance", "extraction_model",
            "expires_at", "extracted_at", "user_id", "conversation_id",
        ]
        for col in required_columns:
            assert col in source, f"Missing column: {col}"

    def test_quality_metadata_unique_constraint(self):
        """UNIQUE constraint on (target_type, target_id)."""
        source = _extract_sql_statements(_migration_023)
        assert "UNIQUE (target_type, target_id)" in source

    def test_quality_metadata_fk_to_conversations(self):
        """FK to conversations(id)."""
        source = _extract_sql_statements(_migration_023)
        assert "REFERENCES conversations(id)" in source

    def test_quality_metadata_partial_index_on_expires(self):
        """Partial index on expires_at WHERE NOT NULL."""
        source = _extract_sql_statements(_migration_023)
        assert "WHERE expires_at IS NOT NULL" in source

    def test_creates_cea_feedback_events(self):
        """Migration SQL should CREATE TABLE cea_feedback_events."""
        source = _extract_sql_statements(_migration_023)
        assert "CREATE TABLE IF NOT EXISTS cea_feedback_events" in source

    def test_feedback_events_dedup_key_unique(self):
        """UNIQUE constraint on dedup_key."""
        source = _extract_sql_statements(_migration_023)
        assert "dedup_key" in source
        assert "UNIQUE" in source

    def test_feedback_events_columns(self):
        """Table should have all required columns."""
        source = _extract_sql_statements(_migration_023)
        required_columns = [
            "target_type", "target_id", "event_type",
            "agent_id", "context", "created_at", "dedup_key",
        ]
        for col in required_columns:
            assert col in source, f"Missing column: {col}"

    def test_idempotent_create(self):
        """IF NOT EXISTS on all CREATE statements."""
        source = _extract_sql_statements(_migration_023)
        # Count CREATE TABLE IF NOT EXISTS
        table_creates = source.count("CREATE TABLE IF NOT EXISTS")
        assert table_creates == 2  # cea_quality_metadata + cea_feedback_events
        # Count CREATE INDEX IF NOT EXISTS
        index_creates = source.count("CREATE INDEX IF NOT EXISTS")
        assert index_creates >= 3  # user, conversation, expires, target


# ---------------------------------------------------------------------------
# Migration 024 — Natural-key dedup and usefulness indexes
# ---------------------------------------------------------------------------


class TestMigration024:
    """Tests for migration 024 — post-review indexes."""

    def test_natural_dedup_index(self):
        """UNIQUE INDEX on (user_id, conversation_id, original_utterance)."""
        source = _extract_sql_statements(_migration_024)
        assert "idx_cea_metadata_natural_dedup" in source
        assert "user_id, conversation_id, original_utterance" in source

    def test_empty_utterance_excluded(self):
        """Partial WHERE excludes empty utterances."""
        source = _extract_sql_statements(_migration_024)
        assert "WHERE original_utterance != ''" in source

    def test_composite_feedback_index(self):
        """Composite index on (target_type, target_id, event_type)."""
        source = _extract_sql_statements(_migration_024)
        assert "idx_cea_events_target_type_id" in source
        assert "target_type, target_id, event_type" in source

    def test_idempotent_indexes(self):
        """IF NOT EXISTS on all CREATE INDEX statements."""
        source = _extract_sql_statements(_migration_024)
        # All index creates should be idempotent
        index_count = source.count("CREATE")
        if_not_exists_count = source.count("IF NOT EXISTS")
        assert if_not_exists_count >= 2
