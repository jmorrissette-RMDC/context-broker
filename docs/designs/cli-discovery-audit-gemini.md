# Post-CEA Discovery Audit: Context Broker

**Date:** Saturday, April 4, 2026
**Scope:** CEA implementation (SDLC-02 Step 8, WPR-103 S1a)
**Project:** state_4_development/context_broker_pmad/

---

## 1. Functions & Public Logic

| Component | Function | Purpose | Inputs / Outputs | Coverage Needed | Covered? |
|-----------|----------|---------|------------------|-----------------|----------|
| `QualityWrapper` | `add()` | Idempotent fact/relation storage with rejection rules. | In: `content`, `metadata`, `_skip_graph`. Out: `results`. | Success path (new), Idempotency (dedup), Rejection (rules), Graph IDs capture. | Partial (M-16) |
| `QualityWrapper` | `search()` | Enriched search blending vector/graph results with quality scores. | In: `query`, `filters`. Out: `List[MemoryItem]`. | Blended score calculation, Global search bypass, Graph result enrichment. | No |
| `QualityWrapper` | `write_metadata()` | Persists quality metrics to Postgres. | In: `target_type`, `target_id`, `durability`, etc. Out: None. | DB write success, Constraint violations (UUID), Field normalization. | Partial (M-16) |
| `QualityWrapper` | `record_feedback()` | Appends to feedback log for usefulness tracking. | In: `target_type`, `target_id`, `event_type`. Out: None. | Success path, Invalid event type, Dedup key logic. | No |
| `QualityWrapper` | `cleanup_expired()` | Deletes memories past their `expires_at`. | In: None. Out: `count`. | Vector cleanup, Graph cleanup, Metadata cleanup, Lock safety. | No |
| `Mem0 (Fork)` | `Memory.add()` | Store facts with `_skip_graph` support. | In: `messages`, `_skip_graph`. Out: `results`. | `_skip_graph=True` skips Neo4j, `_skip_graph=False` uses both. | Yes (M-16) |
| `MemoryGraph` | `search()` | Preserves `elementId` through reranking. | In: `query`. Out: `List[dict]` with `source_id`/`relation_id`. | Reranking preserves IDs, ID mapping fallback. | No |
| `MemoryGraph` | `delete()` | Soft-delete graph relations. | In: `data`. Out: None. | `valid=false` set in Cypher, `invalidated_at` set. | No |

---

## 2. MCP Tools (app/routes/mcp.py)

| Tool Name | Schema / Purpose | Dispatch Path | Error Handling | Coverage Needed | Covered? |
|-----------|------------------|---------------|----------------|-----------------|----------|
| `knowledge_search` | `query`, `user_id`, `limit`. Enriched CEA search. | `QualityWrapper.search` | 400 (invalid user), 500 (DB down). | Success, Empty results, Score ranking. | No |
| `knowledge_add` | `content`, `user_id`, `durability`, `confidence`, etc. | `QualityWrapper.add` + `write_metadata` | Schema validation, LLM failures. | Metadata persistence (S09), Source type mapping. | No (S07/S09) |
| `knowledge_list` | `user_id`, `limit`. List user memories. | `QualityWrapper.list_facts` | 400 (invalid user). | Pagination, Filter accuracy. | Yes (C-18) |
| `knowledge_feedback` | `target_type`, `target_id`, `event_type`, `context`. | `QualityWrapper.record_feedback` | 400 (invalid type/ID). | Append success, Usefulness impact in search. | No |

---

## 3. Pipeline Stages (StateGraphs)

### CEA Extraction Flow (`cea_extraction_flow.py`)
- **`search_existing_facts`**: Queries `QualityWrapper` for overlap.
  - *Risk*: Search fails or returns stale data leading to extraction of existing facts.
- **`run_extraction_llm`**: Structured extraction with temporal resolution.
  - *Risk*: LLM hallucination, unparseable JSON, incorrect durability scoring.
- **`dispatch_results`**: Categorizes as NEW/DUPLICATE/SUPERSEDES/CONFLICTS.
  - *Risk*: Incorrect relationship detection, feedback log failures for conflicts.

### CEAc Enrichment Flow (`cea_enrichment_flow.py`)
- **`search_knowledge`**: Tool-call loop to gather vector/graph facts.
  - *Risk*: Loop timeout, tool failure, excessive token usage.
- **`evaluate_and_rank`**: Applies the ranking formula.
  - *Risk*: Zero-division in formula, incorrect `trustworthiness` weight.
- **`assemble_context`**: Formats the `_enriched_context` block.
  - *Risk*: Context window overflow, unquoted content injection.

---

## 4. Worker Behaviors

| Worker | Logic / Behavior | Retry / Error Paths | Coverage Needed | Covered? |
|--------|------------------|---------------------|-----------------|----------|
| `Compaction` | Triggers `cea_extraction_flow` on Tier 1 content. | Log warning, skip chunk on failure. | Compaction -> CEA handoff, StateGraph exception handling. | No |
| `Expiration` | `QualityWrapper.add()` triggers cleanup every N hours. | Silent fail (log only). | Interval guard logic, Atomic cleanup (vector+graph). | No |
| `Log Shipper` | Discovers and streams container logs to Postgres. | Docker API reconnect, Postgres retry. | Container discovery, Log enrichment (hostname resolution). | Yes (D-04) |

---

## 5. Configuration (app/config.py)

| Parameter | Default | Effect | Validation / Failure Mode |
|-----------|---------|--------|---------------------------|
| `extraction.durability_bias` | 0.7 | LLM durability prompting. | Invalid float -> default 0.7. |
| `memory_extraction.expiration` | None | TTL for memories. | Unparseable duration -> cleanup skipped. |
| `memory_extraction.rejection_rules` | [] | Regex filters for facts. | Invalid regex -> worker crashes or ignores rule. |
| `cea.trust_weights` | {...} | Source type weights. | Missing key -> KeyError in ranking. |

---

## 6. Error & Degradation Paths

- **Vector/Graph Divergence**: `QualityWrapper.add` succeeds in Postgres but fails in Neo4j.
  - *Need*: Test for partial failure recovery or consistency checks.
- **LLM Rate Limiting**: Extraction/Enrichment flows hit OpenAI/Gemini 429s.
  - *Need*: Test retry backoff in StateGraph nodes.
- **Natural Key Collision**: Migration 024 index blocks a write.
  - *Need*: Test that `QualityWrapper.add` handles `UniqueViolation` gracefully as "DUPLICATE".
- **Mem0 Connection Reset**: `reset_mem0_client()` logic.
  - *Need*: Simulate pool exhaustion and verify client recreation.

---

## 7. Database Operations

- **Migration 023**: `cea_quality_metadata` and `cea_feedback_events` creation.
- **Migration 024**: Natural-key index `(user_id, conversation_id, original_utterance)`.
- **Query `_get_usefulness_batch`**: Blends multiple feedback events into a score.
- **Query `cleanup_expired`**: Cascading delete across `mem0_memories` (id-based) and `cea_quality_metadata`.

---

## 8. Integration Points

- **`imperator_flow` -> `enrichment_flow`**: V2 `get_context` integration.
- **`stategraph_registry`**: Entry-point discovery of `context-broker-ae` / `context-broker-te`.
- **`QualityWrapper` -> `Mem0`**: ID passthrough for vector facts.
- **`QualityWrapper` -> `Neo4j`**: elementId passthrough for graph relations.

---

## 9. Critical Gaps Found

1. **Graph Metadata**: Enriched search for graph relations likely lacks metadata because `dispatch_results` only calls `write_metadata` for "fact" (vector) types.
2. **MCP Persistence**: `knowledge_add` tool calls do not trigger `write_metadata`, so manually added memories have no durability/confidence scores.
3. **Formula Stability**: The ranking formula in `evaluate_and_rank` has not been tested with edge cases (e.g., zero feedback events).
4. **Natural Key Dedup**: Audit shows that `QualityWrapper.add` might be comparing the wrong IDs for dedup (computed hash vs. database target_id), potentially breaking idempotency.

---
**Audit Complete.**
