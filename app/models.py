"""
Pydantic models for request/response validation.

All external inputs are validated through these models before
reaching StateGraph flows.
"""

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# ============================================================
# MCP Tool Input Models
# ============================================================


class CreateConversationInput(BaseModel):
    """Input for conv_create_conversation."""

    conversation_id: Optional[UUID] = Field(
        None, description="Caller-supplied ID for idempotent creation"
    )
    title: Optional[str] = Field(None, max_length=500)
    flow_id: Optional[str] = Field(None, max_length=255)
    user_id: Optional[str] = Field(None, max_length=255)


class StoreMessageInput(BaseModel):
    """Input for conv_store_message."""

    context_window_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None
    role: str = Field(..., pattern="^(user|assistant|system|tool)$")

    @model_validator(mode="after")
    def _require_at_least_one_id(self) -> "StoreMessageInput":
        if self.context_window_id is None and self.conversation_id is None:
            raise ValueError(
                "At least one of context_window_id or conversation_id must be provided"
            )
        return self

    sender: str = Field(..., min_length=1, max_length=255)
    recipient: Optional[str] = Field(None, max_length=255)
    content: Optional[str] = Field(None)
    priority: Optional[int] = Field(0, ge=0, le=10)
    model_name: Optional[str] = Field(None, max_length=255)
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = Field(None, max_length=255)


class CreateContextWindowInput(BaseModel):
    """Input for conv_create_context_window."""

    conversation_id: UUID
    participant_id: str = Field(..., min_length=1, max_length=255)
    build_type: str = Field(..., min_length=1, max_length=100)
    max_tokens: Optional[int] = Field(None, ge=1)


class RetrieveContextInput(BaseModel):
    """Input for conv_retrieve_context."""

    context_window_id: UUID


class DeleteConversationInput(BaseModel):
    """Input for conv_delete_conversation."""

    conversation_id: UUID


class RenameConversationInput(BaseModel):
    """Input for conv_rename_conversation."""

    conversation_id: UUID
    title: str


class ListConversationsInput(BaseModel):
    """Input for conv_list_conversations."""

    participant: Optional[str] = Field(
        None,
        max_length=255,
        description="Filter by participant — returns conversations where this value "
        "appears as sender or recipient on any message.",
    )
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)


class SearchConversationsInput(BaseModel):
    """Input for conv_search."""

    query: Optional[str] = Field(None, max_length=2000)
    limit: int = Field(10, ge=1, le=100)
    offset: int = Field(0, ge=0)
    date_from: Optional[str] = Field(None, description="ISO-8601 date lower bound")
    date_to: Optional[str] = Field(None, description="ISO-8601 date upper bound")
    flow_id: Optional[str] = Field(None, max_length=255)
    user_id: Optional[str] = Field(None, max_length=255)
    sender: Optional[str] = Field(None, max_length=255)


class SearchMessagesInput(BaseModel):
    """Input for conv_search_messages."""

    query: str = Field(..., min_length=1, max_length=2000)
    conversation_id: Optional[UUID] = None
    limit: int = Field(10, ge=1, le=100)
    sender: Optional[str] = Field(None, max_length=255)
    role: Optional[str] = Field(None, pattern="^(user|assistant|system|tool)$")
    date_from: Optional[str] = Field(None, description="ISO-8601 date lower bound")
    date_to: Optional[str] = Field(None, description="ISO-8601 date upper bound")


class GetHistoryInput(BaseModel):
    """Input for conv_get_history."""

    conversation_id: UUID
    limit: Optional[int] = Field(None, ge=1, le=10000)


class SearchContextWindowsInput(BaseModel):
    """Input for conv_search_context_windows."""

    context_window_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None
    participant_id: Optional[str] = Field(None, max_length=255)
    build_type: Optional[str] = Field(None, max_length=100)
    limit: int = Field(10, ge=1, le=100)


class MemSearchInput(BaseModel):
    """Input for mem_search (management tool)."""

    query: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(..., min_length=1, max_length=255)
    limit: int = Field(10, ge=1, le=100)


class MemGetContextInput(BaseModel):
    """Input for mem_get_context (management tool — superseded by search_knowledge)."""

    query: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(..., min_length=1, max_length=255)
    limit: int = Field(5, ge=1, le=50)


# ============================================================
# Core Tool Input Models (D-02)
# ============================================================


class GetContextInput(BaseModel):
    """Input for get_context — retrieve assembled context, auto-creating
    conversation and window as needed."""

    build_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Context assembly strategy (e.g., 'sliding-window', 'tiered-summary', 'enriched')",
    )
    budget: int = Field(
        ..., ge=1, description="Token budget — snapped to nearest bucket (4K-2M)"
    )
    conversation_id: Optional[UUID] = Field(
        None, description="Existing conversation ID. Omit to create a new conversation."
    )
    user_prompt: Optional[str] = Field(
        None,
        max_length=10000,
        description="User's current prompt — drives semantic search, KG retrieval, and domain knowledge lookup for enriched build type.",
    )
    model: Optional[dict] = Field(
        None,
        description="Caller's LLM config {base_url, model, api_key_env} for distillation. Uses the caller's model so prompt cache is shared.",
    )
    domain_context: Optional[str] = Field(
        None,
        max_length=50000,
        description="Caller's domain-specific knowledge to fold into context distillation. External MADs pass their own domain RAG results here.",
    )


class StoreMessageCoreInput(BaseModel):
    """Input for store_message — store a message in a conversation."""

    conversation_id: UUID = Field(..., description="Conversation to store message in")
    role: str = Field(..., pattern="^(user|assistant|system|tool)$")
    content: Optional[str] = Field(None)
    sender: str = Field(..., min_length=1, max_length=255)
    recipient: Optional[str] = Field(None, max_length=255)
    model_name: Optional[str] = Field(None, max_length=255)
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = Field(None, max_length=255)


class SearchKnowledgeInput(BaseModel):
    """Input for search_knowledge — search extracted facts and relationships."""

    query: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(..., min_length=1, max_length=255)
    limit: int = Field(10, ge=1, le=100)


class MemAddInput(BaseModel):
    """Input for mem_add — directly add a memory to Mem0."""

    content: str = Field(..., min_length=1, max_length=10000)
    user_id: str = Field(..., min_length=1, max_length=255)


class MemListInput(BaseModel):
    """Input for mem_list — list all memories for a user."""

    user_id: str = Field(..., min_length=1, max_length=255)
    limit: int = Field(50, ge=1, le=500)


class MemDeleteInput(BaseModel):
    """Input for mem_delete — delete a specific memory by ID."""

    memory_id: str = Field(..., min_length=1, max_length=255)


class QueryLogsInput(BaseModel):
    """Input for query_logs — SQL-based log filtering."""

    container_name: Optional[str] = Field(None, max_length=255)
    level: Optional[str] = Field(None, pattern="^(DEBUG|INFO|WARN|WARNING|ERROR)$")
    since: Optional[str] = Field(None, description="ISO-8601 timestamp lower bound")
    until: Optional[str] = Field(None, description="ISO-8601 timestamp upper bound")
    keyword: Optional[str] = Field(None, max_length=500)
    limit: int = Field(50, ge=1, le=500)


class SearchLogsInput(BaseModel):
    """Input for search_logs — semantic search over vectorized logs."""

    query: str = Field(..., min_length=1, max_length=2000)
    container_name: Optional[str] = Field(None, max_length=255)
    level: Optional[str] = Field(None, pattern="^(DEBUG|INFO|WARN|WARNING|ERROR)$")
    since: Optional[str] = Field(None, description="ISO-8601 timestamp lower bound")
    limit: int = Field(20, ge=1, le=100)


class ImperatorChatInput(BaseModel):
    """Input for imperator_chat."""

    message: str = Field(..., min_length=1, max_length=32000)
    context_window_id: Optional[UUID] = None


class MetricsGetInput(BaseModel):
    """Input for metrics_get (no required fields)."""

    pass


# ============================================================
# OpenAI-compatible chat models
# ============================================================


class ChatMessage(BaseModel):
    """A single message in an OpenAI-compatible chat request."""

    role: str = Field(..., pattern="^(system|user|assistant|tool)$")
    content: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible /v1/chat/completions request body."""

    model: str = Field(default="context-broker")
    messages: list[ChatMessage] = Field(..., min_length=1)
    stream: bool = False
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1)
    user: Optional[str] = Field(None, max_length=255)


# ============================================================
# MCP Protocol Models
# ============================================================


class MCPToolCall(BaseModel):
    """MCP JSON-RPC tools/call request."""

    jsonrpc: str = Field(default="2.0")
    id: Optional[Any] = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class MCPToolResult(BaseModel):
    """MCP JSON-RPC tools/call response."""

    jsonrpc: str = "2.0"
    id: Optional[Any] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None
