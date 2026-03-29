"""Session-scoped fixtures for live integration tests.

Lifecycle:
1. Run deploy.sh to stand up the test stack
2. Bulk load Phase 1 data (10,430 messages)
3. Wait for pipeline completion (embeddings, extraction, assembly)
4. Run all live tests
5. Tear down the stack via deploy.sh --down
6. Generate ISSUES.md from issues.json
"""

import json
import subprocess
import time

import httpx
import pytest

from .helpers import (
    BASE_URL,
    BASE_PORT,
    COMPOSE_PROJECT,
    PHASE1_DIR,
    PROJECT_ROOT,
    SSH_TARGET,
    REMOTE_PROJECT_DIR,
    compose_cmd,
    extract_mcp_result,
    generate_issues_md,
    get_db_counts,
    log_issue,
    mcp_call,
    wait_for_health,
    wait_for_pipeline,
    ISSUES_JSON,
    _run_on_docker_host,
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
# Deploy / teardown via deploy.sh
# ---------------------------------------------------------------------------

def _run_deploy_script(args: str, timeout: int = 600) -> tuple[int, str, str]:
    """Run deploy.sh on the Docker host."""
    script_path = f"{REMOTE_PROJECT_DIR}/deploy.sh"
    cmd = f"cd {REMOTE_PROJECT_DIR} && bash {script_path} {args}"

    if SSH_TARGET:
        escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
        full_cmd = f'ssh {SSH_TARGET} "{escaped}"'
    else:
        full_cmd = cmd

    result = subprocess.run(
        full_cmd, shell=True, capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Session-scoped test stack lifecycle
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def test_stack(http_client):
    """Deploy the test stack, load data, yield, then tear down."""
    # ------------------------------------------------------------------
    # Step 1: Check if stack is already running; deploy if not
    # ------------------------------------------------------------------
    # Clear issues from previous runs
    if ISSUES_JSON.exists():
        ISSUES_JSON.unlink()

    print(f"\n[SETUP] Checking if stack '{COMPOSE_PROJECT}' is already running...")
    already_running = False
    try:
        resp = mcp_call(http_client, "metrics_get", {})
        if resp.status_code == 200:
            already_running = True
            print("[SETUP] Stack already running and MCP ready — skipping deploy")
    except Exception:
        pass

    if not already_running:
        print(f"[SETUP] Deploying stack '{COMPOSE_PROJECT}' via deploy.sh...")
        rc, stdout, stderr = _run_deploy_script(
            f"{COMPOSE_PROJECT} {BASE_PORT} ./config-test",
            timeout=600,
        )

        if stdout:
            for line in stdout.splitlines():
                print(f"  {line}")
        if stderr:
            for line in stderr.splitlines():
                print(f"  [stderr] {line}")

        if rc != 0:
            print(f"[SETUP] deploy.sh failed (exit {rc}), attempting teardown...")
            _run_deploy_script(f"--down {COMPOSE_PROJECT}", timeout=60)
            pytest.fail(
                f"deploy.sh failed with exit code {rc}.\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )

        print("[SETUP] Stack deployed successfully")

        # Verify MCP readiness from test runner
        print("[SETUP] Verifying MCP readiness from test runner...")
        mcp_ready = False
        mcp_deadline = time.time() + 60
        while time.time() < mcp_deadline:
            try:
                resp = mcp_call(http_client, "metrics_get", {})
                if resp.status_code == 200:
                    mcp_ready = True
                    break
            except Exception:
                pass
            time.sleep(3)

        if not mcp_ready:
            pytest.fail(
                f"MCP endpoint at {BASE_URL}/mcp not reachable from test runner."
            )

        print("[SETUP] MCP endpoint verified from test runner")

    # ------------------------------------------------------------------
    # Step 3: Bulk load Phase 1 data (skip if already loaded)
    # ------------------------------------------------------------------
    loaded_conversations = {}

    # Check if data is already loaded by querying message count
    from .helpers import get_db_counts
    existing_counts = get_db_counts()
    if existing_counts["total"] > 1000:
        print(
            f"[SETUP] Data already loaded ({existing_counts['total']:,} messages) — skipping bulk load"
        )
        # Recover Phase 1 conversation IDs from DB.
        # conv_list_conversations returns test-created conversations first,
        # so use a large limit and filter by title prefix to find Phase 1 data.
        resp = mcp_call(http_client, "conv_list_conversations", {"limit": 100})
        if resp.status_code == 200:
            convs = extract_mcp_result(resp).get("conversations", [])
            for c in convs:
                title = c.get("title") or ""
                # Phase 1 conversations have titles like "claude-test-conversation-N"
                if "claude-test-conversation" in title:
                    loaded_conversations[title] = c["id"]
        if not loaded_conversations:
            # Fallback: take any conversation
            for c in convs:
                title = c.get("title") or c["id"]
                loaded_conversations[title] = c["id"]
        total_messages = existing_counts["total"]
    else:
        conversation_files = sorted(PHASE1_DIR.glob("conversation-*.json"))
        if not conversation_files:
            pytest.fail(f"No conversation files found in {PHASE1_DIR}")

        total_messages = 0
        load_errors = 0
        load_start = time.time()

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
            if resp.status_code != 200:
                log_issue(
                    "setup_bulk_load",
                    "error",
                    "data-loading",
                    f"Failed to create conversation for {conv_file.name}",
                    "200",
                    str(resp.status_code),
                )
                continue

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
                    load_errors += 1
                    if load_errors <= 5:
                        log_issue(
                            "setup_bulk_load",
                            "error",
                            "data-loading",
                            f"Failed to store message {i} in {conv_file.name}: HTTP {resp.status_code}",
                            "200",
                            str(resp.status_code),
                        )
                total_messages += 1

                if total_messages % 500 == 0:
                    elapsed = time.time() - load_start
                    rate = total_messages / elapsed if elapsed > 0 else 0
                    print(
                        f"[SETUP] Loaded {total_messages} messages "
                        f"({rate:.0f} msg/s, {load_errors} errors)..."
                    )

        load_elapsed = time.time() - load_start
        print(
            f"[SETUP] Loaded {total_messages} messages across "
            f"{len(loaded_conversations)} conversations "
            f"in {load_elapsed:.0f}s ({load_errors} errors)"
        )

        if load_errors > total_messages * 0.1:
            log_issue(
                "setup_bulk_load",
                "error",
                "data-loading",
                f">{10}% of messages failed to load: {load_errors}/{total_messages}",
                "<10% errors",
                f"{load_errors}/{total_messages}",
            )

    # ------------------------------------------------------------------
    # Step 4: Wait for pipeline (skip if data was pre-loaded)
    # ------------------------------------------------------------------
    if existing_counts["total"] <= 1000:
        print("[SETUP] Waiting for pipeline completion...")
        pipeline_start = time.time()
        try:
            final_counts = wait_for_pipeline(http_client, total_messages)
            pipeline_elapsed = time.time() - pipeline_start
            print(
                f"[SETUP] Pipeline complete in {pipeline_elapsed:.0f}s: "
                f"embedded={final_counts['embedded']}, "
                f"summaries={final_counts['summaries']}, "
                f"extracted={final_counts['extracted']}"
            )
        except TimeoutError as exc:
            pipeline_elapsed = time.time() - pipeline_start
            log_issue(
                "setup_pipeline_wait",
                "error",
                "pipeline",
                f"Pipeline did not complete in {pipeline_elapsed:.0f}s: {exc}",
                f"{total_messages} embeddings",
                str(get_db_counts()),
            )
            print(f"[SETUP] WARNING: Pipeline did not complete: {exc}")
    else:
        print("[SETUP] Data pre-loaded — skipping pipeline wait")

    # ------------------------------------------------------------------
    # Step 5: Ensure context windows exist for all conversations
    # ------------------------------------------------------------------
    # get_context creates windows on demand. Quality tests need windows
    # with completed assembly. Trigger get_context for each conversation
    # with tiered-summary build type so the assembly worker processes them.
    print("[SETUP] Creating context windows for all conversations...")
    for conv_name, conv_id in loaded_conversations.items():
        resp = mcp_call(
            http_client,
            "get_context",
            {
                "conversation_id": conv_id,
                "build_type": "tiered-summary",
                "budget": 16000,
            },
        )
        if resp.status_code != 200:
            print(f"[SETUP] WARNING: get_context failed for {conv_name}: {resp.status_code}")

    # Wait for assembly to complete on the newly created windows
    print("[SETUP] Waiting for assembly to complete on new windows...")
    assembly_deadline = time.time() + 120  # 2 minutes max
    while time.time() < assembly_deadline:
        counts = get_db_counts()
        if counts.get("summaries", 0) > 0:
            print(f"[SETUP] Assembly produced {counts['summaries']} summaries")
            break
        time.sleep(5)
    else:
        print("[SETUP] WARNING: Assembly did not produce summaries within 120s")

    # ------------------------------------------------------------------
    # Yield to tests
    # ------------------------------------------------------------------
    yield {
        "conversations": loaded_conversations,
        "total_messages": total_messages,
    }

    # ------------------------------------------------------------------
    # Teardown — generate issues log but leave stack running
    # ------------------------------------------------------------------
    print("\n[TEARDOWN] Generating issues log...")
    generate_issues_md()

    # Stack is left running for faster re-runs.
    # Tear down manually with: deploy.sh --down claude-test
    print("[TEARDOWN] Stack left running for re-use. Tear down with:")
    print(f"  deploy.sh --down {COMPOSE_PROJECT}")


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
def any_conversation_id(loaded_conversations, http_client):
    """Return a Phase 1 conversation ID with substantial data.

    Quality tests need conversations with thousands of messages and
    completed assembly. Uses the loaded_conversations dict which maps
    file stems (e.g., 'conversation-1') to conversation IDs. These
    are always Phase 1 bulk data, not test-created conversations.
    """
    # loaded_conversations is populated during bulk load (Step 3).
    # Keys are file stems like "conversation-1", "conversation-2", etc.
    # These are always Phase 1 data with thousands of messages.
    # When data is pre-loaded, keys come from conv_list_conversations
    # titles like "claude-test-conversation-1".
    for name, conv_id in loaded_conversations.items():
        if "conversation" in name.lower() and "imperator" not in name.lower():
            return conv_id
    # Fallback: return first loaded conversation
    return next(iter(loaded_conversations.values()))
