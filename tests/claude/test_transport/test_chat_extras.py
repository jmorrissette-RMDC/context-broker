"""Tests for app/routes/chat.py — header isolation, ToolMessage, config errors."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_CONFIG = {
    "embeddings": {"embedding_dims": 768},
    "build_types": {},
    "imperator": {},
    "tuning": {},
}


def _chat_body(messages=None, stream=False, user=None):
    """Build a minimal valid /v1/chat/completions request body."""
    if messages is None:
        messages = [{"role": "user", "content": "hello"}]
    body = {"model": "test-model", "messages": messages, "stream": stream}
    if user is not None:
        body["user"] = user
    return body


def _tool_message_body():
    """Body containing a tool-role message with tool_call_id."""
    return _chat_body(
        messages=[
            {"role": "user", "content": "run the tool"},
            {
                "role": "tool",
                "content": "tool result data",
                "tool_call_id": "call_abc123",
            },
            {"role": "user", "content": "thanks"},
        ]
    )


@pytest.fixture
def _mock_app_state():
    """Patch app state so postgres_available is True and imperator_manager
    returns a known context_window_id."""
    from app.main import app

    original_pg = getattr(app.state, "postgres_available", None)
    original_im = getattr(app.state, "imperator_manager", None)

    mock_manager = AsyncMock()
    mock_manager.get_context_window_id = AsyncMock(return_value="default-cw-id")
    app.state.postgres_available = True
    app.state.imperator_manager = mock_manager

    yield mock_manager

    # Restore
    if original_pg is not None:
        app.state.postgres_available = original_pg
    if original_im is not None:
        app.state.imperator_manager = original_im


# ---------------------------------------------------------------------------
# x-context-window-id header isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_window_id_header_forwarded(_mock_app_state):
    """The x-context-window-id header should be passed into the initial_state
    for the Imperator flow, overriding the default."""
    captured_state = {}

    async def capture_invoke(state):
        captured_state.update(state)
        return {"response_text": "ok", "error": None}

    with (
        patch(
            "app.routes.chat.async_load_config",
            new_callable=AsyncMock,
            return_value=_VALID_CONFIG,
        ),
        patch("app.routes.chat.invoke_with_metrics", side_effect=capture_invoke),
        patch("app.routes.chat.resolve_caller", return_value="tester"),
    ):
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=_chat_body(),
                headers={"x-context-window-id": "cw-custom-123"},
            )

    assert resp.status_code == 200
    assert captured_state["context_window_id"] == "cw-custom-123"


@pytest.mark.asyncio
async def test_context_window_id_body_field(_mock_app_state):
    """context_window_id in the request body should also be accepted."""
    captured_state = {}

    async def capture_invoke(state):
        captured_state.update(state)
        return {"response_text": "ok", "error": None}

    body = _chat_body()
    body["context_window_id"] = "cw-from-body"

    with (
        patch(
            "app.routes.chat.async_load_config",
            new_callable=AsyncMock,
            return_value=_VALID_CONFIG,
        ),
        patch("app.routes.chat.invoke_with_metrics", side_effect=capture_invoke),
        patch("app.routes.chat.resolve_caller", return_value="tester"),
    ):
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post("/v1/chat/completions", json=body)

    assert resp.status_code == 200
    assert captured_state["context_window_id"] == "cw-from-body"


# ---------------------------------------------------------------------------
# x-conversation-id header (legacy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_conversation_id_header(_mock_app_state):
    """x-conversation-id should be accepted as a legacy fallback for
    x-context-window-id."""
    captured_state = {}

    async def capture_invoke(state):
        captured_state.update(state)
        return {"response_text": "ok", "error": None}

    with (
        patch(
            "app.routes.chat.async_load_config",
            new_callable=AsyncMock,
            return_value=_VALID_CONFIG,
        ),
        patch("app.routes.chat.invoke_with_metrics", side_effect=capture_invoke),
        patch("app.routes.chat.resolve_caller", return_value="tester"),
    ):
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=_chat_body(),
                headers={"x-conversation-id": "legacy-conv-id"},
            )

    assert resp.status_code == 200
    assert captured_state["context_window_id"] == "legacy-conv-id"


@pytest.mark.asyncio
async def test_context_window_header_takes_priority_over_legacy(_mock_app_state):
    """x-context-window-id should take priority over x-conversation-id."""
    captured_state = {}

    async def capture_invoke(state):
        captured_state.update(state)
        return {"response_text": "ok", "error": None}

    with (
        patch(
            "app.routes.chat.async_load_config",
            new_callable=AsyncMock,
            return_value=_VALID_CONFIG,
        ),
        patch("app.routes.chat.invoke_with_metrics", side_effect=capture_invoke),
        patch("app.routes.chat.resolve_caller", return_value="tester"),
    ):
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=_chat_body(),
                headers={
                    "x-context-window-id": "primary-cw",
                    "x-conversation-id": "legacy-should-lose",
                },
            )

    assert resp.status_code == 200
    assert captured_state["context_window_id"] == "primary-cw"


# ---------------------------------------------------------------------------
# ToolMessage handling (tool_call_id sliding_window)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_message_sliding_window(_mock_app_state):
    """Messages with role=tool should be converted to LangChain ToolMessage
    with the correct tool_call_id."""
    captured_state = {}

    async def capture_invoke(state):
        captured_state.update(state)
        return {"response_text": "done", "error": None}

    with (
        patch(
            "app.routes.chat.async_load_config",
            new_callable=AsyncMock,
            return_value=_VALID_CONFIG,
        ),
        patch("app.routes.chat.invoke_with_metrics", side_effect=capture_invoke),
        patch("app.routes.chat.resolve_caller", return_value="tester"),
    ):
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=_tool_message_body(),
            )

    assert resp.status_code == 200
    from langchain_core.messages import ToolMessage

    lc_messages = captured_state["messages"]
    tool_msgs = [m for m in lc_messages if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "call_abc123"
    assert tool_msgs[0].content == "tool result data"


@pytest.mark.asyncio
async def test_tool_message_missing_tool_call_id_uses_fallback(_mock_app_state):
    """A tool message without tool_call_id should default to 'unknown'."""
    captured_state = {}

    async def capture_invoke(state):
        captured_state.update(state)
        return {"response_text": "done", "error": None}

    body = _chat_body(
        messages=[
            {"role": "user", "content": "run it"},
            {"role": "tool", "content": "result"},
            {"role": "user", "content": "thanks"},
        ]
    )

    with (
        patch(
            "app.routes.chat.async_load_config",
            new_callable=AsyncMock,
            return_value=_VALID_CONFIG,
        ),
        patch("app.routes.chat.invoke_with_metrics", side_effect=capture_invoke),
        patch("app.routes.chat.resolve_caller", return_value="tester"),
    ):
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post("/v1/chat/completions", json=body)

    assert resp.status_code == 200
    from langchain_core.messages import ToolMessage

    tool_msgs = [
        m for m in captured_state["messages"] if isinstance(m, ToolMessage)
    ]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "unknown"


# ---------------------------------------------------------------------------
# Config load failure returns 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_load_failure_returns_500(_mock_app_state):
    """When async_load_config raises, the endpoint should return 500."""
    with (
        patch(
            "app.routes.chat.async_load_config",
            new_callable=AsyncMock,
            side_effect=RuntimeError("config file missing"),
        ),
    ):
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=_chat_body(),
            )

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["type"] == "internal_error"
    assert "Configuration unavailable" in body["error"]["message"]


@pytest.mark.asyncio
async def test_config_load_oserror_returns_500(_mock_app_state):
    """OSError during config load should also return 500."""
    with (
        patch(
            "app.routes.chat.async_load_config",
            new_callable=AsyncMock,
            side_effect=OSError("permission denied"),
        ),
    ):
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=_chat_body(),
            )

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_no_user_messages_returns_400(_mock_app_state):
    """A request with no user messages should return 400."""
    with (
        patch(
            "app.routes.chat.async_load_config",
            new_callable=AsyncMock,
            return_value=_VALID_CONFIG,
        ),
    ):
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=_chat_body(
                    messages=[{"role": "system", "content": "be helpful"}]
                ),
            )

    assert resp.status_code == 400
    assert "user message" in resp.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_default_context_window_from_imperator(_mock_app_state):
    """When no context_window_id header or body field is provided, the
    imperator_manager.get_context_window_id() default should be used."""
    captured_state = {}

    async def capture_invoke(state):
        captured_state.update(state)
        return {"response_text": "ok", "error": None}

    with (
        patch(
            "app.routes.chat.async_load_config",
            new_callable=AsyncMock,
            return_value=_VALID_CONFIG,
        ),
        patch("app.routes.chat.invoke_with_metrics", side_effect=capture_invoke),
        patch("app.routes.chat.resolve_caller", return_value="tester"),
    ):
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=_chat_body(),
            )

    assert resp.status_code == 200
    assert captured_state["context_window_id"] == "default-cw-id"
