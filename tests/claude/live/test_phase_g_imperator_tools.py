"""Phase G: Imperator tool integration tests.

Tests the Imperator's diagnostic, search, and admin tools using Phase 3
synthetic test data prompts. Each test sends a prompt via chat_call() and
verifies the response contains expected keywords.
"""

import json
import pytest

from tests.claude.live.helpers import (
    PHASE3_DIR,
    chat_call,
    log_issue,
)

pytestmark = pytest.mark.live

# ---------------------------------------------------------------------------
# Load Phase 3 test data
# ---------------------------------------------------------------------------

_diagnostic_prompts = json.loads(
    (PHASE3_DIR / "diagnostic-prompts.json").read_text(encoding="utf-8")
)
_search_prompts = json.loads(
    (PHASE3_DIR / "search-prompts.json").read_text(encoding="utf-8")
)
_admin_prompts = json.loads(
    (PHASE3_DIR / "admin-prompts.json").read_text(encoding="utf-8")
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_and_check(http_client, prompt_entry: dict, test_name: str) -> None:
    """Send a prompt via chat_call and verify expected keywords in response."""
    prompt_text = prompt_entry["prompt"]
    keywords = prompt_entry["expected_content_keywords"]

    result = chat_call(http_client, prompt_text)

    # Extract response content
    choices = result.get("choices", [])
    assert choices, f"{test_name}: response has no choices"
    content = choices[0].get("message", {}).get("content", "")
    assert content, f"{test_name}: response content is empty"

    content_lower = content.lower()
    for keyword in keywords:
        if keyword.lower() not in content_lower:
            log_issue(
                test_name,
                "warning",
                "imperator-tool",
                f"Expected keyword '{keyword}' not found in response",
                keyword,
                content[:200],
            )


# ---------------------------------------------------------------------------
# Diagnostic tool tests
# ---------------------------------------------------------------------------

class TestDiagnosticTools:
    """Tests for Imperator diagnostic tools."""

    def test_diagnostic_log_query(self, http_client):
        """Send diagnostic prompt [0] — log query tool."""
        _send_and_check(http_client, _diagnostic_prompts[0], "test_diagnostic_log_query")

    def test_diagnostic_context_introspection(self, http_client):
        """Send diagnostic prompt [1] — context introspection tool."""
        _send_and_check(
            http_client, _diagnostic_prompts[1], "test_diagnostic_context_introspection"
        )

    def test_diagnostic_pipeline_status(self, http_client):
        """Send diagnostic prompt [2] — pipeline status tool."""
        _send_and_check(
            http_client, _diagnostic_prompts[2], "test_diagnostic_pipeline_status"
        )


# ---------------------------------------------------------------------------
# Search tool tests
# ---------------------------------------------------------------------------

class TestSearchTools:
    """Tests for Imperator search tools."""

    def test_search_joshua26(self, http_client):
        """Send search prompt [0] — search for Joshua26 system overview."""
        _send_and_check(http_client, _search_prompts[0], "test_search_joshua26")

    def test_search_docker_health(self, http_client):
        """Send search prompt [1] — search for Docker healthchecks."""
        _send_and_check(http_client, _search_prompts[1], "test_search_docker_health")

    def test_search_imperator_design(self, http_client):
        """Send search prompt [2] — search for Imperator design pattern."""
        _send_and_check(http_client, _search_prompts[2], "test_search_imperator_design")

    def test_search_knowledge_base(self, http_client):
        """Send search prompt [3] — search knowledge base for MADs."""
        _send_and_check(http_client, _search_prompts[3], "test_search_knowledge_base")


# ---------------------------------------------------------------------------
# Admin tool tests
# ---------------------------------------------------------------------------

class TestAdminTools:
    """Tests for Imperator admin tools (requires_admin=true, already enabled)."""

    def test_admin_config_read(self, http_client):
        """Send admin prompt [0] — read config for summarization model."""
        _send_and_check(http_client, _admin_prompts[0], "test_admin_config_read")

    def test_admin_verbose_toggle(self, http_client):
        """Send admin prompt [1] — toggle verbose logging off."""
        _send_and_check(http_client, _admin_prompts[1], "test_admin_verbose_toggle")
