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


def _base_config() -> dict:
    """Return a tiered-summary build type config with deadband settings."""
    return {
        "tier1_floor_pct": 0.20,
        "tier2_chunk_pct": 0.02,
        "tier3_pct": 0.02,
        "tier2_min_chunks": 3,
        "tier2_max_chunks": 6,
        "tier3_header_pct": 0.0025,
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
    """Tests for tier 1 (live) ceiling = 85% - tier2 - tier3."""

    def test_standard_ceiling(self):
        """Standard config: ceiling = 0.85 - 0.02 - 0.02 = 0.81."""
        config = _base_config()
        ceiling = calculate_tier1_ceiling(config)
        expected = 0.85 - 0.02 - 0.02
        assert abs(ceiling - expected) < 1e-6

    def test_enriched_ceiling(self):
        """Enriched config: ceiling accounts for knowledge/semantic pcts."""
        config = _enriched_config()
        ceiling = calculate_tier1_ceiling(config)
        # 0.85 - 0.02 - 0.02 - 0.15 - 0.15 = 0.51
        expected = 0.85 - 0.02 - 0.02 - 0.15 - 0.15
        assert abs(ceiling - expected) < 1e-6

    def test_ceiling_always_positive(self):
        """Ceiling should never go negative even with large tier allocations."""
        config = {
            "tier1_floor_pct": 0.20,
            "tier2_chunk_pct": 0.30,
            "tier3_pct": 0.30,
            "knowledge_graph_pct": 0.10,
            "semantic_retrieval_pct": 0.10,
        }
        ceiling = calculate_tier1_ceiling(config)
        assert ceiling >= 0

    def test_ceiling_exceeds_floor(self):
        """Under standard config, tier 1 ceiling should exceed the floor."""
        config = _base_config()
        ceiling = calculate_tier1_ceiling(config)
        assert ceiling > config["tier1_floor_pct"]


# ------------------------------------------------------------------
# Compaction trigger detection
# ------------------------------------------------------------------


class TestCompactionTrigger:
    """Tests for detecting when tier 1 exceeds its ceiling."""

    def test_below_ceiling_no_trigger(self):
        """When tier 1 usage is below ceiling, no compaction needed."""
        config = _base_config()
        # tier1 at 50% of budget, ceiling is ~81%
        assert should_trigger_compaction(config, tier1_token_pct=0.50) is False

    def test_at_ceiling_triggers(self):
        """When tier 1 usage equals the ceiling, compaction triggers."""
        config = _base_config()
        ceiling = calculate_tier1_ceiling(config)
        assert should_trigger_compaction(config, tier1_token_pct=ceiling) is True

    def test_above_ceiling_triggers(self):
        """When tier 1 usage exceeds ceiling, compaction triggers."""
        config = _base_config()
        assert should_trigger_compaction(config, tier1_token_pct=0.90) is True

    def test_zero_usage_no_trigger(self):
        """Zero tier 1 usage should not trigger compaction."""
        config = _base_config()
        assert should_trigger_compaction(config, tier1_token_pct=0.0) is False

    def test_at_floor_no_trigger(self):
        """Tier 1 at exactly the floor percentage should not trigger."""
        config = _base_config()
        assert should_trigger_compaction(config, tier1_token_pct=0.20) is False


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
        config = _base_config()
        original = copy.deepcopy(config)
        calculate_tier1_ceiling(config)
        assert config == original

    def test_trigger_does_not_mutate(self):
        """should_trigger_compaction does not modify its input."""
        config = _base_config()
        original = copy.deepcopy(config)
        should_trigger_compaction(config, tier1_token_pct=0.50)
        assert config == original
