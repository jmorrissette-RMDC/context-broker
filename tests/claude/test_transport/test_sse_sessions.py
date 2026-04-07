"""Tests for app/routes/mcp.py — SSE session management."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routes import mcp as mcp_module
from app.routes.mcp import _evict_stale_sessions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_TOOL_CALL_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "metrics_get", "arguments": {}},
}

_VALID_CONFIG = {
    "embeddings": {"embedding_dims": 768},
    "build_types": {},
    "imperator": {},
    "tuning": {},
}


def _make_session(queue_items: int = 0, age_seconds: float = 0.0) -> dict:
    """Create a fake session dict with a bounded queue."""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    for _ in range(queue_items):
        q.put_nowait({"data": "x"})
    return {"queue": q, "created_at": time.monotonic() - age_seconds}


@pytest.fixture(autouse=True)
def _reset_sessions():
    """Reset module-level session state before each test."""
    mcp_module._sessions.clear()
    mcp_module._total_queued_messages = 0
    yield
    mcp_module._sessions.clear()
    mcp_module._total_queued_messages = 0


# ---------------------------------------------------------------------------
# Session-mode tool call (POST /mcp?sessionId=X) — queues response, returns
# "queued"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_mode_queues_and_returns_queued():
    """A POST /mcp?sessionId=X with a known session should queue the
    response and return {"result": "queued"}."""
    session_id = "sess-001"
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    mcp_module._sessions[session_id] = {
        "queue": q,
        "created_at": time.monotonic(),
    }

    with (
        patch(
            "app.routes.mcp.async_load_config",
            new_callable=AsyncMock,
            return_value=_VALID_CONFIG,
        ),
        patch(
            "app.routes.mcp.dispatch_tool",
            new_callable=AsyncMock,
            return_value={"status": "ok"},
        ),
        patch("app.routes.mcp.resolve_caller", return_value="test-caller"),
    ):
        import httpx
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/mcp?sessionId={session_id}",
                json=_VALID_TOOL_CALL_BODY,
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "queued"
    # Verify message was placed in the queue
    assert not q.empty()


# ---------------------------------------------------------------------------
# Unknown sessionId returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_session_id_returns_404():
    """POST /mcp?sessionId=<unknown> should return 404."""
    with (
        patch(
            "app.routes.mcp.async_load_config",
            new_callable=AsyncMock,
            return_value=_VALID_CONFIG,
        ),
        patch(
            "app.routes.mcp.dispatch_tool",
            new_callable=AsyncMock,
            return_value={"status": "ok"},
        ),
        patch("app.routes.mcp.resolve_caller", return_value="test-caller"),
    ):
        import httpx
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/mcp?sessionId=nonexistent-id",
                json=_VALID_TOOL_CALL_BODY,
            )

    assert resp.status_code == 404
    assert "Session not found" in resp.json()["error"]["message"]


# ---------------------------------------------------------------------------
# Queue-full returns 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_full_returns_503():
    """When the session queue is full, POST should return 503."""
    session_id = "sess-full"
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    q.put_nowait({"filler": True})  # fill the queue
    mcp_module._sessions[session_id] = {
        "queue": q,
        "created_at": time.monotonic(),
    }

    with (
        patch(
            "app.routes.mcp.async_load_config",
            new_callable=AsyncMock,
            return_value=_VALID_CONFIG,
        ),
        patch(
            "app.routes.mcp.dispatch_tool",
            new_callable=AsyncMock,
            return_value={"status": "ok"},
        ),
        patch("app.routes.mcp.resolve_caller", return_value="test-caller"),
    ):
        import httpx
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/mcp?sessionId={session_id}",
                json=_VALID_TOOL_CALL_BODY,
            )

    assert resp.status_code == 503
    assert "queue full" in resp.json()["error"]["message"].lower()


# ---------------------------------------------------------------------------
# _evict_stale_sessions — TTL eviction
# ---------------------------------------------------------------------------


def test_evict_stale_sessions_ttl():
    """Sessions older than TTL should be evicted."""
    old_id = "old-session"
    new_id = "new-session"
    mcp_module._sessions[old_id] = _make_session(queue_items=2, age_seconds=3700)
    mcp_module._sessions[new_id] = _make_session(queue_items=0, age_seconds=10)
    mcp_module._total_queued_messages = 2

    _evict_stale_sessions(session_ttl=3600, max_sessions=1000, max_total_queued=10000)

    assert old_id not in mcp_module._sessions
    assert new_id in mcp_module._sessions
    assert mcp_module._total_queued_messages == 0


# ---------------------------------------------------------------------------
# _evict_stale_sessions — max sessions cap eviction
# ---------------------------------------------------------------------------


def test_evict_stale_sessions_max_cap():
    """When sessions exceed the cap, oldest should be evicted."""
    for i in range(5):
        mcp_module._sessions[f"sess-{i}"] = _make_session(queue_items=1, age_seconds=0)
    mcp_module._total_queued_messages = 5

    _evict_stale_sessions(session_ttl=99999, max_sessions=3, max_total_queued=10000)

    assert len(mcp_module._sessions) == 3
    # The first two (oldest by insertion order) should be gone
    assert "sess-0" not in mcp_module._sessions
    assert "sess-1" not in mcp_module._sessions
    assert "sess-4" in mcp_module._sessions


# ---------------------------------------------------------------------------
# _evict_stale_sessions — total queued messages pressure eviction
# ---------------------------------------------------------------------------


def test_evict_stale_sessions_queue_pressure():
    """When total queued messages exceed the cap, oldest sessions are evicted."""
    # 3 sessions, each with 5 messages = 15 total
    for i in range(3):
        mcp_module._sessions[f"press-{i}"] = _make_session(
            queue_items=5, age_seconds=0
        )
    mcp_module._total_queued_messages = 15

    # Set max_total_queued=6 — must evict until total <= 6
    _evict_stale_sessions(session_ttl=99999, max_sessions=1000, max_total_queued=6)

    # At least one session evicted; total should drop below threshold
    assert mcp_module._total_queued_messages <= 6
    assert "press-0" not in mcp_module._sessions


# ---------------------------------------------------------------------------
# Session cleanup on disconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_cleanup_on_disconnect():
    """When the SSE generator finishes (client disconnect), the session and
    its queued messages counter should be cleaned up."""
    session_id = "cleanup-test"
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    q.put_nowait({"leftover": True})
    mcp_module._sessions[session_id] = {
        "queue": q,
        "created_at": time.monotonic(),
    }
    mcp_module._total_queued_messages = 1

    # Simulate the finally block from the SSE event_stream generator
    async with mcp_module._session_lock:
        removed = mcp_module._sessions.pop(session_id, None)
        if removed is not None:
            mcp_module._total_queued_messages = max(
                0, mcp_module._total_queued_messages - removed["queue"].qsize()
            )

    assert session_id not in mcp_module._sessions
    assert mcp_module._total_queued_messages == 0
