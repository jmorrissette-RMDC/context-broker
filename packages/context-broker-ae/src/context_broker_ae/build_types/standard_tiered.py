"""
Standard-tiered build type (ARCH-18).

Three-tier progressive compression context assembly with deadband compaction:
  Tier 3: Archival summary (oldest, most compressed — historical header + recent archival)
  Tier 2: Chunk summaries (middle layer, 3-6 chunks of ~2% each)
  Tier 1: Live verbatim messages (newest, full fidelity — swings 20%-73%)

Compaction rhythm:
  1. Tier 1 fills from floor (20%) toward ceiling (85% - tier2 - tier3).
  2. When tier 1 hits ceiling: oldest content above floor summarized to a 2% chunk,
     appended to tier 2. Tier 1 resets to floor.
  3. When tier 2 reaches max_chunks (6): full compaction consolidates 4 oldest
     tier 2 chunks into tier 3, keeping 2 newest + new chunk = 3 chunks.

15% buffer keeps total under 85% utilization (models degrade past ~85%).

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
from context_broker_ae.build_types.tier_scaling import (
    extract_deadband_config,
    calculate_tier1_ceiling,
)
from context_broker_ae.memory_extraction import clean_for_compaction
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


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text length (approx 4 chars per token)."""
    return max(1, len(text) // 4)



# _deadband_config and _tier1_ceiling are imported from tier_scaling.py
# as extract_deadband_config and calculate_tier1_ceiling


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
    tier1_messages: list[dict]
    older_messages: list[dict]
    chunks: list[list[dict]]
    tier2_summaries: list[str]
    tier3_summary: Optional[str]
    tier2_at_max: bool
    lock_key: str
    lock_token: Optional[str]
    lock_acquired: bool
    had_errors: bool
    assembly_start_time: Optional[float]

    # Output
    error: Optional[str]


async def acquire_assembly_lock(state: StandardTieredAssemblyState) -> dict:
    """Acquire a Postgres advisory lock for this context window."""
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

    # R7-M5: Wrap lock calls in try/except — return error state if unavailable
    try:
        pool = get_pg_pool()  # Advisory lock
        lock_id = stable_lock_id(state["context_window_id"])
        acquired = await pool.fetchval("SELECT pg_try_advisory_lock($1)", lock_id)
    except (RuntimeError, OSError, ConnectionError) as exc:
        _log.error(
            "Lock unavailable during acquisition for window=%s: %s",
            state["context_window_id"],
            exc,
        )
        return {
            "lock_key": lock_key,
            "lock_token": None,
            "lock_acquired": False,
            "error": f"Lock unavailable: {exc}",
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

    R2-F11: Adaptive message load limit based on tier 1 (live) budget.
    D-09: On first assembly (no existing summaries), look back further
    using the initial_lookback_multiplier to provide enough raw material
    for initial summarization.
    """
    pool = get_pg_pool()
    build_type_config = state.get("build_type_config") or {}
    max_budget = state.get("max_token_budget", 8192)
    db = extract_deadband_config(build_type_config)

    # Tier 1 (live) ceiling determines how many messages to look back
    tier1_ceiling = calculate_tier1_ceiling(max_budget, db)
    tokens_per_message = get_tuning(state["config"], "tokens_per_message_estimate", 150)
    adaptive_limit = max(50, tier1_ceiling // tokens_per_message)

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
            # Cap lookback for large budgets — no need to process
            # millions of tokens when the window is already generous.
            max_lookback = build_type_config.get("max_lookback_tokens", 400_000)
            lookback_tokens = min(lookback_tokens, max_lookback)
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


async def calculate_compaction_state(state: StandardTieredAssemblyState) -> dict:
    """Determine if compaction is needed and partition messages into tiers.

    Deadband logic:
    - Tier 1 (live) fills from floor (20%) toward ceiling.
    - If tier 1 is below ceiling, no compaction — just load recent messages.
    - If tier 1 exceeds ceiling, mark content above floor for compaction.
    """
    messages = state["all_messages"]
    if not messages:
        return {"tier1_messages": [], "older_messages": [], "chunks": []}

    build_type_config = state["build_type_config"]
    max_budget = state["max_token_budget"]
    db = extract_deadband_config(build_type_config)

    # Query current tier 2 chunk count for dynamic ceiling calculation
    pool = get_pg_pool()
    current_t2_count = await pool.fetchval(
        """
        SELECT COUNT(*)
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND tier = 2
          AND is_active = TRUE
        """,
        uuid.UUID(state["context_window_id"]),
    )
    current_t2_count = current_t2_count or 0

    tier1_ceiling_tokens = calculate_tier1_ceiling(max_budget, db, current_t2_count)
    tier1_floor_tokens = int(max_budget * db["tier1_floor_pct"])

    # Walk backwards to fill tier 1 up to the ceiling
    tier1_messages = []
    tier1_tokens_used = 0
    tier1_start_seq = messages[-1]["sequence_number"] + 1

    for msg in reversed(messages):
        msg_tokens = msg.get("token_count") or max(
            1, len(msg.get("content") or "") // 4
        )
        if tier1_tokens_used + msg_tokens <= tier1_ceiling_tokens:
            tier1_messages.insert(0, msg)
            tier1_tokens_used += msg_tokens
            tier1_start_seq = msg["sequence_number"]
        else:
            break

    # Messages before tier 1 boundary need summarization
    older_messages = [m for m in messages if m["sequence_number"] < tier1_start_seq]

    # If tier 1 is not at ceiling and there are no older messages, no compaction needed
    if not older_messages:
        return {
            "tier1_messages": tier1_messages,
            "older_messages": [],
            "chunks": [],
            "tier2_at_max": False,
        }

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

    # Check if tier 2 is at max capacity (reuse count from ceiling calc)
    tier2_at_max = current_t2_count >= db["tier2_max_chunks"]

    if max_summarized_seq > 0:
        _log.info(
            "Incremental assembly: %d new messages to summarize (already covered through seq %d)",
            len(unsummarized),
            max_summarized_seq,
        )

    return {
        "tier1_messages": tier1_messages,
        "older_messages": older_messages,
        "chunks": chunks,
        "tier2_at_max": tier2_at_max,
    }


async def compact_tier1(state: StandardTieredAssemblyState) -> dict:
    """When tier 1 hits ceiling, summarize oldest content above floor into a 2% chunk.

    Strips artifacts, summarizes to a single tier 2 chunk, appends to tier 2.
    Tier 1 resets to floor after compaction.
    """
    chunks = state["chunks"]
    if not chunks:
        return {"tier2_summaries": []}

    config = state["config"]
    build_type_config = state.get("build_type_config") or {}
    db = extract_deadband_config(build_type_config)
    max_budget = state.get("max_token_budget", 0)

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

    # Advisory lock (no TTL renewal needed)
    pool = get_pg_pool()

    async def _summarize_chunk(chunk: list[dict]) -> tuple[list[dict], str | None]:
        # Advisory locks: no TTL renewal needed
        chunk_text = "\n".join(
            f"[{m['role']} | {m['sender']}] {m.get('content') or ''}" for m in chunk
        )
        # Strip artifacts before summarization — the LLM summarizes the
        # discussion, not the code blocks/JSON/file listings themselves.
        # File paths and identifiers are preserved as retrieval hooks.
        chunk_text = clean_for_compaction(chunk_text)
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

    # R7-M11: Budget guard — stop inserting summaries if cumulative tier2+tier3
    # tokens would exceed their allocation within the max token budget.
    tier2_budget = int(
        max_budget * db["tier2_chunk_pct"] * db["tier2_max_chunks"]
    )
    tier3_budget = int(max_budget * db["tier3_pct"])
    summary_budget = tier2_budget + tier3_budget
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

        # Memory extraction at compaction time: feed the full chunk context
        # to Mem0 for higher quality extraction. The LLM sees significance
        # in context — a decision spanning 50 messages is visible as a
        # decision, not as 50 individual statements.
        # Fire-and-forget — don't block the assembly pipeline on extraction.
        try:
            from context_broker_ae.memory_extraction import build_memory_extraction

            chunk_text_for_extraction = "\n".join(
                f"{m.get('content') or ''}" for m in chunk if m.get("content")
            )
            if chunk_text_for_extraction:
                extraction_graph = build_memory_extraction()
                asyncio.create_task(
                    extraction_graph.ainvoke({
                        "conversation_id": state["conversation_id"],
                        "config": state["config"],
                        "messages": chunk,
                    })
                )
        except (ImportError, RuntimeError, ValueError, OSError) as exc:
            _log.debug("Compaction-time extraction skipped: %s", exc)

    _log.info(
        "Context assembly: wrote %d tier 2 chunk summaries for window=%s",
        len(new_summaries),
        state["context_window_id"],
    )
    result: dict = {"tier2_summaries": new_summaries}
    if had_errors:
        result["had_errors"] = True
    return result


async def run_full_compaction(state: StandardTieredAssemblyState) -> dict:
    """Consolidate oldest tier 2 chunks into tier 3 archival summary.

    Triggered when tier 2 is at max_chunks and a new chunk needs to be added.
    Tier 3 has two parts:
      - Historical header (~0.25% of window): re-summarized with displaced content
      - Recent archival (~1.75% of window): REPLACED with consolidated chunks

    After full compaction: 4 oldest tier 2 chunks consolidated into tier 3,
    2 newest tier 2 chunks kept + new chunk = back to 3 chunks (6%).
    """
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

    build_type_config = state.get("build_type_config") or {}
    db = extract_deadband_config(build_type_config)

    if len(active_t2) <= db["tier2_min_chunks"]:
        return {"tier3_summary": None}

    keep_recent = db["tier2_min_chunks"] - 1  # Keep 2 newest, consolidate the rest

    # R6-M11: Guard against empty consolidation list
    if len(active_t2) <= keep_recent:
        return {"tier3_summary": None}

    to_consolidate = list(active_t2)[:-keep_recent] if keep_recent > 0 else list(active_t2)

    # M-16: Include existing tier 3 summary (historical header) in consolidation
    existing_t3 = await pool.fetchrow(
        """
        SELECT summary_text
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND tier = 3
          AND is_active = TRUE
        ORDER BY summarizes_to_seq DESC
        LIMIT 1
        """,
        uuid.UUID(state["context_window_id"]),
    )

    consolidation_parts = []
    if existing_t3 and existing_t3["summary_text"]:
        consolidation_parts.append(
            f"[Existing archival summary — historical header]\n{existing_t3['summary_text']}"
        )
    consolidation_parts.extend(r["summary_text"] for r in to_consolidate)
    consolidation_text = "\n\n".join(consolidation_parts)

    config = state["config"]

    # F-06: Use build-type-specific LLM config if available
    effective_config = _resolve_llm_config(config, build_type_config)
    llm_config = effective_config.get("llm", {})

    # M-23: Catch prompt-loading failures
    try:
        archival_prompt = await async_load_prompt("archival_consolidation")
    except RuntimeError as exc:
        _log.error("Failed to load archival_consolidation prompt: %s", exc)
        return {"tier3_summary": None, "error": f"Prompt loading failed: {exc}"}

    llm = get_chat_model(effective_config, role="summarization")

    messages = [
        SystemMessage(content=archival_prompt),
        HumanMessage(content=consolidation_text),
    ]

    # R7-M12: Renew lock TTL before the LLM call (advisory locks: no-op)
    lock_key = state.get("lock_key", "")
    lock_token = state.get("lock_token")
    if lock_key and lock_token:
        try:
            pass  # Advisory locks: no TTL renewal needed
        except (RuntimeError, OSError, ConnectionError) as exc:
            _log.warning("Failed to renew lock TTL for archival consolidation: %s", exc)

    try:
        # Two-part tier 3: historical header + recent archival
        # 1. Recent archival: consolidate the displaced tier 2 chunks
        recent_archival_text = "\n\n".join(r["summary_text"] for r in to_consolidate)
        recent_response = await llm.ainvoke([
            SystemMessage(content=archival_prompt),
            HumanMessage(content=f"Consolidate these chunk summaries into a recent archival summary:\n\n{recent_archival_text}"),
        ])
        recent_archival = recent_response.content

        # 2. Historical header: re-summarize existing header with high-level
        #    summary of what's being displaced into recent archival
        existing_header = ""
        if existing_t3 and existing_t3["summary_text"]:
            existing_header = existing_t3["summary_text"]

        header_input = (
            f"[Existing historical header]\n{existing_header}\n\n"
            f"[New content being archived]\n{recent_archival}"
            if existing_header
            else f"[First archival — create historical header]\n{recent_archival}"
        )
        header_response = await llm.ainvoke([
            SystemMessage(content=(
                "You are maintaining a brief historical header for a long conversation. "
                "The header records: origin date, participants, major topics, key decisions. "
                "It should be very short — a few sentences at most. "
                "Update the header with any new major topics or decisions from the archived content. "
                "Do NOT reproduce the archived content — just update the header facts."
            )),
            HumanMessage(content=header_input),
        ])
        header_text = header_response.content

        if recent_archival and header_text:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Deactivate existing tier 3 archival (both header and recent)
                    await conn.execute(
                        """
                        UPDATE conversation_summaries
                        SET is_active = FALSE
                        WHERE context_window_id = $1 AND tier = 3 AND is_active = TRUE
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

                    # Insert historical header (seq 0 to 0 — always first)
                    await conn.execute(
                        """
                        INSERT INTO conversation_summaries
                            (conversation_id, context_window_id, summary_text, tier,
                             summarizes_from_seq, summarizes_to_seq, message_count,
                             summarized_by_model)
                        VALUES ($1, $2, $3, 3, 0, 0, 0, $4)
                        """,
                        uuid.UUID(state["conversation_id"]),
                        uuid.UUID(state["context_window_id"]),
                        header_text,
                        llm_config.get("model", "unknown"),
                    )

                    # Insert recent archival (covers the consolidated chunk range)
                    await conn.execute(
                        """
                        INSERT INTO conversation_summaries
                            (conversation_id, context_window_id, summary_text, tier,
                             summarizes_from_seq, summarizes_to_seq, message_count,
                             summarized_by_model)
                        VALUES ($1, $2, $3, 3, $4, $5, $6, $7)
                        """,
                        uuid.UUID(state["conversation_id"]),
                        uuid.UUID(state["context_window_id"]),
                        recent_archival,
                        to_consolidate[0]["summarizes_from_seq"],
                        to_consolidate[-1]["summarizes_to_seq"],
                        sum(r.get("message_count") or 0 for r in to_consolidate),
                        llm_config.get("model", "unknown"),
                    )

            archival_text = f"{header_text}\n\n{recent_archival}"

            _log.info(
                "Full compaction: consolidated %d tier 2 chunks into tier 3 for window=%s",
                len(to_consolidate),
                state["context_window_id"],
            )
            return {"tier3_summary": archival_text}

    except (openai.APIError, httpx.HTTPError, ValueError) as exc:
        _log.error(
            "Archival consolidation failed for window=%s: %s",
            state["context_window_id"],
            exc,
        )
        return {"tier3_summary": None, "had_errors": True}

    return {"tier3_summary": None}


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
    return "calculate_compaction_state"


def route_after_compaction_state(state: StandardTieredAssemblyState) -> str:
    if state.get("error"):
        return "release_assembly_lock"
    if not state.get("chunks"):
        return "run_full_compaction"
    return "compact_tier1"


def route_after_compact_tier1(state: StandardTieredAssemblyState) -> str:
    if state.get("error"):
        return "release_assembly_lock"
    # Full compaction only triggers when tier 2 was at max capacity
    # before the new chunk was added. If tier 2 had room, just finalize.
    if state.get("tier2_at_max"):
        return "run_full_compaction"
    return "finalize_assembly"


def build_standard_tiered_assembly():
    """Build and compile the standard-tiered assembly StateGraph."""
    workflow = StateGraph(StandardTieredAssemblyState)

    workflow.add_node("acquire_assembly_lock", acquire_assembly_lock)
    workflow.add_node("load_window_config", load_window_config)
    workflow.add_node("load_messages", load_messages)
    workflow.add_node("calculate_compaction_state", calculate_compaction_state)
    workflow.add_node("compact_tier1", compact_tier1)
    workflow.add_node("run_full_compaction", run_full_compaction)
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
            "calculate_compaction_state": "calculate_compaction_state",
            "finalize_assembly": "finalize_assembly",
            "release_assembly_lock": "release_assembly_lock",
        },
    )
    workflow.add_conditional_edges(
        "calculate_compaction_state",
        route_after_compaction_state,
        {
            "compact_tier1": "compact_tier1",
            "run_full_compaction": "run_full_compaction",
            "release_assembly_lock": "release_assembly_lock",
        },
    )
    workflow.add_conditional_edges(
        "compact_tier1",
        route_after_compact_tier1,
        {
            "run_full_compaction": "run_full_compaction",
            "finalize_assembly": "finalize_assembly",
            "release_assembly_lock": "release_assembly_lock",
        },
    )
    workflow.add_edge("run_full_compaction", "finalize_assembly")
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
    tier3_summary: Optional[str]
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

    R6-M9: If advisory lock is unavailable, proceed without waiting rather than crashing.
    """
    try:
        pool = get_pg_pool()  # Advisory lock
    except RuntimeError:
        _log.warning("Retrieval: pool not available, proceeding without assembly wait")
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
                "Retrieval: lock error during assembly wait, proceeding: %s", exc
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
    """Load active tier 3 (archival) and tier 2 (chunk) summaries."""
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

    tier3 = None
    tier2_list = []
    for s in summaries:
        if s["tier"] == 3:
            tier3 = s["summary_text"]
        elif s["tier"] == 2:
            tier2_list.append(s["summary_text"])

    return {"tier3_summary": tier3, "tier2_summaries": tier2_list}


async def ret_load_recent_messages(state: StandardTieredRetrievalState) -> dict:
    """Load tier 1 (live) recent verbatim messages within the remaining token budget.

    Uses deadband config to determine tier 1 ceiling.
    """
    pool = get_pg_pool()
    build_type_config = state["build_type_config"]
    max_budget = state["max_token_budget"]
    db = extract_deadband_config(build_type_config)

    current_t2_chunks = len(state.get("tier2_summaries") or [])
    tier1_ceiling_tokens = calculate_tier1_ceiling(max_budget, db, current_t2_chunks)

    # Calculate tokens already used by summaries
    summary_tokens = 0
    if state.get("tier3_summary"):
        summary_tokens += len(state["tier3_summary"]) // 4
    for s in state.get("tier2_summaries", []):
        summary_tokens += len(s) // 4

    remaining_budget = max(0, min(tier1_ceiling_tokens, max_budget - summary_tokens))

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

    # R2-F11: Adaptive message load limit based on tier 1 token budget
    tokens_per_message = get_tuning(state["config"], "tokens_per_message_estimate", 150)
    adaptive_limit = max(50, tier1_ceiling_tokens // tokens_per_message)

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


async def ret_assemble_context(state: StandardTieredRetrievalState) -> dict:
    """Assemble the final context messages array from all tiers.

    ARCH-03: Produces a structured messages array matching the OpenAI format.
    ARCH-15: Summaries are inserted as system messages.

    Prompt ordering optimized for prefix caching:
      1. Tier 3 (archival) FIRST — most static, changes once per ~2.1M tokens
      2. Tier 2 (chunks) NEXT — grows monotonically between full compactions
      3. Tier 1 (live) LAST — changes every turn
    """
    max_budget = state.get("max_token_budget", 0)
    cumulative_tokens = 0
    messages: list[dict] = []

    # Tier 3: Archival summary (most static — best prefix cache hit rate)
    if state.get("tier3_summary"):
        content = f"[Archival context]\n{state['tier3_summary']}"
        cumulative_tokens += _estimate_tokens(content)
        messages.append({"role": "system", "content": content})

    # Tier 2: Chunk summaries (grows monotonically between full compactions)
    if state.get("tier2_summaries"):
        content = "[Recent summaries]\n" + "\n\n".join(state["tier2_summaries"])
        cumulative_tokens += _estimate_tokens(content)
        messages.append({"role": "system", "content": content})

    # Tier 1: Live verbatim messages (M-08: newest first truncation)
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
        "archival_summary": state.get("tier3_summary"),
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
