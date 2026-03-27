"""Imperator Chat UI — Gradio-based multi-MAD client.

Custom gr.Blocks layout per REQ-optional-gradio-chat-ui:
  - MAD selector with health indicators
  - Chat panel with streaming responses
  - Conversation sidebar (list, create, select, delete)
  - Info panel (model, build type, budget/utilization, health)
  - Artifacts panel (rendered code/markdown from responses)
  - Log viewer
"""

import logging
import os
import re

import gradio as gr
import httpx
import yaml

from mad_client import MADClient

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("ui")


# ── Config ───────────────────────────────────────────────────────────


def load_config() -> dict:
    config_path = os.environ.get("CONFIG_PATH", "/app/config.yml")
    if not os.path.exists(config_path):
        config_path = os.path.join(os.path.dirname(__file__), "config.yml")
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


CONFIG = load_config()
MADS = {
    m["name"]: MADClient(m["name"], m["url"], m.get("hostname", ""))
    for m in CONFIG.get("mads", [])
}


# ── Artifacts extraction ─────────────────────────────────────────────

_CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)


def extract_artifacts(text: str) -> str:
    """Extract code blocks and formatted content from assistant response."""
    blocks = _CODE_BLOCK_RE.findall(text)
    if not blocks:
        return ""
    parts = []
    for lang, code in blocks:
        lang = lang or "text"
        parts.append(f"**{lang}:**\n```{lang}\n{code.strip()}\n```")
    return "\n\n".join(parts)


# ── Event handlers ───────────────────────────────────────────────────


async def check_all_health():
    """Check health of all MADs and return status string."""
    parts = []
    for name, client in MADS.items():
        health = await client.health()
        status = health.get("status", "unknown")
        indicator = {"healthy": "\u2705", "degraded": "\u26a0\ufe0f"}.get(
            status, "\u274c"
        )
        parts.append(f"{indicator} {name}")
    return " | ".join(parts) if parts else "No MADs configured"


async def on_mad_selected(mad_name):
    """Handle MAD selection — refresh conversations, health, clear chat."""
    client = MADS.get(mad_name)
    if not client:
        return gr.update(choices=[], value=None), "No MAD selected", "", "", []

    choices = await _get_conversation_choices(client)
    health_text = await _get_health_text(client)
    return gr.update(choices=choices, value=None), health_text, "", "", []


async def on_conversation_selected(conv_choice, mad_name):
    """Load conversation history into chat."""
    _log.info("on_conversation_selected: choice=%s mad=%s", conv_choice, mad_name)
    if not conv_choice or not mad_name:
        return [], "", ""

    client = MADS.get(mad_name)
    if not client:
        return [], "", ""

    # Parse conversation ID from "title (N msgs) | full-uuid" format
    conv_id = ""
    if "|" in conv_choice:
        conv_id = conv_choice.split("|")[-1].strip()
    if not conv_id:
        return [], "", ""

    # Load history
    messages = await client.get_history(conv_id)
    _log.info("Loaded %d messages for conv %s", len(messages), conv_id[:8])
    chat_history = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            chat_history.append({"role": "user", "content": content})
        elif role == "assistant" and content:
            chat_history.append({"role": "assistant", "content": content})

    # Get context info
    info_text = await _get_context_info_text(client, conv_id)

    # Extract artifacts from last assistant message
    artifacts = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            artifacts = extract_artifacts(msg["content"])
            break

    return chat_history, info_text, artifacts


async def on_create_conversation(title, mad_name):
    """Create a new conversation and add it to the dropdown.

    New conversations have no messages yet, so the participant filter
    won't find them. We add it to the dropdown manually.
    """
    client = MADS.get(mad_name)
    if not client:
        return gr.update(choices=[], value=None), ""

    display_title = title or "New Conversation"
    result = await client.create_conversation(display_title)
    conv_id = result.get("conversation_id", "")

    existing = await _get_conversation_choices(client)
    new_label = f"{display_title} (0 msgs) | {conv_id}"
    choices = [new_label] + existing
    return gr.update(choices=choices, value=new_label), ""


async def on_delete_conversation(conv_choice, mad_name):
    """Delete a conversation and refresh the list."""
    client = MADS.get(mad_name)
    if not client or not conv_choice:
        return gr.update(choices=[], value=None)

    # Parse UUID from dropdown choice string
    conv_id = ""
    if "|" in conv_choice:
        conv_id = conv_choice.split("|")[-1].strip()
    if not conv_id:
        return gr.update(choices=[], value=None)

    await client.delete_conversation(conv_id)
    choices = await _get_conversation_choices(client)
    return gr.update(choices=choices, value=None)


async def on_chat_submit(message, history, mad_name, conv_choice):
    """Handle chat message with streaming. Refreshes conversation list after."""
    client = MADS.get(mad_name)
    if not client:
        history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": "No MAD selected"},
        ]
        yield history, "", gr.update()
        return

    # Parse full conversation ID from dropdown choice string
    resolved_conv_id = None
    if conv_choice and "|" in conv_choice:
        resolved_conv_id = conv_choice.split("|")[-1].strip()

    history = history + [{"role": "user", "content": message}]
    yield history, "", gr.update()

    # Build OpenAI messages from history
    api_messages = [{"role": m["role"], "content": m["content"]} for m in history]

    # Stream response
    response = ""
    try:
        async for chunk in client.chat_stream(
            api_messages, conversation_id=resolved_conv_id, user="gradio-ui"
        ):
            response += chunk
            updated = history + [{"role": "assistant", "content": response}]
            yield updated, extract_artifacts(response), gr.update()
    except (httpx.HTTPError, RuntimeError, OSError) as exc:
        response = f"Error: {exc}"
        updated = history + [{"role": "assistant", "content": response}]
        yield updated, "", gr.update()

    # Refresh conversation list — new messages may make conversations visible
    if client:
        choices = await _get_conversation_choices(client)
        yield (
            history + [{"role": "assistant", "content": response}],
            extract_artifacts(response),
            gr.update(choices=choices),
        )


async def on_refresh_logs(mad_name):
    """Refresh log viewer."""
    client = MADS.get(mad_name)
    if not client:
        return "No MAD selected"
    try:
        entries = await client.query_logs(limit=40)
        if not entries:
            return "No log entries"
        lines = []
        for e in entries:
            ts = (e.get("timestamp") or "?")[-8:]
            lvl = e.get("level", "?")
            msg = e.get("message", "")[:120]
            lines.append(f"[{ts}] [{lvl}] {msg}")
        return "\n".join(lines)
    except (RuntimeError, OSError):
        return "Failed to load logs"


# ── Helpers ──────────────────────────────────────────────────────────


async def _get_conversation_choices(client: MADClient) -> list[str]:
    """Get conversation list as string choices.

    Format: "title (N msgs) | full-uuid"
    The full UUID is used so we can parse it directly without lookup.
    """
    try:
        convs = await client.list_conversations()
        choices = []
        for c in convs:
            title = c.get("title", "Untitled")[:35]
            count = c.get("message_count", 0)
            choices.append(f"{title} ({count} msgs) | {c['id']}")
        return choices
    except (RuntimeError, OSError):
        return []


async def _get_health_text(client: MADClient) -> str:
    """Get formatted health text for a MAD."""
    health = await client.health()
    lines = [f"Status: {health.get('status', 'unknown')}"]
    for key, val in health.items():
        if key != "status":
            lines.append(f"  {key}: {val}")
    return "\n".join(lines)


async def _get_context_info_text(client: MADClient, conv_id: str) -> str:
    """Get formatted context info for a conversation."""
    try:
        result = await client.get_context_info(conv_id)
        windows = result.get("context_windows", [])
        if not windows:
            return "No context windows"
        lines = []
        for w in windows:
            lines.append(f"Build: {w.get('build_type', '?')}")
            lines.append(f"Budget: {w.get('max_token_budget', '?')} tokens")
            lines.append(f"Assembled: {w.get('last_assembled_at', 'never')}")
        return "\n".join(lines)
    except (RuntimeError, OSError):
        return "Context info unavailable"


# ── Pre-load initial data ─────────────────────────────────────────────


def _sync_load_conversations() -> list[tuple[str, str]]:
    """Load initial conversations synchronously at build time."""
    if not MADS:
        return []
    client = list(MADS.values())[0]
    try:
        import json

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "conv_list_conversations",
                "arguments": {"participant": client.hostname, "limit": 50},
            },
        }
        resp = httpx.post(f"{client.base_url}/mcp", json=payload, timeout=10)
        body = resp.json()
        text = body.get("result", {}).get("content", [{}])[0].get("text", "{}")
        data = json.loads(text)
        choices = []
        for c in data.get("conversations", []):
            title = c.get("title", "Untitled")[:35]
            count = c.get("message_count", 0)
            # Use "label | id" format — parse ID on selection
            choices.append(f"{title} ({count} msgs) | {c['id']}")
        _log.info("Pre-loaded %d conversations", len(choices))
        return choices
    except Exception as exc:
        _log.warning("Failed to pre-load conversations: %s", exc)
        return []


_initial_conversations = _sync_load_conversations()

# ── Build the UI ─────────────────────────────────────────────────────

default_mad = list(MADS.keys())[0] if MADS else ""

with gr.Blocks(title="Imperator Chat", theme=gr.themes.Soft()) as demo:
    # State
    current_mad = gr.State(default_mad)
    current_conv = gr.State("")

    gr.Markdown("# Imperator Chat")
    health_bar = gr.Markdown("")

    with gr.Row():
        # ── Left sidebar: MAD selector + conversations ──────────
        with gr.Column(scale=1, min_width=250):
            mad_selector = gr.Dropdown(
                choices=list(MADS.keys()),
                value=default_mad,
                label="Select MAD",
            )

            gr.Markdown("### Conversations")
            conv_dropdown = gr.Dropdown(
                choices=_initial_conversations,
                value=_initial_conversations[0] if _initial_conversations else None,
                label="Conversation",
            )
            with gr.Row():
                new_title = gr.Textbox(
                    placeholder="Title...", show_label=False, scale=3
                )
                create_btn = gr.Button("New", scale=1, size="sm")
            delete_btn = gr.Button("Delete Selected", size="sm", variant="stop")

            gr.Markdown("### Health")
            health_detail = gr.Textbox(lines=4, interactive=False, show_label=False)

        # ── Center: chat ────────────────────────────────────────
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(type="messages", height=500)
            with gr.Row():
                msg_input = gr.Textbox(
                    placeholder="Message the Imperator...",
                    show_label=False,
                    scale=6,
                )
                send_btn = gr.Button("Send", scale=1, variant="primary")

        # ── Right sidebar: info + artifacts + logs ──────────────
        with gr.Column(scale=1, min_width=250):
            gr.Markdown("### Context Info")
            info_panel = gr.Textbox(lines=4, interactive=False, show_label=False)

            gr.Markdown("### Artifacts")
            artifacts_panel = gr.Markdown("")

            gr.Markdown("### Logs")
            log_panel = gr.Textbox(lines=10, interactive=False, show_label=False)
            refresh_logs_btn = gr.Button("Refresh", size="sm")

    # ── Events ──────────────────────────────────────────────────

    # MAD selection
    mad_selector.change(
        fn=on_mad_selected,
        inputs=[mad_selector],
        outputs=[conv_dropdown, health_detail, info_panel, artifacts_panel, chatbot],
    ).then(fn=lambda m: m, inputs=[mad_selector], outputs=[current_mad])

    # Conversation selection
    conv_dropdown.change(
        fn=on_conversation_selected,
        inputs=[conv_dropdown, current_mad],
        outputs=[chatbot, info_panel, artifacts_panel],
    ).then(fn=lambda c: c, inputs=[conv_dropdown], outputs=[current_conv])

    # Create conversation
    create_btn.click(
        fn=on_create_conversation,
        inputs=[new_title, current_mad],
        outputs=[conv_dropdown, new_title],
    )

    # Delete conversation — read directly from dropdown, not state
    delete_btn.click(
        fn=on_delete_conversation,
        inputs=[conv_dropdown, current_mad],
        outputs=[conv_dropdown],
    )

    # Chat submit — also refreshes conversation dropdown after response
    send_btn.click(
        fn=on_chat_submit,
        inputs=[msg_input, chatbot, current_mad, current_conv],
        outputs=[chatbot, artifacts_panel, conv_dropdown],
    ).then(fn=lambda: "", outputs=[msg_input])

    msg_input.submit(
        fn=on_chat_submit,
        inputs=[msg_input, chatbot, current_mad, current_conv],
        outputs=[chatbot, artifacts_panel, conv_dropdown],
    ).then(fn=lambda: "", outputs=[msg_input])

    # Logs
    refresh_logs_btn.click(
        fn=on_refresh_logs, inputs=[current_mad], outputs=[log_panel]
    )

    # Initial load — conversations pre-loaded at build time via _initial_conversations.
    async def _init_health_detail(mad_name):
        client = MADS.get(mad_name)
        if client:
            return await _get_health_text(client)
        return ""

    demo.load(fn=check_all_health, outputs=[health_bar])
    demo.load(fn=_init_health_detail, inputs=[mad_selector], outputs=[health_detail])
    demo.load(fn=on_refresh_logs, inputs=[mad_selector], outputs=[log_panel])


if __name__ == "__main__":
    port = CONFIG.get("port", 7860)
    demo.launch(server_name="0.0.0.0", server_port=port)
