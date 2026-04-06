# CEA Implementation Code Review -- Context Broker

**Date:** 2026-04-04
**Reviewer:** Gemini CLI Agent
**Branch:** `feature/cea-implementation`
**Status:** **FAIL** (6 BLOCKERS)

---

## 1. Executive Summary

The implementation of the Context Engineering Architecture (CEA) provides a solid foundation with the `QualityWrapper`, `CEAsExtractionFlow`, and `CEAcEnrichmentFlow`. The database migrations and ranking logic are correctly implemented according to the PRD. However, there are critical gaps in graph extraction, global search capabilities, and architectural boundary compliance that must be addressed before merging.

---

## 2. PRD Compliance (S01-S10, C01-C06, Q01-Q03, I01-I08, A01-A06)

| Requirement | Status | Finding |
| :--- | :--- | :--- |
| **S01: Extraction Trigger** | **PARTIAL** | Triggered in `compact_tier1()`, but lacks context (see Blocker #3). |
| **S02: Fact & Graph Extraction** | **BLOCKER** | **Graph extraction is missing.** Only vector facts are extracted. |
| **S04: Scope Awareness** | **BLOCKER** | **Global scope is graph-blind.** Graph results are only returned if `user_id` is present. |
| **C01-C03: CEAc Flow & Ranking** | **PASS** | Ranking formula and agentic loop structure are implemented correctly. |
| **C05: Feedback Loop** | **PASS** | `QualityWrapper.record_feedback` and CEAc feedback generation are correct. |
| **Q01: Quality Metadata Store** | **PASS** | Migration 023 correctly implements the `cea_quality_metadata` table. |
| **I01-I02: Build Type Integration** | **BLOCKER** | `KnowledgeEnrichedRetrieval` bypasses the `QualityWrapper` (see Blocker #4). |
| **I03: MCP Tooling** | **PASS** | `knowledge_search`, `knowledge_add`, etc. are correctly registered. |

---

## 3. Findings

### 3.1 BLOCKERS (Must fix before merge)

#### 1. Global Knowledge Search is Graph-Blind (REQ-CEA-S04)
*   **File:** `state_4_development/context_broker_pmad/packages/context-broker-ae/src/context_broker_ae/memory/quality_wrapper.py`
*   **Finding:** In `QualityWrapper.search()`, if `user_id` is None (Global scope), the flow calls `_global_vector_search()`, which returns `{"results": results, "relations": []}`.
*   **Impact:** Global knowledge searches via `knowledge_search` or `CEAc` will never return results from the knowledge graph, even if global facts exist.
*   **Fix:** Modify `MemoryGraph._search_graph_db` in `graph_memory.py` to support optional `user_id` filtering, and update `QualityWrapper` to call it during global search.

#### 2. Missing Graph Extraction in CEAs Flow (REQ-CEA-S02, S05)
*   **File:** `state_4_development/context_broker_pmad/packages/context-broker-ae/src/context_broker_ae/cea_extraction_flow.py`
*   **Finding:** The `run_extraction_llm` node only loads and uses the `cea_vector_extraction` prompt. The `cea_graph_extraction.md` prompt is ignored, and the logic to extract and store graph triples is entirely missing from `dispatch_results`.
*   **Impact:** The system only extracts flat vector facts. The knowledge graph is not populated with the high-quality triples intended by the CEA architecture.
*   **Fix:** Implement the graph extraction node or update the prompt/parsing to handle both sections as required by the PRD.

#### 3. Extraction Context is Not Populated (REQ-CEA-S01)
*   **File:** `state_4_development/context_broker_pmad/packages/context-broker-ae/src/context_broker_ae/build_types/standard_tiered.py`
*   **Finding:** `compact_tier1()` calls `cea_flow.ainvoke()` passing `state.get("tier2_summaries", [])` and `state.get("tier3_summary", "")`. However, these keys are never populated in the `StandardTieredAssemblyState` during the assembly process.
*   **Impact:** The extraction LLM operates without the context of existing summaries, leading to potential redundancy or loss of continuity in extracted knowledge.
*   **Fix:** Update `load_window_config` or a new node in the assembly flow to fetch active summaries into the state before compaction.

#### 4. Enriched Retrieval Bypasses Quality Wrapper (REQ-CEA-Q03)
*   **File:** `state_4_development/context_broker_pmad/packages/context-broker-ae/src/context_broker_ae/build_types/knowledge_enriched.py`
*   **Finding:** `ke_inject_knowledge_graph()` calls `mem0.search()` directly.
*   **Impact:** Internal RAG retrieval for the `enriched` build type bypasses the ranking, feedback scoring, and expiration logic of the `QualityWrapper`. This defeats the purpose of the CEA for the primary internal use case.
*   **Fix:** Update `KnowledgeEnrichedRetrieval` to use `QualityWrapper.search()`.

#### 5. Architectural Boundary Violation in CEAc (REQ-CEA-C02)
*   **File:** `state_4_development/context_broker_pmad/packages/context-broker-te/src/context_broker_te/cea_enrichment_flow.py`
*   **Finding:** The flow directly imports `get_quality_wrapper` from `context_broker_ae`.
*   **Impact:** Violates the PRD requirement to search "via MCP tools" and creates a tight package dependency between TE and AE.
*   **Fix:** Use the MCP tool client or a dedicated service abstraction to perform searches from the TE layer.

#### 6. Incomplete Mem0 Fork for AsyncPath
*   **File:** `state_4_development/context_broker_pmad/packages/mem0-fork/mem0/memory/main.py`
*   **Finding:** The fork changes (CB-24 `_skip_graph`, metadata preservation) were applied to the sync `Memory` class but NOT the `AsyncMemory` class.
*   **Impact:** If any part of the system switches to the async Mem0 path, the CEA features will silently fail or corrupt metadata.
*   **Fix:** Synchronize the fork changes to `AsyncMemory`.

---

### 3.2 OBSERVATIONS (Improvements)

1.  **Feedback Agent Identity:** `CEAsExtractionFlow` records feedback with the default `agent_id="unknown"`. It should explicitly use `"ceas"`.
2.  **Duplicate Extraction:** Since Mem0's `add()` is called for each CEA fact, Mem0's internal `MemoryGraph` performs a second extraction pass on the already-summarized text. This is redundant but safe.
3.  **Redundant Flow Loading:** `compact_tier1()` builds the `cea_flow` inside the loop for every chunk. It should be built once outside the loop.
4.  **SQL Efficiency:** `QualityWrapper._get_metadata_batch()` fetches by `target_id` but doesn't filter by `target_type` in the query, relying on Python-side filtering. While collisions are unlikely, a composite filter would be more robust.

---

## 4. ERQ Compliance

*   **ERQ-002 S2.1 (StateGraph):** PASS. All new flows use `StateGraph`.
*   **ERQ-002 S2.2 (Immutability):** PASS. Nodes return new dicts.
*   **ERQ-001 S4.1 (Non-blocking):** PASS. `QualityWrapper` correctly uses `run_in_executor` for sync Mem0/Neo4j calls.
*   **ERQ-001 S3.5 (Exceptions):** PASS. Specific exception types are caught and logged.

---

## 5. Security

*   **SQL Injection:** No issues found. Parameters are used in all `self.pool` calls.
*   **Secrets:** No hardcoded secrets. `get_api_key` correctly resolves from environment/credentials file.

---

## 6. Conclusion

The implementation is 80% complete but fails on critical integration and functional requirements (Graph extraction, Global search). The bypass of the `QualityWrapper` in the `enriched` build type is a significant regression from the architectural intent.

**Recommendation:** **REJECT.** Fix the 6 blockers before re-review.
