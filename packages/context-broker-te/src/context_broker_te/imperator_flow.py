"""
Imperator — LangGraph ReAct-style conversational agent flow.

The Imperator is the Context Broker's built-in conversational agent.
It uses a proper LangGraph ReAct graph (agent_node -> tool_node loop)
with no checkpointer — conversation history is loaded from PostgreSQL
on each invocation and results are stored via the standard message
pipeline (conv_store_message).

Uses LangChain's ChatOpenAI.bind_tools() for tool binding.

ARCH-05: ReAct loop is graph edges, not a while loop inside a node.
ARCH-06: No MemorySaver — DB is the persistence layer.
F-22:    Messages stored through conv_store_message pipeline.
"""

import logging
import socket
import uuid
from typing import Annotated, Optional

import asyncpg
import httpx
import openai
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from app.config import get_chat_model, get_tuning
from app.database import get_pg_pool
from app.prompt_loader import async_load_prompt

from context_broker_te.tools.admin import get_tools as get_admin_tools
from context_broker_te.tools.alerting import get_tools as get_alerting_tools
from context_broker_te.tools.diagnostic import get_tools as get_diagnostic_tools
from context_broker_te.tools.filesystem import get_tools as get_filesystem_tools
from context_broker_te.tools.notify import get_tools as get_notify_tools
from context_broker_te.tools.operational import get_tools as get_operational_tools
from context_broker_te.tools.scheduling import get_tools as get_scheduling_tools
from context_broker_te.tools.system import get_tools as get_system_tools
from context_broker_te.tools.web import get_tools as get_web_tools

_log = logging.getLogger("context_broker.flows.imperator")

# MAD identity — hostname is the Docker container name
_MAD_HOSTNAME = socket.gethostname()


# ── State ────────────────────────────────────────────────────────────────


class ImperatorState(TypedDict):
    """State for the Imperator ReAct agent.

    ARCH-05: messages accumulates via add_messages reducer across
    agent_node <-> tool_node cycles.  The graph runs fresh each
    invocation — no checkpointer.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    context_window_id: Optional[str]
    config: dict
    response_text: Optional[str]
    error: Optional[str]
    iteration_count: int
    _user_message_stored: Optional[bool]  # V2: set by agent_node when get_context stores the user msg


# ── Core search tools (depend on AE flow singletons) ──────────────────

_conv_search_flow_singleton = None
_mem_search_flow_singleton = None


def _get_conv_search_flow():
    global _conv_search_flow_singleton
    if _conv_search_flow_singleton is None:
        from app.stategraph_registry import get_flow_builder

        builder = get_flow_builder("conversation_search")
        if builder is None:
            raise RuntimeError(
                "AE package not loaded: conversation_search flow unavailable"
            )
        _conv_search_flow_singleton = builder()
    return _conv_search_flow_singleton


def _get_mem_search_flow():
    global _mem_search_flow_singleton
    if _mem_search_flow_singleton is None:
        from app.stategraph_registry import get_flow_builder

        builder = get_flow_builder("memory_search")
        if builder is None:
            raise RuntimeError("AE package not loaded: memory_search flow unavailable")
        _mem_search_flow_singleton = builder()
    return _mem_search_flow_singleton


@tool
async def conv_search(query: str, limit: int = 5) -> str:
    """Search conversation history for relevant messages and conversations.

    Use this when the user asks about what was said, discussed, or decided
    in past conversations.

    Args:
        query: The search query describing what to find.
        limit: Maximum number of results to return (default 5).
    """
    from app.config import async_load_config

    config = await async_load_config()
    flow = _get_conv_search_flow()
    result = await flow.ainvoke(
        {
            "query": query,
            "limit": limit,
            "offset": 0,
            "date_from": None,
            "date_to": None,
            "flow_id": None,
            "user_id": None,
            "sender": None,
            "config": config,
            "query_embedding": None,
            "results": [],
            "warning": None,
            "error": None,
        }
    )
    results = result.get("results", [])
    if not results:
        return "No conversations found matching that query."
    lines = [f"Found {len(results)} conversation(s):"]
    for conv in results:
        lines.append(
            f"- {conv.get('title', 'Untitled')} (id: {conv['id']}, "
            f"messages: {conv.get('total_messages', 0)})"
        )
    return "\n".join(lines)


@tool
async def mem_search(query: str, user_id: str = "imperator", limit: int = 5) -> str:
    """Search extracted knowledge and memories from the knowledge graph.

    Use this when the user asks about facts, preferences, relationships,
    or anything that has been learned and stored as structured knowledge.

    Args:
        query: The search query describing what knowledge to find.
        user_id: The user whose memories to search (default: imperator).
        limit: Maximum number of results to return (default 5).
    """
    from app.config import async_load_config

    config = await async_load_config()
    flow = _get_mem_search_flow()
    result = await flow.ainvoke(
        {
            "query": query,
            "user_id": user_id,
            "limit": limit,
            "config": config,
            "memories": [],
            "relations": [],
            "degraded": False,
            "error": None,
        }
    )
    memories = result.get("memories", [])
    if not memories:
        return "No relevant memories found."
    lines = [f"Found {len(memories)} relevant memory/memories:"]
    for mem in memories:
        fact = mem.get("memory") or mem.get("content") or str(mem)
        lines.append(f"- {fact}")
    return "\n".join(lines)


# ── Tool assembly ──────────────────────────────────────────────────────

# Core tools: always available
_core_tools: list = [conv_search, mem_search]


def _collect_tools(imperator_config: dict) -> list:
    """Collect all active tools based on config.

    Discovers tools from the tools/ modules via get_tools().
    """
    active = list(_core_tools)
    active.extend(get_diagnostic_tools())
    active.extend(get_scheduling_tools())
    active.extend(get_web_tools())
    active.extend(get_filesystem_tools())
    active.extend(get_system_tools())
    active.extend(get_notify_tools(imperator_config))
    if imperator_config.get("admin_tools", False):
        active.extend(get_admin_tools())
    active.extend(get_operational_tools(imperator_config))
    active.extend(get_alerting_tools(imperator_config))
    return active


# ── Message pipeline singleton ──────────────────────────────────────────

# R7-m14: Pre-bound LLM with tools — set at graph compilation time
_prebound_llm = None

_message_pipeline_singleton = None


def _get_message_pipeline():
    """Lazy-init the standard message pipeline flow."""
    global _message_pipeline_singleton
    if _message_pipeline_singleton is None:
        from app.stategraph_registry import get_flow_builder

        builder = get_flow_builder("message_pipeline")
        if builder is None:
            raise RuntimeError(
                "AE package not loaded: message_pipeline flow unavailable"
            )
        _message_pipeline_singleton = builder()
    return _message_pipeline_singleton


# ── Helper: load DB history ─────────────────────────────────────────────


async def _load_conversation_history(context_window_id: str, config: dict) -> str:
    """Load recent conversation history from PostgreSQL for context.

    ARCH-06: History comes from the DB, not a checkpointer.  Returns a
    formatted string to embed in the system prompt.
    """
    history_limit = get_tuning(config, "imperator_history_limit", 20)
    try:
        pool = get_pg_pool()
        cw_row = await pool.fetchrow(
            "SELECT conversation_id FROM context_windows WHERE id = $1",
            uuid.UUID(context_window_id),
        )
        if cw_row is None:
            _log.warning("Context window %s not found", context_window_id)
            return ""

        conversation_id = cw_row["conversation_id"]
        rows = await pool.fetch(
            """
            SELECT role, content
            FROM conversation_messages
            WHERE conversation_id = $1
            ORDER BY sequence_number DESC
            LIMIT $2
            """,
            conversation_id,
            history_limit,
        )
        if not rows:
            return ""

        history_lines = []
        for row in reversed(rows):
            row_content = row.get("content") or ""
            history_lines.append(f"[{row['role']}]: {row_content}")
        return (
            "\n\n--- Recent conversation history (for context) ---\n"
            + "\n".join(history_lines)
            + "\n--- End of history ---\n"
        )
    except (RuntimeError, OSError, asyncpg.PostgresError) as exc:
        _log.warning("Failed to load Imperator history: %s", exc)
        return ""


# ── Graph nodes ──────────────────────────────────────────────────────────


async def agent_node(state: ImperatorState) -> dict:
    """Call the LLM with bound tools and return the response.

    ARCH-05: This node contains NO loop.  Flow control (tool-call vs
    final answer) is handled by the conditional edge after this node.

    V2 cache-friendly message structure:
      1. SystemMessage — static identity (cached, never changes)
      2. HumanMessage/AIMessage — conversation history (cached prefix)
      3. HumanMessage — user's actual message (new, at end)

    History loaded as separate messages (not embedded in SystemMessage)
    to preserve prompt caching across turns.
    """
    config = state["config"]

    # R7-m14: Use the pre-bound LLM from graph compilation.
    # Falls back to runtime binding if _prebound_llm is not available (e.g., tests).
    llm_with_tools = _prebound_llm
    if llm_with_tools is None:
        imperator_config = config.get("imperator", {})
        active_tools = _collect_tools(imperator_config)
        llm = get_chat_model(config, role="imperator")
        llm_with_tools = llm.bind_tools(active_tools)

    messages = list(state["messages"])
    user_query = None  # set inside first-call block if available

    # First call: build cache-friendly message sequence
    has_system = any(isinstance(m, SystemMessage) for m in messages)
    if not has_system:
        imperator_cfg = config.get("imperator", {})
        prompt_name = imperator_cfg.get("system_prompt", "imperator_identity")
        try:
            system_content = await async_load_prompt(prompt_name)
        except RuntimeError as exc:
            _log.error("Failed to load system prompt '%s': %s", prompt_name, exc)
            return {
                "messages": [AIMessage(content="I encountered a configuration error.")],
                "response_text": "I encountered a configuration error.",
                "error": f"Prompt loading failed: {exc}",
            }

        # Static system message — identity only (cached by LLM providers)
        system_msg = SystemMessage(content=system_content)

        # Extract user's query for RAG-driven retrieval
        user_query = None
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                user_query = msg.content
                break

        # Domain RAG: search local domain knowledge for the user's query
        domain_context = None
        if user_query:
            try:
                from app.database import get_pg_pool
                from app.config import get_embeddings_model

                pool = get_pg_pool()
                emb_model = get_embeddings_model(config)
                query_vec = await emb_model.aembed_query(user_query[:500])
                vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"

                rows = await pool.fetch(
                    """
                    SELECT content, 1 - (embedding <=> $1::vector) AS similarity
                    FROM domain_information
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> $1::vector
                    LIMIT 3
                    """,
                    vec_str,
                )
                if rows:
                    relevant = [r["content"] for r in rows if float(r["similarity"]) > 0.3]
                    if relevant:
                        domain_context = "\n".join(f"- {r}" for r in relevant)
            except (RuntimeError, OSError, ValueError) as exc:
                _log.debug("Domain RAG lookup skipped: %s", exc)

        # D-07: Load context via get_context with V2 parameters
        history_messages = []
        conversation_id = state.get("context_window_id")
        if conversation_id:
            try:
                from app.flows.tool_dispatch import dispatch_tool

                build_type = imperator_cfg.get("build_type", "tiered-summary")
                budget = imperator_cfg.get("max_context_tokens", 8192)
                if not isinstance(budget, int):
                    budget = 8192

                # V2: Pass query, model config, and domain context
                get_context_args = {
                    "build_type": build_type,
                    "budget": budget,
                    "conversation_id": str(conversation_id),
                }
                if user_query:
                    get_context_args["user_prompt"] = user_query
                if domain_context:
                    get_context_args["domain_context"] = domain_context
                # Pass the Imperator's own model config for distillation cache
                get_context_args["model"] = {
                    "base_url": imperator_cfg.get("base_url", ""),
                    "model": imperator_cfg.get("model", ""),
                    "api_key_env": imperator_cfg.get("api_key_env", ""),
                }

                ctx_result = await dispatch_tool(
                    "get_context",
                    get_context_args,
                    config,
                    None,
                )
                context_messages = ctx_result.get("context", [])

                # V2: Load history as separate messages for prompt caching
                for msg in context_messages:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if not content:
                        continue
                    if role == "assistant":
                        history_messages.append(AIMessage(content=content))
                    else:
                        history_messages.append(HumanMessage(content=content))
            except (ValueError, RuntimeError, OSError) as exc:
                _log.warning("Failed to load context via get_context: %s", exc)

        # Assemble: system (static, cached) + history (cached prefix) + current messages
        messages = [system_msg] + history_messages + messages
        _first_call = True
    else:
        _first_call = False

    # CB-R3-06: Truncate older messages if the list exceeds the limit.
    max_react_messages = get_tuning(config, "imperator_max_react_messages", 40)
    if len(messages) > max_react_messages:
        from langchain_core.messages import ToolMessage

        cut_index = len(messages) - (max_react_messages - 1)
        while cut_index < len(messages) and isinstance(
            messages[cut_index], ToolMessage
        ):
            cut_index += 1
        messages = [messages[0]] + messages[cut_index:]

    # Retry on empty response — Gemini occasionally returns valid but empty
    # completions (content="" with no tool_calls). This is never a valid
    # agent response, so retry up to 2 times before accepting it.
    max_retries = 2
    response = None
    for attempt in range(max_retries + 1):
        try:
            response = await llm_with_tools.ainvoke(messages)
        except (openai.APIError, httpx.HTTPError, ValueError, RuntimeError) as exc:
            _log.error("Imperator LLM call failed: %s", exc, exc_info=True)
            return {
                "messages": [
                    AIMessage(
                        content="I encountered an error processing your request."
                    )
                ],
                "response_text": "I encountered an error processing your request.",
                "error": str(exc),
            }

        # Valid response: has text content or tool calls
        if (response.content and response.content.strip()) or response.tool_calls:
            break

        if attempt < max_retries:
            _log.warning(
                "Imperator LLM returned empty response (attempt %d/%d) — retrying",
                attempt + 1,
                max_retries + 1,
            )
        else:
            _log.error(
                "Imperator LLM returned empty response after %d attempts",
                max_retries + 1,
            )

    # On first call, include SystemMessage in returned messages so
    # add_messages persists it in state. Prevents re-executing get_context
    # on subsequent ReAct iterations.
    returned_messages = [response]
    if _first_call:
        returned_messages = [system_msg] + returned_messages

    result = {
        "messages": returned_messages,
        "iteration_count": state.get("iteration_count", 0) + 1,
    }

    # V2: Flag that user message was stored by get_context
    if _first_call and user_query and state.get("context_window_id"):
        result["_user_message_stored"] = True

    return result


def should_continue(state: ImperatorState) -> str:
    """Conditional edge: route to tool_node if tool calls, else store nodes.

    ARCH-05: Flow control is graph edges, not loops in nodes.
    Enforces imperator_max_iterations to prevent unbounded ReAct loops.
    """
    if state.get("error"):
        return "store_user_message"

    messages = state["messages"]
    if not messages:
        return "store_user_message"

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        max_iterations = get_tuning(
            state.get("config", {}), "imperator_max_iterations", 5
        )
        if state.get("iteration_count", 0) >= max_iterations:
            _log.warning(
                "Imperator hit max iterations (%d) — forcing end",
                max_iterations,
            )
            return "max_iterations_fallback"
        return "tool_node"

    return "store_user_message"


async def max_iterations_fallback(state: ImperatorState) -> dict:
    """Inject a fallback text response when max iterations is reached.

    Without this, the last message is an AIMessage with tool_calls but no
    text content, causing the chat endpoint to return an empty response.
    """
    return {
        "messages": [
            AIMessage(
                content=(
                    "I was unable to complete that request within the allowed "
                    "number of steps. Please try again, or break the request "
                    "into smaller parts."
                )
            )
        ]
    }


async def store_user_message(state: ImperatorState) -> dict:
    """Persist the user's message via the store_message core tool.

    D-01: Split into separate node for MemorySaver compatibility.
    D-07: Uses dispatch_tool("store_message") — self-consumption.
    Uses hostname as recipient, user field from config as sender.

    V2: Skips storage if the user message was already stored by get_context
    (when query parameter was passed). Prevents duplicate messages.
    """
    conversation_id = state.get("context_window_id")
    if not conversation_id:
        return {}

    # V2: If get_context was called with query, the user message
    # was already stored inside retrieve_context_node. Skip.
    if state.get("_user_message_stored"):
        return {}

    user_content = None
    for msg in state["messages"]:
        if isinstance(msg, HumanMessage):
            user_content = msg.content

    if not user_content:
        return {}

    # Sender is whoever sent the message to the Imperator
    user_identity = (
        state.get("config", {}).get("imperator", {}).get("_request_user", "unknown")
    )

    try:
        from app.flows.tool_dispatch import dispatch_tool

        await dispatch_tool(
            "store_message",
            {
                "conversation_id": str(conversation_id),
                "role": "user",
                "sender": user_identity,
                "recipient": _MAD_HOSTNAME,
                "content": user_content,
            },
            state.get("config", {}),
            None,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        _log.warning("Failed to store Imperator user message: %s", exc)

    return {}


async def store_assistant_message(state: ImperatorState) -> dict:
    """Persist the assistant's response via the store_message core tool.

    D-01: Second persistence node.
    D-07: Uses dispatch_tool("store_message") — self-consumption.
    Uses hostname as sender, user field from config as recipient.
    """
    last_ai = None
    for msg in reversed(state["messages"]):
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            last_ai = msg
            break

    response_text = last_ai.content if last_ai else ""

    # Guard against empty responses — LLM provider errors, rate limits, or
    # tool-call-only final turns can produce AIMessages with empty content.
    if not response_text.strip():
        # Log the full message list to diagnose WHY it's empty
        msg_summary = []
        for i, msg in enumerate(state["messages"]):
            msg_type = type(msg).__name__
            has_tool_calls = bool(getattr(msg, "tool_calls", None))
            content_len = len(msg.content) if msg.content else 0
            content_preview = (msg.content[:80] if msg.content else "None")
            msg_summary.append(
                f"  [{i}] {msg_type}: content_len={content_len}, "
                f"tool_calls={has_tool_calls}, preview={content_preview!r}"
            )
        _log.warning(
            "Imperator produced empty response. Message chain (%d messages):\n%s",
            len(state["messages"]),
            "\n".join(msg_summary),
        )
        response_text = (
            "I was unable to generate a response. This may be due to a "
            "temporary provider issue. Please try again."
        )

    conversation_id = state.get("context_window_id")
    user_identity = (
        state.get("config", {}).get("imperator", {}).get("_request_user", "unknown")
    )

    if conversation_id and response_text:
        try:
            from app.flows.tool_dispatch import dispatch_tool

            await dispatch_tool(
                "store_message",
                {
                    "conversation_id": str(conversation_id),
                    "role": "assistant",
                    "sender": _MAD_HOSTNAME,
                    "recipient": user_identity,
                    "content": response_text,
                },
                state.get("config", {}),
                None,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            _log.warning("Failed to store Imperator assistant message: %s", exc)

    return {"response_text": response_text}


# ── Build the graph ──────────────────────────────────────────────────────


def build_imperator_flow(config: dict | None = None) -> StateGraph:
    """Build and compile the Imperator StateGraph.

    ARCH-05: Proper graph structure with agent_node <-> tool_node loop
             via conditional edges.  No while loops inside nodes.
    D-01:    MemorySaver checkpointer for graph execution state.
    F-22:    Results stored via conv_store_message in store_and_end.

    Tools are discovered from tools/ modules via get_tools().
    """
    # R6-M14: Build the ToolNode with only the tools that match the config.
    if config is None:
        from app.config import load_merged_config

        config = load_merged_config()
    imperator_config = config.get("imperator", {})
    active_tools = _collect_tools(imperator_config)
    tool_node_instance = ToolNode(active_tools)

    # R7-m14: Pre-bind tools to the LLM at graph compilation time
    global _prebound_llm
    llm = get_chat_model(config, role="imperator")
    _prebound_llm = llm.bind_tools(active_tools)

    workflow = StateGraph(ImperatorState)

    workflow.add_node("agent_node", agent_node)
    workflow.add_node("tool_node", tool_node_instance)
    workflow.add_node("max_iterations_fallback", max_iterations_fallback)
    workflow.add_node("store_user_message", store_user_message)
    workflow.add_node("store_assistant_message", store_assistant_message)

    workflow.set_entry_point("agent_node")

    workflow.add_conditional_edges(
        "agent_node",
        should_continue,
        {
            "tool_node": "tool_node",
            "max_iterations_fallback": "max_iterations_fallback",
            "store_user_message": "store_user_message",
        },
    )

    workflow.add_edge("tool_node", "agent_node")
    workflow.add_edge("max_iterations_fallback", "store_user_message")
    workflow.add_edge("store_user_message", "store_assistant_message")
    workflow.add_edge("store_assistant_message", END)

    from langgraph.checkpoint.memory import MemorySaver

    return workflow.compile(checkpointer=MemorySaver())


# Metrics wrappers (invoke_with_metrics, astream_events_with_metrics) live
# in the kernel at app/flows/imperator_wrapper.py — they are AE-side
# instrumentation concerns, not TE logic.
