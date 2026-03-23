"""
Memory Extraction — LangGraph StateGraph flow (background).

Extracts knowledge from conversation messages into the Neo4j knowledge graph
via Mem0's native APIs.

  acquire_lock -> fetch_unextracted -> build_extraction_text
  -> run_mem0_extraction -> mark_extracted -> release_lock

Triggered by ARQ worker consuming from memory_extraction_jobs queue.
"""

import asyncio
import logging
import re
import uuid
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_tuning, verbose_log
from app.database import get_pg_pool, get_redis

_log = logging.getLogger("context_broker.flows.memory_extraction")

# Secret redaction patterns — applied before Mem0 ingestion
_SECRET_PATTERNS = [
    (re.compile(r'(?i)(api[_-]?key|token|secret|password|credential)["\s:=]+["\']?[\w\-\.]{20,}'), '[REDACTED]'),
    (re.compile(r'(?i)bearer\s+[\w\-\.]{20,}'), 'Bearer [REDACTED]'),
    (re.compile(r'sk-[a-zA-Z0-9\-]{20,}'), '[REDACTED]'),
    (re.compile(r'(?i)(aws|gcp|azure)[_-]?[\w]*[_-]?(key|secret|token)["\s:=]+["\']?[\w\-\.]{16,}'), '[REDACTED]'),
]


def _redact_secrets(text: str) -> str:
    """Strip potential secrets from text before Mem0 ingestion.

    Note: This is a heuristic regex-based approach covering common patterns
    (API keys, bearer tokens, sk- keys, cloud provider secrets). It does not
    catch all secret forms (PEM blocks, JWTs, connection strings, etc.).
    For production use with sensitive data, consider integrating detect-secrets
    or a dedicated secret scanning library.
    """
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class MemoryExtractionState(TypedDict):
    """State for the memory extraction pipeline."""

    # Inputs
    conversation_id: str
    config: dict

    # Intermediate
    messages: list[dict]
    user_id: str
    extraction_text: str
    selected_message_ids: list[str]
    fully_extracted_ids: list[str]
    lock_key: str
    lock_token: Optional[str]
    lock_acquired: bool

    # Output
    extracted_count: int
    error: Optional[str]


async def acquire_extraction_lock(state: MemoryExtractionState) -> dict:
    """Acquire a Redis lock to prevent concurrent extraction of the same conversation."""
    verbose_log(state["config"], _log, "memory_extraction.acquire_lock ENTER conv=%s", state["conversation_id"])
    lock_key = f"extraction_in_progress:{state['conversation_id']}"
    lock_token = str(uuid.uuid4())
    redis = get_redis()

    acquired = await redis.set(lock_key, lock_token, ex=get_tuning(state["config"], "extraction_lock_ttl_seconds", 180), nx=True)
    if not acquired:
        _log.info(
            "Memory extraction: lock not acquired for conv=%s — skipping",
            state["conversation_id"],
        )
        return {"lock_key": lock_key, "lock_token": None, "lock_acquired": False}

    return {"lock_key": lock_key, "lock_token": lock_token, "lock_acquired": True}


async def fetch_unextracted_messages(state: MemoryExtractionState) -> dict:
    """Fetch messages that have not yet been memory-extracted."""
    pool = get_pg_pool()

    rows = await pool.fetch(
        """
        SELECT id, role, sender, content, sequence_number
        FROM conversation_messages
        WHERE conversation_id = $1
          AND (memory_extracted IS NOT TRUE)
          AND role IN ('user', 'assistant')
        ORDER BY sequence_number ASC
        """,
        uuid.UUID(state["conversation_id"]),
    )

    if not rows:
        return {"messages": [], "extracted_count": 0}

    # Get user_id from conversation (use participant_id from first context window)
    pool2 = get_pg_pool()
    window = await pool2.fetchrow(
        "SELECT participant_id FROM context_windows WHERE conversation_id = $1 ORDER BY created_at ASC LIMIT 1",
        uuid.UUID(state["conversation_id"]),
    )
    user_id = window["participant_id"] if window else "default"

    return {"messages": [dict(r) for r in rows], "user_id": user_id}


async def build_extraction_text(state: MemoryExtractionState) -> dict:
    """Build the text to send to Mem0 for extraction.

    Limits text size to avoid overwhelming the LLM.
    Selects messages newest-first up to the character budget.
    """
    messages = state["messages"]
    max_chars = get_tuning(state["config"], "extraction_max_chars", 90000)

    lines = []
    for msg in reversed(messages):
        # ARCH-01: Skip messages with NULL content (tool-call messages)
        msg_content = msg.get("content") or ""
        if not msg_content:
            continue
        lines.append(
            (str(msg["id"]), f"{msg['role']} ({msg['sender']}): {msg_content}")
        )

    selected_ids = []
    fully_extracted_ids = []
    selected_lines = []
    total_chars = 0

    for msg_id, line in lines:
        if total_chars + len(line) + 1 > max_chars:
            if not selected_ids:
                # Always include at least one message, but it's truncated
                selected_ids.append(msg_id)
                selected_lines.append(line[:max_chars])
                # Not fully extracted — truncated, so do NOT add to fully_extracted_ids
            break
        selected_ids.append(msg_id)
        fully_extracted_ids.append(msg_id)
        selected_lines.append(line)
        total_chars += len(line) + 1

    # Restore chronological order
    selected_ids.reverse()
    fully_extracted_ids.reverse()
    selected_lines.reverse()

    extraction_text = "\n".join(selected_lines)
    extraction_text = _redact_secrets(extraction_text)

    return {
        "extraction_text": extraction_text,
        "selected_message_ids": selected_ids,
        "fully_extracted_ids": fully_extracted_ids,
    }


async def run_mem0_extraction(state: MemoryExtractionState) -> dict:
    """Call Mem0 to extract knowledge from the conversation text.

    Mem0 operations are synchronous — run in thread pool executor.
    """
    config = state["config"]

    try:
        from app.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            _log.warning(
                "Memory extraction: Mem0 not available for conv=%s",
                state["conversation_id"],
            )
            return {"error": "Mem0 client not available"}

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: mem0.add(
                state["extraction_text"],
                user_id=state["user_id"],
                run_id=state["conversation_id"],
            ),
        )

        _log.info(
            "Memory extraction: extracted from %d messages for conv=%s",
            len(state["selected_message_ids"]),
            state["conversation_id"],
        )
        return {"error": None}

    except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:
        # G5-18: Broad exception handling for Mem0/Neo4j failures.
        # Mem0 wraps Neo4j driver errors and other backend failures in
        # various exception types. We catch broadly here to ensure
        # graceful degradation rather than crashing the worker.
        _log.error(
            "Memory extraction failed for conv=%s: %s",
            state["conversation_id"],
            exc,
        )
        return {"error": str(exc)}


async def mark_messages_extracted(state: MemoryExtractionState) -> dict:
    """Mark fully extracted messages as memory_extracted=TRUE in PostgreSQL.

    Only marks messages whose content was fully included in the extraction text.
    Truncated (partially extracted) messages are excluded so they will be
    picked up again on the next extraction run.
    """
    if state.get("error"):
        return {}

    fully_extracted = state.get("fully_extracted_ids", [])
    if not fully_extracted:
        return {"extracted_count": 0}

    pool = get_pg_pool()
    message_uuids = [uuid.UUID(mid) for mid in fully_extracted]

    await pool.execute(
        """
        UPDATE conversation_messages
        SET memory_extracted = TRUE
        WHERE id = ANY($1::uuid[])
        """,
        message_uuids,
    )

    return {"extracted_count": len(message_uuids)}


async def _atomic_lock_release(redis_client, lock_key: str, lock_token: str) -> bool:
    """Atomically release a Redis lock only if we still own it (CB-R3-02).

    Uses a Lua script to perform check-and-delete in a single atomic operation,
    preventing the race where another worker acquires the lock between our GET
    and DELETE.

    Returns True if the lock was released, False if it was already gone or
    owned by another worker.
    """
    lua_script = """
    if redis.call("GET", KEYS[1]) == ARGV[1] then
        return redis.call("DEL", KEYS[1])
    else
        return 0
    end
    """
    result = await redis_client.eval(lua_script, 1, lock_key, lock_token)
    return result == 1


async def release_extraction_lock(state: MemoryExtractionState) -> dict:
    """Release the Redis extraction lock.

    CB-R3-02: Uses atomic Lua script to prevent race between GET and DELETE.
    """
    lock_key = state.get("lock_key", "")
    lock_token = state.get("lock_token")
    if lock_key and state.get("lock_acquired") and lock_token:
        redis = get_redis()
        released = await _atomic_lock_release(redis, lock_key, lock_token)
        if not released:
            _log.debug(
                "Lock %s was not released (expired or taken by another worker)",
                lock_key,
            )
    return {}


def route_after_lock(state: MemoryExtractionState) -> str:
    """Route: if lock not acquired, end. Otherwise fetch messages."""
    if not state.get("lock_acquired"):
        return END
    return "fetch_unextracted_messages"


def route_after_fetch(state: MemoryExtractionState) -> str:
    """Route: if no messages, release lock and end. Otherwise build text."""
    if not state.get("messages"):
        return "release_extraction_lock"
    return "build_extraction_text"


def route_after_build_text(state: MemoryExtractionState) -> str:
    """Route: if error, release lock. Otherwise run extraction."""
    if state.get("error"):
        return "release_extraction_lock"
    return "run_mem0_extraction"


def route_after_extraction(state: MemoryExtractionState) -> str:
    """Route: if error, release lock. Otherwise mark extracted."""
    if state.get("error"):
        return "release_extraction_lock"
    return "mark_messages_extracted"


def build_memory_extraction() -> StateGraph:
    """Build and compile the memory extraction StateGraph."""
    workflow = StateGraph(MemoryExtractionState)

    workflow.add_node("acquire_extraction_lock", acquire_extraction_lock)
    workflow.add_node("fetch_unextracted_messages", fetch_unextracted_messages)
    workflow.add_node("build_extraction_text", build_extraction_text)
    workflow.add_node("run_mem0_extraction", run_mem0_extraction)
    workflow.add_node("mark_messages_extracted", mark_messages_extracted)
    workflow.add_node("release_extraction_lock", release_extraction_lock)

    workflow.set_entry_point("acquire_extraction_lock")

    workflow.add_conditional_edges(
        "acquire_extraction_lock",
        route_after_lock,
        {"fetch_unextracted_messages": "fetch_unextracted_messages", END: END},
    )

    workflow.add_conditional_edges(
        "fetch_unextracted_messages",
        route_after_fetch,
        {
            "build_extraction_text": "build_extraction_text",
            "release_extraction_lock": "release_extraction_lock",
        },
    )

    workflow.add_conditional_edges(
        "build_extraction_text",
        route_after_build_text,
        {
            "run_mem0_extraction": "run_mem0_extraction",
            "release_extraction_lock": "release_extraction_lock",
        },
    )

    workflow.add_conditional_edges(
        "run_mem0_extraction",
        route_after_extraction,
        {
            "mark_messages_extracted": "mark_messages_extracted",
            "release_extraction_lock": "release_extraction_lock",
        },
    )

    workflow.add_edge("mark_messages_extracted", "release_extraction_lock")
    workflow.add_edge("release_extraction_lock", END)

    return workflow.compile()
