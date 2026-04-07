"""
Memory Extraction — text cleaning utilities.

The old per-message extraction StateGraph flow was deregistered in the CEA
refactor (replaced by cea_extraction_flow.py). This module retains only
the text cleaning functions used by the compaction pipeline.

See: register.py comment at line 35, GitHub #492.
"""

import re

# Regex patterns for artifact stripping
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]{10,}`")
_MARKDOWN_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MARKDOWN_HR_RE = re.compile(r"^\*{3,}$|^-{3,}$|^_{3,}$", re.MULTILINE)
_MARKDOWN_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def clean_for_compaction(text: str) -> str:
    """Strip artifacts before summarization, preserving identifiers as retrieval hooks.

    Unlike the old _clean_for_extraction which replaced paths with [path], this
    function keeps file paths, function names, and entity names in the text so
    they survive as searchable hooks in the tier 2/3 summaries. Code blocks and
    formatting noise are still stripped — the LLM summarizes the discussion
    about artifacts, not the artifacts themselves.
    """
    # Remove code blocks (triple backtick fenced) but keep surrounding discussion
    text = _CODE_BLOCK_RE.sub("[code block removed]", text)
    # Remove long inline code spans but keep short ones (likely identifiers)
    text = _INLINE_CODE_RE.sub("[code]", text)
    # Keep file paths — they are retrieval hooks
    # Keep URLs — they may be referenced in summaries
    # Simplify markdown headers to plain text
    text = _MARKDOWN_HEADER_RE.sub("", text)
    # Remove horizontal rules
    text = _MARKDOWN_HR_RE.sub("", text)
    # Simplify bold markers
    text = _MARKDOWN_BOLD_RE.sub(r"\1", text)
    # Collapse excessive newlines
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()
