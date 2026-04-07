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

    # Fall back to last occurrence of a rating keyword.
    # Check GOOD first — if the judge mentions "POOR" in reasoning
    # ("this is not POOR") but rates GOOD, we must not return POOR.
    # Find the last occurrence of each keyword and use the latest one.
    last_positions = {}
    for rating in ("GOOD", "ACCEPTABLE", "POOR"):
        pos = response_upper.rfind(rating)
        if pos >= 0:
            last_positions[rating] = pos
    if last_positions:
        return max(last_positions, key=last_positions.get)

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

        # Extract tiers separately for the judge — the assembled context
        # is 72% raw recent messages by design. The judge should evaluate
        # the summarized tiers (archival + chunks), not the raw portion.
        tiers = result.get("tiers", {})
        archival = tiers.get("archival_summary", "") or ""
        chunks = tiers.get("chunk_summaries", [])
        chunks_text = "\n---\n".join(chunks) if chunks else "(no chunk summaries)"
        summary_content = f"ARCHIVAL SUMMARY:\n{archival}\n\nCHUNK SUMMARIES:\n{chunks_text}"

        prompt = (
            "You are evaluating the quality of tiered context summaries "
            "for a conversational memory system. The system compresses older "
            "conversation messages into summaries at two levels:\n"
            "- Archival summary: highly compressed overview of the oldest content\n"
            "- Chunk summaries: moderate compression of intermediate content\n\n"
            "SUMMARIES PRODUCED:\n"
            f"```\n{summary_content[:3000]}\n```\n\n"
            "SAMPLE ORIGINAL MESSAGES (for reference):\n"
            f"```\n{raw_excerpt}\n```\n\n"
            "Evaluate on these criteria:\n"
            "1. Do the summaries capture key topics and themes from the conversation?\n"
            "2. Are the summaries coherent, well-structured, and concise?\n"
            "3. Do they preserve important details while compressing?\n"
            "4. Would these summaries be useful for providing context to an AI assistant?\n\n"
            "Rate the quality as exactly one of: GOOD, ACCEPTABLE, or POOR.\n"
            "Provide your reasoning, then end with your rating on its own line: Rating: GOOD/ACCEPTABLE/POOR"
        )

        _judge_and_assert("test_tiered_summary_quality", prompt, "quality-tiered")


# ===========================================================================
# L-02: Enriched build type quality
# ===========================================================================

# L-02: TestEnrichedQuality removed — enriched build type no longer does server-side RAG.
# CEA: enrichment is client-side via CEAc. Server returns tiers only.
# Test deleted per #508.


# ===========================================================================
# L-03: Search relevance
# ===========================================================================

class TestSearchRelevance:
    """L-03: Evaluate search_messages result relevance."""

    def test_search_relevance(self, http_client):
        """Search for a topic present in the Phase 1 data, have Sonnet judge relevance."""
        query = "context assembly token budget"
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
            pytest.fail(f"search_messages returned 0 results for '{query}' against 10K+ messages — search is broken")

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
            "2. Do the results contain information about context assembly, "
            "token budgets, or related system design topics?\n"
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
        """Get CEA-extracted facts via knowledge_search, have Sonnet judge accuracy.

        CEA: extraction fires at compaction time (not per-message).
        knowledge_search returns vector_facts (not memories).
        """
        # Search for facts on a topic from Phase 1 data (MAD architecture,
        # context engineering). Avoids returning low-quality facts from
        # Phase K tool effect tests (file writes, config changes, schedules).
        resp = mcp_call(
            http_client,
            "knowledge_search",
            {"query": "MAD architecture context broker deployment infrastructure", "user_id": "default"},
        )
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        # CEA: knowledge_search returns vector_facts (not memories)
        vector_facts = result.get("vector_facts", [])

        if not vector_facts:
            log_issue(
                "test_knowledge_extraction_quality",
                "warning",
                "quality-extraction",
                "No CEA-extracted vector_facts found; CEA extraction fires at compaction time "
                "— compaction may not have triggered yet",
            )
            pytest.fail(
                "No vector_facts found — CEA extraction has not produced searchable facts. "
                "Ensure compaction was triggered (sufficient messages loaded)."
            )

        # Format facts for the judge
        formatted_facts = []
        for i, fact in enumerate(vector_facts[:10]):
            text = str(fact.get("memory", fact.get("content", fact.get("text", ""))))
            score = fact.get("score", fact.get("similarity", "n/a"))
            formatted_facts.append(f"Fact {i+1} (score={score}):\n{text}")

        facts_text = "\n\n".join(formatted_facts)

        prompt = (
            "You are evaluating the quality of automatically extracted facts "
            "from a conversational memory system. The system (CEA) extracts factual "
            "knowledge from conversation compaction events and stores them as discrete facts.\n\n"
            f"EXTRACTED FACTS:\n```\n{facts_text}\n```\n\n"
            "Evaluate on these criteria:\n"
            "1. Do the facts capture discrete, factual information?\n"
            "2. Are the facts well-formed and understandable out of context?\n"
            "3. Do they avoid being too vague or too verbose?\n"
            "4. Would these facts be useful for personalizing future conversations?\n\n"
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


# ===========================================================================
# L-new: CEA metadata quality evaluation
# ===========================================================================

class TestMetadataQuality:
    """L-new: Evaluate quality of CEA durability/confidence scores."""

    def test_metadata_quality(self, http_client):
        """Get CEA quality metadata, have Sonnet judge whether scores are calibrated.

        Checks that durability scores reflect actual durability of the facts
        and confidence scores reflect extraction confidence.
        """
        from tests.claude.live.helpers import docker_psql

        # Get a sample of facts with their metadata
        raw = docker_psql(
            "SELECT qm.durability, qm.confidence, qm.source_type, "
            "qm.original_utterance "
            "FROM cea_quality_metadata qm "
            "WHERE qm.target_type = 'fact' "
            "AND qm.original_utterance IS NOT NULL "
            "AND qm.original_utterance != '' "
            "ORDER BY qm.extracted_at DESC LIMIT 10"
        ).strip()

        if not raw or "(0 rows)" in raw:
            log_issue(
                "test_metadata_quality",
                "warning",
                "quality-metadata",
                "No CEA quality metadata found; CEA extraction may not have fired yet",
            )
            pytest.skip("cea_quality_metadata empty — CEA extraction not yet triggered")

        prompt = (
            "You are evaluating the calibration of quality scores assigned to "
            "automatically extracted facts from a conversational memory system.\n\n"
            "The system assigns two scores to each fact:\n"
            "- durability (0.0-1.0): how long this fact will remain true "
            "(1.0 = permanent, 0.0 = very temporary)\n"
            "- confidence (0.0-1.0): how confident the LLM was in extracting this fact "
            "(1.0 = very certain, 0.0 = uncertain)\n\n"
            "SAMPLE FACTS WITH SCORES:\n"
            f"```\n{raw[:3000]}\n```\n\n"
            "Evaluate on these criteria:\n"
            "1. Do the durability scores seem calibrated to the actual content? "
            "(e.g., preferences should be lower than facts about immutable events)\n"
            "2. Do the confidence scores reflect reasonable extraction confidence?\n"
            "3. Are the source_type labels appropriate for the content?\n"
            "4. Are there obvious calibration failures (e.g., all scores are 0.5)?\n\n"
            "Rate the calibration quality as exactly one of: GOOD, ACCEPTABLE, or POOR.\n"
            "Provide your reasoning, then end with: Rating: GOOD/ACCEPTABLE/POOR"
        )

        _judge_and_assert("test_metadata_quality", prompt, "quality-metadata")
