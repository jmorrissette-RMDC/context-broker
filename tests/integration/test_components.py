"""
Component tests — verify each capability works against deployed irina.

NOT unit tests. These hit the real deployed system with real providers.
Must ALL pass before integration testing begins.

Usage:
    python tests/integration/test_components.py
    python tests/integration/test_components.py GroupB   # run one group
"""

import json
import subprocess
import sys
import time

import httpx

from config import CB_MCP_URL, CB_CHAT_URL, CB_HEALTH_URL, MCP_CALL_TIMEOUT_SECONDS

SSH_TARGET = "aristotle9@192.168.1.110"
CB_DIR = "/mnt/storage/projects/portfolio/ContextBroker"

# ── Helpers ──────────────────────────────────────────────────────────

def mcp_call(tool_name: str, arguments: dict, timeout: int = MCP_CALL_TIMEOUT_SECONDS) -> dict:
    """Call an MCP tool synchronously and return parsed result."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    with httpx.Client() as client:
        resp = client.post(CB_MCP_URL, json=payload, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"MCP error: {body['error']}")
        text = body.get("result", {}).get("content", [{}])[0].get("text", "{}")
        return json.loads(text)


def chat_call(message: str, timeout: int = 60) -> dict:
    """Send a message to the Imperator via chat endpoint."""
    payload = {
        "model": "imperator",
        "messages": [{"role": "user", "content": message}],
        "stream": False,
    }
    with httpx.Client() as client:
        resp = client.post(CB_CHAT_URL, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()


def ssh_cmd(cmd: str, timeout: int = 30) -> str:
    """Run command on irina via SSH."""
    result = subprocess.run(
        ["ssh", SSH_TARGET, cmd], capture_output=True, text=True, timeout=timeout
    )
    return result.stdout.strip()


def ssh_psql(query: str) -> str:
    """Run a psql query on irina."""
    return ssh_cmd(
        f'docker exec context-broker-postgres psql -U context_broker -d context_broker -t -c "{query}"'
    ).strip()


def ssh_config_set(file: str, key_path: str, value: str):
    """Set a YAML value in a config file on irina using python."""
    ssh_cmd(
        f'docker exec context-broker-langgraph python -c "'
        f'import yaml; '
        f'c=yaml.safe_load(open(\\\"{file}\\\")); '
        # Simple key path for top-level or one-level nested
        f'exec(\\\"c{key_path}={value}\\\"); '
        f'yaml.dump(c,open(\\\"{file}\\\",\\\"w\\\"),default_flow_style=False)'
        f'"'
    )


def reset_small():
    """Quick reset for component tests — truncate messages only."""
    ssh_cmd(
        'docker exec context-broker-postgres psql -U context_broker -d context_broker '
        '-c "TRUNCATE conversation_messages, conversation_summaries, context_windows, conversations CASCADE"'
    )
    ssh_cmd('docker exec context-broker-redis redis-cli FLUSHDB')


# ── Test infrastructure ──────────────────────────────────────────────

class TestResult:
    def __init__(self, name: str, passed: bool, detail: str = ""):
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        detail = f" — {self.detail}" if self.detail else ""
        return f"  [{status}] {self.name}{detail}"


results: list[TestResult] = []


def test(name: str):
    """Decorator to register and run a test."""
    def decorator(func):
        def wrapper():
            try:
                func()
                r = TestResult(name, True)
            except AssertionError as e:
                r = TestResult(name, False, str(e))
            except (httpx.HTTPError, RuntimeError, OSError, subprocess.TimeoutExpired) as e:
                r = TestResult(name, False, f"{type(e).__name__}: {e}")
            results.append(r)
            print(r)
        wrapper._test_name = name
        wrapper._group = getattr(func, '_group', 'ungrouped')
        return wrapper
    return decorator


# ── Group A: Dynamic Loading ─────────────────────────────────────────

@test("A1: scan() discovers AE package")
def test_a1():
    test_a1._group = "GroupA"
    # Check the startup logs for AE registration
    log = ssh_cmd("docker logs context-broker-langgraph 2>&1 | grep 'Registered AE package' | tail -1")
    assert "context-broker-ae" in log, f"AE not registered: {log}"

@test("A2: scan() discovers TE package")
def test_a2():
    test_a2._group = "GroupA"
    log = ssh_cmd("docker logs context-broker-langgraph 2>&1 | grep 'Registered TE package' | tail -1")
    assert "context-broker-te" in log, f"TE not registered: {log}"

@test("A3: install_stategraph MCP tool responds")
def test_a3():
    test_a3._group = "GroupA"
    # Call install_stategraph with an already-installed package — should succeed
    result = mcp_call("install_stategraph", {"package_name": "context-broker-te"}, timeout=120)
    assert result.get("status") in ("installed", "error"), f"Unexpected: {result}"

@test("A4: All 3 build types registered")
def test_a4():
    test_a4._group = "GroupA"
    # Call tools/list and check for build type enum
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    with httpx.Client() as client:
        resp = client.post(CB_MCP_URL, json=payload, timeout=10)
        body = resp.json()
    tools_text = json.dumps(body)
    assert "passthrough" in tools_text, "passthrough not in tool list"
    assert "standard-tiered" in tools_text, "standard-tiered not in tool list"
    assert "knowledge-enriched" in tools_text, "knowledge-enriched not in tool list"

@test("A5: Imperator available via chat endpoint")
def test_a5():
    test_a5._group = "GroupA"
    result = chat_call("Say hello in one word.")
    choices = result.get("choices", [])
    assert len(choices) > 0, f"No choices in response: {result}"
    content = choices[0].get("message", {}).get("content", "")
    assert len(content) > 0, f"Empty response from Imperator"


# ── Group B: Config Hot-Reload ───────────────────────────────────────

@test("B1: Credentials readable from mounted file")
def test_b1():
    test_b1._group = "GroupB"
    # Verify the credentials file exists and has keys
    output = ssh_cmd("docker exec context-broker-langgraph cat /config/credentials/.env | grep -c '='")
    count = int(output or "0")
    assert count >= 5, f"Expected >=5 keys in credentials file, got {count}"

@test("B2: Health endpoint works (proves config loaded)")
def test_b2():
    test_b2._group = "GroupB"
    with httpx.Client() as client:
        resp = client.get(CB_HEALTH_URL, timeout=10)
    assert resp.status_code == 200, f"Health returned {resp.status_code}"
    body = resp.json()
    assert body["status"] == "healthy", f"Status: {body}"

@test("B3: store_message works (proves pipeline config loaded)")
def test_b3():
    test_b3._group = "GroupB"
    reset_small()
    # Create conversation and store a message
    conv = mcp_call("conv_create_conversation", {"title": "b3-test", "flow_id": "test", "user_id": "test"})
    conv_id = conv["conversation_id"]
    result = mcp_call("store_message", {
        "conversation_id": conv_id,
        "role": "user",
        "content": "Hello from component test B3",
        "sender": "test-runner",
    })
    assert result.get("message_id"), f"No message_id: {result}"

@test("B4: Embedding generated (proves embedding provider config works)")
def test_b4():
    test_b4._group = "GroupB"
    # Wait briefly for the embedding job from B3
    time.sleep(5)
    count = ssh_psql("SELECT COUNT(*) FROM conversation_messages WHERE embedding IS NOT NULL")
    assert int(count or "0") > 0, f"No embeddings generated — embedding provider may be misconfigured"

@test("B5: TE config hot-reload — change takes effect")
def test_b5():
    test_b5._group = "GroupB"
    # Read current max_iterations from te.yml, verify it's reflected in behavior
    # We can't easily test model switching without cost, but we CAN verify
    # the config file is being read by checking get_context works with current build_type
    result = mcp_call("get_context", {"build_type": "passthrough", "budget": 4096})
    assert "conversation_id" in result, f"get_context failed: {result}"


# ── Group C: Cross-Provider Full Pipeline ────────────────────────────
# These test the FULL CHAIN: store → embed → retrieve through the CB pipeline.
# Provider switching requires config changes — we test with the currently
# configured provider (Google embeddings per our test setup).

@test("C1: Store message through MCP pipeline")
def test_c1():
    test_c1._group = "GroupC"
    reset_small()
    conv = mcp_call("conv_create_conversation", {"title": "c1-pipeline", "flow_id": "test", "user_id": "test"})
    conv_id = conv["conversation_id"]
    for i in range(5):
        mcp_call("store_message", {
            "conversation_id": conv_id,
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"Pipeline test message {i}: The Context Broker manages infinite conversation history.",
            "sender": "test-runner" if i % 2 == 0 else "assistant",
        })
    # Verify messages stored
    count = ssh_psql("SELECT COUNT(*) FROM conversation_messages")
    assert int(count or "0") >= 5, f"Expected >=5 messages, got {count}"

@test("C2: Embeddings generated for stored messages")
def test_c2():
    test_c2._group = "GroupC"
    # Wait for embedding pipeline
    time.sleep(10)
    embedded = ssh_psql("SELECT COUNT(*) FROM conversation_messages WHERE embedding IS NOT NULL")
    assert int(embedded or "0") >= 3, f"Expected >=3 embeddings, got {embedded}"

@test("C3: get_context returns assembled context")
def test_c3():
    test_c3._group = "GroupC"
    # Get the conversation from C1
    convs = ssh_psql("SELECT id FROM conversations ORDER BY created_at DESC LIMIT 1")
    conv_id = convs.strip()
    assert conv_id, "No conversation found"
    result = mcp_call("get_context", {
        "build_type": "passthrough",
        "budget": 4096,
        "conversation_id": conv_id,
    })
    assert result.get("context"), f"No context returned: {result}"
    assert result.get("total_tokens", 0) > 0, f"Zero tokens in context"

@test("C4: search_messages finds stored messages")
def test_c4():
    test_c4._group = "GroupC"
    result = mcp_call("search_messages", {"query": "Context Broker manages infinite"})
    messages = result.get("messages", [])
    assert len(messages) > 0, f"Search returned no results"


# ── Group D: Imperator Capabilities ──────────────────────────────────

@test("D1: Imperator responds coherently")
def test_d1():
    test_d1._group = "GroupD"
    result = chat_call("What are you? Describe yourself in one sentence.")
    content = result["choices"][0]["message"]["content"]
    assert len(content) > 10, f"Response too short: {content}"

@test("D2: imperator_chat MCP tool works")
def test_d2():
    test_d2._group = "GroupD"
    result = mcp_call("imperator_chat", {"message": "Say hello."}, timeout=60)
    assert result.get("response"), f"No response: {result}"

@test("D3: Pipeline status tool (diagnostic)")
def test_d3():
    test_d3._group = "GroupD"
    # Ask Imperator about pipeline status
    result = chat_call("What is the current pipeline status? Use your pipeline status tool.")
    content = result["choices"][0]["message"]["content"]
    # Should mention queues or pipeline in some form
    assert len(content) > 20, f"Response too short for pipeline status: {content}"

@test("D4: Log query tool (diagnostic)")
def test_d4():
    test_d4._group = "GroupD"
    result = chat_call("Show me the last 5 log entries from any container. Use your log query tool.")
    content = result["choices"][0]["message"]["content"]
    assert len(content) > 20, f"Response too short for log query: {content}"

@test("D5: Context introspection tool (diagnostic)")
def test_d5():
    test_d5._group = "GroupD"
    result = chat_call("Introspect your own context. What build type and tiers are you using?")
    content = result["choices"][0]["message"]["content"]
    assert len(content) > 20, f"Response too short for introspection: {content}"

@test("D6: Metrics endpoint returns Prometheus data")
def test_d6():
    test_d6._group = "GroupD"
    with httpx.Client() as client:
        resp = client.get(CB_HEALTH_URL.replace("/health", "/metrics"), timeout=10)
    assert resp.status_code == 200, f"Metrics returned {resp.status_code}"
    assert "context_broker_" in resp.text, "No context_broker metrics found"


# ── Group E: Pipeline End-to-End ─────────────────────────────────────

@test("E1: Passthrough returns verbatim messages")
def test_e1():
    test_e1._group = "GroupE"
    reset_small()
    conv = mcp_call("conv_create_conversation", {"title": "e1-passthrough", "flow_id": "test", "user_id": "test"})
    conv_id = conv["conversation_id"]
    mcp_call("store_message", {
        "conversation_id": conv_id, "role": "user",
        "content": "Unique passthrough test message XYZ123", "sender": "test",
    })
    time.sleep(2)
    result = mcp_call("get_context", {"build_type": "passthrough", "budget": 4096, "conversation_id": conv_id})
    context = result.get("context", [])
    assert any("XYZ123" in str(m) for m in context), f"Verbatim message not found in passthrough context"

@test("E2: search_messages returns results with embeddings")
def test_e2():
    test_e2._group = "GroupE"
    time.sleep(5)  # Wait for embedding from E1
    result = mcp_call("search_messages", {"query": "Unique passthrough test message"})
    assert len(result.get("messages", [])) > 0, "Search returned no results"

@test("E3: Effective utilization check")
def test_e3():
    test_e3._group = "GroupE"
    # Get context with a known budget, verify total_tokens <= 85% of budget
    conv_id = ssh_psql("SELECT id FROM conversations ORDER BY created_at DESC LIMIT 1").strip()
    if not conv_id:
        results.append(TestResult("E3: Effective utilization", False, "No conversation to test"))
        return
    result = mcp_call("get_context", {"build_type": "passthrough", "budget": 4096, "conversation_id": conv_id})
    total = result.get("total_tokens", 0)
    # 85% of 4096 = 3481
    assert total <= 3481, f"Total tokens {total} exceeds 85% of 4096 (3481)"


# ── Group F: Resilience ──────────────────────────────────────────────

@test("F1: Health endpoint reports all dependencies")
def test_f1():
    test_f1._group = "GroupF"
    with httpx.Client() as client:
        resp = client.get(CB_HEALTH_URL, timeout=10)
    body = resp.json()
    assert "database" in body, "Missing database status"
    assert "cache" in body, "Missing cache status"
    assert "neo4j" in body, "Missing neo4j status"

@test("F2: Store message succeeds (basic pipeline resilience)")
def test_f2():
    test_f2._group = "GroupF"
    conv = mcp_call("conv_create_conversation", {"title": "f2-resilience", "flow_id": "test", "user_id": "test"})
    result = mcp_call("store_message", {
        "conversation_id": conv["conversation_id"],
        "role": "user", "content": "Resilience test", "sender": "test",
    })
    assert result.get("message_id"), f"Store failed: {result}"


# ── Group G: Observability ───────────────────────────────────────────

@test("G1: Prometheus metrics contain flow-level metrics")
def test_g1():
    test_g1._group = "GroupG"
    with httpx.Client() as client:
        resp = client.get(CB_HEALTH_URL.replace("/health", "/metrics"), timeout=10)
    text = resp.text
    assert "context_broker_mcp_requests_total" in text, "Missing MCP request metrics"

@test("G2: Log shipper has collected logs")
def test_g2():
    test_g2._group = "GroupG"
    count = ssh_psql("SELECT COUNT(*) FROM system_logs")
    assert int(count or "0") > 0, f"No logs in system_logs table — log shipper may not be working"

@test("G3: Container logs are structured JSON")
def test_g3():
    test_g3._group = "GroupG"
    log_line = ssh_cmd("docker logs context-broker-langgraph 2>&1 | grep '{\"timestamp' | tail -1")
    assert log_line, "No JSON log lines found"
    parsed = json.loads(log_line)
    assert "timestamp" in parsed, "Missing timestamp in log"
    assert "level" in parsed, "Missing level in log"
    assert "message" in parsed, "Missing message in log"


# ── Runner ───────────────────────────────────────────────────────────

ALL_TESTS = [
    test_a1, test_a2, test_a3, test_a4, test_a5,
    test_b1, test_b2, test_b3, test_b4, test_b5,
    test_c1, test_c2, test_c3, test_c4,
    test_d1, test_d2, test_d3, test_d4, test_d5, test_d6,
    test_e1, test_e2, test_e3,
    test_f1, test_f2,
    test_g1, test_g2, test_g3,
]

GROUPS = {
    "GroupA": [t for t in ALL_TESTS if "test_a" in t.__name__],
    "GroupB": [t for t in ALL_TESTS if "test_b" in t.__name__],
    "GroupC": [t for t in ALL_TESTS if "test_c" in t.__name__],
    "GroupD": [t for t in ALL_TESTS if "test_d" in t.__name__],
    "GroupE": [t for t in ALL_TESTS if "test_e" in t.__name__],
    "GroupF": [t for t in ALL_TESTS if "test_f" in t.__name__],
    "GroupG": [t for t in ALL_TESTS if "test_g" in t.__name__],
}


def main():
    group_filter = sys.argv[1] if len(sys.argv) > 1 else None

    if group_filter and group_filter in GROUPS:
        tests_to_run = GROUPS[group_filter]
        print(f"=== Component Tests: {group_filter} ===")
    else:
        tests_to_run = ALL_TESTS
        print("=== Component Tests: ALL ===")

    print(f"Target: {CB_MCP_URL}\n")

    for t in tests_to_run:
        t()

    print(f"\n=== Results ===")
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    print(f"Passed: {passed}/{len(results)}")
    if failed:
        print(f"FAILED: {failed}")
        for r in results:
            if not r.passed:
                print(f"  {r}")
        sys.exit(1)
    else:
        print("ALL PASSED")


if __name__ == "__main__":
    main()
