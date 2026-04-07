"""LLM judge for context quality at stress test checkpoints.

Uses Claude Sonnet CLI to evaluate whether assembled context is
coherent, useful, and correctly structured at various compression levels.
"""

import logging
import subprocess

_log = logging.getLogger("stress.quality_judge")


def judge_context_quality(
    context_messages: list[dict],
    budget: int,
    conversation_depth_tokens: int,
    compaction_cycle: int,
) -> dict:
    """Judge the quality of assembled context.

    Returns {"rating": "GOOD"|"ACCEPTABLE"|"POOR", "reasoning": str}
    """
    # Build a representation of the context for the judge
    context_summary = []
    for msg in context_messages[:50]:  # Cap at 50 messages for judge prompt
        role = msg.get("role", "unknown")
        content = str(msg.get("content", ""))[:500]
        context_summary.append(f"[{role}] {content}")
    context_text = "\n".join(context_summary)

    total_tokens = sum(
        len(str(msg.get("content", ""))) // 4
        for msg in context_messages
    )

    compression = (
        (1 - budget / conversation_depth_tokens) * 100
        if conversation_depth_tokens > 0
        else 0
    )

    prompt = (
        f"You are evaluating the quality of an AI context assembly system.\n\n"
        f"This context was assembled for a conversation that is {conversation_depth_tokens:,} "
        f"tokens deep, compressed into a {budget:,} token budget "
        f"({compression:.1f}% compression). This is compaction cycle #{compaction_cycle}.\n\n"
        f"The assembled context contains {len(context_messages)} messages "
        f"totaling ~{total_tokens:,} tokens.\n\n"
        f"Here is the assembled context (first 50 messages):\n\n"
        f"{context_text}\n\n"
        f"Evaluate on these criteria:\n"
        f"1. Is the context coherent and readable?\n"
        f"2. Does it capture key themes and topics from what appears to be "
        f"a long conversation?\n"
        f"3. Are the archival summaries (system messages) useful and informative?\n"
        f"4. Is the token budget respected (not wildly over or under)?\n"
        f"5. Would an AI assistant be able to continue this conversation "
        f"meaningfully using this context?\n\n"
        f"Rate the quality as exactly one of: GOOD, ACCEPTABLE, or POOR.\n"
        f"Provide your reasoning, then end with: Rating: GOOD/ACCEPTABLE/POOR"
    )

    try:
        result = subprocess.run(
            ["claude", "--dangerously-skip-permissions", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=120,
        )
        response = result.stdout.strip()
        if not response and result.stderr:
            _log.warning("Judge CLI failed: %s", result.stderr[:200])
            return {"rating": "UNKNOWN", "reasoning": f"CLI error: {result.stderr[:200]}"}

        rating = _extract_rating(response)
        return {"rating": rating, "reasoning": response[:500]}
    except subprocess.TimeoutExpired:
        return {"rating": "UNKNOWN", "reasoning": "Judge timed out after 120s"}
    except FileNotFoundError:
        _log.warning("claude CLI not found, skipping quality check")
        return {"rating": "SKIPPED", "reasoning": "Claude CLI not available"}


def _extract_rating(response: str) -> str:
    """Extract GOOD/ACCEPTABLE/POOR from judge response."""
    import re

    response_upper = response.upper()

    for pattern in [
        r"RATING:\s*(GOOD|ACCEPTABLE|POOR)",
        r"\*\*(GOOD|ACCEPTABLE|POOR)\*\*",
        r"VERDICT:\s*(GOOD|ACCEPTABLE|POOR)",
    ]:
        match = re.search(pattern, response_upper)
        if match:
            return match.group(1)

    # Last occurrence fallback
    last_positions = {}
    for rating in ("GOOD", "ACCEPTABLE", "POOR"):
        pos = response_upper.rfind(rating)
        if pos >= 0:
            last_positions[rating] = pos
    if last_positions:
        return max(last_positions, key=last_positions.get)

    return "UNKNOWN"
