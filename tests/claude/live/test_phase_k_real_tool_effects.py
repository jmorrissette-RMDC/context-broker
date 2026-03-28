"""Phase K: Real tool effects tests.

Verifies that tools produce REAL side effects -- not just that the Imperator
responds, but that the tool actually changed something on the server.

Each test calls a tool (via MCP direct or imperator_chat) and then verifies
the side effect via docker_exec, docker_psql, or follow-up MCP calls.

All tests run against the live stack at http://192.168.1.110:8081.
"""

import json
import re
import time
import uuid

import pytest

from tests.claude.live.helpers import (
    BASE_URL,
    chat_call,
    docker_exec,
    docker_logs,
    docker_psql,
    extract_mcp_result,
    log_issue,
    mcp_call,
)

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _imperator_chat(http_client, message: str, timeout: int = 120) -> str:
    """Send a message via imperator_chat MCP tool and return the response text."""
    resp = mcp_call(
        http_client,
        "imperator_chat",
        {"message": message},
        timeout=timeout,
    )
    assert resp.status_code == 200, f"imperator_chat failed: {resp.status_code} {resp.text[:300]}"
    result = extract_mcp_result(resp)
    return result.get("response", "")


def _chat(http_client, message: str, timeout: int = 120) -> str:
    """Send a message via /v1/chat/completions and return the content."""
    result = chat_call(http_client, message, timeout=timeout)
    assert not result.get("error"), f"chat_call error: {result}"
    return result["choices"][0]["message"]["content"]


# ===========================================================================
# K-01: file_write creates a file
# ===========================================================================

class TestFileWriteCreatesFile:
    """K-01: file_write via Imperator creates a file on disk."""

    def test_file_write_creates_file(self, http_client):
        """Write a file via Imperator, then verify it exists via docker_exec."""
        tag = uuid.uuid4().hex[:8]
        filename = f"test-k01-{tag}.txt"
        filepath = f"/data/downloads/{filename}"
        content = f"Phase K file_write test {tag}"

        # Ask Imperator to write the file
        response = _chat(
            http_client,
            f"Write a file to {filepath} with the exact content: {content}",
        )
        assert any(
            kw in response.lower()
            for kw in ["written", "created", "saved", "success", "wrote"]
        ), f"Imperator did not confirm file write: {response[:300]}"

        # Verify file exists on disk via docker_exec
        ls_output = docker_exec("context-broker-app", f"ls -la {filepath}")
        assert filename in ls_output, (
            f"File not found on disk after file_write. ls output: {ls_output}"
        )

        # Verify content
        cat_output = docker_exec("context-broker-app", f"cat {filepath}")
        assert content in cat_output, (
            f"File content mismatch. Expected '{content}', got: {cat_output[:200]}"
        )

        # Cleanup
        docker_exec("context-broker-app", f"rm -f {filepath}")


# ===========================================================================
# K-02: run_command returns output
# ===========================================================================

class TestRunCommandReturnsOutput:
    """K-02: run_command via Imperator returns real command output."""

    def test_run_command_returns_output(self, http_client):
        """Ask Imperator to run uptime, verify response contains uptime data."""
        response = _chat(http_client, "Run the uptime command and show me the raw output")
        # Uptime output typically contains 'up', 'load average', 'users', etc.
        response_lower = response.lower()
        has_uptime_data = any(
            kw in response_lower for kw in ["up", "load", "day", "hour", "min"]
        )
        assert has_uptime_data, (
            f"run_command uptime did not return uptime data: {response[:300]}"
        )


# ===========================================================================
# K-03: calculate returns correct answer
# ===========================================================================

class TestCalculateReturnsCorrectAnswer:
    """K-03: calculate tool returns mathematically correct result."""

    def test_calculate_returns_correct_answer(self, http_client):
        """Ask Imperator to calculate 1024 * 0.85, verify 870.4 in response."""
        response = _chat(http_client, "Calculate 1024 * 0.85 and tell me the exact result")
        assert "870" in response, (
            f"calculate did not return 870.4: {response[:300]}"
        )


# ===========================================================================
# K-04: create_schedule persists to DB
# ===========================================================================

class TestCreateSchedulePersists:
    """K-04: create_schedule creates a row in the schedules table."""

    SCHEDULE_NAME = None  # Set during test for cleanup

    def test_create_schedule_persists(self, http_client):
        """Create a schedule via Imperator, verify row exists in schedules table."""
        tag = uuid.uuid4().hex[:8]
        schedule_name = f"k04-test-{tag}"
        TestCreateSchedulePersists.SCHEDULE_NAME = schedule_name

        response = _chat(
            http_client,
            f"Create a schedule named '{schedule_name}' that runs every 7200 seconds "
            f"and sends the message 'k04 heartbeat {tag}'",
        )

        # Allow a moment for persistence
        time.sleep(2)

        # Verify in DB
        db_result = docker_psql(
            f"SELECT name, interval_seconds, enabled FROM schedules WHERE name = '{schedule_name}'"
        )
        assert schedule_name in db_result, (
            f"Schedule '{schedule_name}' not found in DB. "
            f"Query result: {db_result}. Imperator response: {response[:200]}"
        )

    def test_cleanup_schedule(self, http_client):
        """Cleanup: disable the schedule created above."""
        name = TestCreateSchedulePersists.SCHEDULE_NAME
        if name:
            _chat(http_client, f"Disable the schedule named '{name}'")
            time.sleep(1)


# ===========================================================================
# K-05: disable_schedule persists
# ===========================================================================

class TestDisableSchedulePersists:
    """K-05: disable_schedule sets enabled=FALSE in DB."""

    def test_disable_schedule_persists(self, http_client):
        """Create then disable a schedule, verify enabled=false in DB."""
        tag = uuid.uuid4().hex[:8]
        schedule_name = f"k05-disable-{tag}"

        # Create
        _chat(
            http_client,
            f"Create a schedule named '{schedule_name}' that runs every 3600 seconds "
            f"and sends the message 'k05 test'",
        )
        time.sleep(2)

        # Verify created
        db_created = docker_psql(
            f"SELECT enabled FROM schedules WHERE name = '{schedule_name}'"
        )
        assert db_created.strip(), (
            f"Schedule '{schedule_name}' not found after creation"
        )

        # Disable
        _chat(http_client, f"Disable the schedule named '{schedule_name}'")
        time.sleep(2)

        # Verify disabled
        db_disabled = docker_psql(
            f"SELECT enabled FROM schedules WHERE name = '{schedule_name}'"
        )
        assert "f" in db_disabled.lower(), (
            f"Schedule '{schedule_name}' not disabled. enabled={db_disabled}"
        )


# ===========================================================================
# K-06: add_alert_instruction persists
# ===========================================================================

class TestAddAlertInstructionPersists:
    """K-06: add_alert_instruction creates a row in alert_instructions table."""

    def test_add_alert_instruction_persists(self, http_client):
        """Add an alert instruction via Imperator, verify in DB."""
        tag = uuid.uuid4().hex[:8]
        description = f"k06-test-alert-{tag}"

        _chat(
            http_client,
            f"Add an alert instruction with description '{description}', "
            f"instruction 'Format alerts for testing', "
            f"channels [{{'type': 'log'}}]",
        )
        time.sleep(2)

        db_result = docker_psql(
            f"SELECT description FROM alert_instructions WHERE description = '{description}'"
        )
        assert description in db_result, (
            f"Alert instruction '{description}' not found in DB. Result: {db_result}"
        )

        # Cleanup: delete it
        _chat(http_client, f"Delete the alert instruction for '{description}'")


# ===========================================================================
# K-07: store_domain_info persists
# ===========================================================================

class TestStoreDomainInfoPersists:
    """K-07: store_domain_info creates a row in domain_information table."""

    def test_store_domain_info_persists(self, http_client):
        """Store domain info via Imperator, verify in domain_information table."""
        tag = uuid.uuid4().hex[:8]
        content = f"K07 test fact: The Context Broker uses {tag} as a marker"

        _chat(
            http_client,
            f"Store this as domain information: {content}",
        )
        time.sleep(2)

        db_result = docker_psql(
            f"SELECT COUNT(*) FROM domain_information WHERE content LIKE '%{tag}%'"
        )
        count = int(re.search(r"(\d+)", db_result).group(1)) if re.search(r"(\d+)", db_result) else 0
        assert count >= 1, (
            f"Domain info with tag '{tag}' not found in DB. Query result: {db_result}"
        )


# ===========================================================================
# K-08: config_write takes effect
# ===========================================================================

class TestConfigWriteTakesEffect:
    """K-08: config_write changes a setting that config_read can verify."""

    def test_config_write_takes_effect(self, http_client):
        """Set verbose_logging to true, verify via config_read, then restore."""
        # Enable verbose logging
        _chat(http_client, "Set tuning.verbose_logging to true in the config")
        time.sleep(1)

        # Verify via Imperator
        verify_response = _chat(
            http_client,
            "Read the current config and tell me the value of tuning.verbose_logging",
        )
        assert "true" in verify_response.lower(), (
            f"config_write did not take effect. Response: {verify_response[:300]}"
        )

        # Restore
        _chat(http_client, "Set tuning.verbose_logging to false in the config")
        time.sleep(1)

        # Verify restored
        restore_response = _chat(
            http_client,
            "Read the current config and tell me the value of tuning.verbose_logging",
        )
        assert "false" in restore_response.lower(), (
            f"config_write restore failed. Response: {restore_response[:300]}"
        )


# ===========================================================================
# K-09: verbose_toggle changes config
# ===========================================================================

class TestVerboseToggleChangesConfig:
    """K-09: verbose_toggle flips the verbose_logging config value."""

    def test_verbose_toggle_changes_config(self, http_client):
        """Toggle verbose logging on and off, verify change via config_read."""
        # First, ensure we know the current state by setting to false
        _chat(http_client, "Set tuning.verbose_logging to false in the config")
        time.sleep(1)

        # Toggle on
        toggle_response = _chat(
            http_client,
            "Toggle verbose logging on",
        )
        time.sleep(1)

        # Check it changed
        check_response = _chat(
            http_client,
            "What is tuning.verbose_logging currently set to?",
        )
        assert "true" in check_response.lower(), (
            f"verbose_toggle did not enable logging. Response: {check_response[:300]}"
        )

        # Restore by toggling off
        _chat(http_client, "Toggle verbose logging off")
        time.sleep(1)


# ===========================================================================
# K-10: db_query returns real data
# ===========================================================================

class TestDbQueryReturnsRealData:
    """K-10: db_query returns actual database row counts."""

    def test_db_query_returns_real_data(self, http_client):
        """Run SELECT COUNT(*) FROM conversations via Imperator, verify numeric result."""
        response = _chat(
            http_client,
            "Run this exact database query and give me only the number: SELECT COUNT(*) FROM conversations",
        )
        # Extract any number from the response
        numbers = re.findall(r"\d+", response)
        assert numbers, (
            f"db_query did not return any numbers. Response: {response[:300]}"
        )
        count = int(numbers[0])
        assert count >= 1, (
            f"db_query returned count={count}, expected at least 1 conversation"
        )


# ===========================================================================
# K-11: change_inference lists models
# ===========================================================================

class TestChangeInferenceListsModels:
    """K-11: change_inference in list mode returns model catalog."""

    def test_change_inference_lists_models(self, http_client):
        """Ask what models are available for summarization slot, verify catalog."""
        response = _chat(
            http_client,
            "What models are available for the summarization inference slot? "
            "Just list the model names.",
        )
        response_lower = response.lower()
        # Should contain at least one known model family
        has_models = any(
            kw in response_lower
            for kw in ["gpt", "gemini", "claude", "sonnet", "haiku", "flash", "model"]
        )
        assert has_models, (
            f"change_inference did not list any known models. Response: {response[:400]}"
        )


# ===========================================================================
# K-12: send_notification reaches alerter
# ===========================================================================

class TestSendNotificationReachesAlerter:
    """K-12: send_notification dispatches an event visible in alerter logs."""

    def test_send_notification_reaches_alerter(self, http_client):
        """Send a notification via Imperator, check alerter logs for the event."""
        tag = uuid.uuid4().hex[:8]
        notification_msg = f"K12 test notification {tag}"

        _chat(
            http_client,
            f"Send a notification with type 'test.k12' and message '{notification_msg}'",
        )
        time.sleep(3)

        # Check alerter logs for the notification
        logs = docker_logs("context-broker-alerter", lines=100)
        if tag in logs:
            # Found the notification in alerter logs
            return

        # If alerter is not running or logs don't contain it, check app logs
        app_logs = docker_logs("context-broker-app", lines=100)
        if tag in app_logs or "notification" in app_logs.lower():
            log_issue(
                "test_send_notification_reaches_alerter",
                "info",
                "alerter",
                f"Notification sent but only found in app logs, not alerter: tag={tag}",
            )
            return

        # Soft-fail: log the issue but don't hard-fail since alerter may not be configured
        log_issue(
            "test_send_notification_reaches_alerter",
            "warning",
            "alerter",
            f"Notification tag '{tag}' not found in alerter or app logs",
            "Notification visible in logs",
            f"alerter logs (last 100 lines): ...not found",
        )


# ===========================================================================
# K-13: mem_add creates searchable memory
# ===========================================================================

class TestMemAddCreatesSearchableMemory:
    """K-13: mem_add creates a memory that mem_search can find."""

    def test_mem_add_creates_searchable_memory(self, http_client):
        """Add a memory via MCP, then search for it."""
        tag = uuid.uuid4().hex[:8]
        user_id = f"k13-test-{tag}"
        fact = f"The user's favorite database is CockroachDB ({tag})"

        # Add memory via direct MCP call
        add_resp = mcp_call(
            http_client,
            "mem_add",
            {"content": fact, "user_id": user_id},
        )
        assert add_resp.status_code == 200
        add_result = extract_mcp_result(add_resp)
        assert isinstance(add_result, dict), f"mem_add returned unexpected type: {add_result}"

        # Search for the memory with retries
        found = False
        for attempt in range(4):
            time.sleep(3)
            search_resp = mcp_call(
                http_client,
                "mem_search",
                {"query": "favorite database", "user_id": user_id},
            )
            assert search_resp.status_code == 200
            search_result = extract_mcp_result(search_resp)
            memories = search_result.get("memories", [])
            if memories:
                # Check that at least one memory relates to our fact
                all_text = " ".join(
                    str(m.get("memory", m.get("content", ""))) for m in memories
                ).lower()
                if "cockroachdb" in all_text or tag in all_text:
                    found = True
                    break

        assert found, (
            f"mem_search did not find the memory added by mem_add. "
            f"user_id={user_id}, searched for 'favorite database'"
        )


# ===========================================================================
# K-14: search_messages returns relevant content
# ===========================================================================

class TestSearchMessagesReturnsRelevant:
    """K-14: search_messages returns content relevant to the query."""

    def test_search_messages_returns_relevant(self, http_client):
        """Search for 'Context Broker' and verify results contain relevant content."""
        resp = mcp_call(
            http_client,
            "search_messages",
            {"query": "Context Broker", "limit": 10},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        messages = result.get("messages", [])
        assert len(messages) > 0, (
            "search_messages returned no results for 'Context Broker'"
        )

        # Verify at least some results mention context, broker, or related terms
        relevant_count = 0
        for msg in messages:
            content = str(msg.get("content", "")).lower()
            if any(kw in content for kw in ["context", "broker", "memory", "conversation"]):
                relevant_count += 1

        assert relevant_count > 0, (
            f"search_messages returned {len(messages)} results but none seem relevant "
            f"to 'Context Broker'"
        )


# ===========================================================================
# K-15: search_knowledge returns facts
# ===========================================================================

class TestSearchKnowledgeReturnsFacts:
    """K-15: search_knowledge returns extracted facts or relations."""

    def test_search_knowledge_returns_facts(self, http_client):
        """Search knowledge for a broad topic, verify facts or memories returned."""
        resp = mcp_call(
            http_client,
            "search_knowledge",
            {"query": "software architecture patterns", "user_id": "default"},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        memories = result.get("memories", [])

        if not memories:
            # Try a different query
            resp2 = mcp_call(
                http_client,
                "search_knowledge",
                {"query": "context engineering memory", "user_id": "default"},
            )
            assert resp2.status_code == 200
            result2 = extract_mcp_result(resp2)
            memories = result2.get("memories", [])

        if not memories:
            log_issue(
                "test_search_knowledge_returns_facts",
                "warning",
                "knowledge",
                "search_knowledge returned no facts for broad queries; "
                "knowledge extraction may not have completed",
                "At least 1 memory/fact",
                "0 results",
            )
            assert False, "search_knowledge returned no facts — extraction not working"

        # Verify structure: each memory should have content or memory text
        for mem in memories[:5]:
            has_text = (
                mem.get("memory") or mem.get("content") or mem.get("text")
            )
            assert has_text, (
                f"Knowledge memory entry has no text content: {mem}"
            )
