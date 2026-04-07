Below is a functionality-preservation review of the new Context Broker against Rogers, focused on behavior rather than architecture.

## Summary

The rewrite preserves most of Rogers’s core capabilities:

- core MCP conversation tools are present
- async message → embedding → context assembly → memory extraction pipeline is preserved
- three-tier context assembly is preserved and improved/configured
- memory search/context retrieval is preserved
- message hybrid search with BM25 + vector + reranking is preserved
- dead-letter / retry worker behavior is preserved
- health/metrics/config/deployment patterns are broadly preserved or improved

However, there are several real feature gaps relative to Rogers:

1. **Conversation/message structured search filters are significantly reduced**  
2. **Rogers’s internal memory admin endpoints/tools (`mem_add`, `mem_list`, `mem_delete`, `mem_extract`) are missing**
3. **Rogers’s context-window lookup by exact window ID via search endpoint is missing**
4. **Consecutive duplicate-message collapsing behavior was replaced with idempotency-key dedup only**
5. **Imperator capabilities are reduced vs Rogers in some practical ways**
6. **Some retrieval/search nuances changed (recency bias, top-N specifics, build-type-specific summarizer selection, etc.)**

These are detailed below.

---

# Findings

## 1) `conv_create_conversation` basic creation preserved, but Rogers supported caller-supplied IDs and metadata
- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py` → `create_conversation()`  
  Rogers created or reused a conversation using:
  - `conversation_id`
  - `flow_id`
  - `flow_name`
  - `user_id`
  - `title`  
  It used `ON CONFLICT DO NOTHING`, so the operation was idempotent by conversation ID and preserved ecosystem metadata.
- **New implementation:**  
  `app/flows/conversation_ops_flow.py` → `create_conversation_node()`  
  Creates a new conversation with generated UUID and optional `title` only.
- **Severity:** major
- **Notes:**  
  This is a real behavior loss if callers depended on:
  - externally assigned conversation IDs
  - flow/user metadata
  - idempotent “create-or-return-existing” semantics keyed by supplied conversation ID  
  The new tool still exists, but functionality is narrower.

---

## 2) `conv_create_context_window` preserved at a basic level, but participant/build-type model changed
- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py` → `create_context_window()`  
  Created context windows by `conversation_id`, `build_type_id`, `max_token_limit`.
- **New implementation:**  
  `app/flows/conversation_ops_flow.py` → `resolve_token_budget_node()`, `create_context_window_node()`  
  Creates by:
  - `conversation_id`
  - `participant_id`
  - `build_type`
  - optional `max_tokens_override`
- **Severity:** minor
- **Notes:**  
  Functionality is preserved and expanded for per-participant windows. This is not a loss. The main difference is schema/API shape. Since behavior remains available, this should not be flagged as missing.

---

## 3) `conv_store_message` preserved, but duplicate handling behavior changed materially
- **Original feature:**  
  `rogers-langgraph/flows/message_pipeline.py` → `dedup_check()`  
  Rogers detected **consecutive duplicate messages from the same sender**, updated the original message content with a `[repeated N times]` suffix, and did not insert a new row.
- **New implementation:**  
  `app/flows/message_pipeline.py` → `check_idempotency()`  
  New system deduplicates only via explicit `idempotency_key`; otherwise every repeated message is stored as a new row.
- **Severity:** major
- **Notes:**  
  This is a substantial behavioral change. Rogers had automatic duplicate-collapse protection for loops/repeats even if the caller supplied nothing. New code only dedups if the caller provides an idempotency key. That is not equivalent.

---

## 4) Message storage pipeline preserved
- **Original feature:**  
  `rogers-langgraph/flows/message_pipeline.py` → `store_message()`, `queue_embed()`  
  Stored messages, incremented conversation counters, queued embedding.
- **New implementation:**  
  `app/flows/message_pipeline.py` → `store_message()`, `enqueue_background_jobs()`
- **Severity:** none
- **Notes:**  
  Core store-and-enqueue behavior is preserved. New version additionally queues memory extraction immediately instead of after embedding.

---

## 5) Background processing fan-out preserved, but sequencing changed
- **Original feature:**  
  Rogers pipeline:
  - `conv_store_message` queued embedding
  - embedding pipeline then queued:
    - context assembly
    - memory extraction  
  (`rogers-langgraph/flows/embed_pipeline.py`)
- **New implementation:**  
  `app/flows/message_pipeline.py` queues:
  - embed job
  - extraction job  
  and `app/flows/embed_pipeline.py` queues context assembly after embedding.
- **Severity:** minor
- **Notes:**  
  End result is preserved, but extraction is now triggered earlier and independently of embedding completion. This is acceptable behaviorally unless callers assumed extraction only happened post-embedding.

---

## 6) Contextual embedding preserved
- **Original feature:**  
  `rogers-langgraph/flows/embed_pipeline.py` → `embed_message()`  
  Built contextual embeddings by prefixing N prior messages (`embedding.context_window_size`, default 3).
- **New implementation:**  
  `app/flows/embed_pipeline.py` → `generate_embedding()`
- **Severity:** none
- **Notes:**  
  Functionality is preserved closely, including configurable prior-context window and truncation.

---

## 7) Context assembly locking preserved
- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py` → `set_assembly_flag()`, `clear_assembly_flag()`
- **New implementation:**  
  `app/flows/context_assembly.py` → `acquire_assembly_lock()`, `release_assembly_lock()`
- **Severity:** none
- **Notes:**  
  Preserved. TTL changed/configured, but behavior is equivalent.

---

## 8) Three-tier progressive compression preserved
- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py` and `flows/retrieval.py`  
  Tier 1 archival, tier 2 chunk summaries, tier 3 recent verbatim messages.
- **New implementation:**  
  `app/flows/context_assembly.py`, `app/flows/retrieval_flow.py`
- **Severity:** none
- **Notes:**  
  Preserved. New version is more configurable and cleaner.

---

## 9) Incremental tier-2 summarization preserved
- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py` → `calculate_tiers()`  
  Avoided re-summarizing already covered ranges by checking active tier-2 summaries.
- **New implementation:**  
  `app/flows/context_assembly.py` → `calculate_tier_boundaries()`
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 10) Tier-1 consolidation preserved
- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py` → `consolidate_tier1()`
- **New implementation:**  
  `app/flows/context_assembly.py` → `consolidate_archival_summary()`
- **Severity:** none
- **Notes:**  
  Preserved. Threshold/keep-recent are configurable in both.

---

## 11) Build-type-specific summarizer selection is reduced
- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py` → `_build_summarization_llm()`  
  Rogers selected different summarization LLMs by build type:
  - `standard-tiered` → Gemini
  - otherwise → Sutherland/local
- **New implementation:**  
  `app/flows/context_assembly.py` → `summarize_message_chunks()`, `consolidate_archival_summary()`  
  Uses a single `config["llm"]` provider/model for summarization.
- **Severity:** minor
- **Notes:**  
  Summarization still works, but Rogers had per-build-type model-routing behavior that is not present. If build types were meant to imply different summarizers, that is lost.

---

## 12) `conv_retrieve_context` preserved and expanded with semantic/KG enrichment
- **Original feature:**  
  `rogers-langgraph/flows/retrieval.py`  
  Loaded summaries + recent messages, blocked on assembly lock.
- **New implementation:**  
  `app/flows/retrieval_flow.py`
- **Severity:** none
- **Notes:**  
  Preserved and expanded with semantic retrieval and knowledge graph facts for enriched build types.

---

## 13) Retrieval wait-on-assembly preserved
- **Original feature:**  
  `rogers-langgraph/flows/retrieval.py` → `check_assembly()`
- **New implementation:**  
  `app/flows/retrieval_flow.py` → `wait_for_assembly()`
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 14) Retrieval XML-style formatting preserved
- **Original feature:**  
  `rogers-langgraph/flows/retrieval.py` → `assemble()`
- **New implementation:**  
  `app/flows/retrieval_flow.py` → `assemble_context_text()`
- **Severity:** none
- **Notes:**  
  Preserved, with additional sections for semantic context and knowledge graph.

---

## 15) Rogers conversation search structured filters are mostly missing
- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py` → `search_conversations()` and `services/database.py` → `search_conversations()`  
  Rogers supported semantic search plus structured filters including:
  - `flow_id`
  - `user_id`
  - `sender_id`
  - `external_session_id`
  - `date_from`
  - `date_to`
- **New implementation:**  
  `app/flows/search_flow.py` → `build_conversation_search_flow()` / `search_conversations_db()`  
  Supports only:
  - `query`
  - `limit`
  - `offset`
- **Severity:** major
- **Notes:**  
  This is one of the largest feature regressions. Search exists, but many original filtering operations are gone.

---

## 16) Rogers message search structured filters are reduced
- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py` → `search_messages()` and `services/database.py` → `search_messages()`  
  Rogers supported filters:
  - `conversation_id`
  - `sender_id`
  - `role`
  - `external_session_id`
  - `date_from`
  - `date_to`
- **New implementation:**  
  `app/flows/search_flow.py` → `MessageSearchState`, `hybrid_search_messages()`  
  Supports only:
  - `query`
  - optional `conversation_id`
  - `limit`
- **Severity:** major
- **Notes:**  
  Hybrid search is preserved, but most structured filtering capability was lost.

---

## 17) Hybrid search preserved
- **Original feature:**  
  `services/database.py` → `search_messages()`  
  Hybrid vector + BM25 via RRF.
- **New implementation:**  
  `app/flows/search_flow.py` → `hybrid_search_messages()`
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 18) Cross-encoder reranking preserved, but provider support differs
- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py` → `search_messages()`  
  Used external rerank tool (`llm_rerank`) via Sutherland, top-10 final results.
- **New implementation:**  
  `app/flows/search_flow.py` → `rerank_results()`  
  Supports local `sentence_transformers.CrossEncoder`, or none.
- **Severity:** minor
- **Notes:**  
  Core reranking behavior is preserved. Provider differs, which is architectural. No functionality loss except that Rogers effectively had an external rerank service route whereas new code does not support a hosted/cohere-style reranker despite config comments mentioning it.

---

## 19) Rogers recency bias in message hybrid search is missing
- **Original feature:**  
  `rogers-langgraph/services/database.py` → `search_messages()`  
  Hybrid search applied mild recency bias to final RRF score.
- **New implementation:**  
  `app/flows/search_flow.py` → `hybrid_search_messages()`  
  No recency bias in scoring.
- **Severity:** minor
- **Notes:**  
  Search quality behavior differs. Not core loss, but Rogers had this ranking nuance.

---

## 20) Rogers conversation semantic search grouped by best matching message preserved at high level
- **Original feature:**  
  `services/database.py` → `search_conversations()`  
  Searched messages by embedding similarity and grouped to conversation score.
- **New implementation:**  
  `app/flows/search_flow.py` → `search_conversations_db()`
- **Severity:** none
- **Notes:**  
  Preserved at a high level, though with fewer filters.

---

## 21) `conv_get_history` preserved, but limit support changed from full-only to optional limit
- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py` → `get_history()`  
  Returned full conversation history chronologically.
- **New implementation:**  
  `app/flows/conversation_ops_flow.py` → `load_conversation_and_messages()`
- **Severity:** none
- **Notes:**  
  Preserved and expanded with optional limit.

---

## 22) `conv_search_context_windows` preserved, but exact-ID lookup capability is missing
- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py` → `search_context_windows_handler()`  
  Supported search by:
  - `context_window_id`
  - `conversation_id`
  - `build_type_id`
- **New implementation:**  
  `app/flows/conversation_ops_flow.py` → `search_context_windows_node()`  
  Supports:
  - `conversation_id`
  - `participant_id`
  - `build_type`
  - `limit`
- **Severity:** major
- **Notes:**  
  Rogers explicitly supported exact ID lookup through the search/list endpoint. New code lacks `context_window_id` filter entirely.

---

## 23) `mem_search` preserved
- **Original feature:**  
  `rogers-langgraph/flows/memory_ops.py` → `mem_search()`
- **New implementation:**  
  `app/flows/memory_search_flow.py` → `search_memory_graph()`
- **Severity:** none
- **Notes:**  
  Preserved with degraded-mode behavior.

---

## 24) `mem_get_context` preserved
- **Original feature:**  
  `rogers-langgraph/flows/memory_ops.py` → `mem_get_context()`
- **New implementation:**  
  `app/flows/memory_search_flow.py` → `retrieve_memory_context()`
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 25) Internal memory admin operations are missing
- **Original feature:**  
  Rogers exposed internal HTTP endpoints:
  - `mem_add`
  - `mem_list`
  - `mem_delete`
  - `mem_extract`  
  via `rogers-langgraph/server.py` and `flows/memory_ops.py`
- **New implementation:**  
  MISSING
- **Severity:** major
- **Notes:**  
  These were not MCP-exposed client tools, but they were part of original system functionality for admin/background usage. The rewrite has no equivalents exposed as routes or tools.

---

## 26) Background memory extraction preserved, but some Rogers routing behavior changed
- **Original feature:**  
  `rogers-langgraph/flows/memory_extraction.py`
  - fetched only unextracted conversation messages
  - chose small vs large extraction tier based on text length
  - used lock
  - marked selected messages extracted
- **New implementation:**  
  `app/flows/memory_extraction.py`
  - fetches unextracted messages
  - uses lock
  - builds bounded text
  - extracts via Mem0
  - marks extracted
- **Severity:** minor
- **Notes:**  
  Core extraction behavior is preserved. Missing is Rogers’s **small/large LLM tier selection** based on extraction size.

---

## 27) Small-vs-large Mem0 extraction model selection is missing
- **Original feature:**  
  `rogers-langgraph/flows/memory_extraction.py` → `build_extraction_text()`, `run_mem0_extraction()`  
  Chose extraction tier (`small`/`large`) based on character budget and routed to different Mem0/LLM setups.
- **New implementation:**  
  `app/flows/memory_extraction.py` → `run_mem0_extraction()`  
  Uses single Mem0 client path.
- **Severity:** minor
- **Notes:**  
  Extraction still works, but Rogers had adaptive model selection for larger text payloads.

---

## 28) Secret redaction before Mem0 ingestion is missing
- **Original feature:**  
  `rogers-langgraph/services/secret_filter.py` and use from:
  - `flows/memory_extraction.py`
  - `flows/memory_ops.py`  
  Redacted likely secrets before sending text to Mem0.
- **New implementation:**  
  MISSING
- **Severity:** major
- **Notes:**  
  This is real lost behavior. New extraction pipeline sends raw message text to Mem0.

---

## 29) Memory extraction lock preserved
- **Original feature:**  
  `rogers-langgraph/flows/memory_extraction.py` → `acquire_extraction_lock()`, `release_extraction_lock()`
- **New implementation:**  
  `app/flows/memory_extraction.py` → same-named behavior
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 30) Queue worker retries/backoff/dead-letter preserved
- **Original feature:**  
  `rogers-langgraph/services/queue_worker.py`
- **New implementation:**  
  `app/workers/arq_worker.py`
- **Severity:** none
- **Notes:**  
  Preserved well:
  - retries
  - backoff
  - dead-letter queue
  - periodic sweep/requeue

---

## 31) Priority-based memory extraction ordering is missing
- **Original feature:**  
  `rogers-langgraph/flows/embed_pipeline.py` and `services/queue_worker.py`  
  Used a Redis ZSET with scored priorities for memory extraction jobs.
- **New implementation:**  
  `app/flows/message_pipeline.py` and `app/workers/arq_worker.py`  
  Uses a plain Redis list for `memory_extraction_jobs`.
- **Severity:** minor
- **Notes:**  
  Background extraction still occurs, but Rogers’s priority scheduling behavior is lost.

---

## 32) Queue dedup for jobs improved/preserved in different form
- **Original feature:**  
  Rogers retried jobs and used lock checks to avoid duplicate assembly work.
- **New implementation:**  
  `app/flows/message_pipeline.py` → Redis dedup keys for embed/extract queueing  
  `app/flows/embed_pipeline.py` → skip assembly queue if lock exists
- **Severity:** none
- **Notes:**  
  Preserved/improved.

---

## 33) Imperator/basic agent functionality preserved, but toolset is reduced
- **Original feature:**  
  `rogers-langgraph/flows/imperator.py`  
  Tool choices:
  - `conv_search`
  - `mem_search`
  - direct response
  - finish
- **New implementation:**  
  `app/flows/imperator_flow.py`  
  Tools:
  - `conv_search_tool`
  - `mem_search_tool`
- **Severity:** minor
- **Notes:**  
  Broadly preserved. New version uses LangChain tool binding more idiomatically. However, Rogers used direct MCP peer tools and had a somewhat broader “finalize/finish/respond directly” decision loop baked into its custom state logic.

---

## 34) Imperator persistent state across restarts is preserved, but conversation linkage behavior differs
- **Original feature:**  
  Rogers Imperator graph did not appear to have standalone persisted conversation-file state like the new system; it was more request-scoped by `user_id`.
- **New implementation:**  
  `app/imperator/state_manager.py`
- **Severity:** none
- **Notes:**  
  This is additive, not a loss.

---

## 35) `broker_chat` tool exists, but Rogers had `rogers_chat` rather than `broker_chat`
- **Original feature:**  
  `rogers-langgraph/server.py` → `_tool_registry["rogers_chat"]`
- **New implementation:**  
  `app/flows/tool_dispatch.py` → `broker_chat`
- **Severity:** minor
- **Notes:**  
  If strict external compatibility with old tool names matters, this is a gap. Under the new requirements, `broker_chat` is the intended interface. Since your task is preservation vs Rogers, the old name/function is not present.

---

## 36) OpenAI-compatible chat endpoint is new/additive, not a loss
- **Original feature:**  
  Rogers did not expose OpenAI-compatible `/v1/chat/completions`.
- **New implementation:**  
  `app/routes/chat.py`
- **Severity:** none
- **Notes:**  
  Additive only.

---

## 37) Rogers stats/Apgar internal tool missing
- **Original feature:**  
  `rogers/config.json` had internal tool `rogers_stats`; `server.py` had metrics endpoints for Apgar scraping.
- **New implementation:**  
  MISSING as `rogers_stats`
- **Severity:** minor
- **Notes:**  
  There is `metrics_get` and `/metrics`, so observability is present. Exact internal tool parity is not.

---

## 38) Health endpoint preserved, but Neo4j criticality changed to degraded
- **Original feature:**  
  `rogers-langgraph/server.py` `/health` checked only postgres + redis for healthy/unhealthy; README/config treated neo4j as critical at gateway level.
- **New implementation:**  
  `app/routes/health.py`  
  Checks postgres, redis, neo4j; postgres+redis are critical, neo4j degrades.
- **Severity:** minor
- **Notes:**  
  Not a loss in capability; semantics differ.

---

## 39) Metrics endpoint/tool preserved
- **Original feature:**  
  `server.py` → `/metrics`, `/metrics_get`
- **New implementation:**  
  `app/routes/metrics.py`, `app/flows/metrics_flow.py`, `tool_dispatch.py` → `metrics_get`
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 40) Search candidate/rerank limits differ from Rogers
- **Original feature:**  
  Rogers effectively did:
  - top 50 hybrid candidates
  - top 10 reranked results  
  (`config.json` search block and code)
- **New implementation:**  
  `app/flows/search_flow.py`
  - candidate limit configurable, default 100
  - reranked top = requested `limit`
- **Severity:** minor
- **Notes:**  
  Behavioral difference, not missing functionality.

---

## 41) Conversation metadata schema operations from Rogers are missing
- **Original feature:**  
  Rogers conversation records included and queried:
  - `user_id`
  - `flow_id`
  - `flow_name`
- **New implementation:**  
  MISSING in schema and flows
- **Severity:** major
- **Notes:**  
  This impacts search/filtering and external correlation use cases. It is more than just a schema change because associated behavior is gone.

---

## 42) External session/content-type/priority message fields from Rogers are missing
- **Original feature:**  
  `services/database.py` → `insert_message()` and schema supported:
  - `external_session_id`
  - `content_type`
  - `priority`
- **New implementation:**  
  `app/flows/message_pipeline.py` / schema: only role, sender_id, content, token_count, model_name, idempotency_key
- **Severity:** major
- **Notes:**  
  This is real lost behavior:
  - provenance (`external_session_id`)
  - content classification (`content_type`)
  - priority handling for downstream queues

---

## 43) `conv_store_message` no longer accepts context-window-only addressing
- **Original feature:**  
  `rogers-langgraph/flows/message_pipeline.py` → `resolve_conversation()`  
  Allowed caller to provide `context_window_id` instead of `conversation_id`.
- **New implementation:**  
  `app/models.py` → `StoreMessageInput` requires `conversation_id`; no `context_window_id` path.
- **Severity:** major
- **Notes:**  
  This is a direct API behavior loss.

---

## 44) Semantic retrieval in assembled context is additive relative to Rogers
- **Original feature:**  
  Rogers retrieval did not inject semantic message recalls into context assembly.
- **New implementation:**  
  `app/flows/retrieval_flow.py` → `inject_semantic_retrieval()`
- **Severity:** none
- **Notes:**  
  Additive.

---

## 45) Knowledge-graph fact injection into context is additive relative to Rogers
- **Original feature:**  
  Rogers had memory search tools but not retrieval-time KG fact injection into `conv_retrieve_context`.
- **New implementation:**  
  `app/flows/retrieval_flow.py` → `inject_knowledge_graph()`
- **Severity:** none
- **Notes:**  
  Additive.

---

# Blocker / Major / Minor rollup

## Blocker
None found. Core service still exists and operates.

## Major
1. `conv_create_conversation` lost caller-supplied ID and metadata (`flow_id`, `flow_name`, `user_id`)
2. `conv_store_message` lost automatic consecutive duplicate-collapse behavior
3. `conv_search` lost most structured filters
4. `conv_search_messages` lost most structured filters
5. `conv_search_context_windows` lost exact `context_window_id` lookup
6. Internal memory admin endpoints/tools (`mem_add`, `mem_list`, `mem_delete`, `mem_extract`) missing
7. Secret redaction before Mem0 ingestion missing
8. Conversation metadata behaviors (`user_id`, `flow_id`, `flow_name`) missing
9. Message metadata behaviors (`external_session_id`, `content_type`, `priority`) missing
10. `conv_store_message` no longer supports `context_window_id`-only addressing

## Minor
1. Build-type-specific summarizer model routing missing
2. Recency bias in hybrid message search missing
3. Small/large extraction model tiering missing
4. Priority-based extraction queue ordering missing
5. Tool-name parity (`rogers_chat`, `rogers_stats`) missing
6. Some Imperator practical behavior differs/reduced
7. Some exact search/rerank limit behaviors differ

---

# Bottom line

The rewrite preserves the **core Context Broker** behaviors well:
- conversation/message storage
- async enrichment pipelines
- three-tier assembly
- retrieval blocking/waiting
- memory extraction/search
- hybrid message search with reranking
- metrics/health/worker retry patterns

But it does **not** fully preserve Rogers functionality. The biggest regressions are:

- **reduced search/filter surface**
- **loss of automatic duplicate message collapsing**
- **loss of several metadata-bearing APIs/data fields**
- **missing internal memory admin endpoints**
- **missing secret redaction before memory ingestion**
- **loss of context-window-based message store addressing**

If you want, I can next turn this into a **CSV-style gap matrix** or a **must-fix checklist sorted by implementation effort**.