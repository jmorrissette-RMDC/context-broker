import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

from context_broker_te.tools.system import run_command, calculate, _is_command_allowed, get_tools

def test_is_command_allowed():
    assert _is_command_allowed("docker ps") is True
    assert _is_command_allowed("docker inspect 1234") is True
    assert _is_command_allowed("rm -rf /") is False
    assert _is_command_allowed("sudo reboot") is False

@pytest.mark.asyncio
async def test_run_command_not_allowed():
    res = await run_command.ainvoke({"command": "rm -rf /"})
    assert "Command not allowed" in res

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_shell")
async def test_run_command_success(mock_create_subprocess):
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"output", b"")
    mock_create_subprocess.return_value = mock_proc
    
    res = await run_command.ainvoke({"command": "docker ps"})
    assert res == "output"

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_shell")
async def test_run_command_with_stderr(mock_create_subprocess):
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"out", b"err")
    mock_create_subprocess.return_value = mock_proc
    
    res = await run_command.ainvoke({"command": "docker ps"})
    assert "out" in res
    assert "--- stderr ---" in res
    assert "err" in res

@pytest.mark.asyncio
@patch("asyncio.wait_for")
@patch("asyncio.create_subprocess_shell")
async def test_run_command_timeout(mock_create_subprocess, mock_wait_for):
    mock_wait_for.side_effect = asyncio.TimeoutError()
    mock_create_subprocess.return_value = AsyncMock()
    
    res = await run_command.ainvoke({"command": "docker ps"})
    assert "timed out" in res

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_shell", side_effect=OSError("test error"))
async def test_run_command_error(mock_create_subprocess):
    res = await run_command.ainvoke({"command": "docker ps"})
    assert "Command error: test error" in res

@pytest.mark.asyncio
async def test_calculate_success():
    assert await calculate.ainvoke({"expression": "1 + 1"}) == "2"
    assert await calculate.ainvoke({"expression": "sqrt(16)"}) == "4.0"
    assert await calculate.ainvoke({"expression": "max(1, 5, 3)"}) == "5"

@pytest.mark.asyncio
async def test_calculate_unsafe():
    assert "unsafe keywords" in await calculate.ainvoke({"expression": "import os"})
    assert "unsafe keywords" in await calculate.ainvoke({"expression": "__builtins__"})

@pytest.mark.asyncio
async def test_calculate_errors():
    assert "Calculation error" in await calculate.ainvoke({"expression": "1 / 0"})
    assert "Calculation error" in await calculate.ainvoke({"expression": "invalid_syntax("})
    assert "Calculation error" in await calculate.ainvoke({"expression": "not_defined_func()"})

def test_get_tools():
    tools = get_tools()
    assert len(tools) == 2
    assert run_command in tools
    assert calculate in tools
