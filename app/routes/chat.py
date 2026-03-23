"""
OpenAI-compatible chat completions endpoint.

Implements /v1/chat/completions following the OpenAI API specification.
Routes to the Imperator StateGraph.
Supports both streaming (SSE) and non-streaming responses.
"""

import json
import logging
import time
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from app.config import async_load_config
from app.flows.imperator_flow import build_imperator_flow
from app.metrics_registry import CHAT_REQUESTS, CHAT_REQUEST_DURATION
from app.models import ChatCompletionRequest

_log = logging.getLogger("context_broker.routes.chat")

router = APIRouter()

# Lazy-initialized Imperator flow — compiled on first use
_imperator_flow = None


def _get_imperator_flow():
    global _imperator_flow
    if _imperator_flow is None:
        _imperator_flow = build_imperator_flow()
    return _imperator_flow


@router.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request):
    """Handle OpenAI-compatible chat completion requests.

    Routes to the Imperator StateGraph. Supports streaming and non-streaming.
    """
    start_time = time.monotonic()
    status = "error"
    is_streaming = False

    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        _log.warning("Chat: failed to parse request body: %s", exc)
        return JSONResponse(
            status_code=400,
            content={
                "error": {"message": "Invalid JSON", "type": "invalid_request_error"}
            },
        )

    try:
        chat_request = ChatCompletionRequest(**body)
    except ValidationError as exc:
        _log.warning("Chat: request validation failed: %s", exc)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "message": str(exc),
                    "type": "invalid_request_error",
                }
            },
        )

    is_streaming = chat_request.stream

    config = await async_load_config()
    imperator_manager = getattr(request.app.state, "imperator_manager", None)

    # Extract the last user message as the primary input
    user_messages = [m for m in chat_request.messages if m.role == "user"]
    if not user_messages:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "At least one user message is required",
                    "type": "invalid_request_error",
                }
            },
        )

    # G5-27: Allow clients to specify a context_window_id for multi-client
    # isolation via x-context-window-id header or context_window_id in the body.
    # Also accepts the legacy x-conversation-id / conversation_id for compatibility.
    # Falls back to the default Imperator context window when not provided.
    context_window_id = (
        request.headers.get("x-context-window-id")
        or body.get("context_window_id")
        or request.headers.get("x-conversation-id")
        or body.get("conversation_id")
    )
    if not context_window_id and imperator_manager is not None:
        context_window_id = await imperator_manager.get_context_window_id()

    # Convert plain messages to LangChain message objects
    # G5-28: Include ToolMessage so tool-role messages are not coerced to HumanMessage.
    _role_map = {
        "user": HumanMessage,
        "system": SystemMessage,
        "assistant": AIMessage,
        "tool": ToolMessage,
    }
    lc_messages = []
    for m in chat_request.messages:
        cls = _role_map.get(m.role, HumanMessage)
        if cls is ToolMessage:
            # ToolMessage requires a tool_call_id; use the one from the
            # request body if available, otherwise fall back to a placeholder.
            tool_call_id = getattr(m, "tool_call_id", None) or "unknown"
            lc_messages.append(
                ToolMessage(content=m.content, tool_call_id=tool_call_id)
            )
        else:
            lc_messages.append(cls(content=m.content))

    initial_state = {
        "messages": lc_messages,
        "context_window_id": str(context_window_id) if context_window_id else None,
        "config": config,
        "response_text": None,
        "error": None,
    }

    try:
        if chat_request.stream:
            # For streaming, metrics are tracked inside the generator after
            # the stream completes, not here in the route handler.
            return StreamingResponse(
                _stream_imperator_response(initial_state, chat_request, start_time),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        else:
            result = await _get_imperator_flow().ainvoke(initial_state)

            if result.get("error"):
                _log.error("Imperator flow error: %s", result["error"])
                status = "error"
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "message": result["error"],
                            "type": "internal_error",
                        }
                    },
                )

            response_text = result.get("response_text", "")
            status = "success"

            return JSONResponse(
                content=_build_completion_response(response_text, chat_request.model)
            )

    except (RuntimeError, ConnectionError, OSError) as exc:
        _log.error("Chat completion failed: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": "Internal server error",
                    "type": "internal_error",
                }
            },
        )
    finally:
        # Only record metrics for non-streaming requests here.
        # Streaming metrics are recorded in the generator.
        if not is_streaming:
            duration = time.monotonic() - start_time
            CHAT_REQUESTS.labels(status=status).inc()
            CHAT_REQUEST_DURATION.observe(duration)


async def _stream_imperator_response(
    initial_state: dict,
    chat_request: ChatCompletionRequest,
    start_time: float,
) -> AsyncGenerator[str, None]:
    """Stream the Imperator response as SSE tokens.

    M-22: astream_events(version="v2") captures on_chat_model_stream events
    from nested ainvoke() calls within the LangGraph runtime, so real token
    streaming works without requiring the agent to use astream() internally.
    If a provider/model does not emit streaming tokens via ainvoke (e.g. some
    local models), true per-token streaming would require the Imperator's
    final non-tool-call LLM invocation to use llm.astream() instead. This is
    a known limitation; the current implementation works correctly with
    OpenAI-compatible providers that support streaming under the hood.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    stream_status = "success"

    try:
        # G5-29: Known limitation — when the ReAct agent processes tool calls,
        # astream_events may emit no content tokens for those intermediate LLM
        # turns (only the final non-tool-call turn produces streamable tokens).
        # This is inherent to how LangGraph processes tool calls and is not a bug.
        async for event in _get_imperator_flow().astream_events(
            initial_state, version="v2"
        ):
            if event["event"] == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if token:
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": chat_request.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": token},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"

        # Final chunk with finish_reason
        final_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": chat_request.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
        yield f"data: {json.dumps(final_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    except (RuntimeError, ConnectionError, OSError) as exc:
        _log.error("Streaming imperator response failed: %s", exc, exc_info=True)
        stream_status = "error"
        error_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": chat_request.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "An error occurred processing your request."},
                    "finish_reason": "stop",
                }
            ],
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    finally:
        # Record streaming metrics after the stream completes
        duration = time.monotonic() - start_time
        CHAT_REQUESTS.labels(status=stream_status).inc()
        CHAT_REQUEST_DURATION.observe(duration)


def _build_completion_response(response_text: str, model: str) -> dict:
    """Build an OpenAI-compatible non-streaming completion response."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": -1,
            "completion_tokens": -1,
            "total_tokens": -1,
        },
    }
