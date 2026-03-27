import pytest
import yaml
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from context_broker_te.tools.admin import (
    _redact_config, config_read, db_query, config_write, verbose_toggle,
    change_inference, migrate_embeddings
)

@pytest.mark.parametrize("config, expected_contains_redacted", [
    ({"credentials": {"key": "val"}, "api_key": "secret123"}, True),
    ({"some_other_key": "val", "nested": {"password": "123"}}, True),
    ({"clean": "val"}, False),
])
def test_redact_config(config, expected_contains_redacted):
    redacted = _redact_config(config)
    if "credentials" in config:
        assert "credentials" not in redacted
    
    def check_redacted(obj):
        found = False
        if isinstance(obj, dict):
            for k, v in obj.items():
                if v == "***REDACTED***":
                    found = True
                elif isinstance(v, (dict, list)):
                    if check_redacted(v):
                        found = True
        elif isinstance(obj, list):
            for item in obj:
                if check_redacted(item):
                    found = True
        return found

    assert check_redacted(redacted) == expected_contains_redacted

@pytest.mark.asyncio
async def test_config_read_success():
    mock_config = {"tuning": {"verbose_logging": True}, "credentials": {"secret": "hide-me"}}
    with patch("builtins.open", mock_open(read_data=yaml.dump(mock_config))), \
         patch("app.config.CONFIG_PATH", "/fake/config.yml"):
        result = await config_read.ainvoke({})
        assert "tuning:" in result
        assert "verbose_logging: true" in result
        assert "credentials" not in result
        assert "***REDACTED***" not in result # credentials section is popped entirely

@pytest.mark.asyncio
async def test_config_read_error():
    with patch("builtins.open", side_effect=FileNotFoundError("Missing file")), \
         patch("app.config.CONFIG_PATH", "/fake/config.yml"):
        result = await config_read.ainvoke({})
        assert "Error reading config" in result

@pytest.mark.asyncio
async def test_db_query_success():
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_tx_ctx = AsyncMock()
    mock_conn.transaction.return_value = mock_tx_ctx
    
    mock_acquire_ctx = AsyncMock()
    mock_acquire_ctx.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value = mock_acquire_ctx
    
    mock_rows = [{"id": 1, "name": "test"}, {"id": 2, "name": "other"}]
    mock_conn.fetch.return_value = mock_rows

    with patch("context_broker_te.tools.admin.get_pg_pool", return_value=mock_pool):
        result = await db_query.ainvoke({"sql": "SELECT * FROM test"})
        assert "id | name" in result
        assert "1 | test" in result
        assert "2 | other" in result

@pytest.mark.asyncio
async def test_db_query_no_results():
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_tx_ctx = AsyncMock()
    mock_conn.transaction.return_value = mock_tx_ctx
    
    mock_acquire_ctx = AsyncMock()
    mock_acquire_ctx.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value = mock_acquire_ctx
    
    mock_conn.fetch.return_value = []

    with patch("context_broker_te.tools.admin.get_pg_pool", return_value=mock_pool):
        result = await db_query.ainvoke({"sql": "SELECT * FROM empty"})
        assert "No results." in result

@pytest.mark.asyncio
async def test_db_query_error():
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_tx_ctx = AsyncMock()
    # Make the transaction context manager raise an exception
    mock_tx_ctx.__aenter__.side_effect = Exception("DB Fail")
    mock_conn.transaction.return_value = mock_tx_ctx
    
    mock_acquire_ctx = AsyncMock()
    mock_acquire_ctx.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value = mock_acquire_ctx
    
    with patch("context_broker_te.tools.admin.get_pg_pool", return_value=mock_pool):
        result = await db_query.ainvoke({"sql": "BAD SQL"})
        assert "Query error" in result

@pytest.mark.asyncio
async def test_config_write_success():
    mock_config = {"tuning": {"verbose_logging": False}}
    m_open = mock_open(read_data=yaml.dump(mock_config))
    with patch("builtins.open", m_open), \
         patch("app.config.CONFIG_PATH", "/fake/config.yml"):
        result = await config_write.ainvoke({"key": "tuning.verbose_logging", "value": "true"})
        assert "Updated 'tuning.verbose_logging': False → True" in result
        # Should have opened for reading then for writing
        assert any(call.args == ('/fake/config.yml', 'w') for call in m_open.call_args_list)

@pytest.mark.asyncio
async def test_config_write_te_restriction():
    result = await config_write.ainvoke({"key": "identity.name", "value": "New Name"})
    assert "Cannot modify TE config key" in result

@pytest.mark.asyncio
async def test_config_write_not_found():
    mock_config = {"tuning": {"verbose_logging": False}}
    with patch("builtins.open", mock_open(read_data=yaml.dump(mock_config))), \
         patch("app.config.CONFIG_PATH", "/fake/config.yml"):
        result = await config_write.ainvoke({"key": "missing.key", "value": "val"})
        assert "Config path 'missing.key' not found" in result

@pytest.mark.asyncio
async def test_verbose_toggle():
    with patch("app.config.load_config", return_value={}), \
         patch("app.config.get_tuning", return_value=False), \
         patch.object(config_write, "ainvoke", new_callable=AsyncMock) as mock_write:
        mock_write.return_value = "Toggled"
        result = await verbose_toggle.ainvoke({})
        assert result == "Toggled"
        mock_write.assert_called_once_with({"key": "tuning.verbose_logging", "value": "true"})

@pytest.mark.asyncio
async def test_change_inference_list():
    mock_catalog = {
        "summarization": {
            "openai": [{"model": "gpt-4o-mini", "base_url": "..."}],
            "google": [{"model": "gemini-1.5-flash", "base_url": "..."}]
        }
    }
    with patch("context_broker_te.tools.admin._load_inference_models", return_value=mock_catalog), \
         patch("app.config.async_load_config", AsyncMock(return_value={})):
        result = await change_inference.ainvoke({"slot": "summarization"})
        assert "Available models for 'summarization'" in result
        assert "openai:" in result
        assert "gpt-4o-mini" in result

@pytest.mark.asyncio
async def test_change_inference_switch_success():
    mock_catalog = {
        "summarization": {
            "openai": [{"model": "gpt-4o-mini", "base_url": "http://api.openai.com", "api_key_env": "OPENAI_API_KEY"}]
        }
    }
    with patch("context_broker_te.tools.admin._load_inference_models", return_value=mock_catalog), \
         patch("context_broker_te.tools.admin._test_endpoint", AsyncMock(return_value=None)), \
         patch("builtins.open", mock_open(read_data=yaml.dump({}))), \
         patch("app.config.CONFIG_PATH", "/fake/config.yml"):
        result = await change_inference.ainvoke({"slot": "summarization", "provider": "openai", "model": "gpt-4o-mini"})
        assert "Switched summarization to gpt-4o-mini (openai)" in result

@pytest.mark.asyncio
async def test_migrate_embeddings_dry_run():
    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=10)
    with patch("context_broker_te.tools.admin.get_pg_pool", return_value=mock_pool), \
         patch("app.config.async_load_config", AsyncMock(return_value={})):
        result = await migrate_embeddings.ainvoke({"new_model": "new-emb", "new_dims": 768, "confirm": False})
        assert "DRY RUN" in result
        assert "Wipe 10 message embeddings" in result

@pytest.mark.asyncio
async def test_migrate_embeddings_confirm():
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()
    with patch("context_broker_te.tools.admin.get_pg_pool", return_value=mock_pool), \
         patch("app.config.async_load_config", AsyncMock(return_value={})), \
         patch("builtins.open", mock_open(read_data=yaml.dump({}))), \
         patch("app.config.CONFIG_PATH", "/fake/config.yml"), \
         patch("context_broker_ae.memory.mem0_client.reset_mem0_client") as mock_reset:
        result = await migrate_embeddings.ainvoke({"new_model": "new-emb", "new_dims": 768, "confirm": True})
        assert "Migration complete" in result
        mock_reset.assert_called_once()
        assert mock_pool.execute.call_count >= 5
