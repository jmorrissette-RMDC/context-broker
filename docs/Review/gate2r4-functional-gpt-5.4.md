Below is a preservation audit focused on behavior, not architecture.

I’m comparing the new Context Broker against Rogers and flagging:
- preserved features,
- behavior changes,
- and genuine gaps.

---

# Executive summary

## Preserved well
The rewrite preserves most of Rogers’s core product behavior:

- MCP tool surface is broadly preserved and expanded
- message storage pipeline still does:
  - conversation/message persistence
  - async embedding
  - async context assembly
  - async memory extraction
- context retrieval still supports tiered assembly and waiting on in-progress assembly
- search still supports:
  - conversation search with semantic + structured filters
  - message hybrid search with BM25 + vector + reranking
- memory search/admin capabilities are preserved and improved
- background worker/retry/dead-letter behavior is preserved and improved
- deployment/configuration are preserved at the behavioral level and made more configurable
- Imperator exists in both systems, though behavior differs materially

## Genuine gaps / regressions
The main losses I found are:

1. **`conv_search_context_windows` no longer supports direct lookup by context window ID**  
   Severity: **major**

2. **Conversation search appears to lose Rogers’s `external_session_id` filter**  
   Severity: **major**

3. **Message search appears to lose Rogers’s `external_session_id` filter**  
   Severity: **major**

4. **Imperator tool name / MCP exposure changed from `rogers_chat` to `broker_chat`**  
   If strict backward compatibility with existing callers is required, this is a real API break.  
   Severity: **major**

5. **Internal `mem_extract` admin endpoint/tool behavior from Rogers is not present**  
   Not MCP-exposed in Rogers, but it did exist as callable behavior.  
   Severity: **minor**

6. **Rogers had build-type-specific summarizer routing (small/local vs Gemini) for context assembly; new system uses one global LLM config**  
   This is a meaningful behavioral difference, but not necessarily lost functionality if single-provider config is intentional.  
   Severity: **minor**

---

# Detailed findings

## 1) Create conversation

- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py :: create_conversation`  
  Created a conversation, idempotently returned existing one if same ID already existed.

- **New implementation:**  
  `app/flows/conversation_ops_flow.py :: create_conversation_node`

- **Severity:** minor

- **Notes:**  
  Preserved and improved. New version supports caller-supplied UUID and idempotent `ON CONFLICT DO NOTHING`. Rogers also required `flow_id` in practice via function signature, while new implementation allows title/flow_id/user_id to be optional. That is not a loss.

---

## 2) Create context window

- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py :: create_context_window`  
  Created a context window for a conversation + build type + token limit.

- **New implementation:**  
  `app/flows/conversation_ops_flow.py :: resolve_token_budget_node`, `create_context_window_node`

- **Severity:** minor

- **Notes:**  
  Preserved and improved. New implementation adds:
  - build-type validation from config
  - token budget auto-resolution
  - idempotent uniqueness on `(conversation_id, participant_id, build_type)`

  Rogers windows were conversation/build-type scoped; new system adds `participant_id`, which is an architectural/domain evolution, not a feature loss.

---

## 3) Store message pipeline: resolve conversation from context window

- **Original feature:**  
  `rogers-langgraph/flows/message_pipeline.py :: resolve_conversation`  
  Allowed `conv_store_message` to accept either `conversation_id` or `context_window_id`, resolving conversation from the window.

- **New implementation:**  
  **MISSING**

- **Severity:** major

- **Notes:**  
  In the new MCP schema and dispatch, `conv_store_message` requires `conversation_id` and does not accept `context_window_id`.  
  Rogers explicitly supported both addressing modes. If existing callers relied on storing via window ID, that behavior is gone.

---

## 4) Store message pipeline: consecutive duplicate collapse

- **Original feature:**  
  `rogers-langgraph/flows/message_pipeline.py :: dedup_check`  
  If the same sender sent the same consecutive content, Rogers updated the existing message content with a repeated-count suffix instead of inserting a new row.

- **New implementation:**  
  `app/flows/message_pipeline.py :: store_message`

- **Severity:** minor

- **Notes:**  
  Preserved in spirit, but behavior differs:
  - Rogers mutated `content` with `"[repeated N times]"` suffix.
  - New system increments `repeat_count` field instead, preserving original content.
  
  This is better structurally, but downstream consumers expecting the suffix in content would observe different results.

---

## 5) Store message pipeline: enqueue embedding job

- **Original feature:**  
  `rogers-langgraph/flows/message_pipeline.py :: queue_embed`

- **New implementation:**  
  `app/flows/message_pipeline.py :: enqueue_background_jobs`

- **Severity:** minor

- **Notes:**  
  Preserved. New system pushes `embed_message` jobs to `embedding_jobs`. Same functional outcome.

---

## 6) Store message pipeline: enqueue memory extraction after message storage

- **Original feature:**  
  In Rogers this happened indirectly:
  - message pipeline queued embedding
  - embed pipeline then queued memory extraction  
  (`rogers-langgraph/flows/embed_pipeline.py :: queue_memory_extraction`)

- **New implementation:**  
  `app/flows/message_pipeline.py :: enqueue_background_jobs`

- **Severity:** minor

- **Notes:**  
  Behavior preserved, but sequencing changed:
  - Rogers queued extraction after embedding
  - New system queues extraction immediately after message store

  Since extraction reads unextracted messages from DB and doesn’t require embeddings, this is not a loss, but ordering differs.

---

## 7) Message idempotency

- **Original feature:**  
  Rogers had duplicate-collapse behavior but no robust explicit idempotency key path in the shown code.

- **New implementation:**  
  `app/flows/message_pipeline.py :: store_message`

- **Severity:** minor

- **Notes:**  
  New system adds explicit `idempotency_key` handling. This is an improvement, not a gap.

---

## 8) Message storage fields: recipient_id

- **Original feature:**  
  Rogers message rows stored role, sender_id, content, token_count, model_name, external_session_id, content_type, priority.

- **New implementation:**  
  `app/flows/message_pipeline.py :: store_message` and schema in `postgres/init.sql`

- **Severity:** major

- **Notes:**  
  New system includes `recipient_id`, but I do **not** see `external_session_id` anywhere in the new schema/models/flows.  
  Rogers stored and searched on `external_session_id`; this appears lost entirely.

---

## 9) Conversation retrieval history

- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py :: get_history`  
  Returned conversation metadata and full chronological message history.

- **New implementation:**  
  `app/flows/conversation_ops_flow.py :: load_conversation_and_messages`

- **Severity:** minor

- **Notes:**  
  Preserved and improved with optional `limit` returning newest N in chronological order.

---

## 10) Search context windows by ID

- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py :: search_context_windows_handler` + `services/database.py :: search_context_windows`  
  Supported filtering by:
  - `context_window_id`
  - `conversation_id`
  - `build_type_id`

- **New implementation:**  
  `app/flows/conversation_ops_flow.py :: search_context_windows_node`

- **Severity:** major

- **Notes:**  
  New implementation supports:
  - `conversation_id`
  - `participant_id`
  - `build_type`
  
  But **not `context_window_id`**.  
  Rogers explicitly supported direct get/list by window ID; that lookup capability is missing.

---

## 11) Conversation search: semantic grouping by conversation

- **Original feature:**  
  `rogers-langgraph/services/database.py :: search_conversations`  
  Did semantic search over message embeddings and grouped by conversation using best match score; also supported structured-only listing.

- **New implementation:**  
  `app/flows/search_flow.py :: embed_conversation_query`, `search_conversations_db`

- **Severity:** minor

- **Notes:**  
  Preserved. New version performs grouped conversation search similarly using message embeddings and `MIN(cm.embedding <=> ...)`.

---

## 12) Conversation search structured filters: flow_id, user_id, sender_id, date range

- **Original feature:**  
  `rogers-langgraph/services/database.py :: search_conversations`

- **New implementation:**  
  `app/flows/search_flow.py :: search_conversations_db`

- **Severity:** minor

- **Notes:**  
  Preserved:
  - `flow_id`
  - `user_id`
  - `sender_id`
  - `date_from`
  - `date_to`

---

## 13) Conversation search structured filter: external_session_id

- **Original feature:**  
  Rogers README/config/database mention `external_session_id` filter for `conv_search`  
  `rogers-langgraph/services/database.py :: search_conversations`

- **New implementation:**  
  **MISSING**

- **Severity:** major

- **Notes:**  
  New `SearchConversationsInput` and search flow do not include `external_session_id`.  
  This is a real feature gap for callers using provenance/session filtering.

---

## 14) Message search: hybrid vector + BM25 + RRF

- **Original feature:**  
  `rogers-langgraph/services/database.py :: search_messages`  
  Hybrid search:
  - vector ANN
  - BM25 full text
  - RRF fusion

- **New implementation:**  
  `app/flows/search_flow.py :: hybrid_search_messages`

- **Severity:** minor

- **Notes:**  
  Preserved. New implementation is actually more explicit and robust.

---

## 15) Message search: reranking

- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py :: search_messages`  
  Stage 2 reranking via Sutherland `llm_rerank`, top 10.

- **New implementation:**  
  `app/flows/search_flow.py :: rerank_results`

- **Severity:** minor

- **Notes:**  
  Preserved behaviorally, but implementation differs:
  - Rogers used external reranker tool through Sutherland
  - New uses local `sentence-transformers` cross-encoder by default

  This is an intentional architecture/provider change, not a loss.

---

## 16) Message search filters: conversation_id, sender_id, role, date range

- **Original feature:**  
  `rogers-langgraph/services/database.py :: search_messages`

- **New implementation:**  
  `app/flows/search_flow.py :: hybrid_search_messages`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 17) Message search filter: external_session_id

- **Original feature:**  
  `rogers-langgraph/services/database.py :: search_messages`

- **New implementation:**  
  **MISSING**

- **Severity:** major

- **Notes:**  
  New `SearchMessagesInput` and SQL do not include `external_session_id`.  
  This is a direct feature loss.

---

## 18) Recency bias in message search

- **Original feature:**  
  Rogers DB search applied mild recency bias in hybrid ranking.  
  `rogers-langgraph/services/database.py :: search_messages`

- **New implementation:**  
  `app/flows/search_flow.py :: hybrid_search_messages`

- **Severity:** minor

- **Notes:**  
  Preserved and made configurable via tuning.

---

## 19) Context retrieval waits if assembly in progress

- **Original feature:**  
  `rogers-langgraph/flows/retrieval.py :: check_assembly`  
  Blocked up to 50 seconds while assembly lock existed.

- **New implementation:**  
  `app/flows/retrieval_flow.py :: wait_for_assembly`

- **Severity:** minor

- **Notes:**  
  Preserved. New version also degrades to stale context with warning on timeout instead of just reporting blocked_waiting.

---

## 20) Context retrieval: three-tier assembly output

- **Original feature:**  
  `rogers-langgraph/flows/retrieval.py :: assemble`  
  Produced:
  - archival summary
  - chunk summaries
  - recent messages

- **New implementation:**  
  `app/flows/retrieval_flow.py :: assemble_context_text`

- **Severity:** minor

- **Notes:**  
  Preserved and extended with semantic and knowledge graph sections for enriched build types.

---

## 21) Context retrieval: avoid duplicate tier-2/tier-3 overlap

- **Original feature:**  
  Rogers retrieval loaded recent messages from all conversation messages and could overlap summarized content.

- **New implementation:**  
  `app/flows/retrieval_flow.py :: load_recent_messages`

- **Severity:** minor

- **Notes:**  
  Improved. New system excludes messages already covered by active tier-2 summaries. No gap.

---

## 22) Context assembly: lock/skip concurrent assembly

- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py :: set_assembly_flag`

- **New implementation:**  
  `app/flows/context_assembly.py :: acquire_assembly_lock`

- **Severity:** minor

- **Notes:**  
  Preserved and improved with tokenized atomic release.

---

## 23) Context assembly: incremental summarization of only unsummarized ranges

- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py :: calculate_tiers`

- **New implementation:**  
  `app/flows/context_assembly.py :: calculate_tier_boundaries`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 24) Context assembly: chunk summarization

- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py :: summarize_chunks`

- **New implementation:**  
  `app/flows/context_assembly.py :: summarize_message_chunks`

- **Severity:** minor

- **Notes:**  
  Preserved. New version runs chunk summarization concurrently and adds idempotency checks.

---

## 25) Context assembly: consolidate tier 2 into tier 1 archival summary

- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py :: consolidate_tier1`

- **New implementation:**  
  `app/flows/context_assembly.py :: consolidate_archival_summary`

- **Severity:** minor

- **Notes:**  
  Preserved and improved; new version also folds previous tier-1 into new archival consolidation to avoid older context loss.

---

## 26) Context assembly model selection by build type

- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py :: select_llm`, `_build_summarization_llm`  
  Build type controlled summarizer choice:
  - `small-basic` -> local/Sutherland
  - `standard-tiered` -> Gemini

- **New implementation:**  
  **MISSING as build-type-specific behavior**  
  General LLM use is in `app/config.py :: get_chat_model`

- **Severity:** minor

- **Notes:**  
  The new system still summarizes, but all summarization uses one configured LLM provider/model, not build-type-specific provider routing.  
  Core functionality remains, but Rogers’s differentiated provider behavior is not preserved.

---

## 27) Embedding pipeline: fetch message and generate embedding

- **Original feature:**  
  `rogers-langgraph/flows/embed_pipeline.py :: fetch_message`, `embed_message`

- **New implementation:**  
  `app/flows/embed_pipeline.py :: fetch_message`, `generate_embedding`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 28) Embedding pipeline: contextual embedding using prior messages as prefix

- **Original feature:**  
  `rogers-langgraph/flows/embed_pipeline.py :: embed_message`

- **New implementation:**  
  `app/flows/embed_pipeline.py :: generate_embedding`

- **Severity:** minor

- **Notes:**  
  Preserved with configurable prior-context window.

---

## 29) Embedding pipeline: after embedding, queue context assembly jobs

- **Original feature:**  
  `rogers-langgraph/flows/embed_pipeline.py :: check_context_assembly`

- **New implementation:**  
  `app/flows/embed_pipeline.py :: enqueue_context_assembly`

- **Severity:** minor

- **Notes:**  
  Preserved and improved with dedup key + batched Redis lock checks.

---

## 30) Assembly threshold behavior

- **Original feature:**  
  Rogers checked conversation estimated token count against build type threshold percent when deciding to queue assembly.

- **New implementation:**  
  `app/flows/embed_pipeline.py :: enqueue_context_assembly`

- **Severity:** minor

- **Notes:**  
  Preserved, but semantics differ:
  - Rogers threshold used conversation token count vs threshold
  - New version uses tokens added since `last_assembled_at` when available
  
  This is a sensible refinement, not a loss.

---

## 31) Memory extraction pipeline: fetch unextracted messages

- **Original feature:**  
  `rogers-langgraph/flows/memory_extraction.py :: fetch_unextracted`

- **New implementation:**  
  `app/flows/memory_extraction.py :: fetch_unextracted_messages`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 32) Memory extraction: newest-first selection within character budget

- **Original feature:**  
  `rogers-langgraph/flows/memory_extraction.py :: build_extraction_text`

- **New implementation:**  
  `app/flows/memory_extraction.py :: build_extraction_text`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 33) Memory extraction: secret redaction before Mem0 ingestion

- **Original feature:**  
  Rogers had `services/secret_filter.py :: redact_secrets` and used it before Mem0 ingestion.

- **New implementation:**  
  `app/flows/memory_extraction.py :: _redact_secrets`, `build_extraction_text`

- **Severity:** minor

- **Notes:**  
  Preserved, though implemented with a simpler regex approach than Rogers’s detect-secrets plugin stack.  
  Functionality remains, but coverage may be somewhat narrower.

---

## 34) Memory extraction: per-conversation extraction lock

- **Original feature:**  
  `rogers-langgraph/flows/memory_extraction.py :: acquire_extraction_lock`

- **New implementation:**  
  `app/flows/memory_extraction.py :: acquire_extraction_lock`

- **Severity:** minor

- **Notes:**  
  Preserved and improved with atomic Lua release.

---

## 35) Memory extraction: mark only fully included messages extracted

- **Original feature:**  
  Rogers marked selected message IDs extracted after successful Mem0 add.

- **New implementation:**  
  `app/flows/memory_extraction.py :: build_extraction_text`, `mark_messages_extracted`

- **Severity:** minor

- **Notes:**  
  Preserved and improved: truncated partial message is explicitly excluded from `fully_extracted_ids`.

---

## 36) Memory search

- **Original feature:**  
  `rogers-langgraph/flows/memory_ops.py :: mem_search`

- **New implementation:**  
  `app/flows/memory_search_flow.py :: search_memory_graph`

- **Severity:** minor

- **Notes:**  
  Preserved, including degraded mode when Mem0/Neo4j unavailable.

---

## 37) Memory get context for prompt injection

- **Original feature:**  
  `rogers-langgraph/flows/memory_ops.py :: mem_get_context`

- **New implementation:**  
  `app/flows/memory_search_flow.py :: retrieve_memory_context`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 38) Memory admin: mem_add

- **Original feature:**  
  `rogers-langgraph/flows/memory_ops.py :: mem_add`  
  Internal/admin endpoint, not MCP-exposed.

- **New implementation:**  
  `app/flows/memory_admin_flow.py :: add_memory` and MCP-exposed via `tool_dispatch`

- **Severity:** minor

- **Notes:**  
  Preserved and expanded to MCP.

---

## 39) Memory admin: mem_list

- **Original feature:**  
  `rogers-langgraph/flows/memory_ops.py :: mem_list`

- **New implementation:**  
  `app/flows/memory_admin_flow.py :: list_memories`

- **Severity:** minor

- **Notes:**  
  Preserved and expanded to MCP.

---

## 40) Memory admin: mem_delete

- **Original feature:**  
  `rogers-langgraph/flows/memory_ops.py :: mem_delete`

- **New implementation:**  
  `app/flows/memory_admin_flow.py :: delete_memory`

- **Severity:** minor

- **Notes:**  
  Preserved and expanded to MCP.

---

## 41) Internal/admin mem_extract endpoint

- **Original feature:**  
  `rogers-langgraph/server.py :: /mem_extract`  
  Manually pulled recent messages and forced a memory extraction/add path.

- **New implementation:**  
  **MISSING**

- **Severity:** minor

- **Notes:**  
  This was not MCP-exposed in Rogers, but it was an available HTTP behavior.  
  If admin workflows depended on manual extraction trigger, that capability is gone.

---

## 42) MCP tool inventory: client-facing tools

- **Original feature:**  
  Rogers exposed 10 client-facing MCP tools:
  - conv_create_conversation
  - conv_create_context_window
  - conv_store_message
  - conv_retrieve_context
  - conv_search
  - conv_search_messages
  - conv_get_history
  - conv_search_context_windows
  - mem_search
  - mem_get_context

- **New implementation:**  
  `app/routes/mcp.py :: _get_tool_list`, `app/flows/tool_dispatch.py`

- **Severity:** minor

- **Notes:**  
  All of these are present. New system additionally exposes:
  - mem_add
  - mem_list
  - mem_delete
  - broker_chat
  - metrics_get

---

## 43) MCP tool name compatibility: `rogers_chat`

- **Original feature:**  
  Rogers internal tool registry exposed `rogers_chat` in MCP dispatch.  
  `rogers-langgraph/server.py :: _tool_registry`

- **New implementation:**  
  `app/routes/mcp.py :: _get_tool_list` exposes `broker_chat`  
  `app/flows/tool_dispatch.py` dispatches `broker_chat`

- **Severity:** major

- **Notes:**  
  This is a breaking API rename unless downstream callers were already expected to migrate.  
  Functionality exists, but compatibility does not.

---

## 44) Imperator existence and general agent behavior

- **Original feature:**  
  `rogers-langgraph/flows/imperator.py`  
  Tool-using agent with at least `conv_search` and `mem_search`.

- **New implementation:**  
  `app/flows/imperator_flow.py`

- **Severity:** minor

- **Notes:**  
  Preserved and significantly improved:
  - proper tool binding
  - multi-turn state/checkpointing
  - persistent DB-backed message history
  - optional admin tools

---

## 45) Imperator persistent conversation across restarts

- **Original feature:**  
  I do not see an equivalent Rogers persistent state file for Imperator conversation.

- **New implementation:**  
  `app/imperator/state_manager.py`

- **Severity:** minor

- **Notes:**  
  New system improves on Rogers here. No gap.

---

## 46) Imperator admin tools: config/db introspection

- **Original feature:**  
  Rogers did not show equivalent admin tools.

- **New implementation:**  
  `app/flows/imperator_flow.py :: _config_read_tool`, `_db_query_tool`

- **Severity:** minor

- **Notes:**  
  New capability; not a gap.

---

## 47) Health endpoint

- **Original feature:**  
  `rogers-langgraph/server.py :: /health`  
  Checked Postgres and Redis only, returned simple healthy/unhealthy.

- **New implementation:**  
  `app/routes/health.py`, `app/flows/health_flow.py`, `app/database.py`

- **Severity:** minor

- **Notes:**  
  Preserved and improved with Neo4j visibility and degraded mode.

---

## 48) Metrics endpoint and MCP metrics tool

- **Original feature:**  
  `rogers-langgraph/server.py :: /metrics`, `/metrics_get`

- **New implementation:**  
  `app/routes/metrics.py`, `app/flows/metrics_flow.py`, `app/routes/mcp.py`, `tool_dispatch.py`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 49) Background job retries and dead-letter handling

- **Original feature:**  
  `rogers-langgraph/services/queue_worker.py`  
  3 queues, retries, dead-letter sweep.

- **New implementation:**  
  `app/workers/arq_worker.py`

- **Severity:** minor

- **Notes:**  
  Preserved and improved:
  - processing queues
  - delayed retries via sorted sets
  - malformed dead-letter preservation
  - queue depth metrics

---

## 50) Memory extraction queue priority behavior

- **Original feature:**  
  Rogers used ZSET scoring by message priority/timestamp for extraction jobs.  
  `rogers-langgraph/flows/embed_pipeline.py :: queue_memory_extraction`

- **New implementation:**  
  `app/flows/message_pipeline.py :: enqueue_background_jobs`

- **Severity:** minor

- **Notes:**  
  Partially preserved:
  - New system prioritizes user-role extraction jobs with `LPUSH` vs others `RPUSH`
  - Rogers had finer-grained numeric priority buckets and timestamp ordering

  Functional prioritization remains, but less granular.

---

## 51) Search candidate counts / top-N semantics

- **Original feature:**  
  Rogers hybrid retrieval used top 50 candidates and reranked to top 10.

- **New implementation:**  
  `app/flows/search_flow.py :: hybrid_search_messages`, `rerank_results`

- **Severity:** minor

- **Notes:**  
  Behavior preserved but now configurable:
  - candidate limit from config
  - final limit from request
  - over-fetch by `limit * 5`

---

## 52) Build types and context window assembly strategies

- **Original feature:**  
  Rogers had DB-stored build types including:
  - `small-basic`
  - `standard-tiered`
  - custom seeds like `grace-cag-full-docs`, `gunner-rag-mem0-heavy`

- **New implementation:**  
  `config/config.example.yml :: build_types`, `app/config.py :: get_build_type_config`

- **Severity:** minor

- **Notes:**  
  Generic build-type support is preserved, but seeded custom Rogers-specific build types are not present by default. Since deployers can define arbitrary build types in config, this is not necessarily a lost capability.

---

# Summary of missing / regressed behaviors

## Major
1. **`conv_store_message` no longer accepts `context_window_id` as alternate addressing mode**
2. **`conv_search_context_windows` no longer supports lookup by `context_window_id`**
3. **`conv_search` lost `external_session_id` filter**
4. **`conv_search_messages` lost `external_session_id` filter**
5. **`rogers_chat` MCP/API name not preserved; replaced by `broker_chat`**

## Minor
6. **Internal/admin `mem_extract` endpoint missing**
7. **Build-type-specific LLM routing for context assembly not preserved**
8. **Duplicate collapse representation changed from content suffix to `repeat_count` field**
9. **Memory extraction priority ordering is less granular than Rogers ZSET priority scheme**

---

# Bottom-line verdict

The rewrite preserves **most core functionality** of Rogers and, in several areas, improves correctness and robustness.

However, it is **not a perfect functional preservation** of Rogers. The most important gaps are:

- missing `external_session_id` search/filter behavior,
- missing direct context-window-ID search in `conv_search_context_windows`,
- missing `context_window_id`-based message store path,
- and the API rename from `rogers_chat` to `broker_chat`.

If strict backward compatibility is the goal, those should be addressed before declaring parity. If you want, I can turn this into a **CSV-style gap matrix** or a **patch checklist ordered by implementation priority**.