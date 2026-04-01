"""
Unit tests for deadband-based tier budget allocation.

Covers deadband config extraction, tier 1 (live) ceiling calculation,
compaction trigger detection, and boundary conditions.

The old scale_tier_percentages logic (dynamic short/medium/long scaling)
is replaced by a deadband model where:
  - Tier 1 (live) gets the residual budget after tier 2 and tier 3 floors
  - Tier 2 (chunks) has min/max chunk constraints
  - Tier 3 (archival) has a small fixed percentage
  - Compaction triggers when tier 1 exceeds its ceiling
"""

import copy

from context_broker_ae.build_types.tier_scaling import (
    calculate_tier1_ceiling,
    extract_deadband_config,
    should_trigger_compaction,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


STANDARD_BUDGET = 1_000_000  # 1M token window for test calculations


def _base_config() -> dict:
    """Return a tiered-summary build type config with deadband settings."""
    return {
        "tier1_floor_pct": 0.20,
        "tier2_chunk_pct": 0.02,
        "tier3_pct": 0.02,
        "tier2_min_chunks": 3,
        "tier2_max_chunks": 6,
        "tier3_header_pct": 0.0025,
        "effective_utilization": 0.85,
        "max_context_tokens": "auto",
        "fallback_tokens": 8192,
    }


def _enriched_config() -> dict:
    """Return an enriched build type config with deadband settings."""
    return {
        "tier1_floor_pct": 0.15,
        "tier2_chunk_pct": 0.02,
        "tier3_pct": 0.02,
        "tier2_min_chunks": 3,
        "tier2_max_chunks": 6,
        "tier3_header_pct": 0.0025,
        "effective_utilization": 0.85,
        "knowledge_graph_pct": 0.15,
        "semantic_retrieval_pct": 0.15,
        "fallback_tokens": 16000,
    }


# ------------------------------------------------------------------
# Deadband config extraction
# ------------------------------------------------------------------


class TestDeadbandConfigExtraction:
    """Tests for extracting deadband parameters from build type config."""

    def test_extracts_standard_config(self):
        """Extracts all deadband fields from a tiered-summary config."""
        config = _base_config()
        db = extract_deadband_config(config)
        assert db["tier1_floor_pct"] == 0.20
        assert db["tier2_chunk_pct"] == 0.02
        assert db["tier3_pct"] == 0.02
        assert db["tier2_min_chunks"] == 3
        assert db["tier2_max_chunks"] == 6
        assert db["tier3_header_pct"] == 0.0025

    def test_extracts_enriched_config(self):
        """Enriched config has a smaller tier1_floor_pct."""
        config = _enriched_config()
        db = extract_deadband_config(config)
        assert db["tier1_floor_pct"] == 0.15

    def test_missing_optional_fields_get_defaults(self):
        """Missing optional fields are filled with sensible defaults."""
        config = {"tier1_floor_pct": 0.20, "tier2_chunk_pct": 0.02, "tier3_pct": 0.02}
        db = extract_deadband_config(config)
        assert "tier2_min_chunks" in db
        assert "tier2_max_chunks" in db
        assert "tier3_header_pct" in db

    def test_does_not_mutate_input(self):
        """Extraction does not modify the input config dict."""
        config = _base_config()
        original = copy.deepcopy(config)
        extract_deadband_config(config)
        assert config == original


# ------------------------------------------------------------------
# Tier 1 ceiling calculation
# ------------------------------------------------------------------


class TestTier1Ceiling:
    """Tests for tier 1 (live) ceiling in tokens.

    ceiling = (budget * utilization) - (tier2_chunk * max_chunks * budget) - (tier3 * budget)
    Never drops below floor.
    """

    def test_standard_ceiling(self):
        """Standard: ceiling = 1M * 0.85 - 1M * 0.02 * 6 - 1M * 0.02 = 710K."""
        db = extract_deadband_config(_base_config())
        ceiling = calculate_tier1_ceiling(STANDARD_BUDGET, db)
        # 850K - 120K - 20K = 710K
        expected = int(STANDARD_BUDGET * 0.85) - int(STANDARD_BUDGET * 0.02 * 6) - int(STANDARD_BUDGET * 0.02)
        assert ceiling == expected

    def test_enriched_ceiling(self):
        """Enriched: same calculation — RAG layers don't affect ceiling directly."""
        db = extract_deadband_config(_enriched_config())
        ceiling = calculate_tier1_ceiling(STANDARD_BUDGET, db)
        # Same formula: 850K - 120K - 20K = 710K
        expected = int(STANDARD_BUDGET * 0.85) - int(STANDARD_BUDGET * 0.02 * 6) - int(STANDARD_BUDGET * 0.02)
        assert ceiling == expected

    def test_ceiling_always_positive(self):
        """Ceiling should never go negative even with large tier allocations."""
        config = {
            "tier1_floor_pct": 0.20,
            "tier2_chunk_pct": 0.30,
            "tier2_max_chunks": 6,
            "tier3_pct": 0.30,
            "effective_utilization": 0.85,
        }
        db = extract_deadband_config(config)
        ceiling = calculate_tier1_ceiling(STANDARD_BUDGET, db)
        assert ceiling >= 0

    def test_ceiling_exceeds_floor(self):
        """Under standard config, tier 1 ceiling should exceed the floor."""
        db = extract_deadband_config(_base_config())
        ceiling = calculate_tier1_ceiling(STANDARD_BUDGET, db)
        floor = int(STANDARD_BUDGET * db["tier1_floor_pct"])
        assert ceiling > floor


# ------------------------------------------------------------------
# Compaction trigger detection
# ------------------------------------------------------------------


class TestCompactionTrigger:
    """Tests for detecting when tier 1 exceeds its ceiling.

    should_trigger_compaction(tier1_tokens, max_budget, db_config) -> bool
    """

    def test_below_ceiling_no_trigger(self):
        """When tier 1 usage is below ceiling, no compaction needed."""
        db = extract_deadband_config(_base_config())
        tier1_tokens = int(STANDARD_BUDGET * 0.50)  # 50% — well below 71% ceiling
        assert should_trigger_compaction(tier1_tokens, STANDARD_BUDGET, db) is False

    def test_at_ceiling_triggers(self):
        """When tier 1 usage exceeds the ceiling, compaction triggers."""
        db = extract_deadband_config(_base_config())
        ceiling = calculate_tier1_ceiling(STANDARD_BUDGET, db)
        assert should_trigger_compaction(ceiling + 1, STANDARD_BUDGET, db) is True

    def test_above_ceiling_triggers(self):
        """When tier 1 usage greatly exceeds ceiling, compaction triggers."""
        db = extract_deadband_config(_base_config())
        tier1_tokens = int(STANDARD_BUDGET * 0.90)  # 90% — way above ceiling
        assert should_trigger_compaction(tier1_tokens, STANDARD_BUDGET, db) is True

    def test_zero_usage_no_trigger(self):
        """Zero tier 1 usage should not trigger compaction."""
        db = extract_deadband_config(_base_config())
        assert should_trigger_compaction(0, STANDARD_BUDGET, db) is False

    def test_at_floor_no_trigger(self):
        """Tier 1 at exactly the floor should not trigger."""
        db = extract_deadband_config(_base_config())
        tier1_tokens = int(STANDARD_BUDGET * 0.20)  # floor
        assert should_trigger_compaction(tier1_tokens, STANDARD_BUDGET, db) is False


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases and special configs."""

    def test_all_zero_tiers_no_crash(self):
        """Config with all zero percentages does not crash."""
        config = {"tier1_floor_pct": 0, "tier2_chunk_pct": 0, "tier3_pct": 0}
        db = extract_deadband_config(config)
        assert db["tier1_floor_pct"] == 0

    def test_non_tier_keys_preserved_in_extraction(self):
        """Non-deadband keys in the config are not lost."""
        config = _enriched_config()
        db = extract_deadband_config(config)
        # extract_deadband_config returns only deadband fields,
        # but the original config should be unchanged
        assert config["knowledge_graph_pct"] == 0.15
        assert config["semantic_retrieval_pct"] == 0.15

    def test_tier2_min_max_ordering(self):
        """tier2_min_chunks should be <= tier2_max_chunks."""
        config = _base_config()
        db = extract_deadband_config(config)
        assert db["tier2_min_chunks"] <= db["tier2_max_chunks"]


# ------------------------------------------------------------------
# Immutability
# ------------------------------------------------------------------


class TestImmutability:
    """Verify deadband functions do not mutate their input."""

    def test_extract_does_not_mutate(self):
        """extract_deadband_config does not modify its input."""
        config = _base_config()
        original = copy.deepcopy(config)
        extract_deadband_config(config)
        assert config == original

    def test_ceiling_does_not_mutate(self):
        """calculate_tier1_ceiling does not modify its input."""
        db = extract_deadband_config(_base_config())
        original = copy.deepcopy(db)
        calculate_tier1_ceiling(STANDARD_BUDGET, db)
        assert db == original

    def test_trigger_does_not_mutate(self):
        """should_trigger_compaction does not modify its input."""
        db = extract_deadband_config(_base_config())
        original = copy.deepcopy(db)
        should_trigger_compaction(500_000, STANDARD_BUDGET, db)
        assert db == original
