"""
MCP (Model Context Protocol) endpoint.

Implements HTTP/SSE transport for MCP tool access.
Routes tool calls to compiled StateGraph flows.

Endpoints:
  GET  /mcp          — Establish SSE session
  POST /mcp          — Sessionless tool call or route to session
  POST /mcp?sessionId=xxx — Route to existing session
"""

import asyncio
import decimal
import json
import logging
import time
import uuid


def _json_default(obj: object) -> object:
    """Handle non-standard types in MCP JSON responses."""
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
from collections import OrderedDict
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.config import async_load_config, get_tuning
from app.flows.tool_dispatch import dispatch_tool
from app.metrics_registry import (
    MCP_REQUEST_DURATION,
    MCP_REQUESTS,
)
from app.models import MCPToolCall

_log = logging.getLogger("context_broker.routes.mcp")

router = APIRouter()

# Active SSE sessions: session_id -> {"queue": asyncio.Queue, "created_at": float}
# OrderedDict preserves insertion order for efficient eviction of oldest sessions.
_sessions: OrderedDict[str, dict[str, Any]] = OrderedDict()

# R5-M25: Track total queued messages across all sessions to bound memory
_total_queued_messages: int = 0

# Configurable limits (defaults; overridden by config at request time)
_MAX_SESSIONS = 1000
_SESSION_TTL_SECONDS = 3600  # 1 hour
_MAX_TOTAL_QUEUED = 10000

# R6-M3: Lock for session dict mutations to prevent race conditions
_session_lock = asyncio.Lock()


def _evict_stale_sessions(
    session_ttl: int = _SESSION_TTL_SECONDS,
    max_sessions: int = _MAX_SESSIONS,
    max_total_queued: int = _MAX_TOTAL_QUEUED,
) -> None:
    """Remove sessions older than TTL and enforce the max sessions cap.

    R5-M25: Also evict oldest sessions when total queued messages exceed the cap.
    R7-m15: Accepts parameters instead of reading module globals on every call.
    """
    global _total_queued_messages
    now = time.monotonic()
    stale_ids = [
        sid
        for sid, info in _sessions.items()
        if now - info["created_at"] > session_ttl
    ]
    for sid in stale_ids:
        info = _sessions.pop(sid, None)
        if info is not None:
            _total_queued_messages -= info["queue"].qsize()
        _log.info("MCP SSE session evicted (TTL): %s", sid)

    # Evict oldest if over cap
    while len(_sessions) > max_sessions:
        evicted_id, info = _sessions.popitem(last=False)
        _total_queued_messages -= info["queue"].qsize()
        _log.info("MCP SSE session evicted (cap): %s", evicted_id)

    # R5-M25: Evict oldest sessions if total queued messages exceed threshold
    while _total_queued_messages > max_total_queued and _sessions:
        evicted_id, info = _sessions.popitem(last=False)
        _total_queued_messages -= info["queue"].qsize()
        _log.warning("MCP SSE session evicted (total queue pressure): %s", evicted_id)


@router.get("/mcp")
async def mcp_sse_session(request: Request) -> StreamingResponse:
    """Establish an SSE session for MCP communication.

    Returns an SSE stream. The client sends tool calls via
    POST /mcp?sessionId=<id>.
    """
    # R7-m15: Read config limits but don't mutate module globals on every request.
    # Use local variables for this request's session creation.
    config = await async_load_config()
    max_sessions = get_tuning(config, "mcp_max_sessions", _MAX_SESSIONS)
    session_ttl = get_tuning(config, "mcp_session_ttl_seconds", _SESSION_TTL_SECONDS)
    max_total_queued = get_tuning(config, "mcp_max_total_queued", _MAX_TOTAL_QUEUED)

    session_id = str(uuid.uuid4())

    # R6-M3: Protect session dict mutations with asyncio.Lock
    async with _session_lock:
        _evict_stale_sessions(session_ttl, max_sessions, max_total_queued)
        # G5-26: Bound the per-session queue to prevent memory growth from slow clients
        message_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        _sessions[session_id] = {"queue": message_queue, "created_at": time.monotonic()}

    _log.info("MCP SSE session established: %s (active=%d)", session_id, len(_sessions))

    async def event_stream() -> AsyncGenerator[str, None]:
        # Send session ID as first event
        yield f"data: {json.dumps({'sessionId': session_id})}\n\n"

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(message_queue.get(), timeout=30.0)
                    # R5-M25: Decrement global counter when message is consumed
                    global _total_queued_messages
                    _total_queued_messages = max(0, _total_queued_messages - 1)
                    yield f"data: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
        finally:
            # R6-M3: Protect session dict mutations with asyncio.Lock
            async with _session_lock:
                # R6-M2: Decrement global counter by remaining queue size before removal
                removed = _sessions.pop(session_id, None)
                if removed is not None:
                    _total_queued_messages = max(
                        0, _total_queued_messages - removed["queue"].qsize()
                    )
            _log.info("MCP SSE session closed: %s", session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/mcp")
async def mcp_tool_call(
    request: Request,
    session_id: str = Query(None, alias="sessionId"),
) -> JSONResponse:
    """Handle an MCP tool call.

    Supports both sessionless mode (no sessionId) and session mode.
    All tool calls are routed through StateGraph flows.
    """
    start_time = time.monotonic()
    tool_name = "unknown"
    status = "error"

    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        _log.warning("MCP: failed to parse request body: %s", exc)
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
        )

    try:
        mcp_request = MCPToolCall(**body)
    except ValidationError as exc:
        _log.warning("MCP: invalid request structure: %s", exc)
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32600, "message": "Invalid Request"},
            },
        )

    if mcp_request.method == "initialize":
        tool_name = "initialize"
        status = "success"
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": mcp_request.id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "context-broker",
                        "version": "1.0.0",
                    },
                },
            }
        )

    if mcp_request.method == "tools/list":
        tool_name = "tools_list"
        status = "success"
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": mcp_request.id,
                "result": {"tools": _get_tool_list()},
            }
        )

    if mcp_request.method != "tools/call":
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": mcp_request.id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {mcp_request.method}",
                },
            },
        )

    tool_name = mcp_request.params.get("name", "unknown")
    tool_arguments = mcp_request.params.get("arguments", {})

    # R7-M9: Wrap config load in try/except with error response
    try:
        config = await async_load_config()
    except (OSError, RuntimeError, ValueError) as exc:
        _log.error("MCP: config load failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "jsonrpc": "2.0",
                "id": mcp_request.id,
                "error": {"code": -32000, "message": f"Configuration unavailable: {exc}"},
            },
        )

    try:
        result = await dispatch_tool(
            tool_name, tool_arguments, config, request.app.state
        )
        status = "success"

        response_content = {
            "jsonrpc": "2.0",
            "id": mcp_request.id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            json.dumps(result, default=_json_default)
                            if isinstance(result, dict)
                            else str(result)
                        ),
                    }
                ]
            },
        }

        # If session mode, push to session queue and return acknowledgment
        if session_id:
            if session_id not in _sessions:
                # G5-25: Unknown sessionId — return error instead of falling through
                return JSONResponse(
                    status_code=404,
                    content={
                        "jsonrpc": "2.0",
                        "id": mcp_request.id,
                        "error": {
                            "code": -32001,
                            "message": f"Session not found: {session_id}",
                        },
                    },
                )
            try:
                async with _session_lock:
                    if session_id not in _sessions:
                        return JSONResponse(
                            status_code=404,
                            content={
                                "jsonrpc": "2.0",
                                "id": mcp_request.id,
                                "error": {
                                    "code": -32001,
                                    "message": f"Session disconnected: {session_id}",
                                },
                            },
                        )
                    _sessions[session_id]["queue"].put_nowait(response_content)
                    global _total_queued_messages
                    _total_queued_messages += 1
            except asyncio.QueueFull:
                _log.warning(
                    "MCP SSE session queue full for session=%s; dropping response",
                    session_id,
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "jsonrpc": "2.0",
                        "id": mcp_request.id,
                        "error": {
                            "code": -32000,
                            "message": "Session queue full — client is not consuming events fast enough",
                        },
                    },
                )
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": mcp_request.id,
                    "result": "queued",
                }
            )

        return JSONResponse(content=response_content)

    except (ValueError, ValidationError) as exc:
        status = "validation_error"
        _log.warning("MCP tool '%s' validation error: %s", tool_name, exc)
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": mcp_request.id,
                "error": {"code": -32602, "message": str(exc)},
            },
        )
    except (RuntimeError, ConnectionError, OSError) as exc:
        status = "internal_error"
        _log.error("MCP tool '%s' failed: %s", tool_name, exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "jsonrpc": "2.0",
                "id": mcp_request.id,
                "error": {"code": -32000, "message": str(exc)},
            },
        )
    finally:
        duration = time.monotonic() - start_time
        MCP_REQUESTS.labels(tool=tool_name, status=status).inc()
        MCP_REQUEST_DURATION.labels(tool=tool_name).observe(duration)


def _get_tool_list() -> list[dict]:
    """Return the MCP tool list with schemas.

    Core tools (get_context, store_message, search_messages, search_knowledge)
    are listed first — these are the primary interface for agents.
    Management tools (conv_*, mem_*, imperator_chat, metrics_get) follow.
    """
    # Build dynamic enum for build_type from config
    try:
        from app.config import load_te_config
        te_config = load_te_config()
    except Exception:
        te_config = {}
    try:
        from app.config import load_config
        ae_config = load_config()
    except Exception:
        ae_config = {}
    build_type_names = list(ae_config.get("build_types", {}).keys()) or [
        "passthrough", "standard-tiered", "knowledge-enriched"
    ]

    from app.budget import BUDGET_BUCKETS
    bucket_desc = ", ".join(str(b) for b in BUDGET_BUCKETS)

    return [
        # ============================================================
        # Core tools (D-02)
        # ============================================================
        {
            "name": "get_context",
            "description": "Retrieve assembled context for a conversation. Auto-creates conversation and context window if needed. Returns conversation_id for subsequent store_message calls.",
            "inputSchema": {
                "type": "object",
                "required": ["build_type", "budget"],
                "properties": {
                    "build_type": {
                        "type": "string",
                        "enum": build_type_names,
                        "description": "Context assembly strategy",
                    },
                    "budget": {
                        "type": "integer",
                        "minimum": 1,
                        "description": f"Token budget (snapped to nearest bucket: {bucket_desc})",
                    },
                    "conversation_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": "Existing conversation ID. Omit to create a new conversation.",
                    },
                },
            },
        },
        {
            "name": "store_message",
            "description": "Store a message in a conversation. Triggers async embedding, extraction, and context assembly.",
            "inputSchema": {
                "type": "object",
                "required": ["conversation_id", "role", "sender"],
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": "Conversation ID (from get_context response)",
                    },
                    "role": {
                        "type": "string",
                        "enum": ["user", "assistant", "system", "tool"],
                    },
                    "content": {"type": "string"},
                    "sender": {"type": "string"},
                    "recipient": {"type": "string"},
                    "model_name": {"type": "string"},
                    "tool_calls": {"type": "array", "items": {"type": "object"}},
                    "tool_call_id": {"type": "string"},
                },
            },
        },
        {
            "name": "search_messages",
            "description": "Hybrid search (vector + BM25 + reranking) across conversation messages.",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "conversation_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": "Scope search to a specific conversation",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                    "sender": {"type": "string"},
                    "role": {"type": "string", "enum": ["user", "assistant", "system", "tool"]},
                    "date_from": {"type": "string", "description": "ISO-8601"},
                    "date_to": {"type": "string", "description": "ISO-8601"},
                },
            },
        },
        {
            "name": "search_knowledge",
            "description": "Search extracted facts and relationships from the knowledge graph.",
            "inputSchema": {
                "type": "object",
                "required": ["query", "user_id"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "user_id": {"type": "string", "minLength": 1, "maxLength": 255},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                },
            },
        },
        # ============================================================
        # Management tools
        # ============================================================
        {
            "name": "conv_create_conversation",
            "description": "Create a new conversation",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": "Caller-supplied ID for idempotent creation",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional conversation title",
                    },
                    "flow_id": {
                        "type": "string",
                        "description": "Optional flow identifier",
                    },
                    "user_id": {
                        "type": "string",
                        "description": "Optional user identifier",
                    },
                },
            },
        },
        {
            "name": "conv_store_message",
            "description": "Store a message in a conversation (triggers async embedding, assembly, extraction). At least one of context_window_id or conversation_id must be provided.",
            "inputSchema": {
                "type": "object",
                "required": ["role", "sender"],
                "properties": {
                    "context_window_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": "Context window ID (resolves conversation automatically). At least one of context_window_id or conversation_id is required.",
                    },
                    "conversation_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": "Direct conversation ID (skips context window lookup). At least one of context_window_id or conversation_id is required.",
                    },
                    "role": {
                        "type": "string",
                        "enum": ["user", "assistant", "system", "tool"],
                    },
                    "sender": {"type": "string"},
                    "recipient": {"type": "string"},
                    "content": {"type": "string"},
                    "priority": {"type": "integer", "default": 0},
                    "model_name": {"type": "string"},
                    "tool_calls": {"type": "array", "items": {"type": "object"}},
                    "tool_call_id": {"type": "string"},
                },
            },
        },
        {
            "name": "conv_retrieve_context",
            "description": "Retrieve the assembled context window for a participant",
            "inputSchema": {
                "type": "object",
                "required": ["context_window_id"],
                "properties": {
                    "context_window_id": {"type": "string", "format": "uuid"},
                },
            },
        },
        {
            "name": "conv_create_context_window",
            "description": "Create a context window instance with a build type and token budget",
            "inputSchema": {
                "type": "object",
                "required": ["conversation_id", "participant_id", "build_type"],
                "properties": {
                    "conversation_id": {"type": "string", "format": "uuid"},
                    "participant_id": {"type": "string"},
                    "build_type": {"type": "string"},
                    "max_tokens": {"type": "integer"},
                },
            },
        },
        {
            "name": "conv_search",
            "description": "Semantic and structured search across conversations",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                    "offset": {"type": "integer", "default": 0},
                    "date_from": {
                        "type": "string",
                        "description": "ISO-8601 date lower bound",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "ISO-8601 date upper bound",
                    },
                    "flow_id": {
                        "type": "string",
                        "description": "Filter by flow identifier",
                    },
                    "user_id": {
                        "type": "string",
                        "description": "Filter by user identifier",
                    },
                    "sender": {
                        "type": "string",
                        "description": "Filter by sender (matches conversations containing messages from this sender)",
                    },
                },
            },
        },
        {
            "name": "conv_search_messages",
            "description": "Hybrid search (vector + BM25 + reranking) across messages",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "conversation_id": {"type": "string", "format": "uuid"},
                    "sender": {"type": "string", "description": "Filter by sender"},
                    "role": {
                        "type": "string",
                        "enum": ["user", "assistant", "system", "tool"],
                    },
                    "date_from": {
                        "type": "string",
                        "description": "ISO-8601 date lower bound",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "ISO-8601 date upper bound",
                    },
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
        {
            "name": "conv_get_history",
            "description": "Retrieve full chronological message sequence for a conversation",
            "inputSchema": {
                "type": "object",
                "required": ["conversation_id"],
                "properties": {
                    "conversation_id": {"type": "string", "format": "uuid"},
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "conv_search_context_windows",
            "description": "Search and list context windows",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "context_window_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": "Look up a specific context window by ID",
                    },
                    "conversation_id": {"type": "string", "format": "uuid"},
                    "participant_id": {"type": "string"},
                    "build_type": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
        {
            "name": "mem_search",
            "description": "Semantic and graph search across extracted knowledge",
            "inputSchema": {
                "type": "object",
                "required": ["query", "user_id"],
                "properties": {
                    "query": {"type": "string"},
                    "user_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
        {
            "name": "mem_get_context",
            "description": "Retrieve relevant memories formatted for prompt injection",
            "inputSchema": {
                "type": "object",
                "required": ["query", "user_id"],
                "properties": {
                    "query": {"type": "string"},
                    "user_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
            },
        },
        {
            "name": "mem_add",
            "description": "Directly add a memory to the knowledge graph",
            "inputSchema": {
                "type": "object",
                "required": ["content", "user_id"],
                "properties": {
                    "content": {"type": "string"},
                    "user_id": {"type": "string"},
                },
            },
        },
        {
            "name": "mem_list",
            "description": "List all memories for a user",
            "inputSchema": {
                "type": "object",
                "required": ["user_id"],
                "properties": {
                    "user_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        },
        {
            "name": "mem_delete",
            "description": "Delete a specific memory by ID",
            "inputSchema": {
                "type": "object",
                "required": ["memory_id"],
                "properties": {
                    "memory_id": {"type": "string"},
                },
            },
        },
        {
            "name": "imperator_chat",
            "description": "Conversational interface to the Imperator",
            "inputSchema": {
                "type": "object",
                "required": ["message"],
                "properties": {
                    "message": {"type": "string"},
                    "conversation_id": {"type": "string", "format": "uuid"},
                },
            },
        },
        {
            "name": "metrics_get",
            "description": "Retrieve Prometheus metrics",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]
