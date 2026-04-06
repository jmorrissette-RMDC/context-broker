"""
CEAs Extraction Flow -- server-side deterministic StateGraph.

Runs at compaction time within compact_tier1(). Extracts durable facts
and graph triples from artifact-stripped tier 1 content via structured
LLM output, then dispatches results through the quality wrapper.

See PRD REQ-CEA-S01 through S10.
See HLD S3 Flow A: Extraction (Write Side).
"""

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_chat_model, get_tuning, verbose_log
from app.prompt_loader import async_load_prompt

_log = logging.getLogger("context_broker.cea.extraction")

# Prometheus metrics (REQ-CEA-A06)
try:
    from prometheus_client import Counter, Histogram

    CEA_EXTRACTION_EVENTS = Counter(
        "cea_extraction_events_total",
        "Total CEAs extraction events",
        ["status"],
    )
    CEA_EXTRACTION_DURATION = Histogram(
        "cea_extraction_duration_seconds",
        "CEAs extraction pipeline duration",
    )
    CEA_FACTS_EXTRACTED = Counter(
        "cea_facts_extracted_total",
        "Total facts extracted by CEAs",
        ["relationship"],
    )
except (ImportError, ValueError):
    # ImportError: prometheus_client not installed
    # ValueError: duplicate registration on install_stategraph re-import
    CEA_EXTRACTION_EVENTS = None
    CEA_EXTRACTION_DURATION = None
    CEA_FACTS_EXTRACTED = None


class CEAsExtractionState(TypedDict):
    content: str  # artifact-stripped tier 1 content
    tier2_context: str  # tier 2 summaries (read-only)
    tier3_context: str  # tier 3 archival (read-only)
    conversation_id: str  # conversation identifier (fix #S1)
    existing_facts: list[dict]
    extraction_output: Optional[dict]  # structured JSON from LLM
    dispatch_results: dict
    config: dict
    error: Optional[str]


async def search_existing_facts(state: CEAsExtractionState) -> dict:
    """Search the quality wrapper for existing facts relevant to the content.

    REQ-CEA-S03: Store-enriched extraction.
    REQ-CEA-S04: Configurable retrieval scope.
    """
    config = state["config"]
    t0 = time.monotonic()
    verbose_log(config, _log, "CEAs.search_existing_facts ENTER conv=%s", state["conversation_id"])

    cea_config = config.get("cea", {})
    fact_limit = cea_config.get("pre_extraction_fact_limit", 20)
    scope = cea_config.get("retrieval_scope", "conversation")

    from context_broker_ae.memory.quality_wrapper import get_quality_wrapper
    wrapper = await get_quality_wrapper(config)

    # Determine user_id based on scope (fix #6)
    user_id = None  # global
    if scope == "user":
        # Extract first sender from the content for user-scoped search
        for line in state["content"].split("\n"):
            if "(" in line and "):" in line:
                try:
                    user_id = line.split("(")[1].split(")")[0].strip()
                    if user_id:
                        break
                except IndexError:
                    continue
    elif scope == "conversation":
        # For conversation scope: use the tier context as query (more targeted)
        # and filter by conversation_id post-retrieval
        pass

    # Use tier2 summary as query if available (better than first 500 chars — fix #23)
    tier2 = state.get("tier2_context", "")
    query = tier2[:500] if tier2.strip() else state["content"][:500]

    try:
        results = await wrapper.search(query=query, user_id=user_id, limit=fact_limit)
        facts = results.get("vector_facts", [])

        # Conversation scope: filter to matching conversation_id
        if scope == "conversation":
            conv_id = state.get("conversation_id", "")
            facts = [
                f for f in facts
                if f.get("_metadata", {}).get("conversation_id") and
                str(f["_metadata"]["conversation_id"]) == conv_id
            ]
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _log.warning("CEAs: failed to search existing facts: %s", exc)
        facts = []

    elapsed = time.monotonic() - t0
    verbose_log(config, _log, "CEAs.search_existing_facts EXIT found=%d scope=%s (%.2fs)", len(facts), scope, elapsed)

    return {"existing_facts": facts}


async def run_extraction_llm(state: CEAsExtractionState) -> dict:
    """Call the extraction LLM with structured output.

    REQ-CEA-S05: Structured extraction output.
    REQ-CEA-S07: Durability assessment.
    REQ-CEA-I04: Temporal resolution (current UTC date provided).
    REQ-CEA-I06: Configurable prompt templates (both vector + graph — fix #4).
    """
    config = state["config"]
    t0 = time.monotonic()
    verbose_log(config, _log, "CEAs.run_extraction_llm ENTER conv=%s", state["conversation_id"])

    try:
        # Load vector fact extraction prompt template (REQ-CEA-I06).
        # Graph extraction is handled by Mem0 internally when skip_graph=False.
        # The cea_graph_extraction.md template exists as documentation of the
        # intended graph schema but is not sent to the LLM — Mem0's own entity
        # extraction prompts handle Neo4j writes. This avoids duplicate token
        # cost for graph triples that Mem0 extracts anyway.
        vector_prompt_template = await async_load_prompt("cea_vector_extraction")

        existing_facts_text = ""
        for fact in state.get("existing_facts", []):
            fid = fact.get("id", "?")
            memory = fact.get("memory", "")
            existing_facts_text += f"- [{fid}] {memory}\n"
        if not existing_facts_text:
            existing_facts_text = "(none)"

        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        prompt = vector_prompt_template.format(
            current_date=current_date,
            existing_facts=existing_facts_text,
            content=state["content"],
            tier2_context=state.get("tier2_context", "(none)"),
            tier3_context=state.get("tier3_context", "(none)"),
        )

        llm = get_chat_model(config, role="extraction")
        response = await llm.ainvoke(
            [{"role": "user", "content": prompt}],
        )

        response_text = response.content if hasattr(response, "content") else str(response)

        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]

        extraction_output = json.loads(response_text)

        elapsed = time.monotonic() - t0
        n_facts = len(extraction_output.get("facts", []))
        verbose_log(config, _log, "CEAs.run_extraction_llm EXIT facts=%d (%.2fs)", n_facts, elapsed)

        return {"extraction_output": extraction_output}

    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        _log.error("CEAs: extraction LLM produced invalid output: %s", exc)
        if CEA_EXTRACTION_EVENTS:
            CEA_EXTRACTION_EVENTS.labels(status="failure").inc()
        return {"extraction_output": None, "error": f"Invalid extraction output: {exc}"}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _log.error("CEAs: extraction LLM call failed: %s", exc)
        if CEA_EXTRACTION_EVENTS:
            CEA_EXTRACTION_EVENTS.labels(status="failure").inc()
        return {"extraction_output": None, "error": f"Extraction LLM error: {exc}"}


async def dispatch_extraction_results(state: CEAsExtractionState) -> dict:
    """Process structured extraction output and write to stores.

    REQ-CEA-S05: Dispatch NEW/DUPLICATE/SUPERSEDES/CONFLICTS.
    REQ-CEA-S09: Original utterance stored in metadata.
    REQ-CEA-S10: user_id from message sender.
    REQ-CEA-I05: Extraction model identity (fix #S6).
    REQ-CEA-A03: Idempotent dispatch.
    """
    config = state["config"]
    t0 = time.monotonic()
    verbose_log(config, _log, "CEAs.dispatch_results ENTER conv=%s", state["conversation_id"])

    extraction = state.get("extraction_output")
    if not extraction:
        verbose_log(config, _log, "CEAs.dispatch_results EXIT (no extraction output)")
        if CEA_EXTRACTION_EVENTS:
            CEA_EXTRACTION_EVENTS.labels(status="skipped").inc()
        return {"dispatch_results": {"new": 0, "duplicate": 0, "supersedes": 0, "conflicts": 0}}

    from context_broker_ae.memory.quality_wrapper import get_quality_wrapper, resolve_temporal
    wrapper = await get_quality_wrapper(config)

    # Get extraction model identity (REQ-CEA-I05, fix #S6)
    extraction_config = config.get("extraction", config.get("llm", {}))
    extraction_model = extraction_config.get("model", "unknown")
    extraction_date = datetime.now(timezone.utc)

    counts = {"new": 0, "duplicate": 0, "supersedes": 0, "conflicts": 0}
    facts = extraction.get("facts", [])

    for fact in facts:
        relationship = fact.get("relationship", "NEW").upper()

        if relationship == "DUPLICATE":
            counts["duplicate"] += 1
            if CEA_FACTS_EXTRACTED:
                CEA_FACTS_EXTRACTED.labels(relationship="DUPLICATE").inc()
            continue

        content = fact.get("content", "")
        if not content:
            continue

        try:
            user_id = fact.get("user_id", "unknown")
            conversation_id = state["conversation_id"]
            original_utterance = fact.get("original_utterance", "")
            expires_at = resolve_temporal(fact.get("expires_at"), extraction_date)

            add_result = await wrapper.add(
                content=content,
                user_id=user_id,
                conversation_id=conversation_id,
                skip_graph=False,
                dedup_utterance=original_utterance,
            )

            memory_id = add_result.get("memory_id")
            if not memory_id:
                continue

            durability = float(fact.get("durability") or 0.5)
            confidence = float(fact.get("confidence") or 0.5)
            source_type = fact.get("source_type", "observation")
            fact_content_hash = hashlib.md5(content.encode()).hexdigest()

            # Write quality metadata for the vector fact
            await wrapper.write_metadata(
                target_type="fact",
                target_id=memory_id,
                durability=durability,
                confidence=confidence,
                source_type=source_type,
                original_utterance=original_utterance,
                extraction_model=extraction_model,
                expires_at=expires_at,
                user_id=user_id,
                conversation_id=conversation_id,
                content_hash=fact_content_hash,
            )

            # Write quality metadata for any graph relations created by Mem0 (REQ-CEA-Q01)
            for rel_id in add_result.get("relation_ids", []):
                await wrapper.write_metadata(
                    target_type="relation",
                    target_id=rel_id,
                    durability=durability,
                    confidence=confidence,
                    source_type=source_type,
                    original_utterance=original_utterance,
                    extraction_model=extraction_model,
                    expires_at=expires_at,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    content_hash=fact_content_hash,
                )

            if relationship == "NEW":
                counts["new"] += 1
                if CEA_FACTS_EXTRACTED:
                    CEA_FACTS_EXTRACTED.labels(relationship="NEW").inc()

            elif relationship == "SUPERSEDES":
                counts["supersedes"] += 1
                related_id = fact.get("related_fact_id")
                if related_id:
                    await wrapper.record_feedback(
                        target_type="fact",
                        target_id=related_id,
                        event_type="superseded",
                        agent_id="ceas",
                        context={"replacement_id": memory_id},
                    )
                if CEA_FACTS_EXTRACTED:
                    CEA_FACTS_EXTRACTED.labels(relationship="SUPERSEDES").inc()

            elif relationship == "CONFLICTS":
                counts["conflicts"] += 1
                related_id = fact.get("related_fact_id")
                if related_id:
                    await wrapper.record_feedback(
                        target_type="fact",
                        target_id=related_id,
                        event_type="conflicted",
                        agent_id="ceas",
                        context={"conflicting_id": memory_id},
                    )
                    await wrapper.record_feedback(
                        target_type="fact",
                        target_id=memory_id,
                        event_type="conflicted",
                        agent_id="ceas",
                        context={"conflicting_id": related_id},
                    )
                if CEA_FACTS_EXTRACTED:
                    CEA_FACTS_EXTRACTED.labels(relationship="CONFLICTS").inc()

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("CEAs: failed to dispatch fact '%s': %s", content[:50], exc)
            continue

    # Graph triples are handled by Mem0 internally when skip_graph=False.
    # The wrapper.add() call passes content to Mem0, which runs its own entity
    # extraction pipeline and writes triples to Neo4j.

    elapsed = time.monotonic() - t0
    verbose_log(
        config, _log,
        "CEAs.dispatch_results EXIT new=%d dup=%d sup=%d con=%d (%.2fs)",
        counts["new"], counts["duplicate"], counts["supersedes"],
        counts["conflicts"], elapsed,
    )

    if CEA_EXTRACTION_EVENTS:
        CEA_EXTRACTION_EVENTS.labels(status="success").inc()

    return {"dispatch_results": counts}


async def handle_error(state: CEAsExtractionState) -> dict:
    """Log extraction error; compaction continues without extraction (REQ-CEA-S06)."""
    error = state.get("error")
    if error:
        _log.error("CEAs extraction failed (compaction continues): %s", error)
    if CEA_EXTRACTION_EVENTS:
        CEA_EXTRACTION_EVENTS.labels(status="failure").inc()
    return {"dispatch_results": {"error": error}}


def _should_dispatch(state: CEAsExtractionState) -> str:
    """Route: dispatch results or handle error."""
    if state.get("error") or not state.get("extraction_output"):
        return "handle_error"
    return "dispatch_extraction_results"


# Compiled flow singleton (fix #15 — don't recompile per chunk)
_compiled_flow = None


def build_cea_extraction_flow():
    """Build and compile the CEAs extraction StateGraph. Cached singleton."""
    global _compiled_flow
    if _compiled_flow is not None:
        return _compiled_flow

    workflow = StateGraph(CEAsExtractionState)

    workflow.add_node("search_existing_facts", search_existing_facts)
    workflow.add_node("run_extraction_llm", run_extraction_llm)
    workflow.add_node("dispatch_extraction_results", dispatch_extraction_results)
    workflow.add_node("handle_error", handle_error)

    workflow.set_entry_point("search_existing_facts")
    workflow.add_edge("search_existing_facts", "run_extraction_llm")
    workflow.add_conditional_edges("run_extraction_llm", _should_dispatch)
    workflow.add_edge("dispatch_extraction_results", END)
    workflow.add_edge("handle_error", END)

    _compiled_flow = workflow.compile()
    return _compiled_flow
