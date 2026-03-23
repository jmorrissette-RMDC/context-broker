"""
Embed Pipeline — LangGraph StateGraph flow (background).

Generates vector embeddings for stored messages using the configured
LangChain embedding model. After embedding, enqueues context assembly
jobs for all context windows attached to the conversation.

Triggered by ARQ worker consuming from embedding_jobs queue.
"""

import json
import logging
import time as _time_mod
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
import openai
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_embeddings_model, get_tuning, verbose_log
from app.database import get_pg_pool, get_redis
from app.metrics_registry import JOBS_ENQUEUED

_log = logging.getLogger("context_broker.flows.embed_pipeline")


class EmbedPipelineState(TypedDict):
    """State for the embedding pipeline."""

    # Inputs
    message_id: str
    conversation_id: str
    config: dict

    # Intermediate
    message: Optional[dict]
    embedding: Optional[list[float]]

    # Outputs
    assembly_jobs_queued: list[str]
    error: Optional[str]


async def fetch_message(state: EmbedPipelineState) -> dict:
    """Fetch the message row from PostgreSQL."""
    verbose_log(state["config"], _log, "embed_pipeline.fetch_message ENTER msg=%s", state["message_id"])
    pool = get_pg_pool()
    row = await pool.fetchrow(
        "SELECT * FROM conversation_messages WHERE id = $1",
        uuid.UUID(state["message_id"]),
    )
    if row is None:
        return {"error": f"Message {state['message_id']} not found"}
    return {"message": dict(row)}


async def generate_embedding(state: EmbedPipelineState) -> dict:
    """Generate a vector embedding for the message content.

    Uses the configured LangChain OpenAIEmbeddings model.
    Builds contextual embedding using prior messages as prefix.
    """
    _t0 = _time_mod.monotonic()
    config = state["config"]
    verbose_log(config, _log, "embed_pipeline.generate_embedding ENTER msg=%s", state["message_id"])
    message = state["message"]

    # ARCH-01: Tool-call messages may have NULL content — skip embedding entirely.
    # This is not an error; tool-call messages carry structured data, not text.
    if not message.get("content"):
        _log.info(
            "Skipping embedding for message %s: content is NULL (tool-call message)",
            state["message_id"],
        )
        return {"embedding": None}

    embeddings_config = config.get("embeddings", {})

    # Build contextual embedding text using prior messages
    context_window_size = embeddings_config.get("context_window_size", 3)
    embed_text = message["content"]

    if context_window_size > 0:
        pool = get_pg_pool()
        prior_rows = await pool.fetch(
            """
            SELECT role, content
            FROM conversation_messages
            WHERE conversation_id = $1
              AND sequence_number < $2
            ORDER BY sequence_number DESC
            LIMIT $3
            """,
            uuid.UUID(state["conversation_id"]),
            message["sequence_number"],
            context_window_size,
        )
        if prior_rows:
            prior_lines = "\n".join(
                f"[{r['role']}] {(r.get('content') or '')[:get_tuning(state['config'], 'content_truncation_chars', 500)]}"
                for r in reversed(prior_rows)
            )
            embed_text = f"[Context]\n{prior_lines}\n[Current]\n{message['content']}"

    # Use cached LangChain embeddings model
    embeddings_model = get_embeddings_model(config)

    try:
        embedding_vector = await embeddings_model.aembed_query(embed_text)
        return {"embedding": embedding_vector}
    except (openai.APIError, httpx.HTTPError, ValueError) as exc:
        _log.error(
            "Embedding generation failed for message %s: %s",
            state["message_id"],
            exc,
        )
        return {"error": f"Embedding failed: {exc}"}


async def store_embedding(state: EmbedPipelineState) -> dict:
    """Write the embedding vector to the message row in PostgreSQL.

    G5-10: This overwrites any existing embedding for the message. This is
    acceptable because embeddings are deterministic for a given content +
    model, so re-embedding produces the same (or improved) vector.
    """
    pool = get_pg_pool()
    embedding = state["embedding"]

    # R6-OPUS-08: asyncpg needs string format for pgvector. The ::vector cast
    # converts the string representation to the vector type.
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    await pool.execute(
        "UPDATE conversation_messages SET embedding = $1::vector WHERE id = $2",
        vec_str,
        uuid.UUID(state["message_id"]),
    )
    return {}


async def enqueue_context_assembly(state: EmbedPipelineState) -> dict:
    """Enqueue context assembly jobs for all context windows on this conversation.

    Each context window gets its own assembly job. Uses Redis distributed
    locks to prevent concurrent assembly of the same window.

    F-08: Only queues assembly when new tokens since last assembly exceed the
    trigger threshold (percentage of max_token_budget).
    """
    pool = get_pg_pool()
    redis = get_redis()
    config = state["config"]

    windows = await pool.fetch(
        "SELECT id, build_type, max_token_budget, last_assembled_at FROM context_windows WHERE conversation_id = $1",
        uuid.UUID(state["conversation_id"]),
    )

    # Get conversation token count for threshold check
    conv = await pool.fetchrow(
        "SELECT estimated_token_count FROM conversations WHERE id = $1",
        uuid.UUID(state["conversation_id"]),
    )
    conv_tokens = conv["estimated_token_count"] if conv else 0

    queued = []
    now = datetime.now(timezone.utc).isoformat()

    # G5-11: Batch the Redis EXISTS checks instead of N+1 queries per window.
    # Use mget on lock keys to check all windows in a single round-trip.
    window_ids = [str(w["id"]) for w in windows]
    lock_keys = [f"assembly_in_progress:{wid}" for wid in window_ids]
    lock_values = await redis.mget(*lock_keys) if lock_keys else []

    # Build a set of windows that already have assembly in progress
    locked_window_ids = {
        wid for wid, val in zip(window_ids, lock_values) if val is not None
    }

    # G5-11: Batch the token-since-last-assembly queries for windows that
    # have a last_assembled_at. Group by last_assembled_at to reduce queries.
    # (Each distinct timestamp still needs its own query, but windows sharing
    # the same timestamp are batched.)

    # R5-M10: Batch-fetch token counts since last assembly for all windows
    # instead of N+1 individual queries per window. One query fetches tokens
    # added after each distinct last_assembled_at timestamp.
    tokens_since_map: dict[str, int] = {}
    windows_needing_token_check = [
        w for w in windows
        if str(w["id"]) not in locked_window_ids and w["last_assembled_at"] is not None
    ]
    if windows_needing_token_check:
        # Batch query: for each window's last_assembled_at, get sum of tokens
        # added after that timestamp. Uses a VALUES list to do it in one round-trip.
        token_rows = await pool.fetch(
            """
            SELECT w.id AS window_id, COALESCE(SUM(m.token_count), 0) AS tokens_since
            FROM (SELECT unnest($1::uuid[]) AS id, unnest($2::timestamptz[]) AS last_assembled_at) w
            LEFT JOIN conversation_messages m
              ON m.conversation_id = $3
              AND m.created_at > w.last_assembled_at
            GROUP BY w.id
            """,
            [w["id"] for w in windows_needing_token_check],
            [w["last_assembled_at"] for w in windows_needing_token_check],
            uuid.UUID(state["conversation_id"]),
        )
        for row in token_rows:
            tokens_since_map[str(row["window_id"])] = row["tokens_since"]

    for window in windows:
        window_id = str(window["id"])

        if window_id in locked_window_ids:
            _log.debug(
                "Skipping assembly queue: already in progress for window=%s",
                window_id,
            )
            continue

        # F-08: Check trigger threshold — only queue if enough new tokens.
        # R6-M16: Known approximation — the threshold check counts all tokens since
        # last assembly, which for shared conversations may include tokens from other
        # participants' messages. Acceptable for triggering purposes (over-triggering
        # is safe, under-triggering is not).
        max_budget = window["max_token_budget"]
        bt_config = config.get("build_types", {}).get(window["build_type"], {})
        # CB-R3-03: The double-fallback (bt_config -> global tuning -> hardcoded 0.1)
        # is intentional: build-type-specific threshold takes priority, then the
        # global tuning knob, then a safe default. This avoids unnecessary assembly
        # when no threshold is configured at any level.
        trigger_pct = float(bt_config.get("trigger_threshold_percent", get_tuning(config, "trigger_threshold_percent", 0.1)))
        threshold_tokens = int(max_budget * trigger_pct)

        if window["last_assembled_at"] is not None:
            # R5-M10: Use batch-fetched token count instead of per-window query
            tokens_since = tokens_since_map.get(window_id, 0)
            if tokens_since < threshold_tokens:
                _log.debug(
                    "Skipping assembly: tokens_since=%d < threshold=%d for window=%s",
                    tokens_since,
                    threshold_tokens,
                    window_id,
                )
                continue

        # G5-12: Use SET NX for atomic dedup instead of racy EXISTS-then-LPUSH.
        # The dedup key expires after a short TTL to allow retries.
        dedup_key = f"assembly_dedup:{window_id}"
        dedup_acquired = await redis.set(dedup_key, "1", ex=60, nx=True)
        if not dedup_acquired:
            _log.debug(
                "Skipping assembly queue: dedup key exists for window=%s",
                window_id,
            )
            continue

        job = json.dumps(
            {
                "job_type": "assemble_context",
                "context_window_id": window_id,
                "conversation_id": state["conversation_id"],
                "build_type": window["build_type"],
                "enqueued_at": now,
            }
        )
        await redis.lpush("context_assembly_jobs", job)
        queued.append(window_id)
        JOBS_ENQUEUED.labels(job_type="assemble_context").inc()
        _log.info("Queued context assembly for window=%s", window_id)

    return {"assembly_jobs_queued": queued}


def route_after_fetch(state: EmbedPipelineState) -> str:
    """Route: if message not found, end. Otherwise generate embedding."""
    if state.get("error"):
        return END
    return "generate_embedding"


def route_after_embed(state: EmbedPipelineState) -> str:
    """Route: if embedding failed or skipped (null content), end. Otherwise store it."""
    if state.get("error"):
        return END
    if state.get("embedding") is None:
        return "enqueue_context_assembly"
    return "store_embedding"


def build_embed_pipeline() -> StateGraph:
    """Build and compile the embedding pipeline StateGraph."""
    workflow = StateGraph(EmbedPipelineState)

    workflow.add_node("fetch_message", fetch_message)
    workflow.add_node("generate_embedding", generate_embedding)
    workflow.add_node("store_embedding", store_embedding)
    workflow.add_node("enqueue_context_assembly", enqueue_context_assembly)

    workflow.set_entry_point("fetch_message")

    workflow.add_conditional_edges(
        "fetch_message",
        route_after_fetch,
        {"generate_embedding": "generate_embedding", END: END},
    )

    workflow.add_conditional_edges(
        "generate_embedding",
        route_after_embed,
        {
            "store_embedding": "store_embedding",
            "enqueue_context_assembly": "enqueue_context_assembly",
            END: END,
        },
    )

    workflow.add_edge("store_embedding", "enqueue_context_assembly")
    workflow.add_edge("enqueue_context_assembly", END)

    return workflow.compile()
