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
from app.database import get_pg_pool
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
    verbose_log(
        state["config"],
        _log,
        "embed_pipeline.fetch_message ENTER msg=%s",
        state["message_id"],
    )
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
    verbose_log(
        config,
        _log,
        "embed_pipeline.generate_embedding ENTER msg=%s",
        state["message_id"],
    )
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
    """No-op: assembly is handled by the DB-driven background worker.

    The worker checks for context windows needing reassembly after
    each embedding batch. No explicit enqueue needed.
    """
    return {"assembly_jobs_queued": []}


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
