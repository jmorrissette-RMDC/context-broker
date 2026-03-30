"""Tests for imperator_flow.py gap coverage.

Covers: system prompt loading, max iterations fallback, empty response retry,
message truncation, should_continue() routing logic, needs_init() routing.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from context_broker_te.imperator_flow import (
    ImperatorState,
    init_context_node,
    llm_call_node,
    max_iterations_fallback,
    needs_init,
    should_continue,
)


@pytest.fixture
def base_config():
    return {
        "imperator": {
            "system_prompt": "imperator_identity",
            "build_type": "tiered-summary",
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


def _make_mock_ctx():
    """Build a minimal mock TEContext for imperator flow tests."""
    ctx = MagicMock()
    # get_tuning: delegate to the config dict's tuning section
    def _get_tuning(config, key, default=None):
        return config.get("tuning", {}).get(key, default)
    ctx.get_tuning.side_effect = _get_tuning
    return ctx


# ── needs_init() ────────────────────────────────────────────────────


def test_needs_init_no_system_message(base_config):
    """Routes to init_context_node when no SystemMessage in state."""
    state = _make_state(messages=[HumanMessage(content="Hi")], config=base_config)
    assert needs_init(state) == "init_context_node"


def test_needs_init_has_system_message(base_config):
    """Routes to llm_call_node when SystemMessage already present."""
    state = _make_state(
        messages=[SystemMessage(content="sys"), HumanMessage(content="Hi")],
        config=base_config,
    )
    assert needs_init(state) == "llm_call_node"


# ── should_continue() ────────────────────────────────────────────────


def test_should_continue_routes_to_tool_node(base_config):
    """Routes to tool_node when last message has tool_calls."""
    ai_msg = AIMessage(content="", tool_calls=[{"name": "test", "args": {}, "id": "1"}])
    state = _make_state(messages=[ai_msg], config=base_config, iteration_count=0)
    ctx = _make_mock_ctx()
    with patch("context_broker_te.imperator_flow.get_ctx", return_value=ctx):
        assert should_continue(state) == "tool_node"


def test_should_continue_routes_to_store_no_tools(base_config):
    """Routes to store_user_message when no tool_calls."""
    ai_msg = AIMessage(content="Final answer")
    state = _make_state(messages=[ai_msg], config=base_config)
    ctx = _make_mock_ctx()
    with patch("context_broker_te.imperator_flow.get_ctx", return_value=ctx):
        assert should_continue(state) == "store_user_message"


def test_should_continue_routes_on_error(base_config):
    """Routes to store_user_message when error is set."""
    state = _make_state(config=base_config, error="some error")
    ctx = _make_mock_ctx()
    with patch("context_broker_te.imperator_flow.get_ctx", return_value=ctx):
        assert should_continue(state) == "store_user_message"


def test_should_continue_routes_on_empty_messages(base_config):
    """Routes to store_user_message when messages list is empty."""
    state = _make_state(messages=[], config=base_config)
    ctx = _make_mock_ctx()
    with patch("context_broker_te.imperator_flow.get_ctx", return_value=ctx):
        assert should_continue(state) == "store_user_message"


def test_should_continue_max_iterations_fallback(base_config):
    """Routes to max_iterations_fallback when iteration count hits limit."""
    ai_msg = AIMessage(content="", tool_calls=[{"name": "test", "args": {}, "id": "1"}])
    state = _make_state(
        messages=[ai_msg], config=base_config, iteration_count=5
    )
    ctx = _make_mock_ctx()
    with patch("context_broker_te.imperator_flow.get_ctx", return_value=ctx):
        assert should_continue(state) == "max_iterations_fallback"


def test_should_continue_below_max_iterations(base_config):
    """Routes to tool_node when below max iterations."""
    ai_msg = AIMessage(content="", tool_calls=[{"name": "test", "args": {}, "id": "1"}])
    state = _make_state(
        messages=[ai_msg], config=base_config, iteration_count=4
    )
    ctx = _make_mock_ctx()
    with patch("context_broker_te.imperator_flow.get_ctx", return_value=ctx):
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


# ── init_context_node: system prompt loading ────────────────────────


@pytest.mark.asyncio
async def test_init_context_node_loads_system_prompt(base_config):
    """First call loads system prompt via async_load_prompt."""
    state = _make_state(
        messages=[HumanMessage(content="Hi")],
        config=base_config,
    )

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_ctx = MagicMock()
    mock_ctx.async_load_prompt = AsyncMock(return_value="You are the Imperator.")
    mock_ctx.get_pool.return_value = mock_pool
    mock_ctx.get_embeddings_model.return_value = AsyncMock()
    mock_ctx.dispatch_tool = AsyncMock(return_value={"context": []})

    with patch("context_broker_te.imperator_flow.get_ctx", return_value=mock_ctx):
        result = await init_context_node(state)

    mock_ctx.async_load_prompt.assert_called_once_with("imperator_identity")
    # Result should include SystemMessage
    assert any(isinstance(m, SystemMessage) for m in result["messages"])


@pytest.mark.asyncio
async def test_init_context_node_prompt_load_failure(base_config):
    """Returns error when system prompt fails to load."""
    state = _make_state(
        messages=[HumanMessage(content="Hi")],
        config=base_config,
    )

    mock_ctx = MagicMock()
    mock_ctx.async_load_prompt = AsyncMock(side_effect=RuntimeError("file not found"))

    with patch("context_broker_te.imperator_flow.get_ctx", return_value=mock_ctx):
        result = await init_context_node(state)

    assert result.get("error")
    assert "Prompt loading failed" in result["error"]


# ── llm_call_node: empty response retry ─────────────────────────────


@pytest.mark.asyncio
async def test_llm_call_node_retries_empty_response(base_config):
    """Retries on empty content response before accepting."""
    empty_response = AIMessage(content="")
    good_response = AIMessage(content="Real answer")
    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = [empty_response, good_response]

    state = _make_state(
        messages=[SystemMessage(content="sys"), HumanMessage(content="Hi")],
        config=base_config,
    )

    mock_ctx = _make_mock_ctx()

    with patch("context_broker_te.imperator_flow._prebound_llm", mock_llm), \
         patch("context_broker_te.imperator_flow.get_ctx", return_value=mock_ctx):
        result = await llm_call_node(state)

    # Should have retried once and returned the good response
    assert mock_llm.ainvoke.call_count == 2
    ai_msgs = [m for m in result["messages"] if isinstance(m, AIMessage)]
    assert ai_msgs[-1].content == "Real answer"


# ── llm_call_node: message truncation ────────────────────────────────


@pytest.mark.asyncio
async def test_llm_call_node_truncates_messages(base_config):
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

    mock_ctx = _make_mock_ctx()

    with patch("context_broker_te.imperator_flow._prebound_llm", mock_llm), \
         patch("context_broker_te.imperator_flow.get_ctx", return_value=mock_ctx):
        result = await llm_call_node(state)

    # Verify the LLM was called with truncated messages
    call_args = mock_llm.ainvoke.call_args[0][0]
    # First message should still be the system message
    assert isinstance(call_args[0], SystemMessage)
    # Total should be <= max_react_messages
    assert len(call_args) <= 5


@pytest.mark.asyncio
async def test_llm_call_node_truncation_skips_tool_messages(base_config):
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

    mock_ctx = _make_mock_ctx()

    with patch("context_broker_te.imperator_flow._prebound_llm", mock_llm), \
         patch("context_broker_te.imperator_flow.get_ctx", return_value=mock_ctx):
        result = await llm_call_node(state)

    # The cut should skip past the ToolMessage
    call_args = mock_llm.ainvoke.call_args[0][0]
    assert isinstance(call_args[0], SystemMessage)
    # Should not start with a ToolMessage after system
    assert not isinstance(call_args[1], ToolMessage)


# ── RB-32b: System message filter in init_context_node ─────────────


class TestSystemMessageFilter:
    """Tests for the system message skip logic in init_context_node — RB-32b.

    The bug: history loading skipped ALL system-role messages, dropping
    domain context (archival summaries, chunk summaries) that the
    assembler produces. The fix: only skip system messages whose content
    exactly matches the identity prompt.
    """

    @pytest.mark.asyncio
    async def test_identity_prompt_skipped(self, base_config):
        """System message matching identity prompt is not duplicated."""
        identity = "You are the Imperator of the Context Broker."
        domain_context = "[Archival context]\nThe system uses tiered compression."

        mock_ctx = _make_mock_ctx()
        mock_ctx.async_load_prompt = AsyncMock(return_value=identity)
        mock_ctx.get_pool = MagicMock(side_effect=RuntimeError("no pool"))
        mock_ctx.get_embeddings_model = MagicMock(side_effect=RuntimeError("no emb"))
        mock_ctx.dispatch_tool = AsyncMock(return_value={
            "context": [
                {"role": "system", "content": identity},  # duplicate — should skip
                {"role": "system", "content": domain_context},  # domain — should keep
                {"role": "user", "content": "What is MAD?"},
                {"role": "assistant", "content": "MAD is..."},
            ],
        })

        state = _make_state(
            messages=[HumanMessage(content="Tell me about MAD")],
            config=base_config,
            cw_id=str(uuid.uuid4()),
        )

        with patch("context_broker_te.imperator_flow.get_ctx", return_value=mock_ctx):
            result = await init_context_node(state)

        assembled = result["messages"]

        # First message is the identity system prompt (added by init_context_node)
        assert isinstance(assembled[0], SystemMessage)
        assert assembled[0].content == identity

        # The identity should appear exactly ONCE (not duplicated from history)
        identity_count = sum(
            1 for m in assembled
            if isinstance(m, SystemMessage) and m.content.strip() == identity.strip()
        )
        assert identity_count == 1, (
            f"Identity prompt appears {identity_count} times, expected 1"
        )

        # Domain context system message must be preserved
        domain_msgs = [
            m for m in assembled
            if isinstance(m, SystemMessage) and "Archival context" in m.content
        ]
        assert len(domain_msgs) == 1, (
            f"Domain context system message missing from assembled history. "
            f"System messages: {[m.content[:50] for m in assembled if isinstance(m, SystemMessage)]}"
        )

    @pytest.mark.asyncio
    async def test_multiple_domain_system_messages_preserved(self, base_config):
        """All non-identity system messages survive the filter."""
        identity = "You are the Imperator."

        mock_ctx = _make_mock_ctx()
        mock_ctx.async_load_prompt = AsyncMock(return_value=identity)
        mock_ctx.get_pool = MagicMock(side_effect=RuntimeError("no pool"))
        mock_ctx.get_embeddings_model = MagicMock(side_effect=RuntimeError("no emb"))
        mock_ctx.dispatch_tool = AsyncMock(return_value={
            "context": [
                {"role": "system", "content": identity},
                {"role": "system", "content": "[Archival context]\nOld summary."},
                {"role": "system", "content": "[Recent summaries]\nChunk 1."},
                {"role": "system", "content": "[Tool instructions]\nAvailable tools: ..."},
                {"role": "user", "content": "Hello"},
            ],
        })

        state = _make_state(
            messages=[HumanMessage(content="Hello")],
            config=base_config,
            cw_id=str(uuid.uuid4()),
        )

        with patch("context_broker_te.imperator_flow.get_ctx", return_value=mock_ctx):
            result = await init_context_node(state)

        assembled = result["messages"]
        system_msgs = [m for m in assembled if isinstance(m, SystemMessage)]

        # 1 identity + 3 domain = 4 system messages total
        assert len(system_msgs) == 4, (
            f"Expected 4 system messages (1 identity + 3 domain), got {len(system_msgs)}: "
            f"{[m.content[:40] for m in system_msgs]}"
        )


# ── RB-30: Tool-call history preservation ──────────────────────────


class TestToolCallHistoryPreservation:
    """RB-30: History loading must preserve tool_calls on AIMessage and
    create proper ToolMessage with tool_call_id.

    The bug: tool_calls were lost during history loading (AIMessage created
    without them), and ToolMessage was coerced to HumanMessage. This broke
    the ReAct tool-call sequence required by LangGraph.
    """

    @pytest.mark.asyncio
    async def test_tool_calls_preserved_on_ai_message(self, base_config):
        """AIMessage from history retains tool_calls list."""
        identity = "You are the Imperator."
        tool_calls = [{"name": "search_messages", "args": {"query": "test"}, "id": "call_123"}]

        mock_ctx = _make_mock_ctx()
        mock_ctx.async_load_prompt = AsyncMock(return_value=identity)
        mock_ctx.get_pool = MagicMock(side_effect=RuntimeError("no pool"))
        mock_ctx.get_embeddings_model = MagicMock(side_effect=RuntimeError("no emb"))
        mock_ctx.dispatch_tool = AsyncMock(return_value={
            "context": [
                {"role": "user", "content": "Search for test data"},
                {"role": "assistant", "content": "", "tool_calls": tool_calls},
                {"role": "tool", "content": "Found 3 results", "tool_call_id": "call_123"},
                {"role": "assistant", "content": "I found 3 results."},
            ],
        })

        state = _make_state(
            messages=[HumanMessage(content="New question")],
            config=base_config,
            cw_id=str(uuid.uuid4()),
        )

        with patch("context_broker_te.imperator_flow.get_ctx", return_value=mock_ctx):
            result = await init_context_node(state)

        assembled = result["messages"]
        ai_msgs = [m for m in assembled if isinstance(m, AIMessage)]

        # The first AIMessage should have tool_calls preserved
        ai_with_tools = [m for m in ai_msgs if m.tool_calls]
        assert len(ai_with_tools) == 1, (
            f"Expected 1 AIMessage with tool_calls, got {len(ai_with_tools)}"
        )
        assert ai_with_tools[0].tool_calls[0]["name"] == "search_messages"

    @pytest.mark.asyncio
    async def test_tool_message_created_correctly(self, base_config):
        """Tool role messages become ToolMessage with tool_call_id."""
        identity = "You are the Imperator."

        mock_ctx = _make_mock_ctx()
        mock_ctx.async_load_prompt = AsyncMock(return_value=identity)
        mock_ctx.get_pool = MagicMock(side_effect=RuntimeError("no pool"))
        mock_ctx.get_embeddings_model = MagicMock(side_effect=RuntimeError("no emb"))
        mock_ctx.dispatch_tool = AsyncMock(return_value={
            "context": [
                {"role": "user", "content": "Do something"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"name": "config_read", "args": {}, "id": "call_456"}
                ]},
                {"role": "tool", "content": '{"key": "value"}', "tool_call_id": "call_456"},
                {"role": "assistant", "content": "The config says..."},
            ],
        })

        state = _make_state(
            messages=[HumanMessage(content="New question")],
            config=base_config,
            cw_id=str(uuid.uuid4()),
        )

        with patch("context_broker_te.imperator_flow.get_ctx", return_value=mock_ctx):
            result = await init_context_node(state)

        assembled = result["messages"]
        tool_msgs = [m for m in assembled if isinstance(m, ToolMessage)]

        assert len(tool_msgs) == 1, (
            f"Expected 1 ToolMessage, got {len(tool_msgs)}. "
            f"Types: {[type(m).__name__ for m in assembled]}"
        )
        assert tool_msgs[0].tool_call_id == "call_456"
        assert tool_msgs[0].content == '{"key": "value"}'

    @pytest.mark.asyncio
    async def test_tool_message_not_coerced_to_human(self, base_config):
        """Tool messages must NOT become HumanMessage (old bug)."""
        identity = "You are the Imperator."

        mock_ctx = _make_mock_ctx()
        mock_ctx.async_load_prompt = AsyncMock(return_value=identity)
        mock_ctx.get_pool = MagicMock(side_effect=RuntimeError("no pool"))
        mock_ctx.get_embeddings_model = MagicMock(side_effect=RuntimeError("no emb"))
        mock_ctx.dispatch_tool = AsyncMock(return_value={
            "context": [
                {"role": "tool", "content": "result data", "tool_call_id": "call_789"},
            ],
        })

        state = _make_state(
            messages=[HumanMessage(content="Question")],
            config=base_config,
            cw_id=str(uuid.uuid4()),
        )

        with patch("context_broker_te.imperator_flow.get_ctx", return_value=mock_ctx):
            result = await init_context_node(state)

        assembled = result["messages"]
        # Should be: SystemMessage (identity) + ToolMessage + HumanMessage (current)
        # Should NOT be: SystemMessage + HumanMessage (coerced tool) + HumanMessage
        human_msgs = [m for m in assembled if isinstance(m, HumanMessage)]
        assert len(human_msgs) == 1, (
            f"Expected 1 HumanMessage (current query only), got {len(human_msgs)}. "
            f"Tool message was coerced to HumanMessage!"
        )


# ── RB-29: Message deduplication in init_context_node ──────────────


class TestMessageDeduplication:
    """RB-29: When get_context returns history ending with the same user
    message we're about to send, remove the duplicate.

    The bug: V2 get_context stores the user message, so it appears in
    history. init_context_node also appends it as the current message.
    Without dedup, the message appears twice in the prompt.
    """

    @pytest.mark.asyncio
    async def test_duplicate_user_message_removed(self, base_config):
        """Last history message matching current input is deduplicated."""
        identity = "You are the Imperator."
        user_query = "What is the MAD pattern?"

        mock_ctx = _make_mock_ctx()
        mock_ctx.async_load_prompt = AsyncMock(return_value=identity)
        mock_ctx.get_pool = MagicMock(side_effect=RuntimeError("no pool"))
        mock_ctx.get_embeddings_model = MagicMock(side_effect=RuntimeError("no emb"))
        mock_ctx.dispatch_tool = AsyncMock(return_value={
            "context": [
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier answer"},
                {"role": "user", "content": user_query},  # V2 stored this
            ],
        })

        state = _make_state(
            messages=[HumanMessage(content=user_query)],  # Same message again
            config=base_config,
            cw_id=str(uuid.uuid4()),
        )

        with patch("context_broker_te.imperator_flow.get_ctx", return_value=mock_ctx):
            result = await init_context_node(state)

        assembled = result["messages"]
        human_msgs = [m for m in assembled if isinstance(m, HumanMessage)]

        # user_query should appear exactly ONCE (not twice)
        matching = [m for m in human_msgs if m.content == user_query]
        assert len(matching) == 1, (
            f"User message '{user_query}' appears {len(matching)} times, expected 1. "
            f"Dedup failed!"
        )

    @pytest.mark.asyncio
    async def test_different_messages_not_deduplicated(self, base_config):
        """Non-matching messages are not removed."""
        identity = "You are the Imperator."

        mock_ctx = _make_mock_ctx()
        mock_ctx.async_load_prompt = AsyncMock(return_value=identity)
        mock_ctx.get_pool = MagicMock(side_effect=RuntimeError("no pool"))
        mock_ctx.get_embeddings_model = MagicMock(side_effect=RuntimeError("no emb"))
        mock_ctx.dispatch_tool = AsyncMock(return_value={
            "context": [
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "First answer"},
            ],
        })

        state = _make_state(
            messages=[HumanMessage(content="Different question")],
            config=base_config,
            cw_id=str(uuid.uuid4()),
        )

        with patch("context_broker_te.imperator_flow.get_ctx", return_value=mock_ctx):
            result = await init_context_node(state)

        assembled = result["messages"]
        human_msgs = [m for m in assembled if isinstance(m, HumanMessage)]

        # Both messages should be present (different content)
        assert len(human_msgs) == 2, (
            f"Expected 2 HumanMessages (history + current), got {len(human_msgs)}"
        )
