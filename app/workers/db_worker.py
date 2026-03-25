"""
Database-driven background worker for the Context Broker.

Replaces Redis queues with direct database polling. The database IS
the queue — messages with NULL embeddings need embedding, messages
with memory_extracted=FALSE need extraction. No external queue system
required.

Three worker loops run concurrently:
  - Embedding: polls for unembedded messages, processes in batches
  - Extraction: polls for unextracted messages, sends to Mem0
  - Assembly: triggered after embedding batches, checks for stale windows
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from app.config import async_load_config, get_tuning
from app.database import get_pg_pool
from app.stategraph_registry import get_flow_builder
from app.metrics_registry import (
    EMBEDDING_QUEUE_DEPTH,
    EXTRACTION_QUEUE_DEPTH,
    ASSEMBLY_QUEUE_DEPTH,
    JOB_DURATION,
    JOBS_COMPLETED,
)

_log = logging.getLogger("context_broker.workers.db_worker")

# Lazy flow singletons
_embed_flow = None
_extraction_flow = None


def _get_embed_flow():
    global _embed_flow
    if _embed_flow is None:
        builder = get_flow_builder("embed_pipeline")
        if builder is None:
            raise RuntimeError("AE package not loaded: embed_pipeline unavailable")
        _embed_flow = builder()
    return _embed_flow


def _get_extraction_flow():
    global _extraction_flow
    if _extraction_flow is None:
        builder = get_flow_builder("memory_extraction")
        if builder is None:
            raise RuntimeError("AE package not loaded: memory_extraction unavailable")
        _extraction_flow = builder()
    return _extraction_flow


# ── Embedding worker ─────────────────────────────────────────────────

async def _embedding_worker(config: dict) -> None:
    """Poll for unembedded messages and process in batches."""
    _log.info("Embedding worker started (DB-driven)")

    poll_interval = get_tuning(config, "worker_poll_interval_seconds", 2)
    batch_size = get_tuning(config, "embedding_batch_size", 50)
    concurrency = get_tuning(config, "embedding_concurrency", 3)
    semaphore = asyncio.Semaphore(concurrency)
    consecutive_failures = 0

    while True:
        try:
            config = await async_load_config()
            pool = get_pg_pool()

            # Count pending for metrics (match the fetch filter)
            pending = await pool.fetchval(
                "SELECT COUNT(*) FROM conversation_messages WHERE embedding IS NULL AND content IS NOT NULL"
            )
            EMBEDDING_QUEUE_DEPTH.set(pending)

            if pending == 0:
                await asyncio.sleep(poll_interval)
                continue

            # Fetch a batch of unembedded messages
            rows = await pool.fetch(
                """
                SELECT id, conversation_id, content, sequence_number, role, sender, priority
                FROM conversation_messages
                WHERE embedding IS NULL AND content IS NOT NULL
                ORDER BY priority DESC, created_at ASC
                LIMIT $1
                """,
                batch_size,
            )

            if not rows:
                await asyncio.sleep(poll_interval)
                continue

            _log.info("Embedding batch: %d messages", len(rows))
            start = time.monotonic()

            # Batch embed using the embeddings model
            from app.config import get_embeddings_model

            embeddings_model = get_embeddings_model(config)
            texts = [r["content"] for r in rows]

            try:
                vectors = await embeddings_model.aembed_documents(texts)
            except (OSError, ValueError, RuntimeError) as exc:
                _log.error("Batch embedding failed: %s", exc)
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    await asyncio.sleep(5)
                continue

            # Store vectors
            for row, vector in zip(rows, vectors):
                vec_str = "[" + ",".join(str(v) for v in vector) + "]"
                await pool.execute(
                    "UPDATE conversation_messages SET embedding = $1::vector WHERE id = $2",
                    vec_str,
                    row["id"],
                )

            duration = time.monotonic() - start
            _log.info(
                "Embedding batch complete: %d messages in %dms",
                len(rows), int(duration * 1000),
            )
            JOB_DURATION.labels(job_type="embed_batch").observe(duration)
            JOBS_COMPLETED.labels(job_type="embed_message", status="success").inc(len(rows))
            consecutive_failures = 0

            # Assembly is handled by the separate assembly worker loop

        except asyncio.CancelledError:
            _log.info("Embedding worker cancelled")
            raise
        except Exception as exc:
            _log.error("Embedding worker error: %s: %s", type(exc).__name__, exc)
            consecutive_failures += 1
            if consecutive_failures >= 3:
                _log.warning("Embedding worker backing off after %d failures", consecutive_failures)
                await asyncio.sleep(5)


# ── Extraction worker ────────────────────────────────────────────────

async def _extraction_worker(config: dict) -> None:
    """Poll for unextracted messages and send to Mem0."""
    _log.info("Extraction worker started (DB-driven)")

    poll_interval = get_tuning(config, "worker_poll_interval_seconds", 2)
    consecutive_failures = 0

    while True:
        try:
            config = await async_load_config()
            pool = get_pg_pool()

            # Count pending for metrics
            pending = await pool.fetchval(
                "SELECT COUNT(*) FROM conversation_messages WHERE memory_extracted IS NOT TRUE"
            )
            EXTRACTION_QUEUE_DEPTH.set(pending)

            if pending == 0:
                await asyncio.sleep(poll_interval)
                continue

            # Find conversations with unextracted messages
            conv_rows = await pool.fetch(
                """
                SELECT DISTINCT conversation_id
                FROM conversation_messages
                WHERE memory_extracted IS NOT TRUE
                LIMIT 5
                """
            )

            for conv_row in conv_rows:
                conv_id = str(conv_row["conversation_id"])

                # Use Postgres advisory lock to prevent concurrent extraction
                lock_id = hash(conv_id) & 0x7FFFFFFFFFFFFFFF  # Positive bigint
                locked = await pool.fetchval(
                    "SELECT pg_try_advisory_lock($1)", lock_id
                )
                if not locked:
                    continue

                try:
                    start = time.monotonic()
                    result = await _get_extraction_flow().ainvoke(
                        {
                            "conversation_id": conv_id,
                            "config": config,
                            "messages": [],
                            "user_id": "",
                            "extraction_text": "",
                            "selected_message_ids": [],
                            "fully_extracted_ids": [],
                            "lock_key": "",
                            "lock_token": None,
                            "lock_acquired": True,  # We handle locking here
                            "extracted_count": 0,
                            "error": None,
                        }
                    )
                    duration = time.monotonic() - start

                    if result.get("error"):
                        _log.error("Extraction failed for %s: %s", conv_id, result["error"])
                        JOBS_COMPLETED.labels(job_type="extract_memory", status="error").inc()
                    else:
                        _log.info(
                            "Extraction complete: conv=%s extracted=%d duration_ms=%d",
                            conv_id, result.get("extracted_count", 0), int(duration * 1000),
                        )
                        JOB_DURATION.labels(job_type="extract_memory").observe(duration)
                        JOBS_COMPLETED.labels(job_type="extract_memory", status="success").inc()
                finally:
                    await pool.execute("SELECT pg_advisory_unlock($1)", lock_id)

            consecutive_failures = 0
            await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            _log.info("Extraction worker cancelled")
            raise
        except Exception as exc:
            _log.error("Extraction worker error: %s: %s", type(exc).__name__, exc)
            consecutive_failures += 1
            if consecutive_failures >= 3:
                await asyncio.sleep(5)


# ── Assembly check ───────────────────────────────────────────────────

async def _check_assembly_needed(pool, config: dict, conv_ids: list[str]) -> None:
    """Check if any context windows need reassembly after new embeddings."""
    from app.flows.build_type_registry import get_assembly_graph

    trigger_pct = get_tuning(config, "trigger_threshold_percent", 0.1)

    for conv_id in conv_ids:
        windows = await pool.fetch(
            """
            SELECT id, build_type, max_token_budget, last_assembled_at
            FROM context_windows
            WHERE conversation_id = $1
            """,
            uuid.UUID(conv_id),
        )

        for window in windows:
            window_id = str(window["id"])
            max_budget = window["max_token_budget"]
            threshold_tokens = int(max_budget * trigger_pct)

            # Check if enough new tokens since last assembly
            if window["last_assembled_at"] is not None:
                tokens_since = await pool.fetchval(
                    """
                    SELECT COALESCE(SUM(token_count), 0)
                    FROM conversation_messages
                    WHERE conversation_id = $1 AND created_at > $2
                    """,
                    uuid.UUID(conv_id),
                    window["last_assembled_at"],
                )
                if tokens_since < threshold_tokens:
                    continue

            _log.info("Triggering assembly for window=%s", window_id)
            assembly_graph = get_assembly_graph(window["build_type"])
            start = time.monotonic()

            try:
                await assembly_graph.ainvoke(
                    {
                        "context_window_id": window_id,
                        "conversation_id": conv_id,
                        "config": config,
                    }
                )

                duration = time.monotonic() - start
                _log.info(
                    "Assembly complete: window=%s duration_ms=%d",
                    window_id, int(duration * 1000),
                )
                JOBS_COMPLETED.labels(job_type="assemble_context", status="success").inc()
                JOB_DURATION.labels(job_type="assemble_context").observe(duration)

            except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
                _log.error("Assembly failed for window=%s: %s", window_id, exc)
                JOBS_COMPLETED.labels(job_type="assemble_context", status="error").inc()


# ── Main entry point ────────────────────────────────────────────────

async def _assembly_worker(config: dict) -> None:
    """Poll for context windows that need reassembly."""
    _log.info("Assembly worker started (DB-driven)")

    poll_interval = get_tuning(config, "worker_poll_interval_seconds", 2)

    while True:
        try:
            config = await async_load_config()
            pool = get_pg_pool()

            # Find windows where new messages exist since last assembly
            windows = await pool.fetch(
                """
                SELECT cw.id, cw.conversation_id, cw.build_type,
                       cw.max_token_budget, cw.last_assembled_at
                FROM context_windows cw
                WHERE EXISTS (
                    SELECT 1 FROM conversation_messages cm
                    WHERE cm.conversation_id = cw.conversation_id
                    AND (cw.last_assembled_at IS NULL OR cm.created_at > cw.last_assembled_at)
                )
                LIMIT 5
                """
            )

            ASSEMBLY_QUEUE_DEPTH.set(len(windows))

            if not windows:
                await asyncio.sleep(poll_interval * 5)  # Poll less frequently for assembly
                continue

            for window in windows:
                conv_id = str(window["conversation_id"])
                window_id = str(window["id"])
                await _run_assembly(pool, config, window_id, conv_id, window["build_type"])

            await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            _log.info("Assembly worker cancelled")
            raise
        except Exception as exc:
            _log.error("Assembly worker error: %s: %s", type(exc).__name__, exc)
            await asyncio.sleep(5)


async def _run_assembly(pool, config, window_id, conv_id, build_type):
    """Run assembly for a single window."""
    from app.flows.build_type_registry import get_assembly_graph

    _log.info("Triggering assembly for window=%s", window_id)
    start = time.monotonic()

    try:
        assembly_graph = get_assembly_graph(build_type)
        await assembly_graph.ainvoke(
            {
                "context_window_id": window_id,
                "conversation_id": conv_id,
                "config": config,
            }
        )

        duration = time.monotonic() - start
        _log.info(
            "Assembly complete: window=%s duration_ms=%d",
            window_id, int(duration * 1000),
        )
        JOBS_COMPLETED.labels(job_type="assemble_context", status="success").inc()
        JOB_DURATION.labels(job_type="assemble_context").observe(duration)

    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        _log.error("Assembly failed for window=%s: %s", window_id, exc)
        JOBS_COMPLETED.labels(job_type="assemble_context", status="error").inc()


async def start_background_worker(config: dict) -> None:
    """Start all background worker loops concurrently."""
    _log.info("Background worker starting (DB-driven, no Redis)")

    await asyncio.gather(
        _embedding_worker(config),
        _extraction_worker(config),
        _assembly_worker(config),
    )
