"""
Unit tests for Pydantic request/response models.
"""

import uuid

import pytest
from pydantic import ValidationError

from app.models import (
    ChatCompletionRequest,
    ChatMessage,
    CreateContextWindowInput,
    CreateConversationInput,
    MCPToolCall,
    MemSearchInput,
    SearchMessagesInput,
    StoreMessageInput,
)


def test_create_conversation_input_optional_title():
    """CreateConversationInput accepts no title."""
    model = CreateConversationInput()
    assert model.title is None


def test_create_conversation_input_with_title():
    """CreateConversationInput accepts a title."""
    model = CreateConversationInput(title="My Conversation")
    assert model.title == "My Conversation"


def test_store_message_input_valid():
    """StoreMessageInput validates a complete valid message."""
    conv_id = uuid.uuid4()
    model = StoreMessageInput(
        conversation_id=conv_id,
        role="user",
        sender_id="user-123",
        content="Hello, world!",
    )
    assert model.role == "user"
    assert model.content == "Hello, world!"


def test_store_message_input_invalid_role():
    """StoreMessageInput rejects invalid roles."""
    with pytest.raises(ValidationError):
        StoreMessageInput(
            conversation_id=uuid.uuid4(),
            role="invalid_role",
            sender_id="user-1",
            content="Hello",
        )


def test_store_message_input_empty_content():
    """StoreMessageInput rejects empty content."""
    with pytest.raises(ValidationError):
        StoreMessageInput(
            conversation_id=uuid.uuid4(),
            role="user",
            sender_id="user-1",
            content="",
        )


def test_create_context_window_input_valid():
    """CreateContextWindowInput validates correctly."""
    model = CreateContextWindowInput(
        conversation_id=uuid.uuid4(),
        participant_id="agent-1",
        build_type="standard-tiered",
    )
    assert model.participant_id == "agent-1"
    assert model.max_tokens is None


def test_mem_search_input_valid():
    """MemSearchInput validates correctly."""
    model = MemSearchInput(query="What do I know about Python?", user_id="user-1")
    assert model.limit == 10


def test_mem_search_input_empty_query():
    """MemSearchInput rejects empty query."""
    with pytest.raises(ValidationError):
        MemSearchInput(query="", user_id="user-1")


def test_chat_completion_request_valid():
    """ChatCompletionRequest validates a standard request."""
    model = ChatCompletionRequest(
        model="gpt-4o-mini",
        messages=[
            ChatMessage(role="user", content="Hello"),
        ],
    )
    assert model.stream is False
    assert len(model.messages) == 1


def test_chat_completion_request_empty_messages():
    """ChatCompletionRequest rejects empty messages list."""
    with pytest.raises(ValidationError):
        ChatCompletionRequest(
            model="gpt-4o-mini",
            messages=[],
        )


def test_chat_completion_request_invalid_role():
    """ChatCompletionRequest rejects messages with invalid roles."""
    with pytest.raises(ValidationError):
        ChatCompletionRequest(
            model="gpt-4o-mini",
            messages=[ChatMessage(role="invalid", content="Hello")],
        )


def test_mcp_tool_call_valid():
    """MCPToolCall validates a standard tools/call request."""
    model = MCPToolCall(
        jsonrpc="2.0",
        id=1,
        method="tools/call",
        params={"name": "conv_create_conversation", "arguments": {}},
    )
    assert model.method == "tools/call"
    assert model.params["name"] == "conv_create_conversation"


def test_search_messages_input_valid():
    """SearchMessagesInput validates correctly."""
    model = SearchMessagesInput(query="What was discussed about Python?")
    assert model.limit == 10
    assert model.conversation_id is None
