import os
import asyncio
import tempfile
import yaml
import pytest
from unittest.mock import patch, mock_open, MagicMock

import app.config as config_module
from app.config import (
    load_config, load_te_config, load_merged_config,
    async_load_config, async_load_te_config, get_api_key,
    get_build_type_config, get_tuning, get_log_level,
    verbose_log, verbose_log_auto, get_chat_model,
    get_embeddings_model, load_startup_config
)

@pytest.fixture(autouse=True)
def reset_config_caches():
    config_module._config_cache = None
    config_module._config_mtime = 0.0
    config_module._config_content_hash = ""
    config_module._te_config_cache = None
    config_module._te_config_mtime = 0.0
    config_module._te_config_content_hash = ""
    config_module._credentials_cache = {}
    config_module._credentials_mtime = 0.0
    config_module._llm_cache.clear()
    config_module._embeddings_cache.clear()
    load_startup_config.cache_clear()
    yield

def test_load_config_success(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("log_level: DEBUG")
    
    with patch("app.config.CONFIG_PATH", str(config_file)):
        cfg = load_config()
        assert cfg["log_level"] == "DEBUG"

        # Test cache hit
        cfg2 = load_config()
        assert cfg2 is cfg

def test_load_config_not_found():
    with patch("app.config.CONFIG_PATH", "/non/existent/path.yml"):
        with pytest.raises(RuntimeError, match="Configuration file not found"):
            load_config()

def test_load_config_invalid_yaml(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("invalid: yaml: :")
    
    with patch("app.config.CONFIG_PATH", str(config_file)):
        with pytest.raises(RuntimeError, match="Failed to parse"):
            load_config()

def test_load_config_not_dict(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("- item1\n- item2")

    with patch("app.config.CONFIG_PATH", str(config_file)):
        with pytest.raises(ValueError, match="config.yml must be a YAML mapping at the top level"):
            load_config()
def test_load_te_config_success(tmp_path):
    te_file = tmp_path / "te.yml"
    te_file.write_text("tuning: {llm_timeout_seconds: 60}")
    
    with patch("app.config.TE_CONFIG_PATH", str(te_file)):
        cfg = load_te_config()
        assert cfg["tuning"]["llm_timeout_seconds"] == 60

def test_load_merged_config(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("ae_key: ae_val\nshared: ae_shared")
    te_file = tmp_path / "te.yml"
    te_file.write_text("te_key: te_val\nshared: te_shared")
    
    with patch("app.config.CONFIG_PATH", str(config_file)), \
         patch("app.config.TE_CONFIG_PATH", str(te_file)):
        merged = load_merged_config()
        assert merged["ae_key"] == "ae_val"
        assert merged["te_key"] == "te_val"
        assert merged["shared"] == "te_shared" # TE overrides AE

def test_load_merged_config_fallback(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("ae_key: ae_val")
    
    with patch("app.config.CONFIG_PATH", str(config_file)), \
         patch("app.config.TE_CONFIG_PATH", "/non/existent/te.yml"):
        merged = load_merged_config()
        assert merged["ae_key"] == "ae_val"

@pytest.mark.asyncio
async def test_async_load_config(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("ae_key: ae_val")
    te_file = tmp_path / "te.yml"
    te_file.write_text("te_key: te_val")
    
    with patch("app.config.CONFIG_PATH", str(config_file)), \
         patch("app.config.TE_CONFIG_PATH", str(te_file)):
        merged = await async_load_config()
        assert merged["ae_key"] == "ae_val"
        assert merged["te_key"] == "te_val"
        
        # Test async cache
        merged2 = await async_load_config()
        assert merged2["te_key"] == "te_val"

@pytest.mark.asyncio
async def test_async_load_config_no_te(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("ae_key: ae_val")
    
    with patch("app.config.CONFIG_PATH", str(config_file)), \
         patch("app.config.TE_CONFIG_PATH", "/non/existent/te.yml"):
        merged = await async_load_config()
        assert merged["ae_key"] == "ae_val"

@pytest.mark.asyncio
async def test_async_load_te_config(tmp_path):
    te_file = tmp_path / "te.yml"
    te_file.write_text("te_key: te_val")
    
    with patch("app.config.TE_CONFIG_PATH", str(te_file)):
        cfg = await async_load_te_config()
        assert cfg["te_key"] == "te_val"

        # Test cache
        cfg2 = await async_load_te_config()
        assert cfg2 is cfg

def test_get_api_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MY_API_KEY=secret_val\n# comment\nOTHER_KEY=val2")
    
    with patch("app.config.CREDENTIALS_PATH", str(env_file)):
        assert get_api_key({"api_key_env": "MY_API_KEY"}) == "secret_val"
        # Test fallback to os.environ
        with patch.dict(os.environ, {"ENV_ONLY_KEY": "env_secret"}):
            assert get_api_key({"api_key_env": "ENV_ONLY_KEY"}) == "env_secret"
        
        # Test missing key
        assert get_api_key({"api_key_env": "MISSING"}) == ""
        # Test no env var provided
        assert get_api_key({}) == ""

def test_get_api_key_no_file():
    with patch("app.config.CREDENTIALS_PATH", "/non/existent/.env"):
        with patch.dict(os.environ, {"ENV_ONLY_KEY": "env_secret"}):
            assert get_api_key({"api_key_env": "ENV_ONLY_KEY"}) == "env_secret"

def test_get_build_type_config():
    config = {
        "build_types": {
            "test_bt": {"tier1_pct": 0.5, "tier2_pct": 0.5}
        }
    }
    bt = get_build_type_config(config, "test_bt")
    assert bt["tier1_pct"] == 0.5

    with pytest.raises(ValueError, match="not found"):
        get_build_type_config(config, "nonexistent")

    config_invalid = {
        "build_types": {
            "test_bt": {"tier1_pct": 0.8, "tier2_pct": 0.3} # sum > 1.0
        }
    }
    with pytest.raises(ValueError, match="exceeds 1.0"):
        get_build_type_config(config_invalid, "test_bt")

def test_get_tuning():
    config = {"tuning": {"key1": "val1"}, "workers": {"key2": "val2"}}
    assert get_tuning(config, "key1", "default") == "val1"
    assert get_tuning(config, "key2", "default") == "val2"
    assert get_tuning(config, "missing", "default") == "default"

def test_get_log_level():
    assert get_log_level({"log_level": "debug"}) == "DEBUG"
    assert get_log_level({}) == "INFO"

def test_verbose_log():
    mock_logger = MagicMock()
    verbose_log({"tuning": {"verbose_logging": True}}, mock_logger, "msg %s", "arg")
    mock_logger.info.assert_called_with("msg %s", "arg")
    
    mock_logger.reset_mock()
    verbose_log({"tuning": {"verbose_logging": False}}, mock_logger, "msg %s", "arg")
    mock_logger.info.assert_not_called()

def test_verbose_log_auto(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("tuning:\n  verbose_logging: true")
    mock_logger = MagicMock()
    
    with patch("app.config.CONFIG_PATH", str(config_file)):
        verbose_log_auto(mock_logger, "msg %s", "arg")
        mock_logger.info.assert_called_with("msg %s", "arg")

def test_verbose_log_auto_error():
    mock_logger = MagicMock()
    with patch("app.config.CONFIG_PATH", "/non/existent"):
        verbose_log_auto(mock_logger, "msg") # Should not crash
        mock_logger.info.assert_not_called()

@patch("langchain_openai.ChatOpenAI")
def test_get_chat_model(mock_chat_openai):
    config = {
        "imperator": {"model": "gpt-test", "api_key_env": "TEST_KEY"},
        "llm": {"model": "gpt-legacy"}
    }
    with patch.dict(os.environ, {"TEST_KEY": "secret"}):
        model1 = get_chat_model(config, "imperator")
        model2 = get_chat_model(config, "imperator")
        assert model1 is model2
        mock_chat_openai.assert_called_once()
        
        # Test fallback to 'llm'
        model3 = get_chat_model(config, "other_role")
        assert model3 is model1
        assert mock_chat_openai.call_count == 2

@patch("langchain_openai.OpenAIEmbeddings")
def test_get_embeddings_model(mock_openai_embeddings):
    config = {
        "embeddings": {"model": "text-emb-test", "api_key_env": "TEST_KEY", "embedding_dims": 128}
    }
    with patch.dict(os.environ, {"TEST_KEY": "secret"}):
        model1 = get_embeddings_model(config)
        model2 = get_embeddings_model(config)
        assert model1 is model2
        mock_openai_embeddings.assert_called_once()
        kwargs = mock_openai_embeddings.call_args[1]
        assert kwargs["dimensions"] == 128
        
        # Eviction test
        for i in range(15):
            get_embeddings_model({
                "embeddings": {"model": f"model-{i}"}
            })
        assert len(config_module._embeddings_cache) <= config_module._MAX_CACHE_ENTRIES

def test_cache_clear_on_change(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("key: v1")
    
    with patch("app.config.CONFIG_PATH", str(config_file)):
        cfg = load_config()
        config_module._llm_cache["test"] = "cached"
        
        # Change file content and mtime
        config_file.write_text("key: v2")
        os.utime(config_file, (os.stat(config_file).st_atime, os.stat(config_file).st_mtime + 10))
        
        cfg2 = load_config()
        assert cfg2["key"] == "v2"
        assert "test" not in config_module._llm_cache
