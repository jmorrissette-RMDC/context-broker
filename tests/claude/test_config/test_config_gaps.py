"""Tests for app/config.py covering gap areas.

Covers: load_merged_config, async_load_config, verbose_log, verbose_log_auto,
get_chat_model, get_embeddings_model, credentials hot-reload, _load_credentials.
"""

import asyncio
import hashlib
import logging
import os
from unittest.mock import MagicMock, mock_open, patch

import pytest
import yaml

import app.config as config_mod


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_config_caches():
    """Reset all module-level caches between tests."""
    config_mod._config_cache = None
    config_mod._config_mtime = 0.0
    config_mod._config_content_hash = ""
    config_mod._te_config_cache = None
    config_mod._te_config_mtime = 0.0
    config_mod._te_config_content_hash = ""
    config_mod._llm_cache.clear()
    config_mod._embeddings_cache.clear()
    config_mod._credentials_cache = {}
    config_mod._credentials_mtime = 0.0
    yield
    # Teardown: clear again to avoid cross-test contamination
    config_mod._config_cache = None
    config_mod._config_mtime = 0.0
    config_mod._config_content_hash = ""
    config_mod._te_config_cache = None
    config_mod._te_config_mtime = 0.0
    config_mod._te_config_content_hash = ""
    config_mod._llm_cache.clear()
    config_mod._embeddings_cache.clear()
    config_mod._credentials_cache = {}
    config_mod._credentials_mtime = 0.0


@pytest.fixture
def ae_yaml():
    return {"database": {"pool_max_size": 10}, "llm": {"model": "gpt-4o-mini"}}


@pytest.fixture
def te_yaml():
    return {"imperator": {"model": "gpt-4o"}, "tuning": {"verbose_logging": True}}


# ── load_merged_config ────────────────────────────────────────────


class TestLoadMergedConfig:
    """Tests for load_merged_config()."""

    def test_merges_ae_and_te_with_te_override(self, ae_yaml, te_yaml):
        """TE keys override AE keys in merged config."""
        with (
            patch.object(config_mod, "load_config", return_value=ae_yaml),
            patch.object(config_mod, "load_te_config", return_value=te_yaml),
        ):
            merged = config_mod.load_merged_config()

        # AE key preserved
        assert merged["database"] == {"pool_max_size": 10}
        # TE key present
        assert merged["imperator"] == {"model": "gpt-4o"}
        assert merged["tuning"] == {"verbose_logging": True}

    def test_te_key_overrides_ae_key(self):
        """When both AE and TE define the same key, TE wins."""
        ae = {"shared_key": "ae_value", "ae_only": 1}
        te = {"shared_key": "te_value", "te_only": 2}
        with (
            patch.object(config_mod, "load_config", return_value=ae),
            patch.object(config_mod, "load_te_config", return_value=te),
        ):
            merged = config_mod.load_merged_config()

        assert merged["shared_key"] == "te_value"
        assert merged["ae_only"] == 1
        assert merged["te_only"] == 2

    def test_graceful_fallback_when_te_not_found(self, ae_yaml):
        """Returns AE config when TE config file is missing."""
        with (
            patch.object(config_mod, "load_config", return_value=ae_yaml),
            patch.object(
                config_mod,
                "load_te_config",
                side_effect=RuntimeError("TE config not found"),
            ),
        ):
            result = config_mod.load_merged_config()

        assert result is ae_yaml


# ── async_load_config ─────────────────────────────────────────────


class TestAsyncLoadConfig:
    """Tests for async_load_config()."""

    @pytest.mark.asyncio
    async def test_returns_merged_config(self, ae_yaml, te_yaml):
        """Returns merged AE + TE config via async path."""
        stat_result = MagicMock()
        stat_result.st_mtime = 100.0

        with (
            patch.object(config_mod, "load_config", return_value=ae_yaml),
            patch("os.stat", return_value=stat_result),
            patch.object(
                config_mod,
                "_read_and_parse_te_config",
                return_value=(te_yaml, yaml.dump(te_yaml)),
            ),
        ):
            result = await config_mod.async_load_config()

        assert result["database"] == {"pool_max_size": 10}
        assert result["imperator"] == {"model": "gpt-4o"}

    @pytest.mark.asyncio
    async def test_mtime_fast_path(self, ae_yaml, te_yaml):
        """Uses cached TE config when mtime has not changed."""
        config_mod._te_config_cache = te_yaml
        config_mod._te_config_mtime = 100.0

        stat_result = MagicMock()
        stat_result.st_mtime = 100.0

        with (
            patch.object(config_mod, "load_config", return_value=ae_yaml),
            patch("os.stat", return_value=stat_result),
        ):
            result = await config_mod.async_load_config()

        assert result["imperator"] == {"model": "gpt-4o"}

    @pytest.mark.asyncio
    async def test_falls_back_when_te_file_missing(self, ae_yaml):
        """Returns AE-only config when TE file does not exist."""
        with (
            patch.object(config_mod, "load_config", return_value=ae_yaml),
            patch("os.stat", side_effect=FileNotFoundError),
        ):
            result = await config_mod.async_load_config()

        assert result is ae_yaml


# ── verbose_log / verbose_log_auto ────────────────────────────────


class TestVerboseLog:
    """Tests for verbose_log() and verbose_log_auto()."""

    def test_verbose_log_logs_when_enabled(self):
        """Logs the message when verbose_logging is True."""
        cfg = {"tuning": {"verbose_logging": True}}
        logger = MagicMock()
        config_mod.verbose_log(cfg, logger, "hello %s", "world")
        logger.info.assert_called_once_with("hello %s", "world")

    def test_verbose_log_noop_when_disabled(self):
        """Does not log when verbose_logging is False."""
        cfg = {"tuning": {"verbose_logging": False}}
        logger = MagicMock()
        config_mod.verbose_log(cfg, logger, "should not appear")
        logger.info.assert_not_called()

    def test_verbose_log_auto_reads_config_each_call(self):
        """verbose_log_auto reads config on each invocation."""
        cfg = {"tuning": {"verbose_logging": True}}
        logger = MagicMock()
        with patch.object(config_mod, "load_config", return_value=cfg) as mock_load:
            config_mod.verbose_log_auto(logger, "msg1")
            config_mod.verbose_log_auto(logger, "msg2")

        assert mock_load.call_count == 2
        assert logger.info.call_count == 2

    def test_verbose_log_auto_noop_on_config_failure(self):
        """verbose_log_auto is a no-op when config cannot be loaded."""
        logger = MagicMock()
        with patch.object(
            config_mod, "load_config", side_effect=RuntimeError("gone")
        ):
            config_mod.verbose_log_auto(logger, "should not crash")

        logger.info.assert_not_called()


# ── get_chat_model ────────────────────────────────────────────────


class TestGetChatModel:
    """Tests for get_chat_model()."""

    def test_returns_cached_instance(self):
        """Returns the same ChatOpenAI instance on repeated calls."""
        cfg = {
            "imperator": {
                "base_url": "http://localhost:11434/v1",
                "model": "gpt-4o",
                "api_key_env": "",
            },
            "tuning": {},
        }
        mock_chat = MagicMock()
        with patch("langchain_openai.ChatOpenAI", return_value=mock_chat) as cls:
            result1 = config_mod.get_chat_model(cfg, "imperator")
            result2 = config_mod.get_chat_model(cfg, "imperator")

        assert result1 is result2
        cls.assert_called_once()

    def test_cache_eviction_at_max_entries(self):
        """Evicts the oldest entry when cache exceeds _MAX_CACHE_ENTRIES."""
        mock_chat = MagicMock()
        with patch("langchain_openai.ChatOpenAI", return_value=mock_chat):
            # Fill cache to max
            for i in range(config_mod._MAX_CACHE_ENTRIES):
                cfg = {
                    "imperator": {
                        "base_url": f"http://host-{i}:11434/v1",
                        "model": f"model-{i}",
                        "api_key_env": "",
                    },
                    "tuning": {},
                }
                config_mod.get_chat_model(cfg, "imperator")

            assert len(config_mod._llm_cache) == config_mod._MAX_CACHE_ENTRIES
            first_key = next(iter(config_mod._llm_cache))

            # One more triggers eviction
            cfg_new = {
                "imperator": {
                    "base_url": "http://new-host:11434/v1",
                    "model": "new-model",
                    "api_key_env": "",
                },
                "tuning": {},
            }
            config_mod.get_chat_model(cfg_new, "imperator")

            assert len(config_mod._llm_cache) == config_mod._MAX_CACHE_ENTRIES
            assert first_key not in config_mod._llm_cache


# ── get_embeddings_model ──────────────────────────────────────────


class TestGetEmbeddingsModel:
    """Tests for get_embeddings_model()."""

    def test_returns_cached_instance(self):
        """Returns the same OpenAIEmbeddings instance on repeated calls."""
        cfg = {
            "embeddings": {
                "base_url": "http://localhost:11434/v1",
                "model": "nomic-embed-text",
                "api_key_env": "",
            }
        }
        mock_emb = MagicMock()
        with patch("langchain_openai.OpenAIEmbeddings", return_value=mock_emb) as cls:
            result1 = config_mod.get_embeddings_model(cfg)
            result2 = config_mod.get_embeddings_model(cfg)

        assert result1 is result2
        cls.assert_called_once()

    def test_passes_dimensions_when_configured(self):
        """Passes dimensions kwarg when embedding_dims is set (MRL)."""
        cfg = {
            "embeddings": {
                "base_url": "http://localhost:11434/v1",
                "model": "text-embedding-3-small",
                "api_key_env": "",
                "embedding_dims": 512,
            }
        }
        mock_emb = MagicMock()
        with patch("langchain_openai.OpenAIEmbeddings", return_value=mock_emb) as cls:
            config_mod.get_embeddings_model(cfg)

        call_kwargs = cls.call_args[1]
        assert call_kwargs["dimensions"] == 512

    def test_no_dimensions_when_not_configured(self):
        """Does not pass dimensions kwarg when embedding_dims is absent."""
        cfg = {
            "embeddings": {
                "base_url": "http://localhost:11434/v1",
                "model": "nomic-embed-text",
                "api_key_env": "",
            }
        }
        mock_emb = MagicMock()
        with patch("langchain_openai.OpenAIEmbeddings", return_value=mock_emb) as cls:
            config_mod.get_embeddings_model(cfg)

        call_kwargs = cls.call_args[1]
        assert "dimensions" not in call_kwargs

    def test_cache_eviction_at_max_entries(self):
        """Evicts the oldest entry when cache exceeds _MAX_CACHE_ENTRIES."""
        mock_emb = MagicMock()
        with patch("langchain_openai.OpenAIEmbeddings", return_value=mock_emb):
            for i in range(config_mod._MAX_CACHE_ENTRIES):
                cfg = {
                    "embeddings": {
                        "base_url": f"http://host-{i}:11434/v1",
                        "model": f"model-{i}",
                        "api_key_env": "",
                    }
                }
                config_mod.get_embeddings_model(cfg)

            assert len(config_mod._embeddings_cache) == config_mod._MAX_CACHE_ENTRIES
            first_key = next(iter(config_mod._embeddings_cache))

            cfg_new = {
                "embeddings": {
                    "base_url": "http://new-host:11434/v1",
                    "model": "new-model",
                    "api_key_env": "",
                }
            }
            config_mod.get_embeddings_model(cfg_new)

            assert len(config_mod._embeddings_cache) == config_mod._MAX_CACHE_ENTRIES
            assert first_key not in config_mod._embeddings_cache


# ── Credentials hot-reload ────────────────────────────────────────


class TestCredentialsHotReload:
    """Tests for _load_credentials() and credentials hot-reload."""

    def test_parses_key_value_pairs(self, tmp_path):
        """Parses key=value lines from credentials file."""
        cred_file = tmp_path / ".env"
        cred_file.write_text("API_KEY=sk-12345\nSECRET=my_secret\n")

        stat_result = MagicMock()
        stat_result.st_mtime = 200.0

        with (
            patch.object(config_mod, "CREDENTIALS_PATH", str(cred_file)),
            patch("os.stat", return_value=stat_result),
        ):
            creds = config_mod._load_credentials()

        assert creds["API_KEY"] == "sk-12345"
        assert creds["SECRET"] == "my_secret"

    def test_skips_comments_and_blank_lines(self, tmp_path):
        """Skips comment lines and blank lines."""
        cred_file = tmp_path / ".env"
        cred_file.write_text("# This is a comment\n\nKEY=value\n  \n# Another\n")

        stat_result = MagicMock()
        stat_result.st_mtime = 300.0

        with (
            patch.object(config_mod, "CREDENTIALS_PATH", str(cred_file)),
            patch("os.stat", return_value=stat_result),
        ):
            creds = config_mod._load_credentials()

        assert len(creds) == 1
        assert creds["KEY"] == "value"

    def test_returns_stale_cache_on_read_error(self):
        """Returns stale cache when file cannot be read."""
        config_mod._credentials_cache = {"OLD_KEY": "old_value"}

        stat_result = MagicMock()
        stat_result.st_mtime = 400.0

        with (
            patch("os.stat", return_value=stat_result),
            patch("builtins.open", side_effect=OSError("disk failure")),
        ):
            creds = config_mod._load_credentials()

        assert creds == {"OLD_KEY": "old_value"}

    def test_rereads_when_mtime_changes(self, tmp_path):
        """Re-reads file when mtime changes (hot-reload)."""
        cred_file = tmp_path / ".env"
        cred_file.write_text("KEY=v1\n")

        stat1 = MagicMock()
        stat1.st_mtime = 500.0

        with (
            patch.object(config_mod, "CREDENTIALS_PATH", str(cred_file)),
            patch("os.stat", return_value=stat1),
        ):
            creds1 = config_mod._load_credentials()
            assert creds1["KEY"] == "v1"

        # Update file and change mtime
        cred_file.write_text("KEY=v2\n")
        stat2 = MagicMock()
        stat2.st_mtime = 600.0

        with (
            patch.object(config_mod, "CREDENTIALS_PATH", str(cred_file)),
            patch("os.stat", return_value=stat2),
        ):
            creds2 = config_mod._load_credentials()
            assert creds2["KEY"] == "v2"

    def test_returns_empty_dict_when_file_missing(self):
        """Returns empty dict when credentials file does not exist."""
        with patch("os.stat", side_effect=FileNotFoundError):
            creds = config_mod._load_credentials()

        assert creds == {}
