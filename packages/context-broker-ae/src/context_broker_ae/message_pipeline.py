"""
Message Pipeline — LangGraph StateGraph flow.

Handles conv_store_message:
  resolve_conversation -> store_message -> enqueue_jobs

Embedding, context assembly, and memory extraction happen asynchronously
via ARQ background workers after this flow completes.
"""

import json
import logging
import time
import uuid
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

import asyncpg

from app.config import verbose_log_auto
from app.database import get_pg_pool

_log = logging.getLogger("context_broker.flows.message_pipeline")

# ARCH-14: Priority derived from role
_ROLE_PRIORITY = {
    "user": 1,
    "assistant": 2,
    "system": 3,
    "tool": 4,
}


class MessagePipelineState(TypedDict):
    """State for the message ingestion pipeline."""

    # Inputs
    context_window_id: Optional[str]  # ARCH-04: replaces conversation_id
    conversation_id_input: Optional[str]  # Direct conversation_id bypass
    role: str
    sender: str  # ARCH-13: was sender_id
    recipient: Optional[str]  # ARCH-13: was recipient_id
    content: Optional[str]  # ARCH-01: now nullable
    model_name: Optional[str]
    tool_calls: Optional[list[dict]]  # ARCH-01: tool calls as list of dicts (JSONB)
    tool_call_id: Optional[str]  # ARCH-01: tool call ID for tool responses

    # Outputs set by nodes
    message_id: Optional[str]
    conversation_id: Optional[str]  # ARCH-04: looked up from context_windows
    sequence_number: Optional[int]
    # R6-m8: was_duplicate removed — always False after dedup was replaced by collapse logic
    was_collapsed: bool
    queued_jobs: list[str]
    error: Optional[str]


async def store_message(state: MessagePipelineState) -> dict:
    """Insert the message into conversation_messages and update conversation counters.

    Deduplication is handled by the repeat_count collapse logic for
    consecutive identical messages from the same sender.
    """
    _t0 = time.monotonic()
    verbose_log_auto(
        _log,
        "store_message ENTER context_window=%s conversation_id_input=%s sender=%s",
        state.get("context_window_id"),
        state.get("conversation_id_input"),
        state["sender"],
    )
    pool = get_pg_pool()

    # Resolve conversation_id from whichever identifier was provided
    cw_id = state.get("context_window_id")
    conv_id_input = state.get("conversation_id_input")

    if cw_id:
        # ARCH-04: Look up conversation_id from context_windows table
        cw_row = await pool.fetchrow(
            "SELECT conversation_id FROM context_windows WHERE id = $1",
            uuid.UUID(cw_id),
        )
        if cw_row is None:
            return {"error": f"Context window {cw_id} not found"}
        conversation_id = str(cw_row["conversation_id"])
    elif conv_id_input:
        # Direct conversation_id — skip context window lookup
        conversation_id = conv_id_input
    else:
        return {
            "error": "At least one of context_window_id or conversation_id must be provided"
        }

    conv_uuid = uuid.UUID(conversation_id)

    # Verify conversation exists
    conversation = await pool.fetchrow(
        "SELECT id FROM conversations WHERE id = $1",
        conv_uuid,
    )
    if conversation is None:
        return {"error": f"Conversation {conversation_id} not found"}

    # ARCH-10: Compute token_count internally from content length
    content = state.get("content")
    effective_token_count = max(1, len(content) // 4) if content else 0

    # ARCH-14: Derive priority from role
    priority = _ROLE_PRIORITY.get(state["role"], 2)

    # ARCH-12: Default recipient from role if not provided
    recipient = state.get("recipient")
    if not recipient:
        if state["role"] == "assistant":
            recipient = "user"
        elif state["role"] == "user":
            recipient = "assistant"
        elif state["role"] == "system":
            recipient = "all"
        elif state["role"] == "tool":
            recipient = "assistant"
        else:
            recipient = "all"

    # Retry once on UniqueViolationError (edge case with advisory lock)
    row = None  # CB-R3-04: Initialize before retry loop for safety
    for _attempt in range(2):
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # B-02: Advisory lock per conversation to serialize inserts
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1::text))",
                        conversation_id,
                    )

                    # F-04: Check for consecutive duplicate message (same sender + content)
                    prev_msg = await conn.fetchrow(
                        """
                        SELECT id, sender, content, repeat_count
                        FROM conversation_messages
                        WHERE conversation_id = $1
                        ORDER BY sequence_number DESC
                        LIMIT 1
                        """,
                        conv_uuid,
                    )
                    if (
                        prev_msg is not None
                        and prev_msg["sender"] == state["sender"]
                        and prev_msg["content"] == content
                        and content is not None
                    ):
                        # Collapse: increment repeat_count instead of inserting.
                        # R6-M17: Collapse increments repeat_count but NOT total_messages
                        # because no new message was added. estimated_token_count is also
                        # unchanged because the collapsed message has the same content.
                        new_count = (prev_msg["repeat_count"] or 1) + 1
                        await conn.execute(
                            "UPDATE conversation_messages SET repeat_count = $1 WHERE id = $2",
                            new_count,
                            prev_msg["id"],
                        )
                        await conn.execute(
                            "UPDATE conversations SET updated_at = NOW() WHERE id = $1",
                            conv_uuid,
                        )
                        _log.info(
                            "Collapsed duplicate message: sender=%s repeat_count=%d",
                            state["sender"],
                            new_count,
                        )
                        return {
                            "message_id": str(prev_msg["id"]),
                            "conversation_id": conversation_id,
                            "sequence_number": None,
                            "was_collapsed": True,
                            "queued_jobs": [],
                        }

                    # Atomic sequence number assignment
                    row = await conn.fetchrow(
                        """
                        INSERT INTO conversation_messages
                            (conversation_id, role, sender, recipient, content,
                             priority, token_count, model_name,
                             tool_calls, tool_call_id, sequence_number)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                                (SELECT COALESCE(MAX(sequence_number), 0) + 1
                                 FROM conversation_messages
                                 WHERE conversation_id = $1))
                        RETURNING id, sequence_number
                        """,
                        conv_uuid,
                        state["role"],
                        state["sender"],
                        recipient,
                        content,
                        priority,
                        effective_token_count,
                        state.get("model_name"),
                        (
                            json.dumps(state["tool_calls"])
                            if state.get("tool_calls")
                            else None
                        ),
                        state.get("tool_call_id"),
                    )

                    # Update conversation counters
                    await conn.execute(
                        """
                        UPDATE conversations
                        SET total_messages = total_messages + 1,
                            estimated_token_count = estimated_token_count + $1,
                            updated_at = NOW()
                        WHERE id = $2
                        """,
                        effective_token_count,
                        conv_uuid,
                    )
            # Success — break out of retry loop
            break
        except asyncpg.UniqueViolationError:
            if _attempt == 0:
                _log.warning(
                    "Sequence number conflict for conv=%s, retrying once",
                    conversation_id,
                )
                continue
            _log.error(
                "Sequence number conflict persisted after retry for conv=%s",
                conversation_id,
            )
            return {
                "error": f"Failed to assign sequence number for conversation {conversation_id}"
            }

    verbose_log_auto(
        _log,
        "store_message EXIT conv=%s msg=%s duration_ms=%d",
        conversation_id,
        str(row["id"]),
        int((time.monotonic() - _t0) * 1000),
    )
    return {
        "message_id": str(row["id"]),
        "conversation_id": conversation_id,
        "sequence_number": row["sequence_number"],
        "was_collapsed": False,
    }


def route_after_store(state: MessagePipelineState) -> str:
    """Route: always END. Background processing is DB-driven — workers
    poll for unembedded/unextracted messages automatically."""
    return END


def build_message_pipeline() -> StateGraph:
    """Build and compile the message ingestion StateGraph.

    Stores the message to Postgres and returns. Background processing
    (embedding, extraction, assembly) is handled by the DB-driven worker
    which polls for messages with NULL embeddings / unextracted flags.
    No queue enqueue step needed.
    """
    workflow = StateGraph(MessagePipelineState)

    workflow.add_node("store_message", store_message)
    workflow.set_entry_point("store_message")
    workflow.add_edge("store_message", END)

    return workflow.compile()
