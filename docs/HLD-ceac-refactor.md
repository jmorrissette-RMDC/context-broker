# HLD: CEAc Enrichment Flow Refactor

**Status:** Draft
**Date:** 2026-04-08
**PRD:** `docs/PRD-ceac-refactor.md`
**Issue:** rmdevpro/Joshua26#551

---

## 1. Overview

The CEAc enrichment flow is a LangGraph StateGraph subgraph in the TE package. It implements a ReAct pattern: decide what to search → search → rank → iterate or assemble → record feedback.

This refactor fixes 5 requirement violations without changing the graph topology. The graph structure, node count, edge connections, and conditional routing remain identical.

---

## 2. Graph Structure (unchanged)

```
decide_search → execute_search → evaluate_and_rank ──(conditional)──→ decide_search (loop)
                                                     \──→ assemble_context → record_feedback → END
```

**Nodes:** 5
- `decide_search` — Agent node: formulates search query from tiers + prior results
- `execute_search` — Tool node: calls injected `search_fn`, deduplicates results
- `evaluate_and_rank` — Ranking node: scores, filters by token budget
- `assemble_context` — Formatting node: builds enriched context string
- `record_feedback` — Side-effect node: records used/discarded events

**Conditional edge:** `_should_iterate` on `evaluate_and_rank`
- `iteration_count >= max_iterations` OR empty query → `assemble_context`
- Otherwise → `decide_search` (loop back)

---

## 3. State Schema

```python
class CEAcEnrichmentState(TypedDict):
    tiers: list[dict]                    # tiered context from get_context
    query: str                           # current search query
    user_id: Optional[str]               # scoped search identity
    search_fn: SearchFn                  # injected: async (query, user_id, limit) -> dict
    feedback_fn: FeedbackFn              # injected: async (target_type, target_id, event_type, agent_id, context) -> bool
    llm_fn: Optional[LlmFn]             # injected: async (prompt) -> str
    template_fn: Optional[TemplateFn]    # NEW: injected: async (name) -> str
    search_results: list[dict]           # accumulated results (immutable per node)
    search_queries: list[str]            # queries tried
    ranked_results: list[dict]           # after ranking + budget filter
    enriched_context: str                # final output
    feedback_events: list[dict]          # events to record
    iteration_count: int                 # loop counter
    config: dict                         # CEAc configuration
```

**New field:** `template_fn` — async callable that loads a named prompt template. Injected by the Imperator flow using `ctx.async_load_prompt` from the TEContext protocol. Optional with hardcoded fallback.

**Type alias** (placed at `cea_enrichment_flow.py:56`, alongside `SearchFn`, `FeedbackFn`, `LlmFn`):
```python
TemplateFn = Callable[[str], Awaitable[str]]  # async (template_name) -> template_string
```

---

## 4. Node Specifications

### 4.1 `decide_search` (unchanged)

Formulates a search query. On first iteration, extracts query from user message in `tiers`. On subsequent iterations, uses `llm_fn` to refine based on prior results.

**Internal loops (pure computation, acceptable per REQ-CEAC-R04):**
- Reversed scan of `tiers` to find first user message
- Summary of prior results for LLM prompt

### 4.2 `execute_search` (Change 5.5)

Calls `search_fn` with current query. Deduplicates new results against existing.

**Change:** Replace in-place mutation of `search_results` list with copy-then-append:
```python
# Before (violates §2.2):
existing = state.get("search_results", [])
existing.append(r)

# After:
new_results = list(state.get("search_results", []))
new_results.append(r)
return {"search_results": new_results, ...}
```

### 4.3 `evaluate_and_rank` (unchanged)

Calls `_rank_results()` (pure function), then applies token budget filter.

**Internal loops (pure computation, acceptable per REQ-CEAC-R04):**
- Ranking iteration: compute score per result
- Token budget accumulator

### 4.4 `assemble_context` (Change 5.2)

Classifies ranked results into vector facts and graph relations. Formats into enriched context string. Builds feedback events list.

**Change:** Replace `from app.prompt_loader import async_load_prompt` with injected `template_fn`:
```python
# Before (violates §12.3):
from app.prompt_loader import async_load_prompt
template = await async_load_prompt("cea_output")

# After (ERQ-001 §6.1: graceful degradation on template failure):
_fallback = f"## Retrieved Knowledge\n\n### Facts\n{facts_text}\n### Relationships\n{rels_text}"
template_fn = state.get("template_fn")
if template_fn:
    try:
        template = await template_fn("cea_output")
        enriched = template.format(vector_facts=facts_text, graph_relations=rels_text)
    except Exception as exc:
        _log.warning("CEAc: template_fn failed, using fallback: %s", exc)
        enriched = _fallback
else:
    enriched = _fallback
```

**Internal loops (pure computation, acceptable per REQ-CEAC-R04):**
- Result classification (fact vs relation)
- Text formatting
- Feedback event building

### 4.5 `record_feedback` (Change 5.1)

Records used/discarded events for all retrieved memories.

**Change:** Replace sequential I/O loop with concurrent dispatch:
```python
# Before (violates §2.1):
for event in events:
    try:
        await feedback_fn(...)
    except Exception:
        log warning

# After:
async def _record_one(event):
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

await asyncio.gather(*(_record_one(e) for e in events))
```

Per-event try/except in the helper preserves fault isolation (REQ-CEAC-R05).

---

## 5. Invocation Site Changes

**File:** `imperator_flow.py`, CEAc invocation block (~line 435-449)

### 5.3 Fix `_llm_fn` AE import

```python
# Before (violates §12.3):
async def _llm_fn(prompt, **kwargs):
    from app.config import get_chat_model
    llm = get_chat_model(config, "imperator")
    ...

# After:
async def _llm_fn(prompt, **kwargs):
    llm = ctx.get_chat_model(config, "imperator")
    ...
```

`ctx` is already in scope (obtained via `get_ctx()` earlier in the function).

### 5.4 Wire `template_fn`

Add to the CEAc invocation state dict:
```python
ceac_result = await ceac_flow.ainvoke({
    ...
    "template_fn": ctx.async_load_prompt,  # NEW
    ...
})
```

---

## 6. Configuration

Enable CEAc in `config-test/te.yml`:
```yaml
cea:
  ceac:
    enabled: true
    max_iterations: 3
    max_memories: 50
    max_token_budget: 8000
```

No new configuration parameters introduced by this refactor. All existing CEAc config parameters remain unchanged.

---

## 7. Dependency Injection Pattern

All CEAc external dependencies are injected via state callables:

| Callable | Source | Protocol Method | Purpose |
|----------|--------|-----------------|---------|
| `search_fn` | closure over `ctx.dispatch_tool("knowledge_search", ...)` | `TEContext.dispatch_tool` | Search vector store + knowledge graph |
| `feedback_fn` | closure over `ctx.dispatch_tool("knowledge_feedback", ...)` | `TEContext.dispatch_tool` | Record feedback events |
| `llm_fn` | closure over `ctx.get_chat_model(config, "imperator")` | `TEContext.get_chat_model` | LLM for query refinement |
| `template_fn` | `ctx.async_load_prompt` | `TEContext.async_load_prompt` | Load prompt templates |

After this refactor, `cea_enrichment_flow.py` has zero imports from `app.*` or `context_broker_ae.*`. All external access goes through the TEContext protocol.
