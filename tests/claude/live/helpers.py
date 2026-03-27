"""Shared helpers for live integration tests.

All helpers operate against the Claude test stack (docker-compose.claude-test.yml).
The test stack runs on port 8081 by default (configurable via CLAUDE_TEST_PORT env var).
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # ContextBroker/
COMPOSE_FILE = str(PROJECT_ROOT / "docker-compose.claude-test.yml")
COMPOSE_PROJECT = "claude-test"
TEST_DATA_DIR = Path(r"Z:\test-data\conversational-memory")
PHASE1_DIR = TEST_DATA_DIR / "phase1-bulk-load"
PHASE2_DIR = TEST_DATA_DIR / "phase2-agent-conversation"
PHASE3_DIR = TEST_DATA_DIR / "phase3-synthetic"

# Docker host — where the test stack runs. If SSH_TARGET is set, docker
# commands are run remotely via SSH. Otherwise they run locally.
SSH_TARGET = os.environ.get("CLAUDE_TEST_SSH", "aristotle9@192.168.1.110")
DOCKER_HOST = os.environ.get("CLAUDE_TEST_HOST", "192.168.1.110")
REMOTE_PROJECT_DIR = "/mnt/storage/projects/portfolio/ContextBroker"

BASE_PORT = int(os.environ.get("CLAUDE_TEST_PORT", "8081"))
BASE_URL = f"http://{DOCKER_HOST}:{BASE_PORT}"
MCP_URL = f"{BASE_URL}/mcp"
CHAT_URL = f"{BASE_URL}/v1/chat/completions"
HEALTH_URL = f"{BASE_URL}/health"
METRICS_URL = f"{BASE_URL}/metrics"

ISSUES_JSON = Path(__file__).resolve().parent.parent / "issues.json"
ISSUES_MD = Path(__file__).resolve().parent.parent / "ISSUES.md"

# Timeouts
MCP_TIMEOUT = 60
CHAT_TIMEOUT = 120
PIPELINE_TIMEOUT = 3600  # 60 min
PIPELINE_POLL_INTERVAL = 10
PIPELINE_STALL_SECONDS = 180


# ---------------------------------------------------------------------------
# Docker Compose helpers
# ---------------------------------------------------------------------------

def _run_on_docker_host(cmd: str, timeout: int = 120) -> str:
    """Run a shell command on the Docker host (via SSH if remote).

    Uses double-quote wrapping for the SSH command so that single quotes
    inside the command (e.g. SQL literals) pass through correctly.
    """
    if SSH_TARGET:
        # Escape any double quotes and backslashes in the command for the
        # outer double-quote wrapping.
        escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
        full_cmd = f'ssh {SSH_TARGET} "{escaped}"'
    else:
        full_cmd = cmd
    result = subprocess.run(
        full_cmd, shell=True, capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip()


def compose_cmd(cmd: str, timeout: int = 120) -> str:
    """Run a docker compose command against the test stack."""
    remote_cmd = (
        f"cd {REMOTE_PROJECT_DIR} && "
        f"docker compose -p {COMPOSE_PROJECT} -f docker-compose.claude-test.yml {cmd}"
    )
    return _run_on_docker_host(remote_cmd, timeout=timeout)


def docker_exec(service: str, cmd: str, timeout: int = 30) -> str:
    """Execute a command inside a running test container via compose exec."""
    remote_cmd = (
        f"cd {REMOTE_PROJECT_DIR} && "
        f"docker compose -p {COMPOSE_PROJECT} -f docker-compose.claude-test.yml "
        f"exec -T {service} {cmd}"
    )
    return _run_on_docker_host(remote_cmd, timeout=timeout)


def docker_psql(query: str) -> str:
    """Run a psql query against the test Postgres container.

    Quoting traverses: local shell -> SSH (double-quote wrapped) -> remote shell
    -> docker compose exec -> psql.

    _run_on_docker_host escapes inner double-quotes and backslashes, then wraps
    the whole command in double quotes for SSH.  So we use double quotes around
    the SQL in the -c flag, and they will be properly escaped/unescaped at
    each layer.
    """
    # No need to pre-escape: _run_on_docker_host handles it.
    # The SQL queries used in tests do not contain double quotes or backslashes.
    return docker_exec(
        "context-broker-postgres",
        f'psql -U context_broker -d context_broker -t -A -c "{query}"',
    )


def docker_logs(service: str, lines: int = 50) -> str:
    """Get recent logs from a test container."""
    remote_cmd = (
        f"cd {REMOTE_PROJECT_DIR} && "
        f"docker compose -p {COMPOSE_PROJECT} -f docker-compose.claude-test.yml "
        f"logs --tail {lines} {service}"
    )
    return _run_on_docker_host(remote_cmd, timeout=30)


# ---------------------------------------------------------------------------
# MCP helpers
# ---------------------------------------------------------------------------

def mcp_call(
    client: httpx.Client,
    tool_name: str,
    arguments: dict,
    *,
    method: str = "tools/call",
    request_id: int = 1,
    timeout: int = MCP_TIMEOUT,
) -> httpx.Response:
    """Send an MCP JSON-RPC tool call."""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {"name": tool_name, "arguments": arguments},
    }
    return client.post("/mcp", json=payload, timeout=timeout)


def mcp_call_raw(client: httpx.Client, payload: dict) -> httpx.Response:
    """Send a raw JSON-RPC payload to the MCP endpoint."""
    return client.post("/mcp", json=payload, timeout=MCP_TIMEOUT)


def extract_mcp_result(response: httpx.Response) -> dict:
    """Extract the parsed result from an MCP JSON-RPC response."""
    body = response.json()
    if "error" in body and body["error"] is not None:
        return body
    text = body["result"]["content"][0]["text"]
    return json.loads(text)


# ---------------------------------------------------------------------------
# Chat helpers
# ---------------------------------------------------------------------------

def chat_call(
    client: httpx.Client,
    message: str,
    history: list[dict] | None = None,
    *,
    stream: bool = False,
    timeout: int = CHAT_TIMEOUT,
) -> dict:
    """Send a message to the Imperator via /v1/chat/completions."""
    messages = list(history or [])
    messages.append({"role": "user", "content": message})
    payload = {
        "model": "context-broker",
        "messages": messages,
        "stream": stream,
    }
    try:
        resp = client.post("/v1/chat/completions", json=payload, timeout=timeout)
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout) as exc:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": f"[Timeout: {exc}]",
                    }
                }
            ],
            "error": True,
        }
    if resp.status_code >= 500:
        # Return a synthetic error response instead of raising on 5xx
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": f"[Server error {resp.status_code}: {resp.text[:200]}]",
                    }
                }
            ],
            "error": True,
        }
    resp.raise_for_status()
    result = resp.json()
    return result


# ---------------------------------------------------------------------------
# Pipeline wait
# ---------------------------------------------------------------------------

def wait_for_health(client: httpx.Client, timeout: int = 120) -> bool:
    """Poll /health until it returns 200."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = client.get("/health", timeout=5)
            if resp.status_code == 200:
                return True
        except (httpx.HTTPError, httpx.ConnectError):
            pass
        time.sleep(2)
    return False


def get_pipeline_status(client: httpx.Client) -> dict:
    """Parse /metrics for queue depths."""
    resp = client.get("/metrics", timeout=10)
    text = resp.text
    status = {}
    for metric_name in [
        "context_broker_embedding_queue_depth",
        "context_broker_assembly_queue_depth",
        "context_broker_extraction_queue_depth",
    ]:
        match = re.search(rf"^{metric_name}\s+([\d.]+)", text, re.MULTILINE)
        status[metric_name] = float(match.group(1)) if match else -1
    return status


def _parse_int(raw: str) -> int:
    """Extract the first integer from psql output, defaulting to 0."""
    m = re.search(r"(\d+)", raw)
    return int(m.group(1)) if m else 0


def get_db_counts() -> dict:
    """Get pipeline progress counts from PostgreSQL."""
    total = docker_psql("SELECT COUNT(*) FROM conversation_messages").strip()
    embedded = docker_psql(
        "SELECT COUNT(*) FROM conversation_messages WHERE embedding IS NOT NULL"
    ).strip()
    summaries = docker_psql("SELECT COUNT(*) FROM conversation_summaries").strip()
    extracted = docker_psql(
        "SELECT COUNT(*) FROM conversation_messages WHERE memory_extracted = TRUE"
    ).strip()
    return {
        "total": _parse_int(total),
        "embedded": _parse_int(embedded),
        "summaries": _parse_int(summaries),
        "extracted": _parse_int(extracted),
    }


def wait_for_pipeline(
    client: httpx.Client,
    expected_messages: int,
    timeout: int = PIPELINE_TIMEOUT,
    stall_seconds: int = PIPELINE_STALL_SECONDS,
) -> dict:
    """Wait for the pipeline to finish processing all messages."""
    start = time.monotonic()
    last_progress_time = start
    last_embedded = 0

    while True:
        elapsed = time.monotonic() - start
        if elapsed > timeout:
            counts = get_db_counts()
            raise TimeoutError(
                f"Pipeline timeout after {int(elapsed)}s. "
                f"Embedded: {counts['embedded']}/{expected_messages}"
            )

        counts = get_db_counts()
        status = get_pipeline_status(client)

        if counts["embedded"] > last_embedded:
            last_progress_time = time.monotonic()
            last_embedded = counts["embedded"]

        # Check for stall
        if time.monotonic() - last_progress_time > stall_seconds:
            raise TimeoutError(
                f"Pipeline stalled for {stall_seconds}s. "
                f"Embedded: {counts['embedded']}/{expected_messages}"
            )

        # Embedding complete when all messages with content have embeddings
        emb_queue = status.get("context_broker_embedding_queue_depth", -1)
        if emb_queue == 0 and counts["embedded"] > 0:
            # Give extraction and assembly a moment to catch up
            time.sleep(5)
            return get_db_counts()

        time.sleep(PIPELINE_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Issues log
# ---------------------------------------------------------------------------

def log_issue(
    test_name: str,
    severity: str,
    category: str,
    description: str,
    expected: str = "",
    actual: str = "",
) -> None:
    """Append an issue to the issues.json file."""
    entry = {
        "test_name": test_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "category": category,
        "description": description,
        "expected": expected,
        "actual": actual,
    }
    issues = []
    if ISSUES_JSON.exists():
        try:
            issues = json.loads(ISSUES_JSON.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            issues = []
    issues.append(entry)
    ISSUES_JSON.write_text(json.dumps(issues, indent=2), encoding="utf-8")


def generate_issues_md() -> None:
    """Generate ISSUES.md from issues.json."""
    if not ISSUES_JSON.exists():
        ISSUES_MD.write_text(
            "# Test Issues Log\n\nNo issues found.\n", encoding="utf-8"
        )
        return

    issues = json.loads(ISSUES_JSON.read_text(encoding="utf-8"))
    if not issues:
        ISSUES_MD.write_text(
            "# Test Issues Log\n\nNo issues found.\n", encoding="utf-8"
        )
        return

    lines = [
        "# Test Issues Log",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Total issues:** {len(issues)}",
        "",
        "| Severity | Category | Test | Description | Expected | Actual |",
        "|----------|----------|------|-------------|----------|--------|",
    ]
    for issue in issues:
        lines.append(
            f"| {issue['severity']} | {issue['category']} | "
            f"`{issue['test_name']}` | {issue['description']} | "
            f"{issue['expected']} | {issue['actual']} |"
        )
    ISSUES_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
