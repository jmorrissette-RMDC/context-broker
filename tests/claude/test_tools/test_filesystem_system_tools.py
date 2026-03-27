"""Tests for filesystem.py and system.py tools.

Covers: file_list, file_search, read_system_prompt, update_system_prompt,
run_command (allowed/denied), calculate.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from context_broker_te.tools.filesystem import (
    file_list,
    file_search,
    read_system_prompt,
    update_system_prompt,
)
from context_broker_te.tools.system import (
    _is_command_allowed,
    calculate,
    run_command,
)


# ── file_list ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_file_list_returns_entries():
    """Lists directory contents with files and subdirectories."""
    entries = ["config.yml", "prompts", "data.json"]

    def mock_isdir(path):
        return "prompts" in path

    with patch("context_broker_te.tools.filesystem._is_safe_read_path", return_value=True), \
         patch("os.listdir", return_value=entries), \
         patch("os.path.isdir", side_effect=mock_isdir), \
         patch("os.path.getsize", return_value=1024):
        result = await file_list.ainvoke({"path": "/config"})

    assert "Contents of /config" in result
    assert "3 entries" in result
    assert "config.yml" in result
    assert "prompts/" in result


@pytest.mark.asyncio
async def test_file_list_access_denied():
    """Returns access denied for paths outside allowed roots."""
    with patch("context_broker_te.tools.filesystem._is_safe_read_path", return_value=False):
        result = await file_list.ainvoke({"path": "/etc/shadow"})

    assert "Access denied" in result


@pytest.mark.asyncio
async def test_file_list_not_found():
    """Returns not found for missing directories."""
    with patch("context_broker_te.tools.filesystem._is_safe_read_path", return_value=True), \
         patch("os.listdir", side_effect=FileNotFoundError("no dir")):
        result = await file_list.ainvoke({"path": "/app/missing"})

    assert "not found" in result.lower()


# ── file_search ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_file_search_finds_matches():
    """Grep-like search returns matching lines."""
    file_content = "line 1\nfoo bar baz\nline 3\n"

    def mock_walk(path):
        yield ("/app", [], ["test.py"])

    with patch("context_broker_te.tools.filesystem._is_safe_read_path", return_value=True), \
         patch("os.walk", side_effect=mock_walk), \
         patch("builtins.open", mock_open(read_data=file_content)):
        result = await file_search.ainvoke({"path": "/app", "pattern": "foo"})

    assert "test.py" in result
    assert "foo bar baz" in result


@pytest.mark.asyncio
async def test_file_search_no_matches():
    """Returns no matches message when pattern not found."""
    def mock_walk(path):
        yield ("/app", [], ["test.py"])

    with patch("context_broker_te.tools.filesystem._is_safe_read_path", return_value=True), \
         patch("os.walk", side_effect=mock_walk), \
         patch("builtins.open", mock_open(read_data="nothing here")):
        result = await file_search.ainvoke({"path": "/app", "pattern": "xyz123"})

    assert "No matches" in result


# ── read_system_prompt ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_system_prompt_returns_content():
    """Reads prompt file based on config."""
    config = {"imperator": {"system_prompt": "imperator_identity"}}
    prompt_text = "You are the Imperator, a system agent."

    with patch("app.config.load_merged_config", return_value=config), \
         patch("builtins.open", mock_open(read_data=prompt_text)):
        result = await read_system_prompt.ainvoke({})

    assert "Imperator" in result


@pytest.mark.asyncio
async def test_read_system_prompt_not_found():
    """Returns error when prompt file is missing."""
    config = {"imperator": {"system_prompt": "nonexistent"}}

    with patch("app.config.load_merged_config", return_value=config), \
         patch("builtins.open", side_effect=FileNotFoundError("no file")):
        result = await read_system_prompt.ainvoke({})

    assert "not found" in result.lower()


# ── update_system_prompt ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_system_prompt_writes_and_backs_up():
    """Writes new prompt and creates backup of old one."""
    config = {"imperator": {"system_prompt": "imperator_identity"}}
    old_content = "Old prompt content here."
    new_content = "New prompt content that is at least 20 characters."

    written_files = {}

    def smart_open(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if "w" in mode:
            m = MagicMock()
            def capture(data):
                written_files[path] = data
            m.write = capture
            m.__enter__ = lambda s: m
            m.__exit__ = lambda s, *a: None
            return m
        else:
            m = mock_open(read_data=old_content)()
            return m

    with patch("app.config.load_merged_config", return_value=config), \
         patch("builtins.open", side_effect=smart_open), \
         patch("os.path.exists", return_value=True):
        result = await update_system_prompt.ainvoke({"content": new_content})

    assert "updated" in result.lower()
    assert "Backup" in result


@pytest.mark.asyncio
async def test_update_system_prompt_too_short():
    """Rejects prompts shorter than 20 characters."""
    result = await update_system_prompt.ainvoke({"content": "Short"})
    assert "too short" in result.lower()


# ── run_command ───────────────────────────────────────────────────────


def test_is_command_allowed_exact():
    """Exact allowlisted command passes."""
    assert _is_command_allowed("uptime") is True
    assert _is_command_allowed("df -h") is True
    assert _is_command_allowed("hostname") is True


def test_is_command_allowed_prefix():
    """Prefix-allowlisted commands pass."""
    assert _is_command_allowed("docker logs mycontainer") is True
    assert _is_command_allowed("ping -c 4 google.com") is True
    assert _is_command_allowed("curl -s http://example.com") is True


def test_is_command_not_allowed():
    """Commands not in allowlist are rejected."""
    assert _is_command_allowed("rm -rf /") is False
    assert _is_command_allowed("cat /etc/passwd") is False
    assert _is_command_allowed("python -c 'import os'") is False


@pytest.mark.asyncio
async def test_run_command_allowed_returns_output():
    """Allowlisted command returns its stdout."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"up 5 days\n", b""))

    async def mock_create_subprocess(*args, **kwargs):
        return mock_proc

    with patch("asyncio.create_subprocess_shell", side_effect=mock_create_subprocess):
        result = await run_command.ainvoke({"command": "uptime"})

    assert "up 5 days" in result


@pytest.mark.asyncio
async def test_run_command_not_allowed():
    """Command not in allowlist returns error listing permitted commands."""
    result = await run_command.ainvoke({"command": "rm -rf /"})
    assert "not allowed" in result.lower()
    assert "Permitted commands" in result


@pytest.mark.asyncio
async def test_run_command_with_prefix_allowed():
    """Prefix-allowlisted commands execute successfully."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"PING response\n", b""))

    async def mock_create_subprocess(*args, **kwargs):
        return mock_proc

    with patch("asyncio.create_subprocess_shell", side_effect=mock_create_subprocess):
        result = await run_command.ainvoke({"command": "ping -c 1 localhost"})

    assert "PING response" in result


# ── calculate ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_calculate_basic_arithmetic():
    """Evaluates basic math expressions."""
    result = await calculate.ainvoke({"expression": "2 + 3 * 4"})
    assert result == "14"


@pytest.mark.asyncio
async def test_calculate_sqrt():
    """Evaluates sqrt function."""
    result = await calculate.ainvoke({"expression": "sqrt(144)"})
    assert result == "12.0"


@pytest.mark.asyncio
async def test_calculate_rejects_unsafe():
    """Rejects expressions with unsafe keywords."""
    result = await calculate.ainvoke({"expression": "__import__('os')"})
    assert "rejected" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_calculate_division_by_zero():
    """Returns error on division by zero."""
    result = await calculate.ainvoke({"expression": "1/0"})
    assert "error" in result.lower()
