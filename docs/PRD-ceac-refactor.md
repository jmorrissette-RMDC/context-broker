# PRD: CEAc Enrichment Flow Refactor

**Status:** Draft
**Date:** 2026-04-08
**Issue:** rmdevpro/Joshua26#551
**Subsumes:** #522 (V-06), #526 (V-10), #527 (V-11), #529 (V-13)

---

## 1. Purpose

Refactor the CEAc (Client-side Context Engineering Architecture) enrichment flow to comply with ERQ-002 §2.1 (StateGraph Mandate), ERQ-002 §2.2 (State Immutability), and ERQ-002 §12.3 (TE Package Independence).

The CEAc has the correct high-level graph structure (5 nodes, conditional iteration loop) but violates requirements inside the nodes. This refactor fixes the violations without changing the graph topology.

CEAc has never been enabled or tested live. After this refactor, it will be enabled and integration-tested for the first time.

---

## 2. Background

The CEAc enrichment flow sits between `get_context` and the Imperator's LLM call. When enabled, it searches the vector store and knowledge graph, ranks results using a four-dimension memory model, assembles enriched context, and records feedback events.

A 3-CLI requirements audit (2026-04-07) identified 33 violations across the CEA implementation. Five of those are in the CEAc flow:

| Violation | Requirement | Description |
|-----------|-------------|-------------|
| V-06 | ERQ-002 §2.1 | `record_feedback` has for-event loop with sequential async I/O |
| V-10 | ERQ-002 §2.1 | Loops inside every CEAc node |
| V-11 | ERQ-002 §12.3 | `cea_enrichment_flow.py` imports `app.prompt_loader` (AE module) |
| V-13 | ERQ-002 §2.2 | `execute_search` mutates `state["search_results"]` in place |
| — | ERQ-002 §12.3 | `_llm_fn` at invocation site imports `app.config` (AE module) |

A 3-CLI design review (2026-04-08) confirmed the refactor plan and identified the additional `_llm_fn` and state mutation violations.

---

## 3. Requirements

### REQ-CEAC-R01: No sequential I/O loops in nodes
`record_feedback` must not iterate over events with sequential `await` calls. Independent I/O operations must be dispatched concurrently. A single event failure must not cancel other events.

### REQ-CEAC-R02: TE package independence
The CEAc flow (`cea_enrichment_flow.py`) must have zero imports from `app.*` or `context_broker_ae.*`. All external dependencies must be injected via state callables. The CEAc invocation site in `imperator_flow.py` must use the TEContext protocol (`ctx.*`) for all external access, not direct AE imports.

### REQ-CEAC-R03: State immutability
Node functions must not modify input state in place. Each node returns a new dictionary with updated values. Lists from state must be copied before modification.

### REQ-CEAC-R04: Pure computation loops acceptable
Loops that perform pure in-memory computation (no I/O, no side effects, no flow control) are acceptable within nodes. This includes: scoring math, text formatting, token counting, set membership checks, find-first patterns. Per ERQ-002 §2.1's intent, these are equivalent to function calls — they transform data within a single conceptual step.

### REQ-CEAC-R05: Fault isolation in concurrent operations
When multiple independent I/O operations are dispatched concurrently (e.g., feedback events), failure of one operation must not prevent others from completing. Failures must be logged per-operation.

### REQ-CEAC-R06: Enable and integration-test CEAc
After refactoring, CEAc must be enabled in the test stack configuration and validated end-to-end: search, ranking, context injection, feedback recording, iteration loop, and feature toggle.

---

## 4. Scope

### In scope
- Fix `record_feedback` sequential I/O loop (REQ-CEAC-R01)
- Fix `app.prompt_loader` import in `assemble_context` (REQ-CEAC-R02)
- Fix `app.config` import in `_llm_fn` at invocation site (REQ-CEAC-R02)
- Fix `execute_search` state mutation (REQ-CEAC-R03)
- Add `template_fn` injectable to state schema (REQ-CEAC-R02)
- Enable CEAc in test config (REQ-CEAC-R06)
- Write mock and live integration tests (REQ-CEAC-R06)

### Out of scope
- Blanket `except Exception` handlers (tracked as #533, applies to all CEA code, not CEAc-specific)
- `_should_iterate` routing simplification (latent architectural smell, not a requirement violation)
- Feedback query context empty after DONE (minor edge case, not a requirement violation)
- Other CEA violations (#519-#543) — tracked separately

---

## 5. Changes

### 5.1 Fix `record_feedback` concurrent dispatch

**Current:** `for event in events: await feedback_fn(...)` — sequential async I/O.

**After:** `asyncio.gather` over a `_record_one(event)` helper coroutine. The helper wraps each call in try/except to preserve per-event fault isolation. Events are independent and bounded by `max_memories` config (default 50).

### 5.2 Inject `template_fn` into state

**Current:** `assemble_context` imports `from app.prompt_loader import async_load_prompt` at line 360.

**After:** New `template_fn: Optional[Callable[[str], Awaitable[str]]]` field on `CEAcEnrichmentState`. `assemble_context` calls `await template_fn("cea_output")` if provided, falls back to hardcoded template if not. Zero AE imports in the file.

### 5.3 Fix `_llm_fn` AE import

**Current:** `imperator_flow.py:429` has `from app.config import get_chat_model`.

**After:** `ctx.get_chat_model(config, "imperator")` — uses the TEContext protocol method that already exists at `_ctx.py:38`.

### 5.4 Wire `template_fn` at invocation site

**Current:** CEAc invocation dict in `imperator_flow.py:435-449` has `search_fn`, `feedback_fn`, `llm_fn` but no `template_fn`.

**After:** Add `"template_fn": ctx.async_load_prompt` — uses the TEContext protocol method already used at `imperator_flow.py:287`.

### 5.5 Fix `execute_search` state mutation

**Current:** `existing = state.get("search_results", [])` then `existing.append(r)` — mutates state list in place.

**After:** `new_results = list(state.get("search_results", []))` then append to the copy. Return the new list in the output dict.

---

## 6. Files Modified

| File | Changes |
|------|---------|
| `packages/context-broker-te/src/context_broker_te/cea_enrichment_flow.py` | 5.1 (record_feedback), 5.2 (template_fn state + assemble_context), 5.5 (execute_search) |
| `packages/context-broker-te/src/context_broker_te/imperator_flow.py` | 5.3 (_llm_fn), 5.4 (template_fn wiring) |
| `config-test/te.yml` | Enable CEAc: `cea.ceac.enabled: true` |
| `tests/claude/test_cea/test_enrichment_flow.py` | New mock tests |
| `tests/claude/live/test_phase_f_imperator.py` | New live integration tests |

---

## 7. Test Plan

### Mock tests (new)

| # | Test | Verifies |
|---|------|----------|
| M1 | `test_record_feedback_concurrent` | 10 events with 0.1s mock delay complete in ~0.1s, not 1s | REQ-CEAC-R01 |
| M2 | `test_record_feedback_partial_failure` | 1 of 5 events fails, other 4 succeed | REQ-CEAC-R05 |
| M3 | `test_template_fn_injected` | `assemble_context` calls `template_fn("cea_output")` | REQ-CEAC-R02 |
| M4 | `test_template_fn_missing_uses_fallback` | Omit `template_fn`, fallback template used | REQ-CEAC-R02 |
| M5 | `test_execute_search_does_not_mutate_state` | Original `search_results` list unchanged | REQ-CEAC-R03 |
| M6 | `test_ceac_empty_knowledge_store` | Zero search results, graceful handling | REQ-CEAC-R06 |
| M7 | `test_ceac_user_id_passed_to_search` | `search_fn` receives `user_id` from state | REQ-CEAC-R06 |

### Live integration tests (new)

| # | Test | Verifies |
|---|------|----------|
| L1 | `test_ceac_searches_during_conversation` | Seed fact, ask question, Imperator response references it | REQ-CEAC-R06 |
| L2 | `test_ceac_enriched_context_contains_ranked_facts` | Seed multiple facts, verify ranked order in response | REQ-CEAC-R06 |
| L3 | `test_ceac_feedback_events_recorded` | After CEAc turn, `cea_feedback_events` has used/discarded entries | REQ-CEAC-R06 |
| L4 | `test_ceac_iteration_refines_search` | Indirect query finds fact via multi-iteration refinement | REQ-CEAC-R06 |
| L5 | `test_ceac_disabled_no_enrichment` | With CEAc disabled, no knowledge search occurs | REQ-CEAC-R06 |

### Regression

All existing tests must pass:
- Mock suite: `pytest tests/claude/ -v --ignore=tests/claude/live/`
- Imperator live tests with CEAc DISABLED: 55/55 (no regression)
- Imperator live tests with CEAc ENABLED: 55 existing + 5 new

---

## 8. Verification

1. `grep -r "from app\." packages/context-broker-te/` returns zero matches after refactor
2. `grep -r "\.append(" cea_enrichment_flow.py` — no in-place state mutation on state fields
3. Mock test M1 proves concurrent dispatch (wall time)
4. Mock test M2 proves fault isolation
5. Live test L1 proves end-to-end CEAc enrichment works
6. Live test L5 proves feature toggle works

---

## 9. Risks

| Risk | Mitigation |
|------|------------|
| CEAc has never run live — unknown failure modes | Extensive mock + live test coverage before enabling |
| `asyncio.gather` error semantics differ from sequential loop | Per-event try/except in helper preserves current behavior |
| Graph singleton cache may conflict with new state field | TypedDict checked at invocation, not compilation — no conflict |
| CEAc adds latency to every Imperator turn when enabled | Controlled by `cea.ceac.enabled` toggle, `max_iterations` limit |
