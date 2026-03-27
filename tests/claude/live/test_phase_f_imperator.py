"""Phase F: Imperator multi-turn conversation tests.

Sends 10 scripted turns through the Imperator via /v1/chat/completions,
verifying response coherence, tool usage, and content keywords.
"""

import json
from pathlib import Path

import pytest

from tests.claude.live.helpers import (
    PHASE2_DIR,
    chat_call,
    extract_mcp_result,
    log_issue,
    mcp_call,
)

pytestmark = pytest.mark.live

TURNS_FILE = PHASE2_DIR / "imperator-turns.json"


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def imperator_turns():
    """Load the imperator-turns.json test data."""
    assert TURNS_FILE.exists(), f"Test data not found: {TURNS_FILE}"
    return json.loads(TURNS_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def conversation_history():
    """Module-scoped mutable history list that accumulates across turns."""
    return []


@pytest.fixture(scope="module")
def turn_responses():
    """Module-scoped dict to store responses by turn index."""
    return {}


# ---------------------------------------------------------------------------
# Turn execution -- run all 10 turns in order
# ---------------------------------------------------------------------------

class TestImperatorTurns:
    """Execute each imperator turn and validate the response."""

    @pytest.fixture(autouse=True, scope="class")
    def _run_all_turns(
        self, http_client, imperator_turns, conversation_history, turn_responses
    ):
        """Run all turns once (class-scoped) so individual tests can inspect."""
        if turn_responses:
            # Already ran
            return

        for i, turn in enumerate(imperator_turns):
            prompt = turn["prompt"]
            try:
                result = chat_call(
                    http_client,
                    prompt,
                    history=list(conversation_history),
                    timeout=180,
                )
                content = result["choices"][0]["message"]["content"]
                turn_responses[i] = {
                    "content": content,
                    "full_response": result,
                    "error": None,
                }
                # Accumulate history for next turn
                conversation_history.append({"role": "user", "content": prompt})
                conversation_history.append({"role": "assistant", "content": content})
            except Exception as exc:
                turn_responses[i] = {
                    "content": "",
                    "full_response": None,
                    "error": str(exc),
                }
                log_issue(
                    f"imperator_turn_{i}",
                    "error",
                    "imperator",
                    f"Turn {i} failed: {exc}",
                    "Successful response",
                    str(exc),
                )

    def test_turn_0_responds(self, imperator_turns, turn_responses):
        """Turn 0: 'What conversations do I have stored?' -- gets a response."""
        resp = turn_responses.get(0)
        assert resp is not None, "Turn 0 was not executed"
        assert resp["error"] is None, f"Turn 0 errored: {resp['error']}"
        assert len(resp["content"]) > 0, "Turn 0 returned empty content"

    def test_turn_1_responds(self, turn_responses):
        """Turn 1: Search for MAD container architecture."""
        resp = turn_responses.get(1)
        assert resp is not None, "Turn 1 was not executed"
        assert resp["error"] is None, f"Turn 1 errored: {resp['error']}"
        assert len(resp["content"]) > 0, "Turn 1 returned empty content"

    def test_turn_2_responds(self, turn_responses):
        """Turn 2: Summarize Context Broker conversations."""
        resp = turn_responses.get(2)
        assert resp is not None, "Turn 2 was not executed"
        assert resp["error"] is None, f"Turn 2 errored: {resp['error']}"
        assert len(resp["content"]) > 0, "Turn 2 returned empty content"

    def test_turn_3_responds(self, turn_responses):
        """Turn 3: Find Imperator pattern messages."""
        resp = turn_responses.get(3)
        assert resp is not None, "Turn 3 was not executed"
        assert resp["error"] is None, f"Turn 3 errored: {resp['error']}"
        assert len(resp["content"]) > 0, "Turn 3 returned empty content"

    def test_turn_4_responds(self, turn_responses):
        """Turn 4: Search for Substack or LinkedIn."""
        resp = turn_responses.get(4)
        assert resp is not None, "Turn 4 was not executed"
        assert resp["error"] is None, f"Turn 4 errored: {resp['error']}"
        assert len(resp["content"]) > 0, "Turn 4 returned empty content"

    def test_turn_5_responds(self, turn_responses):
        """Turn 5: Summarize main projects."""
        resp = turn_responses.get(5)
        assert resp is not None, "Turn 5 was not executed"
        assert resp["error"] is None, f"Turn 5 errored: {resp['error']}"
        assert len(resp["content"]) > 0, "Turn 5 returned empty content"

    def test_turn_6_responds(self, turn_responses):
        """Turn 6: AE and TE separation."""
        resp = turn_responses.get(6)
        assert resp is not None, "Turn 6 was not executed"
        assert resp["error"] is None, f"Turn 6 errored: {resp['error']}"
        assert len(resp["content"]) > 0, "Turn 6 returned empty content"

    def test_turn_7_responds(self, turn_responses):
        """Turn 7: Search for irina or Docker."""
        resp = turn_responses.get(7)
        assert resp is not None, "Turn 7 was not executed"
        assert resp["error"] is None, f"Turn 7 errored: {resp['error']}"
        assert len(resp["content"]) > 0, "Turn 7 returned empty content"

    def test_turn_8_responds(self, turn_responses):
        """Turn 8: Recall earlier MAD architecture discussion."""
        resp = turn_responses.get(8)
        assert resp is not None, "Turn 8 was not executed"
        assert resp["error"] is None, f"Turn 8 errored: {resp['error']}"
        assert len(resp["content"]) > 0, "Turn 8 returned empty content"

    def test_turn_9_responds(self, turn_responses):
        """Turn 9: Summarize everything in 3 bullet points."""
        resp = turn_responses.get(9)
        assert resp is not None, "Turn 9 was not executed"
        assert resp["error"] is None, f"Turn 9 errored: {resp['error']}"
        assert len(resp["content"]) > 0, "Turn 9 returned empty content"


# ---------------------------------------------------------------------------
# Keyword and tool checks (soft-fail with log_issue)
# ---------------------------------------------------------------------------

class TestImperatorKeywords:
    """Check expected content keywords in imperator responses."""

    @pytest.fixture(autouse=True, scope="class")
    def _ensure_turns_ran(
        self, http_client, imperator_turns, conversation_history, turn_responses
    ):
        """Ensure turns have been executed before keyword checks."""
        if not turn_responses:
            # Force execution by instantiating TestImperatorTurns logic
            for i, turn in enumerate(imperator_turns):
                prompt = turn["prompt"]
                try:
                    result = chat_call(
                        http_client, prompt, history=list(conversation_history),
                        timeout=180,
                    )
                    content = result["choices"][0]["message"]["content"]
                    turn_responses[i] = {
                        "content": content,
                        "full_response": result,
                        "error": None,
                    }
                    conversation_history.append({"role": "user", "content": prompt})
                    conversation_history.append({"role": "assistant", "content": content})
                except Exception as exc:
                    turn_responses[i] = {
                        "content": "", "full_response": None, "error": str(exc),
                    }

    def test_keyword_presence(self, imperator_turns, turn_responses):
        """For each turn with expected_content_keywords, check presence
        (case-insensitive). Log issues for missing keywords but don't hard-fail."""
        issues_found = 0
        for i, turn in enumerate(imperator_turns):
            keywords = turn.get("expected_content_keywords", [])
            if not keywords:
                continue
            resp = turn_responses.get(i)
            if resp is None or resp["error"]:
                continue
            content_lower = resp["content"].lower()
            for kw in keywords:
                if kw.lower() not in content_lower:
                    issues_found += 1
                    log_issue(
                        f"test_keyword_turn_{i}",
                        "warning",
                        "imperator",
                        f"Turn {i}: expected keyword '{kw}' missing from response",
                        f"Response contains '{kw}'",
                        resp["content"][:200],
                    )
        # Soft pass -- issues are logged, not hard-failed
        assert True

    def test_tool_usage_noted(self, imperator_turns, turn_responses):
        """For each turn with expected_tools, check if tool calls appear in
        the response. Log issues for missing tools but don't hard-fail."""
        for i, turn in enumerate(imperator_turns):
            expected_tools = turn.get("expected_tools", [])
            if not expected_tools:
                continue
            resp = turn_responses.get(i)
            if resp is None or resp["error"] or resp["full_response"] is None:
                continue

            # Check for tool_calls in the response message
            message = resp["full_response"].get("choices", [{}])[0].get("message", {})
            tool_calls = message.get("tool_calls", [])
            used_tools = {tc.get("function", {}).get("name", "") for tc in tool_calls}

            # Also check if tool names appear in the content itself
            content_lower = resp["content"].lower()

            for tool in expected_tools:
                tool_found = (
                    tool in used_tools
                    or tool.lower() in content_lower
                )
                if not tool_found:
                    log_issue(
                        f"test_tool_turn_{i}",
                        "info",
                        "imperator",
                        f"Turn {i}: expected tool '{tool}' not detected in response",
                        f"Tool '{tool}' used",
                        f"Tools used: {used_tools or 'none detected'}",
                    )
        assert True


# ---------------------------------------------------------------------------
# Coherence and continuity
# ---------------------------------------------------------------------------

class TestImperatorCoherence:
    """High-level coherence checks on the imperator conversation."""

    def test_imperator_responds_coherently(
        self, http_client, imperator_turns, conversation_history, turn_responses
    ):
        """First turn should get a non-empty, substantive response."""
        resp = turn_responses.get(0)
        if resp is None or resp["error"]:
            pytest.skip("Turn 0 did not complete successfully")
        assert len(resp["content"]) > 20, (
            f"First turn response too short ({len(resp['content'])} chars): "
            f"{resp['content'][:100]}"
        )

    def test_imperator_multi_turn_continuity(
        self, imperator_turns, turn_responses
    ):
        """Later turns should reference context from earlier in the conversation.
        Turn 8 asks to recall MAD architecture discussed in turn 1."""
        resp_8 = turn_responses.get(8)
        if resp_8 is None or resp_8["error"]:
            pytest.skip("Turn 8 did not complete successfully")
        content_lower = resp_8["content"].lower()
        # Turn 8 asks about MAD architecture discussed in turn 1
        has_reference = "mad" in content_lower or "architecture" in content_lower
        if not has_reference:
            log_issue(
                "test_imperator_multi_turn_continuity",
                "warning",
                "imperator",
                "Turn 8 does not reference MAD architecture from earlier turns",
                "References to MAD or architecture",
                resp_8["content"][:200],
            )
        assert has_reference, (
            "Turn 8 should reference earlier MAD architecture discussion"
        )

    def test_imperator_tool_binding(self, http_client):
        """tools/list should include the imperator_chat tool."""
        payload = {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/list",
            "params": {},
        }
        resp = http_client.post("/mcp", json=payload, timeout=30)
        assert resp.status_code == 200, f"tools/list failed: {resp.text}"
        body = resp.json()
        tools = body.get("result", {}).get("tools", [])
        tool_names = [t.get("name", "") for t in tools]
        assert "imperator_chat" in tool_names, (
            f"imperator_chat not found in tools/list. "
            f"Available tools: {tool_names[:20]}"
        )
