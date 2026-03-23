"""
Unit tests for configuration management.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from app.config import get_api_key, get_build_type_config, load_config


def test_load_config_success(tmp_path):
    """load_config reads and returns valid YAML configuration."""
    config_data = {
        "log_level": "INFO",
        "llm": {"model": "gpt-4o-mini"},
        "build_types": {"standard-tiered": {"tier3_pct": 0.72}},
    }
    config_file = tmp_path / "config.yml"
    config_file.write_text(yaml.dump(config_data))

    with patch("app.config.CONFIG_PATH", str(config_file)):
        config = load_config()

    assert config["log_level"] == "INFO"
    assert config["llm"]["model"] == "gpt-4o-mini"


def test_load_config_file_not_found():
    """load_config raises RuntimeError when config file is missing."""
    with patch("app.config.CONFIG_PATH", "/nonexistent/config.yml"):
        with pytest.raises(RuntimeError, match="Configuration file not found"):
            load_config()


def test_load_config_invalid_yaml(tmp_path):
    """load_config raises RuntimeError for invalid YAML."""
    config_file = tmp_path / "config.yml"
    config_file.write_text("{ invalid yaml: [")

    with patch("app.config.CONFIG_PATH", str(config_file)):
        with pytest.raises(RuntimeError, match="Failed to parse"):
            load_config()


def test_get_api_key_from_env():
    """get_api_key reads the API key from the named environment variable."""
    with patch.dict(os.environ, {"MY_API_KEY": "sk-test-key-123"}):
        key = get_api_key({"api_key_env": "MY_API_KEY"})
    assert key == "sk-test-key-123"


def test_get_api_key_missing_env_var():
    """get_api_key returns empty string when env var is not set."""
    with patch.dict(os.environ, {}, clear=True):
        key = get_api_key({"api_key_env": "NONEXISTENT_KEY"})
    assert key == ""


def test_get_api_key_no_env_var_configured():
    """get_api_key returns empty string when api_key_env is not set."""
    key = get_api_key({})
    assert key == ""


def test_get_build_type_config_success(sample_config):
    """get_build_type_config returns the correct build type configuration."""
    bt_config = get_build_type_config(sample_config, "standard-tiered")
    assert bt_config["tier3_pct"] == 0.72
    assert bt_config["fallback_tokens"] == 8192


def test_get_build_type_config_not_found(sample_config):
    """get_build_type_config raises ValueError for unknown build types."""
    with pytest.raises(ValueError, match="Build type 'nonexistent' not found"):
        get_build_type_config(sample_config, "nonexistent")
