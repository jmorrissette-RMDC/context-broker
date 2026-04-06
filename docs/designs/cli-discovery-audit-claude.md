# CLI Discovery Audit — CEA Implementation (Claude)

**Date:** 2026-04-04
**Phase:** SDLC-02 Step 8, WPR-103 S1a
**Scope:** New code introduced by the Context Engineering Architecture (CEA) implementation
**Method:** Full source read of every file in the Context Broker codebase; gap analysis against `tests/claude/`

---

## Audit Scope

This audit covers only code added or modified by the CEA implementation. The existing test suite (`tests/claude/`, ~315 tests across 21 files) already provides thorough coverage of the pre-CEA codebase. This document identifies what the new CEA code does and what tests are missing.

### New Files

| File | Package | Purpose |
|------|---------|---------|
| `quality_wrapper.py` | AE / memory | CEA quality metadata, feedback, expiration, global search |
| `cea_extraction_flow.py` | AE | Durable fact extraction from compaction chunks |
| `cea_enrichment_flow.py` | TE | CEAc retrieval-time enrichment with ranking |
| `quality_gate.py` | Mem0 fork | Pre-write quality gate and metadata extraction |

### Modified Files

| File | Change Summary |
|------|---------------|
| `main.py` (Mem0 fork) | `_skip_graph` parameter; payload preservation on update; side-channel metadata extraction |
| `graph_memory.py` (Mem0 fork) | `elementId()` replacing deprecated `id()`; rerank-to-original-row mapping |
| `standard_tiered.py` | `compact_tier1` invokes CEA extraction inline |
| `imperator_flow.py` | `init_context_node` CEAc enrichment branch |
| `stategraph_registry.py` | TE `flows` dict scanning (CEAc registration) |
| `register.py` (AE) | `knowledge_add`, `knowledge_list`, `cea_extraction` flow registration |
| `register.py` (TE) | `flows.ceac_enrichment` export |
| `migrations.py` | Migrations 023, 024 |
| `knowledge_enriched.py` | RAG budget reservation removed; deprecated semantic/KG node removal |

### New Prompts

| File | Purpose |
|------|---------|
| `config/prompts/cea_vector_extraction.md` | Structured fact extraction prompt |
| `config/prompts/cea_graph_extraction.md` | Knowledge graph triple extraction prompt |
| `config/prompts/cea_output.md` | CEAc output formatting template |

---

## 1. quality_wrapper.py — QualityWrapper Class

### 1.1 Module-level Functions

#### `resolve_temporal(expires_at_raw, extraction_date) -> Optional[datetime]`
- **What it does:** Parses ISO 8601 strings or relative references ("in 3 days", "next week", "2 months") into absolute datetimes.
- **Tests needed:**
  - Success: ISO string, relative "in N days", "next week", "N months"
  - Edge: None input returns None, empty string returns None
  - Error: Malformed string returns None (not raise)
- **Existing coverage:** None

#### `_fact_dedup_key(content, conversation_id, user_id, utterance) -> str`
- **What it does:** SHA256 dedup key from fact fields.
- **Tests needed:**
  - Deterministic: same inputs produce same hash
  - Different inputs produce different hashes
- **Existing coverage:** None

#### `_event_dedup_key(target_id, event_type, agent_id) -> str`
- **What it does:** SHA256 dedup key with timestamp bucketing (60s).
- **Tests needed:**
  - Same inputs within same minute produce same key
  - Different event_type produces different key
- **Existing coverage:** None

#### `_apply_rejection_rules(content) -> bool`
- **What it does:** Checks `cea.rejection_rules.min_fact_length` and regex patterns. Returns True to reject.
- **Tests needed:**
  - Success: content above min_length, no pattern match → False
  - Reject: content below min_length → True
  - Reject: content matches regex pattern → True
  - Edge: empty rejection_rules config → False (accept all)
- **Existing coverage:** None

### 1.2 Write Methods

#### `add(content, user_id, *, conversation_id, metadata, skip_graph, dedup_utterance) -> dict`
- **What it does:** Adds fact via Mem0 with idempotent dedup. Checks `cea_quality_metadata` for existing utterance before calling Mem0. Extracts `memory_id` and `relation_ids` from Mem0 response.
- **Tests needed:**
  - Success: new fact added, returns memory_id and relation_ids
  - Dedup: existing utterance found in metadata table → returns existing target_id, skips Mem0
  - Rejection: `_apply_rejection_rules` rejects → returns empty dict
  - Error: Mem0 `add()` raises → propagation behavior
  - Edge: `skip_graph=True` passed through to Mem0
  - Edge: empty content
- **Existing coverage:** None

#### `write_metadata(target_type, target_id, *, durability, confidence, source_type, original_utterance, extraction_model, expires_at, user_id, conversation_id) -> None`
- **What it does:** INSERT INTO `cea_quality_metadata` with ON CONFLICT DO NOTHING.
- **Tests needed:**
  - Success: row inserted with all fields
  - Idempotent: duplicate (target_type, target_id) → no error, no update
  - Error: invalid UUID conversation_id → caught ValueError
  - Error: asyncpg.UniqueViolationError from concurrent insert → caught
  - Edge: expires_at=None stored correctly
- **Existing coverage:** None

### 1.3 Read Methods

#### `search(query, *, user_id, limit) -> dict`
- **What it does:** Searches Mem0 (user-scoped) or pgvector directly (global). Enriches with metadata and usefulness aggregates. Filters expired facts.
- **Tests needed:**
  - Success: user-scoped search returns enriched results with `_metadata` and `_usefulness`
  - Success: global search (user_id=None) uses `_global_vector_search`
  - Expiration: expired facts filtered from results
  - Degraded: Mem0 unavailable → graceful return
  - Edge: empty results
- **Existing coverage:** None

#### `list_facts(*, user_id, limit) -> list`
- **What it does:** Lists facts via Mem0 `get_all()` or direct pgvector query (global).
- **Tests needed:**
  - Success: user-scoped list returns facts
  - Success: global list queries `mem0_memories` directly
  - Edge: empty results → empty list
- **Existing coverage:** None

### 1.4 Feedback

#### `record_feedback(target_type, target_id, event_type, agent_id, context) -> bool`
- **What it does:** INSERT INTO `cea_feedback_events` with ON CONFLICT dedup via `dedup_key`.
- **Tests needed:**
  - Success: event inserted, returns True
  - Dedup: same event within same timestamp bucket → ON CONFLICT, returns False
  - Error: asyncpg.PostgresError → returns False
- **Existing coverage:** None

### 1.5 Expiration

#### `cleanup_expired() -> int`
- **What it does:** Selects expired rows from `cea_quality_metadata`, deletes from Mem0 and metadata table.
- **Tests needed:**
  - Success: 3 expired facts deleted, returns 3
  - Partial: Mem0 delete fails for one fact → continues, still deletes metadata
  - Edge: no expired facts → returns 0
- **Existing coverage:** None

#### `_maybe_cleanup() -> None`
- **What it does:** Throttled cleanup via `cea.expiration_cleanup_interval` (default 3600s).
- **Tests needed:**
  - Throttle: called twice within interval → only first runs cleanup
  - Fires: called after interval elapsed → runs cleanup
- **Existing coverage:** None

### 1.6 Internal Helpers

#### `_get_metadata_batch(id_pairs) -> dict`
- **What it does:** Batch metadata fetch from `cea_quality_metadata` using unnest arrays.
- **Tests needed:**
  - Success: returns dict keyed by (target_type, target_id)
  - Edge: empty id_pairs → empty dict
- **Existing coverage:** None

#### `_get_usefulness_batch(id_pairs) -> dict`
- **What it does:** Aggregate feedback event counts grouped by event_type.
- **Tests needed:**
  - Success: returns dict with event counts per target
  - Edge: no feedback events → empty counts
- **Existing coverage:** None

#### `_global_vector_search(query, limit) -> dict`
- **What it does:** Direct pgvector ANN search on `mem0_memories` bypassing Mem0's user_id guard.
- **Tests needed:**
  - Success: returns vector_facts list
  - Edge: no embeddings → empty results
- **Existing coverage:** None

#### `_global_graph_search(query, limit) -> list`
- **What it does:** Direct Neo4j cosine similarity search.
- **Tests needed:**
  - Success: returns graph relations
  - Error: bare Exception caught → returns empty list
- **Existing coverage:** None

### 1.7 Singleton Factory

#### `get_quality_wrapper(config=None) -> QualityWrapper`
- **What it does:** Async singleton. Creates QualityWrapper(mem0, pool, config).
- **Tests needed:**
  - Success: returns wrapper instance
  - Singleton: second call returns same instance
  - Error: Mem0 client unavailable → returns None or raises
- **Existing coverage:** None
- **Finding:** No reset mechanism (unlike `reset_mem0_client`). Stale references possible.

---

## 2. cea_extraction_flow.py — CEA Extraction StateGraph

### 2.1 Prometheus Metrics (module-level)
- `CEA_EXTRACTION_EVENTS` — Counter, labels: `["status"]`
- `CEA_EXTRACTION_DURATION` — Histogram
- `CEA_FACTS_EXTRACTED` — Counter, labels: `["relationship"]`

### 2.2 StateGraph Nodes

```
search_existing_facts → run_extraction_llm → (conditional) dispatch_results | handle_error → END
```

#### `search_existing_facts(state) -> dict`
- **What it does:** Searches quality wrapper for pre-existing facts relevant to chunk content (REQ-CEA-S03). Scope: "conversation", "user", or global.
- **Config:** `cea.pre_extraction_fact_limit` (default 20), `cea.retrieval_scope` (default "conversation")
- **Tests needed:**
  - Success: conversation-scoped search returns existing facts as formatted text
  - Success: user-scoped search parses sender from content
  - Success: global scope (no filters)
  - Error: wrapper.search() raises → caught, returns empty existing_facts
  - Edge: no existing facts → empty string
- **Existing coverage:** None

#### `run_extraction_llm(state) -> dict`
- **What it does:** Calls extraction LLM with `cea_vector_extraction` prompt. Parses JSON from response. Injects `{current_date}`, `{existing_facts}`, `{content}`, `{tier2_context}`, `{tier3_context}`.
- **Tests needed:**
  - Success: valid JSON response parsed into extraction_output dict
  - Error: json.JSONDecodeError from LLM → error state, counter incremented
  - Error: LLM call raises → error state, counter incremented
  - Edge: LLM returns markdown-fenced JSON → fences stripped before parse
  - Edge: LLM returns empty facts array → valid empty extraction
- **Existing coverage:** None

#### `dispatch_results(state) -> dict`
- **What it does:** Processes each extracted fact by relationship type:
  - DUPLICATE → counted, skipped
  - NEW → `wrapper.add()`, `wrapper.write_metadata()`, graph relation metadata
  - SUPERSEDES → add + metadata + feedback(`event_type="superseded"`) on superseded fact
  - CONFLICTS → add + metadata + bidirectional feedback(`event_type="conflicted"`)
- **Tests needed:**
  - Success: NEW fact dispatched — add, write_metadata called
  - Success: DUPLICATE fact — skipped, counter incremented
  - Success: SUPERSEDES — add + feedback on old fact
  - Success: CONFLICTS — add + bidirectional feedback
  - Edge: mixed fact types in single extraction
  - Counter: `CEA_FACTS_EXTRACTED` incremented per relationship type
  - Counter: `CEA_EXTRACTION_EVENTS` success on completion
- **Existing coverage:** None

#### `handle_error(state) -> dict`
- **What it does:** Logs error, increments failure counter. Compaction continues (REQ-CEA-S06).
- **Tests needed:**
  - Error logged, counter incremented, returns without raising
- **Existing coverage:** None

#### `_should_dispatch(state) -> str`
- **What it does:** Routes to `dispatch_results` or `handle_error` based on error state.
- **Tests needed:**
  - Route: error present → "handle_error"
  - Route: extraction_output present → "dispatch_results"
- **Existing coverage:** None

#### `build_cea_extraction_flow() -> compiled graph`
- **What it does:** Cached singleton (global `_compiled_flow`).
- **Tests needed:**
  - Singleton: second call returns same compiled graph
  - Graph structure: correct node ordering and edges
- **Existing coverage:** None

---

## 3. cea_enrichment_flow.py — CEAc Enrichment StateGraph

### 3.1 Prometheus Metrics
- `CEAC_ENRICHMENT_DURATION` — Histogram
- `CEAC_SEARCH_COUNT` — Counter
- `CEAC_FEEDBACK_EVENTS` — Counter, label: `event_type`

### 3.2 Pure Ranking Functions

#### `_compute_memory_quality(durability, usefulness_data, config) -> float`
- **What it does:** REQ-CEA-C06. At zero feedback, returns durability. As feedback accumulates, usefulness dominates via crossover weighting.
- **Config:** `cea.ranking.usefulness_crossover_events` (default 10)
- **Tests needed:**
  - Zero feedback: returns durability as-is
  - High positive feedback: usefulness > durability, score shifts toward usefulness
  - Negative feedback types weighted: discarded=0.3, contradicted=0.8, superseded=0.5, invalidated=0.9, conflicted=0.4
  - Crossover: at N=crossover events, weighting is ~50/50
- **Existing coverage:** None

#### `_compute_trustworthiness(confidence, source_type, config) -> float`
- **What it does:** REQ-CEA-Q02. `source_weight * confidence`.
- **Config:** `cea.source_type_weights` (override dict)
- **Tests needed:**
  - Default weights: decision=1.0, instruction=0.95, preference=0.9, observation=0.8, speculation=0.5
  - Custom weight override from config
  - Unknown source_type → fallback=0.7
- **Existing coverage:** None

#### `_rank_results(results, config) -> list[dict]`
- **What it does:** REQ-CEA-C03/C04. Score = `relevance * trustworthiness * memory_quality`. Cold-start exploration mixing. Filters below `trust_min`.
- **Config:** `cea.ranking.exploration_rate` (0.1), `exploration_min_feedback` (3), `trustworthiness_min_threshold` (0.1)
- **Tests needed:**
  - Success: results sorted by composite score descending
  - Filter: results below trust threshold removed
  - Exploration: cold-start items (< min_feedback events) mixed in at exploration_rate
  - Edge: empty results → empty list
  - Edge: all results below threshold → empty list
- **Existing coverage:** None

### 3.3 StateGraph Nodes

```
decide_search → execute_search → evaluate_and_rank → (conditional) assemble_context | decide_search (iterate) → record_feedback → END
```

#### `decide_search(state) -> dict`
- **What it does:** Iteration 0: uses state.query or extracts from tiers. Subsequent: uses LLM to decide next query.
- **Config:** `imperator.base_url`, `imperator.model`, `imperator.api_key`
- **Tests needed:**
  - Iteration 0: query from state.query
  - Iteration 0: no query, extracts from last user message in tiers
  - Iteration N>0: LLM called to refine query
  - LLM returns "DONE" → empty query (triggers assembly)
  - Error: LLM call raises → empty query (graceful)
- **Existing coverage:** None

#### `execute_search(state) -> dict`
- **What it does:** Calls injected `search_fn`, deduplicates results by id/relation_id.
- **Config:** `cea.ceac.max_memories` (default 50)
- **Tests needed:**
  - Success: search_fn called, results accumulated
  - Dedup: duplicate ids across iterations removed
  - Error: no search_fn → logs error, increments iteration
  - Error: search_fn raises → empty results
  - Counter: `CEAC_SEARCH_COUNT` incremented
- **Existing coverage:** None

#### `evaluate_and_rank(state) -> dict`
- **What it does:** Calls `_rank_results`, enforces token budget.
- **Config:** `cea.ceac.max_token_budget` (default 8000)
- **Tests needed:**
  - Success: results ranked and truncated to budget
  - Budget: results exceeding budget truncated (estimated as len//4+1)
  - Edge: empty results → empty ranked_results
- **Existing coverage:** None

#### `assemble_context(state) -> dict`
- **What it does:** Separates vector facts from graph relations, formats with scores. Loads `cea_output` prompt. Builds used/discarded feedback events.
- **Tests needed:**
  - Success: formatted output with vector facts and graph relations sections
  - Feedback: used events for ranked results, discarded events for unranked
  - Error: prompt load fails → hardcoded fallback template used
  - Edge: only vector facts, no graph relations
- **Existing coverage:** None

#### `record_feedback(state) -> dict`
- **What it does:** Iterates feedback events, calls injected `feedback_fn` for each.
- **Tests needed:**
  - Success: all feedback events dispatched
  - Error: individual feedback_fn failure → logged, continues
  - Edge: no feedback_fn → logs warning, returns {}
  - Counter: `CEAC_FEEDBACK_EVENTS` incremented per event
- **Existing coverage:** None

#### `_should_iterate(state) -> str`
- **What it does:** Routes based on iteration count and query state.
- **Config:** `cea.ceac.max_iterations` (default 3)
- **Tests needed:**
  - At max iterations → "assemble_context"
  - Empty query (LLM said DONE) → "assemble_context"
  - Iteration > 0 with query → "decide_search" (refine)
  - After first search (iteration=1) → "assemble_context" (default single-pass)
- **Existing coverage:** None

---

## 4. Migrations 023 and 024

### Migration 023 — CEA quality metadata and feedback events

**Creates `cea_quality_metadata`:**
- Columns: id (SERIAL PK), target_type (TEXT NOT NULL), target_id (TEXT NOT NULL), durability (DOUBLE PRECISION NOT NULL), confidence (DOUBLE PRECISION NOT NULL), source_type (TEXT NOT NULL), original_utterance (TEXT NOT NULL), extraction_model (TEXT NOT NULL), expires_at (TIMESTAMPTZ nullable), extracted_at (TIMESTAMPTZ DEFAULT NOW()), user_id (TEXT NOT NULL), conversation_id (UUID FK)
- UNIQUE on (target_type, target_id)
- Indexes: on user_id, conversation_id, partial on expires_at WHERE NOT NULL

**Creates `cea_feedback_events`:**
- Columns: id (SERIAL PK), target_type (TEXT NOT NULL), target_id (TEXT NOT NULL), event_type (TEXT NOT NULL), agent_id (TEXT NOT NULL), context (JSONB), created_at (TIMESTAMPTZ DEFAULT NOW()), dedup_key (TEXT NOT NULL UNIQUE)
- Index on (target_type, target_id)

**Tests needed:**
- Tables created successfully on fresh DB
- Idempotent: re-running migration does not fail (IF NOT EXISTS)
- UNIQUE constraint on (target_type, target_id) enforced
- UNIQUE constraint on dedup_key enforced
- FK to conversations(id) validated
- Partial index on expires_at only includes non-NULL rows

### Migration 024 — CEA post-review indexes

**Creates:**
- `idx_cea_metadata_natural_dedup`: UNIQUE INDEX on (user_id, conversation_id, original_utterance) WHERE original_utterance != ''
- `idx_cea_events_target_type_id`: INDEX on (target_type, target_id, event_type)

**Tests needed:**
- Natural dedup index prevents same user/conversation/utterance combination
- Empty utterance excluded from dedup index (partial WHERE)
- Composite feedback index created
- Idempotent: IF NOT EXISTS

**Existing coverage:** Neither migration is in `test_database/test_database_gaps.py`.

---

## 5. Modified Code — CEA Integration Points

### 5.1 `compact_tier1()` in standard_tiered.py — CEA Extraction Invocation

**What changed:** After each chunk summarization, invokes `build_cea_extraction_flow().ainvoke()` inline with the chunk content, tier2/tier3 context, and conversation_id.

**Error handling:** `except (ImportError, RuntimeError, ValueError, OSError)` — logged, compaction continues.

**Tests needed:**
- Success: CEA extraction invoked per chunk during compaction
- Error: CEA extraction fails → compaction continues without CEA (REQ-CEA-S06)
- Edge: ImportError (CEA not installed) → graceful skip
- Integration: extraction receives correct content, tier2_context, tier3_context, conversation_id

**Existing coverage:** `test_assembly_guards.py` tests budget guards and partial failure but does NOT test the CEA extraction branch within compact_tier1.

### 5.2 `init_context_node()` in imperator_flow.py — CEAc Integration

**What changed:** When `config.cea.ceac.enabled` is True, builds and invokes `build_ceac_enrichment_flow()` with injected `_search_fn` and `_feedback_fn` that wrap `ctx.dispatch_tool("knowledge_search", ...)` and `ctx.dispatch_tool("knowledge_feedback", ...)`.

**Error handling:** `except (ImportError, RuntimeError, ValueError, OSError)` — logged, continues without enrichment.

**Tests needed:**
- Success: CEAc flow invoked, enriched_context injected as SystemMessage
- Disabled: `cea.ceac.enabled=False` → CEAc not invoked
- Error: CEAc flow raises → continues without enrichment
- Integration: `_search_fn` correctly wraps knowledge_search dispatch
- Integration: `_feedback_fn` correctly wraps knowledge_feedback dispatch
- Edge: CEAc returns empty enriched_context → no SystemMessage injected

**Existing coverage:** `test_imperator_flow_gaps.py` tests init_context_node prompt loading and failure, but does NOT test the CEAc branch.

### 5.3 `stategraph_registry.py` — TE Flows Scanning

**What changed:** Lines 106-108: TE packages can now export a `"flows"` dict (just like AE packages). CEAc enrichment flow registered this way.

**Tests needed:**
- Success: TE package with flows dict → flows registered in `_flow_builders`
- Success: `get_flow_builder("ceac_enrichment")` returns builder after scan
- Edge: TE package without flows dict → no error

**Existing coverage:** None for TE flows scanning specifically.

### 5.4 `register.py` (AE) — New Flow Registrations

**What changed:** Registers `knowledge_add`, `knowledge_list`, `cea_extraction` flows. Deregisters `memory_extraction`, `memory_search`, `memory_context`, `knowledge_delete`.

**Tests needed:**
- `knowledge_add` builder returns compiled graph
- `knowledge_list` builder returns compiled graph
- `cea_extraction` builder returns compiled graph
- Deregistered flows NOT in registry

**Existing coverage:** None specifically for the new registrations.

### 5.5 `register.py` (TE) — CEAc Flow Export

**What changed:** Exports `flows.ceac_enrichment` → `build_ceac_enrichment_flow`.

**Tests needed:**
- `register()` returns dict with `flows.ceac_enrichment` key
- `tools_required` includes `"knowledge_search"` and `"knowledge_feedback"`

**Existing coverage:** None.

### 5.6 `knowledge_enriched.py` — RAG Budget Reservation Removed

**What changed:** Deprecated RAG budget reservation. `ke_load_recent_messages` now uses same logic as standard-tiered. Deprecated `ke_route_after_semantic` (dead code). Removed `ke_inject_semantic_retrieval` and `ke_inject_knowledge_graph` nodes.

**Tests needed:**
- `ke_load_recent_messages` allocates full tier1 budget (no RAG reservation)
- Removal of semantic/KG nodes means graph goes directly from load_recent → assemble_context

**Existing coverage:** None specific to the budget change.

**Finding:** `retrieval_flow.py` (backward-compat shim) still imports `ke_inject_semantic_retrieval` and `ke_inject_knowledge_graph` which no longer exist. This will cause `ImportError` if anything imports `retrieval_flow.py`. Test needed to verify this shim is dead code or to catch the broken import.

---

## 6. Mem0 Fork Changes

### 6.1 `main.py` — `_skip_graph` Parameter

**What changed:** `Memory.add()` accepts `_skip_graph: bool = False`. When True, skips `_add_to_graph()` entirely (CB-24 performance optimization for extraction).

**Tests needed:**
- `_skip_graph=True` → `_add_to_graph` not called
- `_skip_graph=False` (default) → `_add_to_graph` called
- Return value still contains `results` key when graph skipped

**Existing coverage:** None.

### 6.2 `main.py` — Payload Preservation on Update

**What changed:** `Memory._update_memory()` (sync path) preserves ALL existing payload keys, not just session IDs. New metadata with explicit `None` clears a field.

**Tests needed:**
- Update preserves custom metadata keys (e.g., durability, source_type) from existing payload
- Explicit `None` in new metadata removes the key
- Session IDs (user_id, agent_id, run_id) preserved as before

**Existing coverage:** None.

**Finding:** `AsyncMemory._update_memory()` does NOT have this fork behavior — it only preserves session IDs. This is a sync/async asymmetry. Test should verify or document this.

### 6.3 `main.py` — Side-Channel Metadata Extraction

**What changed:** `quality_gate.extract_metadata_from_facts()` is called during `_add_to_vector_store` to extract structured metadata from facts before storage. Metadata is positionally aligned with the facts array.

**Tests needed:**
- Facts with `{"fact": "...", "durability": 0.8}` format → metadata extracted, fact text preserved
- Reserved keys in metadata rejected (logged warning)
- `expires_at` normalized via `_normalize_expires_at`

**Existing coverage:** None.

### 6.4 `graph_memory.py` — elementId() Migration

**What changed:** All Cypher queries use `elementId(n)`, `elementId(r)`, `elementId(m)` instead of deprecated `id()` (Neo4j 5+ compatibility). The `search()` method maps reranked results back to original rows to preserve elementId fields.

**Tests needed:**
- `_search_graph_db` returns source_id, relation_id, destination_id as elementId strings
- `search()` BM25 reranking preserves elementId from original Cypher rows
- `_add_entities` RETURN clauses include `elementId(r)` for relation tracking
- `_search_source_node` / `_search_destination_node` return elementId strings

**Existing coverage:** None.

**Finding:** If BM25 reranking produces a result that doesn't match any original tuple (duplicate entity names), the fallback returns the result without IDs. This edge case needs a test.

### 6.5 `quality_gate.py` — Pre-Write Quality Gate

**What it does:** Post-extraction filter that rejects low-quality facts before vector store write.

#### `extract_metadata_from_facts(raw_facts) -> (facts, metadata_array)`
- **Tests needed:**
  - Plain string facts → empty metadata dicts
  - Dict facts `{"fact": "...", "durability": 0.8}` → text extracted, metadata populated
  - Reserved key rejection → warning logged, key removed
  - `expires_at` normalization: ISO string, unix timestamp, None

#### `apply_quality_gate(facts, metadata, rejection_rules, custom_validator, min_length) -> (surviving, meta, rejected_count)`
- **Tests needed:**
  - Success: all facts pass → full list returned, rejected_count=0
  - Rejection: fact below min_length → rejected
  - Rejection: fact matches regex pattern → rejected
  - Error: invalid regex pattern → warning logged, pattern skipped
  - Custom validator: callable returns reason string → fact rejected

#### `apply_post_update_gate(actions, metadata_by_fact, rejection_rules, ...) -> filtered_actions`
- **Tests needed:**
  - DELETE/NONE actions pass through unchanged
  - ADD action with short fact → rejected
  - UPDATE action with matching pattern → rejected

#### `_normalize_expires_at(value) -> Optional[str]`
- **Tests needed:**
  - None → None
  - ISO string → UTC ISO string
  - ISO string with 'Z' → replaced with +00:00, parsed
  - Unix timestamp (int) → UTC ISO string
  - Unix timestamp (float) → UTC ISO string
  - Invalid string → None (logged warning)

**Existing coverage:** None.

### 6.6 `expiration.py` — Memory Expiration

This module exists pre-CEA but is newly wired into the quality wrapper path.

#### `filter_expired_from_results(memories, now) -> list`
- **Tests needed:**
  - Non-expired memories pass through
  - Expired memories filtered
  - Memories without expires_at pass through

**Existing coverage:** None in `tests/claude/`.

---

## 7. MCP Tool Dispatch Paths

### 7.1 `knowledge_search` (via tool_dispatch.py → memory_search_flow or quality_wrapper)

The `knowledge_search` tool in `imperator_flow.py` dispatches to the `memory_search` flow builder.

**Tests needed:**
- Success: query dispatched, formatted results returned
- Empty results: "No relevant memories found"
- Degraded: Mem0 unavailable → degraded response

**Existing coverage:** `test_memory/test_memory_integration.py` tests the search flow but not the `knowledge_search` tool wrapper in imperator_flow.

### 7.2 `knowledge_add` (registered flow)

Registered in AE `register.py` as `build_mem_add_flow`.

**Tests needed:**
- Success: content added via Mem0, result returned
- Error: Mem0 unavailable → degraded response

**Existing coverage:** None for the registered flow path.

### 7.3 `knowledge_list` (registered flow)

Registered in AE `register.py` as `build_mem_list_flow`.

**Tests needed:**
- Success: memories listed for user
- Error: Mem0 unavailable → degraded response

**Existing coverage:** None for the registered flow path.

### 7.4 `knowledge_feedback` (dispatched by CEAc)

Called by CEAc enrichment flow's `_feedback_fn` → `ctx.dispatch_tool("knowledge_feedback", ...)`.

**Tests needed:**
- Success: feedback event recorded
- Error: dispatch failure → caught by CEAc record_feedback

**Existing coverage:** None. **Finding:** The `knowledge_feedback` tool is dispatched by CEAc but needs to be registered as a flow or handled in tool_dispatch. Verify this path exists end-to-end.

---

## 8. Configuration Parameters (New)

All new CEA configuration parameters and their defaults:

| Parameter | Default | Used By |
|-----------|---------|---------|
| `cea.pre_extraction_fact_limit` | 20 | cea_extraction_flow.search_existing_facts |
| `cea.retrieval_scope` | "conversation" | cea_extraction_flow.search_existing_facts |
| `cea.rejection_rules.min_fact_length` | (from config) | quality_wrapper._apply_rejection_rules |
| `cea.rejection_rules` | (regex patterns) | quality_wrapper._apply_rejection_rules |
| `cea.expiration_cleanup_interval` | 3600 | quality_wrapper._maybe_cleanup |
| `cea.ceac.enabled` | (bool) | imperator_flow.init_context_node |
| `cea.ceac.max_memories` | 50 | cea_enrichment_flow.execute_search |
| `cea.ceac.max_token_budget` | 8000 | cea_enrichment_flow.evaluate_and_rank |
| `cea.ceac.max_iterations` | 3 | cea_enrichment_flow._should_iterate |
| `cea.ranking.usefulness_crossover_events` | 10 | cea_enrichment_flow._compute_memory_quality |
| `cea.ranking.exploration_rate` | 0.1 | cea_enrichment_flow._rank_results |
| `cea.ranking.exploration_min_feedback` | 3 | cea_enrichment_flow._rank_results |
| `cea.ranking.trustworthiness_min_threshold` | 0.1 | cea_enrichment_flow._rank_results |
| `cea.source_type_weights` | (dict override) | cea_enrichment_flow._compute_trustworthiness |

**Tests needed for each:**
- Default value used when absent from config
- Override value used when present
- Invalid value handling (where applicable)

**Existing coverage:** None.

---

## 9. Database Operations (New)

### New Tables
- `cea_quality_metadata` — Created by migration 023
- `cea_feedback_events` — Created by migration 023

### New Queries in quality_wrapper.py

| Query | Method | Purpose |
|-------|--------|---------|
| `SELECT target_id FROM cea_quality_metadata WHERE user_id=$1 AND conversation_id=$2 AND original_utterance=$3` | `add()` | Dedup check |
| `INSERT INTO cea_quality_metadata (...) ON CONFLICT (target_type, target_id) DO NOTHING` | `write_metadata()` | Metadata write |
| `INSERT INTO cea_feedback_events (...) ON CONFLICT (dedup_key) DO NOTHING` | `record_feedback()` | Feedback write |
| `SELECT ... FROM cea_quality_metadata WHERE (target_type, target_id) IN (...)` | `_get_metadata_batch()` | Batch read |
| `SELECT target_type, target_id, event_type, COUNT(*) FROM cea_feedback_events WHERE ... GROUP BY ...` | `_get_usefulness_batch()` | Aggregate read |
| `SELECT target_type, target_id FROM cea_quality_metadata WHERE expires_at IS NOT NULL AND expires_at < $1` | `cleanup_expired()` | Expiration scan |
| `DELETE FROM cea_quality_metadata WHERE ...` | `cleanup_expired()` | Expiration delete |
| `SELECT ... FROM mem0_memories WHERE embedding IS NOT NULL ORDER BY embedding <=> $1::vector LIMIT $2` | `_global_vector_search()` | Global ANN search |

**Existing coverage:** None.

---

## 10. Findings and Structural Issues

### F-01: Broken backward-compat shim in `retrieval_flow.py`
`retrieval_flow.py` imports `ke_inject_semantic_retrieval` and `ke_inject_knowledge_graph` from `knowledge_enriched.py`, but these functions were removed during CEA refactor. Any import of `retrieval_flow.py` will crash with `ImportError`. Need a test to confirm this shim is unused, or fix it.

### F-02: Broken backward-compat shim in `context_assembly.py`
Re-exports `calculate_tier_boundaries`, `consolidate_archival_summary`, `summarize_message_chunks` which were renamed to `calculate_compaction_state`, `run_full_compaction`, `compact_tier1`. Same `ImportError` risk.

### F-03: Sync/Async asymmetry in Mem0 fork `_update_memory`
Sync `Memory._update_memory()` preserves all payload keys (fork behavior). Async `AsyncMemory._update_memory()` only preserves session IDs. CEA metadata (durability, source_type, expires_at) stored via the sync path would be dropped if a future code path uses the async update.

### F-04: No reset mechanism for QualityWrapper singleton
`quality_wrapper.get_quality_wrapper()` caches a singleton, but there's no `reset_quality_wrapper()` function. If the underlying Mem0 client or DB pool is recreated (e.g., `reset_mem0_client()`), the wrapper holds stale references.

### F-05: `knowledge_feedback` dispatch path unverified
CEAc's `_feedback_fn` calls `ctx.dispatch_tool("knowledge_feedback", ...)`. This tool name must be registered in the AE flow registry or handled by `tool_dispatch.py`. Verify end-to-end path exists.

### F-06: Dead code after CEA refactor
- `memory_extraction.py` — Full flow deregistered (only `clean_for_compaction` still used)
- `memory_search_flow.py` — Deregistered (replaced by quality wrapper)
- `memory_admin_flow.build_mem_delete_flow` — Defined but not registered (facts are CR-only per REQ-CEA-I03)

### F-07: Bare Exception catches in CEA code
- `cea_extraction_flow.search_existing_facts` catches bare `Exception`
- `quality_wrapper._global_graph_search` catches bare `Exception`
- `quality_wrapper.cleanup_expired` catches bare `Exception` per fact
- These mask unexpected errors and make debugging harder.

---

## 11. Test File Mapping

Recommended new test files and their coverage targets:

### `tests/claude/test_cea/test_quality_wrapper.py`
Covers: Sections 1.1–1.7 above. ~35 tests.

### `tests/claude/test_cea/test_extraction_flow.py`
Covers: Section 2 above. ~15 tests.

### `tests/claude/test_cea/test_enrichment_flow.py`
Covers: Section 3 above. ~20 tests.

### `tests/claude/test_cea/test_quality_gate.py`
Covers: Section 6.5 above. ~12 tests.

### `tests/claude/test_cea/test_mem0_fork_cea.py`
Covers: Sections 6.1–6.4, 6.6 above. ~15 tests.

### `tests/claude/test_database/test_migration_023_024.py`
Covers: Section 4 above. ~8 tests.

### `tests/claude/test_cea/test_cea_integration.py`
Covers: Sections 5.1–5.6 above (compact_tier1 CEA branch, init_context_node CEAc branch, stategraph_registry TE flows, register.py changes, knowledge_enriched budget change). ~12 tests.

### `tests/claude/test_cea/test_cea_config.py`
Covers: Section 8 above (all new config params defaults and overrides). ~14 tests.

**Estimated total: ~131 new tests across 8 files.**

---

## 12. Summary

| Area | New Functions | Tests Needed | Existing Tests |
|------|--------------|-------------|----------------|
| quality_wrapper.py | 16 | ~35 | 0 |
| cea_extraction_flow.py | 6 | ~15 | 0 |
| cea_enrichment_flow.py | 10 | ~20 | 0 |
| quality_gate.py | 5 | ~12 | 0 |
| Mem0 fork changes | 4 changes | ~15 | 0 |
| Migrations 023/024 | 2 migrations | ~8 | 0 |
| Integration points | 6 changes | ~12 | 0 |
| Config parameters | 14 params | ~14 | 0 |
| **Total** | | **~131** | **0** |

All new CEA code has **zero** test coverage in the existing `tests/claude/` suite. The 7 findings (F-01 through F-07) identify structural issues that should be addressed during test implementation.
