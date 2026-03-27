"""Tests for app/imperator/state_manager.py (ImperatorStateManager).

Covers: initialize(), _read_state_file(), _write_state_file(),
get_conversation_id() with PG-43 recovery, get_context_window_id()
backward compatibility, _conversation_exists(), _create_imperator_conversation().
"""

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from app.imperator.state_manager import ImperatorStateManager


@pytest.fixture
def sample_config():
    return {"imperator": {"model": "test-model"}}


@pytest.fixture
def manager(sample_config):
    return ImperatorStateManager(sample_config)


@pytest.fixture
def mock_pool():
    pool = AsyncMock()
    with patch("app.imperator.state_manager.get_pg_pool", return_value=pool):
        yield pool


# ── _read_state_file ──────────────────────────────────────────────────


def test_read_state_file_missing(manager):
    """Returns None when state file does not exist."""
    with patch("app.imperator.state_manager.IMPERATOR_STATE_FILE") as mock_path:
        mock_path.exists.return_value = False
        result = manager._read_state_file()
    assert result is None


def test_read_state_file_valid(manager):
    """Returns UUID when state file contains valid JSON."""
    conv_id = uuid.uuid4()
    data = json.dumps({"conversation_id": str(conv_id)})
    with patch("app.imperator.state_manager.IMPERATOR_STATE_FILE") as mock_path:
        mock_path.exists.return_value = True
        with patch("builtins.open", mock_open(read_data=data)):
            result = manager._read_state_file()
    assert result == conv_id


def test_read_state_file_corrupt_json(manager):
    """Returns None and handles corrupt JSON gracefully."""
    with patch("app.imperator.state_manager.IMPERATOR_STATE_FILE") as mock_path:
        mock_path.exists.return_value = True
        with patch("builtins.open", mock_open(read_data="NOT JSON {")):
            result = manager._read_state_file()
    assert result is None


def test_read_state_file_missing_key(manager):
    """Returns None when conversation_id key is absent."""
    data = json.dumps({"other_key": "value"})
    with patch("app.imperator.state_manager.IMPERATOR_STATE_FILE") as mock_path:
        mock_path.exists.return_value = True
        with patch("builtins.open", mock_open(read_data=data)):
            result = manager._read_state_file()
    assert result is None


# ── _write_state_file ─────────────────────────────────────────────────


def test_write_state_file_creates_parent_dirs(manager):
    """Creates parent directories before writing."""
    conv_id = uuid.uuid4()
    m_open = mock_open()
    with patch("app.imperator.state_manager.IMPERATOR_STATE_FILE") as mock_path:
        mock_parent = MagicMock()
        mock_path.parent = mock_parent
        with patch("builtins.open", m_open):
            manager._write_state_file(conv_id)

    mock_parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)
    m_open.assert_called_once()


def test_write_state_file_handles_oserror(manager):
    """Logs error and does not raise on OSError."""
    conv_id = uuid.uuid4()
    with patch("app.imperator.state_manager.IMPERATOR_STATE_FILE") as mock_path:
        mock_path.parent.mkdir.side_effect = OSError("disk full")
        with patch("app.imperator.state_manager._log") as mock_log:
            # Should not raise
            manager._write_state_file(conv_id)
        mock_log.error.assert_called_once()


# ── _conversation_exists ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_conversation_exists_true(manager, mock_pool):
    """Returns True when DB returns a row."""
    mock_pool.fetchrow.return_value = {"id": uuid.uuid4()}
    result = await manager._conversation_exists(uuid.uuid4())
    assert result is True


@pytest.mark.asyncio
async def test_conversation_exists_false(manager, mock_pool):
    """Returns False when DB returns None."""
    mock_pool.fetchrow.return_value = None
    result = await manager._conversation_exists(uuid.uuid4())
    assert result is False


# ── _create_imperator_conversation ────────────────────────────────────


@pytest.mark.asyncio
async def test_create_imperator_conversation(manager, mock_pool):
    """Inserts with title, flow_id, and user_id."""
    mock_pool.execute = AsyncMock()
    new_id = await manager._create_imperator_conversation()

    assert isinstance(new_id, uuid.UUID)
    mock_pool.execute.assert_called_once()
    call_args = mock_pool.execute.call_args
    sql = call_args[0][0]
    assert "INSERT INTO conversations" in sql
    # Positional args: id, title, flow_id, user_id
    assert call_args[0][2] == "Imperator \u2014 System Conversation"
    assert call_args[0][3] == "imperator"
    assert call_args[0][4] == "system"


# ── initialize() ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_resumes_existing(manager, mock_pool):
    """Reads existing state file and verifies conversation exists in DB."""
    conv_id = uuid.uuid4()
    with patch.object(manager, "_read_state_file", return_value=conv_id):
        mock_pool.fetchrow.return_value = {"id": conv_id}
        await manager.initialize()

    assert manager._conversation_id == conv_id


@pytest.mark.asyncio
async def test_initialize_creates_new_when_file_missing(manager, mock_pool):
    """Creates new conversation when state file is missing."""
    new_id = uuid.uuid4()
    with patch.object(manager, "_read_state_file", return_value=None), \
         patch.object(manager, "_create_imperator_conversation", return_value=new_id) as mock_create, \
         patch.object(manager, "_write_state_file") as mock_write:
        await manager.initialize()

    assert manager._conversation_id == new_id
    mock_create.assert_called_once()
    mock_write.assert_called_once_with(new_id)


@pytest.mark.asyncio
async def test_initialize_creates_new_when_id_not_in_db(manager, mock_pool):
    """Creates new conversation when saved ID no longer exists in DB."""
    old_id = uuid.uuid4()
    new_id = uuid.uuid4()
    with patch.object(manager, "_read_state_file", return_value=old_id), \
         patch.object(manager, "_create_imperator_conversation", return_value=new_id) as mock_create, \
         patch.object(manager, "_write_state_file") as mock_write:
        mock_pool.fetchrow.return_value = None  # old conv gone
        await manager.initialize()

    assert manager._conversation_id == new_id
    mock_create.assert_called_once()
    mock_write.assert_called_once_with(new_id)


# ── get_conversation_id() ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_conversation_id_verifies_exists(manager, mock_pool):
    """Verifies conversation still exists (PG-43)."""
    conv_id = uuid.uuid4()
    manager._conversation_id = conv_id
    mock_pool.fetchrow.return_value = {"id": conv_id}

    result = await manager.get_conversation_id()
    assert result == conv_id


@pytest.mark.asyncio
async def test_get_conversation_id_auto_recreates(manager, mock_pool):
    """Auto-recreates conversation on runtime deletion."""
    old_id = uuid.uuid4()
    new_id = uuid.uuid4()
    manager._conversation_id = old_id

    mock_pool.fetchrow.return_value = None  # deleted
    with patch.object(manager, "_create_imperator_conversation", return_value=new_id), \
         patch.object(manager, "_write_state_file") as mock_write:
        result = await manager.get_conversation_id()

    assert result == new_id
    assert manager._conversation_id == new_id
    mock_write.assert_called_once_with(new_id)


@pytest.mark.asyncio
async def test_get_conversation_id_returns_none_if_uninitialized(manager):
    """Returns None if never initialized."""
    result = await manager.get_conversation_id()
    assert result is None


# ── get_context_window_id() ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_context_window_id_delegates(manager, mock_pool):
    """Backward compat: delegates to get_conversation_id."""
    conv_id = uuid.uuid4()
    manager._conversation_id = conv_id
    mock_pool.fetchrow.return_value = {"id": conv_id}

    result = await manager.get_context_window_id()
    assert result == conv_id
