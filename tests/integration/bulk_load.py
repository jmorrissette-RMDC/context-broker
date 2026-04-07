"""
Bulk load test conversations into the Context Broker.

Reads phase1 test data from Z drive and loads via MCP store_message.
Monitors the async pipeline (embedding, assembly, extraction) with
fail-fast on errors and progress reporting.

Usage:
    python tests/integration/bulk_load.py
"""

import asyncio
import json
import logging
import sys
import time

import httpx

from config import (
    CB_MCP_URL,
    PHASE1_DIR,
    PIPELINE_TIMEOUT_SECONDS,
    PIPELINE_POLL_INTERVAL_SECONDS,
    PIPELINE_STALL_SECONDS,
    EARLY_VALIDATION_MESSAGES,
    MCP_CALL_TIMEOUT_SECONDS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


async def mcp_call(client: httpx.AsyncClient, tool_name: str, arguments: dict) -> dict:
    """Call an MCP tool and return the parsed result."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    resp = await client.post(CB_MCP_URL, json=payload, timeout=MCP_CALL_TIMEOUT_SECONDS)
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"MCP error: {body['error']}")
    result_text = body.get("result", {}).get("content", [{}])[0].get("text", "{}")
    return json.loads(result_text)


async def create_conversation(client: httpx.AsyncClient, title: str) -> str:
    """Create a new conversation and return its ID."""
    result = await mcp_call(
        client,
        "conv_create_conversation",
        {
            "title": title,
            "flow_id": "integration-test",
            "user_id": "test-runner",
        },
    )
    conv_id = result["conversation_id"]
    log.info(f"Created conversation: {title} ({conv_id})")
    return conv_id


async def store_message(client: httpx.AsyncClient, conv_id: str, msg: dict) -> None:
    """Store a single message via MCP."""
    await mcp_call(
        client,
        "store_message",
        {
            "conversation_id": conv_id,
            "role": msg["role"],
            "content": msg["content"],
            "sender": msg.get("sender", "unknown"),
        },
    )


async def get_pipeline_status(client: httpx.AsyncClient) -> dict:
    """Get queue depths and dead letter counts via direct DB query.

    Returns dict with embedding_count, assembly_count, extraction_count,
    dead_letter_count, total_messages, total_embeddings.
    """
    # Use metrics endpoint for queue depths
    try:
        resp = await client.get(f"{CB_MCP_URL.replace('/mcp', '/metrics')}", timeout=10)
        metrics_text = resp.text

        def extract_metric(name: str) -> float:
            for line in metrics_text.split("\n"):
                if line.startswith(name + " "):
                    return float(line.split()[-1])
            return 0.0

        return {
            "embedding_queue": extract_metric("context_broker_embedding_queue_depth"),
            "assembly_queue": extract_metric("context_broker_assembly_queue_depth"),
            "extraction_queue": extract_metric("context_broker_extraction_queue_depth"),
        }
    except (httpx.HTTPError, ValueError) as exc:
        log.warning(f"Failed to get metrics: {exc}")
        return {"embedding_queue": -1, "assembly_queue": -1, "extraction_queue": -1}


async def get_db_counts() -> dict:
    """Get message and embedding counts via SSH + docker exec psql."""
    import subprocess

    def ssh_psql(query: str) -> str:
        result = subprocess.run(
            [
                "ssh",
                "aristotle9@192.168.1.110",
                f'docker exec context-broker-postgres psql -U context_broker -d context_broker -t -c "{query}"',
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()

    def ssh_redis(cmd: str) -> str:
        result = subprocess.run(
            [
                "ssh",
                "aristotle9@192.168.1.110",
                f"docker exec context-broker-redis redis-cli {cmd}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()

    try:
        total_msgs = int(ssh_psql("SELECT COUNT(*) FROM conversation_messages") or "0")
        total_embedded = int(
            ssh_psql(
                "SELECT COUNT(*) FROM conversation_messages WHERE embedding IS NOT NULL"
            )
            or "0"
        )
        total_summaries = int(
            ssh_psql("SELECT COUNT(*) FROM conversation_summaries") or "0"
        )
    except (ValueError, subprocess.TimeoutExpired):
        total_msgs = total_embedded = total_summaries = -1

    dead_letters = 0
    try:
        dl_keys = ssh_redis("KEYS *dead_letter*")
        for key in dl_keys.split("\n"):
            key = key.strip()
            if key:
                count = ssh_redis(f"LLEN {key}")
                dead_letters += int(count or "0")
    except (ValueError, subprocess.TimeoutExpired):
        pass

    return {
        "total_messages": total_msgs,
        "total_embedded": total_embedded,
        "total_summaries": total_summaries,
        "dead_letters": dead_letters,
    }


async def wait_for_pipeline(
    client: httpx.AsyncClient,
    expected_messages: int,
) -> bool:
    """Wait for the async pipeline to process all messages.

    Returns True if pipeline completed, False on timeout/error.
    """
    start = time.monotonic()
    last_progress_time = start
    last_embedded = 0

    while True:
        elapsed = time.monotonic() - start

        if elapsed > PIPELINE_TIMEOUT_SECONDS:
            log.error(
                f"TIMEOUT: Pipeline did not complete in {PIPELINE_TIMEOUT_SECONDS}s"
            )
            return False

        counts = await get_db_counts()
        queues = await get_pipeline_status(client)

        log.info(
            f"[{elapsed:6.0f}s] "
            f"Embedded: {counts['total_embedded']}/{expected_messages}  "
            f"Summaries: {counts['total_summaries']}  "
            f"Dead: {counts['dead_letters']}  "
            f"Queues: emb={queues['embedding_queue']:.0f} "
            f"asm={queues['assembly_queue']:.0f} "
            f"ext={queues['extraction_queue']:.0f}"
        )

        # Fail fast: dead letters
        if counts["dead_letters"] > 0:
            log.error(f"FAIL: {counts['dead_letters']} dead letter messages found")
            return False

        # Check for stall
        if counts["total_embedded"] > last_embedded:
            last_progress_time = time.monotonic()
            last_embedded = counts["total_embedded"]
        elif time.monotonic() - last_progress_time > PIPELINE_STALL_SECONDS:
            # Stall is only an error if queues aren't empty
            if queues["embedding_queue"] > 0 or queues["assembly_queue"] > 0:
                log.error(
                    f"STALL: No progress for {PIPELINE_STALL_SECONDS}s "
                    f"but queues not empty"
                )
                return False

        # Success: embedding queue empty (extraction runs in background, may take longer)
        if queues["embedding_queue"] == 0 and counts["total_embedded"] > 0:
            log.info(
                f"Pipeline complete. "
                f"Embedded: {counts['total_embedded']}, "
                f"Summaries: {counts['total_summaries']}, "
                f"Elapsed: {elapsed:.0f}s"
            )
            return True

        await asyncio.sleep(PIPELINE_POLL_INTERVAL_SECONDS)


async def main():
    # Discover test data files
    data_files = sorted(PHASE1_DIR.glob("conversation-*.json"))
    if not data_files:
        log.error(f"No test data files found in {PHASE1_DIR}")
        sys.exit(3)

    log.info(f"=== Bulk Load: {len(data_files)} conversations ===")

    total_messages = 0
    conversations = []

    async with httpx.AsyncClient() as client:
        # Phase 1a: Load messages
        for data_file in data_files:
            messages = json.loads(data_file.read_text(encoding="utf-8"))
            conv_id = await create_conversation(client, data_file.stem)

            log.info(f"Loading {len(messages)} messages into {data_file.stem}...")
            load_start = time.monotonic()

            for i, msg in enumerate(messages):
                try:
                    await store_message(client, conv_id, msg)
                except (httpx.HTTPError, RuntimeError) as exc:
                    log.error(f"Failed at message {i}: {exc}")
                    sys.exit(1)

                total_messages += 1

                # Early validation checkpoint
                if total_messages == EARLY_VALIDATION_MESSAGES:
                    log.info(
                        f"--- Early validation at {EARLY_VALIDATION_MESSAGES} messages ---"
                    )
                    await asyncio.sleep(5)  # Give pipeline a moment
                    counts = await get_db_counts()
                    if counts["total_embedded"] == 0:
                        log.error(
                            "FAIL: No embeddings generated after "
                            f"{EARLY_VALIDATION_MESSAGES} messages. "
                            "Check Ollama/embedding config."
                        )
                        sys.exit(1)
                    if counts["dead_letters"] > 0:
                        log.error(
                            f"FAIL: {counts['dead_letters']} dead letters "
                            "after early messages. Pipeline is failing."
                        )
                        sys.exit(1)
                    log.info(
                        f"  Early validation PASS: "
                        f"{counts['total_embedded']} embeddings, "
                        f"0 dead letters"
                    )

                # Progress every 500 messages
                if total_messages % 500 == 0:
                    elapsed = time.monotonic() - load_start
                    rate = total_messages / elapsed if elapsed > 0 else 0
                    log.info(f"  Stored {total_messages} messages ({rate:.0f} msg/s)")

            conversations.append(
                {
                    "file": data_file.name,
                    "conversation_id": conv_id,
                    "message_count": len(messages),
                }
            )

            load_elapsed = time.monotonic() - load_start
            log.info(
                f"  Loaded {len(messages)} messages in {load_elapsed:.0f}s "
                f"({len(messages)/load_elapsed:.0f} msg/s)"
            )

        log.info(f"\n=== All messages stored: {total_messages} total ===")
        log.info("Waiting for pipeline to complete...")

        # Phase 1b: Wait for pipeline
        success = await wait_for_pipeline(client, total_messages)

        if not success:
            log.error("Pipeline failed. See logs above.")
            sys.exit(1)

    # Write results for downstream scripts
    results = {
        "total_messages": total_messages,
        "conversations": conversations,
        "status": "success",
    }
    results_path = PHASE1_DIR / "load_results.json"
    results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    log.info(f"Results written to {results_path}")

    log.info("=== Bulk load complete ===")


if __name__ == "__main__":
    asyncio.run(main())
