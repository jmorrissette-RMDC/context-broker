"""Session-scoped fixtures for live integration tests.

Lifecycle:
1. Deploy the test Docker Compose stack (claude-test-net, port 8081)
2. Wait for all containers healthy
3. Bulk load Phase 1 data (10,430 messages)
4. Wait for pipeline completion (embeddings, extraction, assembly)
5. Run all live tests
6. Tear down the stack and generate ISSUES.md
"""

import json
import time
import uuid

import httpx
import pytest

from .helpers import (
    BASE_URL,
    COMPOSE_FILE,
    PHASE1_DIR,
    PROJECT_ROOT,
    compose_cmd,
    docker_exec,
    docker_psql,
    extract_mcp_result,
    generate_issues_md,
    get_db_counts,
    log_issue,
    mcp_call,
    wait_for_health,
    wait_for_pipeline,
    ISSUES_JSON,
)


def pytest_configure(config):
    """Register the 'live' marker."""
    config.addinivalue_line("markers", "live: integration tests against deployed test stack")


# ---------------------------------------------------------------------------
# Session-scoped HTTP client
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def http_client():
    """Session-scoped httpx client pointing at the test stack."""
    with httpx.Client(base_url=BASE_URL, timeout=180.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Session-scoped test stack lifecycle
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def test_stack(http_client):
    """Deploy the test stack, load data, yield, then tear down."""
    # ------------------------------------------------------------------
    # Step 1: Deploy
    # ------------------------------------------------------------------
    print("\n[SETUP] Deploying Claude test stack...")
    compose_cmd("up -d --build", timeout=600)

    # ------------------------------------------------------------------
    # Step 2: Wait for health
    # ------------------------------------------------------------------
    print("[SETUP] Waiting for test stack to become healthy...")
    healthy = wait_for_health(http_client, timeout=180)
    if not healthy:
        logs = compose_cmd("logs --tail 50")
        compose_cmd("down -v", timeout=60)
        pytest.fail(f"Test stack failed to become healthy within 180s.\nLogs:\n{logs}")

    print("[SETUP] Test stack healthy")

    # ------------------------------------------------------------------
    # Step 2a: Create mem0_memories stub if missing
    # ------------------------------------------------------------------
    # Migration 016 expects this table to exist but Mem0 creates it
    # lazily. On a fresh DB, migrations loop forever without this stub.
    docker_psql(
        "CREATE TABLE IF NOT EXISTS mem0_memories ("
        "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), "
        "memory TEXT, payload JSONB, user_id VARCHAR(255), "
        "embedding vector)"
    )
    # Restart langgraph so migrations can complete with the table present
    compose_cmd("restart context-broker-langgraph", timeout=60)
    time.sleep(10)

    # ------------------------------------------------------------------
    # Step 2b: Wait for MCP readiness (Postgres middleware)
    # ------------------------------------------------------------------
    # /health can return 200 while the langgraph app's Postgres middleware
    # still returns 503. Wait until an MCP call succeeds.
    print("[SETUP] Waiting for MCP readiness...")
    mcp_ready = False
    mcp_deadline = time.time() + 120
    while time.time() < mcp_deadline:
        try:
            resp = mcp_call(
                http_client, "metrics_get", {},
            )
            if resp.status_code == 200:
                mcp_ready = True
                break
        except Exception:
            pass
        time.sleep(3)

    if not mcp_ready:
        logs = compose_cmd("logs --tail 50")
        pytest.fail(f"MCP endpoint not ready within 120s.\nLogs:\n{logs}")

    print("[SETUP] MCP endpoint ready")

    # ------------------------------------------------------------------
    # Step 3: Bulk load Phase 1 data
    # ------------------------------------------------------------------
    loaded_conversations = {}

    conversation_files = sorted(PHASE1_DIR.glob("conversation-*.json"))
    if not conversation_files:
        pytest.fail(f"No conversation files found in {PHASE1_DIR}")

    total_messages = 0
    for conv_file in conversation_files:
        print(f"[SETUP] Loading {conv_file.name}...")
        messages = json.loads(conv_file.read_text(encoding="utf-8"))

        # Create conversation
        resp = mcp_call(
            http_client,
            "conv_create_conversation",
            {
                "title": f"claude-test-{conv_file.stem}",
                "flow_id": "claude-test",
                "user_id": "test-runner",
            },
        )
        assert resp.status_code == 200, f"Failed to create conversation: {resp.text}"
        conv_id = extract_mcp_result(resp)["conversation_id"]
        loaded_conversations[conv_file.stem] = conv_id

        # Store all messages
        for i, msg in enumerate(messages):
            resp = mcp_call(
                http_client,
                "store_message",
                {
                    "conversation_id": conv_id,
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                    "sender": msg.get("sender", "unknown"),
                },
            )
            if resp.status_code != 200:
                log_issue(
                    "setup_bulk_load",
                    "error",
                    "data-loading",
                    f"Failed to store message {i} in {conv_file.name}",
                    "200",
                    str(resp.status_code),
                )
            total_messages += 1

            if total_messages % 500 == 0:
                print(f"[SETUP] Loaded {total_messages} messages...")

    print(f"[SETUP] Loaded {total_messages} messages across {len(loaded_conversations)} conversations")

    # ------------------------------------------------------------------
    # Step 4: Wait for pipeline
    # ------------------------------------------------------------------
    print("[SETUP] Waiting for pipeline completion...")
    try:
        final_counts = wait_for_pipeline(http_client, total_messages)
        print(
            f"[SETUP] Pipeline complete: "
            f"embedded={final_counts['embedded']}, "
            f"summaries={final_counts['summaries']}, "
            f"extracted={final_counts['extracted']}"
        )
    except TimeoutError as exc:
        log_issue(
            "setup_pipeline_wait",
            "error",
            "pipeline",
            str(exc),
            f"{total_messages} embeddings",
            str(get_db_counts()),
        )
        print(f"[SETUP] WARNING: Pipeline did not complete: {exc}")
        # Don't fail — tests can still run on partial data

    # ------------------------------------------------------------------
    # Yield to tests
    # ------------------------------------------------------------------
    yield {
        "conversations": loaded_conversations,
        "total_messages": total_messages,
    }

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    print("\n[TEARDOWN] Generating issues log...")
    generate_issues_md()

    print("[TEARDOWN] Tearing down Claude test stack...")
    compose_cmd("down -v", timeout=120)
    print("[TEARDOWN] Done")


# ---------------------------------------------------------------------------
# Convenience fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def loaded_conversations(test_stack):
    """Dict mapping conversation file stems to their UUIDs."""
    return test_stack["conversations"]


@pytest.fixture(scope="session")
def total_messages(test_stack):
    """Total number of messages loaded."""
    return test_stack["total_messages"]


@pytest.fixture(scope="session")
def any_conversation_id(loaded_conversations):
    """Return any loaded conversation ID for tests that just need one."""
    return next(iter(loaded_conversations.values()))
