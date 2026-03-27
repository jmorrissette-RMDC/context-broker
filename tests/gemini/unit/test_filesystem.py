import pytest
import os
from unittest.mock import patch, mock_open, MagicMock
from context_broker_te.tools.filesystem import (
    _is_safe_read_path, _is_safe_write_path, file_read, file_list,
    file_search, file_write, read_system_prompt, update_system_prompt
)

@pytest.mark.parametrize("path, is_safe", [
    ("/app/some/file.txt", True),
    ("/config/prompts/test.md", True),
    ("/data/downloads/file.bin", True),
    ("/etc/passwd", False),
    ("/var/log/syslog", False),
    ("relative/path", False)
])
def test_is_safe_read_path(path, is_safe):
    with patch("pathlib.Path.resolve", return_value=MagicMock(startswith=lambda x: path.startswith(x))):
        assert _is_safe_read_path(path) == is_safe

@pytest.mark.parametrize("path, is_safe", [
    ("/data/downloads/file.txt", True),
    ("/data/downloads/nested/file.txt", True),
    ("/app/file.txt", False),
    ("/config/file.txt", False),
])
def test_is_safe_write_path(path, is_safe):
    with patch("pathlib.Path.resolve", return_value=MagicMock(startswith=lambda x: path.startswith(x))):
        assert _is_safe_write_path(path) == is_safe

@pytest.mark.asyncio
async def test_file_read_success():
    with patch("context_broker_te.tools.filesystem._is_safe_read_path", return_value=True), \
         patch("builtins.open", mock_open(read_data="file content")):
        result = await file_read.ainvoke({"path": "/app/test.txt"})
        assert result == "file content"

@pytest.mark.asyncio
async def test_file_read_access_denied():
    with patch("context_broker_te.tools.filesystem._is_safe_read_path", return_value=False):
        result = await file_read.ainvoke({"path": "/etc/passwd"})
        assert "Access denied" in result

@pytest.mark.asyncio
async def test_file_list_success():
    with patch("context_broker_te.tools.filesystem._is_safe_read_path", return_value=True), \
         patch("os.listdir", return_value=["dir1", "file.txt"]), \
         patch("os.path.isdir", side_effect=lambda x: x.endswith("dir1")), \
         patch("os.path.getsize", return_value=123):
        result = await file_list.ainvoke({"path": "/app"})
        assert "Contents of /app (2 entries):" in result
        assert "dir1/" in result
        assert "file.txt (123 bytes)" in result

@pytest.mark.asyncio
async def test_file_search_success():
    mock_walk = [("/app", ("dir",), ("test.py", "other.py"))]
    with patch("context_broker_te.tools.filesystem._is_safe_read_path", return_value=True), \
         patch("os.walk", return_value=mock_walk), \
         patch("builtins.open", mock_open(read_data="line 1\nmatch this\nline 3")):
        result = await file_search.ainvoke({"path": "/app", "pattern": "match"})
        assert "test.py:2: match this" in result
        assert "other.py:2: match this" in result

@pytest.mark.asyncio
async def test_file_write_success():
    with patch("context_broker_te.tools.filesystem._is_safe_write_path", return_value=True), \
         patch("os.makedirs"), \
         patch("builtins.open", mock_open()) as m_open:
        result = await file_write.ainvoke({"path": "newfile.txt", "content": "data"})
        assert "Written 4 chars to /data/downloads/newfile.txt" in result
        m_open.assert_called_once_with("/data/downloads/newfile.txt", "w", encoding="utf-8")

@pytest.mark.asyncio
async def test_read_system_prompt_success():
    with patch("app.config.load_merged_config", return_value={"imperator": {"system_prompt": "my_prompt"}}), \
         patch("builtins.open", mock_open(read_data="prompt content")):
        result = await read_system_prompt.ainvoke({})
        assert result == "prompt content"

@pytest.mark.asyncio
async def test_update_system_prompt_success():
    with patch("app.config.load_merged_config", return_value={"imperator": {"system_prompt": "my_prompt"}}), \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="old content")) as m_open:
        
        result = await update_system_prompt.ainvoke({"content": "This is a new prompt with enough length."})
        assert "System prompt updated" in result
        # Check writes (backup then actual file)
        write_calls = [call for call in m_open.mock_calls if call[0] == "().write"]
        assert len(write_calls) == 2
        assert write_calls[0][1][0] == "old content"
        assert write_calls[1][1][0] == "This is a new prompt with enough length."

@pytest.mark.asyncio
async def test_update_system_prompt_too_short():
    result = await update_system_prompt.ainvoke({"content": "short"})
    assert "too short" in result
