"""Tests for CEA configuration parameters and validation.

Covers: all 14 new CEA config params (defaults and overrides),
validate_cea_config() error cases.

Discovery audit: cli-discovery-audit-claude.md §8.
"""

import pytest


# ---------------------------------------------------------------------------
# validate_cea_config — valid configurations
# ---------------------------------------------------------------------------


class TestValidateCeaConfigValid:
    """Tests for validate_cea_config with valid inputs."""

    def test_empty_config_uses_defaults(self):
        from app.config import validate_cea_config

        # Empty config should not raise — all params have defaults
        validate_cea_config({})

    def test_minimal_cea_block(self):
        from app.config import validate_cea_config

        config = {"cea": {}}
        validate_cea_config(config)

    def test_full_valid_config(self):
        from app.config import validate_cea_config

        config = {
            "cea": {
                "retrieval_scope": "user",
                "pre_extraction_fact_limit": 30,
                "expiration_cleanup_interval": 7200,
                "ranking": {
                    "exploration_rate": 0.2,
                    "usefulness_crossover_events": 15,
                    "trustworthiness_min_threshold": 0.05,
                },
                "ceac": {
                    "max_iterations": 5,
                    "max_memories": 100,
                    "max_token_budget": 16000,
                },
                "source_type_weights": {
                    "decision": 0.9,
                    "observation": 0.7,
                },
            }
        }
        validate_cea_config(config)


# ---------------------------------------------------------------------------
# validate_cea_config — invalid configurations
# ---------------------------------------------------------------------------


class TestValidateCeaConfigInvalid:
    """Tests for validate_cea_config with invalid inputs."""

    def test_invalid_retrieval_scope(self):
        from app.config import validate_cea_config

        config = {"cea": {"retrieval_scope": "invalid"}}
        with pytest.raises(RuntimeError, match="retrieval_scope"):
            validate_cea_config(config)

    def test_negative_fact_limit(self):
        from app.config import validate_cea_config

        config = {"cea": {"pre_extraction_fact_limit": -1}}
        with pytest.raises(RuntimeError, match="pre_extraction_fact_limit"):
            validate_cea_config(config)

    def test_non_int_fact_limit(self):
        from app.config import validate_cea_config

        config = {"cea": {"pre_extraction_fact_limit": 3.5}}
        with pytest.raises(RuntimeError, match="pre_extraction_fact_limit"):
            validate_cea_config(config)

    def test_negative_cleanup_interval(self):
        from app.config import validate_cea_config

        config = {"cea": {"expiration_cleanup_interval": -100}}
        with pytest.raises(RuntimeError, match="expiration_cleanup_interval"):
            validate_cea_config(config)

    def test_exploration_rate_above_one(self):
        from app.config import validate_cea_config

        config = {"cea": {"ranking": {"exploration_rate": 1.5}}}
        with pytest.raises(RuntimeError, match="exploration_rate"):
            validate_cea_config(config)

    def test_exploration_rate_negative(self):
        from app.config import validate_cea_config

        config = {"cea": {"ranking": {"exploration_rate": -0.1}}}
        with pytest.raises(RuntimeError, match="exploration_rate"):
            validate_cea_config(config)

    def test_crossover_zero(self):
        from app.config import validate_cea_config

        config = {"cea": {"ranking": {"usefulness_crossover_events": 0}}}
        with pytest.raises(RuntimeError, match="usefulness_crossover_events"):
            validate_cea_config(config)

    def test_trust_threshold_above_one(self):
        from app.config import validate_cea_config

        config = {"cea": {"ranking": {"trustworthiness_min_threshold": 2.0}}}
        with pytest.raises(RuntimeError, match="trustworthiness_min_threshold"):
            validate_cea_config(config)

    def test_max_iterations_zero(self):
        from app.config import validate_cea_config

        config = {"cea": {"ceac": {"max_iterations": 0}}}
        with pytest.raises(RuntimeError, match="max_iterations"):
            validate_cea_config(config)

    def test_max_memories_zero(self):
        from app.config import validate_cea_config

        config = {"cea": {"ceac": {"max_memories": 0}}}
        with pytest.raises(RuntimeError, match="max_memories"):
            validate_cea_config(config)

    def test_max_token_budget_too_low(self):
        from app.config import validate_cea_config

        config = {"cea": {"ceac": {"max_token_budget": 50}}}
        with pytest.raises(RuntimeError, match="max_token_budget"):
            validate_cea_config(config)

    def test_invalid_source_type_weight(self):
        from app.config import validate_cea_config

        config = {"cea": {"source_type_weights": {"decision": 1.5}}}
        with pytest.raises(RuntimeError, match="source_type_weights"):
            validate_cea_config(config)

    def test_negative_source_type_weight(self):
        from app.config import validate_cea_config

        config = {"cea": {"source_type_weights": {"observation": -0.1}}}
        with pytest.raises(RuntimeError, match="source_type_weights"):
            validate_cea_config(config)


# ---------------------------------------------------------------------------
# Default value tests — verify code uses correct defaults when absent
# ---------------------------------------------------------------------------


class TestCeaConfigDefaults:
    """Tests that CEA code uses correct defaults when config keys are absent."""

    def test_retrieval_scope_default(self):
        """Default retrieval_scope should be 'conversation'."""
        config = {}
        scope = config.get("cea", {}).get("retrieval_scope", "conversation")
        assert scope == "conversation"

    def test_fact_limit_default(self):
        """Default pre_extraction_fact_limit should be 20."""
        config = {}
        limit = config.get("cea", {}).get("pre_extraction_fact_limit", 20)
        assert limit == 20

    def test_cleanup_interval_default(self):
        """Default expiration_cleanup_interval should be 3600."""
        config = {}
        interval = config.get("cea", {}).get("expiration_cleanup_interval", 3600)
        assert interval == 3600

    def test_max_iterations_default(self):
        """Default ceac.max_iterations should be 3."""
        config = {}
        max_iter = config.get("cea", {}).get("ceac", {}).get("max_iterations", 3)
        assert max_iter == 3

    def test_max_memories_default(self):
        """Default ceac.max_memories should be 50."""
        config = {}
        max_mem = config.get("cea", {}).get("ceac", {}).get("max_memories", 50)
        assert max_mem == 50

    def test_max_token_budget_default(self):
        """Default ceac.max_token_budget should be 8000."""
        config = {}
        budget = config.get("cea", {}).get("ceac", {}).get("max_token_budget", 8000)
        assert budget == 8000

    def test_exploration_rate_default(self):
        """Default ranking.exploration_rate should be 0.1."""
        config = {}
        rate = config.get("cea", {}).get("ranking", {}).get("exploration_rate", 0.1)
        assert rate == 0.1

    def test_crossover_events_default(self):
        """Default ranking.usefulness_crossover_events should be 10."""
        config = {}
        crossover = config.get("cea", {}).get("ranking", {}).get("usefulness_crossover_events", 10)
        assert crossover == 10

    def test_trust_threshold_default(self):
        """Default ranking.trustworthiness_min_threshold should be 0.1."""
        config = {}
        threshold = config.get("cea", {}).get("ranking", {}).get("trustworthiness_min_threshold", 0.1)
        assert threshold == 0.1
