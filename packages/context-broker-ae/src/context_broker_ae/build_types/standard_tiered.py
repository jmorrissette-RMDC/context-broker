"""
Standard-tiered build type (ARCH-18).

Three-tier progressive compression context assembly:
  Tier 1: Archival summary (oldest, most compressed)
  Tier 2: Chunk summaries (middle layer)
  Tier 3: Recent verbatim messages (newest, full fidelity)

Moved from the monolithic context_assembly.py and retrieval_flow.py.
All original logic, error handling, and lock management preserved.

F-06: Reads its own LLM config from config["build_types"]["standard-tiered"]["llm"],
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
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_build_type_config, get_chat_model, get_tuning, verbose_log
from app.database import get_pg_pool
from app.utils import stable_lock_id

# Registration handled by register.py — no module-scope side effects
from context_broker_ae.build_types.tier_scaling import scale_tier_percentages
from app.metrics_registry import CONTEXT_ASSEMBLY_DURATION
from app.prompt_loader import async_load_prompt

_log = logging.getLogger("context_broker.flows.build_types.standard_tiered")


def _resolve_llm_config(config: dict, build_type_config: dict) -> dict:
    """Resolve effective LLM config: build-type-specific overrides global (F-06).

    Returns a config dict suitable for passing to get_chat_model().
    """
    bt_llm = build_type_config.get("llm")
    if bt_llm:
        # Build a config dict that get_chat_model expects (top-level "llm" key)
        return {**config, "llm": bt_llm}
    return config


# ============================================================
# Assembly
# ============================================================


class StandardTieredAssemblyState(TypedDict):
    """State for the standard-tiered assembly pipeline."""

    # Inputs
    context_window_id: str
    conversation_id: str
    config: dict

    # Intermediate
    window: Optional[dict]
    build_type_config: Optional[dict]
    max_token_budget: int
    all_messages: list[dict]
    tier3_messages: list[dict]
    older_messages: list[dict]
    chunks: list[list[dict]]
    tier2_summaries: list[str]
    tier1_summary: Optional[str]
    lock_key: str
    lock_token: Optional[str]
    lock_acquired: bool
    had_errors: bool
    assembly_start_time: Optional[float]

    # Output
    error: Optional[str]


async def acquire_assembly_lock(state: StandardTieredAssemblyState) -> dict:
    """Acquire a Redis distributed lock for this context window."""
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
        "standard_tiered.acquire_lock ENTER window=%s",
        state["context_window_id"],
    )
    lock_key = f"assembly_in_progress:{state['context_window_id']}"
    lock_token = str(uuid.uuid4())

    # R7-M5: Wrap Redis calls in try/except — return error state if unavailable
    try:
        pool = get_pg_pool()  # Advisory lock (Redis removed)
        lock_id = stable_lock_id(state["context_window_id"])
        acquired = await pool.fetchval("SELECT pg_try_advisory_lock($1)", lock_id)
    except (RuntimeError, OSError, ConnectionError) as exc:
        _log.error(
            "Redis unavailable during lock acquisition for window=%s: %s",
            state["context_window_id"],
            exc,
        )
        return {
            "lock_key": lock_key,
            "lock_token": None,
            "lock_acquired": False,
            "error": f"Redis unavailable: {exc}",
        }

    if not acquired:
        _log.info(
            "Context assembly: lock not acquired for window=%s — skipping",
            state["context_window_id"],
        )
        return {"lock_key": lock_key, "lock_token": None, "lock_acquired": False}

    return {
        "lock_key": lock_key,
        "lock_token": lock_token,
        "lock_acquired": True,
        "assembly_start_time": time.monotonic(),
    }


async def load_window_config(state: StandardTieredAssemblyState) -> dict:
    """Load the context window and resolve its build type configuration."""
    pool = get_pg_pool()

    window = await pool.fetchrow(
        "SELECT * FROM context_windows WHERE id = $1",
        uuid.UUID(state["context_window_id"]),
    )
    if window is None:
        return {"error": f"Context window {state['context_window_id']} not found"}

    window_dict = dict(window)

    try:
        build_type_config = get_build_type_config(
            state["config"], window_dict["build_type"]
        )
    except ValueError as exc:
        return {"error": str(exc)}

    # D-05/D-10: Apply effective utilization — models degrade past ~85% fill.
    # The build type owns the threshold; raw budget comes from the window.
    from app.budget import EFFECTIVE_UTILIZATION_DEFAULT

    raw_budget = window_dict["max_token_budget"]
    utilization = build_type_config.get(
        "effective_utilization", EFFECTIVE_UTILIZATION_DEFAULT
    )
    effective_budget = int(raw_budget * utilization)

    return {
        "window": window_dict,
        "build_type_config": build_type_config,
        "max_token_budget": effective_budget,
    }


async def load_messages(state: StandardTieredAssemblyState) -> dict:
    """Load messages for the conversation in chronological order.

    R2-F11: Adaptive message load limit based on tier3 budget.
    D-09: On first assembly (no existing summaries), look back further
    using the initial_lookback_multiplier to provide enough raw material
    for initial summarization.
    """
    pool = get_pg_pool()
    build_type_config = state.get("build_type_config") or {}
    max_budget = state.get("max_token_budget", 8192)
    tier3_pct = build_type_config.get("tier3_pct", 0.72)
    tier3_budget = int(max_budget * tier3_pct)
    tokens_per_message = get_tuning(state["config"], "tokens_per_message_estimate", 150)
    adaptive_limit = max(50, tier3_budget // tokens_per_message)

    # D-09: On initial assembly, look back further to build first summaries.
    # Check if any summaries exist for this window — if not, use multiplier.
    window_id = state.get("context_window_id") or (
        state["window"]["id"] if state.get("window") else None
    )
    if window_id:
        existing_summaries = await pool.fetchval(
            "SELECT COUNT(*) FROM conversation_summaries WHERE context_window_id = $1",
            uuid.UUID(str(window_id)),
        )
        if existing_summaries == 0:
            lookback_multiplier = build_type_config.get(
                "initial_lookback_multiplier", 3
            )
            lookback_tokens = int(max_budget * lookback_multiplier)
            adaptive_limit = max(adaptive_limit, lookback_tokens // tokens_per_message)

    rows = await pool.fetch(
        """
        SELECT id, role, sender, content, sequence_number, token_count, created_at
        FROM conversation_messages
        WHERE conversation_id = $1
        ORDER BY sequence_number DESC
        LIMIT $2
        """,
        uuid.UUID(state["conversation_id"]),
        adaptive_limit,
    )

    rows = list(reversed(rows))
    messages = [dict(r) for r in rows]
    _log.info(
        "Context assembly: loaded %d messages for window=%s",
        len(messages),
        state["context_window_id"],
    )
    return {"all_messages": messages}


async def calculate_tier_boundaries(state: StandardTieredAssemblyState) -> dict:
    """Calculate which messages belong to each tier.

    F-05: Applies dynamic tier scaling based on conversation length.
    """
    messages = state["all_messages"]
    if not messages:
        return {"tier3_messages": [], "older_messages": [], "chunks": []}

    build_type_config = state["build_type_config"]
    max_budget = state["max_token_budget"]

    # F-05: Dynamic tier scaling
    scaled_config = scale_tier_percentages(build_type_config, len(messages))

    tier3_pct = scaled_config.get("tier3_pct", 0.72)
    tier3_budget = int(max_budget * tier3_pct)

    # Walk backwards to fill tier 3 budget
    tier3_messages = []
    tier3_tokens_used = 0
    tier3_start_seq = messages[-1]["sequence_number"] + 1

    for msg in reversed(messages):
        msg_tokens = msg.get("token_count") or max(
            1, len(msg.get("content") or "") // 4
        )
        if tier3_tokens_used + msg_tokens <= tier3_budget:
            tier3_messages.insert(0, msg)
            tier3_tokens_used += msg_tokens
            tier3_start_seq = msg["sequence_number"]
        else:
            break

    # Messages before tier 3 boundary need summarization
    older_messages = [m for m in messages if m["sequence_number"] < tier3_start_seq]

    # Incremental: find what's already covered by existing tier 2 summaries
    pool = get_pg_pool()
    existing_t2 = await pool.fetch(
        """
        SELECT summarizes_to_seq
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND tier = 2
          AND is_active = TRUE
        ORDER BY summarizes_to_seq DESC
        LIMIT 1
        """,
        uuid.UUID(state["context_window_id"]),
    )

    max_summarized_seq = 0
    if existing_t2:
        max_summarized_seq = existing_t2[0].get("summarizes_to_seq", 0)

    # Only process messages not yet summarized
    unsummarized = [
        m for m in older_messages if m["sequence_number"] > max_summarized_seq
    ]

    # Chunk unsummarized messages into groups
    chunk_size = get_tuning(state["config"], "chunk_size", 20)
    chunks = [
        unsummarized[i : i + chunk_size]
        for i in range(0, len(unsummarized), chunk_size)
    ]

    if max_summarized_seq > 0:
        _log.info(
            "Incremental assembly: %d new messages to summarize (already covered through seq %d)",
            len(unsummarized),
            max_summarized_seq,
        )

    return {
        "tier3_messages": tier3_messages,
        "older_messages": older_messages,
        "chunks": chunks,
    }


async def summarize_message_chunks(state: StandardTieredAssemblyState) -> dict:
    """LLM-summarize each new chunk of older messages."""
    chunks = state["chunks"]
    if not chunks:
        return {"tier2_summaries": []}

    config = state["config"]
    build_type_config = state.get("build_type_config") or {}

    # F-06: Use build-type-specific LLM config if available, else summarization role
    effective_config = _resolve_llm_config(config, build_type_config)
    llm_config = effective_config.get(
        "llm", config.get("inference", {}).get("summarization", {})
    )
    llm = get_chat_model(effective_config, role="summarization")

    pool = get_pg_pool()

    # M-23: Load prompt once before the concurrent calls
    try:
        chunk_prompt = await async_load_prompt("chunk_summarization")
    except RuntimeError as exc:
        _log.error("Failed to load chunk_summarization prompt: %s", exc)
        return {"tier2_summaries": [], "error": f"Prompt loading failed: {exc}"}

    # Advisory lock (Redis removed — no TTL renewal needed)
    pool = get_pg_pool()

    async def _summarize_chunk(chunk: list[dict]) -> tuple[list[dict], str | None]:
        # Advisory locks: no TTL renewal needed
        chunk_text = "\n".join(
            f"[{m['role']} | {m['sender']}] {m.get('content') or ''}" for m in chunk
        )
        messages = [
            SystemMessage(content=chunk_prompt),
            HumanMessage(content=chunk_text),
        ]
        try:
            response = await llm.ainvoke(messages)
            return (chunk, response.content)
        except (openai.APIError, httpx.HTTPError, ValueError) as exc:
            _log.error(
                "Chunk summarization failed for window=%s: %s",
                state["context_window_id"],
                exc,
            )
            return (chunk, None)

    # m16: Limit concurrent LLM calls
    max_concurrent = get_tuning(config, "max_concurrent_chunk_summaries", 5)
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _bounded_summarize(chunk: list[dict]) -> tuple[list[dict], str | None]:
        async with semaphore:
            return await _summarize_chunk(chunk)

    llm_results = await asyncio.gather(*[_bounded_summarize(chunk) for chunk in chunks])

    # M-09: Track whether any chunk failed
    had_errors = any(summary_text is None for _, summary_text in llm_results)

    # R7-M11: Budget guard — stop inserting summaries if cumulative tier1+tier2
    # tokens would exceed their allocation within the max token budget.
    max_budget = state.get("max_token_budget", 0)
    scaled_config = scale_tier_percentages(
        build_type_config, len(state.get("all_messages", []))
    )
    tier1_pct = scaled_config.get("tier1_pct", 0.08)
    tier2_pct = scaled_config.get("tier2_pct", 0.20)
    summary_budget = int(max_budget * (tier1_pct + tier2_pct))
    cumulative_summary_tokens = 0

    # Insert summaries sequentially to preserve ordering
    new_summaries = []
    for chunk, summary_text in llm_results:
        if summary_text is None:
            continue

        # R7-M11: Check cumulative token budget before inserting
        summary_token_count = max(1, len(summary_text) // 4)
        if (
            summary_budget > 0
            and cumulative_summary_tokens + summary_token_count > summary_budget
        ):
            _log.info(
                "Budget guard: stopping tier 2 summarization at %d tokens (budget=%d) for window=%s",
                cumulative_summary_tokens,
                summary_budget,
                state["context_window_id"],
            )
            break

        chunk_tokens = sum(
            m.get("token_count") or max(1, len(m.get("content") or "") // 4)
            for m in chunk
        )

        # Idempotency: skip if summary already exists for this range
        existing = await pool.fetchval(
            """
            SELECT id FROM conversation_summaries
            WHERE context_window_id = $1
              AND tier = 2
              AND summarizes_from_seq = $2
              AND summarizes_to_seq = $3
              AND is_active = TRUE
            """,
            uuid.UUID(state["context_window_id"]),
            chunk[0]["sequence_number"],
            chunk[-1]["sequence_number"],
        )
        if existing:
            _log.info(
                "Skipping duplicate summary for seq %d-%d",
                chunk[0]["sequence_number"],
                chunk[-1]["sequence_number"],
            )
            continue

        # R5-M14: Catch UniqueViolationError in case a concurrent assembler
        # already inserted a summary for this range between our pre-check
        # and this INSERT.
        try:
            await pool.execute(
                """
                INSERT INTO conversation_summaries
                    (conversation_id, context_window_id, summary_text, tier,
                     summarizes_from_seq, summarizes_to_seq, message_count,
                     original_token_count, summary_token_count, summarized_by_model)
                VALUES ($1, $2, $3, 2, $4, $5, $6, $7, $8, $9)
                """,
                uuid.UUID(state["conversation_id"]),
                uuid.UUID(state["context_window_id"]),
                summary_text,
                chunk[0]["sequence_number"],
                chunk[-1]["sequence_number"],
                len(chunk),
                chunk_tokens,
                len(summary_text) // 4,
                llm_config.get("model", "unknown"),
            )
        except asyncpg.UniqueViolationError:
            _log.info(
                "Concurrent summary insert for seq %d-%d — skipping (other assembler won)",
                chunk[0]["sequence_number"],
                chunk[-1]["sequence_number"],
            )
            continue
        new_summaries.append(summary_text)
        cumulative_summary_tokens += summary_token_count

    _log.info(
        "Context assembly: wrote %d tier 2 summaries for window=%s",
        len(new_summaries),
        state["context_window_id"],
    )
    result: dict = {"tier2_summaries": new_summaries}
    if had_errors:
        result["had_errors"] = True
    return result


async def consolidate_archival_summary(state: StandardTieredAssemblyState) -> dict:
    """Consolidate oldest tier 2 summaries into a tier 1 archival summary."""
    pool = get_pg_pool()

    active_t2 = await pool.fetch(
        """
        SELECT id, summary_text, summarizes_from_seq, summarizes_to_seq, message_count
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND tier = 2
          AND is_active = TRUE
        ORDER BY summarizes_from_seq ASC
        """,
        uuid.UUID(state["context_window_id"]),
    )

    if len(active_t2) <= get_tuning(state["config"], "consolidation_threshold", 3):
        return {"tier1_summary": None}

    keep_recent = get_tuning(state["config"], "consolidation_keep_recent", 2)

    # R6-M11: Guard against empty consolidation list when keep_recent >= len(active_t2)
    if len(active_t2) <= keep_recent:
        return {"tier1_summary": None}

    to_consolidate = list(active_t2)[:-keep_recent]

    # M-16: Include existing tier 1 summary in consolidation
    existing_t1 = await pool.fetchrow(
        """
        SELECT summary_text
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND tier = 1
          AND is_active = TRUE
        ORDER BY summarizes_to_seq DESC
        LIMIT 1
        """,
        uuid.UUID(state["context_window_id"]),
    )

    consolidation_parts = []
    if existing_t1 and existing_t1["summary_text"]:
        consolidation_parts.append(
            f"[Existing archival summary]\n{existing_t1['summary_text']}"
        )
    consolidation_parts.extend(r["summary_text"] for r in to_consolidate)
    consolidation_text = "\n\n".join(consolidation_parts)

    config = state["config"]
    build_type_config = state.get("build_type_config") or {}

    # F-06: Use build-type-specific LLM config if available
    effective_config = _resolve_llm_config(config, build_type_config)
    llm_config = effective_config.get("llm", {})

    # M-23: Catch prompt-loading failures
    try:
        archival_prompt = await async_load_prompt("archival_consolidation")
    except RuntimeError as exc:
        _log.error("Failed to load archival_consolidation prompt: %s", exc)
        return {"tier1_summary": None, "error": f"Prompt loading failed: {exc}"}

    llm = get_chat_model(effective_config, role="summarization")

    messages = [
        SystemMessage(content=archival_prompt),
        HumanMessage(content=consolidation_text),
    ]

    # R7-M12: Renew lock TTL before the LLM call (same pattern as _summarize_chunk)
    lock_key = state.get("lock_key", "")
    lock_token = state.get("lock_token")
    if lock_key and lock_token:
        try:
            pass  # Advisory locks: no TTL renewal needed
        except (RuntimeError, OSError, ConnectionError) as exc:
            _log.warning("Failed to renew lock TTL for archival consolidation: %s", exc)

    try:
        response = await llm.ainvoke(messages)
        archival_text = response.content

        if archival_text:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Deactivate existing tier 1
                    await conn.execute(
                        """
                        UPDATE conversation_summaries
                        SET is_active = FALSE
                        WHERE context_window_id = $1 AND tier = 1 AND is_active = TRUE
                        """,
                        uuid.UUID(state["context_window_id"]),
                    )

                    # Deactivate consolidated tier 2 chunks
                    consolidated_ids = [r["id"] for r in to_consolidate]
                    await conn.execute(
                        """
                        UPDATE conversation_summaries
                        SET is_active = FALSE
                        WHERE id = ANY($1::uuid[])
                        """,
                        consolidated_ids,
                    )

                    # Insert new tier 1 archival summary
                    await conn.execute(
                        """
                        INSERT INTO conversation_summaries
                            (conversation_id, context_window_id, summary_text, tier,
                             summarizes_from_seq, summarizes_to_seq, message_count,
                             summarized_by_model)
                        VALUES ($1, $2, $3, 1, $4, $5, $6, $7)
                        """,
                        uuid.UUID(state["conversation_id"]),
                        uuid.UUID(state["context_window_id"]),
                        archival_text,
                        to_consolidate[0]["summarizes_from_seq"],
                        to_consolidate[-1]["summarizes_to_seq"],
                        sum(r.get("message_count") or 0 for r in to_consolidate),
                        llm_config.get("model", "unknown"),
                    )

            _log.info(
                "Context assembly: consolidated %d tier 2 summaries into tier 1 for window=%s",
                len(to_consolidate),
                state["context_window_id"],
            )
            return {"tier1_summary": archival_text}

    except (openai.APIError, httpx.HTTPError, ValueError) as exc:
        _log.error(
            "Archival consolidation failed for window=%s: %s",
            state["context_window_id"],
            exc,
        )
        return {"tier1_summary": None, "had_errors": True}

    return {"tier1_summary": None}


async def finalize_assembly(state: StandardTieredAssemblyState) -> dict:
    """Update last_assembled_at and observe duration metric.

    M-09: Skip last_assembled_at update if there were partial failures.
    """
    if state.get("had_errors"):
        _log.warning(
            "Context assembly had errors for window=%s — skipping last_assembled_at update",
            state["context_window_id"],
        )
    else:
        pool = get_pg_pool()
        await pool.execute(
            "UPDATE context_windows SET last_assembled_at = NOW() WHERE id = $1",
            uuid.UUID(state["context_window_id"]),
        )

    start_time = state.get("assembly_start_time")
    if start_time is not None:
        duration = time.monotonic() - start_time
        CONTEXT_ASSEMBLY_DURATION.labels(build_type="standard-tiered").observe(duration)

    _log.info(
        "Context assembly %s for window=%s",
        "complete (with errors)" if state.get("had_errors") else "complete",
        state["context_window_id"],
    )
    return {}


async def release_assembly_lock(state: StandardTieredAssemblyState) -> dict:
    """Release the Postgres advisory lock for assembly."""
    lock_key = state.get("lock_key", "")
    if lock_key and state.get("lock_acquired"):
        try:
            pool = get_pg_pool()
            lock_id = stable_lock_id(state["context_window_id"])
            await pool.execute("SELECT pg_advisory_unlock($1)", lock_id)
        except (ValueError, OSError) as exc:
            _log.debug("Lock release failed: %s", exc)
    return {}


def route_after_lock(state: StandardTieredAssemblyState) -> str:
    if not state.get("lock_acquired"):
        return END
    return "load_window_config"


def route_after_load_config(state: StandardTieredAssemblyState) -> str:
    if state.get("error"):
        return "release_assembly_lock"
    return "load_messages"


def route_after_load_messages(state: StandardTieredAssemblyState) -> str:
    if state.get("error"):
        return "release_assembly_lock"
    if not state.get("all_messages"):
        return "finalize_assembly"
    return "calculate_tier_boundaries"


def route_after_calculate_tiers(state: StandardTieredAssemblyState) -> str:
    if state.get("error"):
        return "release_assembly_lock"
    if not state.get("chunks"):
        return "consolidate_archival_summary"
    return "summarize_message_chunks"


def route_after_summarize(state: StandardTieredAssemblyState) -> str:
    if state.get("error"):
        return "release_assembly_lock"
    return "consolidate_archival_summary"


def build_standard_tiered_assembly():
    """Build and compile the standard-tiered assembly StateGraph."""
    workflow = StateGraph(StandardTieredAssemblyState)

    workflow.add_node("acquire_assembly_lock", acquire_assembly_lock)
    workflow.add_node("load_window_config", load_window_config)
    workflow.add_node("load_messages", load_messages)
    workflow.add_node("calculate_tier_boundaries", calculate_tier_boundaries)
    workflow.add_node("summarize_message_chunks", summarize_message_chunks)
    workflow.add_node("consolidate_archival_summary", consolidate_archival_summary)
    workflow.add_node("finalize_assembly", finalize_assembly)
    workflow.add_node("release_assembly_lock", release_assembly_lock)

    workflow.set_entry_point("acquire_assembly_lock")

    workflow.add_conditional_edges(
        "acquire_assembly_lock",
        route_after_lock,
        {"load_window_config": "load_window_config", END: END},
    )
    workflow.add_conditional_edges(
        "load_window_config",
        route_after_load_config,
        {
            "load_messages": "load_messages",
            "release_assembly_lock": "release_assembly_lock",
        },
    )
    workflow.add_conditional_edges(
        "load_messages",
        route_after_load_messages,
        {
            "calculate_tier_boundaries": "calculate_tier_boundaries",
            "finalize_assembly": "finalize_assembly",
            "release_assembly_lock": "release_assembly_lock",
        },
    )
    workflow.add_conditional_edges(
        "calculate_tier_boundaries",
        route_after_calculate_tiers,
        {
            "summarize_message_chunks": "summarize_message_chunks",
            "consolidate_archival_summary": "consolidate_archival_summary",
            "release_assembly_lock": "release_assembly_lock",
        },
    )
    workflow.add_conditional_edges(
        "summarize_message_chunks",
        route_after_summarize,
        {
            "consolidate_archival_summary": "consolidate_archival_summary",
            "release_assembly_lock": "release_assembly_lock",
        },
    )
    workflow.add_edge("consolidate_archival_summary", "finalize_assembly")
    workflow.add_edge("finalize_assembly", "release_assembly_lock")
    workflow.add_edge("release_assembly_lock", END)

    return workflow.compile()


# ============================================================
# Retrieval
# ============================================================


class StandardTieredRetrievalState(TypedDict):
    """State for the standard-tiered retrieval flow."""

    # Inputs
    context_window_id: str
    config: dict

    # Intermediate
    window: Optional[dict]
    build_type_config: Optional[dict]
    conversation_id: Optional[str]
    max_token_budget: int
    tier1_summary: Optional[str]
    tier2_summaries: list[str]
    recent_messages: list[dict]
    assembly_status: str

    # Output
    context_messages: Optional[list[dict]]
    context_tiers: Optional[dict]
    total_tokens_used: int
    warnings: Annotated[list[str], operator.add]  # R5-m5: accumulate, don't overwrite
    error: Optional[str]


async def ret_load_window(state: StandardTieredRetrievalState) -> dict:
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
        "standard_tiered.retrieval.load_window ENTER window=%s",
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

    # D-05/D-10: Apply effective utilization for retrieval
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


async def ret_wait_for_assembly(state: StandardTieredRetrievalState) -> dict:
    """Block if context assembly is in progress, with timeout.

    R6-M9: If Redis is unavailable, proceed without waiting rather than crashing.
    """
    try:
        pool = get_pg_pool()  # Advisory lock (Redis removed)
    except RuntimeError:
        _log.warning("Retrieval: Redis not available, proceeding without assembly wait")
        return {"assembly_status": "ready"}

    timeout = get_tuning(state["config"], "assembly_wait_timeout_seconds", 50)
    poll_interval = get_tuning(state["config"], "assembly_poll_interval_seconds", 2)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            lock_id = stable_lock_id(state["context_window_id"])
            acquired = await pool.fetchval("SELECT pg_try_advisory_lock($1)", lock_id)
            if acquired:
                await pool.execute("SELECT pg_advisory_unlock($1)", lock_id)
                in_progress = False
            else:
                in_progress = True
        except (ConnectionError, OSError, RuntimeError) as exc:
            _log.warning(
                "Retrieval: Redis error during assembly wait, proceeding: %s", exc
            )
            return {"assembly_status": "ready"}
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


async def ret_load_summaries(state: StandardTieredRetrievalState) -> dict:
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


async def ret_load_recent_messages(state: StandardTieredRetrievalState) -> dict:
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

    tier3_pct = scaled_config.get("tier3_pct", 0.72)
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

    # R2-F11: Adaptive message load limit based on tier3 token budget
    tokens_per_message = get_tuning(state["config"], "tokens_per_message_estimate", 150)
    adaptive_limit = max(50, tier3_budget // tokens_per_message)

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
        adaptive_limit,
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


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text length (approx 4 chars per token)."""
    return max(1, len(text) // 4)


async def ret_assemble_context(state: StandardTieredRetrievalState) -> dict:
    """Assemble the final context messages array from all tiers.

    ARCH-03: Produces a structured messages array matching the OpenAI format.
    ARCH-15: Summaries are inserted as system messages.
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

    context_tiers = {
        "archival_summary": state.get("tier1_summary"),
        "chunk_summaries": state.get("tier2_summaries", []),
        "semantic_messages": [],
        "knowledge_graph_facts": [],
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


def ret_route_after_load_window(state: StandardTieredRetrievalState) -> str:
    if state.get("error"):
        return END
    return "ret_wait_for_assembly"


def ret_route_after_wait(state: StandardTieredRetrievalState) -> str:
    return "ret_load_summaries"


def build_standard_tiered_retrieval():
    """Build and compile the standard-tiered retrieval StateGraph."""
    workflow = StateGraph(StandardTieredRetrievalState)

    workflow.add_node("ret_load_window", ret_load_window)
    workflow.add_node("ret_wait_for_assembly", ret_wait_for_assembly)
    workflow.add_node("ret_load_summaries", ret_load_summaries)
    workflow.add_node("ret_load_recent_messages", ret_load_recent_messages)
    workflow.add_node("ret_assemble_context", ret_assemble_context)

    workflow.set_entry_point("ret_load_window")

    workflow.add_conditional_edges(
        "ret_load_window",
        ret_route_after_load_window,
        {"ret_wait_for_assembly": "ret_wait_for_assembly", END: END},
    )
    workflow.add_conditional_edges(
        "ret_wait_for_assembly",
        ret_route_after_wait,
        {"ret_load_summaries": "ret_load_summaries"},
    )
    workflow.add_edge("ret_load_summaries", "ret_load_recent_messages")
    workflow.add_edge("ret_load_recent_messages", "ret_assemble_context")
    workflow.add_edge("ret_assemble_context", END)

    return workflow.compile()


# Registration handled by context_broker_ae.register — no module-scope side effects.
