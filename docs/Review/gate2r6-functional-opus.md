# Gate 2 Round 6 Pass 3 — Functional Review (Opus)

**Date:** 2026-03-22
**Reviewer:** Claude Opus 4.6
**Scope:** All `.py` under `app/` (including `build_types/`), `docker-compose.yml`, `postgres/init.sql`, `config/config.example.yml` vs. original Rogers code and REQ-context-broker.md.

---

## Summary

The Context Broker faithfully reimplements the core Rogers feature set — message storage, three-tier assembly, hybrid search, memory extraction, and background job processing — while adding several features required by the new requirements (OpenAI-compatible chat, Imperator agent, build type registry, passthrough build type, tier scaling, memory confidence scoring). No blockers found. Two major items and several minor items are documented below.

---

## Findings

### F-01: MCP tool name mismatch — `broker_chat` vs `imperator_chat`

| Aspect | Detail |
|---|---|
| **Original feature** | REQ section 4.6 lists `broker_chat` as the MCP tool name for the conversational Imperator interface |
| **New implementation** | Tool is registered as `imperator_chat` in `_get_tool_list()` (mcp.py line 493) and dispatched as `imperator_chat` in tool_dispatch.py |
| **Severity** | Major |
| **Notes** | Any external client following the requirements document or README will call `broker_chat` and get "Unknown tool" back. Either the tool name or the requirements need to be aligned. The tool_dispatch, mcp tool list, and models (`ImperatorChatInput`) all consistently use `imperator_chat`, so the code is internally consistent — it just diverges from the published spec. |

### F-02: `conv_store_message` no longer enqueues assembly and extraction in parallel after embedding

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers README: "Steps 3 and 4 queue **in parallel** after embedding completes." Context assembly and memory extraction were both enqueued after embedding. |
| **New implementation** | `message_pipeline.py` enqueues embedding AND memory extraction in `enqueue_background_jobs` (lines 222-278). Context assembly is then enqueued separately inside `embed_pipeline.py` after embedding completes. So memory extraction is enqueued immediately (before embedding finishes), while assembly waits for embedding. |
| **Severity** | Minor |
| **Notes** | This is actually an improvement: memory extraction doesn't depend on the embedding, so starting it earlier is correct. The original Rogers behavior of waiting for embedding before queuing extraction was unnecessarily sequential. Assembly still correctly waits for embedding since it needs to check all windows. Not a regression. |

### F-03: `context_window_build_types` table removed — build types are config-only

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers had a `context_window_build_types` DB table storing strategy templates (small-basic, standard-tiered). Build types were database entities. |
| **New implementation** | Build types are defined entirely in `config.yml` and resolved via `get_build_type_config()` at runtime. No DB table. |
| **Severity** | Minor |
| **Notes** | This is an intentional design change per REQ section 5.3. Config-only build types are simpler and hot-reloadable. The old DB-based approach is correctly superseded. |

### F-04: Per-build-type LLM selection preserved but mechanism changed

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers selected LLM by build type: Sutherland (local) for small-basic, Gemini Flash-Lite (API) for standard-tiered. |
| **New implementation** | `_resolve_llm_config()` in standard_tiered.py reads `config["build_types"]["<name>"]["llm"]` and falls back to global `config["llm"]`. Documented in config.example.yml (lines 79-83, 97-100). |
| **Severity** | None (preserved) |
| **Notes** | Feature is correctly implemented. F-06 from the requirements is satisfied. |

### F-05: Dedup mechanism changed from skip to repeat_count collapse

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers: "Dedup check — first step; skips duplicate consecutive messages from same sender" |
| **New implementation** | `store_message()` in message_pipeline.py (lines 119-158) collapses consecutive identical messages by incrementing `repeat_count` instead of silently skipping. Returns `was_collapsed: True`. |
| **Severity** | Minor |
| **Notes** | This is a behavioral change. The original silently dropped duplicates. The new implementation preserves evidence of the duplicate (repeat_count) while still preventing redundant storage. This is arguably an improvement since no data is lost. The caller knows what happened via the `was_collapsed` flag. |

### F-06: Dead-letter sweep re-queues limited to 10 jobs per sweep

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers: "periodic sweep (every 60s, re-queues up to 10 jobs)" |
| **New implementation** | `_sweep_dead_letters()` in arq_worker.py (line 427): `for _ in range(10)` limits to 10 jobs per sweep. Sweep interval configurable via `dead_letter_sweep_interval_seconds` (default 60s). |
| **Severity** | None (preserved) |
| **Notes** | Exact match with Rogers behavior. Additionally, the new implementation adds `max_total_dead_letter_attempts` to prevent infinite retry loops (line 452), which is an improvement. |

### F-07: Exponential backoff uses delayed sorted sets instead of sleep

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers: "jobs queue in Redis with 3-retry exponential backoff" |
| **New implementation** | `_handle_job_failure()` (arq_worker.py lines 249-289) computes backoff but pushes to `{queue_name}:delayed` sorted set with `retry_after` timestamp instead of sleeping. `_sweep_delayed_queues()` promotes ready jobs. |
| **Severity** | Minor |
| **Notes** | Behavioral improvement: the consumer loop is never blocked by sleeping during backoff. The retry count (max 3) matches Rogers. |

### F-08: Assembly trigger threshold implemented

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers queued assembly "when threshold crossed" but the threshold was implicit. |
| **New implementation** | `enqueue_context_assembly()` in embed_pipeline.py (lines 225-246) checks `trigger_threshold_percent` (default 10% of token budget). Only queues assembly when new tokens since last assembly exceed the threshold. |
| **Severity** | None (preserved/improved) |
| **Notes** | This is a more explicit implementation of the Rogers threshold behavior. The threshold is configurable per build type and globally. |

### F-09: `conv_search` missing `external_session_id` filter

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers config.json: conv_search description mentions "external_session_id" as a filter |
| **New implementation** | No `external_session_id` field exists in `SearchConversationsInput` (models.py) or in the search SQL. The `conversations` table has no `external_session_id` column. |
| **Severity** | Minor |
| **Notes** | This appears to be an intentional simplification. The REQ document (section 4.6) lists `conv_search` with "semantic query + structured filters" but does not enumerate `external_session_id` specifically. The field was Rogers-ecosystem-specific and is not relevant to the standalone Context Broker. |

### F-10: `mem_extract` internal endpoint not implemented as HTTP

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers README: "4 additional internal tools (`mem_add`, `mem_list`, `mem_delete`, `mem_extract`) are accessible as HTTP endpoints but not exposed via MCP" |
| **New implementation** | `mem_add`, `mem_list`, `mem_delete` are exposed via MCP (in the tool list). `mem_extract` does not exist as a callable tool — extraction is only triggered via the background job queue. No HTTP endpoints exist for any of the internal tools outside of MCP. |
| **Severity** | Minor |
| **Notes** | The REQ document does not require `mem_extract` as a tool. The three mem admin tools are actually promoted to MCP visibility, which is more useful. Extraction being queue-only is fine since it's a background operation. |

### F-11: Contextual embedding prefix window correctly implemented

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers v3.1: "Contextual embeddings: 3-message prefix window for all conversation message embeds" |
| **New implementation** | `generate_embedding()` in embed_pipeline.py (lines 82-106) fetches `context_window_size` prior messages (default 3) and prepends them as `[Context]` prefix before embedding. |
| **Severity** | None (preserved) |
| **Notes** | Exact behavioral match. The context window size is configurable via `embeddings.context_window_size` in config.yml. |

### F-12: Hybrid search (vector ANN + BM25 + RRF + cross-encoder) fully preserved

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers v3.1: "Hybrid RRF retrieval: vector ANN + BM25 full-text combined via Reciprocal Rank Fusion... Cross-encoder reranker: bge-reranker-v2-m3" |
| **New implementation** | `hybrid_search_messages()` in search_flow.py implements the full pipeline: vector ANN + BM25 CTEs, RRF combination, recency bias, then `rerank_results()` applies cross-encoder reranking via `sentence_transformers.CrossEncoder`. |
| **Severity** | None (preserved) |
| **Notes** | Full feature parity. Additionally adds configurable recency decay (F-10 in the search_flow), which is an improvement. |

### F-13: `conv_store_message` input changed from `conversation_id` to `context_window_id`

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers: `conv_store_message` accepted `conversation_id` directly |
| **New implementation** | `StoreMessageInput` requires `context_window_id` (ARCH-04). The message pipeline looks up `conversation_id` from the `context_windows` table. |
| **Severity** | Major |
| **Notes** | This is a deliberate architectural change (ARCH-04) that scopes message storage to a context window rather than a bare conversation. Callers migrating from Rogers must create a context window first and use its ID when storing messages. The REQ document (section 4.6) specifies this as the intended API. This is not lost behavior per se, but it is a breaking API change from Rogers that any migration must account for. |

### F-14: Three-tier assembly logic fully preserved

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers: Tier 1 archival, Tier 2 chunk summaries, Tier 3 recent verbatim. Incremental summarization. Consolidation of tier 2 into tier 1. |
| **New implementation** | `standard_tiered.py`: `calculate_tier_boundaries` splits messages, `summarize_message_chunks` does LLM chunk summarization with idempotency checks, `consolidate_archival_summary` rolls oldest tier 2 into tier 1. All logic preserved including the `consolidation_threshold` and `consolidation_keep_recent` parameters. |
| **Severity** | None (preserved) |
| **Notes** | The new implementation adds F-05 dynamic tier scaling, per-build-type LLM config (F-06), concurrent chunk summarization with semaphore limiting, and lock TTL renewal during long summarizations. All improvements over Rogers. |

### F-15: Retrieval wait-for-assembly behavior preserved

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers: "If assembly is in progress when context is requested, the call blocks and waits (up to 50s timeout)" |
| **New implementation** | `ret_wait_for_assembly()` / `ke_wait_for_assembly()` poll the Redis lock key with configurable timeout (default 50s) and poll interval (default 2s). Returns stale context with a warning on timeout. |
| **Severity** | None (preserved) |
| **Notes** | Exact behavioral match. |

### F-16: Memory extraction secret redaction added

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers had no explicit secret redaction before Mem0 ingestion |
| **New implementation** | `_redact_secrets()` in memory_extraction.py (lines 28-47) applies regex patterns to strip API keys, bearer tokens, sk- prefixes, and cloud provider secrets before sending to Mem0. |
| **Severity** | None (new feature) |
| **Notes** | New defensive feature not in Rogers. Good addition. |

### F-17: Imperator conversation persistence correctly implemented

| Aspect | Detail |
|---|---|
| **Original feature** | Not in Rogers (new feature per REQ) |
| **New implementation** | `ImperatorStateManager` reads/writes `/data/imperator_state.json` with `conversation_id` and `context_window_id`. On boot, verifies both exist in DB. Creates missing resources as needed. Imperator flow stores messages via `conv_store_message` pipeline (F-22). History loaded from DB on each invocation (ARCH-06). |
| **Severity** | None (new feature) |
| **Notes** | Correctly implements REQ section 3.2 and 5.5. |

### F-18: OpenAI-compatible chat endpoint correctly implemented

| Aspect | Detail |
|---|---|
| **Original feature** | Not in Rogers (new feature per REQ) |
| **New implementation** | `chat.py` route handles `/v1/chat/completions` with streaming (SSE) and non-streaming modes. Converts OpenAI message format to LangChain messages. Routes to Imperator flow. Response format matches OpenAI spec. |
| **Severity** | None (new feature) |
| **Notes** | Correctly implements REQ section 4.2. |

### F-19: Passthrough build type correctly implemented

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers had only two build types (small-basic, standard-tiered) |
| **New implementation** | `passthrough.py` implements assembly (no-op, just updates timestamp) and retrieval (loads recent messages as-is). Registered via build type registry. |
| **Severity** | None (new feature) |
| **Notes** | Correctly implements REQ section 5.3 passthrough requirement. |

### F-20: Knowledge-enriched build type correctly implemented

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers had no semantic retrieval or KG injection in the retrieval path |
| **New implementation** | `knowledge_enriched.py` extends standard-tiered retrieval with `ke_inject_semantic_retrieval` (pgvector similarity search) and `ke_inject_knowledge_graph` (Mem0 search). Budget-aware assembly respects `semantic_retrieval_pct` and `knowledge_graph_pct`. |
| **Severity** | None (new feature) |
| **Notes** | Correctly implements REQ section 5.3 knowledge-enriched requirement. |

### F-21: Memory confidence scoring with half-life decay (new feature)

| Aspect | Detail |
|---|---|
| **Original feature** | Not in Rogers |
| **New implementation** | `memory_scoring.py` applies exponential half-life decay by category (ephemeral/contextual/factual/historical). Applied at retrieval time in memory_search_flow and knowledge_enriched retrieval. Configurable half-lives in config.yml. |
| **Severity** | None (new feature) |
| **Notes** | Correctly implements M-22 requirement. |

### F-22: `build_types` DB table absent — no `small-basic` equivalent

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers had a `context_window_build_types` table with `small-basic` and `standard-tiered` entries |
| **New implementation** | No DB table. Config.yml defines `passthrough`, `standard-tiered`, and `knowledge-enriched`. No `small-basic` equivalent exists. |
| **Severity** | Minor |
| **Notes** | `small-basic` from Rogers was a minimal build type with no summarization, equivalent to the new `passthrough`. The name changed but the functionality is preserved. |

### F-23: Priority-based extraction queue (new feature)

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers used a simple Redis list for extraction jobs |
| **New implementation** | Memory extraction jobs use a Redis sorted set (ZADD/ZPOPMIN) with role-based priority scores. User messages get priority 1, assistant gets 2, etc. |
| **Severity** | None (new feature) |
| **Notes** | Improvement over Rogers. Higher-priority messages are extracted first. |

### F-24: Crash-safe lock cleanup in worker

| Aspect | Detail |
|---|---|
| **Original feature** | Rogers had basic retry but no explicit crash recovery |
| **New implementation** | `arq_worker.py`: On graph crash, assembly/extraction locks are deleted unconditionally (F-17). On startup, `_sweep_stranded_processing_jobs()` recovers jobs stuck in processing queues. |
| **Severity** | None (new feature) |
| **Notes** | Improvement over Rogers. |

---

## Verdict

**No blockers.** Two major items:

1. **F-01**: `broker_chat` vs `imperator_chat` tool name mismatch with the REQ document. Decide which is canonical and align.
2. **F-13**: `conv_store_message` API changed from `conversation_id` to `context_window_id`. This is by design (ARCH-04) but is a breaking change from Rogers that must be documented in migration guidance.

All core Rogers features (message storage, dedup, embedding pipeline, three-tier assembly, hybrid search with RRF + cross-encoder, memory extraction via Mem0, dead-letter queue, retry with backoff) are faithfully reimplemented. The new implementation adds several improvements: dynamic tier scaling, per-build-type LLM config, build type registry, passthrough and knowledge-enriched build types, memory confidence scoring, Imperator agent, OpenAI-compatible chat, crash-safe lock cleanup, non-blocking retry backoff, and secret redaction.
