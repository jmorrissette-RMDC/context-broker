#!/usr/bin/env python3
"""Context Broker Stress Test — Endurance & Scale Verification.

Three scenarios executed sequentially:
  1. Seed & Lifecycle: Load 2M tokens, drive 10 compaction cycles via live chat
  2. Budget Gradient: Open windows at 8K-1M on the deep conversation
  3. Concurrent Windows: Open all 6 budget tiers simultaneously

Usage:
    python tests/stress/stress_test.py [--resume] [--scenario N] [--config path]

Requires:
    - Test stack running on irina (or configured host)
    - GOOGLE_API_KEY env var set (for Gemini Flash Lite user driver)
    - Claude CLI available (for quality judge)
"""

import argparse
import concurrent.futures
import json
import logging
import sys
import time
from pathlib import Path

import httpx
import yaml

from checkpoint import Checkpoint
from conversation_driver import ConversationDriver
from metrics_collector import CompactionState, MetricsCollector
from quality_judge import judge_context_quality
from session_parser import load_seed_messages

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("stress")


def load_config(path: str = None) -> dict:
    config_path = Path(path or Path(__file__).parent / "config.yml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def mcp_call(client: httpx.Client, tool: str, args: dict, timeout: int = 120) -> dict:
    """Call an MCP tool and return the parsed result."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }
    resp = client.post("/mcp", json=payload, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    if "error" in body and body["error"] is not None:
        return body
    text = body["result"]["content"][0]["text"]
    return json.loads(text)


def chat_call(client: httpx.Client, message: str, timeout: int = 180) -> str:
    """Send a chat message and return the assistant's response text."""
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "context-broker",
            "messages": [{"role": "user", "content": message}],
            "stream": False,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Scenario 1: Seed & Lifecycle
# ---------------------------------------------------------------------------


def scenario_seed_and_lifecycle(
    config: dict, client: httpx.Client, metrics: MetricsCollector, ckpt: Checkpoint
):
    """Load seed data, then drive live conversation until 10 compaction cycles."""
    seed_cfg = config["seed"]
    life_cfg = config["lifecycle"]
    target_cycles = life_cfg["target_compaction_cycles"]
    quality_interval = life_cfg["quality_check_interval"]
    budget = life_cfg["budget"]
    build_type = life_cfg["build_type"]

    # ── Step 1: Create conversation ──────────────────────────────
    conv_id = ckpt.conversation_id
    if not conv_id:
        _log.info("Creating stress test conversation...")
        result = mcp_call(client, "conv_create_conversation", {
            "title": "stress-test-endurance",
            "flow_id": "stress-test",
            "user_id": "stress-runner",
        })
        conv_id = result["conversation_id"]
        ckpt.conversation_id = conv_id
        ckpt.save(force=True)
        _log.info("Created conversation: %s", conv_id)
    else:
        _log.info("Resuming conversation: %s", conv_id)

    # ── Step 2: Seed data ────────────────────────────────────────
    already_loaded = ckpt.messages_loaded
    if already_loaded < seed_cfg["max_seed_tokens"] * seed_cfg["chars_per_token"] // 500:
        _log.info("Loading seed data from session files...")
        messages = load_seed_messages(
            seed_cfg["session_dir"],
            max_tokens=seed_cfg["max_seed_tokens"],
        )
        _log.info("Parsed %d messages for seeding", len(messages))

        batch_size = seed_cfg["batch_size"]
        start_idx = already_loaded
        load_start = time.time()
        errors = 0

        for i, msg in enumerate(messages[start_idx:], start=start_idx):
            result = mcp_call(client, "store_message", {
                "conversation_id": conv_id,
                "role": msg["role"],
                "content": msg["content"][:10000],  # Truncate very long messages
                "sender": "stress-seed",
            })
            if "error" in result:
                errors += 1
                if errors <= 5:
                    _log.warning("Store error at msg %d: %s", i, result.get("error"))

            ckpt.messages_loaded = i + 1
            ckpt.save()

            if (i + 1) % 500 == 0:
                elapsed = time.time() - load_start
                rate = (i + 1 - start_idx) / elapsed if elapsed > 0 else 0
                token_count = metrics.get_conversation_token_count(conv_id)
                _log.info(
                    "Seeded %d/%d messages (%.0f msg/s, ~%dK tokens, %d errors)",
                    i + 1, len(messages), rate, token_count // 1000, errors,
                )

        elapsed = time.time() - load_start
        _log.info(
            "Seed complete: %d messages in %.0fs (%d errors)",
            len(messages), elapsed, errors,
        )
    else:
        _log.info("Seed data already loaded (%d messages)", already_loaded)

    # Wait for pipeline to process seed data
    _log.info("Waiting for pipeline to process seed data...")
    time.sleep(30)

    # ── Step 3: Create initial context window ────────────────────
    _log.info("Creating initial context window (budget=%dK)...", budget // 1024)
    result = mcp_call(client, "get_context", {
        "conversation_id": conv_id,
        "build_type": build_type,
        "budget": budget,
    }, timeout=300)
    window_id = None
    if "context" in result:
        _log.info("Initial context assembled: %d messages", len(result.get("context", [])))
    # Get window ID from DB
    raw = metrics._psql(
        f"SELECT id FROM context_windows WHERE conversation_id = '{conv_id}' "
        f"AND build_type = '{build_type}' ORDER BY created_at DESC LIMIT 1"
    )
    if raw:
        window_id = raw.strip()
        _log.info("Context window: %s", window_id)

    # ── Step 4: Live conversation until target compaction cycles ──
    _log.info("Starting live conversation phase (target: %d compaction cycles)...", target_cycles)
    driver = ConversationDriver(
        model=life_cfg["user_model"],
        base_url=life_cfg["user_model_base_url"],
        api_key_env=life_cfg["user_model_api_key_env"],
    )

    last_quality_check = 0
    poll_interval = life_cfg.get("conversation_poll_interval", 30)
    turn_count = ckpt.live_turns

    try:
        while True:
            # Check compaction state
            if window_id:
                state = metrics.get_compaction_state(window_id)
                current_cycles = state.compaction_cycles
                ckpt.compaction_cycles = current_cycles

                if current_cycles >= target_cycles:
                    _log.info(
                        "TARGET REACHED: %d compaction cycles completed!", current_cycles
                    )
                    break

                # Quality checkpoint
                if current_cycles > 0 and current_cycles - last_quality_check >= quality_interval:
                    _log.info("Quality checkpoint at cycle %d...", current_cycles)
                    ctx_result = mcp_call(client, "get_context", {
                        "conversation_id": conv_id,
                        "build_type": build_type,
                        "budget": budget,
                    }, timeout=300)
                    context = ctx_result.get("context", [])
                    conv_tokens = metrics.get_conversation_token_count(conv_id)
                    quality = judge_context_quality(context, budget, conv_tokens, current_cycles)
                    ckpt.add_quality_checkpoint(current_cycles, budget, quality["rating"], quality["reasoning"])
                    _log.info(
                        "Quality at cycle %d: %s (tier3=%d, tier2=%d tokens)",
                        current_cycles, quality["rating"],
                        state.tier3_tokens, state.tier2_tokens,
                    )
                    last_quality_check = current_cycles

                # Log progress
                rss = metrics.get_container_rss_mb()
                _log.info(
                    "Turn %d | Cycles: %d/%d | T3: %d tok | T2: %d tok (%d chunks) | RSS: %.0f MB",
                    turn_count, current_cycles, target_cycles,
                    state.tier3_tokens, state.tier2_tokens, state.active_tier2_count,
                    rss,
                )

            # Generate user message
            if turn_count == 0:
                user_msg = driver.generate_initial_message()
            else:
                user_msg = driver.generate_message(last_response if 'last_response' in dir() else "Hello")

            # Send to Imperator
            _log.info("Turn %d: sending %d chars...", turn_count, len(user_msg))
            try:
                last_response = chat_call(client, user_msg, timeout=180)
                _log.info("Turn %d: received %d chars", turn_count, len(last_response))
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                _log.warning("Turn %d failed: %s", turn_count, exc)
                last_response = "I had trouble processing that. Could you rephrase?"
                time.sleep(10)

            turn_count += 1
            ckpt.live_turns = turn_count
            ckpt.save()

            # Brief pause between turns
            time.sleep(5)

    finally:
        driver.close()

    _log.info("Scenario 1 complete: %d turns, %d compaction cycles", turn_count, current_cycles)


# ---------------------------------------------------------------------------
# Scenario 2: Budget Gradient
# ---------------------------------------------------------------------------


def scenario_budget_gradient(
    config: dict, client: httpx.Client, metrics: MetricsCollector, ckpt: Checkpoint
):
    """Open context windows at each budget tier on the deep conversation."""
    grad_cfg = config["gradient"]
    conv_id = ckpt.conversation_id
    if not conv_id:
        _log.error("No conversation_id — run scenario 1 first")
        return

    conv_tokens = metrics.get_conversation_token_count(conv_id)
    _log.info("Budget gradient on conversation with ~%dK tokens", conv_tokens // 1000)

    results = []
    for budget in grad_cfg["budgets"]:
        compression = (1 - budget / conv_tokens) * 100 if conv_tokens > 0 else 0
        _log.info(
            "Testing budget %dK (%.1f%% compression)...",
            budget // 1024, compression,
        )

        start = time.time()
        try:
            result = mcp_call(client, "get_context", {
                "conversation_id": conv_id,
                "build_type": grad_cfg["build_type"],
                "budget": budget,
            }, timeout=grad_cfg["assembly_timeout"])

            elapsed = time.time() - start
            context = result.get("context", [])
            context_tokens = sum(len(str(m.get("content", ""))) // 4 for m in context)

            # Get window for tier breakdown
            raw = metrics._psql(
                f"SELECT id FROM context_windows WHERE conversation_id = '{conv_id}' "
                f"AND build_type = '{grad_cfg['build_type']}' "
                f"AND max_token_budget = {budget} LIMIT 1"
            )
            tier_info = {}
            if raw:
                state = metrics.get_compaction_state(raw.strip())
                tier_info = {
                    "tier3_tokens": state.tier3_tokens,
                    "tier2_tokens": state.tier2_tokens,
                    "tier2_chunks": state.active_tier2_count,
                }

            # Quality judge
            quality = judge_context_quality(context, budget, conv_tokens, 0)

            entry = {
                "budget": budget,
                "compression_pct": round(compression, 1),
                "assembly_seconds": round(elapsed, 1),
                "context_messages": len(context),
                "context_tokens": context_tokens,
                "quality_rating": quality["rating"],
                **tier_info,
            }
            results.append(entry)
            _log.info(
                "  %dK: %d msgs, %dK tokens, %.1fs, quality=%s, T3=%d T2=%d",
                budget // 1024, len(context), context_tokens // 1000,
                elapsed, quality["rating"],
                tier_info.get("tier3_tokens", 0), tier_info.get("tier2_tokens", 0),
            )
            ckpt.add_quality_checkpoint(0, budget, quality["rating"], quality["reasoning"])

        except Exception as exc:
            _log.error("  %dK FAILED: %s", budget // 1024, exc)
            results.append({
                "budget": budget,
                "error": str(exc),
            })

    # Summary table
    _log.info("\n--- Budget Gradient Results ---")
    _log.info("%-8s %-8s %-8s %-8s %-10s %-8s", "Budget", "Compress", "Msgs", "Tokens", "Quality", "Time")
    for r in results:
        if "error" in r:
            _log.info("%-8s ERROR: %s", f"{r['budget']//1024}K", r["error"][:40])
        else:
            _log.info(
                "%-8s %-8s %-8s %-8s %-10s %-8s",
                f"{r['budget']//1024}K",
                f"{r['compression_pct']}%",
                r["context_messages"],
                f"{r['context_tokens']//1000}K",
                r["quality_rating"],
                f"{r['assembly_seconds']}s",
            )

    ckpt.set("gradient_results", results)
    ckpt.save(force=True)


# ---------------------------------------------------------------------------
# Scenario 3: Concurrent Windows
# ---------------------------------------------------------------------------


def scenario_concurrent_windows(
    config: dict, client: httpx.Client, metrics: MetricsCollector, ckpt: Checkpoint
):
    """Open all budget tiers simultaneously on the same conversation."""
    conc_cfg = config["concurrent"]
    conv_id = ckpt.conversation_id
    if not conv_id:
        _log.error("No conversation_id — run scenario 1 first")
        return

    budgets = conc_cfg["budgets"]
    _log.info("Concurrent window test: %d budgets in parallel...", len(budgets))

    def _open_window(budget):
        """Open a single context window (runs in thread)."""
        start = time.time()
        try:
            # Each thread needs its own HTTP client
            with httpx.Client(base_url=client.base_url, timeout=600.0) as thread_client:
                result = mcp_call(thread_client, "get_context", {
                    "conversation_id": conv_id,
                    "build_type": "tiered-summary",
                    "budget": budget,
                }, timeout=600)
                elapsed = time.time() - start
                context = result.get("context", [])
                return {
                    "budget": budget,
                    "success": True,
                    "messages": len(context),
                    "seconds": round(elapsed, 1),
                }
        except Exception as exc:
            return {
                "budget": budget,
                "success": False,
                "error": str(exc),
                "seconds": round(time.time() - start, 1),
            }

    start_all = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(budgets)) as pool:
        futures = {pool.submit(_open_window, b): b for b in budgets}
        results = []
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            budget = result["budget"]
            if result["success"]:
                _log.info(
                    "  %dK: %d msgs in %.1fs",
                    budget // 1024, result["messages"], result["seconds"],
                )
            else:
                _log.error("  %dK FAILED in %.1fs: %s", budget // 1024, result["seconds"], result["error"])

    total_time = time.time() - start_all
    successes = sum(1 for r in results if r["success"])
    _log.info(
        "Concurrent test complete: %d/%d succeeded in %.1fs",
        successes, len(budgets), total_time,
    )

    ckpt.set("concurrent_results", results)
    ckpt.save(force=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Context Broker Stress Test")
    parser.add_argument("--config", default=None, help="Config file path")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--scenario", type=int, choices=[1, 2, 3], help="Run specific scenario only")
    args = parser.parse_args()

    config = load_config(args.config)
    target = config["target"]
    base_url = f"http://{target['host']}:{target['port']}"

    ckpt_path = config.get("checkpoint", {}).get("file", "tests/stress/checkpoint.json")
    ckpt = Checkpoint(path=ckpt_path)
    if not args.resume:
        ckpt.clear()

    metrics = MetricsCollector(
        base_url=base_url,
        ssh_target=target["ssh"],
        remote_project_dir=target["remote_project_dir"],
        compose_project=target["compose_project"],
        compose_file=target["compose_file"],
    )

    client = httpx.Client(base_url=base_url, timeout=300.0)

    # Verify stack is up
    try:
        resp = client.get("/health", timeout=10)
        if resp.status_code != 200:
            _log.error("Stack not healthy: %s", resp.status_code)
            sys.exit(1)
    except httpx.HTTPError as exc:
        _log.error("Stack not reachable at %s: %s", base_url, exc)
        sys.exit(1)
    _log.info("Stack healthy at %s", base_url)

    start_time = time.time()

    try:
        if args.scenario in (None, 1):
            _log.info("=" * 60)
            _log.info("SCENARIO 1: Seed & Lifecycle (10 compaction cycles)")
            _log.info("=" * 60)
            ckpt.scenario = "seed_and_lifecycle"
            scenario_seed_and_lifecycle(config, client, metrics, ckpt)

        if args.scenario in (None, 2):
            _log.info("=" * 60)
            _log.info("SCENARIO 2: Budget Gradient (8K-1M)")
            _log.info("=" * 60)
            ckpt.scenario = "budget_gradient"
            scenario_budget_gradient(config, client, metrics, ckpt)

        if args.scenario in (None, 3):
            _log.info("=" * 60)
            _log.info("SCENARIO 3: Concurrent Windows")
            _log.info("=" * 60)
            ckpt.scenario = "concurrent_windows"
            scenario_concurrent_windows(config, client, metrics, ckpt)

    except KeyboardInterrupt:
        _log.info("Interrupted — checkpoint saved")
        ckpt.save(force=True)
    finally:
        elapsed = time.time() - start_time
        _log.info("=" * 60)
        _log.info("Stress test complete in %.0f minutes", elapsed / 60)
        _log.info("Total estimated cost: $%.2f", ckpt.total_cost)
        _log.info("Checkpoint: %s", ckpt.path)
        _log.info("=" * 60)
        client.close()
        metrics.close()
        ckpt.save(force=True)


if __name__ == "__main__":
    main()
