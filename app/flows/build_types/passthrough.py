"""
Passthrough build type (ARCH-18).

Minimal build type that demonstrates the contract:
- Assembly: no-op, just updates last_assembled_at.
- Retrieval: loads recent messages as-is and returns them.

No LLM calls, no summarization, no tier logic.
"""

import logging
import operator
import time
import uuid
from typing import Annotated, Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_tuning, verbose_log
from app.database import get_pg_pool, get_redis
from app.flows.build_type_registry import register_build_type
from app.metrics_registry import CONTEXT_ASSEMBLY_DURATION

_log = logging.getLogger("context_broker.flows.build_types.passthrough")


# ============================================================
# Assembly
# ============================================================


class PassthroughAssemblyState(TypedDict):
    """State for passthrough assembly — minimal."""

    context_window_id: str
    conversation_id: str
    config: dict

    # Lock management
    lock_key: str
    lock_token: Optional[str]
    lock_acquired: bool
    assembly_start_time: Optional[float]

    # Output
    error: Optional[str]


async def pt_acquire_lock(state: PassthroughAssemblyState) -> dict:
    """Acquire Redis assembly lock."""
    # R5-M11: Validate UUID at assembly entry point to fail gracefully
    try:
        uuid.UUID(state["context_window_id"])
        uuid.UUID(state["conversation_id"])
    except (ValueError, AttributeError):
        _log.error(
            "Invalid UUID in assembly input: context_window_id=%s, conversation_id=%s",
            state.get("context_window_id"),
            state.get("conversation_id"),
        )
        return {"error": "Invalid UUID in assembly input", "lock_acquired": False}

    verbose_log(
        state["config"],
        _log,
        "passthrough.acquire_lock ENTER window=%s",
        state["context_window_id"],
    )
    lock_key = f"assembly_in_progress:{state['context_window_id']}"
    lock_token = str(uuid.uuid4())
    redis = get_redis()

    acquired = await redis.set(
        lock_key,
        lock_token,
        ex=get_tuning(state["config"], "assembly_lock_ttl_seconds", 300),
        nx=True,
    )
    if not acquired:
        _log.info(
            "Passthrough assembly: lock not acquired for window=%s — skipping",
            state["context_window_id"],
        )
        return {"lock_key": lock_key, "lock_token": None, "lock_acquired": False}

    return {
        "lock_key": lock_key,
        "lock_token": lock_token,
        "lock_acquired": True,
        "assembly_start_time": time.monotonic(),
    }


async def pt_finalize(state: PassthroughAssemblyState) -> dict:
    """No-op assembly: just update last_assembled_at.

    R6-M13: Wrapped in try/except so errors route to pt_release_lock
    instead of raising and leaking the lock.
    """
    try:
        pool = get_pg_pool()
        await pool.execute(
            "UPDATE context_windows SET last_assembled_at = NOW() WHERE id = $1",
            uuid.UUID(state["context_window_id"]),
        )

        start_time = state.get("assembly_start_time")
        if start_time is not None:
            duration = time.monotonic() - start_time
            CONTEXT_ASSEMBLY_DURATION.labels(build_type="passthrough").observe(duration)

        _log.info(
            "Passthrough assembly complete for window=%s", state["context_window_id"]
        )
        return {}
    except (RuntimeError, OSError, Exception) as exc:
        _log.error(
            "Passthrough finalize failed for window=%s: %s",
            state["context_window_id"],
            exc,
        )
        return {"error": f"Passthrough finalize failed: {exc}"}


async def pt_release_lock(state: PassthroughAssemblyState) -> dict:
    """Release Redis assembly lock."""
    lock_key = state.get("lock_key", "")
    lock_token = state.get("lock_token")
    if lock_key and state.get("lock_acquired") and lock_token:
        redis = get_redis()
        lua_script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """
        await redis.eval(lua_script, 1, lock_key, lock_token)
    return {}


def pt_route_after_lock(state: PassthroughAssemblyState) -> str:
    if not state.get("lock_acquired"):
        return END
    return "pt_finalize"


def pt_route_after_finalize(state: PassthroughAssemblyState) -> str:
    """R6-M13: Route to lock release regardless of error state."""
    return "pt_release_lock"


def build_passthrough_assembly():
    """Build and compile the passthrough assembly StateGraph."""
    workflow = StateGraph(PassthroughAssemblyState)

    workflow.add_node("pt_acquire_lock", pt_acquire_lock)
    workflow.add_node("pt_finalize", pt_finalize)
    workflow.add_node("pt_release_lock", pt_release_lock)

    workflow.set_entry_point("pt_acquire_lock")
    workflow.add_conditional_edges(
        "pt_acquire_lock",
        pt_route_after_lock,
        {"pt_finalize": "pt_finalize", END: END},
    )
    # R6-M13: Use conditional edge from pt_finalize so that errors
    # route to pt_release_lock instead of leaking the lock.
    workflow.add_conditional_edges(
        "pt_finalize",
        pt_route_after_finalize,
        {"pt_release_lock": "pt_release_lock"},
    )
    workflow.add_edge("pt_release_lock", END)

    return workflow.compile()


# ============================================================
# Retrieval
# ============================================================


class PassthroughRetrievalState(TypedDict):
    """State for passthrough retrieval."""

    context_window_id: str
    config: dict

    # Intermediate
    window: Optional[dict]
    conversation_id: Optional[str]
    max_token_budget: int

    # Output
    context_messages: Optional[list[dict]]
    context_tiers: Optional[dict]
    total_tokens_used: int
    warnings: Annotated[list[str], operator.add]  # R5-m5: accumulate, don't overwrite
    error: Optional[str]


async def pt_load_window(state: PassthroughRetrievalState) -> dict:
    """Load the context window."""
    # R5-M11: Validate UUID at retrieval entry point to fail gracefully
    try:
        uuid.UUID(state["context_window_id"])
    except (ValueError, AttributeError):
        _log.error(
            "Invalid UUID in retrieval input: context_window_id=%s",
            state.get("context_window_id"),
        )
        return {"error": "Invalid UUID in retrieval input"}

    verbose_log(
        state["config"],
        _log,
        "passthrough.retrieval.load_window ENTER window=%s",
        state["context_window_id"],
    )
    pool = get_pg_pool()

    window = await pool.fetchrow(
        "SELECT * FROM context_windows WHERE id = $1",
        uuid.UUID(state["context_window_id"]),
    )
    if window is None:
        return {"error": f"Context window {state['context_window_id']} not found"}

    window_dict = dict(window)

    # D-05/D-10: Apply effective utilization
    from app.budget import EFFECTIVE_UTILIZATION_DEFAULT

    raw_budget = window_dict["max_token_budget"]
    utilization = EFFECTIVE_UTILIZATION_DEFAULT
    effective_budget = int(raw_budget * utilization)

    return {
        "window": window_dict,
        "conversation_id": str(window_dict["conversation_id"]),
        "max_token_budget": effective_budget,
    }


async def pt_load_recent(state: PassthroughRetrievalState) -> dict:
    """Load recent messages as-is, up to the token budget."""
    pool = get_pg_pool()
    max_budget = state["max_token_budget"]
    max_messages = get_tuning(state["config"], "max_messages_to_load", 1000)

    rows = await pool.fetch(
        """
        SELECT id, role, sender, content, sequence_number, token_count,
               tool_calls, tool_call_id, created_at
        FROM conversation_messages
        WHERE conversation_id = $1
        ORDER BY sequence_number DESC
        LIMIT $2
        """,
        uuid.UUID(state["conversation_id"]),
        max_messages,
    )

    messages = []
    tokens_used = 0
    for row in rows:
        msg = dict(row)
        msg_tokens = msg.get("token_count") or max(
            1, len(msg.get("content", "") or "") // 4
        )
        if tokens_used + msg_tokens > max_budget:
            break
        messages.insert(0, msg)
        tokens_used += msg_tokens

    # Build output messages array
    context_messages = []
    recent_for_tiers = []
    for m in messages:
        out = {"role": m["role"], "content": m.get("content", "")}
        if m.get("tool_calls"):
            out["tool_calls"] = m["tool_calls"]
        if m.get("tool_call_id"):
            out["tool_call_id"] = m["tool_call_id"]
        if m.get("sender"):
            out["name"] = m["sender"]
        context_messages.append(out)
        recent_for_tiers.append(
            {
                "id": str(m["id"]),
                "role": m["role"],
                "sender": m.get("sender", ""),
                "content": m.get("content", ""),
                "sequence_number": m["sequence_number"],
            }
        )

    context_tiers = {
        "archival_summary": None,
        "chunk_summaries": [],
        "semantic_messages": [],
        "knowledge_graph_facts": [],
        "recent_messages": recent_for_tiers,
    }

    return {
        "context_messages": context_messages,
        "context_tiers": context_tiers,
        "total_tokens_used": tokens_used,
    }


def pt_ret_route_after_load(state: PassthroughRetrievalState) -> str:
    if state.get("error"):
        return END
    return "pt_load_recent"


def build_passthrough_retrieval():
    """Build and compile the passthrough retrieval StateGraph."""
    workflow = StateGraph(PassthroughRetrievalState)

    workflow.add_node("pt_load_window", pt_load_window)
    workflow.add_node("pt_load_recent", pt_load_recent)

    workflow.set_entry_point("pt_load_window")
    workflow.add_conditional_edges(
        "pt_load_window",
        pt_ret_route_after_load,
        {"pt_load_recent": "pt_load_recent", END: END},
    )
    workflow.add_edge("pt_load_recent", END)

    return workflow.compile()


# ============================================================
# Registration
# ============================================================

register_build_type(
    "passthrough", build_passthrough_assembly, build_passthrough_retrieval
)
