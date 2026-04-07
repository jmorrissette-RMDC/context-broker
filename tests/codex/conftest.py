import json
import os
import uuid
from pathlib import Path

import httpx
import pytest

try:
    from tests.integration import config as integration_config
except Exception:
    integration_config = None

def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value

DEFAULT_BASE_URL = (
    integration_config.CB_BASE_URL
    if integration_config and hasattr(integration_config, "CB_BASE_URL")
    else None
)
DEFAULT_DATA_DIR = (
    integration_config.TEST_DATA_DIR
    if integration_config and hasattr(integration_config, "TEST_DATA_DIR")
    else None
)
DEFAULT_Z_DRIVE_DIR = Path(r"Z:\test-data\conversational-memory")

BASE_URL = _env("CB_BASE_URL", DEFAULT_BASE_URL or "http://localhost:8080")
MCP_URL = _env("CB_MCP_URL", f"{BASE_URL}/mcp")
CHAT_URL = _env("CB_CHAT_URL", f"{BASE_URL}/v1/chat/completions")
HEALTH_URL = _env("CB_HEALTH_URL", f"{BASE_URL}/health")

TEST_DATA_DIR = None
if DEFAULT_DATA_DIR is not None:
    TEST_DATA_DIR = Path(str(_env("TEST_DATA_DIR", str(DEFAULT_DATA_DIR))))
else:
    env_data_dir = _env("TEST_DATA_DIR", None)
    if env_data_dir:
        TEST_DATA_DIR = Path(env_data_dir)
    elif DEFAULT_Z_DRIVE_DIR.exists():
        TEST_DATA_DIR = DEFAULT_Z_DRIVE_DIR


@pytest.fixture(scope="session")
def cb_base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def cb_client(cb_base_url: str):
    timeout = float(_env("CB_CLIENT_TIMEOUT", "180"))
    with httpx.Client(base_url=cb_base_url, timeout=timeout) as client:
        try:
            resp = client.get("/health")
        except httpx.HTTPError as exc:
            pytest.fail(
                f"Context Broker not reachable at {cb_base_url}: {exc}"
            )
        if resp.status_code != 200:
            pytest.fail(
                f"Context Broker /health failed ({resp.status_code}): {resp.text}"
            )
        yield client


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    if TEST_DATA_DIR is None:
        pytest.fail(
            "TEST_DATA_DIR not set and Z:\\test-data\\conversational-memory not found."
        )
    if not TEST_DATA_DIR.exists():
        pytest.fail(f"TEST_DATA_DIR does not exist: {TEST_DATA_DIR}")
    return TEST_DATA_DIR


@pytest.fixture(scope="session")
def phase1_dir(test_data_dir: Path) -> Path:
    phase1 = test_data_dir / "phase1-bulk-load"
    if not phase1.exists():
        pytest.fail(f"Missing phase1-bulk-load directory: {phase1}")
    return phase1


@pytest.fixture(scope="session")
def sample_phase1_messages(phase1_dir: Path):
    files = sorted(phase1_dir.glob("conversation-*.json"))
    if not files:
        pytest.fail(f"No conversation-*.json files found in {phase1_dir}")
    data = json.loads(files[0].read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        pytest.fail(f"Invalid or empty conversation data in {files[0]}")
    return data


def mcp_call_raw(client: httpx.Client, payload: dict) -> httpx.Response:
    return client.post("/mcp", json=payload)


def mcp_call(client: httpx.Client, tool_name: str, arguments: dict) -> httpx.Response:
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    return mcp_call_raw(client, payload)


def extract_mcp_result(response: httpx.Response):
    body = response.json()
    if "error" in body:
        raise AssertionError(f"MCP error: {body['error']}")

    result = body.get("result")
    if isinstance(result, dict) and "content" in result:
        content = result.get("content") or []
        if content and isinstance(content, list) and "text" in content[0]:
            text = content[0]["text"]
            try:
                return json.loads(text)
            except (TypeError, json.JSONDecodeError):
                return text
    return result


def wait_for_condition(check_fn, timeout_seconds: float = 30.0, interval: float = 1.0):
    import time

    end = time.time() + timeout_seconds
    last = None
    while time.time() < end:
        last = check_fn()
        if last:
            return True
        time.sleep(interval)
    return False
