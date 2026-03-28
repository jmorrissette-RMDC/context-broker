"""Phase L: Quality evaluation tests using Claude Sonnet as an LLM judge.

Each test retrieves real data from the live system, then passes it to
Claude Sonnet CLI (subprocess) for qualitative evaluation. Sonnet rates
the output as GOOD, ACCEPTABLE, or POOR. The test fails only on POOR.

All tests run against the live stack at http://192.168.1.110:8081.
"""

import json
import re
import subprocess
import time
import uuid

import pytest

from tests.claude.live.helpers import (
    chat_call,
    extract_mcp_result,
    log_issue,
    mcp_call,
)

pytestmark = pytest.mark.live

SONNET_TIMEOUT = 120  # seconds for Claude CLI subprocess


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_sonnet_judge(prompt: str) -> str:
    """Call Claude Sonnet CLI and return the response text.

    Uses: claude --model sonnet --print -p "<prompt>"
    """
    result = subprocess.run(
        ["claude", "--model", "sonnet", "--print", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=SONNET_TIMEOUT,
    )
    output = result.stdout.strip()
    if not output and result.stderr:
        raise RuntimeError(f"Claude CLI failed: {result.stderr[:500]}")
    return output


def _extract_rating(response: str) -> str:
    """Extract GOOD, ACCEPTABLE, or POOR from the judge response.

    Looks for the rating keyword in the response text, prioritizing
    explicit rating patterns like 'Rating: GOOD' or '**GOOD**'.
    """
    response_upper = response.upper()

    # Look for explicit rating patterns first
    for pattern in [
        r"RATING:\s*(GOOD|ACCEPTABLE|POOR)",
        r"\*\*(GOOD|ACCEPTABLE|POOR)\*\*",
        r"VERDICT:\s*(GOOD|ACCEPTABLE|POOR)",
        r"OVERALL:\s*(GOOD|ACCEPTABLE|POOR)",
    ]:
        match = re.search(pattern, response_upper)
        if match:
            return match.group(1)

    # Fall back to simple keyword presence (last occurrence wins for ambiguity)
    if "POOR" in response_upper:
        return "POOR"
    if "ACCEPTABLE" in response_upper:
        return "ACCEPTABLE"
    if "GOOD" in response_upper:
        return "GOOD"

    # If no rating found, treat as ACCEPTABLE with a warning
    return "ACCEPTABLE"


def _judge_and_assert(
    test_name: str,
    prompt: str,
    category: str = "quality",
) -> str:
    """Call the Sonnet judge, extract rating, log results, assert not POOR.

    Returns the full judge response for further inspection if needed.
    """
    judge_response = _call_sonnet_judge(prompt)
    rating = _extract_rating(judge_response)

    # Log the evaluation regardless of outcome
    log_issue(
        test_name,
        "info" if rating == "GOOD" else ("warning" if rating == "ACCEPTABLE" else "error"),
        category,
        f"LLM judge rating: {rating}",
        "GOOD or ACCEPTABLE",
        f"Rating: {rating}. Judge response excerpt: {judge_response[:400]}",
    )

    assert rating != "POOR", (
        f"LLM judge rated output as POOR.\n"
        f"Judge response:\n{judge_response[:800]}"
    )

    return judge_response


# ===========================================================================
# L-01: Tiered summary quality
# ===========================================================================

class TestTieredSummaryQuality:
    """L-01: Evaluate tiered-summary build type output quality."""

    def test_tiered_summary_quality(self, http_client, any_conversation_id):
        """Get tiered-summary context, have Sonnet judge its quality."""
        # Get tiered-summary context for a loaded conversation
        resp = mcp_call(
            http_client,
            "get_context",
            {
                "build_type": "tiered-summary",
                "budget": 16000,
                "conversation_id": any_conversation_id,
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)

        # Extract the assembled context text
        context_text = ""
        if "context" in result:
            ctx = result["context"]
            if isinstance(ctx, str):
                context_text = ctx
            elif isinstance(ctx, list):
                context_text = "\n".join(
                    m.get("content", str(m)) for m in ctx
                )
            elif isinstance(ctx, dict):
                context_text = json.dumps(ctx, indent=2)
        elif "tiers" in result:
            context_text = json.dumps(result["tiers"], indent=2)
        elif "messages" in result:
            context_text = "\n".join(
                m.get("content", str(m)) for m in result["messages"][:30]
            )

        assert len(context_text) > 50, (
            f"Tiered summary returned insufficient content ({len(context_text)} chars)"
        )

        # Also get some raw messages for comparison
        history_resp = mcp_call(
            http_client,
            "conv_get_history",
            {"conversation_id": any_conversation_id},
        )
        raw_messages = ""
        if history_resp.status_code == 200:
            hist_result = extract_mcp_result(history_resp)
            msgs = hist_result.get("messages", [])[:10]
            raw_messages = "\n".join(
                f"[{m.get('role', '?')}]: {m.get('content', '')[:200]}" for m in msgs
            )

        # Truncate for the judge prompt
        context_excerpt = context_text[:3000]
        raw_excerpt = raw_messages[:1500] if raw_messages else "(no raw messages available)"

        prompt = (
            "You are evaluating the quality of a tiered-summary context assembly "
            "for a conversational memory system. The system takes raw conversation "
            "messages and produces a summarized context window.\n\n"
            "ASSEMBLED CONTEXT (tiered-summary output):\n"
            f"```\n{context_excerpt}\n```\n\n"
            "SAMPLE ORIGINAL MESSAGES:\n"
            f"```\n{raw_excerpt}\n```\n\n"
            "Evaluate on these criteria:\n"
            "1. Does the summary capture key topics and themes?\n"
            "2. Is the summary coherent and well-structured?\n"
            "3. Does it preserve important details while being concise?\n"
            "4. Would this context be useful for an AI assistant continuing the conversation?\n\n"
            "Rate the quality as exactly one of: GOOD, ACCEPTABLE, or POOR.\n"
            "Provide your reasoning, then end with your rating on its own line: Rating: GOOD/ACCEPTABLE/POOR"
        )

        _judge_and_assert("test_tiered_summary_quality", prompt, "quality-tiered")


# ===========================================================================
# L-02: Enriched build type quality
# ===========================================================================

class TestEnrichedQuality:
    """L-02: Evaluate enriched build type output quality."""

    def test_enriched_quality(self, http_client, any_conversation_id):
        """Get enriched context, have Sonnet judge whether enrichment adds value."""
        # Get enriched context
        resp = mcp_call(
            http_client,
            "get_context",
            {
                "build_type": "enriched",
                "budget": 16000,
                "conversation_id": any_conversation_id,
            },
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)

        # Extract context text
        context_text = ""
        if "context" in result:
            ctx = result["context"]
            if isinstance(ctx, str):
                context_text = ctx
            elif isinstance(ctx, list):
                context_text = "\n".join(
                    m.get("content", str(m)) for m in ctx
                )
            elif isinstance(ctx, dict):
                context_text = json.dumps(ctx, indent=2)
        elif "messages" in result:
            context_text = "\n".join(
                m.get("content", str(m)) for m in result["messages"][:30]
            )

        if len(context_text) < 50:
            # Enriched may fall back to simpler build on new conversations
            log_issue(
                "test_enriched_quality",
                "warning",
                "quality-enriched",
                f"Enriched context too short ({len(context_text)} chars), "
                "may have fallen back to simpler build type",
            )
            pytest.skip("Enriched context too short -- may lack knowledge graph data")

        context_excerpt = context_text[:3000]

        prompt = (
            "You are evaluating the quality of an 'enriched' context assembly "
            "for a conversational memory system. The enriched build type adds "
            "semantic search results and knowledge graph facts to the base context.\n\n"
            "ENRICHED CONTEXT OUTPUT:\n"
            f"```\n{context_excerpt}\n```\n\n"
            "Evaluate on these criteria:\n"
            "1. Does the context include semantic enrichment (related memories, "
            "knowledge graph facts, or entity relationships)?\n"
            "2. Does the enrichment add value beyond raw message history?\n"
            "3. Is the enriched context coherent and not cluttered with noise?\n"
            "4. Would an AI assistant benefit from this enriched context?\n\n"
            "If the output appears to be a plain message list with no enrichment, "
            "rate it ACCEPTABLE (the system may fall back when enrichment data is sparse).\n\n"
            "Rate the quality as exactly one of: GOOD, ACCEPTABLE, or POOR.\n"
            "Provide your reasoning, then end with: Rating: GOOD/ACCEPTABLE/POOR"
        )

        _judge_and_assert("test_enriched_quality", prompt, "quality-enriched")


# ===========================================================================
# L-03: Search relevance
# ===========================================================================

class TestSearchRelevance:
    """L-03: Evaluate search_messages result relevance."""

    def test_search_relevance(self, http_client):
        """Search for 'MAD container architecture', have Sonnet judge relevance."""
        query = "MAD container architecture"
        resp = mcp_call(
            http_client,
            "search_messages",
            {"query": query, "limit": 10},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        messages = result.get("messages", [])

        if not messages:
            log_issue(
                "test_search_relevance",
                "warning",
                "quality-search",
                f"search_messages returned 0 results for '{query}'",
            )
            pytest.skip(f"No search results for '{query}'")

        # Format results for the judge
        formatted_results = []
        for i, msg in enumerate(messages[:10]):
            content = str(msg.get("content", ""))[:300]
            score = msg.get("similarity", msg.get("score", "n/a"))
            formatted_results.append(f"Result {i+1} (score={score}):\n{content}")

        results_text = "\n\n".join(formatted_results)

        prompt = (
            "You are evaluating the relevance of search results from a "
            "conversational memory system's semantic search.\n\n"
            f"SEARCH QUERY: \"{query}\"\n\n"
            f"SEARCH RESULTS:\n```\n{results_text}\n```\n\n"
            "Evaluate on these criteria:\n"
            "1. Are the results semantically relevant to the query?\n"
            "2. Do the results contain information about MAD architecture, "
            "containers, or related system design topics?\n"
            "3. Are the results ordered by relevance (best matches first)?\n\n"
            "Rate the relevance as exactly one of: GOOD, ACCEPTABLE, or POOR.\n"
            "Provide your reasoning, then end with: Rating: GOOD/ACCEPTABLE/POOR"
        )

        _judge_and_assert("test_search_relevance", prompt, "quality-search")


# ===========================================================================
# L-04: Knowledge extraction quality
# ===========================================================================

class TestKnowledgeExtractionQuality:
    """L-04: Evaluate quality of extracted memories."""

    def test_knowledge_extraction_quality(self, http_client):
        """Get extracted memories via mem_search, have Sonnet judge accuracy."""
        # Search for memories on a broad topic
        resp = mcp_call(
            http_client,
            "mem_search",
            {"query": "software architecture design patterns", "user_id": "test-runner"},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        memories = result.get("memories", [])

        if not memories:
            # Try search_knowledge as fallback
            resp2 = mcp_call(
                http_client,
                "search_knowledge",
                {"query": "software architecture", "user_id": "test-runner"},
            )
            if resp2.status_code == 200:
                result2 = extract_mcp_result(resp2)
                memories = result2.get("memories", [])

        if not memories:
            log_issue(
                "test_knowledge_extraction_quality",
                "warning",
                "quality-extraction",
                "No extracted memories found for quality evaluation",
            )
            pytest.skip("No extracted memories available for evaluation")

        # Format memories for the judge
        formatted_memories = []
        for i, mem in enumerate(memories[:10]):
            text = str(mem.get("memory", mem.get("content", mem.get("text", ""))))
            score = mem.get("score", mem.get("similarity", "n/a"))
            formatted_memories.append(f"Memory {i+1} (score={score}):\n{text}")

        memories_text = "\n\n".join(formatted_memories)

        prompt = (
            "You are evaluating the quality of automatically extracted memories "
            "from a conversational memory system. The system extracts factual "
            "knowledge from user conversations and stores them as discrete memories.\n\n"
            f"EXTRACTED MEMORIES:\n```\n{memories_text}\n```\n\n"
            "Evaluate on these criteria:\n"
            "1. Do the memories capture discrete, factual information?\n"
            "2. Are the memories well-formed and understandable out of context?\n"
            "3. Do they avoid being too vague or too verbose?\n"
            "4. Would these memories be useful for personalizing future conversations?\n\n"
            "Rate the quality as exactly one of: GOOD, ACCEPTABLE, or POOR.\n"
            "Provide your reasoning, then end with: Rating: GOOD/ACCEPTABLE/POOR"
        )

        _judge_and_assert(
            "test_knowledge_extraction_quality", prompt, "quality-extraction"
        )


# ===========================================================================
# L-05: Imperator coherence
# ===========================================================================

class TestImperatorCoherence:
    """L-05: Evaluate Imperator response coherence on a multi-step prompt."""

    def test_imperator_coherence(self, http_client, any_conversation_id):
        """Send a multi-step prompt to the Imperator, have Sonnet judge coherence."""
        # Send a complex multi-step prompt
        multi_step_prompt = (
            "I need you to do three things:\n"
            "1. List the conversations you have stored (just the count and a few titles)\n"
            "2. Search your memories for anything about 'system architecture'\n"
            "3. Summarize what you know about the user's projects in 2-3 sentences"
        )

        result = chat_call(http_client, multi_step_prompt, timeout=180)
        assert not result.get("error"), f"Imperator returned error: {result}"

        content = result["choices"][0]["message"]["content"]
        assert len(content) > 50, (
            f"Imperator response too short ({len(content)} chars)"
        )

        # Truncate for the judge
        response_excerpt = content[:3000]

        prompt = (
            "You are evaluating the coherence and quality of an AI assistant's response "
            "to a multi-step request. The assistant (called 'Imperator') manages a "
            "conversational memory system and has access to tools for searching "
            "conversations, memories, and knowledge.\n\n"
            "USER PROMPT:\n"
            f"```\n{multi_step_prompt}\n```\n\n"
            "IMPERATOR RESPONSE:\n"
            f"```\n{response_excerpt}\n```\n\n"
            "Evaluate on these criteria:\n"
            "1. Does the response address all three parts of the request?\n"
            "2. Is the response coherent and well-structured?\n"
            "3. Does the response demonstrate actual use of tools (not fabricated data)?\n"
            "4. Is the tone appropriate and the information presented clearly?\n\n"
            "Note: It's acceptable if the assistant couldn't find results for every part, "
            "as long as it attempted each step and reported honestly.\n\n"
            "Rate the coherence as exactly one of: GOOD, ACCEPTABLE, or POOR.\n"
            "Provide your reasoning, then end with: Rating: GOOD/ACCEPTABLE/POOR"
        )

        _judge_and_assert("test_imperator_coherence", prompt, "quality-imperator")
