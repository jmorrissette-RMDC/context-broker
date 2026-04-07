"""
Generate test dataset from Claude Code conversation files.

Reads .jsonl conversation files, extracts user/assistant messages,
and writes cleaned phase1 bulk-load data to Z:/test-data/conversational-memory/.

Run once. Re-run only if source conversations change.

Usage:
    python tests/integration/generate_test_data.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

# Source conversation files
SOURCE_DIR = Path(r"C:\Users\j\.claude\projects\C--Users-j-projects")
SOURCE_FILES = [
    ("717680d5-1d52-4c79-8e18-d37c5f850db3.jsonl", "conversation-1.json"),
    ("74b9a980-fb15-4208-acb1-f507eced44d3.jsonl", "conversation-2.json"),
    ("f84056ae-cd8a-4b0c-8b17-765b41f9b945.jsonl", "conversation-3.json"),
]

# Output directory
OUTPUT_DIR = Path(r"Z:\test-data\conversational-memory")
PHASE1_DIR = OUTPUT_DIR / "phase1-bulk-load"


def extract_text_content(content) -> str:
    """Extract plain text from Claude Code message content.

    Content can be:
    - A string (direct text)
    - A list of content blocks [{type: "text", text: "..."}, ...]
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    # Tool results may contain nested content
                    inner = block.get("content", "")
                    if isinstance(inner, str):
                        parts.append(inner)
                    elif isinstance(inner, list):
                        for sub in inner:
                            if isinstance(sub, dict) and sub.get("type") == "text":
                                parts.append(sub.get("text", ""))
        return "\n".join(parts)
    return str(content)


def process_conversation(source_path: Path) -> list[dict]:
    """Read a .jsonl file and extract user/assistant messages.

    Returns a list of {role, content, sender} dicts ready for store_message.
    """
    messages = []
    with open(source_path, encoding="utf-8") as f:
        for line in f:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = raw.get("type", "")
            if msg_type not in ("user", "assistant"):
                continue

            inner = raw.get("message", {})
            inner_content = inner.get("content", "")
            text = extract_text_content(inner_content)

            # Skip empty messages and tool-only messages
            if not text or not text.strip():
                continue

            # Skip very short system-generated messages
            if len(text.strip()) < 5:
                continue

            role = "user" if msg_type == "user" else "assistant"
            sender = "human" if role == "user" else inner.get("model", "claude")

            messages.append(
                {
                    "role": role,
                    "content": text,
                    "sender": sender,
                }
            )

    return messages


def write_manifest(conversations: list[dict]) -> None:
    """Write manifest.json with dataset metadata."""
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "tests/integration/generate_test_data.py",
        "source_dir": str(SOURCE_DIR),
        "conversations": conversations,
        "format": {
            "phase1": "List of {role, content, sender} dicts — feed directly to store_message",
            "phase2": "{prompt, expected_tools, expected_content_keywords} — Imperator conversation turns",
            "phase3": "{prompt, expected_tools, expected_content_keywords} — tool exercise prompts",
        },
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote manifest: {manifest_path}")


def main():
    # Ensure output directories exist
    PHASE1_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "phase2-agent-conversation").mkdir(exist_ok=True)
    (OUTPUT_DIR / "phase3-synthetic").mkdir(exist_ok=True)

    conversation_meta = []
    total_messages = 0

    for source_name, output_name in SOURCE_FILES:
        source_path = SOURCE_DIR / source_name
        if not source_path.exists():
            print(f"WARNING: Source file not found: {source_path}")
            continue

        print(f"Processing {source_name}...")
        messages = process_conversation(source_path)

        output_path = PHASE1_DIR / output_name
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(messages, f, indent=None, ensure_ascii=False)

        user_count = sum(1 for m in messages if m["role"] == "user")
        asst_count = sum(1 for m in messages if m["role"] == "assistant")
        total_messages += len(messages)

        meta = {
            "source_file": source_name,
            "output_file": output_name,
            "total_messages": len(messages),
            "user_messages": user_count,
            "assistant_messages": asst_count,
        }
        conversation_meta.append(meta)

        print(
            f"  {output_name}: {len(messages)} messages ({user_count} user, {asst_count} assistant)"
        )

    write_manifest(conversation_meta)

    print(
        f"\nDone. Total: {total_messages} messages across {len(conversation_meta)} conversations."
    )
    print(f"Output: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
