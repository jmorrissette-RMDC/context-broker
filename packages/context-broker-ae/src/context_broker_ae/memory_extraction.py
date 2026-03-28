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
from app.database import get_pg_pool

_log = logging.getLogger("context_broker.flows.memory_extraction")

# Secret redaction patterns — applied before Mem0 ingestion
_SECRET_PATTERNS = [
    (
        re.compile(
            r'(?i)(api[_-]?key|token|secret|password|credential)["\s:=]+["\']?[\w\-\.]{20,}'
        ),
        "[REDACTED]",
    ),
    (re.compile(r"(?i)bearer\s+[\w\-\.]{20,}"), "Bearer [REDACTED]"),
    (re.compile(r"sk-[a-zA-Z0-9\-]{20,}"), "[REDACTED]"),
    (
        re.compile(
            r'(?i)(aws|gcp|azure)[_-]?[\w]*[_-]?(key|secret|token)["\s:=]+["\']?[\w\-\.]{16,}'
        ),
        "[REDACTED]",
    ),
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
    """Acquire a Postgres advisory lock for extraction of this conversation."""
    verbose_log(
        state["config"],
        _log,
        "memory_extraction.acquire_lock ENTER conv=%s",
        state["conversation_id"],
    )
    pool = get_pg_pool()
    from app.utils import stable_lock_id

    lock_id = stable_lock_id(state["conversation_id"])
    acquired = await pool.fetchval("SELECT pg_try_advisory_lock($1)", lock_id)

    if not acquired:
        _log.info(
            "Memory extraction: lock not acquired for conv=%s — skipping",
            state["conversation_id"],
        )
        return {"lock_key": str(lock_id), "lock_token": None, "lock_acquired": False}

    return {
        "lock_key": str(lock_id),
        "lock_token": "pg_advisory",
        "lock_acquired": True,
    }


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
    # R7-m3: Removed duplicate get_pg_pool() call — reuse existing pool variable
    window = await pool.fetchrow(
        "SELECT participant_id FROM context_windows WHERE conversation_id = $1 ORDER BY created_at ASC LIMIT 1",
        uuid.UUID(state["conversation_id"]),
    )
    user_id = window["participant_id"] if window else "default"

    return {"messages": [dict(r) for r in rows], "user_id": user_id}


# Patterns for stripping noise from extraction text
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]{10,}`")
_FILE_PATH_RE = re.compile(
    r"(?:[A-Z]:\\|/(?:mnt|home|usr|storage|app|tmp)/)[\w/\\._-]{10,}"
)
_URL_RE = re.compile(r"https?://\S{20,}")
_MARKDOWN_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MARKDOWN_HR_RE = re.compile(r"^\*{3,}$|^-{3,}$|^_{3,}$", re.MULTILINE)
_MARKDOWN_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def _clean_for_extraction(text: str) -> str:
    """Strip code blocks, file paths, URLs, and markdown noise.

    Preserves conversational content — the signal Mem0 needs for
    knowledge extraction. Removes noise that confuses the LLM and
    wastes tokens.
    """
    # Remove code blocks (triple backtick fenced)
    text = _CODE_BLOCK_RE.sub("[code removed]", text)
    # Remove long inline code spans
    text = _INLINE_CODE_RE.sub("[code]", text)
    # Remove file paths
    text = _FILE_PATH_RE.sub("[path]", text)
    # Remove long URLs
    text = _URL_RE.sub("[url]", text)
    # Simplify markdown headers to plain text
    text = _MARKDOWN_HEADER_RE.sub("", text)
    # Remove horizontal rules
    text = _MARKDOWN_HR_RE.sub("", text)
    # Simplify bold markers
    text = _MARKDOWN_BOLD_RE.sub(r"\1", text)
    # Collapse excessive newlines
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def _chunk_text(text: str, max_chunk: int) -> list[str]:
    """Split text into chunks that fit within max_chunk characters.

    Splits on paragraph boundaries first, then sentence boundaries,
    then hard-splits as a last resort.
    """
    if len(text) <= max_chunk:
        return [text]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chunk:
            chunks.append(remaining)
            break
        # Try to split on paragraph boundary
        cut = remaining.rfind("\n\n", 0, max_chunk)
        if cut < max_chunk // 4:
            # Try sentence boundary
            cut = remaining.rfind(". ", 0, max_chunk)
        if cut < max_chunk // 4:
            # Hard split
            cut = max_chunk
        chunk = remaining[: cut + 1].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut + 1 :].strip()
    return chunks


async def build_extraction_text(state: MemoryExtractionState) -> dict:
    """Build the text to send to Mem0 for extraction.

    Limits text size to fit the extraction model's capacity.
    Large messages are split into chunks. All messages are marked as
    fully extracted — no poison pills from oversized content.

    Cleans content by stripping code blocks, file paths, and markdown
    noise that wastes tokens and confuses structured JSON output.
    """
    messages = state["messages"]
    max_chars = get_tuning(state["config"], "extraction_max_chars", 8000)

    # Clean and collect all messages with their IDs
    cleaned_messages = []
    for msg in reversed(messages):
        msg_content = msg.get("content") or ""
        if not msg_content:
            continue
        cleaned = _clean_for_extraction(msg_content)
        if not cleaned or len(cleaned) < 10:
            # Skip empty/tiny messages but still mark them as extracted
            cleaned_messages.append((str(msg["id"]), ""))
            continue
        cleaned_messages.append(
            (str(msg["id"]), f"{msg['role']} ({msg['sender']}): {cleaned}")
        )

    # Build extraction text within budget, chunking large messages
    selected_ids = []
    selected_lines = []
    total_chars = 0

    for msg_id, line in cleaned_messages:
        selected_ids.append(msg_id)
        if not line:
            continue

        if len(line) > max_chars:
            # Large message — take the first chunk that fits
            chunks = _chunk_text(
                line, max_chars - total_chars if total_chars > 0 else max_chars
            )
            if chunks:
                selected_lines.append(chunks[0])
                total_chars += len(chunks[0]) + 1
            # Message is still marked as extracted even if we only took one chunk.
            # The key facts are usually in the first part of a large message.
            if total_chars >= max_chars:
                break
        elif total_chars + len(line) + 1 > max_chars:
            break
        else:
            selected_lines.append(line)
            total_chars += len(line) + 1

    # All selected messages are marked as fully extracted — no poison pills.
    # Even if a message was chunked, we mark it done. Re-extracting the same
    # message forever is worse than missing some facts from the tail.
    fully_extracted_ids = list(selected_ids)

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
        from context_broker_ae.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            _log.warning(
                "Memory extraction: Mem0 not available for conv=%s",
                state["conversation_id"],
            )
            return {"error": "Mem0 client not available"}

        # Load custom extraction prompt if available (externalized per REQ-001 §8.2)
        from app.prompt_loader import load_prompt

        extraction_prompt = None
        try:
            extraction_prompt = load_prompt("memory_extraction")
        except RuntimeError:
            _log.debug("No custom extraction prompt found — using Mem0 defaults")

        loop = asyncio.get_running_loop()
        add_kwargs = {
            "user_id": state["user_id"],
            "run_id": state["conversation_id"],
        }
        if extraction_prompt:
            add_kwargs["prompt"] = extraction_prompt

        result = await loop.run_in_executor(
            None,
            lambda: mem0.add(state["extraction_text"], **add_kwargs),
        )

        # TA-04: Validate that mem0.add() actually ran.
        # If result is None, Mem0 silently failed (tables missing, connection dropped).
        # If result is a dict (even with empty results/relations), Mem0 processed the
        # text successfully — the LLM may not have found new discrete facts, but
        # relationship updates in Neo4j still happen (visible in logs as
        # "Updating relationship: ..."). Empty results is normal, not an error.
        if result is None:
            _log.warning(
                "Memory extraction: mem0.add() returned None for conv=%s — "
                "treating as error to prevent silent data loss",
                state["conversation_id"],
            )
            return {"error": "Mem0 returned None — data may not have been persisted"}

        results_list = result.get("results", []) if isinstance(result, dict) else result
        result_count = len(results_list) if isinstance(results_list, list) else 0

        _log.info(
            "Memory extraction: processed %d messages for conv=%s (new_memories=%d)",
            len(state["selected_message_ids"]),
            state["conversation_id"],
            result_count,
        )
        return {"error": None}

    except (
        ConnectionError,
        RuntimeError,
        ValueError,
        ImportError,
        OSError,
        Exception,
    ) as exc:
        # G5-18: Broad exception handling for Mem0/Neo4j failures.
        # Mem0 wraps Neo4j driver errors and other backend failures in
        # various exception types. We catch broadly here to ensure
        # graceful degradation rather than crashing the worker.
        _log.error(
            "Memory extraction failed for conv=%s: %s",
            state["conversation_id"],
            exc,
        )
        # PG-49: Reset Mem0 client on error so next retry gets a fresh
        # connection. Prevents poisoned transaction state from cascading.
        try:
            from context_broker_ae.memory.mem0_client import reset_mem0_client

            reset_mem0_client()
        except ImportError:
            pass
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


async def release_extraction_lock(state: MemoryExtractionState) -> dict:
    """Release the Postgres advisory lock for extraction."""
    lock_key = state.get("lock_key", "")
    if lock_key and state.get("lock_acquired"):
        try:
            pool = get_pg_pool()
            lock_id = int(lock_key)
            await pool.execute("SELECT pg_advisory_unlock($1)", lock_id)
        except (ValueError, OSError) as exc:
            _log.debug("Lock release failed: %s", exc)
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
