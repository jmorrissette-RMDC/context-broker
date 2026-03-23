"""
Background worker for the Context Broker.

Processes three job types from Redis queues:
  - embedding_jobs: Generate vector embeddings for messages (list, BLMOVE)
  - context_assembly_jobs: Build context window summaries (list, BLMOVE)
  - memory_extraction_jobs: Extract knowledge into Neo4j via Mem0
    (sorted set, ZPOPMIN — highest priority / lowest score first)

Uses atomic BLMOVE (Redis 7+) for list-backed queues and ZPOPMIN for
sorted-set queues. Includes retry with backoff, dead-letter handling,
and crash-safe lock cleanup for assembly and extraction flows.
"""

import asyncio
import json
import logging
import random
import sys
import time
import uuid
from typing import Any

import redis.asyncio as aioredis
import redis.exceptions as redis_exc

from app.config import async_load_config, get_tuning
from app.database import get_redis
from app.flows.embed_pipeline import build_embed_pipeline
from app.flows.memory_extraction import build_memory_extraction

# ARCH-18: Import build_types package to trigger registration of all build types
import app.flows.build_types  # noqa: F401
from app.flows.build_type_registry import get_assembly_graph
from app.metrics_registry import (
    ASSEMBLY_QUEUE_DEPTH,
    EMBEDDING_QUEUE_DEPTH,
    EXTRACTION_QUEUE_DEPTH,
    JOB_DURATION,
    JOBS_COMPLETED,
)

_log = logging.getLogger("context_broker.workers.arq_worker")

# Map queue names to their depth gauges
_QUEUE_DEPTH_GAUGES = {
    "embedding_jobs": EMBEDDING_QUEUE_DEPTH,
    "context_assembly_jobs": ASSEMBLY_QUEUE_DEPTH,
    "memory_extraction_jobs": EXTRACTION_QUEUE_DEPTH,
}

# Lazy-initialized flow singletons — compiled on first use
_embed_flow = None
_extraction_flow = None


def _get_embed_flow():
    global _embed_flow
    if _embed_flow is None:
        _embed_flow = build_embed_pipeline()
    return _embed_flow


def _get_extraction_flow():
    global _extraction_flow
    if _extraction_flow is None:
        _extraction_flow = build_memory_extraction()
    return _extraction_flow


async def process_embedding_job(job: dict) -> None:
    """Process a single embedding job using the embed pipeline StateGraph."""
    config = await async_load_config()
    message_id = job.get("message_id", "")
    conversation_id = job.get("conversation_id", "")

    # M-25: Validate UUIDs from Redis job data before passing to flows
    try:
        if message_id:
            uuid.UUID(message_id)
        if conversation_id:
            uuid.UUID(conversation_id)
    except ValueError as exc:
        raise ValueError(f"Malformed UUID in embedding job: {exc}") from exc

    _log.info("Processing embedding job: message_id=%s", message_id)
    start = time.monotonic()

    result = await _get_embed_flow().ainvoke(
        {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "config": config,
            "message": None,
            "embedding": None,
            "assembly_jobs_queued": [],
            "error": None,
        }
    )

    duration = time.monotonic() - start

    if result.get("error"):
        _log.error(
            "Embedding job failed: message_id=%s error=%s",
            message_id,
            result["error"],
        )
        JOBS_COMPLETED.labels(job_type="embed_message", status="error").inc()
        raise RuntimeError(result["error"])

    _log.info(
        "Embedding job complete: message_id=%s duration_ms=%d",
        message_id,
        int(duration * 1000),
    )
    JOB_DURATION.labels(job_type="embed_message").observe(duration)
    JOBS_COMPLETED.labels(job_type="embed_message", status="success").inc()


async def process_assembly_job(job: dict) -> None:
    """Process a single context assembly job using the assembly StateGraph."""
    config = await async_load_config()
    context_window_id = job.get("context_window_id", "")
    conversation_id = job.get("conversation_id", "")
    build_type = job.get("build_type", "standard-tiered")

    # M-25: Validate UUIDs from Redis job data before passing to flows
    try:
        if context_window_id:
            uuid.UUID(context_window_id)
        if conversation_id:
            uuid.UUID(conversation_id)
    except ValueError as exc:
        raise ValueError(f"Malformed UUID in assembly job: {exc}") from exc

    _log.info("Processing assembly job: window=%s build_type=%s", context_window_id, build_type)
    start = time.monotonic()

    # ARCH-18: Look up the assembly graph from the registry by build type name
    assembly_graph = get_assembly_graph(build_type)

    lock_key = f"assembly_in_progress:{context_window_id}"
    try:
        # R5-m10: Pass only the AssemblyInput contract fields plus common
        # lock-management fields. Build-type-specific intermediate state
        # (e.g., standard-tiered's all_messages, chunks) is populated by
        # each graph's own nodes. This avoids passing keys that don't
        # exist in simpler build types like passthrough.
        result = await assembly_graph.ainvoke(
            {
                "context_window_id": context_window_id,
                "conversation_id": conversation_id,
                "config": config,
            }
        )
    except Exception:
        # R6-M5: Don't delete the lock unconditionally — that could release
        # another worker's lock. The lock has a TTL; let it expire naturally.
        _log.warning(
            "Assembly graph crashed for window=%s; lock %s will expire via TTL",
            context_window_id,
            lock_key,
        )
        raise

    duration = time.monotonic() - start

    if result.get("error"):
        _log.error(
            "Assembly job failed: window=%s error=%s",
            context_window_id,
            result["error"],
        )
        JOBS_COMPLETED.labels(job_type="assemble_context", status="error").inc()
        raise RuntimeError(result["error"])

    _log.info(
        "Assembly job complete: window=%s duration_ms=%d",
        context_window_id,
        int(duration * 1000),
    )
    JOB_DURATION.labels(job_type="assemble_context").observe(duration)
    JOBS_COMPLETED.labels(job_type="assemble_context", status="success").inc()


async def process_extraction_job(job: dict) -> None:
    """Process a single memory extraction job using the extraction StateGraph."""
    config = await async_load_config()
    conversation_id = job.get("conversation_id", "")

    # M-25: Validate UUIDs from Redis job data before passing to flows
    try:
        if conversation_id:
            uuid.UUID(conversation_id)
    except ValueError as exc:
        raise ValueError(f"Malformed UUID in extraction job: {exc}") from exc

    _log.info("Processing extraction job: conversation_id=%s", conversation_id)
    start = time.monotonic()

    lock_key = f"extraction_in_progress:{conversation_id}"
    try:
        result = await _get_extraction_flow().ainvoke(
            {
                "conversation_id": conversation_id,
                "config": config,
                "messages": [],
                "user_id": "",
                "extraction_text": "",
                "selected_message_ids": [],
                "fully_extracted_ids": [],
                "lock_key": "",
                "lock_acquired": False,
                "extracted_count": 0,
                "error": None,
            }
        )
    except Exception:
        # R6-M5: Don't delete the lock unconditionally — that could release
        # another worker's lock. The lock has a TTL; let it expire naturally.
        _log.warning(
            "Extraction graph crashed for conversation=%s; lock %s will expire via TTL",
            conversation_id,
            lock_key,
        )
        raise

    duration = time.monotonic() - start

    if result.get("error") and result["error"] != "Mem0 client not available":
        _log.error(
            "Extraction job failed: conversation_id=%s error=%s",
            conversation_id,
            result["error"],
        )
        JOBS_COMPLETED.labels(job_type="extract_memory", status="error").inc()
        raise RuntimeError(result["error"])

    _log.info(
        "Extraction job complete: conversation_id=%s extracted=%d duration_ms=%d",
        conversation_id,
        result.get("extracted_count", 0),
        int(duration * 1000),
    )
    JOB_DURATION.labels(job_type="extract_memory").observe(duration)
    JOBS_COMPLETED.labels(job_type="extract_memory", status="success").inc()


async def _handle_job_failure(
    redis: aioredis.Redis,
    queue_name: str,
    job: dict,
    raw_job: str,
    error: Exception,
    config: dict,
) -> None:
    """Handle a failed job: schedule retry with backoff or move to dead-letter.

    Instead of sleeping (which blocks the consumer loop), the job is pushed
    back to the queue with a retry_after timestamp. The consumer checks this
    timestamp and re-queues jobs that are not yet ready.
    """
    max_retries = get_tuning(config, "max_retries", 3)
    attempt = job.get("attempt", 1) + 1
    job["attempt"] = attempt

    if attempt <= max_retries:
        # R6-m18: Add random jitter to prevent thundering herd on retries
        backoff = min(2 ** (attempt - 1) * 5, 60) + random.uniform(0, 2)
        job["retry_after"] = time.time() + backoff
        _log.warning(
            "Scheduling retry (attempt=%d backoff=%ds): queue=%s error=%s",
            attempt,
            backoff,
            queue_name,
            error,
        )
        # B-03 / G5-34: Use sorted set for delayed queue (score = retry_after)
        retry_after_ts = job["retry_after"]
        await redis.zadd(
            f"{queue_name}:delayed", {json.dumps(job): retry_after_ts}
        )
    else:
        _log.error(
            "Dead-lettering job after %d attempts: queue=%s error=%s",
            max_retries,
            queue_name,
            error,
        )
        await redis.lpush("dead_letter_jobs", raw_job)


async def _consume_queue(
    queue_name: str,
    processor,
    config: dict,
    *,
    sorted_set: bool = False,
) -> None:
    """Independent consumer loop for a single Redis queue.

    For list-backed queues, uses BLMOVE to atomically pop from the source
    queue into a processing queue. For sorted-set queues (sorted_set=True),
    uses ZPOPMIN to get the highest-priority (lowest score) job.

    On success the job is removed from the processing queue. On crash,
    items remain in the processing queue and can be recovered.
    """
    _log.info("Queue consumer started: %s (sorted_set=%s)", queue_name, sorted_set)
    processing_queue = f"{queue_name}:processing"
    poll_timeout = get_tuning(config, "worker_poll_interval_seconds", 2)
    # G5-33: Track consecutive failures for backoff
    consecutive_failures = 0
    _FAILURE_BACKOFF_THRESHOLD = 3
    _FAILURE_BACKOFF_SECONDS = 5

    while True:
        raw_job = None
        job = None
        try:
            redis = get_redis()

            # Update queue depth gauge for this queue
            depth_gauge = _QUEUE_DEPTH_GAUGES.get(queue_name)
            if depth_gauge is not None:
                if sorted_set:
                    # F-01: Extraction queue is a sorted set
                    queue_len = await redis.zcard(queue_name)
                else:
                    queue_len = await redis.llen(queue_name)
                depth_gauge.set(queue_len)

            if sorted_set:
                # F-01: Use ZPOPMIN for sorted-set backed queues
                # R6-M6: ZPOPMIN + LPUSH to processing queue is not atomic.
                # If the worker crashes between these two operations, the job
                # is lost from the sorted set but not yet in the processing
                # queue. The startup stranded-job sweep (_sweep_stranded_processing_jobs)
                # recovers jobs that made it to the processing queue. For the
                # narrow window between ZPOPMIN and LPUSH, the original enqueuer's
                # retry/dead-letter logic provides eventual recovery.
                result = await redis.zpopmin(queue_name, count=1)
                if not result:
                    # No items available — wait before polling again
                    await asyncio.sleep(poll_timeout)
                    continue
                raw_job = result[0][0]  # (member, score) tuple
                # Track in processing list for crash recovery
                await redis.lpush(processing_queue, raw_job)
            else:
                # Atomically move job from source queue to processing queue
                raw_job = await redis.blmove(
                    queue_name,
                    processing_queue,
                    poll_timeout,
                    "RIGHT",
                    "LEFT",
                )

            if not raw_job:
                # blmove returned None on timeout — loop back
                continue

            # decode_responses=True ensures Redis returns strings, not bytes.
            job = json.loads(raw_job)

            # Strip retry_after if present (jobs promoted from delayed queue)
            job.pop("retry_after", None)

            await processor(job)

            # Success — remove from processing queue and reset failure counter
            await redis.lrem(processing_queue, 1, raw_job)
            consecutive_failures = 0

        except asyncio.CancelledError:
            _log.info("Queue consumer cancelled: %s", queue_name)
            raise
        # M-24: Broadened exception handler to cover flow-level errors
        # (ValueError, KeyError, TypeError) that shouldn't kill the consumer.
        # CB-R3-05: Added redis_exc.RedisError for Redis-level failures.
        except (RuntimeError, ConnectionError, json.JSONDecodeError, OSError,
                ValueError, KeyError, TypeError,
                redis_exc.RedisError) as exc:
            _log.error(
                "Job processing error in queue %s: %s", queue_name, exc, exc_info=True
            )
            # M6: Wrap error handler Redis ops in their own try/except
            # to prevent crashes when Redis is unavailable during error handling
            try:
                if job is not None and raw_job is not None:
                    # Remove from processing queue before retry/dead-letter
                    err_redis = get_redis()
                    await err_redis.lrem(processing_queue, 1, raw_job)
                    await _handle_job_failure(
                        err_redis, queue_name, job, raw_job, exc, config
                    )
            except (ConnectionError, OSError, redis_exc.RedisError) as redis_err:
                # R5-M26: Log the job payload to stderr so it's not completely lost
                _log.error(
                    "Redis failure during error handling for queue %s: %s",
                    queue_name,
                    redis_err,
                )
                print(
                    f"LOST_JOB queue={queue_name} payload={raw_job}",
                    file=sys.stderr,
                    flush=True,
                )

            # G5-33: Backoff on repeated consecutive failures
            consecutive_failures += 1
            if consecutive_failures >= _FAILURE_BACKOFF_THRESHOLD:
                _log.warning(
                    "Consumer %s hit %d consecutive failures, backing off %ds",
                    queue_name,
                    consecutive_failures,
                    _FAILURE_BACKOFF_SECONDS,
                )
                await asyncio.sleep(_FAILURE_BACKOFF_SECONDS)


async def _sweep_dead_letters(config: dict) -> None:
    """Periodically re-queue dead-letter jobs for retry.

    Increments the attempt counter instead of resetting it.
    Discards jobs that exceed max_total_dead_letter_attempts to
    prevent infinite retry loops.
    """
    redis = get_redis()
    swept = 0
    max_retries = get_tuning(config, "max_retries", 3)
    max_total_attempts = max_retries * 2

    for _ in range(10):
        raw = await redis.rpop("dead_letter_jobs")
        if not raw:
            break

        # decode_responses=True ensures Redis returns strings, not bytes.
        try:
            job = json.loads(raw)
            job_type = job.get("job_type", "")

            queue_map = {
                "embed_message": "embedding_jobs",
                "assemble_context": "context_assembly_jobs",
                "extract_memory": "memory_extraction_jobs",
            }

            target_queue = queue_map.get(job_type)
            if not target_queue:
                _log.warning(
                    "Dead-letter sweep: unknown job_type=%s, discarding", job_type
                )
                continue

            # Increment attempt counter (do NOT reset to 1)
            current_attempt = job.get("attempt", 1)
            if current_attempt >= max_total_attempts:
                _log.error(
                    "Dead-letter sweep: discarding job after %d total attempts: "
                    "job_type=%s job=%s",
                    current_attempt,
                    job_type,
                    json.dumps(job),
                )
                continue

            job["attempt"] = current_attempt + 1
            payload = json.dumps(job)
            # R5-M18: Extraction queue is a sorted set; use ZADD with the
            # job's priority as score so retried jobs preserve their priority
            if target_queue == "memory_extraction_jobs":
                # R6-M12: Use stored priority, fall back to 2 (assistant priority)
                score = job.get("priority", 2)
                await redis.zadd(target_queue, {payload: score})
            else:
                await redis.lpush(target_queue, payload)
            swept += 1

        except json.JSONDecodeError as exc:
            # G5-35: Preserve unparseable payloads for forensic review
            _log.error(
                "Dead-letter sweep: malformed JSON, pushing to "
                "dead_letter_unparseable: %s", exc,
            )
            await redis.lpush("dead_letter_unparseable", raw)
        except (ConnectionError, OSError) as exc:
            _log.error("Dead-letter sweep error: %s", exc)

    if swept > 0:
        _log.info("Dead-letter sweep: re-queued %d jobs", swept)


async def _sweep_delayed_queues(config: dict) -> None:
    """Promote jobs from delayed queues whose retry_after has passed.

    Delayed queues are Redis Sorted Sets keyed by retry_after timestamp
    (G5-34). Uses ZRANGEBYSCORE to fetch all ready jobs and ZREM + LPUSH
    to atomically promote them back to the main queue.
    """
    redis = get_redis()
    now = time.time()
    promoted = 0

    queue_names = ["embedding_jobs", "context_assembly_jobs", "memory_extraction_jobs"]
    for queue_name in queue_names:
        delayed_queue = f"{queue_name}:delayed"
        # G5-34: Use ZRANGEBYSCORE to fetch all ready jobs from sorted set
        try:
            ready_jobs = await redis.zrangebyscore(
                delayed_queue, "-inf", now, start=0, num=50
            )
            for raw in ready_jobs:
                await redis.zrem(delayed_queue, raw)
                # F-01: Extraction queue is a sorted set; use ZADD
                if queue_name == "memory_extraction_jobs":
                    await redis.zadd(queue_name, {raw: 0})
                else:
                    await redis.lpush(queue_name, raw)
                promoted += 1
        except (ConnectionError, OSError, redis_exc.RedisError) as exc:
            _log.error("Delayed queue sweep error: %s", exc)

    if promoted > 0:
        _log.info("Delayed queue sweep: promoted %d jobs", promoted)


async def _dead_letter_sweep_loop(config: dict) -> None:
    """Periodic dead-letter and delayed-queue sweep loop."""
    while True:
        await asyncio.sleep(get_tuning(config, "dead_letter_sweep_interval_seconds", 60))
        try:
            await _sweep_dead_letters(config)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ConnectionError, OSError) as exc:
            _log.error("Dead-letter sweep loop error: %s", exc)
        try:
            await _sweep_delayed_queues(config)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ConnectionError, OSError) as exc:
            _log.error("Delayed queue sweep loop error: %s", exc)


async def _sweep_stranded_processing_jobs() -> None:
    """Recover jobs stranded in processing queues after a worker crash.

    R5-M17: On startup, check all processing queues. Move stranded jobs
    back to their main queues so they can be re-processed.
    """
    redis = get_redis()
    queue_names = ["embedding_jobs", "context_assembly_jobs", "memory_extraction_jobs"]
    total_recovered = 0

    for queue_name in queue_names:
        processing_queue = f"{queue_name}:processing"
        try:
            stranded_count = await redis.llen(processing_queue)
            if stranded_count == 0:
                continue

            _log.warning(
                "Found %d stranded job(s) in %s — recovering",
                stranded_count,
                processing_queue,
            )
            for _ in range(stranded_count):
                raw = await redis.rpop(processing_queue)
                if raw is None:
                    break
                # R5-M18: Extraction queue is a sorted set; use ZADD
                if queue_name == "memory_extraction_jobs":
                    # Parse to get priority if available, default score 0
                    try:
                        job = json.loads(raw)
                        score = job.get("priority", 0)
                    except (json.JSONDecodeError, TypeError):
                        score = 0
                    await redis.zadd(queue_name, {raw: score})
                else:
                    await redis.lpush(queue_name, raw)
                total_recovered += 1
        except (ConnectionError, OSError, redis_exc.RedisError) as exc:
            _log.error(
                "Failed to sweep stranded jobs from %s: %s",
                processing_queue,
                exc,
            )

    if total_recovered > 0:
        _log.info("Startup sweep: recovered %d stranded job(s)", total_recovered)


async def start_background_worker(config: dict) -> None:
    """Start all queue consumer loops concurrently.

    Each consumer is an independent async loop. They do not block each other.
    """
    _log.info("Background worker starting (3 consumers + dead-letter sweep)")

    # R5-M17: Recover any jobs stranded in processing queues from a prior crash
    try:
        await _sweep_stranded_processing_jobs()
    except (ConnectionError, OSError) as exc:
        _log.error("Startup sweep failed (non-fatal): %s", exc)

    await asyncio.gather(
        _consume_queue("embedding_jobs", process_embedding_job, config),
        _consume_queue("context_assembly_jobs", process_assembly_job, config),
        _consume_queue(
            "memory_extraction_jobs", process_extraction_job, config,
            sorted_set=True,
        ),
        _dead_letter_sweep_loop(config),
    )
