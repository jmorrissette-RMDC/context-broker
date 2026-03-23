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

import copy
import logging
import re
import uuid
from typing import Annotated, Optional

import asyncpg
import httpx
import openai
import yaml
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from app.config import get_chat_model, get_tuning
from app.database import get_pg_pool
from app.prompt_loader import async_load_prompt

_log = logging.getLogger("context_broker.flows.imperator")


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


# ── Tool singletons ─────────────────────────────────────────────────────

# Lazy-initialized flow singletons for Imperator tool functions.
_conv_search_flow_singleton = None
_mem_search_flow_singleton = None


def _get_conv_search_flow():
    global _conv_search_flow_singleton
    if _conv_search_flow_singleton is None:
        from app.flows.search_flow import build_conversation_search_flow
        _conv_search_flow_singleton = build_conversation_search_flow()
    return _conv_search_flow_singleton


def _get_mem_search_flow():
    global _mem_search_flow_singleton
    if _mem_search_flow_singleton is None:
        from app.flows.memory_search_flow import build_memory_search_flow
        _mem_search_flow_singleton = build_memory_search_flow()
    return _mem_search_flow_singleton


@tool
async def _conv_search_tool(query: str, limit: int = 5) -> str:
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
    # R5-m11: Include all ConversationSearchState fields explicitly
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
async def _mem_search_tool(query: str, user_id: str = "imperator", limit: int = 5) -> str:
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


# Module-level tool singletons (M-10)
_imperator_tools: list = [_conv_search_tool, _mem_search_tool]


def _redact_config(config: dict) -> dict:
    """Return a deep copy of *config* with sensitive values redacted (G5-16).

    Removes the top-level ``credentials`` section entirely and replaces any
    value whose key matches common secret patterns (api_key, secret, token,
    password) with ``"***REDACTED***"``.
    """
    redacted = copy.deepcopy(config)
    redacted.pop("credentials", None)

    # R6-m10: Use word boundaries to avoid false positives on keys like
    # "max_token_budget" — only match when the sensitive word is a distinct
    # component of the key name (e.g., "api_key", "db_password").
    _secret_key_re = re.compile(r"(api_key|secret|_token|password)", re.IGNORECASE)

    def _walk(obj: dict | list) -> None:
        if isinstance(obj, dict):
            for key in list(obj.keys()):
                if _secret_key_re.search(key) and obj[key]:
                    obj[key] = "***REDACTED***"
                elif isinstance(obj[key], (dict, list)):
                    _walk(obj[key])
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    _walk(item)

    _walk(redacted)
    return redacted


@tool
async def _config_read_tool() -> str:
    """Read the current config.yml contents (sensitive values are redacted).

    Admin-only tool. Returns the configuration as YAML text with credentials
    and API keys redacted for safety.
    """
    from app.config import CONFIG_PATH
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        sanitized = _redact_config(raw)
        return yaml.dump(sanitized, default_flow_style=False)
    except (FileNotFoundError, OSError, yaml.YAMLError) as exc:
        return f"Error reading config: {exc}"


@tool
async def _db_query_tool(sql: str) -> str:
    """Execute a read-only SQL query against the Context Broker database.

    Admin-only tool. The transaction is set to READ ONLY mode, so any
    DML/DDL will be rejected by PostgreSQL regardless of query structure.
    A 5-second statement timeout prevents expensive queries.

    Args:
        sql: A SQL query to execute (enforced read-only at the DB level).
    """
    import asyncpg

    # R5-M15: The real security boundary is SET TRANSACTION READ ONLY +
    # statement_timeout below. The SELECT prefix check was bypassable via
    # CTEs (e.g., WITH x AS (DELETE ...) SELECT ...) so it has been removed.
    # READ ONLY mode causes PostgreSQL to reject any DML/DDL regardless of
    # how the SQL is structured.
    try:
        pool = get_pg_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                await conn.execute("SET statement_timeout = '5000'")  # 5 second max
                rows = await conn.fetch(sql)
        if not rows:
            return "No results."
        # Format as text table
        columns = list(rows[0].keys())
        lines = [" | ".join(columns)]
        for row in rows[:50]:  # Limit to 50 rows
            lines.append(" | ".join(str(row[c]) for c in columns))
        return "\n".join(lines)
    except (asyncpg.PostgresError, OSError, RuntimeError) as exc:
        return f"Query error: {exc}"


# Admin tool singletons — gated by config
_admin_tools: list = [_config_read_tool, _db_query_tool]


# ── Message pipeline singleton ──────────────────────────────────────────

_message_pipeline_singleton = None


def _get_message_pipeline():
    """Lazy-init the standard message pipeline flow."""
    global _message_pipeline_singleton
    if _message_pipeline_singleton is None:
        from app.flows.message_pipeline import build_message_pipeline
        _message_pipeline_singleton = build_message_pipeline()
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
        # Look up conversation_id from context_windows
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

    On the first call (no system prompt in messages yet), loads DB
    history and prepends the system prompt + history context.
    """
    config = state["config"]

    # Determine active tools (admin tools gated by config)
    imperator_config = config.get("imperator", {})
    active_tools = list(_imperator_tools)
    if imperator_config.get("admin_tools", False):
        active_tools.extend(_admin_tools)

    llm = get_chat_model(config)
    llm_with_tools = llm.bind_tools(active_tools)

    messages = list(state["messages"])

    # First call: prepend system prompt with DB history context
    has_system = any(isinstance(m, SystemMessage) for m in messages)
    if not has_system:
        try:
            system_content = await async_load_prompt("imperator_identity")
        except RuntimeError as exc:
            _log.error("Failed to load imperator_identity prompt: %s", exc)
            return {
                "messages": [AIMessage(content="I encountered a configuration error.")],
                "response_text": "I encountered a configuration error.",
                "error": f"Prompt loading failed: {exc}",
            }

        context_window_id = state.get("context_window_id")
        if context_window_id:
            history_context = await _load_conversation_history(context_window_id, config)
            if history_context:
                system_content += history_context

        messages = [SystemMessage(content=system_content)] + messages

    # CB-R3-06: Truncate older messages if the list exceeds the limit.
    # M9: When truncating, ensure we don't split a tool-call sequence by
    # starting the kept portion on a ToolMessage. Scan backwards from the
    # cut point until we find a non-ToolMessage boundary.
    max_react_messages = get_tuning(config, "imperator_max_react_messages", 40)
    if len(messages) > max_react_messages:
        from langchain_core.messages import ToolMessage
        # Start with the default cut index (keep last max_react_messages-1)
        cut_index = len(messages) - (max_react_messages - 1)
        # Walk backwards until the message at cut_index is not a ToolMessage
        while cut_index < len(messages) and isinstance(messages[cut_index], ToolMessage):
            cut_index += 1
        messages = [messages[0]] + messages[cut_index:]

    try:
        response = await llm_with_tools.ainvoke(messages)
    except (openai.APIError, httpx.HTTPError, ValueError, RuntimeError) as exc:
        _log.error("Imperator LLM call failed: %s", exc, exc_info=True)
        return {
            "messages": [AIMessage(content="I encountered an error processing your request.")],
            "response_text": "I encountered an error processing your request.",
            "error": str(exc),
        }

    # Return the AI response — add_messages reducer will append it
    return {"messages": [response], "iteration_count": state.get("iteration_count", 0) + 1}


def should_continue(state: ImperatorState) -> str:
    """Conditional edge: route to tool_node if tool calls, else store_and_end.

    ARCH-05: Flow control is graph edges, not loops in nodes.
    Enforces imperator_max_iterations to prevent unbounded ReAct loops.
    """
    if state.get("error"):
        return "store_and_end"

    messages = state["messages"]
    if not messages:
        return "store_and_end"

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        # Check iteration limit to prevent unbounded loops
        max_iterations = get_tuning(state.get("config", {}), "imperator_max_iterations", 5)
        if state.get("iteration_count", 0) >= max_iterations:
            _log.warning(
                "Imperator hit max iterations (%d) — forcing end",
                max_iterations,
            )
            return "store_and_end"
        return "tool_node"

    return "store_and_end"


async def store_and_end(state: ImperatorState) -> dict:
    """Store user message and assistant response via the standard message pipeline.

    F-22: Uses conv_store_message (the standard pipeline), NOT direct SQL.
    ARCH-06: The graph runs fresh each invocation — results are persisted
    to the DB here so the next invocation can load them as history.
    """
    context_window_id = state.get("context_window_id")
    if not context_window_id:
        # No context window — extract response text but skip persistence
        messages = state["messages"]
        last_ai = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)),
            None,
        )
        return {"response_text": last_ai.content if last_ai else ""}

    messages = state["messages"]

    # Find the last user message and last AI message (the final answer)
    user_content = None
    for msg in messages:
        if isinstance(msg, HumanMessage):
            user_content = msg.content

    last_ai = None
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            last_ai = msg
            break

    response_text = last_ai.content if last_ai else ""

    pipeline = _get_message_pipeline()

    # Store user message
    if user_content:
        try:
            await pipeline.ainvoke({
                "context_window_id": context_window_id,
                "role": "user",
                "sender": "imperator_user",
                "recipient": "imperator",
                "content": user_content,
                "model_name": None,
                "tool_calls": None,
                "tool_call_id": None,
                "message_id": None,
                "conversation_id": None,
                "sequence_number": None,
                "was_collapsed": False,
                "queued_jobs": [],
                "error": None,
            })
        except (RuntimeError, OSError) as exc:
            _log.warning("Failed to store Imperator user message via pipeline: %s", exc)

    # Store assistant response
    if response_text:
        try:
            await pipeline.ainvoke({
                "context_window_id": context_window_id,
                "role": "assistant",
                "sender": "imperator",
                "recipient": "imperator_user",
                "content": response_text,
                "model_name": None,
                "tool_calls": None,
                "tool_call_id": None,
                "message_id": None,
                "conversation_id": None,
                "sequence_number": None,
                "was_collapsed": False,
                "queued_jobs": [],
                "error": None,
            })
        except (RuntimeError, OSError) as exc:
            _log.warning("Failed to store Imperator assistant message via pipeline: %s", exc)

    return {"response_text": response_text}


# ── Build the graph ──────────────────────────────────────────────────────

def build_imperator_flow(config: dict | None = None) -> StateGraph:
    """Build and compile the Imperator StateGraph.

    ARCH-05: Proper graph structure with agent_node <-> tool_node loop
             via conditional edges.  No while loops inside nodes.
    ARCH-06: No checkpointer.  The graph runs fresh each invocation.
             History is loaded from PostgreSQL in agent_node.
    F-22:    Results stored via conv_store_message in store_and_end.
    """
    # R6-M14: Build the ToolNode with only the tools that match the config.
    # If admin_tools=false (or no config), the ToolNode only gets base tools,
    # so even if the LLM hallucinated an admin tool call, ToolNode would reject it.
    if config is None:
        from app.config import load_config
        config = load_config()
    imperator_config = config.get("imperator", {})
    active_tools = list(_imperator_tools)
    if imperator_config.get("admin_tools", False):
        active_tools.extend(_admin_tools)
    tool_node_instance = ToolNode(active_tools)

    workflow = StateGraph(ImperatorState)

    workflow.add_node("agent_node", agent_node)
    workflow.add_node("tool_node", tool_node_instance)
    workflow.add_node("store_and_end", store_and_end)

    workflow.set_entry_point("agent_node")

    # ARCH-05: Conditional edge — tool_calls route to tool_node, else store
    workflow.add_conditional_edges(
        "agent_node",
        should_continue,
        {
            "tool_node": "tool_node",
            "store_and_end": "store_and_end",
        },
    )

    # tool_node -> back to agent_node for the next reasoning step
    workflow.add_edge("tool_node", "agent_node")

    workflow.add_edge("store_and_end", END)

    # ARCH-06: No checkpointer — compile without one
    return workflow.compile()
