import pytest
import asyncio
import json
import time
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import Request
from fastapi.responses import StreamingResponse

import app.routes.mcp as mcp_module
from app.routes.mcp import mcp_sse_session, mcp_tool_call, _evict_stale_sessions

@pytest.fixture(autouse=True)
def reset_mcp_state():
    mcp_module._sessions.clear()
    mcp_module._total_queued_messages = 0
    yield
    mcp_module._sessions.clear()
    mcp_module._total_queued_messages = 0

def test_evict_stale_sessions():
    now = time.monotonic()
    queue = asyncio.Queue()
    queue.put_nowait({"test": 1})
    queue.put_nowait({"test": 2})
    
    # 1 stale, 1 active, 1 over cap
    mcp_module._sessions["stale"] = {"queue": queue, "created_at": now - 100}
    mcp_module._sessions["active1"] = {"queue": asyncio.Queue(), "created_at": now}
    mcp_module._sessions["active2"] = {"queue": asyncio.Queue(), "created_at": now}
    
    mcp_module._total_queued_messages = 2
    
    # TTL eviction
    _evict_stale_sessions(session_ttl=50, max_sessions=10, max_total_queued=100)
    assert "stale" not in mcp_module._sessions
    assert mcp_module._total_queued_messages == 0
    
    # Cap eviction
    mcp_module._sessions["active3"] = {"queue": asyncio.Queue(), "created_at": now}
    _evict_stale_sessions(session_ttl=50, max_sessions=1, max_total_queued=100)
    assert len(mcp_module._sessions) == 1

@pytest.mark.asyncio
async def test_evict_stale_sessions_total_queue():
    now = time.monotonic()
    queue1 = asyncio.Queue()
    queue1.put_nowait(1)
    queue1.put_nowait(2)
    mcp_module._sessions["s1"] = {"queue": queue1, "created_at": now - 10}
    mcp_module._total_queued_messages = 2
    
    _evict_stale_sessions(session_ttl=50, max_sessions=10, max_total_queued=1)
    assert len(mcp_module._sessions) == 0
    assert mcp_module._total_queued_messages == 0

@pytest.mark.asyncio
async def test_mcp_sse_session():
    request = MagicMock(spec=Request)
    request.client.host = "127.0.0.1"
    request.is_disconnected = AsyncMock(side_effect=[False, False, True])
    
    with patch("app.routes.mcp.async_load_config", return_value={}):
        response = await mcp_sse_session(request)
        assert isinstance(response, StreamingResponse)
        
        # We need to manually iterate the async generator from the response body
        # to test the logic
        async_gen = response.body_iterator
        
        # 1. Session ID message
        first_msg = await async_gen.__anext__()
        assert first_msg.startswith("data: {\"sessionId\":")
        
        session_id = json.loads(first_msg[6:])["sessionId"]
        assert session_id in mcp_module._sessions
        
        # 2. Put message in queue
        mcp_module._total_queued_messages += 1
        mcp_module._sessions[session_id]["queue"].put_nowait({"test": "data"})
        
        # 3. Read queued message
        second_msg = await async_gen.__anext__()
        assert second_msg == "data: {\"test\": \"data\"}\n\n"
        assert mcp_module._total_queued_messages == 0
        
        # 4. Trigger timeout for keepalive
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            third_msg = await async_gen.__anext__()
            assert third_msg == ": keepalive\n\n"
            
        # 5. request.is_disconnected is True, breaks loop
        with pytest.raises(StopAsyncIteration):
            await async_gen.__anext__()
            
        assert session_id not in mcp_module._sessions

@pytest.mark.asyncio
async def test_mcp_tool_call_initialize():
    request = MagicMock(spec=Request)
    request.client.host = "127.0.0.1"
    request.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    
    response = await mcp_tool_call(request)
    body = json.loads(response.body)
    assert body["result"]["serverInfo"]["name"] == "context-broker"

@pytest.mark.asyncio
async def test_mcp_tool_call_tools_list():
    request = MagicMock(spec=Request)
    request.client.host = "127.0.0.1"
    request.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    
    response = await mcp_tool_call(request)
    body = json.loads(response.body)
    assert "tools" in body["result"]
    assert len(body["result"]["tools"]) > 0

@pytest.mark.asyncio
async def test_mcp_tool_call_invalid_json():
    request = MagicMock(spec=Request)
    request.client.host = "127.0.0.1"
    request.json = AsyncMock(side_effect=ValueError("bad json"))
    
    response = await mcp_tool_call(request)
    assert response.status_code == 400

@pytest.mark.asyncio
async def test_mcp_tool_call_invalid_method():
    request = MagicMock(spec=Request)
    request.client.host = "127.0.0.1"
    request.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "method": "unknown_method", "params": {}})
    
    response = await mcp_tool_call(request)
    assert response.status_code == 400

@pytest.mark.asyncio
@patch("app.routes.mcp.async_load_config", return_value={})
@patch("app.routes.mcp.dispatch_tool", return_value={"status": "success"})
@patch("app.routes.mcp.resolve_caller", return_value="test_caller")
async def test_mcp_tool_call_sessionless(mock_resolve, mock_dispatch, mock_config):
    request = MagicMock(spec=Request)
    request.client.host = "127.0.0.1"
    request.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "test_tool", "arguments": {}}})
    request.app.state = MagicMock()

    response = await mcp_tool_call(request)
    body = json.loads(response.body)
    # The actual structure returned by dispatch_tool mock is "success"
    # MCP expects {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "..."}]}}
    assert "jsonrpc" in body
    if "result" in body:
        assert body["result"]["content"][0]["text"] == '{"status": "success"}'
    else:
        # if there was an error
        assert "error" not in body, f"MCP returned error: {body}"
@pytest.mark.asyncio
@patch("app.routes.mcp.async_load_config", return_value={})
@patch("app.routes.mcp.dispatch_tool", return_value={"status": "success"})
async def test_mcp_tool_call_with_session(mock_dispatch, mock_config):
    queue = asyncio.Queue(maxsize=1)
    session_id = "test-session"
    mcp_module._sessions[session_id] = {"queue": queue, "created_at": time.monotonic()}
    
    request = MagicMock(spec=Request)
    request.client.host = "127.0.0.1"
    request.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "test_tool"}})
    
    response = await mcp_tool_call(request, session_id=session_id)
    body = json.loads(response.body)
    assert body["result"] == "queued"
    
    assert queue.qsize() == 1
    assert mcp_module._total_queued_messages == 1

@pytest.mark.asyncio
@patch("app.routes.mcp.async_load_config", return_value={})
@patch("app.routes.mcp.dispatch_tool", return_value={"status": "success"})
async def test_mcp_tool_call_session_full(mock_dispatch, mock_config):
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({"existing": "data"})
    session_id = "test-session"
    mcp_module._sessions[session_id] = {"queue": queue, "created_at": time.monotonic()}
    
    request = MagicMock(spec=Request)
    request.client.host = "127.0.0.1"
    request.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "test_tool"}})
    
    response = await mcp_tool_call(request, session_id=session_id)
    assert response.status_code == 503

@pytest.mark.asyncio
async def test_mcp_tool_call_session_not_found():
    request = MagicMock(spec=Request)
    request.client.host = "127.0.0.1"
    request.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "test_tool"}})
    
    with patch("app.routes.mcp.async_load_config", return_value={}):
        with patch("app.routes.mcp.dispatch_tool", return_value={"status": "success"}):
            response = await mcp_tool_call(request, session_id="missing-session")
            assert response.status_code == 404

@pytest.mark.asyncio
@patch("app.routes.mcp.async_load_config", side_effect=RuntimeError("config error"))
async def test_mcp_tool_call_config_error(mock_config):
    request = MagicMock(spec=Request)
    request.client.host = "127.0.0.1"
    request.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "test_tool"}})
    
    response = await mcp_tool_call(request)
    assert response.status_code == 500

@pytest.mark.asyncio
@patch("app.routes.mcp.async_load_config", return_value={})
@patch("app.routes.mcp.dispatch_tool", side_effect=RuntimeError("tool error"))
async def test_mcp_tool_call_tool_error(mock_dispatch, mock_config):
    request = MagicMock(spec=Request)
    request.client.host = "127.0.0.1"
    request.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "test_tool"}})
    
    response = await mcp_tool_call(request)
    assert response.status_code == 500
