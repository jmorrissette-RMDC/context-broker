Below is a functionality-preservation review comparing the new Context Broker rewrite against the original Rogers implementation.

I focused on preserved behavior, not architectural rewrites.

---

# Summary

The rewrite preserves most of Rogers’ core behavior and substantially expands it. Core MCP operations, message storage, async embedding, context assembly, memory extraction, hybrid message search, and memory search are all present.

However, there are several notable behavior gaps/regressions versus Rogers:

1. **Conversation/message search lost some original filters** — especially `external_session_id`.
2. **Internal/admin memory extraction endpoint/tool behavior is not exposed equivalently.**
3. **Imperator behavior changed materially** — persistent built-in agent exists, but the original `rogers_chat` tool contract is not preserved 1:1.
4. **Context retrieval output format changed** — OpenAI-style messages instead of Rogers’ XML-like assembled text.
5. **Conversation search reranking behavior differs** — Rogers only reranked message search, which is preserved; conversation search remains vector/text only as before.
6. **A few minor schema/field-level behaviors changed** — `sender_id`→`sender`, `participant-scoped windows` instead of Rogers’ conversation/build-type windows, etc. These are mostly intentional unless they break client-visible semantics.

---

# Findings

## 1) MCP tool: `conv_create_conversation`
- **Original feature:** Rogers created or returned an existing conversation by ID with `flow_id`, `flow_name`, `user_id`, `title`.  
  - File/function: `rogers-langgraph/flows/conversation_ops.py::create_conversation`, `services/database.py::insert_conversation`
- **New implementation:**  
  - `app/flows/conversation_ops_flow.py::create_conversation_node`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool`
- **Severity:** minor
- **Notes:** Preserved idempotent create-or-return behavior via caller-supplied ID. `flow_name` support from Rogers is not present in new schema/tool. If clients depended on `flow_name`, that field is lost.

---

## 2) MCP tool: `conv_create_context_window`
- **Original feature:** Created a context window for a conversation + build type + token limit.  
  - File/function: `rogers-langgraph/flows/conversation_ops.py::create_context_window`, `services/database.py::insert_context_window`
- **New implementation:**  
  - `app/flows/conversation_ops_flow.py::resolve_token_budget_node`, `create_context_window_node`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool`
- **Severity:** minor
- **Notes:** Preserved and improved with token-budget resolution and idempotency.  
  Behavioral difference: Rogers windows were keyed by `(conversation_id, build_type_id)` effectively; new system keys by `(conversation_id, participant_id, build_type)`. This is intentional architecture, but clients must now supply `participant_id`. Functionality is broader, not reduced.

---

## 3) MCP tool: `conv_store_message`
- **Original feature:** Store one message, deduplicate consecutive duplicates from same sender, enqueue embedding asynchronously.  
  - File/function: `rogers-langgraph/flows/message_pipeline.py::{resolve_conversation,dedup_check,store_message,queue_embed}`
- **New implementation:**  
  - `app/flows/message_pipeline.py::{store_message, enqueue_background_jobs}`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool`
- **Severity:** minor
- **Notes:** Core behavior preserved. New implementation adds:
  - role-derived priority
  - nullable content/tool-call support
  - background enqueue for both embedding and memory extraction
  - duplicate collapse via `repeat_count`
  
  Behavioral differences:
  - Rogers accepted `sender_id`, `external_session_id`, `content_type`, `priority`; new tool uses `sender` string, optional `recipient`, tool call fields, and ignores caller priority at storage time (despite validation model containing it).
  - `external_session_id` is no longer stored/searchable. That is a real lost behavior for provenance tracking.

---

## 4) Deduplication/collapse of consecutive duplicate messages
- **Original feature:** Consecutive duplicate from same sender updated existing message content with a repeat-count suffix instead of inserting a new row.  
  - File/function: `rogers-langgraph/flows/message_pipeline.py::dedup_check`, `services/database.py::update_message_content`
- **New implementation:**  
  - `app/flows/message_pipeline.py::store_message`
- **Severity:** minor
- **Notes:** Preserved in spirit but behavior differs.  
  Rogers mutated message content to append `[repeated N times]`.  
  New implementation increments `repeat_count` on the existing row instead. Better schema design, but clients reading raw message content will no longer see the suffix.

---

## 5) Resolve `conversation_id` from `context_window_id` when storing messages
- **Original feature:** `conv_store_message` accepted either `conversation_id` or `context_window_id`; resolved conversation from window when needed.  
  - File/function: `rogers-langgraph/flows/message_pipeline.py::resolve_conversation`
- **New implementation:**  
  - `app/flows/message_pipeline.py::store_message`
- **Severity:** minor
- **Notes:** Preserved, but stricter: new pipeline expects `context_window_id` as the primary input and looks up `conversation_id` from it. This is consistent with new architecture.

---

## 6) Background embedding pipeline
- **Original feature:** For each stored message, fetch message, compute contextual embedding using prior messages as prefix, store embedding, then queue context assembly and memory extraction.  
  - File/function: `rogers-langgraph/flows/embed_pipeline.py::{fetch_message, embed_message, store_embedding, check_context_assembly, queue_memory_extraction}`
- **New implementation:**  
  - `app/flows/embed_pipeline.py::{fetch_message, generate_embedding, store_embedding, enqueue_context_assembly}`
  - worker: `app/workers/arq_worker.py::process_embedding_job`
- **Severity:** minor
- **Notes:** Preserved. Contextual embedding prefix behavior is preserved via `context_window_size`. New system skips embeddings for null-content tool-call messages, which Rogers did not support because content was required. That is an intentional extension.

---

## 7) Queue assembly only when thresholds crossed
- **Original feature:** After embedding, queue assembly only when token threshold crossed and not already covered by last assembly/in-progress lock.  
  - File/function: `rogers-langgraph/flows/embed_pipeline.py::check_context_assembly`
- **New implementation:**  
  - `app/flows/embed_pipeline.py::enqueue_context_assembly`
- **Severity:** minor
- **Notes:** Preserved and improved. New code uses:
  - `trigger_threshold_percent`
  - in-progress lock checks
  - dedup key to avoid duplicate queue spam
  - batched Redis/Postgres checks

  Rogers used `estimated_token_count >= threshold`; new code uses “tokens since last assembly >= threshold” when possible, which is more precise.

---

## 8) Queue memory extraction after embedding
- **Original feature:** Embedding pipeline queued memory extraction jobs for eligible messages/conversations.  
  - File/function: `rogers-langgraph/flows/embed_pipeline.py::queue_memory_extraction`
- **New implementation:**  
  - `app/flows/message_pipeline.py::enqueue_background_jobs`
  - extraction worker: `app/workers/arq_worker.py::process_extraction_job`
- **Severity:** minor
- **Notes:** Preserved, though moved earlier: new code queues memory extraction at message-ingest time rather than from embed pipeline. Since extraction uses stored messages, behavior remains effectively preserved.

---

## 9) Background context assembly pipeline
- **Original feature:** Per-window assembly pipeline with Redis lock, message loading, tier calculation, chunk summarization, tier-1 consolidation, finalize, cleanup.  
  - File/function: `rogers-langgraph/flows/context_assembly.py`
- **New implementation:**  
  - `app/flows/build_types/standard_tiered.py::{acquire_assembly_lock, load_window_config, load_messages, calculate_tier_boundaries, summarize_message_chunks, consolidate_archival_summary, finalize_assembly, release_assembly_lock}`
  - worker: `app/workers/arq_worker.py::process_assembly_job`
- **Severity:** minor
- **Notes:** Preserved and expanded. New implementation adds:
  - idempotent summary inserts
  - lock TTL renewal during chunk summarization
  - dynamic tier scaling
  - build-type registry
  - partial-failure handling

---

## 10) Incremental assembly (only summarize unsummarized older messages)
- **Original feature:** Existing tier-2 summaries were detected and only new message ranges were summarized.  
  - File/function: `rogers-langgraph/flows/context_assembly.py::calculate_tiers`
- **New implementation:**  
  - `app/flows/build_types/standard_tiered.py::calculate_tier_boundaries`
- **Severity:** minor
- **Notes:** Preserved.

---

## 11) Tier-1 archival consolidation from older tier-2 summaries
- **Original feature:** If enough active tier-2 summaries existed, consolidate oldest into a tier-1 summary and deactivate old summaries.  
  - File/function: `rogers-langgraph/flows/context_assembly.py::consolidate_tier1`
- **New implementation:**  
  - `app/flows/build_types/standard_tiered.py::consolidate_archival_summary`
- **Severity:** minor
- **Notes:** Preserved and improved by including prior tier-1 summary in consolidation.

---

## 12) Retrieval waits for assembly in progress
- **Original feature:** `conv_retrieve_context` blocked up to 50s if assembly was running.  
  - File/function: `rogers-langgraph/flows/retrieval.py::check_assembly`
- **New implementation:**  
  - standard: `app/flows/build_types/standard_tiered.py::ret_wait_for_assembly`
  - knowledge-enriched: `app/flows/build_types/knowledge_enriched.py::ke_wait_for_assembly`
- **Severity:** minor
- **Notes:** Preserved. New implementation surfaces timeout as warning rather than just blocked status.

---

## 13) Retrieval of tier 1, tier 2, tier 3 context
- **Original feature:** Load active summaries plus recent verbatim messages under remaining budget.  
  - File/function: `rogers-langgraph/flows/retrieval.py::{get_summaries, get_recent}`
- **New implementation:**  
  - standard: `ret_load_summaries`, `ret_load_recent_messages`
  - knowledge-enriched: `ke_load_summaries`, `ke_load_recent_messages`
- **Severity:** minor
- **Notes:** Preserved. New implementation avoids loading messages already covered by summaries more explicitly.

---

## 14) Retrieval output format
- **Original feature:** Returned one assembled XML-ish text blob with `<archival_summary>`, `<chunk_summaries>`, `<recent_messages>` plus structured `tiers`.  
  - File/function: `rogers-langgraph/flows/retrieval.py::assemble`
- **New implementation:**  
  - standard: `app/flows/build_types/standard_tiered.py::ret_assemble_context`
  - knowledge-enriched: `app/flows/build_types/knowledge_enriched.py::ke_assemble_context`
- **Severity:** major
- **Notes:** The structured tier information is preserved, but the primary `context` output is now a **list of OpenAI-format messages** instead of Rogers’ single tagged text block.  
  This is a real client-visible contract change. If downstream consumers depended on Rogers’ XML-like assembled string, that behavior is not preserved.

---

## 15) MCP tool: `conv_retrieve_context`
- **Original feature:** Returned assembled context, tiers, total tokens, assembly status.  
  - File/function: `server.py::_invoke_retrieval_flow`
- **New implementation:**  
  - `app/flows/tool_dispatch.py::dispatch_tool` (`conv_retrieve_context`)
- **Severity:** major
- **Notes:** Same conceptual fields are returned, but `context` is now `context_messages` (message array) rather than Rogers text blob. See finding above.

---

## 16) Build types: `small-basic` / `standard-tiered`
- **Original feature:** Rogers had at least `small-basic` and `standard-tiered`, with LLM selection depending on build type.  
  - File/function: `rogers-postgres/init.sql` seed data, `flows/context_assembly.py::_build_summarization_llm`
- **New implementation:**  
  - `app/flows/build_types/passthrough.py`
  - `app/flows/build_types/standard_tiered.py`
  - `app/flows/build_types/knowledge_enriched.py`
- **Severity:** minor
- **Notes:** `standard-tiered` preserved. `small-basic` is not present by name.  
  Functionally, `passthrough` is not equivalent to `small-basic`, because Rogers small-basic still did tiered summarization using a small local LLM. If any callers referenced build type name `small-basic`, that specific behavior/name is missing.

---

## 17) Build-type-specific LLM selection for summarization
- **Original feature:** Assembly chose local small LLM for `small-basic`, Gemini for `standard-tiered`.  
  - File/function: `rogers-langgraph/flows/context_assembly.py::_build_summarization_llm`, `select_llm`
- **New implementation:**  
  - `app/flows/build_types/standard_tiered.py::_resolve_llm_config`
- **Severity:** minor
- **Notes:** Preserved in a more general form: per-build-type LLM override. Specific Rogers routing by build-type name is not preserved exactly, but capability is preserved.

---

## 18) MCP tool: `conv_search`
- **Original feature:** Search conversations semantically via message embeddings grouped by conversation, plus structured filters: `flow_id`, `user_id`, `sender_id`, `external_session_id`, date range.  
  - File/function: `rogers-langgraph/flows/conversation_ops.py::search_conversations`, `services/database.py::search_conversations`
- **New implementation:**  
  - `app/flows/search_flow.py::{embed_conversation_query, search_conversations_db}`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool`
- **Severity:** major
- **Notes:** Partially preserved. New implementation supports:
  - semantic embedding search
  - `flow_id`
  - `user_id`
  - `sender`
  - date range
  
  Missing versus Rogers:
  - **`external_session_id` filter** is gone.
  - filter field changed from `sender_id` to `sender` string, which may break clients/data parity.

---

## 19) MCP tool: `conv_search_messages`
- **Original feature:** Hybrid search over messages: vector ANN + BM25 via RRF, top candidates reranked by cross-encoder; structured filters `conversation_id`, `sender_id`, `role`, `external_session_id`, date range.  
  - File/function: `rogers-langgraph/flows/conversation_ops.py::search_messages`, `services/database.py::search_messages`
- **New implementation:**  
  - `app/flows/search_flow.py::{embed_message_query, hybrid_search_messages, rerank_results}`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool`
- **Severity:** major
- **Notes:** Core hybrid search + reranking is preserved well. Missing behavior:
  - **`external_session_id` filter** is missing.
  - `sender_id` changed to `sender`.
  
  The hybrid architecture is actually stronger in the new version, but those filter semantics are lost.

---

## 20) Hybrid search specifics: vector + BM25 + RRF + reranker
- **Original feature:** Two-stage hybrid retrieval with vector ANN + BM25 RRF and cross-encoder reranker.  
  - File/function: `services/database.py::search_messages`, `flows/conversation_ops.py::search_messages`
- **New implementation:**  
  - `app/flows/search_flow.py::{hybrid_search_messages, rerank_results}`
- **Severity:** minor
- **Notes:** Preserved. New implementation adds recency bias and configurable candidate limits. No loss here.

---

## 21) MCP tool: `conv_get_history`
- **Original feature:** Return conversation plus chronological message sequence.  
  - File/function: `rogers-langgraph/flows/conversation_ops.py::get_history`
- **New implementation:**  
  - `app/flows/conversation_ops_flow.py::load_conversation_and_messages`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool`
- **Severity:** minor
- **Notes:** Preserved. New implementation also supports `limit`.

---

## 22) MCP tool: `conv_search_context_windows`
- **Original feature:** Search/list context windows by ID, conversation, build type.  
  - File/function: `rogers-langgraph/flows/conversation_ops.py::search_context_windows_handler`, `services/database.py::search_context_windows`
- **New implementation:**  
  - `app/flows/conversation_ops_flow.py::search_context_windows_node`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool`
- **Severity:** minor
- **Notes:** Preserved and expanded with `participant_id`.

---

## 23) MCP tool: `mem_search`
- **Original feature:** Query Mem0 knowledge graph/search and return memories + relations, degrading gracefully on errors.  
  - File/function: `rogers-langgraph/flows/memory_ops.py::mem_search`
- **New implementation:**  
  - `app/flows/memory_search_flow.py::search_memory_graph`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool`
- **Severity:** minor
- **Notes:** Preserved. New implementation adds half-life decay ranking.

---

## 24) MCP tool: `mem_get_context`
- **Original feature:** Query memories and return prompt-formatted text plus raw memories.  
  - File/function: `rogers-langgraph/flows/memory_ops.py::mem_get_context`
- **New implementation:**  
  - `app/flows/memory_search_flow.py::retrieve_memory_context`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool`
- **Severity:** minor
- **Notes:** Preserved.

---

## 25) Internal tool/endpoint: `mem_add`
- **Original feature:** Internal/admin endpoint to add a memory directly.  
  - File/function: `server.py::mem_add_route`, `flows/memory_ops.py::mem_add`
- **New implementation:**  
  - `app/flows/memory_admin_flow.py::add_memory`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool` as MCP tool `mem_add`
- **Severity:** minor
- **Notes:** Preserved and now exposed through MCP.

---

## 26) Internal tool/endpoint: `mem_list`
- **Original feature:** Internal/admin endpoint to list memories.  
  - File/function: `server.py::mem_list_route`, `flows/memory_ops.py::mem_list`
- **New implementation:**  
  - `app/flows/memory_admin_flow.py::list_memories`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool` as MCP tool `mem_list`
- **Severity:** minor
- **Notes:** Preserved and now exposed through MCP.

---

## 27) Internal tool/endpoint: `mem_delete`
- **Original feature:** Internal/admin endpoint to delete memory by ID.  
  - File/function: `server.py::mem_delete_route`, `flows/memory_ops.py::mem_delete`
- **New implementation:**  
  - `app/flows/memory_admin_flow.py::delete_memory`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool` as MCP tool `mem_delete`
- **Severity:** minor
- **Notes:** Preserved and now exposed through MCP.

---

## 28) Internal tool/endpoint: `mem_extract`
- **Original feature:** Internal endpoint to fetch recent conversation messages and trigger extraction ad hoc.  
  - File/function: `server.py::mem_extract_route`
- **New implementation:** **MISSING**
- **Severity:** major
- **Notes:** There is background extraction flow, but I do not see an equivalent ad hoc `mem_extract` tool/route. If this endpoint was used operationally/admin-side, that behavior is lost.

---

## 29) Memory extraction pipeline
- **Original feature:** Fetch unextracted messages, build text, acquire lock, run Mem0 extraction, mark extracted, release lock.  
  - File/function: `rogers-langgraph/flows/memory_extraction.py`
- **New implementation:**  
  - `app/flows/memory_extraction.py`
  - worker: `app/workers/arq_worker.py::process_extraction_job`
- **Severity:** minor
- **Notes:** Preserved and improved.

---

## 30) Memory extraction locks to prevent concurrent extraction
- **Original feature:** Redis lock per conversation for extraction.  
  - File/function: `rogers-langgraph/flows/memory_extraction.py::acquire_extraction_lock`
- **New implementation:**  
  - `app/flows/memory_extraction.py::{acquire_extraction_lock, release_extraction_lock}`
- **Severity:** minor
- **Notes:** Preserved.

---

## 31) Mark extracted messages after successful extraction
- **Original feature:** Set `memory_extracted=TRUE` only after successful Mem0 add.  
  - File/function: `rogers-langgraph/flows/memory_extraction.py::mark_extracted`
- **New implementation:**  
  - `app/flows/memory_extraction.py::mark_messages_extracted`
- **Severity:** minor
- **Notes:** Preserved.

---

## 32) Secret redaction before Mem0 ingestion
- **Original feature:** Redacted secrets before text reached Mem0 using `detect-secrets` plus custom detectors.  
  - File/function: `rogers-langgraph/services/secret_filter.py`, used by `flows/memory_extraction.py` and `flows/memory_ops.py`
- **New implementation:**  
  - `app/flows/memory_extraction.py::_redact_secrets`
- **Severity:** minor
- **Notes:** Preserved partially, but weaker. Rogers used a richer secret-detection approach. New implementation uses regex heuristics only. Functionality exists, but detection coverage is reduced.

---

## 33) Memory scoring / freshness decay
- **Original feature:** Rogers config defined recency decay and confidence parameters for memory, but I do not see strong runtime use in the provided code.  
  - File/function: `rogers-langgraph/config.json::memory`
- **New implementation:**  
  - `app/flows/memory_scoring.py::{score_memory, filter_and_rank_memories}`
  - used by memory retrieval flows
- **Severity:** minor
- **Notes:** New implementation actually exceeds Rogers here.

---

## 34) Imperator / `rogers_chat`
- **Original feature:** Rogers had `rogers_chat` tool invoking a simple Imperator graph using `conv_search` and `mem_search` tools. Input contract required `user_id` and `messages`; output was `response`.  
  - File/function: `rogers-langgraph/flows/imperator.py`, `server.py::_invoke_imperator`
- **New implementation:**  
  - `app/flows/imperator_flow.py`
  - exposed as MCP tool `imperator_chat`
  - also used by `/v1/chat/completions`
- **Severity:** major
- **Notes:** Functionality is present, but **tool contract is not preserved**:
  - tool name changed from `rogers_chat` to `imperator_chat`
  - input changed from `user_id + messages` to `message + optional context_window_id`
  - behavior shifted from lightweight tool-chooser to persistent ReAct agent with DB-backed history
  
  This is more capable, but if compatibility with existing Rogers callers matters, original behavior is not preserved.

---

## 35) Imperator persistence across restarts
- **Original feature:** Rogers Imperator did not have persistent state manager in the shown code.  
- **New implementation:**  
  - `app/imperator/state_manager.py`
- **Severity:** n/a
- **Notes:** New feature, not a gap.

---

## 36) OpenAI-compatible chat endpoint
- **Original feature:** Rogers did not expose `/v1/chat/completions` in the provided code.  
- **New implementation:**  
  - `app/routes/chat.py`
- **Severity:** n/a
- **Notes:** New feature, not a gap.

---

## 37) MCP metrics tool
- **Original feature:** Rogers exposed `metrics_get` plus `/metrics`.  
  - File/function: `server.py::metrics`, `metrics_get`
- **New implementation:**  
  - `app/flows/metrics_flow.py`
  - routed via `app/flows/tool_dispatch.py::dispatch_tool`
  - HTTP route `app/routes/metrics.py`
- **Severity:** minor
- **Notes:** Preserved.

---

## 38) Internal stats tool: `rogers_stats`
- **Original feature:** Gateway exposed internal tool `rogers_stats` for health/Apgar monitoring.  
  - File/function: `rogers/config.json`
- **New implementation:** **MISSING**
- **Severity:** minor
- **Notes:** Not in the explicit requirements, but it existed in Rogers. I do not see an equivalent tool. Metrics endpoint may cover most use cases, but the specific tool is absent.

---

## 39) Dead-letter and retry handling for background jobs
- **Original feature:** Queue worker retried jobs with backoff and dead-letter sweep.  
  - File/function: `rogers-langgraph/services/queue_worker.py::{_handle_failure,_sweep_dead_letters,_sweep_dead_letters_loop}`
- **New implementation:**  
  - `app/workers/arq_worker.py::{_handle_job_failure,_sweep_dead_letters,_sweep_delayed_queues,_dead_letter_sweep_loop}`
- **Severity:** minor
- **Notes:** Preserved and improved.

---

## 40) Independent consumers for embedding / assembly / extraction
- **Original feature:** Separate async consumers for each queue.  
  - File/function: `rogers-langgraph/services/queue_worker.py::{_consume_embedding_queue,_consume_context_assembly_queue,_consume_memory_extraction_queue}`
- **New implementation:**  
  - `app/workers/arq_worker.py::{_consume_queue,start_background_worker}`
- **Severity:** minor
- **Notes:** Preserved.

---

## 41) Recovery of stranded processing jobs after worker crash
- **Original feature:** Rogers tracked dead letters but did not show explicit startup sweep for stranded processing queues.  
- **New implementation:**  
  - `app/workers/arq_worker.py::_sweep_stranded_processing_jobs`
- **Severity:** n/a
- **Notes:** Improvement, not a gap.

---

## 42) Health endpoint with dependency checks
- **Original feature:** `/health` checked Postgres and Redis only.  
  - File/function: `server.py::health`
- **New implementation:**  
  - `app/flows/health_flow.py::check_dependencies`
  - route `app/routes/health.py`
- **Severity:** minor
- **Notes:** Preserved and expanded to Neo4j/degraded mode.

---

## 43) Search filter: `external_session_id`
- **Original feature:** Both conversation search and message search supported `external_session_id` filtering.  
  - File/function: `rogers-langgraph/services/database.py::{search_conversations,search_messages}`
- **New implementation:** **MISSING**
- **Severity:** major
- **Notes:** I found no equivalent storage field or search filter in models/flows/schema. This is one of the clearest lost behaviors.

---

## 44) Search filter: numeric `sender_id`
- **Original feature:** Search and retrieval operated on integer `sender_id`.  
  - File/function: multiple Rogers DB/query functions
- **New implementation:**  
  - string `sender` field throughout new schema and flows
- **Severity:** minor
- **Notes:** Functional replacement exists, but compatibility is not 1:1. Any client assuming numeric sender IDs will need adaptation.

---

## 45) Message provenance field: `external_session_id`
- **Original feature:** Stored on messages and available for filtering.  
  - File/function: `services/database.py::insert_message`, schema in `rogers-postgres/init.sql`
- **New implementation:** **MISSING**
- **Severity:** major
- **Notes:** Not just search loss — the field itself appears removed.

---

## 46) Message content typing: `content_type`
- **Original feature:** Messages had `content_type` (`conversation|tool_output|info|error`) and extraction pipeline filtered on conversational content.  
  - File/function: schema and multiple Rogers flows
- **New implementation:** **MISSING as explicit field**
- **Severity:** minor
- **Notes:** New code infers based on role/null content/tool calls rather than explicit `content_type`. Core behavior mostly preserved, but explicit typing is gone.

---

## 47) Priority-aware extraction ordering
- **Original feature:** Extraction jobs were prioritized by message priority via ZSET scores.  
  - File/function: `rogers-langgraph/flows/embed_pipeline.py::_PRIORITY_OFFSET`, `queue_memory_extraction`
- **New implementation:**  
  - `app/flows/message_pipeline.py::enqueue_background_jobs`
  - worker uses sorted set for extraction queue
- **Severity:** minor
- **Notes:** Preserved partially. New system uses role-based priority, not caller-provided priority buckets. Similar effect, different semantics.

---

## 48) Mem0 setup with separate small/large extraction models
- **Original feature:** Rogers had `get_mem0_small` and `get_mem0_large`; extraction routed based on text size.  
  - File/function: `services/mem0_setup.py`, `flows/memory_extraction.py::build_extraction_text`
- **New implementation:**  
  - `app/memory/mem0_client.py::get_mem0_client`
  - extraction flow uses single configured Mem0 client
- **Severity:** major
- **Notes:** This is a real behavior reduction. Rogers could route memory extraction to different LLM backends based on extraction text size. New system has only one Mem0 LLM config path at a time. Large/small model routing is not preserved.

---

## 49) Semantic retrieval layer in context retrieval
- **Original feature:** Rogers context retrieval was three-tier only; no semantic retrieval in the provided retrieval flow.  
- **New implementation:**  
  - `app/flows/build_types/knowledge_enriched.py::ke_inject_semantic_retrieval`
- **Severity:** n/a
- **Notes:** New feature.

---

## 50) Knowledge graph facts in context retrieval
- **Original feature:** Rogers `conv_retrieve_context` did not inject knowledge graph facts in the shown code.  
- **New implementation:**  
  - `app/flows/build_types/knowledge_enriched.py::ke_inject_knowledge_graph`
- **Severity:** n/a
- **Notes:** New feature.

---

## 51) Deployment/package source configurability
- **Original feature:** Rogers used offline package caches and fixed ecosystem wiring.  
  - File/function: Dockerfiles, compose, package caches
- **New implementation:**  
  - `entrypoint.sh`, `Dockerfile`, `config/config.example.yml::packages`
- **Severity:** minor
- **Notes:** Preserved in capability and generalized.

---

# Most Important Gaps

If your goal is strict behavioral preservation for Rogers clients, these are the top issues:

1. **`external_session_id` storage/filtering is missing**  
   - Affects `conv_store_message`, `conv_search`, and `conv_search_messages`.  
   - **Severity: major**

2. **Ad hoc `mem_extract` internal operation is missing**  
   - Background extraction exists, but no explicit admin/internal extraction trigger.  
   - **Severity: major**

3. **Imperator tool contract not preserved (`rogers_chat` → `imperator_chat`)**  
   - Different name, input schema, and behavior.  
   - **Severity: major**

4. **Context retrieval output format changed from XML-ish text blob to message array**  
   - Structured content exists, but client-visible primary output differs.  
   - **Severity: major**

5. **Dual small/large Mem0 extraction model routing is gone**  
   - Rogers used different LLM tiers for extraction depending on text size.  
   - **Severity: major**

6. **`small-basic` build type missing by name/behavior**  
   - `passthrough` is not equivalent.  
   - **Severity: minor to major**, depending on whether existing callers reference that build type.

---

# Bottom-line verdict

**Core Rogers functionality is mostly preserved**, especially:
- conversation creation
- message storage
- async embedding
- tiered context assembly
- context retrieval with assembly waiting
- memory extraction
- memory search
- hybrid message search with reranking
- metrics/health/background retry patterns

**But the rewrite is not a perfect behavioral superset of Rogers.** The most meaningful regressions are:
- loss of `external_session_id` behavior,
- changed Imperator tool contract,
- changed `conv_retrieve_context` output contract,
- missing explicit `mem_extract`,
- missing small/large extraction model routing.

If you want, I can turn this into a **CSV-style gap matrix** or a **blocker/major/minor checklist by file/tool** for easier engineering follow-up.