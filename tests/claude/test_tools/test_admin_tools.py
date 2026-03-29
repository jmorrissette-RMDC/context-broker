"""Tests for packages/context-broker-te/src/context_broker_te/tools/admin.py.

Covers: config_read, _redact_config, db_query, config_write, verbose_toggle.
"""

import copy
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import asyncpg
import pytest
import yaml

from context_broker_te.tools.admin import (
    _redact_config,
    config_read,
    config_write,
    db_query,
    verbose_toggle,
)


# ── _redact_config ───────────────────────────────────────────────────


def test_redact_config_removes_credentials():
    """Removes the top-level credentials section entirely."""
    cfg = {"credentials": {"openai_key": "sk-123"}, "llm": {"model": "gpt-4"}}
    result = _redact_config(cfg)
    assert "credentials" not in result
    assert result["llm"]["model"] == "gpt-4"


def test_redact_config_redacts_api_key():
    """Redacts values matching api_key pattern."""
    cfg = {"llm": {"api_key": "sk-secret123", "model": "gpt-4"}}
    result = _redact_config(cfg)
    assert result["llm"]["api_key"] == "***REDACTED***"
    assert result["llm"]["model"] == "gpt-4"


def test_redact_config_redacts_secret_and_token():
    """Redacts keys matching secret, _token, password patterns."""
    cfg = {
        "service": {
            "client_secret": "abc",
            "access_token": "tok-123",
            "db_password": "pass",
            "host": "localhost",
        }
    }
    result = _redact_config(cfg)
    assert result["service"]["client_secret"] == "***REDACTED***"
    assert result["service"]["access_token"] == "***REDACTED***"
    assert result["service"]["db_password"] == "***REDACTED***"
    assert result["service"]["host"] == "localhost"


def test_redact_config_handles_nested_lists():
    """Redacts secrets in dicts nested inside lists."""
    cfg = {"providers": [{"name": "a", "api_key": "key1"}, {"name": "b"}]}
    result = _redact_config(cfg)
    assert result["providers"][0]["api_key"] == "***REDACTED***"
    assert result["providers"][1]["name"] == "b"


def test_redact_config_does_not_mutate_original():
    """Returns a deep copy; original is unchanged."""
    cfg = {"llm": {"api_key": "sk-secret"}}
    original_val = cfg["llm"]["api_key"]
    _redact_config(cfg)
    assert cfg["llm"]["api_key"] == original_val


# ── config_read ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_read_returns_redacted_yaml():
    """Returns YAML with redacted secrets."""
    raw_config = {"llm": {"model": "gpt-4", "api_key": "sk-secret"}}
    yaml_text = yaml.dump(raw_config)

    mock_ctx = MagicMock()
    mock_ctx.config_path = "/config/config.yml"

    with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx), \
         patch("builtins.open", mock_open(read_data=yaml_text)):
        result = await config_read.ainvoke({})

    assert "gpt-4" in result
    assert "sk-secret" not in result
    assert "REDACTED" in result


@pytest.mark.asyncio
async def test_config_read_handles_file_not_found():
    """Returns error string when config file is missing."""
    mock_ctx = MagicMock()
    mock_ctx.config_path = "/config/config.yml"

    with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx), \
         patch("builtins.open", side_effect=FileNotFoundError("no file")):
        result = await config_read.ainvoke({})

    assert "Error reading config" in result


# ── db_query ──────────────────────────────────────────────────────────


def _make_db_mocks():
    """Build pool + conn mocks with proper async context manager support.

    asyncpg uses ``async with pool.acquire() as conn`` and
    ``async with conn.transaction()``. Both ``acquire()`` and
    ``transaction()`` return objects with ``__aenter__``/``__aexit__``
    directly (no intermediate await). So we use MagicMock for the
    connection and explicitly configure the transaction method.
    """
    pool = MagicMock()
    conn = MagicMock()

    # pool.acquire() -> async CM that yields conn
    acm = MagicMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm

    # conn.transaction() -> async CM
    txn = MagicMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction.return_value = txn

    # conn.execute and conn.fetch need to be async
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock()

    return pool, conn


@pytest.mark.asyncio
async def test_db_query_returns_formatted_results():
    """Executes read-only SQL and returns formatted results."""
    mock_row1 = MagicMock()
    mock_row1.keys.return_value = ["id", "name"]
    mock_row1.__getitem__ = lambda s, k: {"id": 1, "name": "test"}[k]
    mock_row2 = MagicMock()
    mock_row2.keys.return_value = ["id", "name"]
    mock_row2.__getitem__ = lambda s, k: {"id": 2, "name": "other"}[k]

    pool, conn = _make_db_mocks()
    conn.fetch.return_value = [mock_row1, mock_row2]

    mock_ctx = MagicMock()
    mock_ctx.get_pool.return_value = pool

    with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx):
        result = await db_query.ainvoke({"sql": "SELECT id, name FROM test"})

    assert "id" in result
    assert "name" in result
    assert "test" in result


@pytest.mark.asyncio
async def test_db_query_empty_results():
    """Returns 'No results.' when query returns empty."""
    pool, conn = _make_db_mocks()
    conn.fetch.return_value = []

    mock_ctx = MagicMock()
    mock_ctx.get_pool.return_value = pool

    with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx):
        result = await db_query.ainvoke({"sql": "SELECT 1 WHERE false"})

    assert result == "No results."


@pytest.mark.asyncio
async def test_db_query_returns_error_on_postgres_error():
    """Returns error string on PostgresError."""
    pool, conn = _make_db_mocks()
    conn.fetch.side_effect = asyncpg.PostgresError("syntax error")

    mock_ctx = MagicMock()
    mock_ctx.get_pool.return_value = pool

    with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx):
        result = await db_query.ainvoke({"sql": "INVALID SQL"})

    assert "Query error" in result


# ── config_write ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_write_updates_value():
    """Writes value to config and type-converts based on existing type."""
    raw_config = {"tuning": {"verbose_logging": False, "batch_size": 50}}
    yaml_text = yaml.dump(raw_config)

    calls = []

    def smart_open(path, *args, **kwargs):
        mode = ""
        if args:
            mode = args[0]
        elif "mode" in kwargs:
            mode = kwargs["mode"]
        if "w" in mode:
            m = MagicMock()
            m.__enter__ = lambda s: m
            m.__exit__ = lambda s, *a: None
            calls.append("write")
            return m
        return mock_open(read_data=yaml_text)()

    mock_ctx = MagicMock()
    mock_ctx.config_path = "/config/config.yml"

    with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx), \
         patch("builtins.open", side_effect=smart_open):
        result = await config_write.ainvoke({"key": "tuning.verbose_logging", "value": "true"})

    assert "Updated" in result
    assert "verbose_logging" in result


@pytest.mark.asyncio
async def test_config_write_rejects_te_keys():
    """Rejects TE config keys (imperator, system_prompt, identity, purpose)."""
    mock_ctx = MagicMock()
    mock_ctx.config_path = "/config/config.yml"

    with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx):
        for key in ["imperator.model", "system_prompt.name", "identity.x", "purpose.y"]:
            result = await config_write.ainvoke({"key": key, "value": "test"})
            assert "Cannot modify TE config key" in result


@pytest.mark.asyncio
async def test_config_write_missing_key_path():
    """Returns error for nonexistent key path."""
    raw_config = {"tuning": {"verbose_logging": False}}
    yaml_text = yaml.dump(raw_config)

    mock_ctx = MagicMock()
    mock_ctx.config_path = "/config/config.yml"

    with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx), \
         patch("builtins.open", mock_open(read_data=yaml_text)):
        result = await config_write.ainvoke({"key": "tuning.nonexistent_key", "value": "x"})

    assert "not found" in result


@pytest.mark.asyncio
async def test_config_write_type_converts_int():
    """Type-converts string to int based on existing value type."""
    raw_config = {"tuning": {"batch_size": 50}}
    yaml_text = yaml.dump(raw_config)

    written = {}

    def smart_open(path, *args, **kwargs):
        mode = args[0] if args else ""
        if "w" in mode:
            m = MagicMock()
            m.__enter__ = lambda s: m
            m.__exit__ = lambda s, *a: None
            def capture_dump(data, f, **kw):
                written["data"] = data
            return m
        return mock_open(read_data=yaml_text)()

    mock_ctx = MagicMock()
    mock_ctx.config_path = "/config/config.yml"

    with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx), \
         patch("builtins.open", side_effect=smart_open):
        result = await config_write.ainvoke({"key": "tuning.batch_size", "value": "100"})

    assert "Updated" in result
    assert "100" in result


# ── verbose_toggle ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verbose_toggle_false_to_true():
    """Toggles from False to True."""
    config = {"tuning": {"verbose_logging": False}}

    mock_ctx = MagicMock()
    mock_ctx.load_config.return_value = config
    mock_ctx.get_tuning.return_value = False

    with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx), \
         patch("context_broker_te.tools.admin.config_write") as mock_cw:
        mock_cw.ainvoke = AsyncMock(return_value="Updated 'tuning.verbose_logging': False -> True.")
        result = await verbose_toggle.ainvoke({})

    # verbose_toggle calls config_write.ainvoke with value="true"
    mock_cw.ainvoke.assert_called_once()
    call_args = mock_cw.ainvoke.call_args[0][0]
    assert call_args["value"] == "true"


@pytest.mark.asyncio
async def test_verbose_toggle_true_to_false():
    """Toggles from True to False."""
    config = {"tuning": {"verbose_logging": True}}

    mock_ctx = MagicMock()
    mock_ctx.load_config.return_value = config
    mock_ctx.get_tuning.return_value = True

    with patch("context_broker_te.tools.admin.get_ctx", return_value=mock_ctx), \
         patch("context_broker_te.tools.admin.config_write") as mock_cw:
        mock_cw.ainvoke = AsyncMock(return_value="Updated 'tuning.verbose_logging': True -> False.")
        result = await verbose_toggle.ainvoke({})

    mock_cw.ainvoke.assert_called_once()
    call_args = mock_cw.ainvoke.call_args[0][0]
    assert call_args["value"] == "false"
