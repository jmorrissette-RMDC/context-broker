"""
Unit tests for dynamic tier scaling (app.flows.build_types.tier_scaling).

Covers short/medium/long conversation adjustments, renormalization,
boundary conditions, and input immutability.
"""

import copy


from context_broker_ae.build_types.tier_scaling import (
    _LONG_CONVERSATION,
    _SHORT_CONVERSATION,
    scale_tier_percentages,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _base_config() -> dict:
    """Return a tiered-summary build type config for testing."""
    return {
        "tier1_pct": 0.08,
        "tier2_pct": 0.20,
        "tier3_pct": 0.72,
        "max_context_tokens": "auto",
        "fallback_tokens": 8192,
    }


def _tier_sum(cfg: dict) -> float:
    """Return sum of tier1/2/3 percentages."""
    return cfg["tier1_pct"] + cfg["tier2_pct"] + cfg["tier3_pct"]


# ------------------------------------------------------------------
# Short conversations
# ------------------------------------------------------------------


class TestShortConversation:
    """Tests for conversations with < 50 messages."""

    def test_zero_messages_boosts_tier3(self):
        """At 0 messages, tier3 gets maximum boost from tier1/tier2."""
        config = _base_config()
        result = scale_tier_percentages(config, 0)
        assert result["tier3_pct"] > config["tier3_pct"]
        assert result["tier1_pct"] < config["tier1_pct"]
        assert result["tier2_pct"] < config["tier2_pct"]

    def test_short_conversation_boosts_tier3(self):
        """At 25 messages (midpoint of short range), tier3 is boosted."""
        config = _base_config()
        result = scale_tier_percentages(config, 25)
        assert result["tier3_pct"] > config["tier3_pct"]

    def test_near_threshold_minimal_shift(self):
        """At 49 messages (just under threshold), the shift is minimal."""
        config = _base_config()
        result = scale_tier_percentages(config, 49)
        # Very close to original values
        assert abs(result["tier3_pct"] - config["tier3_pct"]) < 0.01

    def test_at_threshold_no_shift(self):
        """At exactly 50 messages, no adjustment is made (medium range)."""
        config = _base_config()
        result = scale_tier_percentages(config, _SHORT_CONVERSATION)
        assert result["tier1_pct"] == config["tier1_pct"]
        assert result["tier2_pct"] == config["tier2_pct"]
        assert result["tier3_pct"] == config["tier3_pct"]


# ------------------------------------------------------------------
# Medium conversations
# ------------------------------------------------------------------


class TestMediumConversation:
    """Tests for conversations with 50-500 messages (no adjustment)."""

    def test_medium_no_change(self):
        """A medium-length conversation (200 messages) uses config as-is."""
        config = _base_config()
        result = scale_tier_percentages(config, 200)
        assert result["tier1_pct"] == config["tier1_pct"]
        assert result["tier2_pct"] == config["tier2_pct"]
        assert result["tier3_pct"] == config["tier3_pct"]

    def test_at_long_threshold_no_change(self):
        """At exactly 500 messages, no adjustment is made."""
        config = _base_config()
        result = scale_tier_percentages(config, _LONG_CONVERSATION)
        assert result["tier1_pct"] == config["tier1_pct"]
        assert result["tier2_pct"] == config["tier2_pct"]
        assert result["tier3_pct"] == config["tier3_pct"]


# ------------------------------------------------------------------
# Long conversations
# ------------------------------------------------------------------


class TestLongConversation:
    """Tests for conversations with > 500 messages."""

    def test_long_conversation_shifts_to_tier1_tier2(self):
        """At 1000 messages, budget shifts from tier3 to tier1/tier2."""
        config = _base_config()
        result = scale_tier_percentages(config, 1000)
        assert result["tier1_pct"] > config["tier1_pct"]
        assert result["tier2_pct"] > config["tier2_pct"]
        assert result["tier3_pct"] < config["tier3_pct"]

    def test_very_long_conversation_max_shift(self):
        """At 2000+ messages, the shift reaches its maximum (30% of tier3)."""
        config = _base_config()
        result_2000 = scale_tier_percentages(config, 2000)
        result_5000 = scale_tier_percentages(config, 5000)
        # Both should be at the same max shift (capped at 2000)
        assert abs(result_2000["tier3_pct"] - result_5000["tier3_pct"]) < 1e-6

    def test_tier1_gets_40_percent_of_shift(self):
        """Tier1 receives 40% of the tier3-to-upper shift."""
        config = _base_config()
        result = scale_tier_percentages(config, 2000)
        # At max shift: shift_amount = tier3_pct * 0.3 = 0.72 * 0.3 = 0.216
        # tier1 gain = 0.216 * 0.4 = 0.0864
        # tier2 gain = 0.216 * 0.6 = 0.1296
        tier1_gain = result["tier1_pct"] - config["tier1_pct"]
        tier2_gain = result["tier2_pct"] - config["tier2_pct"]
        assert abs(tier1_gain / (tier1_gain + tier2_gain) - 0.4) < 0.01


# ------------------------------------------------------------------
# Renormalization
# ------------------------------------------------------------------


class TestRenormalization:
    """Tests for tier percentage renormalization."""

    def test_short_conversation_sums_to_original(self):
        """After short-conversation adjustment, tiers sum to original total."""
        config = _base_config()
        original_sum = _tier_sum(config)
        result = scale_tier_percentages(config, 10)
        assert abs(_tier_sum(result) - original_sum) < 1e-6

    def test_long_conversation_sums_to_original(self):
        """After long-conversation adjustment, tiers sum to original total."""
        config = _base_config()
        original_sum = _tier_sum(config)
        result = scale_tier_percentages(config, 1500)
        assert abs(_tier_sum(result) - original_sum) < 1e-6

    def test_zero_messages_sums_to_original(self):
        """Even at 0 messages (max shift), tiers sum to original total."""
        config = _base_config()
        original_sum = _tier_sum(config)
        result = scale_tier_percentages(config, 0)
        assert abs(_tier_sum(result) - original_sum) < 1e-6

    def test_no_negative_values(self):
        """No tier percentage should ever go negative."""
        config = _base_config()
        for count in (0, 1, 10, 49, 50, 100, 500, 501, 1000, 2000, 10000):
            result = scale_tier_percentages(config, count)
            assert result["tier1_pct"] >= 0, f"tier1 negative at {count} msgs"
            assert result["tier2_pct"] >= 0, f"tier2 negative at {count} msgs"
            assert result["tier3_pct"] >= 0, f"tier3 negative at {count} msgs"


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases and special configs."""

    def test_all_zero_tiers_no_crash(self):
        """Config with all zero tier percentages does not crash."""
        config = {"tier1_pct": 0, "tier2_pct": 0, "tier3_pct": 0}
        result = scale_tier_percentages(config, 10)
        assert result["tier1_pct"] == 0
        assert result["tier2_pct"] == 0
        assert result["tier3_pct"] == 0

    def test_single_tier_nonzero(self):
        """Config with only tier3 nonzero is handled."""
        config = {"tier1_pct": 0, "tier2_pct": 0, "tier3_pct": 1.0}
        result = scale_tier_percentages(config, 10)
        # Short conversation wants to shift from tier1+tier2 to tier3,
        # but tier1+tier2 = 0, so no shift possible.
        # tier3 stays at 1.0
        assert abs(result["tier3_pct"] - 1.0) < 1e-6

    def test_missing_tier_keys_medium_range_sliding_window(self):
        """In the medium range (no adjustment), missing tier keys are harmless."""
        config = {"tier1_pct": 0, "tier2_pct": 0, "tier3_pct": 0.5}
        result = scale_tier_percentages(config, 100)
        assert result["tier3_pct"] == 0.5

    def test_non_tier_keys_preserved(self):
        """Non-tier keys (knowledge_graph_pct etc.) are preserved unchanged."""
        config = {
            "tier1_pct": 0.05,
            "tier2_pct": 0.15,
            "tier3_pct": 0.50,
            "knowledge_graph_pct": 0.15,
            "semantic_retrieval_pct": 0.15,
            "fallback_tokens": 16000,
        }
        result = scale_tier_percentages(config, 10)
        assert result["knowledge_graph_pct"] == 0.15
        assert result["semantic_retrieval_pct"] == 0.15
        assert result["fallback_tokens"] == 16000


# ------------------------------------------------------------------
# Immutability
# ------------------------------------------------------------------


class TestImmutability:
    """Verify scale_tier_percentages does not mutate its input."""

    def test_does_not_mutate_input(self):
        """The original build_type_config dict must remain unchanged."""
        config = _base_config()
        original = copy.deepcopy(config)
        scale_tier_percentages(config, 10)
        assert config == original

    def test_returns_new_dict(self):
        """The returned dict is a different object from the input."""
        config = _base_config()
        result = scale_tier_percentages(config, 10)
        assert result is not config
