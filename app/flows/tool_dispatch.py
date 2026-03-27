"""
Tool dispatch — routes MCP tool calls to compiled StateGraph flows.

All tool logic lives in StateGraph flows loaded dynamically from AE/TE
packages via entry_points (REQ-001 §10). This module is the thin
kernel-side routing layer that maps tool names to their flows using
the stategraph_registry.
"""

import logging
import time
from typing import Any

from app.flows.build_type_registry import get_retrieval_graph
from app.metrics_registry import MCP_REQUESTS, MCP_REQUEST_DURATION
from app.stategraph_registry import get_flow_builder, get_imperator_builder
from app.models import (
    GetContextInput,
    ImperatorChatInput,
    CreateContextWindowInput,
    CreateConversationInput,
    GetHistoryInput,
    DeleteConversationInput,
    ListConversationsInput,
    QueryLogsInput,
    SearchLogsInput,
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

# Lazy-initialized flow singletons — compiled on first use from
# dynamically loaded packages via the stategraph_registry.
_flow_cache: dict[str, Any] = {}


def _get_flow(name: str) -> Any:
    """Get a compiled flow by registry name (lazy singleton)."""
    if name not in _flow_cache:
        builder = get_flow_builder(name)
        if builder is None:
            raise RuntimeError(
                f"Flow '{name}' not available. Is the AE package installed?"
            )
        _flow_cache[name] = builder()
    return _flow_cache[name]


def _get_imperator_flow() -> Any:
    """Get the compiled Imperator flow from the TE registry."""
    if "imperator" not in _flow_cache:
        builder = get_imperator_builder()
        if builder is None:
            raise RuntimeError(
                "No TE package registered. Install a TE package with "
                "install_stategraph or ensure one is installed at startup."
            )
        _flow_cache["imperator"] = builder()
    return _flow_cache["imperator"]


def invalidate_flow_cache() -> None:
    """Clear all cached flows. Called after install_stategraph()."""
    _flow_cache.clear()
    _log.info("Flow dispatch cache cleared")


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
    _start_time = time.monotonic()
    _status = "error"

    try:
        result = await _dispatch_tool_inner(tool_name, arguments, config, app_state)
        _status = "success"
        return result
    finally:
        _duration = time.monotonic() - _start_time
        MCP_REQUESTS.labels(tool=tool_name, status=_status).inc()
        MCP_REQUEST_DURATION.labels(tool=tool_name).observe(_duration)


async def _dispatch_tool_inner(
    tool_name: str,
    arguments: dict[str, Any],
    config: dict[str, Any],
    app_state: Any,
) -> dict[str, Any]:
    """Inner dispatch — routes tool calls to their StateGraph flows."""

    # ============================================================
    # Core tools (D-02)
    # ============================================================

    if tool_name == "get_context":
        validated = GetContextInput(**arguments)
        result = await _get_flow("get_context").ainvoke(
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
                # V2: query-driven retrieval parameters
                "query": validated.query,
                "model": validated.model,
                "domain_context": validated.domain_context,
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
        result = await _get_flow("message_pipeline").ainvoke(
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
        result = await _get_flow("message_search").ainvoke(
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
        result = await _get_flow("memory_search").ainvoke(
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
        result = await _get_flow("create_conversation").ainvoke(
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

    elif tool_name == "conv_delete_conversation":
        validated = DeleteConversationInput(**arguments)
        from app.database import get_pg_pool

        pool = get_pg_pool()
        # Atomic delete — all or nothing
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM conversation_summaries WHERE context_window_id IN "
                    "(SELECT id FROM context_windows WHERE conversation_id = $1)",
                    validated.conversation_id,
                )
                await conn.execute(
                    "DELETE FROM context_windows WHERE conversation_id = $1",
                    validated.conversation_id,
                )
                await conn.execute(
                    "DELETE FROM conversation_messages WHERE conversation_id = $1",
                    validated.conversation_id,
                )
                result = await conn.execute(
                    "DELETE FROM conversations WHERE id = $1",
                    validated.conversation_id,
                )
        return {"deleted": result == "DELETE 1"}

    elif tool_name == "conv_rename_conversation":
        from app.models import RenameConversationInput

        validated = RenameConversationInput(**arguments)
        from app.database import get_pg_pool

        pool = get_pg_pool()
        result = await pool.execute(
            "UPDATE conversations SET title = $1 WHERE id = $2",
            validated.title,
            validated.conversation_id,
        )
        return {"renamed": result == "UPDATE 1", "title": validated.title}

    elif tool_name == "conv_list_conversations":
        validated = ListConversationsInput(**arguments)
        from app.database import get_pg_pool

        pool = get_pg_pool()

        if validated.participant:
            # Filter to conversations where participant appears as sender or recipient
            rows = await pool.fetch(
                """
                SELECT DISTINCT c.id, c.title, c.flow_id, c.user_id, c.created_at,
                       (SELECT COUNT(*) FROM conversation_messages cm
                        WHERE cm.conversation_id = c.id) AS message_count
                FROM conversations c
                WHERE EXISTS (
                    SELECT 1 FROM conversation_messages cm
                    WHERE cm.conversation_id = c.id
                    AND (cm.sender = $1 OR cm.recipient = $1)
                )
                ORDER BY c.created_at DESC
                LIMIT $2 OFFSET $3
                """,
                validated.participant,
                validated.limit,
                validated.offset,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT c.id, c.title, c.flow_id, c.user_id, c.created_at,
                       (SELECT COUNT(*) FROM conversation_messages cm
                        WHERE cm.conversation_id = c.id) AS message_count
                FROM conversations c
                ORDER BY c.created_at DESC
                LIMIT $1 OFFSET $2
                """,
                validated.limit,
                validated.offset,
            )

        conversations = [
            {
                "id": str(row["id"]),
                "title": row["title"],
                "flow_id": row["flow_id"],
                "user_id": row["user_id"],
                "created_at": (
                    row["created_at"].isoformat() if row["created_at"] else None
                ),
                "message_count": row["message_count"],
            }
            for row in rows
        ]
        return {"conversations": conversations}

    elif tool_name == "conv_store_message":
        validated = StoreMessageInput(**arguments)
        result = await _get_flow("message_pipeline").ainvoke(
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
        # R7-m19: Null check for build_type before registry lookup
        if not _build_type:
            raise ValueError(
                f"Context window {validated.context_window_id} has no build_type set"
            )
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
        result = await _get_flow("create_context_window").ainvoke(
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
        result = await _get_flow("conversation_search").ainvoke(
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
        result = await _get_flow("message_search").ainvoke(
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
        result = await _get_flow("get_history").ainvoke(
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
        result = await _get_flow("search_context_windows").ainvoke(
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

    elif tool_name == "query_logs":
        validated = QueryLogsInput(**arguments)
        from app.database import get_pg_pool as _get_pool

        pool = _get_pool()
        conditions = []
        args: list = []
        idx = 1

        if validated.container_name:
            conditions.append(f"container_name ILIKE ${idx}")
            args.append(f"%{validated.container_name}%")
            idx += 1
        if validated.level:
            conditions.append(f"data->>'level' = ${idx}")
            args.append(validated.level.upper())
            idx += 1
        if validated.since:
            conditions.append(f"log_timestamp >= ${idx}::timestamptz")
            args.append(validated.since)
            idx += 1
        if validated.until:
            conditions.append(f"log_timestamp <= ${idx}::timestamptz")
            args.append(validated.until)
            idx += 1
        if validated.keyword:
            conditions.append(f"message ILIKE ${idx}")
            args.append(f"%{validated.keyword}%")
            idx += 1

        where = " AND ".join(conditions) if conditions else "1=1"
        args.append(validated.limit)

        rows = await pool.fetch(
            f"""
            SELECT container_name, log_timestamp, message, data
            FROM system_logs
            WHERE {where}
            ORDER BY log_timestamp DESC
            LIMIT ${idx}
            """,
            *args,
        )
        entries = []
        for row in rows:
            data = row["data"] if isinstance(row["data"], dict) else {}
            entries.append(
                {
                    "timestamp": (
                        row["log_timestamp"].isoformat()
                        if row["log_timestamp"]
                        else None
                    ),
                    "container_name": row["container_name"],
                    "level": data.get("level", "unknown"),
                    "logger": data.get("logger", ""),
                    "message": row["message"] or "",
                }
            )
        return {"entries": entries, "count": len(entries)}

    elif tool_name == "search_logs":
        validated = SearchLogsInput(**arguments)
        from app.config import async_load_config as _aload

        cfg = await _aload()
        log_emb_config = cfg.get("log_embeddings")
        if not log_emb_config:
            raise ValueError(
                "Log vectorization is not enabled. "
                "Add a 'log_embeddings' section to config.yml to enable semantic log search."
            )

        # Embed the query
        from app.config import get_embeddings_model

        emb_model = get_embeddings_model(cfg, config_key="log_embeddings")
        query_vec = await emb_model.aembed_query(validated.query)
        vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"

        from app.database import get_pg_pool as _get_pool2

        pool = _get_pool2()
        conditions = ["embedding IS NOT NULL"]
        args_search: list = [vec_str]
        idx_s = 2

        if validated.container_name:
            conditions.append(f"container_name ILIKE ${idx_s}")
            args_search.append(f"%{validated.container_name}%")
            idx_s += 1
        if validated.level:
            conditions.append(f"data->>'level' = ${idx_s}")
            args_search.append(validated.level.upper())
            idx_s += 1
        if validated.since:
            conditions.append(f"log_timestamp >= ${idx_s}::timestamptz")
            args_search.append(validated.since)
            idx_s += 1

        where = " AND ".join(conditions)
        args_search.append(validated.limit)

        rows = await pool.fetch(
            f"""
            SELECT container_name, log_timestamp, message, data,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM system_logs
            WHERE {where}
            ORDER BY embedding <=> $1::vector
            LIMIT ${idx_s}
            """,
            *args_search,
        )
        entries = []
        for row in rows:
            data = row["data"] if isinstance(row["data"], dict) else {}
            entries.append(
                {
                    "timestamp": (
                        row["log_timestamp"].isoformat()
                        if row["log_timestamp"]
                        else None
                    ),
                    "container_name": row["container_name"],
                    "level": data.get("level", "unknown"),
                    "message": row["message"] or "",
                    "similarity": round(float(row["similarity"]), 4),
                }
            )
        return {"entries": entries, "count": len(entries)}

    elif tool_name == "mem_search":
        validated = MemSearchInput(**arguments)
        result = await _get_flow("memory_search").ainvoke(
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
        result = await _get_flow("memory_context").ainvoke(
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

        # Unique thread_id per invocation — MemorySaver persists within
        # a single ReAct execution, not across user turns.
        import uuid as _uuid

        thread_id = str(_uuid.uuid4())

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
        result = await _get_flow("mem_add").ainvoke(
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
        result = await _get_flow("mem_list").ainvoke(
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
        result = await _get_flow("mem_delete").ainvoke(
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
        result = await _get_flow("metrics").ainvoke(
            {
                "action": "collect",
                "metrics_output": "",
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {"metrics": result.get("metrics_output", "")}

    elif tool_name == "install_stategraph":
        package_name = arguments.get("package_name", "")
        version = arguments.get("version")
        if not package_name:
            raise ValueError("package_name is required")
        from app.flows.install_stategraph import install_stategraph

        result = await install_stategraph(package_name, version)
        # Invalidate all cached flows so next call uses new package
        invalidate_flow_cache()
        from app.flows.imperator_wrapper import invalidate as invalidate_imperator

        invalidate_imperator()
        return result

    else:
        raise ValueError(f"Unknown tool: {tool_name}")
