Below is a feature-preservation audit comparing the new standalone Context Broker against Rogers, with intentional architectural changes excluded as requested.

---

# Summary

Overall, the rewrite preserves most of Rogers’s core behavior:

- MCP tool surface is largely preserved and expanded
- Core DB CRUD is preserved
- Background embedding / assembly / extraction pipelines are preserved
- Hybrid message search is preserved and improved in some areas
- Context assembly is preserved and extended via build types
- Imperator exists, but behavior differs meaningfully from Rogers’s original Imperator contract
- A few Rogers behaviors are missing or changed enough to be notable

The most important gaps I found are:

1. **Rogers retrieval’s direct text/XML context output is gone** — intentional return-format change, so not flagged.
2. **Conversation search structured filters no longer include `external_session_id`** — this is not listed as an intentional removal.
3. **Context window model lost participant-independent build-type semantics from Rogers and now requires `participant_id`** — may be architectural, but this is behaviorally different.
4. **Imperator no longer uses Rogers’s original MCP-tool-based decision loop / `user_id` contract** — functionality exists, but behavior and interface differ materially.
5. **Memory extraction queueing timing changed** — now parallel with embedding from message ingest, which was marked intentional, so not flagged as missing.

---

# Findings

## 1) `conv_create_conversation`
- **Original feature:**  
  Rogers created or returned an existing conversation by ID, storing `flow_id`, `flow_name`, `user_id`, `title`.  
  File/function: `rogers-langgraph/flows/conversation_ops.py::create_conversation`, `services/database.py::insert_conversation`
- **New implementation:**  
  `app/flows/conversation_ops_flow.py::create_conversation_node`
- **Severity:** minor
- **Notes:**  
  Preserved for `conversation_id`, `title`, `flow_id`, `user_id`.  
  **Difference:** Rogers also supported `flow_name`; new implementation does not store or expose `flow_name`.

---

## 2) `conv_create_context_window`
- **Original feature:**  
  Created a context window for `(conversation_id, build_type_id, max_token_limit)`. No participant dimension.  
  File/function: `rogers-langgraph/flows/conversation_ops.py::create_context_window`, `services/database.py::insert_context_window`
- **New implementation:**  
  `app/flows/conversation_ops_flow.py::resolve_token_budget_node`, `::create_context_window_node`
- **Severity:** major
- **Notes:**  
  New implementation requires `participant_id` and keys uniqueness on `(conversation_id, participant_id, build_type)`.  
  This is a substantial behavioral change from Rogers, where windows were conversation/build-type scoped, not participant-scoped.  
  It may align with the new requirements, but relative to Rogers functionality, it changes what callers must supply and how windows are identified.

---

## 3) `conv_store_message` basic storage path
- **Original feature:**  
  Resolved `conversation_id` from `context_window_id` if needed, deduped consecutive duplicates, inserted message, updated counters, queued embedding.  
  File/function: `rogers-langgraph/flows/message_pipeline.py::{resolve_conversation,dedup_check,store_message,queue_embed}`
- **New implementation:**  
  `app/flows/message_pipeline.py::{store_message,enqueue_background_jobs}`
- **Severity:** minor
- **Notes:**  
  Preserved overall. New code folds resolve/store logic into one node rather than separate nodes.

---

## 4) Consecutive duplicate collapse
- **Original feature:**  
  If same sender sent same consecutive content, updated existing message content with `[repeated N times]` suffix and returned deduplicated result.  
  File/function: `rogers-langgraph/flows/message_pipeline.py::dedup_check`, `services/database.py::update_message_content`
- **New implementation:**  
  `app/flows/message_pipeline.py::store_message`
- **Severity:** major
- **Notes:**  
  Behavior changed materially. New system collapses duplicates by incrementing `repeat_count`, **not** by mutating `content`.  
  This preserves anti-duplication semantics but **does not preserve Rogers’s visible content rewriting behavior**. Any downstream consumer expecting the textual suffix in stored content will behave differently.

---

## 5) Message fields: sender/recipient/tool metadata
- **Original feature:**  
  Stored `role`, `sender_id`, `content`, `token_count`, `model_name`, `external_session_id`, `content_type`, `priority`.  
  File/function: `services/database.py::insert_message`
- **New implementation:**  
  `app/flows/message_pipeline.py::store_message`, schema in `postgres/init.sql`
- **Severity:** major
- **Notes:**  
  New code preserves `role`, sender-ish identity, content, token count, model name, priority-like behavior.  
  But Rogers fields **not preserved**:
  - `external_session_id` — intentional, do not flag
  - `content_type` — **missing**
  - numeric `sender_id` semantics replaced with string `sender`
  - recipient/tool-call fields added in new code

  `content_type` was used by Rogers extraction/embed queue gating, so its loss is behaviorally meaningful.

---

## 6) Queue embedding after message store
- **Original feature:**  
  Every non-deduped message enqueued `embedding_jobs`.  
  File/function: `rogers-langgraph/flows/message_pipeline.py::queue_embed`
- **New implementation:**  
  `app/flows/message_pipeline.py::enqueue_background_jobs`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 7) Queue memory extraction after embedding
- **Original feature:**  
  After embedding, queued memory extraction only if `content_type='conversation'` and `memory_extracted` false.  
  File/function: `rogers-langgraph/flows/embed_pipeline.py::queue_memory_extraction`
- **New implementation:**  
  `app/flows/message_pipeline.py::enqueue_background_jobs` and `app/flows/memory_extraction.py`
- **Severity:** minor
- **Notes:**  
  Automatic extraction is preserved, but gating changed. New system queues extraction directly at message-ingest time and dedups per conversation.  
  Since “Extraction queued parallel with embedding” is intentional, not flagged as missing.  
  However, Rogers’s `content_type` gate is absent; extraction now filters by `role IN ('user','assistant')`.

---

## 8) Context assembly queue trigger threshold
- **Original feature:**  
  Embedding pipeline checked all windows, compared conversation `estimated_token_count` to `window.max_token_limit * trigger_threshold_percent`, and queued assembly if crossed and not already covered.  
  File/function: `rogers-langgraph/flows/embed_pipeline.py::check_context_assembly`
- **New implementation:**  
  `app/flows/embed_pipeline.py::enqueue_context_assembly`
- **Severity:** minor
- **Notes:**  
  Preserved and improved. New code uses `tokens_since_last_assembly` threshold rather than only total conversation tokens. This is behaviorally better but not identical.

---

## 9) Context assembly in-progress lock
- **Original feature:**  
  Used Redis `assembly_in_progress:<window_id>` with TTL to prevent concurrent assembly.  
  File/function: `rogers-langgraph/flows/context_assembly.py::set_assembly_flag`, `::clear_assembly_flag`
- **New implementation:**  
  `app/flows/build_types/standard_tiered.py::acquire_assembly_lock`, `::release_assembly_lock`;  
  `app/flows/build_types/passthrough.py::pt_acquire_lock`, `::pt_release_lock`
- **Severity:** minor
- **Notes:**  
  Preserved, improved with token ownership and atomic Lua release.

---

## 10) Retrieval waits for assembly in progress
- **Original feature:**  
  Retrieval blocked up to 50s while assembly flag existed.  
  File/function: `rogers-langgraph/flows/retrieval.py::check_assembly`
- **New implementation:**  
  `app/flows/build_types/standard_tiered.py::ret_wait_for_assembly`;  
  `app/flows/build_types/knowledge_enriched.py::ke_wait_for_assembly`
- **Severity:** minor
- **Notes:**  
  Preserved. New implementation degrades gracefully if Redis unavailable.

---

## 11) Three-tier assembly: tier 1, tier 2, tier 3
- **Original feature:**  
  Built archival summary, chunk summaries, and recent verbatim messages inside token budget.  
  File/function: `rogers-langgraph/flows/context_assembly.py::{calculate_tiers,summarize_chunks,consolidate_tier1}` and retrieval in `flows/retrieval.py`
- **New implementation:**  
  Assembly: `app/flows/build_types/standard_tiered.py::{calculate_tier_boundaries,summarize_message_chunks,consolidate_archival_summary}`  
  Retrieval: `::ret_load_summaries`, `::ret_load_recent_messages`, `::ret_assemble_context`
- **Severity:** minor
- **Notes:**  
  Preserved. New code is richer and more configurable.

---

## 12) Incremental assembly based on already summarized ranges
- **Original feature:**  
  Did not re-summarize messages already covered by existing tier-2 summaries.  
  File/function: `rogers-langgraph/flows/context_assembly.py::calculate_tiers`
- **New implementation:**  
  `app/flows/build_types/standard_tiered.py::calculate_tier_boundaries`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 13) Tier consolidation threshold and keep-recent behavior
- **Original feature:**  
  If >3 active tier-2 summaries, consolidated oldest ones into tier-1 while keeping 2 recent tier-2 summaries.  
  File/function: `rogers-langgraph/flows/context_assembly.py::consolidate_tier1`
- **New implementation:**  
  `app/flows/build_types/standard_tiered.py::consolidate_archival_summary`
- **Severity:** minor
- **Notes:**  
  Preserved, now config-driven.

---

## 14) Build-type-specific LLM choice for summarization
- **Original feature:**  
  `small-basic` used local Sutherland; `standard-tiered` used Gemini Flash-Lite.  
  File/function: `rogers-langgraph/flows/context_assembly.py::_build_summarization_llm`, `::select_llm`
- **New implementation:**  
  `app/flows/build_types/standard_tiered.py::_resolve_llm_config`, `summarize_message_chunks`, `consolidate_archival_summary`
- **Severity:** minor
- **Notes:**  
  Preserved conceptually via per-build-type LLM overrides, though routing strategy differs.  
  “Single LLM per build type instead of dual small/large routing” is intentional.

---

## 15) Passthrough / `small-basic`-like low-cost build
- **Original feature:**  
  Rogers had `small-basic` build type in DB seed data.  
  File/function: `rogers-postgres/init.sql` build type seed; assembly logic in `context_assembly.py`
- **New implementation:**  
  `app/flows/build_types/passthrough.py`
- **Severity:** minor
- **Notes:**  
  Preserved under renamed build type `passthrough` — intentional rename, not a gap.

---

## 16) Retrieval assembled context content
- **Original feature:**  
  Returned XML-tagged text blob with `<archival_summary>`, `<chunk_summaries>`, `<recent_messages>`, plus parallel structured `tiers`.  
  File/function: `rogers-langgraph/flows/retrieval.py::assemble`
- **New implementation:**  
  `app/flows/build_types/standard_tiered.py::ret_assemble_context`;  
  `app/flows/build_types/knowledge_enriched.py::ke_assemble_context`
- **Severity:** minor
- **Notes:**  
  Preserved in substance but returned as structured OpenAI-style `messages` array rather than XML text.  
  This is intentional per instructions; do not flag as missing.

---

## 17) Retrieval returns structured tier breakdown
- **Original feature:**  
  Returned `tiers` object with summary and recent message decomposition.  
  File/function: `rogers-langgraph/flows/retrieval.py::assemble`
- **New implementation:**  
  `app/flows/build_types/standard_tiered.py::ret_assemble_context`;  
  `app/flows/build_types/knowledge_enriched.py::ke_assemble_context`
- **Severity:** minor
- **Notes:**  
  Preserved and expanded to include semantic messages and KG facts in enriched mode.

---

## 18) Hybrid message search: vector + BM25 + RRF + reranking
- **Original feature:**  
  Performed hybrid retrieval over messages using vector search + BM25, fused via RRF, then reranked via cross-encoder.  
  File/function: `rogers-langgraph/services/database.py::search_messages`, `flows/conversation_ops.py::search_messages`
- **New implementation:**  
  `app/flows/search_flow.py::{hybrid_search_messages,rerank_results}`
- **Severity:** minor
- **Notes:**  
  Preserved and more explicit/configurable.

---

## 19) Message search recency bias
- **Original feature:**  
  Hybrid search applied recency penalty up to 20% at 90 days.  
  File/function: `services/database.py::search_messages`
- **New implementation:**  
  `app/flows/search_flow.py::hybrid_search_messages`
- **Severity:** minor
- **Notes:**  
  Preserved with config-driven tuning.

---

## 20) Conversation search semantic grouping by conversation
- **Original feature:**  
  Embedded query, searched message embeddings, grouped by conversation, ranked by best match score.  
  File/function: `services/database.py::search_conversations`, `flows/conversation_ops.py::search_conversations`
- **New implementation:**  
  `app/flows/search_flow.py::{embed_conversation_query,search_conversations_db}`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 21) Conversation search structured filters: `flow_id`, `user_id`, `sender_id`, date range
- **Original feature:**  
  Supported `flow_id`, `user_id`, `sender_id`, `external_session_id`, `date_from`, `date_to`.  
  File/function: `services/database.py::search_conversations`
- **New implementation:**  
  `app/flows/search_flow.py::search_conversations_db`
- **Severity:** major
- **Notes:**  
  Preserved:
  - `flow_id`
  - `user_id`
  - sender filter (now `sender`, string)
  - date range

  **Missing:** `external_session_id` filtering.  
  This was part of Rogers behavior and is not listed as intentionally removed.

---

## 22) Message search structured filters: `conversation_id`, `sender_id`, `role`, date range, `external_session_id`
- **Original feature:**  
  Supported `conversation_id`, `sender_id`, `role`, `external_session_id`, `date_from`, `date_to`.  
  File/function: `services/database.py::search_messages`
- **New implementation:**  
  `app/flows/search_flow.py::hybrid_search_messages`
- **Severity:** major
- **Notes:**  
  Preserved except **`external_session_id` filter is missing**.  
  This is a behavior regression relative to Rogers.

---

## 23) `conv_get_history`
- **Original feature:**  
  Returned conversation metadata and all messages in chronological order.  
  File/function: `rogers-langgraph/flows/conversation_ops.py::get_history`, `services/database.py::get_conversation_messages`
- **New implementation:**  
  `app/flows/conversation_ops_flow.py::load_conversation_and_messages`
- **Severity:** minor
- **Notes:**  
  Preserved.  
  `flow_name` from Rogers conversation metadata is absent.

---

## 24) `conv_search_context_windows`
- **Original feature:**  
  Search by `context_window_id`, `conversation_id`, `build_type_id`.  
  File/function: `rogers-langgraph/flows/conversation_ops.py::search_context_windows_handler`, `services/database.py::search_context_windows`
- **New implementation:**  
  `app/flows/conversation_ops_flow.py::search_context_windows_node`
- **Severity:** minor
- **Notes:**  
  Preserved and extended with `participant_id`.

---

## 25) Embedding pipeline fetch → embed → store
- **Original feature:**  
  Loaded message, built contextual embedding with prior messages, called embeddings tool, stored vector.  
  File/function: `rogers-langgraph/flows/embed_pipeline.py::{fetch_message,embed_message,store_embedding}`
- **New implementation:**  
  `app/flows/embed_pipeline.py::{fetch_message,generate_embedding,store_embedding}`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 26) Contextual embeddings using prior N messages
- **Original feature:**  
  Used prior messages as prefix text for embedding; config `embedding.context_window_size`, default 3.  
  File/function: `rogers-langgraph/flows/embed_pipeline.py::embed_message`
- **New implementation:**  
  `app/flows/embed_pipeline.py::generate_embedding`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 27) Skip embedding if content absent / unsuitable
- **Original feature:**  
  Rogers required non-null content, since schema had `content NOT NULL`.  
  Effective behavior: all stored messages had text.  
- **New implementation:**  
  `app/flows/embed_pipeline.py::generate_embedding`
- **Severity:** minor
- **Notes:**  
  New code explicitly skips NULL-content tool-call messages. This is an extension, not a loss.

---

## 28) Memory extraction lock per conversation
- **Original feature:**  
  Used `extraction_in_progress:<conversation_id>` lock to avoid concurrent extraction.  
  File/function: `rogers-langgraph/flows/memory_extraction.py::acquire_extraction_lock`, `::release_extraction_lock`
- **New implementation:**  
  `app/flows/memory_extraction.py::{acquire_extraction_lock,release_extraction_lock}`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 29) Memory extraction from unextracted conversational messages
- **Original feature:**  
  Fetched `content_type='conversation' AND memory_extracted IS NOT TRUE`.  
  File/function: `services/database.py::get_unextracted_messages`, `flows/memory_extraction.py::fetch_unextracted`
- **New implementation:**  
  `app/flows/memory_extraction.py::fetch_unextracted_messages`
- **Severity:** major
- **Notes:**  
  New implementation fetches `role IN ('user','assistant')` and `memory_extracted IS NOT TRUE`; Rogers used `content_type='conversation'`.  
  This may include/exclude a different set of messages. Since `content_type` was removed, this exact behavior is not preserved.

---

## 30) Memory extraction text budgeting newest-first
- **Original feature:**  
  Built extraction text newest-first within char budget, then restored chronological order.  
  File/function: `rogers-langgraph/flows/memory_extraction.py::build_extraction_text`
- **New implementation:**  
  `app/flows/memory_extraction.py::build_extraction_text`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 31) Small/large extraction model routing by text size
- **Original feature:**  
  Chose small vs large Mem0/LLM instance based on text length thresholds.  
  File/function: `rogers-langgraph/flows/memory_extraction.py::build_extraction_text`, `::run_mem0_extraction`, `services/mem0_setup.py::{get_mem0_small,get_mem0_large}`
- **New implementation:**  
  MISSING
- **Severity:** major
- **Notes:**  
  New implementation uses a single Mem0 client path and no small/large extraction routing.  
  This is related to the intentional “single LLM per build type” change, but that note applies to build types, not explicitly to memory extraction model routing.  
  Behaviorally, Rogers could route large extraction loads to a different LLM; new code cannot.

---

## 32) Secret redaction before Mem0 ingestion
- **Original feature:**  
  Used `detect-secrets` plus custom detectors to redact secrets before Mem0 ingestion.  
  File/function: `rogers-langgraph/services/secret_filter.py::redact_secrets`; used in `flows/memory_extraction.py` and `flows/memory_ops.py`
- **New implementation:**  
  `app/flows/memory_extraction.py::_redact_secrets`
- **Severity:** minor
- **Notes:**  
  Preserved in behavior, implementation intentionally changed to regex-based redaction. Do not flag.

---

## 33) `mem_search`
- **Original feature:**  
  Queried Mem0, returned memories and relations, degraded gracefully on failure.  
  File/function: `rogers-langgraph/flows/memory_ops.py::mem_search`
- **New implementation:**  
  `app/flows/memory_search_flow.py::search_memory_graph`
- **Severity:** minor
- **Notes:**  
  Preserved and improved with confidence decay ranking.

---

## 34) `mem_get_context`
- **Original feature:**  
  Queried Mem0 and formatted context lines for prompt injection.  
  File/function: `rogers-langgraph/flows/memory_ops.py::mem_get_context`
- **New implementation:**  
  `app/flows/memory_search_flow.py::retrieve_memory_context`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 35) Internal/admin `mem_add`
- **Original feature:**  
  Added a memory directly to Mem0. Internal HTTP endpoint, not MCP-exposed.  
  File/function: `rogers-langgraph/flows/memory_ops.py::mem_add`, route `server.py:/mem_add`
- **New implementation:**  
  `app/flows/memory_admin_flow.py::add_memory`; exposed in MCP dispatch as `mem_add`
- **Severity:** minor
- **Notes:**  
  Preserved, now externally tool-dispatchable.

---

## 36) Internal/admin `mem_list`
- **Original feature:**  
  Listed user memories from Mem0. Internal HTTP endpoint.  
  File/function: `rogers-langgraph/flows/memory_ops.py::mem_list`, route `server.py:/mem_list`
- **New implementation:**  
  `app/flows/memory_admin_flow.py::list_memories`; exposed in MCP dispatch as `mem_list`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 37) Internal/admin `mem_delete`
- **Original feature:**  
  Deleted a memory by ID. Internal HTTP endpoint.  
  File/function: `rogers-langgraph/flows/memory_ops.py::mem_delete`, route `server.py:/mem_delete`
- **New implementation:**  
  `app/flows/memory_admin_flow.py::delete_memory`; exposed in MCP dispatch as `mem_delete`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 38) Internal/manual `mem_extract`
- **Original feature:**  
  Manual extraction trigger endpoint over recent messages.  
  File/function: `rogers-langgraph/server.py::mem_extract_route`
- **New implementation:**  
  MISSING
- **Severity:** minor
- **Notes:**  
  Intentional removal per instructions; do not flag as missing.

---

## 39) Rogers stats / health-style internal metrics endpoint
- **Original feature:**  
  Exposed `rogers_stats` MCP tool / `/stats` style metrics for health monitoring.  
  File/function: `rogers/config.json::rogers_stats`, `server.py::metrics_get`/metrics graph
- **New implementation:**  
  `app/routes/metrics.py`, `app/flows/metrics_flow.py`, MCP `metrics_get`
- **Severity:** minor
- **Notes:**  
  Preserved via Prometheus and `metrics_get`. Intentional replacement; do not flag.

---

## 40) Imperator / `rogers_chat`
- **Original feature:**  
  Tool available as `rogers_chat`; graph took `user_id` and messages, chose tools `conv_search` or `mem_search`, then finalized.  
  File/function: `rogers-langgraph/flows/imperator.py`, route wiring in `server.py::_invoke_imperator`
- **New implementation:**  
  `app/flows/imperator_flow.py`, dispatched as `imperator_chat`
- **Severity:** major
- **Notes:**  
  Exists, but behavior differs substantially:
  - interface changed from `user_id + messages` to `message + optional context_window_id`
  - Rogers Imperator used explicit planning over `conv_search`/`mem_search`; new one uses LangGraph ToolNode and its own internal tools
  - persistence semantics are different; new one writes through message pipeline into context window history

  Rename is intentional, but the actual agent behavior and API contract are not equivalent.

---

## 41) Imperator access to conversation search and memory search
- **Original feature:**  
  Could call `conv_search` and `mem_search`.  
  File/function: `rogers-langgraph/flows/imperator.py::{decide_next_action,execute_selected_tool}`
- **New implementation:**  
  `app/flows/imperator_flow.py::{_conv_search_tool,_mem_search_tool}`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 42) Imperator persistent ongoing conversation across restarts
- **Original feature:**  
  Rogers Imperator graph itself did not clearly implement durable persistent system conversation management in the provided code.
- **New implementation:**  
  `app/imperator/state_manager.py::ImperatorStateManager`
- **Severity:** minor
- **Notes:**  
  New system appears stronger than Rogers here, not weaker.

---

## 43) Health endpoint checking Postgres and Redis
- **Original feature:**  
  `/health` checked Postgres and Redis and returned healthy/unhealthy.  
  File/function: `rogers-langgraph/server.py::health`
- **New implementation:**  
  `app/flows/health_flow.py::check_dependencies`, route `app/routes/health.py`
- **Severity:** minor
- **Notes:**  
  Preserved and extended to include Neo4j degraded state.

---

## 44) Metrics endpoint
- **Original feature:**  
  `/metrics` and `metrics_get` existed.  
  File/function: `rogers-langgraph/server.py::{metrics,metrics_get}`
- **New implementation:**  
  `app/routes/metrics.py`, `app/flows/metrics_flow.py`, `tool_dispatch.py`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 45) Queue worker with 3 independent consumers
- **Original feature:**  
  Independent consumers for embedding, assembly, extraction; dead-letter sweep.  
  File/function: `rogers-langgraph/services/queue_worker.py`
- **New implementation:**  
  `app/workers/arq_worker.py`
- **Severity:** minor
- **Notes:**  
  Preserved and improved with stranded-job recovery, delayed queues, metrics.

---

## 46) Retry/backoff and dead-letter handling
- **Original feature:**  
  Retries with backoff, dead-letters after max attempts, periodic sweep.  
  File/function: `rogers-langgraph/services/queue_worker.py::{_handle_failure,_sweep_dead_letters,_sweep_dead_letters_loop}`
- **New implementation:**  
  `app/workers/arq_worker.py::{_handle_job_failure,_sweep_dead_letters,_dead_letter_sweep_loop,_sweep_delayed_queues}`
- **Severity:** minor
- **Notes:**  
  Preserved.

---

## 47) Memory extraction priority queue
- **Original feature:**  
  Used Redis sorted set `memory_extraction_jobs` with priority score ordering.  
  File/function: `rogers-langgraph/flows/embed_pipeline.py::queue_memory_extraction`, `services/queue_worker.py::_consume_memory_extraction_queue`
- **New implementation:**  
  `app/flows/message_pipeline.py::enqueue_background_jobs`, `app/workers/arq_worker.py`
- **Severity:** minor
- **Notes:**  
  Preserved, though scoring scheme changed. Intentional architecture note covers queue implementation details.

---

## 48) Search reranker provider
- **Original feature:**  
  Used peer `llm_rerank` via Sutherland in conversation ops.  
  File/function: `rogers-langgraph/flows/conversation_ops.py::search_messages`
- **New implementation:**  
  `app/flows/search_flow.py::rerank_results`
- **Severity:** minor
- **Notes:**  
  Preserved conceptually but now local `sentence-transformers` cross-encoder only, with config also mentioning `cohere` but no implementation for it.  
  Rogers depended on external rerank tool; new code uses local model.

---

## 49) Build type registry / configurable build types
- **Original feature:**  
  Build types were DB rows in `context_window_build_types`; behavior keyed off build_type IDs in code.  
  File/function: `rogers-postgres/init.sql`, `flows/context_assembly.py`
- **New implementation:**  
  `app/flows/build_type_registry.py`, `app/flows/build_types/*`
- **Severity:** minor
- **Notes:**  
  Functionality preserved, architecture intentionally changed.

---

## 50) Knowledge-enriched retrieval beyond Rogers
- **Original feature:**  
  Rogers had only three-tier episodic retrieval in provided code. No semantic injection or KG facts in retrieval output.  
- **New implementation:**  
  `app/flows/build_types/knowledge_enriched.py`
- **Severity:** minor
- **Notes:**  
  New functionality exceeds Rogers; no issue.

---

# Missing / Gap List Only

These are the substantive non-intentional behavior gaps I found:

1. **`flow_name` on conversations not preserved**  
   - Original: `flows/conversation_ops.py::create_conversation`, DB schema  
   - New: MISSING  
   - Severity: minor

2. **`content_type` message field not preserved**  
   - Original: `services/database.py::insert_message`, schema, extraction/embed gating  
   - New: MISSING  
   - Severity: major

3. **Visible duplicate-message content suffix behavior not preserved**  
   - Original: `flows/message_pipeline.py::dedup_check` mutated content with `[repeated N times]`  
   - New: `repeat_count` only in `app/flows/message_pipeline.py::store_message`  
   - Severity: major

4. **`external_session_id` filter in conversation search missing**  
   - Original: `services/database.py::search_conversations`  
   - New: MISSING  
   - Severity: major

5. **`external_session_id` filter in message search missing**  
   - Original: `services/database.py::search_messages`  
   - New: MISSING  
   - Severity: major

6. **Memory extraction small/large model routing missing**  
   - Original: `flows/memory_extraction.py`, `services/mem0_setup.py`  
   - New: MISSING  
   - Severity: major

7. **Rogers context window creation semantics changed from conversation/build-type scoped to participant-scoped**  
   - Original: `insert_context_window(conversation_id, build_type_id, max_token_limit)`  
   - New: `participant_id` required in `app/flows/conversation_ops_flow.py`  
   - Severity: major

8. **Imperator contract and behavior not equivalent to Rogers’s `rogers_chat`**  
   - Original: `flows/imperator.py`, `server.py::_invoke_imperator`  
   - New: `app/flows/imperator_flow.py`  
   - Severity: major

---

# Bottom Line

If the goal is **“preserve Rogers’s practical core behavior for conversation storage, retrieval, search, and background processing”**, the rewrite is mostly successful.

If the goal is **strict behavioral parity**, the main regressions are:

- loss of `content_type` semantics
- loss of `external_session_id` search filters
- changed duplicate-collapse representation
- changed context-window creation model
- changed Imperator interface/behavior
- missing extraction small/large model routing

If you want, I can turn this into a **CSV-style audit table** or a **blocker/major/minor punch list sorted by subsystem**.