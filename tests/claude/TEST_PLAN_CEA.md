# Test Plan — CEA Implementation (Context Engineering Architecture)

**Author:** Claude Opus 4.6
**Date:** 2026-04-04
**Scope:** CEA delta — new code from `feature/cea-implementation` branch (6 commits)
**Parent plan:** `tests/claude/TEST_PLAN.md` (covers pre-CEA codebase: 315 mock + 123 live = 438 tests)
**Discovery audits:** `docs/designs/cli-discovery-audit-{claude,gemini,codex}.md`

---

## 1. Discovery Phase: Multi-Model Capability Audit

### 1a. Post-Code Discovery Audit (WPR-103 S1a)

The CEA code is complete. Three independent CLIs audited the full codebase (SDLC-02 Step 8):

| CLI | Audit File | Method | Key Findings |
|-----|-----------|--------|--------------|
| Claude Opus 4.6 | `cli-discovery-audit-claude.md` | Full source read of every `.py` file; gap analysis against `tests/claude/` | 131 tests needed, 7 structural findings (F-01..F-07), 8 recommended test files |
| Gemini 2.5 Pro | `cli-discovery-audit-gemini.md` | Capability matrix with risk annotations | 4 critical gaps: graph metadata, MCP persistence, formula stability, natural key dedup |
| Codex (GPT) | `cli-discovery-audit-codex.md` | Function-by-function coverage mapping | Confirmed zero coverage for all CEA code; identified CEAc init_context_node gap |

### 1b. Master Capability List

The Claude audit is the authoritative delta-focused document. Gemini and Codex findings are reconciled below where they add capabilities Claude did not enumerate.

**Gemini additions not in Claude audit:**
- G-1: `MemoryGraph.delete()` soft-delete via `valid=false` — included in Mem0 fork tests
- G-2: Vector/Graph divergence on partial `add()` failure — included in quality wrapper error tests
- G-3: Natural key collision handling in `QualityWrapper.add()` — included in wrapper dedup tests

**Codex additions not in Claude audit:**
- C-1: `_query_provider_context_length` HTTP fallback — out of scope (pre-CEA code, not modified)
- C-2: `get_package_metadata` / `is_loaded` — out of scope (pre-CEA code, not modified)
- C-3: `list_build_types` / `clear_compiled_cache` — out of scope (pre-CEA code, not modified)

All Gemini additions are incorporated into the test scenarios below. Codex additions C-1..C-3 are pre-CEA code not modified by CEA; they remain untested per the parent test plan's existing gaps.

### 1c. Coverage Assessment

All new CEA code has **zero** test coverage in the existing `tests/claude/` suite. 66 tests were written prematurely across 3 files (quality_wrapper, extraction_flow, enrichment_flow) covering approximately 50% of the Claude audit's recommendations. The remaining 65 tests across 5 additional files need to be written per this plan.

---

## 2. Engineering Requirements Gate

Per ERQ-001 (base) and ERQ-002 (MAD):

- **Code formatting:** Python files follow project style (no linter configured — manual review)
- **Unit test execution:** `pytest` with `pytest-asyncio` for async tests
- **Framework:** LangGraph StateGraph for flows; `unittest.mock` for mocking
- **Import convention:** Function-level imports within test methods (prevents import failures from blocking entire test files)

---

## 3. Test Strategy: Two Layers

### Mock Tests (Unit)

All CEA tests in this plan are mock tests. Dependencies mocked:

| Dependency | Mock Strategy |
|------------|---------------|
| Mem0 `Memory` instance | `MagicMock` with configured return values |
| asyncpg connection pool | `AsyncMock` for `execute`, `fetch`, `fetchval` |
| LLM calls (`get_chat_model`) | `AsyncMock` returning structured JSON responses |
| Neo4j (graph search) | `AsyncMock` returning relation dicts |
| Prometheus counters/histograms | Not mocked (module-level singletons; tested by side effect) |
| Prompt loader (`async_load_prompt`) | `AsyncMock` returning template strings |
| Quality wrapper singleton | `patch` on `get_quality_wrapper` |

**Why mock:** CEA components are pure logic (ranking formulas, dispatch routing, state machines) or thin wrappers around external services. Mock tests verify the logic without requiring Postgres, Neo4j, Mem0, or LLM endpoints.

### Live Tests (Integration)

CEA live tests are additions to the existing live phase structure:

| Phase | Addition | Tests |
|-------|----------|-------|
| Phase B | `knowledge_search`, `knowledge_add`, `knowledge_feedback` tool calls via MCP | 3-4 |
| Phase C | `knowledge_list` management tool | 1-2 |
| Phase D | CEA extraction triggered by compaction worker | 2-3 |
| Phase E | `knowledge-enriched` build type returns tiers only (no RAG injection) | 1-2 |
| Phase L | Quality evaluation of extracted facts (existing, extend for CEA) | 1-2 |

Live tests are out of scope for this document — they will be added to the existing live test files during deployment testing (Phase 5 of the implementation plan). This plan covers mock tests only.

---

## 4. Test Infrastructure

### 4.1 Isolation Strategy

Mock tests run locally with no infrastructure. All external dependencies are mocked at the function boundary.

### 4.2 Configuration

Tests use inline config dicts matching the structure of `config.yml` / `te.yml`. No config files are loaded from disk during mock tests.

### 4.3 Data Loading

No test data loading required. Each test constructs its own input state.

### 4.4 Stack Lifecycle

N/A for mock tests. For live tests, see parent `TEST_PLAN.md` Section "Session Setup."

### 4.5 Test Results Output

Mock test results: standard pytest output to terminal.
Live test results (future): `/storage/test-results/context-broker-cea/{run-timestamp}/`

---

## 5. Coverage Targets

### 5.1 Capability Audit — CEA Delta

Every capability identified by the 3-CLI audit must have at least one test. Target: zero NONE entries.

### 5.2 Coverage by Layer

- Every new MCP tool (`knowledge_search`, `knowledge_add`, `knowledge_list`, `knowledge_feedback`) tested with realistic inputs and verified outputs
- Every CEA StateGraph node tested for success, error, and edge conditions
- Every ranking formula tested with boundary values
- Every configuration parameter tested for default and override behavior
- Every database query path tested with mocked pool responses

---

## 6. Test Cases by Component

### 6.1 Quality Wrapper (`test_quality_wrapper.py`) — ~35 tests

**Status:** 32 tests written, covering sections 1.1-1.7 of Claude audit. ~3 tests remaining.

#### 6.1.1 `resolve_temporal()` — REQ-CEA-I04

| Scenario | Input | Expected | Layer | Gray-box |
|----------|-------|----------|-------|----------|
| ISO 8601 string | `"2026-06-15T12:00:00+00:00"` | datetime(2026,6,15,12,0,0,utc) | Mock | — |
| ISO with Z suffix | `"2026-06-15T12:00:00Z"` | datetime with tzinfo set | Mock | — |
| Date only | `"2026-06-15"` | datetime(2026,6,15) | Mock | — |
| Relative days | `"in 3 days"`, base=Jan 1 | base + timedelta(days=3) | Mock | — |
| Relative weeks | `"in 2 weeks"`, base=Jan 1 | base + timedelta(weeks=2) | Mock | — |
| Relative months | `"in 3 months"`, base=Jan 1 | base + timedelta(days=90) | Mock | — |
| None input | `None` | `None` | Mock | — |
| Empty string | `""` | `None` | Mock | — |
| Malformed string | `"not a date"` | `None` (no raise) | Mock | — |

#### 6.1.2 Dedup Keys

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Fact dedup deterministic | same inputs twice | identical SHA256 hashes | Mock |
| Fact dedup different inputs | different content | different hashes | Mock |
| Event dedup same minute | same target+type+agent | identical keys | Mock |
| Event dedup different type | different event_type | different keys | Mock |

#### 6.1.3 Rejection Rules (`_apply_rejection_rules`)

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Valid content above min_length | "This is a valid fact..." | False (accept) | Mock |
| Content below min_length | "too short" | True (reject) | Mock |
| Content matches regex pattern | `"Task #42 was updated"` with pattern `^Task #\d+` | True (reject) | Mock |
| No pattern match | "Context Broker uses pgvector" | False (accept) | Mock |
| Empty rules config | any content, min_length=0, patterns=[] | False (accept) | Mock |

#### 6.1.4 `add()` — REQ-CEA-A03

| Scenario | Input | Expected | Layer | Gray-box |
|----------|-------|----------|-------|----------|
| New fact added | valid content, user_id | `{"memory_id": "mem-123", "relation_ids": [...]}` | Mock | pool.fetchval called for dedup check |
| Rejection by rules | content below min_length | `{"memory_id": None}` | Mock | Mem0.add not called |
| Dedup hit | utterance exists in metadata table | `{"memory_id": None}` | Mock | pool.fetchval returns existing ID |
| skip_graph=True | valid content, skip_graph=True | Mem0.add called with _skip_graph | Mock | — |
| Partial failure (G-2) | Mem0.add succeeds but graph fails | memory_id returned, relation_ids empty | Mock | — |

#### 6.1.5 `write_metadata()` — REQ-CEA-Q01

| Scenario | Input | Expected | Layer | Gray-box |
|----------|-------|----------|-------|----------|
| Success | all valid fields | pool.execute called once | Mock | SQL contains INSERT...ON CONFLICT |
| Invalid conversation_id | "not-a-uuid" | pool.execute not called, warning logged | Mock | — |
| expires_at=None | nullable field | stored correctly | Mock | — |

#### 6.1.6 `search()` — Enriched Results

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| User-scoped search | query + user_id | enriched results with `_metadata` and `_usefulness` | Mock |
| Global search | query, user_id=None | `_global_vector_search` called | Mock |
| Expired facts filtered | results with past expires_at | expired entries removed | Mock |
| Empty results | no matches | `{"vector_facts": [], "graph_relations": []}` | Mock |

#### 6.1.7 `record_feedback()` — REQ-CEA-C05

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Success | valid target + event_type | True, pool.execute returns "INSERT 0 1" | Mock |
| Dedup (same bucket) | duplicate within timestamp bucket | False, ON CONFLICT fires | Mock |

#### 6.1.8 Expiration (`cleanup_expired`, `_maybe_cleanup`) — REQ-CEA-S08

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| No expired facts | pool.fetch returns [] | returns 0 | Mock |
| Expired facts deleted | pool.fetch returns 2 rows | returns 2, Mem0 delete called | Mock |
| Throttle within interval | called twice rapidly | second call skips cleanup | Mock |

#### 6.1.9 Batch Queries (`_get_metadata_batch`, `_get_usefulness_batch`)

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Empty pairs | [] | {} | Mock |
| Metadata returns keyed dict | [("fact", "mem-1")] | dict with (fact, mem-1) key | Mock |
| Usefulness empty | no feedback events | empty counts | Mock |

### 6.2 CEA Extraction Flow (`test_extraction_flow.py`) — ~15 tests

**Status:** 14 tests written, covering sections 2.2-2.6 of Claude audit. ~1 test remaining.

#### 6.2.1 `search_existing_facts`

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Conversation-scoped search | state with content | existing_facts populated | Mock |
| Search failure | wrapper.search raises RuntimeError | existing_facts = [] | Mock |

#### 6.2.2 `run_extraction_llm`

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Valid JSON response | LLM returns `{"facts": [...]}` | extraction_output populated with facts | Mock |
| Invalid JSON | LLM returns "not valid json {" | extraction_output=None, error set | Mock |
| Markdown-fenced JSON | LLM returns `` ```json\n{...}\n``` `` | fences stripped, parsed | Mock |
| Empty facts array | `{"facts": []}` | valid empty extraction | Mock |

#### 6.2.3 `dispatch_results`

| Scenario | Input | Expected | Layer | Gray-box |
|----------|-------|----------|-------|----------|
| NEW fact dispatched | relationship=NEW | wrapper.add + write_metadata called | Mock | Both fact and relation metadata written |
| DUPLICATE skipped | relationship=DUPLICATE | wrapper.add NOT called | Mock | dispatch_results.duplicate incremented |
| No extraction output | extraction_output=None | dispatch_results.new=0 | Mock | — |

#### 6.2.4 `handle_error`

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Error logged and returned | error="LLM call failed" | dispatch_results contains "error" key | Mock |

#### 6.2.5 `_should_dispatch` Routing

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Error present | error="something broke" | "handle_error" | Mock |
| Output present | extraction_output={"facts": []} | "dispatch_results" | Mock |
| No output, no error | extraction_output=None | "handle_error" | Mock |

#### 6.2.6 `build_cea_extraction_flow` Singleton

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Returns compiled graph | — | not None | Mock |
| Singleton behavior | called twice | same object | Mock |

### 6.3 CEAc Enrichment Flow (`test_enrichment_flow.py`) — ~20 tests

**Status:** 20 tests written, covering sections 3.2-3.3 of Claude audit. Complete.

#### 6.3.1 Ranking Functions

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| `_compute_memory_quality` zero feedback | durability=0.8, total_events=0 | 0.8 | Mock |
| `_compute_memory_quality` high positive | 20 events, 18 used | > 0.5 (shifts toward usefulness) | Mock |
| `_compute_memory_quality` negative feedback | 10 events, 5 discarded + 5 contradicted | < 0.8 | Mock |
| `_compute_trustworthiness` defaults | confidence=1.0, decision | 1.0 | Mock |
| `_compute_trustworthiness` observation | confidence=1.0, observation | 0.8 | Mock |
| `_compute_trustworthiness` speculation | confidence=1.0, speculation | 0.5 | Mock |
| `_compute_trustworthiness` custom weight | config override decision=0.5 | 0.5 | Mock |
| `_compute_trustworthiness` unknown type | "unknown_type" | 0.7 (fallback) | Mock |
| `_rank_results` sorted by score | two results | highest score first | Mock |
| `_rank_results` below threshold filtered | trustworthiness < min | empty list | Mock |
| `_rank_results` empty input | [] | [] | Mock |
| `_rank_results` exploration mixing | underexplored + scored items | both included | Mock |

#### 6.3.2 StateGraph Nodes

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| `decide_search` iteration 0 with query | query="architecture" | query preserved | Mock |
| `decide_search` iteration 0 extracts from tiers | tiers with user message | query contains topic keywords | Mock |
| `execute_search` success | search_fn returns results | results accumulated, iteration incremented | Mock |
| `execute_search` empty query | query="" | search_fn not called | Mock |
| `execute_search` no search_fn | search_fn=None | iteration incremented, no crash | Mock |
| `execute_search` dedup across iterations | duplicate ID in existing results | no duplicate added | Mock |
| `_should_iterate` max iterations | iteration_count=max | "assemble_context" | Mock |
| `_should_iterate` empty query | query="" | "assemble_context" | Mock |
| `_should_iterate` continue | iteration=1, query present | "decide_search" | Mock |
| `build_ceac_enrichment_flow` | — | returns compiled graph | Mock |

### 6.4 Quality Gate (`test_quality_gate.py`) — ~12 tests

**Status:** Not written.

#### 6.4.1 `extract_metadata_from_facts`

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Plain string facts | ["fact text"] | facts unchanged, empty metadata dicts | Mock |
| Dict facts with metadata | [{"fact": "text", "durability": 0.8}] | text extracted, metadata populated | Mock |
| Reserved key rejection | {"fact": "text", "user_id": "x"} | "user_id" removed, warning logged | Mock |
| `expires_at` ISO normalization | {"expires_at": "2026-06-15T00:00:00Z"} | UTC ISO string | Mock |
| `expires_at` unix timestamp | {"expires_at": 1750000000} | UTC ISO string | Mock |
| `expires_at` None | {"expires_at": null} | None preserved | Mock |

#### 6.4.2 `apply_quality_gate`

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| All facts pass | valid facts above min_length | full list, rejected_count=0 | Mock |
| Short fact rejected | fact below min_length | rejected, count=1 | Mock |
| Regex pattern rejection | fact matches pattern | rejected | Mock |
| Invalid regex | bad pattern in config | warning logged, pattern skipped | Mock |
| Custom validator rejects | callable returns reason string | fact rejected | Mock |

#### 6.4.3 `apply_post_update_gate`

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| DELETE/NONE pass through | action type DELETE | unchanged | Mock |

#### 6.4.4 `_normalize_expires_at`

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| None | None | None | Mock |
| ISO string | "2026-06-15T00:00:00+00:00" | UTC ISO string | Mock |
| Z suffix | "2026-06-15T00:00:00Z" | parsed with UTC | Mock |
| Unix int | 1750000000 | UTC ISO string | Mock |
| Invalid string | "not-a-date" | None, warning logged | Mock |

### 6.5 Mem0 Fork CEA Changes (`test_mem0_fork_cea.py`) — ~15 tests

**Status:** Not written.

#### 6.5.1 `_skip_graph` Parameter

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| skip_graph=True | Memory.add(..., _skip_graph=True) | `_add_to_graph` not called | Mock |
| skip_graph=False (default) | Memory.add(...) | `_add_to_graph` called | Mock |
| Return value with graph skipped | _skip_graph=True | results key still present | Mock |

#### 6.5.2 Payload Preservation on Update

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Preserves custom metadata | existing payload has durability=0.8 | preserved after update | Mock |
| Explicit None clears field | new metadata {"durability": None} | key removed | Mock |
| Session IDs preserved | existing user_id, agent_id | preserved as before | Mock |
| F-03: Async path asymmetry | AsyncMemory._update_memory | document that only session IDs preserved | Mock |

#### 6.5.3 elementId Migration (graph_memory.py)

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| `_search_graph_db` returns elementId strings | Cypher query | source_id, relation_id, destination_id present | Mock |
| BM25 reranking preserves elementId | reranked results | elementId fields from original rows | Mock |
| BM25 no-match fallback | result doesn't match any original tuple | result returned without IDs | Mock |
| `_add_entities` returns relation elementId | entity addition | `elementId(r)` in RETURN clause | Mock |

#### 6.5.4 Side-Channel Metadata Extraction

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Dict facts metadata extracted | `{"fact": "...", "durability": 0.8}` | metadata populated | Mock |
| Reserved keys rejected | metadata with "user_id" | warning logged, key removed | Mock |

#### 6.5.5 Expiration Filtering

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Non-expired pass through | memory with future expires_at | included | Mock |
| Expired filtered | memory with past expires_at | excluded | Mock |
| No expires_at pass through | memory without field | included | Mock |

### 6.6 Migrations (`test_migration_023_024.py`) — ~8 tests

**Status:** Not written.

#### 6.6.1 Migration 023

| Scenario | Input | Expected | Layer | Gray-box |
|----------|-------|----------|-------|----------|
| Tables created | fresh DB | `cea_quality_metadata` and `cea_feedback_events` exist | Mock (SQL parse) | Table schema matches spec |
| Idempotent | run twice | no error (IF NOT EXISTS) | Mock | — |
| UNIQUE on (target_type, target_id) | duplicate insert | constraint violation | Mock | — |
| UNIQUE on dedup_key | duplicate event | constraint violation | Mock | — |
| FK to conversations(id) | invalid conversation_id | FK violation | Mock | — |

#### 6.6.2 Migration 024

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Natural dedup index | same user+conv+utterance | UNIQUE violation | Mock |
| Empty utterance excluded | utterance="" | not in index (partial WHERE) | Mock |
| Composite feedback index | — | index created | Mock |

### 6.7 CEA Integration Points (`test_cea_integration.py`) — ~12 tests

**Status:** Not written.

#### 6.7.1 `compact_tier1()` CEA Branch (standard_tiered.py)

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| CEA extraction invoked per chunk | compaction with content | build_cea_extraction_flow().ainvoke called | Mock |
| CEA extraction fails gracefully | flow raises RuntimeError | compaction continues (REQ-CEA-S06) | Mock |
| ImportError graceful skip | CEA not installed | compaction continues | Mock |
| Correct state passed | — | content, tier2_context, tier3_context, conversation_id in state | Mock |

#### 6.7.2 `init_context_node()` CEAc Branch (imperator_flow.py)

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| CEAc invoked when enabled | cea.ceac.enabled=True | enrichment flow invoked | Mock |
| CEAc disabled | cea.ceac.enabled=False | enrichment flow NOT invoked | Mock |
| CEAc failure graceful | flow raises RuntimeError | continues without enrichment | Mock |
| Empty enriched_context | flow returns empty string | no SystemMessage injected | Mock |

#### 6.7.3 `stategraph_registry.py` TE Flows

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| TE flows dict scanned | TE package exports flows | flows in `_flow_builders` | Mock |
| TE without flows dict | TE package with no flows key | no error | Mock |

#### 6.7.4 `knowledge_enriched.py` Budget Change

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| No RAG budget reservation | ke_load_recent_messages | full tier1 budget allocated | Mock |

### 6.8 CEA Configuration (`test_cea_config.py`) — ~14 tests

**Status:** Not written.

#### 6.8.1 AE Config Parameters

| Parameter | Default | Test | Layer |
|-----------|---------|------|-------|
| `cea.pre_extraction_fact_limit` | 20 | absent → 20; present → override | Mock |
| `cea.retrieval_scope` | "conversation" | absent → "conversation"; "user" → "user" | Mock |
| `cea.rejection_rules.min_fact_length` | (from config) | absent → default; present → override | Mock |
| `cea.expiration_cleanup_interval` | 3600 | absent → 3600; present → override | Mock |

#### 6.8.2 TE Config Parameters

| Parameter | Default | Test | Layer |
|-----------|---------|------|-------|
| `cea.ceac.enabled` | (bool) | true → CEAc runs; false → skipped | Mock |
| `cea.ceac.max_memories` | 50 | absent → 50; present → override | Mock |
| `cea.ceac.max_token_budget` | 8000 | absent → 8000; present → override | Mock |
| `cea.ceac.max_iterations` | 3 | absent → 3; present → override | Mock |
| `cea.ranking.usefulness_crossover_events` | 10 | absent → 10; present → override | Mock |
| `cea.ranking.exploration_rate` | 0.1 | absent → 0.1; present → override | Mock |
| `cea.ranking.exploration_min_feedback` | 3 | absent → 3; present → override | Mock |
| `cea.ranking.trustworthiness_min_threshold` | 0.1 | absent → 0.1; present → override | Mock |
| `cea.source_type_weights` | default dict | absent → defaults; override → custom | Mock |

#### 6.8.3 `validate_cea_config()` (app/config.py)

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Valid config | all valid params | no error | Mock |
| Invalid retrieval_scope | "invalid" | ValueError raised | Mock |
| Invalid threshold | negative float | ValueError raised | Mock |

---

## 7. Endpoint and Tool Testing

### MCP Tools — Full Parameter Variation

#### `knowledge_search`

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Success with user_id | query="architecture", user_id="alice" | formatted results | Mock |
| Success global (no user_id) | query="architecture" | global search results | Mock |
| Empty results | query with no matches | "No relevant memories found" | Mock |
| Missing query | query="" | validation error or empty results | Mock |

#### `knowledge_add`

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Success with metadata | content + durability + confidence | memory created with metadata | Mock |
| Rejection by rules | content too short | rejected, no Mem0 write | Mock |
| Dedup hit | duplicate utterance | skipped | Mock |

#### `knowledge_list`

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| User-scoped list | user_id="alice" | facts for alice | Mock |
| Global list | no user_id | all facts | Mock |
| Empty results | no facts | empty list | Mock |

#### `knowledge_feedback`

| Scenario | Input | Expected | Layer |
|----------|-------|----------|-------|
| Success | valid target + event_type | True | Mock |
| Invalid event_type | event_type="invalid" | validation error | Mock |
| Duplicate event | same event within bucket | False (dedup) | Mock |

---

## 8. Pipeline End-to-End Verification

### 8.1 CEA Extraction Pipeline (CEAs)

**Stage verification:**
1. `search_existing_facts` — check that existing_facts state key is populated (or empty on error)
2. `run_extraction_llm` — check that extraction_output contains valid JSON with facts array
3. `dispatch_results` — check that dispatch_results dict has correct counts (new, duplicate, supersedes, conflicts)

**Completion detection:** CEAs is synchronous within `compact_tier1()`. Completion is determined by the flow returning.

**Performance measurement:** `CEA_EXTRACTION_DURATION` histogram captures end-to-end time. `CEA_FACTS_EXTRACTED` counter tracks per-relationship-type counts.

### 8.2 CEAc Enrichment Pipeline

**Stage verification:**
1. `decide_search` — query state populated
2. `execute_search` — search_results accumulated, iteration_count incremented
3. `evaluate_and_rank` — ranked_results sorted by composite score
4. `assemble_context` — enriched_context formatted string
5. `record_feedback` — feedback events dispatched

**Completion detection:** CEAc runs within `init_context_node()` before agent response. Completion = flow returns.

**Performance measurement:** `CEAC_ENRICHMENT_DURATION` histogram, `CEAC_SEARCH_COUNT` counter, `CEAC_FEEDBACK_EVENTS` counter.

---

## 9. Non-Deterministic System Testing

### 9.1 Behavioral Assertions

CEA extraction uses an LLM to extract facts. Tests mock the LLM response and verify:
- Structured JSON output parsed correctly
- Relationship classification (NEW/DUPLICATE/SUPERSEDES/CONFLICTS) dispatched to correct handlers
- Durability and confidence values stored in metadata table

Live tests (future) will verify real LLM extraction quality.

### 9.2 Quality Evaluation

**Existing coverage:** `test_phase_l_quality_evals.py` tests extraction quality via LLM-as-judge. This will be extended for CEA-extracted facts once deployed.

**Mock test approach:** Mock tests do not evaluate quality — they verify that the LLM response is correctly parsed and dispatched. Quality is a live test concern.

### 9.3 CEAc Query Refinement

CEAc uses LLM to decide subsequent search queries. Mock tests verify:
- Iteration 0 uses state.query directly (no LLM)
- Subsequent iterations call LLM for query refinement
- "DONE" response terminates iteration

---

## 10. Gray-Box Verification Strategy

### Database Queries

| Table | What to Check | When |
|-------|--------------|------|
| `cea_quality_metadata` | Row exists with correct durability, confidence, source_type after `write_metadata()` | After dispatch_results |
| `cea_feedback_events` | Event row exists with correct event_type, dedup_key after `record_feedback()` | After CEAc record_feedback |
| `mem0_memories` | Fact exists in pgvector after `wrapper.add()` | After extraction dispatch |

**Mock implementation:** Verify `pool.execute` / `pool.fetch` called with expected SQL patterns and parameters.

### Application Metrics

| Metric | What to Check | When |
|--------|--------------|------|
| `CEA_EXTRACTION_EVENTS` | Counter incremented with status=success or status=failure | After extraction flow |
| `CEA_FACTS_EXTRACTED` | Counter incremented per relationship type | After dispatch |
| `CEAC_SEARCH_COUNT` | Counter incremented per search | After execute_search |
| `CEAC_FEEDBACK_EVENTS` | Counter incremented per event | After record_feedback |

---

## 11. Runtime Issue Logging

Issues discovered during test execution will be filed as GitHub Issues on `rmdevpro/Joshua26`:

| Severity | Criteria | Label |
|----------|----------|-------|
| Hard failure | Test assertion fails | `bug`, `component:context-broker` |
| Warning | Degraded behavior, unexpected but non-breaking | `investigation`, `component:context-broker` |
| Performance | Latencies outside expected ranges | `performance`, `component:context-broker` |

Quality thresholds for LLM extraction (live tests only):
- Extraction quality rating GOOD or EXCELLENT → pass
- POOR → file issue with extracted facts sample

---

## 12. Hot-Reload Testing

CEA configuration parameters that are hot-reloadable:
- Prompt templates (`cea_vector_extraction`, `cea_graph_extraction`, `cea_output`)
- Ranking weights (`source_type_weights`, `exploration_rate`, thresholds)

**Test:** Modify config between operations and verify new values take effect. This is a live test concern — mock tests verify config is read per-invocation (not cached at import time).

---

## 13. UI Testing

N/A — Context Broker has no browser UI for CEA features.

### 13a. Context Stress Testing

CEA extraction triggers during compaction. Stress testing of the compaction pipeline (SDLC-07) will exercise CEA extraction at scale. The stress test plan is documented in `docs/guides/sdlc/SDLC-07-stress-testing-guide.md` and executed via `scripts/stress-test-primer/runner.py`.

CEA-specific stress concerns:
- Extraction latency per chunk as context grows
- Metadata table row count growth over many compaction cycles
- Feedback event table growth during extended CEAc sessions

### 13b. Fresh-Container Testing

Existing `FRESH_DEPLOY=1` mechanism handles this. CEA tables are created by migrations 023 and 024, which run during fresh deploy.

---

## 14. Failure Investigation Strategy

Per SDLC-06 debugging guide:

1. **Root cause before fix** — trace every failure to exact code path
2. **Full analysis** — read logs, trace data, prove cause
3. **Second opinion** — consult second CLI for non-trivial bugs
4. **Never weaken tests** — fix the code, not the test
5. **Every fix verified** — deploy, run failing test, run full suite

---

## 15. What Is Not Tested

### Exclusions (out of scope)

| Area | Reason |
|------|--------|
| Pre-CEA code not modified by CEA | Covered by parent TEST_PLAN.md (438 tests) |
| Codex items C-1..C-3 (provider context query, package metadata, build type cache) | Pre-CEA code, not modified |
| LLMLingua-2 compression (Phase 2.8) | Deferred to deployment per implementation plan |
| Actual LLM extraction quality | Live test concern (Phase L), not mock testable |
| Neo4j graph quality after extraction | Live test concern, requires deployed Neo4j |

### Blockers (to fix)

| Area | Blocker | Resolution |
|------|---------|------------|
| F-01: Broken `retrieval_flow.py` backward-compat shim | Imports removed functions | Add test to confirm shim is dead code, or fix imports |
| F-02: Broken `context_assembly.py` backward-compat shim | Imports renamed functions | Same as F-01 |
| F-04: No `reset_quality_wrapper()` | Stale singleton after Mem0 reset | Add reset function + test |
| F-05: `knowledge_feedback` dispatch path unverified | End-to-end path may be broken | Add integration test |
| F-07: Bare Exception catches | Masks unexpected errors | Narrow exception types in code fix |

### Structural Findings to Address During Implementation

| Finding | Description | Test Coverage |
|---------|-------------|---------------|
| F-01 | `retrieval_flow.py` imports removed functions | test_cea_integration: broken import test |
| F-02 | `context_assembly.py` imports renamed functions | test_cea_integration: broken import test |
| F-03 | Sync/Async `_update_memory` asymmetry | test_mem0_fork_cea: document asymmetry |
| F-04 | No QualityWrapper reset mechanism | test_quality_wrapper: add reset test after fix |
| F-05 | `knowledge_feedback` dispatch path | test_cea_integration: end-to-end dispatch |
| F-06 | Dead code after CEA refactor | test_cea_integration: verify deregistration |
| F-07 | Bare Exception catches | Addressed in code fix, not test |

---

## 16. Traceability Matrix

| Scenario ID | Test File | Layer | Status | Result | Notes |
|-------------|-----------|-------|--------|--------|-------|
| **6.1 Quality Wrapper** |
| 6.1.1-temporal-iso | test_quality_wrapper.py::TestResolveTemporal::test_iso_string | Mock | Written | Pass | |
| 6.1.1-temporal-z | test_quality_wrapper.py::TestResolveTemporal::test_iso_z_suffix | Mock | Written | Pass | |
| 6.1.1-temporal-date | test_quality_wrapper.py::TestResolveTemporal::test_date_only | Mock | Written | Pass | |
| 6.1.1-temporal-days | test_quality_wrapper.py::TestResolveTemporal::test_relative_days | Mock | Written | Pass | |
| 6.1.1-temporal-weeks | test_quality_wrapper.py::TestResolveTemporal::test_relative_weeks | Mock | Written | Pass | |
| 6.1.1-temporal-months | test_quality_wrapper.py::TestResolveTemporal::test_relative_months | Mock | Written | Pass | |
| 6.1.1-temporal-none | test_quality_wrapper.py::TestResolveTemporal::test_none_input | Mock | Written | Pass | |
| 6.1.1-temporal-empty | test_quality_wrapper.py::TestResolveTemporal::test_empty_string | Mock | Written | Pass | |
| 6.1.1-temporal-malformed | test_quality_wrapper.py::TestResolveTemporal::test_malformed_string | Mock | Written | Pass | |
| 6.1.2-dedup-fact-det | test_quality_wrapper.py::TestDedupKeys::test_fact_dedup_deterministic | Mock | Written | Pass | |
| 6.1.2-dedup-fact-diff | test_quality_wrapper.py::TestDedupKeys::test_fact_dedup_different_inputs | Mock | Written | Pass | |
| 6.1.2-dedup-event-same | test_quality_wrapper.py::TestDedupKeys::test_event_dedup_same_minute | Mock | Written | Pass | |
| 6.1.2-dedup-event-diff | test_quality_wrapper.py::TestDedupKeys::test_event_dedup_different_type | Mock | Written | Pass | |
| 6.1.3-reject-valid | test_quality_wrapper.py::TestRejectionRules::test_accept_valid_content | Mock | Written | Pass | |
| 6.1.3-reject-short | test_quality_wrapper.py::TestRejectionRules::test_reject_short_content | Mock | Written | Pass | |
| 6.1.3-reject-pattern | test_quality_wrapper.py::TestRejectionRules::test_reject_pattern_match | Mock | Written | Pass | |
| 6.1.3-reject-no-match | test_quality_wrapper.py::TestRejectionRules::test_accept_no_pattern_match | Mock | Written | Pass | |
| 6.1.3-reject-empty | test_quality_wrapper.py::TestRejectionRules::test_empty_rules | Mock | Written | Pass | |
| 6.1.4-add-success | test_quality_wrapper.py::TestQualityWrapperAdd::test_success_returns_memory_id | Mock | Written | Pass | |
| 6.1.4-add-rejection | test_quality_wrapper.py::TestQualityWrapperAdd::test_rejection_returns_empty | Mock | Written | Pass | |
| 6.1.4-add-dedup | test_quality_wrapper.py::TestQualityWrapperAdd::test_dedup_returns_empty | Mock | Written | Pass | |
| 6.1.4-add-skipgraph | test_quality_wrapper.py::TestQualityWrapperAdd::test_skip_graph_passed_to_mem0 | Mock | Written | Pass | |
| 6.1.5-meta-success | test_quality_wrapper.py::TestWriteMetadata::test_success | Mock | Written | Pass | |
| 6.1.5-meta-invalid-uuid | test_quality_wrapper.py::TestWriteMetadata::test_invalid_conversation_id | Mock | Written | Pass | |
| 6.1.7-fb-success | test_quality_wrapper.py::TestRecordFeedback::test_success | Mock | Written | Pass | |
| 6.1.7-fb-dedup | test_quality_wrapper.py::TestRecordFeedback::test_dedup_returns_false | Mock | Written | Pass | |
| 6.1.8-cleanup-none | test_quality_wrapper.py::TestCleanupExpired::test_no_expired | Mock | Written | Pass | |
| 6.1.8-cleanup-deleted | test_quality_wrapper.py::TestCleanupExpired::test_expired_deleted | Mock | Written | Pass | |
| 6.1.8-throttle | test_quality_wrapper.py::TestMaybeCleanup::test_throttle | Mock | Written | Pass | |
| 6.1.9-meta-empty | test_quality_wrapper.py::TestBatchQueries::test_metadata_empty_pairs | Mock | Written | Pass | |
| 6.1.9-use-empty | test_quality_wrapper.py::TestBatchQueries::test_usefulness_empty_pairs | Mock | Written | Pass | |
| 6.1.9-meta-keyed | test_quality_wrapper.py::TestBatchQueries::test_metadata_returns_keyed_dict | Mock | Written | Pass | |
| **6.2 Extraction Flow** |
| 6.2.1-search-conv | test_extraction_flow.py::TestSearchExistingFacts::test_conversation_scope | Mock | Written | Pass | |
| 6.2.1-search-fail | test_extraction_flow.py::TestSearchExistingFacts::test_search_failure_returns_empty | Mock | Written | Pass | |
| 6.2.2-llm-valid | test_extraction_flow.py::TestRunExtractionLLM::test_valid_json_response | Mock | Written | Pass | |
| 6.2.2-llm-invalid | test_extraction_flow.py::TestRunExtractionLLM::test_invalid_json_sets_error | Mock | Written | Pass | |
| 6.2.2-llm-fenced | test_extraction_flow.py::TestRunExtractionLLM::test_markdown_fenced_json | Mock | Written | Pass | |
| 6.2.3-dispatch-new | test_extraction_flow.py::TestDispatchResults::test_new_fact_dispatched | Mock | Written | Pass | |
| 6.2.3-dispatch-dup | test_extraction_flow.py::TestDispatchResults::test_duplicate_skipped | Mock | Written | Pass | |
| 6.2.3-dispatch-none | test_extraction_flow.py::TestDispatchResults::test_no_extraction_output | Mock | Written | Pass | |
| 6.2.4-error | test_extraction_flow.py::TestHandleError::test_logs_and_returns | Mock | Written | Pass | |
| 6.2.5-route-error | test_extraction_flow.py::TestShouldDispatch::test_routes_to_error_on_error | Mock | Written | Pass | |
| 6.2.5-route-dispatch | test_extraction_flow.py::TestShouldDispatch::test_routes_to_dispatch_on_output | Mock | Written | Pass | |
| 6.2.5-route-no-output | test_extraction_flow.py::TestShouldDispatch::test_routes_to_error_on_no_output | Mock | Written | Pass | |
| 6.2.6-singleton | test_extraction_flow.py::TestBuildExtractionFlow::test_returns_compiled_graph | Mock | Written | Pass | |
| 6.2.6-singleton-same | test_extraction_flow.py::TestBuildExtractionFlow::test_singleton | Mock | Written | Pass | |
| **6.3 Enrichment Flow** |
| 6.3.1-mq-zero | test_enrichment_flow.py::TestComputeMemoryQuality::test_zero_feedback_returns_durability | Mock | Written | Pass | |
| 6.3.1-mq-positive | test_enrichment_flow.py::TestComputeMemoryQuality::test_high_positive_feedback | Mock | Written | Pass | |
| 6.3.1-mq-negative | test_enrichment_flow.py::TestComputeMemoryQuality::test_negative_feedback | Mock | Written | Pass | |
| 6.3.1-tw-default | test_enrichment_flow.py::TestComputeTrustworthiness::test_default_weights | Mock | Written | Pass | |
| 6.3.1-tw-custom | test_enrichment_flow.py::TestComputeTrustworthiness::test_custom_weight | Mock | Written | Pass | |
| 6.3.1-tw-unknown | test_enrichment_flow.py::TestComputeTrustworthiness::test_unknown_source_type | Mock | Written | Pass | |
| 6.3.1-rank-sorted | test_enrichment_flow.py::TestRankResults::test_sorted_by_score | Mock | Written | Pass | |
| 6.3.1-rank-filtered | test_enrichment_flow.py::TestRankResults::test_below_threshold_filtered | Mock | Written | Pass | |
| 6.3.1-rank-empty | test_enrichment_flow.py::TestRankResults::test_empty_results | Mock | Written | Pass | |
| 6.3.1-rank-explore | test_enrichment_flow.py::TestRankResults::test_exploration_mixing | Mock | Written | Pass | |
| 6.3.2-decide-query | test_enrichment_flow.py::TestDecideSearch::test_first_iteration_uses_query | Mock | Written | Pass | |
| 6.3.2-decide-tiers | test_enrichment_flow.py::TestDecideSearch::test_first_iteration_extracts_from_tiers | Mock | Written | Pass | |
| 6.3.2-exec-success | test_enrichment_flow.py::TestExecuteSearch::test_success | Mock | Written | Pass | |
| 6.3.2-exec-empty | test_enrichment_flow.py::TestExecuteSearch::test_empty_query_skips | Mock | Written | Pass | |
| 6.3.2-exec-no-fn | test_enrichment_flow.py::TestExecuteSearch::test_no_search_fn | Mock | Written | Pass | |
| 6.3.2-exec-dedup | test_enrichment_flow.py::TestExecuteSearch::test_dedup_across_iterations | Mock | Written | Pass | |
| 6.3.2-iter-max | test_enrichment_flow.py::TestShouldIterate::test_max_iterations | Mock | Written | Pass | |
| 6.3.2-iter-empty | test_enrichment_flow.py::TestShouldIterate::test_empty_query_done | Mock | Written | Pass | |
| 6.3.2-iter-refine | test_enrichment_flow.py::TestShouldIterate::test_iteration_gt_0_refines | Mock | Written | Pass | |
| 6.3.2-build | test_enrichment_flow.py::TestBuildEnrichmentFlow::test_returns_compiled_graph | Mock | Written | Pass | |
| **6.4 Quality Gate** |
| 6.4.1-extract-plain | test_quality_gate.py | Mock | Not started | — | |
| 6.4.1-extract-dict | test_quality_gate.py | Mock | Not started | — | |
| 6.4.1-extract-reserved | test_quality_gate.py | Mock | Not started | — | |
| 6.4.1-expires-iso | test_quality_gate.py | Mock | Not started | — | |
| 6.4.1-expires-unix | test_quality_gate.py | Mock | Not started | — | |
| 6.4.1-expires-none | test_quality_gate.py | Mock | Not started | — | |
| 6.4.2-gate-pass | test_quality_gate.py | Mock | Not started | — | |
| 6.4.2-gate-short | test_quality_gate.py | Mock | Not started | — | |
| 6.4.2-gate-regex | test_quality_gate.py | Mock | Not started | — | |
| 6.4.2-gate-bad-regex | test_quality_gate.py | Mock | Not started | — | |
| 6.4.2-gate-custom | test_quality_gate.py | Mock | Not started | — | |
| 6.4.3-post-delete | test_quality_gate.py | Mock | Not started | — | |
| 6.4.4-norm-none | test_quality_gate.py | Mock | Not started | — | |
| 6.4.4-norm-iso | test_quality_gate.py | Mock | Not started | — | |
| 6.4.4-norm-z | test_quality_gate.py | Mock | Not started | — | |
| 6.4.4-norm-unix | test_quality_gate.py | Mock | Not started | — | |
| 6.4.4-norm-invalid | test_quality_gate.py | Mock | Not started | — | |
| **6.5 Mem0 Fork** |
| 6.5.1-skip-true | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.1-skip-false | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.1-skip-return | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.2-preserve-meta | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.2-clear-none | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.2-preserve-session | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.2-async-asymmetry | test_mem0_fork_cea.py | Mock | Not started | — | F-03 |
| 6.5.3-elementid-search | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.3-elementid-rerank | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.3-elementid-fallback | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.3-elementid-add | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.4-meta-extract | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.4-meta-reserved | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.5-expire-pass | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.5-expire-filter | test_mem0_fork_cea.py | Mock | Not started | — | |
| 6.5.5-expire-absent | test_mem0_fork_cea.py | Mock | Not started | — | |
| **6.6 Migrations** |
| 6.6.1-tables-created | test_migration_023_024.py | Mock | Not started | — | |
| 6.6.1-idempotent | test_migration_023_024.py | Mock | Not started | — | |
| 6.6.1-unique-target | test_migration_023_024.py | Mock | Not started | — | |
| 6.6.1-unique-dedup | test_migration_023_024.py | Mock | Not started | — | |
| 6.6.1-fk-conv | test_migration_023_024.py | Mock | Not started | — | |
| 6.6.2-natural-dedup | test_migration_023_024.py | Mock | Not started | — | |
| 6.6.2-empty-excluded | test_migration_023_024.py | Mock | Not started | — | |
| 6.6.2-composite-idx | test_migration_023_024.py | Mock | Not started | — | |
| **6.7 Integration** |
| 6.7.1-compact-invoke | test_cea_integration.py | Mock | Not started | — | |
| 6.7.1-compact-fail | test_cea_integration.py | Mock | Not started | — | |
| 6.7.1-compact-import | test_cea_integration.py | Mock | Not started | — | |
| 6.7.1-compact-state | test_cea_integration.py | Mock | Not started | — | |
| 6.7.2-ceac-enabled | test_cea_integration.py | Mock | Not started | — | |
| 6.7.2-ceac-disabled | test_cea_integration.py | Mock | Not started | — | |
| 6.7.2-ceac-fail | test_cea_integration.py | Mock | Not started | — | |
| 6.7.2-ceac-empty | test_cea_integration.py | Mock | Not started | — | |
| 6.7.3-te-flows | test_cea_integration.py | Mock | Not started | — | |
| 6.7.3-te-no-flows | test_cea_integration.py | Mock | Not started | — | |
| 6.7.4-ke-no-rag | test_cea_integration.py | Mock | Not started | — | |
| 6.7-f01-shim | test_cea_integration.py | Mock | Not started | — | F-01 |
| 6.7-f02-shim | test_cea_integration.py | Mock | Not started | — | F-02 |
| **6.8 Config** |
| 6.8.1-fact-limit-default | test_cea_config.py | Mock | Not started | — | |
| 6.8.1-fact-limit-override | test_cea_config.py | Mock | Not started | — | |
| 6.8.1-scope-default | test_cea_config.py | Mock | Not started | — | |
| 6.8.1-scope-override | test_cea_config.py | Mock | Not started | — | |
| 6.8.1-min-length | test_cea_config.py | Mock | Not started | — | |
| 6.8.1-cleanup-interval | test_cea_config.py | Mock | Not started | — | |
| 6.8.2-ceac-enabled | test_cea_config.py | Mock | Not started | — | |
| 6.8.2-max-memories | test_cea_config.py | Mock | Not started | — | |
| 6.8.2-max-budget | test_cea_config.py | Mock | Not started | — | |
| 6.8.2-max-iterations | test_cea_config.py | Mock | Not started | — | |
| 6.8.2-crossover | test_cea_config.py | Mock | Not started | — | |
| 6.8.2-explore-rate | test_cea_config.py | Mock | Not started | — | |
| 6.8.2-explore-min | test_cea_config.py | Mock | Not started | — | |
| 6.8.2-trust-threshold | test_cea_config.py | Mock | Not started | — | |
| 6.8.2-source-weights | test_cea_config.py | Mock | Not started | — | |
| 6.8.3-validate-valid | test_cea_config.py | Mock | Not started | — | |
| 6.8.3-validate-scope | test_cea_config.py | Mock | Not started | — | |
| 6.8.3-validate-threshold | test_cea_config.py | Mock | Not started | — | |

---

## Summary

| File | Tests Planned | Written | Remaining |
|------|--------------|---------|-----------|
| `test_cea/test_quality_wrapper.py` | 32 | 32 | 0 |
| `test_cea/test_extraction_flow.py` | 14 | 14 | 0 |
| `test_cea/test_enrichment_flow.py` | 20 | 20 | 0 |
| `test_cea/test_quality_gate.py` | 17 | 0 | 17 |
| `test_cea/test_mem0_fork_cea.py` | 16 | 0 | 16 |
| `test_database/test_migration_023_024.py` | 8 | 0 | 8 |
| `test_cea/test_cea_integration.py` | 13 | 0 | 13 |
| `test_cea/test_cea_config.py` | 18 | 0 | 18 |
| **Total** | **138** | **66** | **72** |

**Structural findings to address:** 7 (F-01 through F-07)
**Estimated remaining work:** 72 tests across 5 new files
