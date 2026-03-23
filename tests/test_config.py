"""
Unit tests for configuration management (app.config).

Covers load_config file reading and caching, get_build_type_config
validation, get_api_key environment lookup, get_tuning defaults,
and cache invalidation on content change.
"""

import os
from unittest.mock import patch

import pytest
import yaml

import app.config as config_module
from app.config import (
    get_api_key,
    get_build_type_config,
    get_tuning,
    load_config,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _reset_config_cache():
    """Reset the module-level config cache so each test starts clean."""
    config_module._config_cache = None
    config_module._config_mtime = 0.0
    config_module._config_content_hash = ""
    config_module._llm_cache.clear()
    config_module._embeddings_cache.clear()


# ------------------------------------------------------------------
# load_config
# ------------------------------------------------------------------


class TestLoadConfig:
    """Tests for load_config reading and caching."""

    def setup_method(self):
        _reset_config_cache()

    def test_reads_yaml(self, tmp_path):
        """load_config reads and returns valid YAML configuration."""
        config_data = {
            "log_level": "INFO",
            "llm": {"model": "gpt-4o-mini"},
            "build_types": {"standard-tiered": {"tier3_pct": 0.72}},
        }
        config_file = tmp_path / "config.yml"
        config_file.write_text(yaml.dump(config_data))

        with patch("app.config.CONFIG_PATH", str(config_file)):
            cfg = load_config()

        assert cfg["log_level"] == "INFO"
        assert cfg["llm"]["model"] == "gpt-4o-mini"

    def test_raises_on_missing_file(self):
        """load_config raises RuntimeError when config file is missing."""
        _reset_config_cache()
        with patch("app.config.CONFIG_PATH", "/nonexistent/config.yml"):
            with pytest.raises(RuntimeError, match="Configuration file not found"):
                load_config()

    def test_raises_on_invalid_yaml(self, tmp_path):
        """load_config raises RuntimeError for malformed YAML."""
        config_file = tmp_path / "config.yml"
        config_file.write_text("{ invalid yaml: [")

        with patch("app.config.CONFIG_PATH", str(config_file)):
            with pytest.raises(RuntimeError, match="Failed to parse"):
                load_config()

    def test_raises_on_non_mapping_yaml(self, tmp_path):
        """load_config raises ValueError if YAML root is not a mapping."""
        config_file = tmp_path / "config.yml"
        config_file.write_text("- just\n- a\n- list\n")

        with patch("app.config.CONFIG_PATH", str(config_file)):
            with pytest.raises(ValueError, match="must be a YAML mapping"):
                load_config()

    def test_mtime_cache_returns_same_object(self, tmp_path):
        """Second call returns cached config without re-reading when mtime unchanged."""
        config_data = {"log_level": "DEBUG"}
        config_file = tmp_path / "config.yml"
        config_file.write_text(yaml.dump(config_data))

        with patch("app.config.CONFIG_PATH", str(config_file)):
            first = load_config()
            second = load_config()

        assert first is second  # Same dict object from cache

    def test_cache_invalidation_on_content_change(self, tmp_path):
        """Config cache is invalidated when file content changes (even if mtime is bumped)."""
        config_file = tmp_path / "config.yml"
        config_file.write_text(yaml.dump({"log_level": "INFO"}))

        with patch("app.config.CONFIG_PATH", str(config_file)):
            first = load_config()
            assert first["log_level"] == "INFO"

            # Simulate file change: write different content
            config_file.write_text(yaml.dump({"log_level": "DEBUG"}))
            # Force cache miss by resetting mtime tracker
            config_module._config_mtime = 0.0

            second = load_config()
            assert second["log_level"] == "DEBUG"
            assert first is not second

    def test_llm_cache_cleared_on_content_change(self, tmp_path):
        """LLM and embeddings caches are cleared when config content hash changes."""
        config_file = tmp_path / "config.yml"
        config_file.write_text(yaml.dump({"version": 1}))

        with patch("app.config.CONFIG_PATH", str(config_file)):
            load_config()
            # Simulate cached LLM client
            config_module._llm_cache["test_key"] = "fake_client"
            config_module._embeddings_cache["test_key"] = "fake_embeddings"

            # Change file content
            config_file.write_text(yaml.dump({"version": 2}))
            config_module._config_mtime = 0.0  # force re-read

            load_config()
            assert "test_key" not in config_module._llm_cache
            assert "test_key" not in config_module._embeddings_cache


# ------------------------------------------------------------------
# get_build_type_config
# ------------------------------------------------------------------


class TestGetBuildTypeConfig:
    """Tests for get_build_type_config validation."""

    def test_returns_config(self, sample_config):
        """Returns the correct build type configuration dict."""
        bt = get_build_type_config(sample_config, "standard-tiered")
        assert bt["tier3_pct"] == 0.72
        assert bt["fallback_tokens"] == 8192

    def test_unknown_type_raises(self, sample_config):
        """Raises ValueError for an unregistered build type name."""
        with pytest.raises(ValueError, match="Build type 'nonexistent' not found"):
            get_build_type_config(sample_config, "nonexistent")

    def test_percentages_sum_valid(self, sample_config):
        """Does not raise when tier percentages sum to <= 1.0."""
        # standard-tiered: 0.08 + 0.20 + 0.72 = 1.0
        bt = get_build_type_config(sample_config, "standard-tiered")
        assert bt is not None

    def test_percentages_sum_exceeds_raises(self):
        """Raises ValueError when tier percentages sum exceeds 1.0."""
        config = {
            "build_types": {
                "bad-type": {
                    "tier1_pct": 0.5,
                    "tier2_pct": 0.4,
                    "tier3_pct": 0.3,
                },
            },
        }
        with pytest.raises(ValueError, match="exceeds 1.0"):
            get_build_type_config(config, "bad-type")

    def test_percentages_include_all_five_keys(self):
        """Validation checks all five percentage keys including knowledge/semantic."""
        config = {
            "build_types": {
                "over-budget": {
                    "tier1_pct": 0.1,
                    "tier2_pct": 0.2,
                    "tier3_pct": 0.3,
                    "knowledge_graph_pct": 0.25,
                    "semantic_retrieval_pct": 0.25,
                },
            },
        }
        with pytest.raises(ValueError, match="exceeds 1.0"):
            get_build_type_config(config, "over-budget")

    def test_knowledge_enriched_valid(self, sample_config):
        """knowledge-enriched type with all five pcts summing to 1.0 passes."""
        bt = get_build_type_config(sample_config, "knowledge-enriched")
        assert bt["knowledge_graph_pct"] == 0.15


# ------------------------------------------------------------------
# get_api_key
# ------------------------------------------------------------------


class TestGetApiKey:
    """Tests for get_api_key environment variable resolution."""

    def test_reads_from_env(self):
        """Reads the API key from the named environment variable."""
        with patch.dict(os.environ, {"MY_API_KEY": "sk-test-123"}):
            key = get_api_key({"api_key_env": "MY_API_KEY"})
        assert key == "sk-test-123"

    def test_missing_env_returns_empty(self):
        """Returns empty string when the env var is not set."""
        with patch.dict(os.environ, {}, clear=True):
            key = get_api_key({"api_key_env": "NONEXISTENT_VAR"})
        assert key == ""

    def test_no_env_var_configured(self):
        """Returns empty string when api_key_env is not in the provider config."""
        key = get_api_key({})
        assert key == ""

    def test_empty_env_var_name(self):
        """Returns empty string when api_key_env is an empty string."""
        key = get_api_key({"api_key_env": ""})
        assert key == ""


# ------------------------------------------------------------------
# get_tuning
# ------------------------------------------------------------------


class TestGetTuning:
    """Tests for get_tuning with defaults."""

    def test_returns_configured_value(self, sample_config):
        """Returns the value from config when present."""
        result = get_tuning(sample_config, "verbose_logging", True)
        assert result is False  # sample_config sets it to False

    def test_returns_default_when_missing(self, sample_config):
        """Returns default when the key is absent from tuning section."""
        result = get_tuning(sample_config, "nonexistent_key", 42)
        assert result == 42

    def test_returns_default_when_no_tuning_section(self):
        """Returns default when config has no tuning section at all."""
        result = get_tuning({}, "some_key", "fallback")
        assert result == "fallback"

    def test_returns_falsy_value_not_default(self, sample_config):
        """Returns the configured falsy value, not the default."""
        result = get_tuning(sample_config, "verbose_logging", True)
        # verbose_logging is False in sample_config, not the default True
        assert result is False
