"""
Conversation Operations — LangGraph StateGraph flows for CRUD operations.

Handles: conv_create_conversation, conv_create_context_window,
conv_get_history, conv_search_context_windows.

These are straightforward database operations wrapped in StateGraphs
per the LangGraph mandate (REQ §4.5).
"""

import logging
import uuid
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_build_type_config
from app.database import get_pg_pool
from app.token_budget import resolve_token_budget

_log = logging.getLogger("context_broker.flows.conversation_ops")


# ============================================================
# Create Conversation Flow
# ============================================================


class CreateConversationState(TypedDict):
    """State for conversation creation."""

    conversation_id: Optional[str]
    title: Optional[str]
    flow_id: Optional[str]
    user_id: Optional[str]
    error: Optional[str]


async def create_conversation_node(state: CreateConversationState) -> dict:
    """Insert a new conversation record into PostgreSQL.

    Supports caller-supplied IDs (F-13) via ON CONFLICT DO NOTHING
    for idempotent create-or-return.
    """
    pool = get_pg_pool()

    # F-13: Use caller-supplied ID if provided, otherwise generate one
    if state.get("conversation_id"):
        try:
            new_id = uuid.UUID(state["conversation_id"])
        except ValueError:
            return {"error": f"Invalid conversation_id format: {state['conversation_id']}"}
    else:
        new_id = uuid.uuid4()

    row = await pool.fetchrow(
        """
        INSERT INTO conversations (id, title, flow_id, user_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (id) DO NOTHING
        RETURNING id, title, created_at, updated_at, total_messages, estimated_token_count
        """,
        new_id,
        state.get("title"),
        state.get("flow_id"),
        state.get("user_id"),
    )

    if row is None:
        # Already exists — return existing conversation ID
        return {"conversation_id": str(new_id)}

    return {"conversation_id": str(row["id"])}


def build_create_conversation_flow() -> StateGraph:
    """Build and compile the create conversation StateGraph."""
    workflow = StateGraph(CreateConversationState)
    workflow.add_node("create_conversation_node", create_conversation_node)
    workflow.set_entry_point("create_conversation_node")
    workflow.add_edge("create_conversation_node", END)
    return workflow.compile()


# ============================================================
# Create Context Window Flow
# ============================================================


class CreateContextWindowState(TypedDict):
    """State for context window creation."""

    conversation_id: str
    participant_id: str
    build_type: str
    max_tokens_override: Optional[int]
    config: dict

    context_window_id: Optional[str]
    build_type_config: Optional[dict]
    resolved_token_budget: int
    error: Optional[str]


async def resolve_token_budget_node(state: CreateContextWindowState) -> dict:
    """Resolve the token budget for the new context window."""
    config = state["config"]

    try:
        build_type_config = get_build_type_config(config, state["build_type"])
    except ValueError as exc:
        return {"error": str(exc)}

    token_budget = await resolve_token_budget(
        config=config,
        build_type_config=build_type_config,
        caller_override=state.get("max_tokens_override"),
    )

    return {"resolved_token_budget": token_budget, "build_type_config": build_type_config}


async def create_context_window_node(state: CreateContextWindowState) -> dict:
    """Insert the context window record into PostgreSQL."""
    if state.get("error"):
        return {}

    pool = get_pg_pool()

    # Verify conversation exists
    conversation = await pool.fetchrow(
        "SELECT id FROM conversations WHERE id = $1",
        uuid.UUID(state["conversation_id"]),
    )
    if conversation is None:
        return {"error": f"Conversation {state['conversation_id']} not found"}

    # G5-08: Idempotent creation — use ON CONFLICT DO NOTHING on the
    # unique constraint (conversation_id, participant_id, build_type).
    # If the window already exists, return the existing ID.
    row = await pool.fetchrow(
        """
        INSERT INTO context_windows
            (conversation_id, participant_id, build_type, max_token_budget)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (conversation_id, participant_id, build_type) DO NOTHING
        RETURNING id, conversation_id, participant_id, build_type, max_token_budget, created_at
        """,
        uuid.UUID(state["conversation_id"]),
        state["participant_id"],
        state["build_type"],
        state["resolved_token_budget"],
    )

    if row is None:
        # Already exists — look up the existing window
        existing = await pool.fetchrow(
            """
            SELECT id FROM context_windows
            WHERE conversation_id = $1 AND participant_id = $2 AND build_type = $3
            """,
            uuid.UUID(state["conversation_id"]),
            state["participant_id"],
            state["build_type"],
        )
        # R5-M21: Defensive None check — should not happen but prevents crash
        if existing is None:
            return {"error": "Context window conflict: INSERT returned nothing and existing row not found"}
        return {"context_window_id": str(existing["id"])}

    return {"context_window_id": str(row["id"])}


def route_after_resolve_budget(state: CreateContextWindowState) -> str:
    """Route: if error, end. Otherwise create the window."""
    if state.get("error"):
        return END
    return "create_context_window_node"


def build_create_context_window_flow() -> StateGraph:
    """Build and compile the create context window StateGraph."""
    workflow = StateGraph(CreateContextWindowState)

    workflow.add_node("resolve_token_budget_node", resolve_token_budget_node)
    workflow.add_node("create_context_window_node", create_context_window_node)

    workflow.set_entry_point("resolve_token_budget_node")

    workflow.add_conditional_edges(
        "resolve_token_budget_node",
        route_after_resolve_budget,
        {"create_context_window_node": "create_context_window_node", END: END},
    )

    workflow.add_edge("create_context_window_node", END)
    return workflow.compile()


# ============================================================
# Get History Flow
# ============================================================


class GetHistoryState(TypedDict):
    """State for conversation history retrieval."""

    conversation_id: str
    limit: Optional[int]

    conversation: Optional[dict]
    messages: list[dict]
    error: Optional[str]


async def load_conversation_and_messages(state: GetHistoryState) -> dict:
    """Load conversation metadata and all messages in chronological order."""
    pool = get_pg_pool()

    conversation = await pool.fetchrow(
        "SELECT id, title, created_at, updated_at, total_messages, estimated_token_count FROM conversations WHERE id = $1",
        uuid.UUID(state["conversation_id"]),
    )
    if conversation is None:
        return {"error": f"Conversation {state['conversation_id']} not found"}

    conv_dict = dict(conversation)
    conv_dict["id"] = str(conv_dict["id"])
    if conv_dict.get("created_at"):
        conv_dict["created_at"] = conv_dict["created_at"].isoformat()
    if conv_dict.get("updated_at"):
        conv_dict["updated_at"] = conv_dict["updated_at"].isoformat()

    limit = state.get("limit")
    if limit:
        # G5-09: Use a subquery to get the most recent N messages (not oldest N),
        # then re-sort in chronological order for the caller.
        rows = await pool.fetch(
            """
            SELECT * FROM (
                SELECT id, role, sender, recipient, content, sequence_number,
                       token_count, model_name, created_at
                FROM conversation_messages
                WHERE conversation_id = $1
                ORDER BY sequence_number DESC
                LIMIT $2
            ) sub
            ORDER BY sequence_number ASC
            """,
            uuid.UUID(state["conversation_id"]),
            limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, role, sender, recipient, content, sequence_number,
                   token_count, model_name, created_at
            FROM conversation_messages
            WHERE conversation_id = $1
            ORDER BY sequence_number ASC
            """,
            uuid.UUID(state["conversation_id"]),
        )

    messages = []
    for row in rows:
        m = dict(row)
        m["id"] = str(m["id"])
        if m.get("created_at"):
            m["created_at"] = m["created_at"].isoformat()
        messages.append(m)

    return {"conversation": conv_dict, "messages": messages}


def build_get_history_flow() -> StateGraph:
    """Build and compile the get history StateGraph."""
    workflow = StateGraph(GetHistoryState)
    workflow.add_node("load_conversation_and_messages", load_conversation_and_messages)
    workflow.set_entry_point("load_conversation_and_messages")
    workflow.add_edge("load_conversation_and_messages", END)
    return workflow.compile()


# ============================================================
# Search Context Windows Flow
# ============================================================


class SearchContextWindowsState(TypedDict):
    """State for context window search."""

    context_window_id: Optional[str]
    conversation_id: Optional[str]
    participant_id: Optional[str]
    build_type: Optional[str]
    limit: int

    results: list[dict]
    error: Optional[str]


async def search_context_windows_node(state: SearchContextWindowsState) -> dict:
    """Search context windows by various filters.

    If context_window_id is provided, look up that specific window directly
    (M-20), bypassing other filters.
    """
    pool = get_pg_pool()

    # M-20: Direct lookup by context_window_id if provided
    if state.get("context_window_id"):
        row = await pool.fetchrow(
            """
            SELECT id, conversation_id, participant_id, build_type,
                   max_token_budget, last_assembled_at, created_at
            FROM context_windows
            WHERE id = $1
            """,
            uuid.UUID(state["context_window_id"]),
        )
        if row is None:
            return {"results": []}
        r = dict(row)
        r["id"] = str(r["id"])
        r["conversation_id"] = str(r["conversation_id"])
        if r.get("last_assembled_at"):
            r["last_assembled_at"] = r["last_assembled_at"].isoformat()
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
        return {"results": [r]}

    conditions = []
    args: list = []
    arg_idx = 1

    if state.get("conversation_id"):
        conditions.append(f"conversation_id = ${arg_idx}")
        args.append(uuid.UUID(state["conversation_id"]))
        arg_idx += 1

    if state.get("participant_id"):
        conditions.append(f"participant_id = ${arg_idx}")
        args.append(state["participant_id"])
        arg_idx += 1

    if state.get("build_type"):
        conditions.append(f"build_type = ${arg_idx}")
        args.append(state["build_type"])
        arg_idx += 1

    # Safety note: where_clause is built from fixed column-name strings above
    # (never from user input), so f-string interpolation is safe here.
    # The actual filter values are passed as bind parameters ($1, $2, etc.).
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    args.append(state["limit"])

    rows = await pool.fetch(
        f"""
        SELECT id, conversation_id, participant_id, build_type,
               max_token_budget, last_assembled_at, created_at
        FROM context_windows
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT ${arg_idx}
        """,
        *args,
    )

    results = []
    for row in rows:
        r = dict(row)
        r["id"] = str(r["id"])
        r["conversation_id"] = str(r["conversation_id"])
        if r.get("last_assembled_at"):
            r["last_assembled_at"] = r["last_assembled_at"].isoformat()
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
        results.append(r)

    return {"results": results}


def build_search_context_windows_flow() -> StateGraph:
    """Build and compile the search context windows StateGraph."""
    workflow = StateGraph(SearchContextWindowsState)
    workflow.add_node("search_context_windows_node", search_context_windows_node)
    workflow.set_entry_point("search_context_windows_node")
    workflow.add_edge("search_context_windows_node", END)
    return workflow.compile()
