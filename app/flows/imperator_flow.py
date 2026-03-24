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
async def _mem_search_tool(
    query: str, user_id: str = "imperator", limit: int = 5
) -> str:
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

    llm = get_chat_model(config, role="imperator")
    llm_with_tools = llm.bind_tools(active_tools)

    messages = list(state["messages"])

    # First call: prepend system prompt with DB history context
    # PG-13: System prompt contains Identity, Purpose, Persona (REQ-001 §11.2)
    # The prompt name is configured in te.yml; defaults to "imperator_identity"
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

        # D-07: Load context via the get_context core tool (self-consumption).
        # The Imperator uses the same tool interface any external agent would.
        conversation_id = state.get("context_window_id")  # legacy name in state
        if conversation_id:
            try:
                from app.flows.tool_dispatch import dispatch_tool

                imperator_cfg = config.get("imperator", {})
                build_type = imperator_cfg.get("build_type", "standard-tiered")
                budget = imperator_cfg.get("max_context_tokens", 8192)
                if not isinstance(budget, int):
                    budget = 8192

                ctx_result = await dispatch_tool(
                    "get_context",
                    {
                        "build_type": build_type,
                        "budget": budget,
                        "conversation_id": str(conversation_id),
                    },
                    config,
                    None,
                )
                context_messages = ctx_result.get("context", [])
                if context_messages:
                    # Format context messages as history text for the system prompt
                    history_lines = []
                    for msg in context_messages:
                        role = msg.get("role", "unknown")
                        content = msg.get("content", "")
                        if content:
                            history_lines.append(f"[{role}] {content}")
                    if history_lines:
                        system_content += (
                            "\n\n--- Conversation History ---\n"
                            + "\n".join(history_lines)
                        )
            except (ValueError, RuntimeError, OSError) as exc:
                _log.warning("Failed to load context via get_context: %s", exc)

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
        while cut_index < len(messages) and isinstance(
            messages[cut_index], ToolMessage
        ):
            cut_index += 1
        messages = [messages[0]] + messages[cut_index:]

    try:
        response = await llm_with_tools.ainvoke(messages)
    except (openai.APIError, httpx.HTTPError, ValueError, RuntimeError) as exc:
        _log.error("Imperator LLM call failed: %s", exc, exc_info=True)
        return {
            "messages": [
                AIMessage(content="I encountered an error processing your request.")
            ],
            "response_text": "I encountered an error processing your request.",
            "error": str(exc),
        }

    # Return the AI response — add_messages reducer will append it
    return {
        "messages": [response],
        "iteration_count": state.get("iteration_count", 0) + 1,
    }


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
        # Check iteration limit to prevent unbounded loops
        max_iterations = get_tuning(
            state.get("config", {}), "imperator_max_iterations", 5
        )
        if state.get("iteration_count", 0) >= max_iterations:
            _log.warning(
                "Imperator hit max iterations (%d) — forcing end",
                max_iterations,
            )
            return "store_user_message"
        return "tool_node"

    return "store_user_message"


async def store_user_message(state: ImperatorState) -> dict:
    """Persist the user's message via the store_message core tool.

    D-01: Split into separate node for MemorySaver compatibility.
    D-07: Uses dispatch_tool("store_message") — self-consumption.
    """
    conversation_id = state.get("context_window_id")  # legacy name in state
    if not conversation_id:
        return {}

    user_content = None
    for msg in state["messages"]:
        if isinstance(msg, HumanMessage):
            user_content = msg.content

    if not user_content:
        return {}

    try:
        from app.flows.tool_dispatch import dispatch_tool

        await dispatch_tool(
            "store_message",
            {
                "conversation_id": str(conversation_id),
                "role": "user",
                "sender": "imperator_user",
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
    """
    last_ai = None
    for msg in reversed(state["messages"]):
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            last_ai = msg
            break

    response_text = last_ai.content if last_ai else ""

    conversation_id = state.get("context_window_id")  # legacy name in state
    if conversation_id and response_text:
        try:
            from app.flows.tool_dispatch import dispatch_tool

            await dispatch_tool(
                "store_message",
                {
                    "conversation_id": str(conversation_id),
                    "role": "assistant",
                    "sender": "imperator",
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
    D-01:    MemorySaver checkpointer for graph execution state —
             interrupt/resume, tool call tracking, mid-execution persistence.
             Long-term conversation persistence handled by Context Broker pipeline.
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
    workflow.add_node("store_user_message", store_user_message)
    workflow.add_node("store_assistant_message", store_assistant_message)

    workflow.set_entry_point("agent_node")

    # ARCH-05: Conditional edge — tool_calls route to tool_node, else persist
    workflow.add_conditional_edges(
        "agent_node",
        should_continue,
        {
            "tool_node": "tool_node",
            "store_user_message": "store_user_message",
        },
    )

    # tool_node -> back to agent_node for the next reasoning step
    workflow.add_edge("tool_node", "agent_node")

    # D-01: Split persistence into separate nodes (one subgraph per node)
    workflow.add_edge("store_user_message", "store_assistant_message")
    workflow.add_edge("store_assistant_message", END)

    # D-01: MemorySaver for graph execution state (interrupt/resume, tool tracking).
    # No infrastructure dependency — works for eMADs and standalone TEs.
    from langgraph.checkpoint.memory import MemorySaver

    return workflow.compile(checkpointer=MemorySaver())
