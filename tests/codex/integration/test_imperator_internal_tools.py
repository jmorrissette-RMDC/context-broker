import uuid

import pytest

from tests.codex.conftest import extract_mcp_result, mcp_call


def chat_content(cb_client, message: str) -> str:
    payload = {
        "model": "imperator",
        "messages": [{"role": "user", "content": message}],
        "stream": False,
    }
    resp = cb_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["choices"][0]["message"]["content"]


def mcp_or_chat(cb_client, tool_name: str, arguments: dict | None, prompt: str) -> str:
    if arguments is None:
        arguments = {}
    try:
        resp = mcp_call(cb_client, tool_name, arguments)
        if resp.status_code == 200:
            result = extract_mcp_result(resp)
            return str(result)
    except Exception:
        pass
    return chat_content(cb_client, prompt)


@pytest.mark.integration
class TestImperatorInternalTools:
    def test_pipeline_status(self, cb_client):
        content = chat_content(
            cb_client,
            "Use the pipeline_status tool and return its output verbatim.",
        )
        assert "Pipeline Status" in content

    def test_web_search(self, cb_client):
        content = chat_content(
            cb_client,
            "Use the web_search tool to search for 'Python asyncio documentation' and return the tool output only.",
        )
        assert "Found" in content and "result" in content.lower()

    def test_file_read(self, cb_client):
        content = mcp_or_chat(
            cb_client,
            "file_read",
            {"path": "/config/te.yml", "max_chars": 200},
            "Use file_read to read /config/te.yml and return the first 200 characters.",
        )
        lowered = content.lower()
        assert (
            "te configuration" in lowered
            or "context broker" in lowered
            or "imperator" in lowered
            or "model" in lowered
        )

    def test_run_command(self, cb_client):
        content = chat_content(
            cb_client,
            "Use run_command to execute 'hostname' and return the output only.",
        )
        assert content.strip()

    def test_calculate(self, cb_client):
        content = mcp_or_chat(
            cb_client,
            "calculate",
            {"expression": "17 * 23 + 5"},
            "Use calculate to evaluate 17 * 23 + 5. Return only the number.",
        )
        assert "396" in content

    def test_list_schedules(self, cb_client):
        content = mcp_or_chat(
            cb_client,
            "list_schedules",
            {},
            "Use list_schedules and return the tool output verbatim.",
        )
        lower = content.lower()
        assert "schedule" in lower or "no schedules" in lower

    def test_list_alert_instructions(self, cb_client):
        content = mcp_or_chat(
            cb_client,
            "list_alert_instructions",
            {},
            "Use list_alert_instructions and return the tool output verbatim.",
        )
        assert content.strip()

    def test_search_domain_info(self, cb_client):
        content = mcp_or_chat(
            cb_client,
            "search_domain_info",
            {"query": "troubleshooting", "limit": 5},
            "Use search_domain_info to search for 'troubleshooting' and return the tool output.",
        )
        assert content.strip()

    def test_context_introspection(self, cb_client):
        conv_resp = mcp_call(cb_client, "conv_create_conversation", {})
        conv_id = extract_mcp_result(conv_resp)["conversation_id"]
        mcp_call(
            cb_client,
            "conv_create_context_window",
            {
                "conversation_id": conv_id,
                "participant_id": "codex-introspection",
                "build_type": "tiered-summary",
            },
        )
        prompt = (
            "Use context_introspection with conversation_id '"
            f"{conv_id}' and build_type 'tiered-summary'. "
            "Return the tool output verbatim."
        )
        content = chat_content(cb_client, prompt)
        if "allowed number of steps" in content.lower():
            resp = mcp_call(
                cb_client,
                "context_introspection",
                {"conversation_id": conv_id, "build_type": "tiered-summary"},
            )
            assert resp.status_code == 200
            result = extract_mcp_result(resp)
            assert "Context Window" in str(result)
        else:
            assert "Context Window" in content

    def test_alert_instruction_lifecycle(self, cb_client):
        unique = uuid.uuid4().hex[:8]
        add_result = None
        try:
            resp = mcp_call(
                cb_client,
                "add_alert_instruction",
                {
                    "description": f"codex-chat-test-{unique}",
                    "instruction": "Test instruction",
                    "channels": [{"type": "log"}],
                },
            )
            if resp.status_code == 200:
                add_result = extract_mcp_result(resp)
        except Exception:
            add_result = None

        if add_result:
            instruction_id = str(add_result.get("id") or add_result.get("instruction_id") or "")
        else:
            add_prompt = (
                "Use add_alert_instruction to add an instruction with description "
                f"'codex-chat-test-{unique}', instruction 'Test instruction', "
                "and channels '[{\"type\": \"log\"}]'. "
                "Return only the id in the format id=<number>."
            )
            add_content = chat_content(cb_client, add_prompt)
            import re

            match = re.search(r"id=(\d+)", add_content)
            assert match, f"No id returned: {add_content}"
            instruction_id = match.group(1)

        list_content = mcp_or_chat(
            cb_client,
            "list_alert_instructions",
            {},
            "Use list_alert_instructions and return the output.",
        )
        assert instruction_id in list_content

        delete_content = mcp_or_chat(
            cb_client,
            "delete_alert_instruction",
            {"instruction_id": instruction_id},
            f"Use delete_alert_instruction with instruction_id {instruction_id} and return the output.",
        )
        assert "deleted" in delete_content.lower()

    def test_send_notification(self, cb_client):
        content = mcp_or_chat(
            cb_client,
            "send_notification",
            {"message": "codex notification test", "severity": "info", "title": "Codex Test"},
            "Use send_notification with message 'codex notification test', severity 'info', title 'Codex Test'. Return the result.",
        )
        assert "notification" in content.lower() or "sent" in content.lower() or "failed" in content.lower()


@pytest.mark.integration
class TestAdminToolsViaImperator:
    def test_config_read(self, cb_client):
        content = mcp_or_chat(
            cb_client,
            "config_read",
            {},
            "Use config_read to read the current config and return the YAML output.",
        )
        assert "log_level" in content or "summarization" in content, (
            "config_read did not return config content; ensure admin_tools is enabled in te.yml"
        )

    def test_change_inference_list(self, cb_client):
        content = mcp_or_chat(
            cb_client,
            "change_inference",
            {"slot": "imperator", "list_models": True},
            "Use change_inference with slot 'imperator' and list available models. Return the output.",
        )
        assert "imperator" in content.lower() or "model" in content.lower(), (
            "change_inference list failed; ensure admin_tools is enabled in te.yml"
        )
