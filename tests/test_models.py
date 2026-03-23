"""
Unit tests for Pydantic request/response models.

Covers validation rules, nullable fields, model_validator logic,
and field constraints for all externally-facing input models.
"""

import uuid

import pytest
from pydantic import ValidationError

from app.models import (
    ChatCompletionRequest,
    ChatMessage,
    CreateContextWindowInput,
    CreateConversationInput,
    ImperatorChatInput,
    MCPToolCall,
    MemSearchInput,
    SearchConversationsInput,
    SearchMessagesInput,
    StoreMessageInput,
)


# ==================================================================
# StoreMessageInput
# ==================================================================


class TestStoreMessageInput:
    """Tests for StoreMessageInput validation."""

    def test_valid_with_context_window_id(self):
        """Accepts a message identified by context_window_id only."""
        cw_id = uuid.uuid4()
        model = StoreMessageInput(
            context_window_id=cw_id,
            role="user",
            sender="user-1",
            content="Hello",
        )
        assert model.context_window_id == cw_id
        assert model.conversation_id is None

    def test_valid_with_conversation_id(self):
        """Accepts a message identified by conversation_id only."""
        conv_id = uuid.uuid4()
        model = StoreMessageInput(
            conversation_id=conv_id,
            role="assistant",
            sender="bot-1",
            content="Hi there",
        )
        assert model.conversation_id == conv_id
        assert model.context_window_id is None

    def test_valid_with_both_ids(self):
        """Accepts a message when both IDs are provided."""
        model = StoreMessageInput(
            context_window_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            role="user",
            sender="user-1",
            content="Hello",
        )
        assert model.context_window_id is not None
        assert model.conversation_id is not None

    def test_error_when_neither_id_provided(self):
        """Raises ValidationError when neither context_window_id nor conversation_id is given."""
        with pytest.raises(ValidationError, match="At least one of context_window_id or conversation_id"):
            StoreMessageInput(
                role="user",
                sender="user-1",
                content="Hello",
            )

    def test_nullable_content(self):
        """Content can be None (e.g. tool_calls-only messages)."""
        model = StoreMessageInput(
            context_window_id=uuid.uuid4(),
            role="assistant",
            sender="bot-1",
            content=None,
        )
        assert model.content is None

    def test_tool_calls_as_list_of_dicts(self):
        """tool_calls accepts a list of dicts."""
        tool_calls = [
            {"id": "tc_1", "type": "function", "function": {"name": "search", "arguments": "{}"}},
        ]
        model = StoreMessageInput(
            context_window_id=uuid.uuid4(),
            role="assistant",
            sender="bot-1",
            content=None,
            tool_calls=tool_calls,
        )
        assert model.tool_calls == tool_calls
        assert isinstance(model.tool_calls, list)
        assert isinstance(model.tool_calls[0], dict)

    def test_tool_calls_none_by_default(self):
        """tool_calls defaults to None."""
        model = StoreMessageInput(
            context_window_id=uuid.uuid4(),
            role="user",
            sender="user-1",
            content="Hello",
        )
        assert model.tool_calls is None

    def test_tool_call_id_field(self):
        """tool_call_id accepts a string for tool response messages."""
        model = StoreMessageInput(
            context_window_id=uuid.uuid4(),
            role="tool",
            sender="tool-runner",
            content='{"result": 42}',
            tool_call_id="tc_1",
        )
        assert model.tool_call_id == "tc_1"

    def test_invalid_role_rejected(self):
        """Rejects roles not in the allowed set."""
        with pytest.raises(ValidationError):
            StoreMessageInput(
                context_window_id=uuid.uuid4(),
                role="moderator",
                sender="user-1",
                content="Hello",
            )

    def test_sender_required_nonempty(self):
        """Sender must be a non-empty string."""
        with pytest.raises(ValidationError):
            StoreMessageInput(
                context_window_id=uuid.uuid4(),
                role="user",
                sender="",
                content="Hello",
            )

    def test_priority_default_zero(self):
        """Priority defaults to 0."""
        model = StoreMessageInput(
            context_window_id=uuid.uuid4(),
            role="user",
            sender="user-1",
            content="Hello",
        )
        assert model.priority == 0

    def test_priority_out_of_range(self):
        """Priority outside 0-10 is rejected."""
        with pytest.raises(ValidationError):
            StoreMessageInput(
                context_window_id=uuid.uuid4(),
                role="user",
                sender="user-1",
                content="Hello",
                priority=11,
            )


# ==================================================================
# ChatMessage
# ==================================================================


class TestChatMessage:
    """Tests for ChatMessage model."""

    def test_nullable_content(self):
        """Content can be None (e.g., assistant messages with tool_calls)."""
        msg = ChatMessage(role="assistant", content=None)
        assert msg.content is None

    def test_tool_calls_field(self):
        """tool_calls accepts a list of dicts."""
        calls = [{"id": "tc_1", "type": "function", "function": {"name": "foo"}}]
        msg = ChatMessage(role="assistant", content=None, tool_calls=calls)
        assert msg.tool_calls == calls

    def test_tool_call_id_field(self):
        """tool_call_id accepts a string for tool response messages."""
        msg = ChatMessage(role="tool", content="result", tool_call_id="tc_1")
        assert msg.tool_call_id == "tc_1"

    def test_valid_roles(self):
        """All four valid roles are accepted."""
        for role in ("system", "user", "assistant", "tool"):
            msg = ChatMessage(role=role, content="test")
            assert msg.role == role

    def test_invalid_role_rejected(self):
        """Invalid role is rejected."""
        with pytest.raises(ValidationError):
            ChatMessage(role="function", content="test")


# ==================================================================
# ChatCompletionRequest
# ==================================================================


class TestChatCompletionRequest:
    """Tests for ChatCompletionRequest model."""

    def test_valid_non_streaming(self):
        """Non-streaming request validates correctly."""
        req = ChatCompletionRequest(
            model="context-broker",
            messages=[ChatMessage(role="user", content="Hello")],
            stream=False,
        )
        assert req.stream is False
        assert len(req.messages) == 1

    def test_valid_streaming(self):
        """Streaming request validates correctly."""
        req = ChatCompletionRequest(
            model="context-broker",
            messages=[ChatMessage(role="user", content="Hello")],
            stream=True,
        )
        assert req.stream is True

    def test_default_model(self):
        """Model defaults to 'context-broker'."""
        req = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="Hello")],
        )
        assert req.model == "context-broker"

    def test_default_stream_false(self):
        """Stream defaults to False."""
        req = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="Hello")],
        )
        assert req.stream is False

    def test_empty_messages_rejected(self):
        """Empty messages list is rejected."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(model="test", messages=[])

    def test_temperature_range(self):
        """Temperature outside 0.0-2.0 is rejected."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                messages=[ChatMessage(role="user", content="test")],
                temperature=2.5,
            )

    def test_max_tokens_positive(self):
        """max_tokens must be >= 1 if provided."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                messages=[ChatMessage(role="user", content="test")],
                max_tokens=0,
            )


# ==================================================================
# SearchConversationsInput
# ==================================================================


class TestSearchConversationsInput:
    """Tests for SearchConversationsInput model."""

    def test_flow_id_filter(self):
        """flow_id filter is accepted."""
        model = SearchConversationsInput(flow_id="flow-abc")
        assert model.flow_id == "flow-abc"

    def test_user_id_filter(self):
        """user_id filter is accepted."""
        model = SearchConversationsInput(user_id="user-1")
        assert model.user_id == "user-1"

    def test_sender_filter(self):
        """sender filter is accepted."""
        model = SearchConversationsInput(sender="agent-1")
        assert model.sender == "agent-1"

    def test_defaults(self):
        """Default limit/offset values are set."""
        model = SearchConversationsInput()
        assert model.limit == 10
        assert model.offset == 0
        assert model.query is None

    def test_limit_range(self):
        """Limit must be between 1 and 100."""
        with pytest.raises(ValidationError):
            SearchConversationsInput(limit=0)
        with pytest.raises(ValidationError):
            SearchConversationsInput(limit=101)

    def test_date_filters(self):
        """Date range filters are accepted."""
        model = SearchConversationsInput(
            date_from="2026-01-01T00:00:00Z",
            date_to="2026-03-23T00:00:00Z",
        )
        assert model.date_from is not None
        assert model.date_to is not None


# ==================================================================
# ImperatorChatInput
# ==================================================================


class TestImperatorChatInput:
    """Tests for ImperatorChatInput model."""

    def test_valid_with_context_window_id(self):
        """Accepts message with optional context_window_id."""
        cw_id = uuid.uuid4()
        model = ImperatorChatInput(
            message="Hello Imperator",
            context_window_id=cw_id,
        )
        assert model.context_window_id == cw_id
        assert model.message == "Hello Imperator"

    def test_valid_without_context_window_id(self):
        """context_window_id is optional."""
        model = ImperatorChatInput(message="Hello Imperator")
        assert model.context_window_id is None

    def test_empty_message_rejected(self):
        """Empty message string is rejected."""
        with pytest.raises(ValidationError):
            ImperatorChatInput(message="")

    def test_message_required(self):
        """Message field is required."""
        with pytest.raises(ValidationError):
            ImperatorChatInput()


# ==================================================================
# Other models — basic coverage
# ==================================================================


class TestCreateConversationInput:
    """Tests for CreateConversationInput."""

    def test_all_fields_optional(self):
        """All fields are optional."""
        model = CreateConversationInput()
        assert model.conversation_id is None
        assert model.title is None
        assert model.flow_id is None
        assert model.user_id is None

    def test_with_idempotent_id(self):
        """Accepts a caller-supplied UUID."""
        uid = uuid.uuid4()
        model = CreateConversationInput(conversation_id=uid)
        assert model.conversation_id == uid


class TestCreateContextWindowInput:
    """Tests for CreateContextWindowInput."""

    def test_valid(self):
        """Validates correctly with required fields."""
        model = CreateContextWindowInput(
            conversation_id=uuid.uuid4(),
            participant_id="agent-1",
            build_type="standard-tiered",
        )
        assert model.max_tokens is None

    def test_with_max_tokens(self):
        """Accepts optional max_tokens."""
        model = CreateContextWindowInput(
            conversation_id=uuid.uuid4(),
            participant_id="agent-1",
            build_type="standard-tiered",
            max_tokens=4096,
        )
        assert model.max_tokens == 4096


class TestMCPToolCall:
    """Tests for MCPToolCall."""

    def test_valid(self):
        """Validates a standard MCP tool call."""
        model = MCPToolCall(
            id=1,
            method="tools/call",
            params={"name": "conv_create_conversation", "arguments": {}},
        )
        assert model.jsonrpc == "2.0"
        assert model.method == "tools/call"

    def test_default_params(self):
        """params defaults to empty dict."""
        model = MCPToolCall(method="tools/list")
        assert model.params == {}
