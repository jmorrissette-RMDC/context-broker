# Gate 2 Round 2 — Functional Review (Opus)

**Reviewer:** Claude Opus 4.6 (1M context)
**Date:** 2026-03-22
**Scope:** Behavioral comparison of new Context Broker against original Rogers code
**Pass:** 3

---

## Summary

The Context Broker faithfully reimplements the core Rogers pipeline: message storage with dedup, async embedding with contextual prefix, three-tier context assembly with incremental summarization, hybrid search with RRF and cross-encoder reranking, and Mem0-based memory extraction with secret redaction. The refactor intentionally drops ecosystem-specific coupling (peer proxies, build type DB table, Sutherland adapters, Node.js gateway) in favor of direct OpenAI-compatible provider access, nginx gateway, and YAML-configured build types. These are architectural changes, not lost behavior.

The findings below focus on behavioral differences that may result in lost functionality or changed semantics relative to Rogers.

---

## Findings

### F-01: Memory extraction priority queue downgraded from ZSET to LIST

- **Original feature:** Rogers used a Redis ZSET (`memory_extraction_jobs`) with priority-based scoring. Messages at priority 0 (live user interactions) scored higher than priority 3 (bulk backlog) by large offsets (10^12 apart), guaranteeing strict priority ordering regardless of timestamp. Within a priority level, newest messages scored highest.
- **New implementation:** Context Broker uses a Redis LIST. In `message_pipeline.py`, user-role messages get `LPUSH` (front of queue) and others get `RPUSH` (back of queue). This is a two-level approximation of the original four-level priority system.
- **Severity:** minor
- **Notes:** The LIST approach preserves the basic intent (user messages are higher priority) but loses the granularity of the four-level priority system (P0-P3) and the within-priority timestamp ordering. Since the new system defaults to `priority=0` for all messages (no callers currently set differentiated priorities), this is acceptable for standalone use but worth documenting as a simplification.

### F-02: content_type guard removed from memory extraction eligibility

- **Original feature:** Rogers only queued memory extraction for messages with `content_type='conversation'`. The embed pipeline's `queue_memory_extraction` node explicitly checked `if msg.get("content_type") != "conversation" or msg.get("memory_extracted")` and skipped non-conversation content.
- **New implementation:** In `message_pipeline.py`, the `enqueue_background_jobs` function enqueues extraction for all non-duplicate messages regardless of `content_type`. The extraction flow (`memory_extraction.py`) queries messages where `memory_extracted IS NOT TRUE` with no `content_type` filter.
- **Severity:** minor
- **Notes:** This means system messages, tool outputs, and other non-conversational content will be sent to Mem0 for extraction. This is arguably better behavior for a standalone system (more knowledge captured) but changes the extraction scope. The `content_type` column exists in the schema and is stored on insert, so the filter could be re-added if needed.

### F-03: Embedding queued from message pipeline instead of being the sole job trigger

- **Original feature:** In Rogers, `conv_store_message` queued only an embedding job. The embed pipeline then fanned out to both context assembly AND memory extraction after embedding succeeded. This guaranteed that extraction and assembly only ran on embedded messages.
- **New implementation:** In `message_pipeline.py`, `enqueue_background_jobs` queues both `embed_message` and `extract_memory` jobs in parallel immediately after message storage. Extraction runs independently of whether embedding has completed.
- **Severity:** minor
- **Notes:** This is a deliberate architectural choice for better parallelism. The tradeoff: in Rogers, extraction only ran on messages that had been successfully embedded; in the new system, extraction can process messages that failed embedding. Since Mem0 extraction does not depend on embedding vectors, this is functionally correct. However, assembly is still correctly gated behind the embed pipeline (queued from `enqueue_context_assembly` inside `embed_pipeline.py`).

### F-04: Build type configuration moved from database table to YAML

- **Original feature:** Rogers stored build types in a `context_window_build_types` database table with columns like `trigger_threshold_percent`, tier percentages, etc. Build types were queried via `db.get_build_type(build_type_id)`.
- **New implementation:** Build types are defined in `config.yml` under `build_types:` and read via `get_build_type_config()`. No database table for build types exists.
- **Severity:** not a finding (intentional architectural change per REQ section 5.3)
- **Notes:** Documented for completeness. The YAML approach is the correct State 4 pattern.

### F-05: Dynamic tier percentage scaling removed

- **Original feature:** Rogers calculated tier proportions dynamically based on window size: `tier3_pct = 0.70 + 0.14 * min(1.0, max_limit / 1_000_000)`, scaling from 70% at small windows to 84% at 1M tokens. This made tier proportions adaptive to the context window size.
- **New implementation:** Tier percentages are fixed values from `config.yml` (e.g., `tier3_pct: 0.72` for standard-tiered). They do not scale based on the window's `max_token_budget`.
- **Severity:** minor
- **Notes:** The dynamic scaling was a Rogers-specific optimization for handling a range of window sizes (30k to 1M) with a single build type. The new system achieves the same outcome through user-defined build types: deployers can create build types with different tier percentages for different window sizes. The flexibility is preserved, just moved from code to configuration.

### F-06: Per-build-type LLM selection removed

- **Original feature:** Rogers selected different LLM backends per build type for summarization: `small-basic` used Sutherland (local Qwen), `standard-tiered` used Gemini Flash-Lite (cloud API). The `select_llm` node in context assembly chose the adapter based on `build_type_id`.
- **New implementation:** All summarization uses the single LLM configured in `config.yml` under `llm:`. There is no per-build-type LLM override.
- **Severity:** minor
- **Notes:** This is an intentional State 4 simplification. The single-LLM approach is cleaner for standalone deployment. For users who want different LLMs for different build types, this would need to be added as a build type config field (e.g., `llm_override:`). Not a regression for standalone use since users control the single LLM config.

### F-07: rogers_stats / DB counts endpoint removed

- **Original feature:** Rogers exposed a `rogers_stats` MCP tool that returned database counts and queue depths for the ecosystem's Apgar health monitoring system.
- **New implementation:** No equivalent stats tool. Prometheus metrics (`metrics_get`) provide queue depths but not database row counts.
- **Severity:** not a finding (intentional — ecosystem-specific Apgar integration)
- **Notes:** The Prometheus metrics endpoint covers observability for standalone deployments. Database row counts were Apgar-specific.

### F-08: Assembly trigger threshold check changed from absolute to delta-based

- **Original feature:** Rogers checked `conv["estimated_token_count"] >= threshold` (where threshold = `max_token_limit * trigger_threshold_percent`), comparing total conversation tokens against an absolute threshold derived from the window's budget.
- **New implementation:** In `embed_pipeline.py`, the trigger check counts only tokens added since `last_assembled_at` and compares against `max_budget * trigger_pct`. This is a delta-based check: "have enough new tokens accumulated since last assembly?"
- **Severity:** minor
- **Notes:** The new delta-based approach is arguably better. Rogers' absolute threshold meant that once a conversation exceeded the threshold, every single new message would trigger assembly (since the total always exceeds the threshold). The new approach only triggers when a meaningful amount of new content has accumulated. This is a behavioral improvement, not a regression.

### F-09: Summarization chunks are sequential in Rogers, concurrent in Context Broker

- **Original feature:** Rogers summarized chunks sequentially in a for loop, one LLM call at a time: `for chunk in chunks: summary_response = await loop.run_in_executor(None, lambda ct=chunk_text: llm.generate_response(...))`.
- **New implementation:** In `context_assembly.py`, chunks are summarized concurrently via `asyncio.gather`: `llm_results = await asyncio.gather(*[_summarize_chunk(chunk) for chunk in chunks])`. Summaries are then inserted sequentially to preserve order.
- **Severity:** not a finding (improvement)
- **Notes:** The new concurrent approach is faster. Ordering is preserved by sequential DB insertion after all LLM calls complete.

### F-10: Memory extraction two-tier LLM routing removed

- **Original feature:** Rogers routed extraction text to different LLMs based on text length: small texts went to a local LLM (Sutherland/Qwen), large texts went to Gemini Flash-Lite with a higher character budget (450k chars vs 90k). The `extraction_tier` in state tracked which tier was selected, and `_build_mem0_instance` created different Mem0 instances per tier.
- **New implementation:** All extraction uses the single configured LLM regardless of text size. The extraction character budget is a single tuning parameter (`extraction_max_chars: 90000`).
- **Severity:** minor
- **Notes:** Rogers' two-tier approach was an optimization for cost and speed (use cheap local LLM for small extractions, cloud LLM for large ones). The new system uses a single LLM, which is simpler but may be less cost-efficient for deployments with both local and cloud LLMs available. The 90k character limit matches Rogers' small-tier limit, so large-context extraction is effectively lost.

### F-11: Mem0 deduplication index not applied at startup

- **Original feature:** The Rogers `init.sql` / migration code applied a deduplication index on Mem0's tables after Mem0 initialized them.
- **New implementation:** The `init.sql` has a comment noting "The application attempts to create this index after Mem0 initializes" but no startup code actually creates this index. The `migrations.py` file contains no migration for the Mem0 dedup index.
- **Severity:** major
- **Notes:** Without the dedup index, Mem0 may store duplicate memories for the same facts extracted from overlapping conversation windows. This was an explicit fix in Rogers (referenced as "Fix 2A" in the extraction flow). The index needs to be added as a migration or startup hook that runs after Mem0's first initialization creates its tables.

### F-12: Imperator architecture changed from manual ReAct to LangGraph bind_tools

- **Original feature:** Rogers' Imperator used a manual ReAct loop with JSON-based tool selection: the LLM was prompted to return JSON specifying which tool to call, and the code parsed the JSON to dispatch. Tools were limited to `conv_search` and `mem_search`.
- **New implementation:** The Context Broker Imperator uses LangChain's `bind_tools()` with `ToolNode` for proper tool calling. It has the same two base tools (`conv_search`, `mem_search`) plus optional admin tools (`config_read`, `db_query`). It also loads conversation history from PostgreSQL for cross-restart persistence and uses externalized prompt templates.
- **Severity:** not a finding (improvement)
- **Notes:** The new implementation is strictly superior: proper tool calling protocol, cross-restart history, admin tools, externalized prompts.

### F-13: conv_search missing flow_id, user_id, sender_id structured filters

- **Original feature:** Rogers' `conv_search` supported structured filters: `flow_id`, `user_id`, `sender_id`, `external_session_id`, and date range. These were passed through to `db.search_conversations()` for SQL WHERE clause construction.
- **New implementation:** The `SearchConversationsInput` model and `search_conversations_db` node only support `query`, `limit`, `offset`, `date_from`, and `date_to`. The `flow_id`, `user_id`, and `sender_id` structured filters are absent.
- **Severity:** major
- **Notes:** The `conversations` table has `flow_id` and `user_id` columns (added by migration 005), but the search flow does not expose them as filters. Callers who need to scope searches to a specific flow or user cannot do so. This is a functional regression from Rogers.

### F-14: conv_search_messages missing sender_id and role SQL-level filters

- **Original feature:** Rogers passed `sender_id` and `role` filters into the database query itself, filtering at the SQL level for efficiency.
- **New implementation:** In `search_flow.py`, the `hybrid_search_messages` function runs the full hybrid SQL query without sender_id, role, date_from, or date_to filters. These filters are applied in Python after the query returns, in a post-filter loop. This means the SQL query over-fetches candidates that will be discarded.
- **Severity:** minor
- **Notes:** The post-filtering approach is functionally correct but less efficient. For large databases, pushing filters into the SQL WHERE clause would reduce I/O. The date filters in particular could miss candidates if the candidate_limit is reached before the relevant date range.

### F-15: Conversation search does not filter by flow_id and user_id at SQL level

- **Original feature:** Rogers' `db.search_conversations()` accepted `flow_id`, `user_id`, `sender_id` as SQL WHERE filters.
- **New implementation:** See F-13 above. These filters are not present at all.
- **Severity:** covered by F-13
- **Notes:** N/A

### F-16: No idempotency_key dedup check for collapsed messages

- **Original feature:** Rogers' dedup checked consecutive messages by sender+content to skip identical consecutive messages from the same sender.
- **New implementation:** The new code also performs this check (F-04 in `message_pipeline.py`). However, the collapsed message path returns `was_collapsed: True` without checking the `idempotency_key`. If a caller resends a message with the same idempotency_key after it was collapsed, the collapse will fire again (incrementing `repeat_count` again) instead of being caught by the idempotency guard.
- **Severity:** minor
- **Notes:** The collapse check runs before the INSERT with ON CONFLICT, so the idempotency_key is never tested for collapsed messages. This could lead to `repeat_count` being incremented multiple times for retried requests. The fix would be to check idempotency_key before the collapse check.

### F-17: Assembly lock TTL increased from 120s to 300s

- **Original feature:** Rogers used a 120-second TTL for the `assembly_in_progress` Redis lock: `await redis.set(assembly_key, "1", ex=120, nx=True)`.
- **New implementation:** The TTL defaults to 300 seconds (configurable via `assembly_lock_ttl_seconds`).
- **Severity:** minor
- **Notes:** The longer TTL provides more headroom for slow LLM calls but also means that if a worker crashes mid-assembly, the lock blocks other assemblies for longer. Since it is configurable, this is not a functional regression.

### F-18: Missing nomic-embed-text dimension mapping in Mem0 client

- **Original feature:** Rogers' Sutherland embedder adapter hardcoded 768 dimensions matching the deployed embedding model.
- **New implementation:** The `_get_embedding_dims` function in `mem0_client.py` has a lookup table for OpenAI models (`text-embedding-3-small: 1536`, etc.) but no entry for `nomic-embed-text`, which is the default embedding model in `config.example.yml`. The function defaults to 1536 for unknown models.
- **Severity:** major
- **Notes:** The default config uses `nomic-embed-text` via Ollama, which produces 768-dimensional embeddings. But `_get_embedding_dims` returns 1536 for this model (default fallback). This dimension mismatch will cause Mem0's pgvector storage to fail or produce incorrect similarity results. The fix is to either add `nomic-embed-text` to the dims_map with value 768, or add an `embedding_dims` field to the config example set to 768.

---

## Summary Table

| # | Finding | Severity | Category |
|---|---------|----------|----------|
| F-01 | Priority ZSET downgraded to LIST | minor | simplification |
| F-02 | content_type guard removed from extraction | minor | scope change |
| F-03 | Extraction queued in parallel with embedding | minor | architecture |
| F-04 | Build types in YAML not DB | n/a | intentional |
| F-05 | Dynamic tier scaling removed | minor | simplification |
| F-06 | Per-build-type LLM selection removed | minor | simplification |
| F-07 | Stats endpoint removed | n/a | intentional |
| F-08 | Assembly trigger changed to delta-based | minor | improvement |
| F-09 | Concurrent chunk summarization | n/a | improvement |
| F-10 | Two-tier extraction LLM routing removed | minor | simplification |
| F-11 | Mem0 dedup index not applied at startup | major | missing behavior |
| F-12 | Imperator uses bind_tools | n/a | improvement |
| F-13 | conv_search missing structured filters | major | regression |
| F-14 | Message search filters applied post-query | minor | efficiency |
| F-16 | Idempotency not checked for collapsed msgs | minor | edge case |
| F-17 | Assembly lock TTL increased | minor | config change |
| F-18 | nomic-embed-text dimension mismatch | major | configuration bug |

**Blockers:** 0
**Majors:** 3 (F-11, F-13, F-18)
**Minors:** 10
