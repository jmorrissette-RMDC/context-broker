"""
Knowledge-enriched build type (ARCH-18).

Extends standard-tiered with semantic retrieval and knowledge graph nodes.
Full retrieval pipeline: episodic tiers + semantic vector search + KG traversal.

Assembly is identical to standard-tiered (reuses the same graph builder).
Retrieval adds inject_semantic_retrieval and inject_knowledge_graph nodes.

F-06: Reads its own LLM config from config["build_types"]["knowledge-enriched"]["llm"],
falling back to the global config["llm"] if not set.
"""

import asyncio
import logging
import operator
import time
import uuid
from typing import Annotated, Optional

import asyncpg
import httpx
import openai
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import (
    get_build_type_config,
    get_embeddings_model,
    get_tuning,
    verbose_log,
)
from context_broker_ae.memory_scoring import filter_and_rank_memories
from app.database import get_pg_pool
from app.utils import stable_lock_id

# Registration handled by register.py — no module-scope side effects
from context_broker_ae.build_types.standard_tiered import (
    _estimate_tokens,
)
from context_broker_ae.build_types.tier_scaling import scale_tier_percentages

_log = logging.getLogger("context_broker.flows.build_types.knowledge_enriched")


# ============================================================
# Retrieval (extends standard-tiered with semantic + KG)
# ============================================================


class KnowledgeEnrichedRetrievalState(TypedDict):
    """State for the enriched retrieval flow."""

    # Inputs
    context_window_id: str
    config: dict

    # V2: query-driven retrieval parameters (optional, backward compatible)
    query: Optional[str]  # user's prompt — drives semantic/KG search
    model: Optional[dict]  # caller's LLM config for distillation cache
    domain_context: Optional[str]  # caller's domain RAG results

    # Intermediate
    window: Optional[dict]
    build_type_config: Optional[dict]
    conversation_id: Optional[str]
    max_token_budget: int
    tier1_summary: Optional[str]
    tier2_summaries: list[str]
    recent_messages: list[dict]
    semantic_messages: list[dict]
    knowledge_graph_facts: list[str]
    assembly_status: str

    # Output
    context_messages: Optional[list[dict]]
    context_tiers: Optional[dict]
    total_tokens_used: int
    warnings: Annotated[list[str], operator.add]  # R5-m5: accumulate, don't overwrite
    error: Optional[str]


async def ke_load_window(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Load the context window and its build type configuration."""
    # R5-M11: Validate UUID at retrieval entry point to fail gracefully
    try:
        uuid.UUID(state["context_window_id"])
    except (ValueError, AttributeError):
        _log.error(
            "Invalid UUID in retrieval input: context_window_id=%s",
            state.get("context_window_id"),
        )
        return {"error": "Invalid UUID in retrieval input", "assembly_status": "error"}

    verbose_log(
        state["config"],
        _log,
        "knowledge_enriched.retrieval.load_window ENTER window=%s",
        state["context_window_id"],
    )
    pool = get_pg_pool()

    window = await pool.fetchrow(
        "SELECT * FROM context_windows WHERE id = $1",
        uuid.UUID(state["context_window_id"]),
    )
    if window is None:
        return {
            "error": f"Context window {state['context_window_id']} not found",
            "assembly_status": "error",
        }

    # Update last_accessed_at on every retrieval
    await pool.execute(
        "UPDATE context_windows SET last_accessed_at = NOW() WHERE id = $1",
        uuid.UUID(state["context_window_id"]),
    )

    window_dict = dict(window)

    try:
        build_type_config = get_build_type_config(
            state["config"], window_dict["build_type"]
        )
    except ValueError as exc:
        return {"error": str(exc), "assembly_status": "error"}

    # D-05/D-10: Apply effective utilization
    from app.budget import EFFECTIVE_UTILIZATION_DEFAULT

    raw_budget = window_dict["max_token_budget"]
    utilization = build_type_config.get(
        "effective_utilization", EFFECTIVE_UTILIZATION_DEFAULT
    )
    effective_budget = int(raw_budget * utilization)

    return {
        "window": window_dict,
        "build_type_config": build_type_config,
        "conversation_id": str(window_dict["conversation_id"]),
        "max_token_budget": effective_budget,
    }


async def ke_wait_for_assembly(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Block if context assembly is in progress, with timeout.

    R6-M9: If Redis is unavailable, proceed without waiting rather than crashing.
    """
    pool = get_pg_pool()
    lock_id = stable_lock_id(state["context_window_id"])

    timeout = get_tuning(state["config"], "assembly_wait_timeout_seconds", 50)
    poll_interval = get_tuning(state["config"], "assembly_poll_interval_seconds", 2)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Try to acquire lock — if we can, assembly is NOT in progress
        acquired = await pool.fetchval("SELECT pg_try_advisory_lock($1)", lock_id)
        if acquired:
            # Release immediately — we just wanted to check
            await pool.execute("SELECT pg_advisory_unlock($1)", lock_id)
            in_progress = False
        else:
            in_progress = True
        if not in_progress:
            return {"assembly_status": "ready"}

        elapsed = timeout - (deadline - time.monotonic())
        _log.info(
            "Retrieval: waiting for assembly on window=%s (%.0fs/%ds)",
            state["context_window_id"],
            elapsed,
            timeout,
        )
        await asyncio.sleep(poll_interval)

    _log.warning(
        "Retrieval: assembly timeout for window=%s after %ds",
        state["context_window_id"],
        timeout,
    )
    return {
        "assembly_status": "timeout",
        "warnings": [
            "Context assembly was still in progress at retrieval time; "
            "context may be stale."
        ],
    }


async def ke_load_summaries(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Load active tier 1 and tier 2 summaries."""
    pool = get_pg_pool()

    summaries = await pool.fetch(
        """
        SELECT tier, summary_text, summarizes_from_seq
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND is_active = TRUE
        ORDER BY tier ASC, summarizes_from_seq ASC
        """,
        uuid.UUID(state["context_window_id"]),
    )

    tier1 = None
    tier2_list = []
    for s in summaries:
        if s["tier"] == 1:
            tier1 = s["summary_text"]
        elif s["tier"] == 2:
            tier2_list.append(s["summary_text"])

    return {"tier1_summary": tier1, "tier2_summaries": tier2_list}


async def ke_load_recent_messages(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Load tier 3 recent verbatim messages within the remaining token budget.

    F-05: Applies dynamic tier scaling based on conversation length.
    """
    pool = get_pg_pool()
    build_type_config = state["build_type_config"]
    max_budget = state["max_token_budget"]

    # Count total messages for F-05 tier scaling
    total_msg_count = await pool.fetchval(
        "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = $1",
        uuid.UUID(state["conversation_id"]),
    )
    scaled_config = scale_tier_percentages(build_type_config, total_msg_count or 0)

    tier3_pct = scaled_config.get("tier3_pct", 0.50)
    tier3_budget = int(max_budget * tier3_pct)

    # Calculate tokens already used by summaries
    summary_tokens = 0
    if state.get("tier1_summary"):
        summary_tokens += len(state["tier1_summary"]) // 4
    for s in state.get("tier2_summaries", []):
        summary_tokens += len(s) // 4

    remaining_budget = max(0, min(tier3_budget, max_budget - summary_tokens))

    # M-06: Avoid loading messages already covered by summaries
    highest_summarized_seq = await pool.fetchval(
        """
        SELECT COALESCE(MAX(summarizes_to_seq), 0)
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND tier = 2
          AND is_active = TRUE
        """,
        uuid.UUID(state["context_window_id"]),
    )

    max_messages = get_tuning(state["config"], "max_messages_to_load", 1000)

    all_messages = await pool.fetch(
        """
        SELECT id, role, sender, content, sequence_number, token_count,
               tool_calls, tool_call_id, created_at
        FROM conversation_messages
        WHERE conversation_id = $1
          AND sequence_number > $2
        ORDER BY sequence_number DESC
        LIMIT $3
        """,
        uuid.UUID(state["conversation_id"]),
        highest_summarized_seq,
        max_messages,
    )

    recent = []
    tokens_used = 0

    for msg in all_messages:
        msg_tokens = msg["token_count"] or max(1, len(msg.get("content") or "") // 4)
        if tokens_used + msg_tokens <= remaining_budget:
            recent.insert(0, dict(msg))
            tokens_used += msg_tokens
        else:
            break

    return {
        "recent_messages": recent,
        "total_tokens_used": summary_tokens + tokens_used,
    }


async def ke_inject_semantic_retrieval(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Retrieve semantically similar messages via pgvector.

    V2: Uses state["query"] (user's current prompt) when available.
    Falls back to recent messages for backward compatibility.

    G5-22a: Semantic retrieval may surface messages already compressed into
    summaries. This is a known and accepted trade-off.
    """
    build_type_config = state["build_type_config"]
    semantic_pct = build_type_config.get("semantic_retrieval_pct", 0)

    if not semantic_pct or semantic_pct <= 0:
        return {"semantic_messages": []}

    if not state.get("recent_messages"):
        return {"semantic_messages": []}

    config = state["config"]

    # V2: Use the user's query for semantic search when provided
    query_text = state.get("query")
    if not query_text:
        # Backward compatibility: build query from recent messages
        query_trunc = get_tuning(config, "query_truncation_chars", 200)
        query_text = " ".join(
            (m.get("content") or "")[:query_trunc] for m in state["recent_messages"][-3:]
        )

    try:
        embeddings_model = get_embeddings_model(config)
        query_embedding = await embeddings_model.aembed_query(query_text)
    except (openai.APIError, httpx.HTTPError, ValueError) as exc:
        _log.warning("Semantic retrieval: embedding failed: %s", exc)
        return {"semantic_messages": []}

    tier3_min_seq = (
        state["recent_messages"][0]["sequence_number"]
        if state["recent_messages"]
        else None
    )

    if tier3_min_seq is None:
        return {"semantic_messages": []}

    semantic_budget = int(state["max_token_budget"] * semantic_pct)
    tokens_per_msg = max(1, get_tuning(config, "tokens_per_message_estimate", 150))
    semantic_limit = max(5, semantic_budget // tokens_per_msg)

    pool = get_pg_pool()
    vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    try:
        rows = await pool.fetch(
            """
            SELECT id, role, sender, content, sequence_number, token_count,
                   tool_calls, tool_call_id
            FROM conversation_messages
            WHERE conversation_id = $1
              AND sequence_number < $2
              AND embedding IS NOT NULL
            ORDER BY embedding <=> $3::vector
            LIMIT $4
            """,
            uuid.UUID(state["conversation_id"]),
            tier3_min_seq,
            vec_str,
            semantic_limit,
        )
        semantic_messages = [dict(r) for r in rows]
        _log.info(
            "Semantic retrieval: found %d relevant messages for window=%s",
            len(semantic_messages),
            state["context_window_id"],
        )
        return {"semantic_messages": semantic_messages}
    except (asyncpg.PostgresError, OSError) as exc:
        _log.warning("Semantic retrieval query failed: %s", exc)
        return {"semantic_messages": []}


async def ke_inject_knowledge_graph(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Retrieve knowledge graph facts via Mem0.

    V2: Uses state["query"] (user's current prompt) when available.
    Falls back to recent messages for backward compatibility.
    """
    build_type_config = state["build_type_config"]
    kg_pct = build_type_config.get("knowledge_graph_pct", 0)

    if not kg_pct or kg_pct <= 0:
        return {"knowledge_graph_facts": []}

    if not state.get("recent_messages") and not state.get("query"):
        return {"knowledge_graph_facts": []}

    config = state["config"]

    # V2: Use the user's query when provided
    search_text = state.get("query")
    if not search_text:
        search_text = " ".join(
            (m.get("content") or "")[:500] for m in state["recent_messages"][-5:]
        )

    try:
        from context_broker_ae.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            return {"knowledge_graph_facts": []}

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: mem0.search(
                search_text,
                user_id=state["window"].get("participant_id", "default"),
                limit=10,
            ),
        )

        facts = []
        if isinstance(results, dict):
            memories = results.get("results", [])
        else:
            memories = results or []

        # M-22: Apply half-life decay scoring and filter stale memories
        memories = filter_and_rank_memories(memories, config)

        for mem in memories:
            fact_text = mem.get("memory") or mem.get("content") or str(mem)
            if fact_text:
                facts.append(fact_text)

        _log.info(
            "Knowledge graph: retrieved %d facts for window=%s",
            len(facts),
            state["context_window_id"],
        )
        return {"knowledge_graph_facts": facts}

    except (
        ConnectionError,
        RuntimeError,
        ValueError,
        ImportError,
        OSError,
        Exception,
    ) as exc:  # EX-CB-001: broad catch for Mem0
        _log.warning("Knowledge graph retrieval failed (degraded mode): %s", exc)
        return {"knowledge_graph_facts": []}


async def ke_assemble_context(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Assemble the final context messages array from all tiers including semantic + KG.

    ARCH-03: Produces a structured messages array matching the OpenAI format.
    """
    max_budget = state.get("max_token_budget", 0)
    cumulative_tokens = 0
    messages: list[dict] = []

    # Tier 1: Archival summary
    if state.get("tier1_summary"):
        content = f"[Archival context]\n{state['tier1_summary']}"
        cumulative_tokens += _estimate_tokens(content)
        messages.append({"role": "system", "content": content})

    # Tier 2: Chunk summaries
    if state.get("tier2_summaries"):
        content = "[Recent summaries]\n" + "\n\n".join(state["tier2_summaries"])
        cumulative_tokens += _estimate_tokens(content)
        messages.append({"role": "system", "content": content})

    # Semantic retrieval — budget-aware (M-07)
    # R7-M10: Track which semantic messages survive truncation for context_tiers
    truncated_semantic_messages: list[dict] = []
    if state.get("semantic_messages"):
        remaining = (
            max(0, max_budget - cumulative_tokens) if max_budget else float("inf")
        )
        semantic_lines = []
        semantic_tokens = 0
        for m in state["semantic_messages"]:
            line = f"[{m['role']}] {m['sender']}: {m.get('content') or ''}"
            line_tokens = _estimate_tokens(line)
            if semantic_tokens + line_tokens > remaining:
                break
            semantic_lines.append(line)
            truncated_semantic_messages.append(m)
            semantic_tokens += line_tokens
        if semantic_lines:
            content = "[Semantically relevant context]\n" + "\n".join(semantic_lines)
            cumulative_tokens += _estimate_tokens(content)
            messages.append({"role": "system", "content": content})

    # Knowledge graph facts — budget-aware (M-07)
    if state.get("knowledge_graph_facts"):
        remaining = (
            max(0, max_budget - cumulative_tokens) if max_budget else float("inf")
        )
        fact_lines = []
        fact_tokens = 0
        for f in state["knowledge_graph_facts"]:
            line = f"- {f}"
            line_tokens = _estimate_tokens(line)
            if fact_tokens + line_tokens > remaining:
                break
            fact_lines.append(line)
            fact_tokens += line_tokens
        if fact_lines:
            content = "[Knowledge graph]\n" + "\n".join(fact_lines)
            cumulative_tokens += _estimate_tokens(content)
            messages.append({"role": "system", "content": content})

    # V2: Domain context from caller (external MAD support)
    if state.get("domain_context"):
        remaining = (
            max(0, max_budget - cumulative_tokens) if max_budget else float("inf")
        )
        domain_text = state["domain_context"][:int(remaining * 4)]  # rough char limit
        if domain_text:
            content = "[Domain knowledge]\n" + domain_text
            cumulative_tokens += _estimate_tokens(content)
            messages.append({"role": "system", "content": content})

    # Tier 3: Recent verbatim messages (M-08: newest first truncation)
    truncated_recent_messages: list[dict] = []
    if state.get("recent_messages"):
        remaining = (
            max(0, max_budget - cumulative_tokens) if max_budget else float("inf")
        )
        msg_tokens = 0
        for m in reversed(state["recent_messages"]):
            msg_content = m.get("content", "")
            msg_token_count = _estimate_tokens(msg_content)
            if msg_tokens + msg_token_count > remaining:
                break
            truncated_recent_messages.insert(0, m)
            msg_tokens += msg_token_count
        for m in truncated_recent_messages:
            msg = {"role": m["role"], "content": m["content"]}
            if m.get("tool_calls"):
                msg["tool_calls"] = m["tool_calls"]
            if m.get("tool_call_id"):
                msg["tool_call_id"] = m["tool_call_id"]
            if m.get("sender"):
                msg["name"] = m["sender"]
            messages.append(msg)
            cumulative_tokens += _estimate_tokens(m.get("content", ""))

    # R7-M10: Build context_tiers using truncated lists — semantic_messages should
    # only include the messages that actually made it into the context output.
    context_tiers = {
        "archival_summary": state.get("tier1_summary"),
        "chunk_summaries": state.get("tier2_summaries", []),
        "semantic_messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "sender": m["sender"],
                "content": m["content"],
                "sequence_number": m["sequence_number"],
            }
            for m in truncated_semantic_messages
        ],
        "knowledge_graph_facts": state.get("knowledge_graph_facts", []),
        "domain_context": state.get("domain_context", ""),
        "recent_messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "sender": m["sender"],
                "content": m["content"],
                "sequence_number": m["sequence_number"],
            }
            for m in truncated_recent_messages
        ],
    }

    return {
        "context_messages": messages,
        "context_tiers": context_tiers,
    }


def ke_route_after_load_window(state: KnowledgeEnrichedRetrievalState) -> str:
    if state.get("error"):
        return END
    return "ke_wait_for_assembly"


def ke_route_after_wait(state: KnowledgeEnrichedRetrievalState) -> str:
    return "ke_load_summaries"


def ke_route_after_load_messages(state: KnowledgeEnrichedRetrievalState) -> str:
    """Route: check if build type needs semantic/KG retrieval."""
    build_type_config = state.get("build_type_config", {})
    needs_semantic = build_type_config.get("semantic_retrieval_pct", 0) > 0
    needs_kg = build_type_config.get("knowledge_graph_pct", 0) > 0

    if needs_semantic:
        return "ke_inject_semantic_retrieval"
    if needs_kg:
        return "ke_inject_knowledge_graph"
    return "ke_assemble_context"


def ke_route_after_semantic(state: KnowledgeEnrichedRetrievalState) -> str:
    build_type_config = state.get("build_type_config", {})
    needs_kg = build_type_config.get("knowledge_graph_pct", 0) > 0
    if needs_kg:
        return "ke_inject_knowledge_graph"
    return "ke_assemble_context"


def build_knowledge_enriched_retrieval():
    """Build and compile the knowledge-enriched retrieval StateGraph."""
    workflow = StateGraph(KnowledgeEnrichedRetrievalState)

    workflow.add_node("ke_load_window", ke_load_window)
    workflow.add_node("ke_wait_for_assembly", ke_wait_for_assembly)
    workflow.add_node("ke_load_summaries", ke_load_summaries)
    workflow.add_node("ke_load_recent_messages", ke_load_recent_messages)
    workflow.add_node("ke_inject_semantic_retrieval", ke_inject_semantic_retrieval)
    workflow.add_node("ke_inject_knowledge_graph", ke_inject_knowledge_graph)
    workflow.add_node("ke_assemble_context", ke_assemble_context)

    workflow.set_entry_point("ke_load_window")

    workflow.add_conditional_edges(
        "ke_load_window",
        ke_route_after_load_window,
        {"ke_wait_for_assembly": "ke_wait_for_assembly", END: END},
    )
    workflow.add_conditional_edges(
        "ke_wait_for_assembly",
        ke_route_after_wait,
        {"ke_load_summaries": "ke_load_summaries"},
    )
    workflow.add_edge("ke_load_summaries", "ke_load_recent_messages")

    workflow.add_conditional_edges(
        "ke_load_recent_messages",
        ke_route_after_load_messages,
        {
            "ke_inject_semantic_retrieval": "ke_inject_semantic_retrieval",
            "ke_inject_knowledge_graph": "ke_inject_knowledge_graph",
            "ke_assemble_context": "ke_assemble_context",
        },
    )

    workflow.add_conditional_edges(
        "ke_inject_semantic_retrieval",
        ke_route_after_semantic,
        {
            "ke_inject_knowledge_graph": "ke_inject_knowledge_graph",
            "ke_assemble_context": "ke_assemble_context",
        },
    )

    workflow.add_edge("ke_inject_knowledge_graph", "ke_assemble_context")
    workflow.add_edge("ke_assemble_context", END)

    return workflow.compile()


# ============================================================
# Assembly — reuse standard-tiered (same logic, just build type label differs)
# ============================================================

# The assembly process is identical for knowledge-enriched: chunk summarization
# and archival consolidation work the same way. The difference is only in retrieval
# (additional semantic + KG nodes). We reuse the standard-tiered assembly builder.
# The build_type label in the assembly state is set by the caller (arq_worker),
# so metrics are correctly attributed.

# Registration handled by context_broker_ae.register — no module-scope side effects.
