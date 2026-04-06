"""Tests for quality_gate.py — Mem0 fork pre-write quality gate.

Covers: extract_metadata_from_facts, apply_quality_gate,
apply_post_update_gate, _normalize_expires_at.

Discovery audit: cli-discovery-audit-claude.md §6.5.
"""

import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# extract_metadata_from_facts
# ---------------------------------------------------------------------------


class TestExtractMetadataFromFacts:
    """Tests for extract_metadata_from_facts."""

    def test_plain_string_facts(self):
        from mem0.memory.quality_gate import extract_metadata_from_facts

        cleaned, metadata = extract_metadata_from_facts(["fact one", "fact two"])
        assert cleaned == ["fact one", "fact two"]
        assert metadata == [{}, {}]

    def test_dict_facts_with_metadata(self):
        from mem0.memory.quality_gate import extract_metadata_from_facts

        raw = [{"fact": "Uses pgvector", "durability": 0.8, "confidence": 0.9}]
        cleaned, metadata = extract_metadata_from_facts(raw)
        assert metadata[0]["durability"] == 0.8
        assert metadata[0]["confidence"] == 0.9

    def test_dict_with_text_key(self):
        from mem0.memory.quality_gate import extract_metadata_from_facts

        raw = [{"text": "Some fact", "x_category": "architecture"}]
        cleaned, metadata = extract_metadata_from_facts(raw)
        assert metadata[0]["x_category"] == "architecture"

    def test_reserved_key_rejected(self, caplog):
        from mem0.memory.quality_gate import extract_metadata_from_facts

        raw = [{"fact": "A fact", "user_id": "should_be_rejected"}]
        with caplog.at_level(logging.WARNING):
            cleaned, metadata = extract_metadata_from_facts(raw)
        assert "user_id" not in metadata[0]
        assert "reserved metadata key" in caplog.text

    def test_expires_at_iso_normalization(self):
        from mem0.memory.quality_gate import extract_metadata_from_facts

        raw = [{"fact": "Temp fact", "expires_at": "2026-06-15T00:00:00Z"}]
        cleaned, metadata = extract_metadata_from_facts(raw)
        assert "expires_at" in metadata[0]
        assert "2026-06-15" in metadata[0]["expires_at"]

    def test_expires_at_unix_timestamp(self):
        from mem0.memory.quality_gate import extract_metadata_from_facts

        raw = [{"fact": "Temp fact", "expires_at": 1750000000}]
        cleaned, metadata = extract_metadata_from_facts(raw)
        assert "expires_at" in metadata[0]
        assert isinstance(metadata[0]["expires_at"], str)

    def test_expires_at_none_preserved(self):
        from mem0.memory.quality_gate import extract_metadata_from_facts

        raw = [{"fact": "Permanent fact", "expires_at": None}]
        cleaned, metadata = extract_metadata_from_facts(raw)
        # None expires_at is not included (no key, not None value)
        # because the code skips normalization when value is None
        assert metadata[0].get("expires_at") is None

    def test_expires_at_invalid_dropped(self, caplog):
        from mem0.memory.quality_gate import extract_metadata_from_facts

        raw = [{"fact": "Bad date", "expires_at": "not-a-date"}]
        with caplog.at_level(logging.WARNING):
            cleaned, metadata = extract_metadata_from_facts(raw)
        assert "expires_at" not in metadata[0]

    def test_empty_input(self):
        from mem0.memory.quality_gate import extract_metadata_from_facts

        cleaned, metadata = extract_metadata_from_facts([])
        assert cleaned == []
        assert metadata == []

    def test_dict_without_fact_or_text_key(self):
        from mem0.memory.quality_gate import extract_metadata_from_facts

        raw = [{"unknown_key": "value"}]
        cleaned, metadata = extract_metadata_from_facts(raw)
        # Passed through as-is for normalize_facts to handle
        assert cleaned == [{"unknown_key": "value"}]
        assert metadata == [{}]


# ---------------------------------------------------------------------------
# apply_quality_gate
# ---------------------------------------------------------------------------


class TestApplyQualityGate:
    """Tests for apply_quality_gate."""

    def test_all_facts_pass(self):
        from mem0.memory.quality_gate import apply_quality_gate

        facts = ["This is a valid fact about architecture"]
        meta = [{}]
        surviving, surv_meta, rejected = apply_quality_gate(facts, meta)
        assert surviving == facts
        assert rejected == 0

    def test_short_fact_rejected(self):
        from mem0.memory.quality_gate import apply_quality_gate

        facts = ["short"]
        meta = [{}]
        surviving, _, rejected = apply_quality_gate(facts, meta, min_length=20)
        assert rejected == 1
        assert len(surviving) == 0

    def test_regex_pattern_rejection(self):
        from mem0.memory.quality_gate import apply_quality_gate

        facts = ["Task #42 was updated"]
        meta = [{}]
        rules = [{"pattern": r"^Task #\d+", "reason": "task reference"}]
        surviving, _, rejected = apply_quality_gate(facts, meta, rejection_rules=rules)
        assert rejected == 1

    def test_invalid_regex_skipped(self, caplog):
        from mem0.memory.quality_gate import apply_quality_gate

        facts = ["Normal fact content here"]
        meta = [{}]
        rules = [{"pattern": r"[invalid(", "reason": "bad regex"}]
        with caplog.at_level(logging.WARNING):
            surviving, _, rejected = apply_quality_gate(facts, meta, rejection_rules=rules)
        assert rejected == 0  # Bad regex skipped, fact passes
        assert "invalid regex" in caplog.text

    def test_custom_validator_rejects(self):
        from mem0.memory.quality_gate import apply_quality_gate

        facts = ["Some fact to validate"]
        meta = [{}]
        validator = lambda fact, m: False  # Reject everything
        surviving, _, rejected = apply_quality_gate(facts, meta, custom_validator=validator)
        assert rejected == 1

    def test_custom_validator_accepts(self):
        from mem0.memory.quality_gate import apply_quality_gate

        facts = ["Some fact to validate"]
        meta = [{}]
        validator = lambda fact, m: True  # Accept everything
        surviving, _, rejected = apply_quality_gate(facts, meta, custom_validator=validator)
        assert rejected == 0

    def test_empty_facts(self):
        from mem0.memory.quality_gate import apply_quality_gate

        surviving, meta, rejected = apply_quality_gate([], [])
        assert surviving == []
        assert rejected == 0


# ---------------------------------------------------------------------------
# apply_post_update_gate
# ---------------------------------------------------------------------------


class TestApplyPostUpdateGate:
    """Tests for apply_post_update_gate."""

    def test_delete_passes_through(self):
        from mem0.memory.quality_gate import apply_post_update_gate

        actions = [{"event": "DELETE", "text": "x"}]
        result = apply_post_update_gate(actions, {})
        assert len(result) == 1
        assert result[0]["event"] == "DELETE"

    def test_none_passes_through(self):
        from mem0.memory.quality_gate import apply_post_update_gate

        actions = [{"event": "NONE", "text": ""}]
        result = apply_post_update_gate(actions, {})
        assert len(result) == 1

    def test_add_short_rejected(self):
        from mem0.memory.quality_gate import apply_post_update_gate

        actions = [{"event": "ADD", "text": "hi"}]
        result = apply_post_update_gate(actions, {}, min_length=20)
        assert len(result) == 0

    def test_update_pattern_rejected(self):
        from mem0.memory.quality_gate import apply_post_update_gate

        actions = [{"event": "UPDATE", "text": "Task #99 done"}]
        rules = [{"pattern": r"^Task #\d+"}]
        result = apply_post_update_gate(actions, {}, rejection_rules=rules)
        assert len(result) == 0

    def test_empty_actions(self):
        from mem0.memory.quality_gate import apply_post_update_gate

        result = apply_post_update_gate([], {})
        assert result == []


# ---------------------------------------------------------------------------
# _normalize_expires_at
# ---------------------------------------------------------------------------


class TestNormalizeExpiresAt:
    """Tests for _normalize_expires_at."""

    def test_none_returns_none(self):
        from mem0.memory.quality_gate import _normalize_expires_at

        assert _normalize_expires_at(None) is None

    def test_iso_string(self):
        from mem0.memory.quality_gate import _normalize_expires_at

        result = _normalize_expires_at("2026-06-15T00:00:00+00:00")
        assert "2026-06-15" in result

    def test_z_suffix(self):
        from mem0.memory.quality_gate import _normalize_expires_at

        result = _normalize_expires_at("2026-06-15T00:00:00Z")
        assert result is not None
        assert "2026-06-15" in result

    def test_unix_int(self):
        from mem0.memory.quality_gate import _normalize_expires_at

        result = _normalize_expires_at(1750000000)
        assert isinstance(result, str)
        assert "T" in result  # ISO format

    def test_unix_float(self):
        from mem0.memory.quality_gate import _normalize_expires_at

        result = _normalize_expires_at(1750000000.5)
        assert isinstance(result, str)

    def test_invalid_string(self, caplog):
        from mem0.memory.quality_gate import _normalize_expires_at

        with caplog.at_level(logging.WARNING):
            result = _normalize_expires_at("not-a-date")
        assert result is None
        assert "unparseable" in caplog.text
