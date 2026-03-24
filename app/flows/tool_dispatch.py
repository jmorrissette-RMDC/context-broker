"""
Tool dispatch — routes MCP tool calls to compiled StateGraph flows.

All tool logic lives in StateGraph flows. This module is the thin
routing layer that maps tool names to their flows.
"""

import logging
from typing import Any

# ARCH-18: Import build_types package to trigger registration of all build types
import app.flows.build_types  # noqa: F401
from app.flows.build_type_registry import get_retrieval_graph
from app.flows.conversation_ops_flow import (
    build_create_conversation_flow,
    build_create_context_window_flow,
    build_get_context_flow,
    build_get_history_flow,
    build_search_context_windows_flow,
)
from app.flows.imperator_flow import build_imperator_flow
from app.flows.memory_admin_flow import (
    build_mem_add_flow,
    build_mem_delete_flow,
    build_mem_list_flow,
)
from app.flows.memory_search_flow import (
    build_memory_context_flow,
    build_memory_search_flow,
)
from app.flows.message_pipeline import build_message_pipeline
from app.flows.metrics_flow import build_metrics_flow
from app.flows.search_flow import (
    build_conversation_search_flow,
    build_message_search_flow,
)
from app.models import (
    GetContextInput,
    ImperatorChatInput,
    CreateContextWindowInput,
    CreateConversationInput,
    GetHistoryInput,
    MemAddInput,
    MemDeleteInput,
    MemGetContextInput,
    MemListInput,
    MemSearchInput,
    MetricsGetInput,
    RetrieveContextInput,
    SearchContextWindowsInput,
    SearchConversationsInput,
    SearchKnowledgeInput,
    SearchMessagesInput,
    StoreMessageCoreInput,
    StoreMessageInput,
)

_log = logging.getLogger("context_broker.flows.tool_dispatch")

# Lazy-initialized flow singletons — compiled on first use to avoid
# import-time side effects and allow graceful startup ordering.
_create_conversation_flow = None
_store_message_flow = None
_create_context_window_flow = None
_search_conversations_flow = None
_search_messages_flow = None
_get_history_flow = None
_search_context_windows_flow = None
_mem_search_flow = None
_mem_context_flow = None
_mem_add_flow = None
_mem_list_flow = None
_mem_delete_flow = None
_metrics_flow = None
_imperator_flow = None
_get_context_flow = None


def _get_create_conversation_flow():
    global _create_conversation_flow
    if _create_conversation_flow is None:
        _create_conversation_flow = build_create_conversation_flow()
    return _create_conversation_flow


def _get_store_message_flow():
    global _store_message_flow
    if _store_message_flow is None:
        _store_message_flow = build_message_pipeline()
    return _store_message_flow


def _get_create_context_window_flow():
    global _create_context_window_flow
    if _create_context_window_flow is None:
        _create_context_window_flow = build_create_context_window_flow()
    return _create_context_window_flow


def _get_search_conversations_flow():
    global _search_conversations_flow
    if _search_conversations_flow is None:
        _search_conversations_flow = build_conversation_search_flow()
    return _search_conversations_flow


def _get_search_messages_flow():
    global _search_messages_flow
    if _search_messages_flow is None:
        _search_messages_flow = build_message_search_flow()
    return _search_messages_flow


def _get_get_history_flow():
    global _get_history_flow
    if _get_history_flow is None:
        _get_history_flow = build_get_history_flow()
    return _get_history_flow


def _get_search_context_windows_flow():
    global _search_context_windows_flow
    if _search_context_windows_flow is None:
        _search_context_windows_flow = build_search_context_windows_flow()
    return _search_context_windows_flow


def _get_mem_search_flow():
    global _mem_search_flow
    if _mem_search_flow is None:
        _mem_search_flow = build_memory_search_flow()
    return _mem_search_flow


def _get_mem_context_flow():
    global _mem_context_flow
    if _mem_context_flow is None:
        _mem_context_flow = build_memory_context_flow()
    return _mem_context_flow


def _get_mem_add_flow():
    global _mem_add_flow
    if _mem_add_flow is None:
        _mem_add_flow = build_mem_add_flow()
    return _mem_add_flow


def _get_mem_list_flow():
    global _mem_list_flow
    if _mem_list_flow is None:
        _mem_list_flow = build_mem_list_flow()
    return _mem_list_flow


def _get_mem_delete_flow():
    global _mem_delete_flow
    if _mem_delete_flow is None:
        _mem_delete_flow = build_mem_delete_flow()
    return _mem_delete_flow


def _get_metrics_flow():
    global _metrics_flow
    if _metrics_flow is None:
        _metrics_flow = build_metrics_flow()
    return _metrics_flow


def _get_imperator_flow():
    global _imperator_flow
    if _imperator_flow is None:
        _imperator_flow = build_imperator_flow()
    return _imperator_flow


def _get_get_context_flow():
    global _get_context_flow
    if _get_context_flow is None:
        _get_context_flow = build_get_context_flow()
    return _get_context_flow


async def dispatch_tool(
    tool_name: str,
    arguments: dict[str, Any],
    config: dict[str, Any],
    app_state: Any,
) -> dict[str, Any]:
    """Route a tool call to its StateGraph flow.

    Validates inputs using Pydantic models before invoking flows.
    Raises ValueError for unknown tools or validation errors.
    """
    _log.info("Dispatching tool: %s", tool_name)

    # ============================================================
    # Core tools (D-02)
    # ============================================================

    if tool_name == "get_context":
        validated = GetContextInput(**arguments)
        result = await _get_get_context_flow().ainvoke(
            {
                "build_type": validated.build_type,
                "budget": validated.budget,
                "snapped_budget": 0,
                "conversation_id": (
                    str(validated.conversation_id)
                    if validated.conversation_id
                    else None
                ),
                "config": config,
                "context_window_id": None,
                "context_messages": None,
                "context_tiers": None,
                "total_tokens_used": 0,
                "assembly_status": "pending",
                "warnings": [],
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        response: dict[str, Any] = {
            "conversation_id": result.get("conversation_id"),
            "context": result.get("context_messages"),
            "tiers": result.get("context_tiers"),
            "total_tokens": result.get("total_tokens_used", 0),
            "assembly_status": result.get("assembly_status", "ready"),
        }
        if result.get("warnings"):
            response["warnings"] = result["warnings"]
        return response

    elif tool_name == "store_message":
        validated = StoreMessageCoreInput(**arguments)
        result = await _get_store_message_flow().ainvoke(
            {
                "context_window_id": None,
                "conversation_id_input": str(validated.conversation_id),
                "role": validated.role,
                "sender": validated.sender,
                "recipient": validated.recipient,
                "content": validated.content,
                "model_name": validated.model_name,
                "tool_calls": validated.tool_calls,
                "tool_call_id": validated.tool_call_id,
                "message_id": None,
                "sequence_number": None,
                "was_collapsed": False,
                "queued_jobs": [],
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {
            "message_id": result.get("message_id"),
            "sequence_number": result.get("sequence_number"),
        }

    elif tool_name == "search_messages":
        validated = SearchMessagesInput(**arguments)
        result = await _get_search_messages_flow().ainvoke(
            {
                "query": validated.query,
                "conversation_id": (
                    str(validated.conversation_id)
                    if validated.conversation_id
                    else None
                ),
                "sender": validated.sender,
                "role": validated.role,
                "date_from": validated.date_from,
                "date_to": validated.date_to,
                "limit": validated.limit,
                "config": config,
                "query_embedding": None,
                "candidates": [],
                "reranked_results": [],
                "warning": None,
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        response = {"messages": result.get("reranked_results", [])}
        if result.get("warning"):
            response["warning"] = result["warning"]
        return response

    elif tool_name == "search_knowledge":
        validated = SearchKnowledgeInput(**arguments)
        result = await _get_mem_search_flow().ainvoke(
            {
                "query": validated.query,
                "user_id": validated.user_id,
                "limit": validated.limit,
                "config": config,
                "memories": [],
                "relations": [],
                "degraded": False,
                "error": None,
            }
        )
        if result.get("error") and not result.get("degraded"):
            raise ValueError(result["error"])
        return {
            "memories": result.get("memories", []),
            "relations": result.get("relations", []),
            "degraded": result.get("degraded", False),
        }

    # ============================================================
    # Management tools (keep existing names)
    # ============================================================

    elif tool_name == "conv_create_conversation":
        validated = CreateConversationInput(**arguments)
        result = await _get_create_conversation_flow().ainvoke(
            {
                "conversation_id": (
                    str(validated.conversation_id)
                    if validated.conversation_id
                    else None
                ),
                "title": validated.title,
                "flow_id": validated.flow_id,
                "user_id": validated.user_id,
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {"conversation_id": result["conversation_id"]}

    elif tool_name == "conv_store_message":
        validated = StoreMessageInput(**arguments)
        result = await _get_store_message_flow().ainvoke(
            {
                "context_window_id": (
                    str(validated.context_window_id)
                    if validated.context_window_id
                    else None
                ),
                "conversation_id_input": (
                    str(validated.conversation_id)
                    if validated.conversation_id
                    else None
                ),
                "role": validated.role,
                "sender": validated.sender,
                "recipient": validated.recipient,
                "content": validated.content,
                "model_name": validated.model_name,
                "tool_calls": validated.tool_calls,
                "tool_call_id": validated.tool_call_id,
                "message_id": None,
                "sequence_number": None,
                "was_collapsed": False,
                "queued_jobs": [],
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {
            "message_id": result.get("message_id"),
            "sequence_number": result.get("sequence_number"),
            "was_collapsed": result.get("was_collapsed", False),
            "queued_jobs": result.get("queued_jobs", []),
        }

    elif tool_name == "conv_retrieve_context":
        validated = RetrieveContextInput(**arguments)
        # ARCH-18: Look up the build type from the context window, then
        # dispatch to the correct retrieval graph from the registry.
        import uuid as _uuid
        from app.database import get_pg_pool as _get_pg_pool

        _pool = _get_pg_pool()
        _window_row = await _pool.fetchrow(
            "SELECT build_type FROM context_windows WHERE id = $1",
            _uuid.UUID(str(validated.context_window_id)),
        )
        if _window_row is None:
            raise ValueError(f"Context window {validated.context_window_id} not found")

        _build_type = _window_row["build_type"]
        retrieval_graph = get_retrieval_graph(_build_type)

        result = await retrieval_graph.ainvoke(
            {
                "context_window_id": str(validated.context_window_id),
                "config": config,
                "window": None,
                "build_type_config": None,
                "conversation_id": None,
                "max_token_budget": 0,
                "tier1_summary": None,
                "tier2_summaries": [],
                "recent_messages": [],
                "semantic_messages": [],
                "knowledge_graph_facts": [],
                "assembly_status": "pending",
                "context_messages": None,
                "context_tiers": None,
                "total_tokens_used": 0,
                "warnings": [],
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        response: dict[str, Any] = {
            "context": result.get("context_messages"),
            "tiers": result.get("context_tiers"),
            "total_tokens": result.get("total_tokens_used", 0),
            "assembly_status": result.get("assembly_status", "ready"),
        }
        # m4: Surface retrieval warnings (e.g., assembly timeout) to the caller
        if result.get("warnings"):
            response["warnings"] = result["warnings"]
        return response

    elif tool_name == "conv_create_context_window":
        validated = CreateContextWindowInput(**arguments)
        result = await _get_create_context_window_flow().ainvoke(
            {
                "conversation_id": str(validated.conversation_id),
                "participant_id": validated.participant_id,
                "build_type": validated.build_type,
                "max_tokens_override": validated.max_tokens,
                "config": config,
                "context_window_id": None,
                "resolved_token_budget": 0,
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {
            "context_window_id": result.get("context_window_id"),
            "resolved_token_budget": result.get("resolved_token_budget"),
        }

    elif tool_name == "conv_search":
        validated = SearchConversationsInput(**arguments)
        result = await _get_search_conversations_flow().ainvoke(
            {
                "query": validated.query,
                "limit": validated.limit,
                "offset": validated.offset,
                "date_from": validated.date_from,
                "date_to": validated.date_to,
                "flow_id": validated.flow_id,
                "user_id": validated.user_id,
                "sender": validated.sender,
                "config": config,
                "query_embedding": None,
                "results": [],
                "warning": None,
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        response: dict[str, Any] = {"conversations": result.get("results", [])}
        if result.get("warning"):
            response["warning"] = result["warning"]
        return response

    elif tool_name == "conv_search_messages":
        validated = SearchMessagesInput(**arguments)
        result = await _get_search_messages_flow().ainvoke(
            {
                "query": validated.query,
                "conversation_id": (
                    str(validated.conversation_id)
                    if validated.conversation_id
                    else None
                ),
                "sender": validated.sender,
                "role": validated.role,
                "date_from": validated.date_from,
                "date_to": validated.date_to,
                "limit": validated.limit,
                "config": config,
                "query_embedding": None,
                "candidates": [],
                "reranked_results": [],
                "warning": None,
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        response = {"messages": result.get("reranked_results", [])}
        if result.get("warning"):
            response["warning"] = result["warning"]
        return response

    elif tool_name == "conv_get_history":
        validated = GetHistoryInput(**arguments)
        result = await _get_get_history_flow().ainvoke(
            {
                "conversation_id": str(validated.conversation_id),
                "limit": validated.limit,
                "conversation": None,
                "messages": [],
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {
            "conversation": result.get("conversation"),
            "messages": result.get("messages", []),
        }

    elif tool_name == "conv_search_context_windows":
        validated = SearchContextWindowsInput(**arguments)
        result = await _get_search_context_windows_flow().ainvoke(
            {
                "context_window_id": (
                    str(validated.context_window_id)
                    if validated.context_window_id
                    else None
                ),
                "conversation_id": (
                    str(validated.conversation_id)
                    if validated.conversation_id
                    else None
                ),
                "participant_id": validated.participant_id,
                "build_type": validated.build_type,
                "limit": validated.limit,
                "results": [],
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {"context_windows": result.get("results", [])}

    elif tool_name == "mem_search":
        validated = MemSearchInput(**arguments)
        result = await _get_mem_search_flow().ainvoke(
            {
                "query": validated.query,
                "user_id": validated.user_id,
                "limit": validated.limit,
                "config": config,
                "memories": [],
                "relations": [],
                "degraded": False,
                "error": None,
            }
        )
        if result.get("error") and not result.get("degraded"):
            raise ValueError(result["error"])
        return {
            "memories": result.get("memories", []),
            "relations": result.get("relations", []),
            "degraded": result.get("degraded", False),
        }

    elif tool_name == "mem_get_context":
        validated = MemGetContextInput(**arguments)
        result = await _get_mem_context_flow().ainvoke(
            {
                "query": validated.query,
                "user_id": validated.user_id,
                "limit": validated.limit,
                "config": config,
                "memories": [],
                "context_text": "",
                "degraded": False,
                "error": None,
            }
        )
        if result.get("error") and not result.get("degraded"):
            raise ValueError(result["error"])
        return {
            "context": result.get("context_text", ""),
            "memories": result.get("memories", []),
        }

    elif tool_name == "imperator_chat":
        validated = ImperatorChatInput(**arguments)
        from langchain_core.messages import HumanMessage

        thread_id = (
            str(validated.context_window_id)
            if validated.context_window_id
            else "imperator-default"
        )

        result = await _get_imperator_flow().ainvoke(
            {
                "messages": [HumanMessage(content=validated.message)],
                "context_window_id": (
                    str(validated.context_window_id)
                    if validated.context_window_id
                    else None
                ),
                "config": config,
                "response_text": None,
                "error": None,
                "iteration_count": 0,
            },
            config={"configurable": {"thread_id": thread_id}},
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {
            "response": result.get("response_text", ""),
        }

    elif tool_name == "mem_add":
        # M-18: Routed through StateGraph flow instead of direct Mem0 call
        validated = MemAddInput(**arguments)
        result = await _get_mem_add_flow().ainvoke(
            {
                "content": validated.content,
                "user_id": validated.user_id,
                "config": config,
                "result": None,
                "degraded": False,
                "error": None,
            }
        )
        if result.get("error") and not result.get("degraded"):
            raise ValueError(result["error"])
        return {"status": "added", "result": result.get("result")}

    elif tool_name == "mem_list":
        # M-18: Routed through StateGraph flow instead of direct Mem0 call
        validated = MemListInput(**arguments)
        result = await _get_mem_list_flow().ainvoke(
            {
                "user_id": validated.user_id,
                "limit": validated.limit,
                "config": config,
                "memories": [],
                "degraded": False,
                "error": None,
            }
        )
        if result.get("error") and not result.get("degraded"):
            raise ValueError(result["error"])
        return {"memories": result.get("memories", [])}

    elif tool_name == "mem_delete":
        # M-18: Routed through StateGraph flow instead of direct Mem0 call
        validated = MemDeleteInput(**arguments)
        result = await _get_mem_delete_flow().ainvoke(
            {
                "memory_id": validated.memory_id,
                "config": config,
                "deleted": False,
                "degraded": False,
                "error": None,
            }
        )
        if result.get("error") and not result.get("degraded"):
            raise ValueError(result["error"])
        return {"status": "deleted", "memory_id": validated.memory_id}

    elif tool_name == "metrics_get":
        MetricsGetInput(**arguments)
        result = await _get_metrics_flow().ainvoke(
            {
                "action": "collect",
                "metrics_output": "",
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {"metrics": result.get("metrics_output", "")}

    else:
        raise ValueError(f"Unknown tool: {tool_name}")
