"""
CEAc Enrichment Flow -- client-side agentic StateGraph subgraph (ReAct pattern).

Sits between get_context and agent response generation. Searches the
vector store and knowledge graph via injected tool callables (no AE imports),
ranks results using the four-dimension memory model, assembles enriched
context, and records feedback events.

The CEAc has ZERO imports from context_broker_ae. It communicates with the
server exclusively through tool callables injected in the state. For the CB's
own Imperator, these callables wrap the MCP tool dispatch. For external agents,
they would wrap MCP protocol calls. (ERQ-002 S12.3: TE package independence.)

See PRD REQ-CEA-C01 through C06.
See HLD S3 Flow B: Enrichment (Read Side).
"""

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine, Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

_log = logging.getLogger("context_broker.cea.enrichment")

# Prometheus metrics (REQ-CEA-A06)
try:
    from prometheus_client import Counter, Histogram

    CEAC_ENRICHMENT_DURATION = Histogram(
        "ceac_enrichment_duration_seconds",
        "CEAc enrichment pipeline duration",
    )
    CEAC_SEARCH_COUNT = Counter(
        "ceac_search_count_total",
        "Total CEAc search iterations",
    )
    CEAC_FEEDBACK_EVENTS = Counter(
        "ceac_feedback_events_total",
        "Total CEAc feedback events recorded",
        ["event_type"],
    )
except ImportError:
    CEAC_ENRICHMENT_DURATION = None
    CEAC_SEARCH_COUNT = None
    CEAC_FEEDBACK_EVENTS = None


# Type aliases for the injected tool callables
SearchFn = Callable[..., Coroutine[Any, Any, dict]]
FeedbackFn = Callable[..., Coroutine[Any, Any, bool]]
LlmFn = Callable[..., Coroutine[Any, Any, str]]  # async (prompt) -> response text


class CEAcEnrichmentState(TypedDict):
    tiers: list[dict]  # tiered context from get_context
    query: str  # agent's current query/context
    user_id: Optional[str]  # user_id for scoped search (fix #22)
    search_fn: SearchFn  # injected: async (query, user_id, limit) -> dict
    feedback_fn: FeedbackFn  # injected: async (target_type, target_id, event_type, agent_id, context) -> bool
    llm_fn: Optional[LlmFn]  # injected: async (prompt) -> response text. For query refinement.
    search_results: list[dict]  # accumulated raw results from all iterations
    search_queries: list[str]  # queries tried so far (for agentic loop)
    ranked_results: list[dict]  # after ranking
    enriched_context: str  # final assembled output
    feedback_events: list[dict]  # events to record
    iteration_count: int
    config: dict


# ------------------------------------------------------------------
# Ranking helpers (pure functions, no imports)
# ------------------------------------------------------------------

def _compute_memory_quality(durability: float, usefulness_data: dict, config: dict) -> float:
    """Blend durability and usefulness based on feedback volume.

    REQ-CEA-C06: At zero feedback events, memory_quality = durability.
    As feedback accumulates, usefulness signal dominates.
    """
    cea_config = config.get("cea", {}).get("ranking", {})
    crossover = cea_config.get("usefulness_crossover_events", 10)

    total_events = usefulness_data.get("total_events", 0)
    if total_events == 0:
        return durability

    used = usefulness_data.get("used", 0)
    discarded = usefulness_data.get("discarded", 0)
    contradicted = usefulness_data.get("contradicted", 0)
    superseded = usefulness_data.get("superseded", 0)
    invalidated = usefulness_data.get("invalidated", 0)
    conflicted = usefulness_data.get("conflicted", 0)

    positive = used
    negative = (
        discarded * 0.3
        + contradicted * 0.8
        + superseded * 0.5
        + invalidated * 0.9
        + conflicted * 0.4
    )
    usefulness = positive / (positive + negative + 1.0)

    usefulness_weight = min(1.0, total_events / crossover)
    durability_weight = 1.0 - usefulness_weight

    return durability * durability_weight + usefulness * usefulness_weight


def _compute_trustworthiness(confidence: float, source_type: str, config: dict) -> float:
    """REQ-CEA-Q02: trustworthiness = source_type_weight * confidence."""
    weights = config.get("cea", {}).get("source_type_weights", {})
    default_weights = {
        "decision": 1.0,
        "instruction": 0.95,
        "preference": 0.9,
        "observation": 0.8,
        "speculation": 0.5,
    }
    source_weight = weights.get(source_type, default_weights.get(source_type, 0.7))
    return source_weight * confidence


def _rank_results(results: list[dict], config: dict) -> list[dict]:
    """Rank: score = relevance * trustworthiness * memory_quality.

    REQ-CEA-C03: Ranking formula.
    REQ-CEA-C04: Cold-start exploration.
    """
    cea_config = config.get("cea", {}).get("ranking", {})
    exploration_rate = cea_config.get("exploration_rate", 0.1)
    min_feedback = cea_config.get("exploration_min_feedback", 3)
    trust_min = cea_config.get("trustworthiness_min_threshold", 0.1)

    scored = []
    underexplored = []

    for r in results:
        meta = r.get("_metadata", {})
        usefulness = r.get("_usefulness", {})

        durability = meta.get("durability", 0.5)
        confidence = meta.get("confidence", 0.5)
        source_type = meta.get("source_type", "observation")
        relevance = r.get("score", 0.5)

        trustworthiness = _compute_trustworthiness(confidence, source_type, config)
        if trustworthiness < trust_min:
            continue

        memory_quality = _compute_memory_quality(durability, usefulness, config)
        score = relevance * trustworthiness * memory_quality

        total_events = usefulness.get("total_events", 0)
        if total_events < min_feedback:
            underexplored.append({**r, "_score": score})
        else:
            scored.append({**r, "_score": score})

    scored.sort(key=lambda x: x["_score"], reverse=True)
    underexplored.sort(key=lambda x: x["_score"], reverse=True)

    if exploration_rate > 0 and underexplored:
        n_explore = max(1, int(len(scored) * exploration_rate / (1.0 - exploration_rate + 0.001)))
        n_explore = min(n_explore, len(underexplored))
        final = scored + underexplored[:n_explore]
        final.sort(key=lambda x: x["_score"], reverse=True)
        return final

    return scored


# ------------------------------------------------------------------
# StateGraph nodes (ReAct agentic loop — fix #2)
# ------------------------------------------------------------------

async def decide_search(state: CEAcEnrichmentState) -> dict:
    """Agent node: decide what to search for based on tiers, query, and
    prior search results. Uses LLM for agentic query formulation.

    REQ-CEA-C02: "Decides what to search for based on context and query."
    REQ-CEA-A01: Agent node in ReAct pattern.
    """
    config = state["config"]
    t0 = time.monotonic()

    query = state.get("query", "")
    prior_queries = state.get("search_queries", [])
    prior_results = state.get("search_results", [])
    iteration = state.get("iteration_count", 0)

    # First iteration: derive query from user input or tier context
    if iteration == 0:
        if not query and state.get("tiers"):
            for msg in reversed(state.get("tiers", [])):
                if msg.get("role") == "user" and msg.get("content"):
                    query = msg["content"][:500]
                    break
    else:
        # Subsequent iterations: LLM decides next query based on what was found.
        llm_fn = state.get("llm_fn")
        if not llm_fn:
            # No LLM callable provided — skip refinement, go to assembly
            return {"query": "", "search_queries": prior_queries}

        try:
            prior_summary = "\n".join(
                f"- {r.get('memory', r.get('source', ''))[:100]}"
                for r in prior_results[:10]
            )
            prior_queries_text = ", ".join(f'"{q}"' for q in prior_queries)

            prompt = (
                f"You are refining a knowledge search. The user's query is: \"{state.get('query', '')}\"\n"
                f"Previous search queries tried: {prior_queries_text}\n"
                f"Results so far ({len(prior_results)} items):\n{prior_summary}\n\n"
                f"Based on what was found, suggest ONE follow-up search query that would find "
                f"additional relevant knowledge not yet retrieved. Return ONLY the query string, "
                f"nothing else. If the existing results are sufficient, return DONE."
            )

            new_query = (await llm_fn(prompt)).strip().strip('"')

            if new_query.upper() == "DONE" or not new_query:
                # LLM says results are sufficient — skip to assembly
                elapsed = time.monotonic() - t0
                _log.debug("CEAc.decide_search: LLM says DONE (%.2fs)", elapsed)
                return {"query": "", "search_queries": prior_queries}

            query = new_query
        except Exception as exc:
            _log.warning("CEAc.decide_search: LLM query refinement failed: %s", exc)
            # Fall through with empty query to trigger assembly
            return {"query": "", "search_queries": prior_queries}

    elapsed = time.monotonic() - t0
    _log.debug("CEAc.decide_search query=%s iter=%d (%.2fs)", query[:80], iteration, elapsed)
    return {"query": query, "search_queries": (prior_queries or []) + [query]}


async def execute_search(state: CEAcEnrichmentState) -> dict:
    """Tool node: execute search via injected search_fn (no AE imports).

    REQ-CEA-C02: "Executes searches via the CB's MCP tools."
    """
    t0 = time.monotonic()
    query = state.get("query", "")
    if not query:
        return {"iteration_count": state.get("iteration_count", 0) + 1}

    cea_config = state.get("config", {}).get("cea", {}).get("ceac", {})
    max_memories = cea_config.get("max_memories", 50)

    search_fn = state.get("search_fn")
    if not search_fn:
        _log.error("CEAc: no search_fn provided in state")
        return {"iteration_count": state.get("iteration_count", 0) + 1}

    try:
        result = await search_fn(
            query=query,
            user_id=state.get("user_id"),  # fix #22: pass user_id for scoped search
            limit=max_memories,
        )
        new_facts = result.get("vector_facts", [])
        new_relations = result.get("graph_relations", [])
        new_results = new_facts + new_relations
    except Exception as exc:
        _log.warning("CEAc: search failed: %s", exc)
        new_results = []

    if CEAC_SEARCH_COUNT:
        CEAC_SEARCH_COUNT.inc()

    # Accumulate results across iterations (dedup by ID)
    existing = state.get("search_results", [])
    existing_ids = {r.get("id") or r.get("relation_id") for r in existing}
    for r in new_results:
        rid = r.get("id") or r.get("relation_id")
        if rid and rid not in existing_ids:
            existing.append(r)
            existing_ids.add(rid)

    elapsed = time.monotonic() - t0
    _log.debug("CEAc.execute_search new=%d total=%d (%.2fs)", len(new_results), len(existing), elapsed)
    return {
        "search_results": existing,
        "iteration_count": state.get("iteration_count", 0) + 1,
    }


async def evaluate_and_rank(state: CEAcEnrichmentState) -> dict:
    """Rank results and apply token budget.

    REQ-CEA-C03: Ranking formula.
    REQ-CEA-C04: Cold-start exploration.
    REQ-CEA-C06: Memory quality at query time.
    """
    config = state["config"]
    results = state.get("search_results", [])

    ranked = _rank_results(results, config)

    cea_config = config.get("cea", {}).get("ceac", {})
    max_token_budget = cea_config.get("max_token_budget", 8000)

    budget_used = 0
    within_budget = []
    for r in ranked:
        text = r.get("memory", r.get("source", ""))
        tokens_est = len(text) // 4 + 1
        if budget_used + tokens_est > max_token_budget:
            break
        within_budget.append(r)
        budget_used += tokens_est

    return {"ranked_results": within_budget}


async def assemble_context(state: CEAcEnrichmentState) -> dict:
    """Format vector facts and graph relations using the output template.

    REQ-CEA-I07: Configurable output template.
    REQ-CEA-C05: Build feedback events with query context (fix #21).
    """
    ranked = state.get("ranked_results", [])

    vector_facts = []
    graph_relations = []
    for r in ranked:
        if "relationship" in r and "destination" in r:
            graph_relations.append(r)
        else:
            vector_facts.append(r)

    facts_text = ""
    for f in vector_facts:
        memory = f.get("memory", "")
        score = f.get("_score", 0)
        facts_text += f"- [{score:.2f}] {memory}\n"
    if not facts_text:
        facts_text = "(none)\n"

    rels_text = ""
    for r in graph_relations:
        source = r.get("source", "?")
        rel = r.get("relationship", "?")
        dest = r.get("destination", "?")
        rels_text += f"- {source} --[{rel}]--> {dest}\n"
    if not rels_text:
        rels_text = "(none)\n"

    # Load template — use fallback if prompt_loader unavailable (TE independence)
    try:
        from app.prompt_loader import async_load_prompt
        template = await async_load_prompt("cea_output")
        enriched = template.format(vector_facts=facts_text, graph_relations=rels_text)
    except Exception:
        enriched = f"## Retrieved Knowledge\n\n### Facts\n{facts_text}\n### Relationships\n{rels_text}"

    # Build feedback events with query context (fix #21)
    query_context = state.get("query", "")
    feedback = []
    ranked_ids = {r.get("id") or r.get("relation_id") for r in ranked}
    for r in state.get("search_results", []):
        rid = r.get("id") or r.get("relation_id")
        if not rid:
            continue
        target_type = "relation" if r.get("relation_id") else "fact"
        event_type = "used" if rid in ranked_ids else "discarded"
        feedback.append({
            "target_type": target_type,
            "target_id": rid,
            "event_type": event_type,
            "context": {"query": query_context},
        })

    return {"enriched_context": enriched, "feedback_events": feedback}


async def record_feedback(state: CEAcEnrichmentState) -> dict:
    """Record used/discarded events via injected feedback_fn (no AE imports).

    REQ-CEA-C05: Feedback event log.
    """
    events = state.get("feedback_events", [])
    if not events:
        return {}

    feedback_fn = state.get("feedback_fn")
    if not feedback_fn:
        _log.warning("CEAc: no feedback_fn provided — skipping feedback recording")
        return {}

    for event in events:
        try:
            await feedback_fn(
                target_type=event["target_type"],
                target_id=event["target_id"],
                event_type=event["event_type"],
                agent_id="ceac",
                context=event.get("context"),
            )
            if CEAC_FEEDBACK_EVENTS:
                CEAC_FEEDBACK_EVENTS.labels(event_type=event["event_type"]).inc()
        except Exception as exc:
            _log.warning("CEAc: feedback recording failed for %s: %s", event["target_id"], exc)

    _log.debug("CEAc.record_feedback recorded %d events", len(events))
    return {}


def _should_iterate(state: CEAcEnrichmentState) -> str:
    """ReAct routing: search again or proceed to assembly.

    Iterates when: under iteration limit AND query is non-empty (LLM decided
    to search again). Stops when: LLM returned DONE (empty query), or at limit.
    """
    cea_config = state.get("config", {}).get("cea", {}).get("ceac", {})
    max_iterations = cea_config.get("max_iterations", 3)
    iteration = state.get("iteration_count", 0)

    if iteration >= max_iterations:
        return "assemble_context"

    # If query is empty, LLM said DONE — proceed to assembly
    if not state.get("query"):
        return "assemble_context"

    # After first search, always give the LLM a chance to refine —
    # especially valuable when zero results were found.
    if iteration > 0:
        return "decide_search"

    return "assemble_context"


_compiled_ceac_flow = None


def build_ceac_enrichment_flow():
    """Build and compile the CEAc enrichment StateGraph (ReAct pattern).

    Cached singleton — compiled once, reused across invocations.
    """
    global _compiled_ceac_flow
    if _compiled_ceac_flow is not None:
        return _compiled_ceac_flow

    workflow = StateGraph(CEAcEnrichmentState)

    workflow.add_node("decide_search", decide_search)
    workflow.add_node("execute_search", execute_search)
    workflow.add_node("evaluate_and_rank", evaluate_and_rank)
    workflow.add_node("assemble_context", assemble_context)
    workflow.add_node("record_feedback", record_feedback)

    workflow.set_entry_point("decide_search")
    workflow.add_edge("decide_search", "execute_search")
    workflow.add_edge("execute_search", "evaluate_and_rank")
    workflow.add_conditional_edges("evaluate_and_rank", _should_iterate)
    workflow.add_edge("assemble_context", "record_feedback")
    workflow.add_edge("record_feedback", END)

    _compiled_ceac_flow = workflow.compile()
    return _compiled_ceac_flow
