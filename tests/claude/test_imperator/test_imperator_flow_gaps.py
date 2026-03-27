"""Tests for imperator_flow.py gap coverage.

Covers: system prompt loading, max iterations fallback, empty response retry,
message truncation, should_continue() routing logic.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from context_broker_te.imperator_flow import (
    ImperatorState,
    agent_node,
    max_iterations_fallback,
    should_continue,
)


@pytest.fixture
def base_config():
    return {
        "imperator": {
            "system_prompt": "imperator_identity",
            "build_type": "standard-tiered",
            "max_context_tokens": 4096,
        },
        "tuning": {
            "imperator_max_react_messages": 40,
            "imperator_max_iterations": 5,
        },
    }


def _make_state(messages=None, config=None, iteration_count=0, error=None, cw_id=None):
    """Build an ImperatorState dict for testing."""
    return {
        "messages": messages or [],
        "context_window_id": cw_id,
        "config": config or {},
        "response_text": None,
        "error": error,
        "iteration_count": iteration_count,
    }


# ── should_continue() ────────────────────────────────────────────────


def test_should_continue_routes_to_tool_node(base_config):
    """Routes to tool_node when last message has tool_calls."""
    ai_msg = AIMessage(content="", tool_calls=[{"name": "test", "args": {}, "id": "1"}])
    state = _make_state(messages=[ai_msg], config=base_config, iteration_count=0)
    assert should_continue(state) == "tool_node"


def test_should_continue_routes_to_store_no_tools(base_config):
    """Routes to store_user_message when no tool_calls."""
    ai_msg = AIMessage(content="Final answer")
    state = _make_state(messages=[ai_msg], config=base_config)
    assert should_continue(state) == "store_user_message"


def test_should_continue_routes_on_error(base_config):
    """Routes to store_user_message when error is set."""
    state = _make_state(config=base_config, error="some error")
    assert should_continue(state) == "store_user_message"


def test_should_continue_routes_on_empty_messages(base_config):
    """Routes to store_user_message when messages list is empty."""
    state = _make_state(messages=[], config=base_config)
    assert should_continue(state) == "store_user_message"


def test_should_continue_max_iterations_fallback(base_config):
    """Routes to max_iterations_fallback when iteration count hits limit."""
    ai_msg = AIMessage(content="", tool_calls=[{"name": "test", "args": {}, "id": "1"}])
    state = _make_state(
        messages=[ai_msg], config=base_config, iteration_count=5
    )
    assert should_continue(state) == "max_iterations_fallback"


def test_should_continue_below_max_iterations(base_config):
    """Routes to tool_node when below max iterations."""
    ai_msg = AIMessage(content="", tool_calls=[{"name": "test", "args": {}, "id": "1"}])
    state = _make_state(
        messages=[ai_msg], config=base_config, iteration_count=4
    )
    assert should_continue(state) == "tool_node"


# ── max_iterations_fallback ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_iterations_fallback_injects_text():
    """Injects fallback text when max iterations reached."""
    state = _make_state()
    result = await max_iterations_fallback(state)

    assert "messages" in result
    assert len(result["messages"]) == 1
    msg = result["messages"][0]
    assert isinstance(msg, AIMessage)
    assert "unable to complete" in msg.content.lower()
    assert "smaller parts" in msg.content.lower()


# ── agent_node: system prompt loading ────────────────────────────────


@pytest.mark.asyncio
async def test_agent_node_loads_system_prompt(base_config):
    """First call loads system prompt via async_load_prompt."""
    mock_llm = AsyncMock()
    ai_response = AIMessage(content="Hello!")
    mock_llm.ainvoke.return_value = ai_response

    state = _make_state(
        messages=[HumanMessage(content="Hi")],
        config=base_config,
    )

    with patch("context_broker_te.imperator_flow._prebound_llm", mock_llm), \
         patch("context_broker_te.imperator_flow.async_load_prompt", return_value="You are the Imperator.") as mock_prompt:
        result = await agent_node(state)

    mock_prompt.assert_called_once_with("imperator_identity")
    # Result should include SystemMessage + AIMessage
    assert any(isinstance(m, SystemMessage) for m in result["messages"])


@pytest.mark.asyncio
async def test_agent_node_prompt_load_failure(base_config):
    """Returns error when system prompt fails to load."""
    state = _make_state(
        messages=[HumanMessage(content="Hi")],
        config=base_config,
    )

    with patch("context_broker_te.imperator_flow._prebound_llm", AsyncMock()), \
         patch("context_broker_te.imperator_flow.async_load_prompt", side_effect=RuntimeError("file not found")):
        result = await agent_node(state)

    assert result.get("error")
    assert "Prompt loading failed" in result["error"]


# ── agent_node: empty response retry ─────────────────────────────────


@pytest.mark.asyncio
async def test_agent_node_retries_empty_response(base_config):
    """Retries on empty content response before accepting."""
    empty_response = AIMessage(content="")
    good_response = AIMessage(content="Real answer")
    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = [empty_response, good_response]

    state = _make_state(
        messages=[SystemMessage(content="sys"), HumanMessage(content="Hi")],
        config=base_config,
    )

    with patch("context_broker_te.imperator_flow._prebound_llm", mock_llm):
        result = await agent_node(state)

    # Should have retried once and returned the good response
    assert mock_llm.ainvoke.call_count == 2
    ai_msgs = [m for m in result["messages"] if isinstance(m, AIMessage)]
    assert ai_msgs[-1].content == "Real answer"


# ── agent_node: message truncation ────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_node_truncates_messages(base_config):
    """Truncates older messages when exceeding max_react_messages."""
    base_config["tuning"]["imperator_max_react_messages"] = 5

    # Build a message list that exceeds the limit:
    # system + 10 human messages = 11 total (> 5)
    messages = [SystemMessage(content="sys")]
    for i in range(10):
        messages.append(HumanMessage(content=f"msg {i}"))

    ai_response = AIMessage(content="answer")
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = ai_response

    state = _make_state(messages=messages, config=base_config)

    with patch("context_broker_te.imperator_flow._prebound_llm", mock_llm):
        result = await agent_node(state)

    # Verify the LLM was called with truncated messages
    call_args = mock_llm.ainvoke.call_args[0][0]
    # First message should still be the system message
    assert isinstance(call_args[0], SystemMessage)
    # Total should be <= max_react_messages
    assert len(call_args) <= 5


@pytest.mark.asyncio
async def test_agent_node_truncation_skips_tool_messages(base_config):
    """Truncation boundary skips ToolMessage to avoid orphaned tool results."""
    base_config["tuning"]["imperator_max_react_messages"] = 4

    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="a"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "1"}]),
        ToolMessage(content="result", tool_call_id="1"),
        HumanMessage(content="b"),
        HumanMessage(content="c"),
    ]

    ai_response = AIMessage(content="ok")
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = ai_response

    state = _make_state(messages=messages, config=base_config)

    with patch("context_broker_te.imperator_flow._prebound_llm", mock_llm):
        result = await agent_node(state)

    # The cut should skip past the ToolMessage
    call_args = mock_llm.ainvoke.call_args[0][0]
    assert isinstance(call_args[0], SystemMessage)
    # Should not start with a ToolMessage after system
    assert not isinstance(call_args[1], ToolMessage)
