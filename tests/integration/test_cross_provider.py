"""
Cross-provider full pipeline tests — State 4 validation.

Tests the FULL Context Broker pipeline through each provider:
configure CB via hot-reload → store_message → wait for embedding →
get_context → search_messages → verify results.

Replaces the old test_cross_provider.py which only tested direct
API calls to providers, bypassing the Context Broker entirely.

Usage:
    python tests/integration/test_cross_provider.py
    python tests/integration/test_cross_provider.py openai   # run one provider
"""

import json
import subprocess
import sys
import time

import httpx

from config import CB_MCP_URL, MCP_CALL_TIMEOUT_SECONDS

SSH_TARGET = "aristotle9@192.168.1.110"
CONFIG_PATH = "/mnt/storage/projects/portfolio/ContextBroker/config/config.yml"
CREDS_PATH = "/mnt/storage/projects/portfolio/ContextBroker/config/credentials/.env"

# Provider configurations for embeddings + LLM
# Each provider must support both embeddings and chat completions
PROVIDERS = {
    "google": {
        "embeddings": {
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-embedding-001",
            "api_key_env": "GOOGLE_API_KEY",
            "embedding_dims": 3072,
        },
        "summarization": {
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-2.5-flash",
            "api_key_env": "GOOGLE_API_KEY",
        },
    },
    "openai": {
        "embeddings": {
            "base_url": "https://api.openai.com/v1",
            "model": "text-embedding-3-small",
            "api_key_env": "OPENAI_API_KEY",
            "embedding_dims": 1536,
        },
        "summarization": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
        },
    },
    # Together skipped: no serverless embedding models on this account (PG-14)
    # xAI does not offer embedding models via the API
    "ollama": {
        "embeddings": {
            "base_url": "http://context-broker-ollama:11434/v1",
            "model": "nomic-embed-text",
            "api_key_env": "",
            "embedding_dims": 768,
        },
        "summarization": {
            "base_url": "http://context-broker-ollama:11434/v1",
            "model": "qwen2.5:7b",
            "api_key_env": "",
        },
    },
}


def ssh_cmd(cmd: str, timeout: int = 30) -> str:
    result = subprocess.run(
        ["ssh", SSH_TARGET, cmd], capture_output=True, text=True, timeout=timeout
    )
    return result.stdout.strip()


def mcp_call(
    tool_name: str, arguments: dict, timeout: int = MCP_CALL_TIMEOUT_SECONDS
) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
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


def reset_for_provider():
    """Clear messages and embeddings between provider runs."""
    ssh_cmd(
        "docker exec context-broker-postgres psql -U context_broker -d context_broker "
        '-c "TRUNCATE conversation_messages, conversation_summaries, context_windows CASCADE"'
    )
    ssh_cmd("docker exec context-broker-redis redis-cli FLUSHDB")


def set_provider_config(provider_name: str, config: dict):
    """Hot-reload embeddings and summarization config on irina via SSH.

    Writes a Python script that reads config.yml, updates the embeddings
    and summarization sections, and writes it back. The Context Broker's
    mtime-based cache detects the change on next operation.
    """
    emb = config["embeddings"]
    summ = config["summarization"]

    # Build the Python script to update config.yml
    script = f"""
import yaml
with open('{CONFIG_PATH}') as f:
    cfg = yaml.safe_load(f)
cfg['embeddings'] = {{
    'base_url': '{emb["base_url"]}',
    'model': '{emb["model"]}',
    'api_key_env': '{emb["api_key_env"]}',
    'embedding_dims': {emb.get("embedding_dims", 3072)},
    'tiktoken_enabled': False,
    'check_embedding_ctx_length': False,
}}
cfg['summarization'] = {{
    'base_url': '{summ["base_url"]}',
    'model': '{summ["model"]}',
    'api_key_env': '{summ["api_key_env"]}',
}}
with open('{CONFIG_PATH}', 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False)
print('Config updated for {provider_name}')
"""
    ssh_cmd(f'python3 -c "{script.strip()}"')


def run_provider_test(provider_name: str, config: dict) -> tuple[bool, str]:
    """Run the full pipeline test for one provider.

    Returns (passed, detail).
    """
    print(f"\n--- {provider_name} ---")

    # Step 1: Reset data
    print("  Resetting data...")
    reset_for_provider()

    # Step 2: Switch config to this provider (hot-reload)
    print(f"  Switching config to {provider_name}...")
    set_provider_config(provider_name, config)
    time.sleep(2)  # Brief pause for mtime cache to detect change

    # Step 3: Store messages through MCP
    print("  Storing messages...")
    try:
        conv = mcp_call(
            "conv_create_conversation",
            {
                "title": f"cross-provider-{provider_name}",
                "flow_id": "cross-provider-test",
                "user_id": "test-runner",
            },
        )
        conv_id = conv["conversation_id"]
    except (httpx.HTTPError, RuntimeError) as exc:
        return False, f"Failed to create conversation: {exc}"

    test_content = f"Cross-provider test with {provider_name}: The Joshua26 ecosystem uses MAD architecture."
    try:
        for i in range(3):
            mcp_call(
                "store_message",
                {
                    "conversation_id": conv_id,
                    "role": "user" if i % 2 == 0 else "assistant",
                    "content": f"{test_content} Message {i}.",
                    "sender": "test-runner" if i % 2 == 0 else "assistant",
                },
            )
    except (httpx.HTTPError, RuntimeError) as exc:
        return False, f"Failed to store messages: {exc}"

    # Step 4: Wait for embeddings
    print("  Waiting for embeddings...")
    for attempt in range(15):
        time.sleep(2)
        count = ssh_cmd(
            "docker exec context-broker-postgres psql -U context_broker -d context_broker "
            '-t -c "SELECT COUNT(*) FROM conversation_messages WHERE embedding IS NOT NULL"'
        ).strip()
        embedded = int(count or "0")
        if embedded >= 3:
            break
    else:
        # Check for errors
        errors = ssh_cmd(
            "docker logs context-broker-langgraph 2>&1 | grep 'Embedding.*failed\\|error.*embed' | tail -2"
        )
        return False, f"Only {embedded}/3 embeddings after 30s. Errors: {errors}"

    print(f"  Embeddings: {embedded}/3")

    # Step 5: get_context through MCP
    print("  Getting context...")
    try:
        ctx = mcp_call(
            "get_context",
            {
                "build_type": "passthrough",
                "budget": 4096,
                "conversation_id": conv_id,
            },
        )
        if not ctx.get("context"):
            return False, f"get_context returned empty context: {ctx}"
    except (httpx.HTTPError, RuntimeError) as exc:
        return False, f"get_context failed: {exc}"

    # Step 6: search_messages through MCP
    print("  Searching messages...")
    try:
        search = mcp_call("search_messages", {"query": "MAD architecture"})
        if not search.get("messages"):
            return False, "search_messages returned no results"
    except (httpx.HTTPError, RuntimeError) as exc:
        return False, f"search_messages failed: {exc}"

    return True, f"Stored 3, embedded {embedded}, context OK, search OK"


def main():
    provider_filter = sys.argv[1].lower() if len(sys.argv) > 1 else None

    if provider_filter and provider_filter in PROVIDERS:
        providers_to_test = {provider_filter: PROVIDERS[provider_filter]}
    else:
        providers_to_test = PROVIDERS

    print("=== Cross-Provider Full Pipeline Tests ===")
    print(f"Target: {CB_MCP_URL}")
    print(f"Providers: {', '.join(providers_to_test.keys())}")

    results = {}
    for name, config in providers_to_test.items():
        passed, detail = run_provider_test(name, config)
        results[name] = (passed, detail)
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}: {detail}")

    # Restore Google config (our standard test config)
    if "google" in PROVIDERS:
        set_provider_config("google", PROVIDERS["google"])

    print("\n=== Results ===")
    passed = sum(1 for p, _ in results.values() if p)
    total = len(results)
    print(f"Passed: {passed}/{total}")

    for name, (p, detail) in results.items():
        status = "PASS" if p else "FAIL"
        print(f"  [{status}] {name}: {detail}")

    if passed < total:
        print(f"\nFAILED: {total - passed} provider(s)")
        sys.exit(1)
    else:
        print("\nALL PROVIDERS PASSED — State 4 validated")


if __name__ == "__main__":
    main()
