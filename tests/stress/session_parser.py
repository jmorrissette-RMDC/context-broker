"""Parse Claude Code .jsonl session files into conversation message pairs.

Session files contain JSONL with one record per line:
- Line 0: snapshot (skip)
- Lines 1+: messages with {type, message: {role, content}, ...}

Content can be a string or a list of blocks (text, tool_use, tool_result).
We extract only user/assistant text content for seeding conversations.
"""

import json
import logging
import os
from pathlib import Path
from typing import Generator

_log = logging.getLogger("stress.session_parser")


def _extract_text(content) -> str:
    """Extract plain text from message content (string or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    # Skip tool results — not conversational
                    pass
                elif block.get("type") == "tool_use":
                    # Skip tool calls — not conversational
                    pass
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content) if content else ""


def parse_session_file(
    path: str | Path,
    min_content_length: int = 20,
) -> list[dict]:
    """Parse a single session file into conversation messages.

    Returns list of {"role": "user"|"assistant", "content": str}.
    Filters out empty messages, tool calls, and system messages.
    """
    messages = []
    try:
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == 0:
                    continue  # Skip snapshot line
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = obj.get("message", {})
                role = msg.get("role")
                if role not in ("user", "assistant"):
                    continue

                content = _extract_text(msg.get("content", ""))
                if len(content) < min_content_length:
                    continue

                messages.append({"role": role, "content": content})
    except (OSError, UnicodeDecodeError) as exc:
        _log.warning("Failed to parse %s: %s", path, exc)

    return messages


def iter_session_files(
    session_dir: str | Path,
    skip_subagents: bool = True,
) -> Generator[Path, None, None]:
    """Yield .jsonl session file paths, sorted largest first."""
    session_dir = Path(session_dir)
    files = []
    for root, _dirs, fnames in os.walk(session_dir):
        if skip_subagents and "subagents" in root:
            continue
        for fn in fnames:
            if fn.endswith(".jsonl"):
                fp = Path(root) / fn
                files.append((fp, fp.stat().st_size))

    # Largest first — more data per file
    files.sort(key=lambda x: x[1], reverse=True)
    for fp, _size in files:
        yield fp


def load_seed_messages(
    session_dir: str | Path,
    max_tokens: int = 2_000_000,
    chars_per_token: int = 4,
) -> list[dict]:
    """Load messages from session files until token target is reached.

    Returns a flat list of {"role", "content"} dicts ready for
    store_message calls.
    """
    max_chars = max_tokens * chars_per_token
    messages = []
    total_chars = 0

    for session_path in iter_session_files(session_dir):
        session_msgs = parse_session_file(session_path)
        for msg in session_msgs:
            messages.append(msg)
            total_chars += len(msg["content"])
            if total_chars >= max_chars:
                _log.info(
                    "Loaded %d messages (%d chars, ~%dK tokens) from %d files",
                    len(messages),
                    total_chars,
                    total_chars // (chars_per_token * 1000),
                    len(set(m.get("_source", "") for m in messages)) or "multiple",
                )
                return messages

    _log.info(
        "Exhausted all session files: %d messages, %d chars, ~%dK tokens",
        len(messages),
        total_chars,
        total_chars // (chars_per_token * 1000),
    )
    return messages


if __name__ == "__main__":
    """Quick test: parse and summarize available session data."""
    import sys

    session_dir = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\j\.claude\projects"
    logging.basicConfig(level=logging.INFO)

    total_msgs = 0
    total_chars = 0
    file_count = 0

    for fp in iter_session_files(session_dir):
        msgs = parse_session_file(fp)
        chars = sum(len(m["content"]) for m in msgs)
        total_msgs += len(msgs)
        total_chars += chars
        file_count += 1
        if file_count <= 5:
            print(f"  {fp.name[:40]}: {len(msgs)} msgs, {chars:,} chars")

    print(f"\nTotal: {file_count} files, {total_msgs:,} messages, "
          f"{total_chars:,} chars (~{total_chars // 4:,} tokens)")
