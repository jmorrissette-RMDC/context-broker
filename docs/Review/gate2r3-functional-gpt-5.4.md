Below is a functionality-preservation audit comparing **new Context Broker** against **original Rogers**. I focused on preserved behavior, not architectural rewrites.

## Executive summary

The rewrite preserves most of Rogers’ core behavior:

- MCP tool surface is broadly preserved and expanded.
- Core DB-backed conversation/message/context-window operations are preserved.
- Background pipelines for embedding, context assembly, and memory extraction are preserved.
- Hybrid message search is preserved and in some ways improved.
- Health/metrics/config/deployment patterns are preserved or strengthened.
- Imperator exists and is more integrated than Rogers’.

However, there are several meaningful feature gaps or behavior changes:

1. **Conversation search lost structured filters** Rogers had (`flow_id`, `user_id`, `sender_id`, `external_session_id`) — only date filters remain in the new tool.  
2. **Context window search lost direct search-by-window-ID behavior** present in Rogers.  
3. **Memory extraction trigger semantics changed**: in Rogers extraction was queued only for `content_type='conversation'` and after embedding; in the new system extraction is queued for all non-duplicate/non-collapsed messages immediately after storage.  
4. **Context retrieval no longer uses build-type-specific dynamic tier proportions from Rogers for larger windows**; it uses configured percentages only. This is likely acceptable if intentional, but behavior differs.  
5. **Imperator admin tools differ**: Rogers had no dangerous write tools; new requirements mention write capability, but implementation only includes config read + read-only SQL.

---

# Findings

## 1) MCP tool: `conv_create_conversation`

- **Original feature:**  
  Rogers created a conversation and returned its ID. Supported caller-supplied `conversation_id`, `flow_id`, `flow_name`, `user_id`, `title`.  
  - File: `rogers-langgraph/flows/conversation_ops.py`
  - Function: `create_conversation`
  - DB op: `services/database.py::insert_conversation`
- **New implementation:**  
  - File: `app/flows/conversation_ops_flow.py`
  - Function: `create_conversation_node`
  - Dispatch: `app/flows/tool_dispatch.py` branch `conv_create_conversation`
- **Severity:** minor
- **Notes:**  
  Preserved core behavior, including idempotent caller-supplied ID via `ON CONFLICT DO NOTHING`.  
  Difference: **`flow_name` is no longer supported/stored**. New schema stores `flow_id` and `user_id`, but not `flow_name`.

---

## 2) MCP tool: `conv_create_context_window`

- **Original feature:**  
  Create context window for a conversation with `build_type_id` and `max_token_limit`. Validated conversation and build type existence.  
  - File: `rogers-langgraph/flows/conversation_ops.py`
  - Function: `create_context_window`
  - DB op: `services/database.py::insert_context_window`
- **New implementation:**  
  - File: `app/flows/conversation_ops_flow.py`
  - Functions: `resolve_token_budget_node`, `create_context_window_node`
  - Dispatch: `app/flows/tool_dispatch.py`
- **Severity:** minor
- **Notes:**  
  Preserved and expanded. New version adds:
  - participant-scoped windows (`participant_id`)
  - token budget auto-resolution from provider
  - caller override support  
  This is a superset of Rogers behavior, but schema/semantics differ.

---

## 3) MCP tool: `conv_store_message`

- **Original feature:**  
  Stored message with:
  - optional conversation resolution from `context_window_id`
  - deduplication of consecutive duplicate messages from same sender by mutating previous content with repeat counter
  - sequence assignment
  - async embed queueing  
  - Fields: `role`, `sender_id`, `content`, `token_count`, `model_name`, `external_session_id`, `content_type`, `priority`
  - File: `rogers-langgraph/flows/message_pipeline.py`
  - Functions: `resolve_conversation`, `dedup_check`, `store_message`, `queue_embed`
- **New implementation:**  
  - File: `app/flows/message_pipeline.py`
  - Functions: `store_message`, `enqueue_background_jobs`
  - Dispatch: `app/flows/tool_dispatch.py`
- **Severity:** major
- **Notes:**  
  Core storage and async processing are preserved, but there are notable behavioral differences:
  - New implementation **does not accept `context_window_id` input** to resolve a conversation.
  - New dedup behavior is different: instead of modifying content, it increments `repeat_count`.
  - New implementation supports `recipient_id` and `idempotency_key`.
  - Rogers had `external_session_id`; **new implementation does not store/support it**.
  - Rogers `sender_id` was integer-like; new uses string.
  - Background queueing expanded to enqueue memory extraction immediately too.

---

## 4) Consecutive duplicate message collapsing

- **Original feature:**  
  Consecutive duplicate messages from same sender were collapsed into the previous message by editing content and appending `[repeated N times]`.  
  - File: `rogers-langgraph/flows/message_pipeline.py`
  - Function: `dedup_check`
- **New implementation:**  
  - File: `app/flows/message_pipeline.py`
  - Function: `store_message`
- **Severity:** minor
- **Notes:**  
  Behavior preserved conceptually, but representation changed:
  - Rogers mutated message content.
  - New increments `repeat_count` field.  
  Better normalized, but clients expecting suffix text would see different history content.

---

## 5) Idempotent message storage

- **Original feature:**  
  Rogers retried jobs safely, but `conv_store_message` itself had no explicit idempotency key.  
  - File: `rogers-langgraph/flows/message_pipeline.py`
- **New implementation:**  
  - File: `app/flows/message_pipeline.py`
  - Function: `store_message`
- **Severity:** none
- **Notes:**  
  New implementation is stronger; not a gap.

---

## 6) Background embedding pipeline

- **Original feature:**  
  For each stored message:
  - fetch message
  - build contextual embedding using N prior messages as prefix (`embedding.context_window_size`)
  - store embedding
  - queue context assembly
  - queue memory extraction  
  - File: `rogers-langgraph/flows/embed_pipeline.py`
  - Functions: `fetch_message`, `embed_message`, `store_embedding`, `check_context_assembly`, `queue_memory_extraction`
- **New implementation:**  
  - File: `app/flows/embed_pipeline.py`
  - Functions: `fetch_message`, `generate_embedding`, `store_embedding`, `enqueue_context_assembly`
- **Severity:** major
- **Notes:**  
  Embedding generation/storage is preserved, including contextual-prefix embedding.  
  **Difference:** Rogers queued memory extraction from embed pipeline after embedding; new system does **not** do that there. Instead, extraction is queued earlier in message pipeline. Net result is async extraction still happens, but trigger timing/eligibility changed.

---

## 7) Memory extraction trigger restricted by `content_type='conversation'`

- **Original feature:**  
  Rogers queued extraction only if message `content_type == 'conversation'` and `memory_extracted` false.  
  - File: `rogers-langgraph/flows/embed_pipeline.py`
  - Function: `queue_memory_extraction`
- **New implementation:**  
  - File: `app/flows/message_pipeline.py`
  - Function: `enqueue_background_jobs`
- **Severity:** major
- **Notes:**  
  **Behavior gap/difference:** New code queues memory extraction for all stored messages except duplicates/collapsed ones. It does not inspect `content_type`.  
  Since extraction fetches all unextracted conversation messages without content-type filtering in new code, this may pull in tool/system/error-like content that Rogers intentionally excluded.

---

## 8) Memory extraction queue prioritization

- **Original feature:**  
  Rogers used a Redis **ZSET** with message priority/time-derived score so urgent/live messages were extracted first.  
  - File: `rogers-langgraph/flows/embed_pipeline.py`
  - Function: `queue_memory_extraction`
  - Worker: `services/queue_worker.py::_consume_memory_extraction_queue`
- **New implementation:**  
  - File: `app/flows/message_pipeline.py`
  - Function: `enqueue_background_jobs`
  - Worker: `app/workers/arq_worker.py`
- **Severity:** minor
- **Notes:**  
  New code uses simple list semantics with:
  - `LPUSH` for user messages
  - `RPUSH` for others
  - dedup key per conversation  
  This preserves some prioritization, but loses Rogers’ finer-grained numeric priority scheduling.

---

## 9) Context assembly queue threshold trigger

- **Original feature:**  
  Rogers checked all windows after embedding and queued assembly only when conversation estimated tokens crossed `max_token_limit * trigger_threshold_percent`, plus guards for in-progress assembly and whether last assembly already covered the message.  
  - File: `rogers-langgraph/flows/embed_pipeline.py`
  - Function: `check_context_assembly`
- **New implementation:**  
  - File: `app/flows/embed_pipeline.py`
  - Function: `enqueue_context_assembly`
- **Severity:** minor
- **Notes:**  
  Preserved well:
  - threshold-based queueing
  - in-progress lock guard
  - last assembled vs tokens-since check  
  The exact trigger calculation differs (Rogers used estimated conversation token count threshold; new checks tokens since last assembly). Behavior is similar but not identical.

---

## 10) Context assembly lock / single-flight protection

- **Original feature:**  
  Rogers used Redis `assembly_in_progress:<window>` key with `nx=True` to avoid concurrent assembly.  
  - File: `rogers-langgraph/flows/context_assembly.py`
  - Function: `set_assembly_flag`, `clear_assembly_flag`
- **New implementation:**  
  - File: `app/flows/context_assembly.py`
  - Functions: `acquire_assembly_lock`, `release_assembly_lock`
- **Severity:** none
- **Notes:**  
  Preserved and improved with token-based lock ownership validation on release.

---

## 11) Incremental context assembly / avoid re-summarizing old ranges

- **Original feature:**  
  Rogers examined existing active tier-2 summaries and only summarized messages above the highest covered sequence.  
  - File: `rogers-langgraph/flows/context_assembly.py`
  - Function: `calculate_tiers`
- **New implementation:**  
  - File: `app/flows/context_assembly.py`
  - Function: `calculate_tier_boundaries`
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 12) Tier-2 chunk summarization

- **Original feature:**  
  Summarized older messages in chunks of 20 using LLM, stored tier-2 summaries with metadata.  
  - File: `rogers-langgraph/flows/context_assembly.py`
  - Function: `summarize_chunks`
- **New implementation:**  
  - File: `app/flows/context_assembly.py`
  - Function: `summarize_message_chunks`
- **Severity:** none
- **Notes:**  
  Preserved and improved:
  - concurrent chunk summarization
  - prompt externalization
  - duplicate-summary avoidance
  - partial-failure tracking

---

## 13) Tier-1 archival consolidation

- **Original feature:**  
  When >3 active tier-2 summaries, consolidate oldest summaries into one tier-1 archival summary, keep 2 most recent tier-2 active.  
  - File: `rogers-langgraph/flows/context_assembly.py`
  - Function: `consolidate_tier1`
- **New implementation:**  
  - File: `app/flows/context_assembly.py`
  - Function: `consolidate_archival_summary`
- **Severity:** none
- **Notes:**  
  Preserved and improved:
  - includes existing tier-1 summary in reconsolidation
  - transactional deactivation/insertion

---

## 14) Build-type-specific LLM selection for context assembly

- **Original feature:**  
  Rogers selected different summarization LLMs by build type:
  - `standard-tiered` -> Gemini
  - others/default -> Sutherland/local  
  - File: `rogers-langgraph/flows/context_assembly.py`
  - Functions: `_build_summarization_llm`, `select_llm`
- **New implementation:**  
  - File: `app/flows/context_assembly.py`
  - Uses `app.config.get_chat_model`
- **Severity:** minor
- **Notes:**  
  New code has a **single global LLM config**, not per-build-type model selection.  
  This loses Rogers’ behavior where different build types could imply different summarization backends.

---

## 15) Context retrieval blocking while assembly is in progress

- **Original feature:**  
  Retrieval checked assembly flag and waited up to 50s.  
  - File: `rogers-langgraph/flows/retrieval.py`
  - Function: `check_assembly`
- **New implementation:**  
  - File: `app/flows/retrieval_flow.py`
  - Function: `wait_for_assembly`
- **Severity:** none
- **Notes:**  
  Preserved, now configurable.

---

## 16) Context retrieval: three-tier assembly output

- **Original feature:**  
  Returned:
  - tier1 archival summary
  - tier2 chunk summaries
  - tier3 recent verbatim messages
  formatted with XML-ish tags  
  - File: `rogers-langgraph/flows/retrieval.py`
  - Functions: `get_summaries`, `get_recent`, `assemble`
- **New implementation:**  
  - File: `app/flows/retrieval_flow.py`
  - Functions: `load_summaries`, `load_recent_messages`, `assemble_context_text`
- **Severity:** none
- **Notes:**  
  Preserved and expanded with semantic and KG injections.

---

## 17) Retrieval tier-budget behavior

- **Original feature:**  
  Rogers used dynamic tier proportions scaling with window size inside assembly. Retrieval then used overall remaining budget after summaries.  
  - File: `rogers-langgraph/flows/context_assembly.py::calculate_tiers`
  - File: `rogers-langgraph/flows/retrieval.py::get_recent`
- **New implementation:**  
  - File: `app/flows/context_assembly.py::calculate_tier_boundaries`
  - File: `app/flows/retrieval_flow.py::load_recent_messages`
- **Severity:** minor
- **Notes:**  
  New behavior is build-type-config percentage based, not Rogers’ dynamic scaling. This is a meaningful behavior change, though likely aligned to new requirements.

---

## 18) Retrieval excludes verbatim messages already covered by summaries

- **Original feature:**  
  Rogers retrieval loaded recent messages from full conversation and could overlap more with summarized content.  
- **New implementation:**  
  - File: `app/flows/retrieval_flow.py`
  - Function: `load_recent_messages`
- **Severity:** none
- **Notes:**  
  New implementation improves behavior by excluding messages already covered by active tier-2 summaries. No gap.

---

## 19) Semantic retrieval in context window assembly/retrieval

- **Original feature:**  
  Rogers README/design referenced semantic layers/build types, but the actual retrieval flow shown only assembled three tiers and did **not** inject semantic-retrieved messages into the final context.  
  - File: `rogers-langgraph/flows/retrieval.py`
- **New implementation:**  
  - File: `app/flows/retrieval_flow.py`
  - Function: `inject_semantic_retrieval`
- **Severity:** none
- **Notes:**  
  New implementation is a superset. Not a gap.

---

## 20) Knowledge graph fact injection into retrieved context

- **Original feature:**  
  Rogers exposed memory tools, but retrieval flow did **not** inject graph facts into final conversation context.  
- **New implementation:**  
  - File: `app/flows/retrieval_flow.py`
  - Function: `inject_knowledge_graph`
- **Severity:** none
- **Notes:**  
  Superset, not a gap.

---

## 21) MCP tool: `conv_get_history`

- **Original feature:**  
  Return conversation metadata plus all messages in chronological order.  
  - File: `rogers-langgraph/flows/conversation_ops.py`
  - Function: `get_history`
- **New implementation:**  
  - File: `app/flows/conversation_ops_flow.py`
  - Function: `load_conversation_and_messages`
  - Dispatch: `tool_dispatch.py`
- **Severity:** minor
- **Notes:**  
  Preserved.  
  Rogers included `external_session_id` in messages; new does not.  
  New adds `recipient_id`, `content_type`, `priority`, etc. depending on query fields.

---

## 22) MCP tool: `conv_search`

- **Original feature:**  
  Conversation search supported:
  - semantic query
  - structured filters: `flow_id`, `user_id`, `sender_id`, `external_session_id`, date range  
  - grouped by conversation via message similarity  
  - File: `rogers-langgraph/flows/conversation_ops.py::search_conversations`
  - DB: `services/database.py::search_conversations`
- **New implementation:**  
  - File: `app/flows/search_flow.py`
  - Functions: `embed_conversation_query`, `search_conversations_db`
  - Dispatch: `tool_dispatch.py`
  - Model: `app/models.py::SearchConversationsInput`
- **Severity:** major
- **Notes:**  
  Core semantic conversation search remains, but **structured filters are substantially reduced**:
  - Preserved: `query`, `date_from`, `date_to`, `limit`, `offset`
  - Missing: **`flow_id`, `user_id`, `sender_id`, `external_session_id`**  
  This is a real feature loss.

---

## 23) MCP tool: `conv_search_messages`

- **Original feature:**  
  Hybrid two-stage message search:
  - vector ANN + BM25
  - RRF candidate fusion
  - cross-encoder reranking
  - filters: `conversation_id`, `sender_id`, `role`, `external_session_id`, `date range`
  - File: `rogers-langgraph/flows/conversation_ops.py::search_messages`
  - DB: `services/database.py::search_messages`
- **New implementation:**  
  - File: `app/flows/search_flow.py`
  - Functions: `embed_message_query`, `hybrid_search_messages`, `rerank_results`
  - Dispatch/tool schema in `tool_dispatch.py`, `routes/mcp.py`, `models.py`
- **Severity:** major
- **Notes:**  
  Most behavior is preserved and improved:
  - vector + BM25 hybrid
  - RRF
  - cross-encoder reranking
  - recency bias
  - structured filters pushed into SQL correctly  
  **Missing filter:** `external_session_id`.  
  That is a real feature loss.

---

## 24) Conversation search fallback when embeddings unavailable

- **Original feature:**  
  If embedding failed, Rogers fell back to structured-only search.  
  - File: `rogers-langgraph/flows/conversation_ops.py::search_conversations`
- **New implementation:**  
  - File: `app/flows/search_flow.py::embed_conversation_query`
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 25) Message search fallback when reranker unavailable

- **Original feature:**  
  Rogers returned Stage 1 results if reranker failed.  
  - File: `rogers-langgraph/flows/conversation_ops.py::search_messages`
- **New implementation:**  
  - File: `app/flows/search_flow.py::rerank_results`
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 26) MCP tool: `conv_search_context_windows`

- **Original feature:**  
  Search/list/get context windows by:
  - `context_window_id`
  - `conversation_id`
  - `build_type_id`
  - File: `rogers-langgraph/flows/conversation_ops.py::search_context_windows_handler`
  - DB: `services/database.py::search_context_windows`
- **New implementation:**  
  - File: `app/flows/conversation_ops_flow.py`
  - Function: `search_context_windows_node`
  - Input model: `app/models.py::SearchContextWindowsInput`
- **Severity:** major
- **Notes:**  
  New search supports:
  - `conversation_id`
  - `participant_id`
  - `build_type`  
  But **lost direct `context_window_id` lookup via this tool**.  
  That is a real behavior gap compared to Rogers’ “search/list/get by ID”.

---

## 27) MCP tool: `mem_search`

- **Original feature:**  
  Semantic + graph search across Mem0 knowledge; supports `query`, `user_id`, optional `conversation_id`, `agent_id`, `limit`. Returns `results` and `relations`, with degraded mode on failure.  
  - File: `rogers-langgraph/flows/memory_ops.py::mem_search`
- **New implementation:**  
  - File: `app/flows/memory_search_flow.py`
  - Function: `search_memory_graph`
  - Dispatch: `tool_dispatch.py`
- **Severity:** minor
- **Notes:**  
  Core behavior preserved:
  - query by user
  - returns memories + relations
  - degraded mode  
  Missing Rogers optional filters: **`conversation_id` / run_id** and `agent_id`.

---

## 28) MCP tool: `mem_get_context`

- **Original feature:**  
  Query Mem0 and format memories for prompt injection. Could scope by `conversation_id`/`agent_id`.  
  - File: `rogers-langgraph/flows/memory_ops.py::mem_get_context`
- **New implementation:**  
  - File: `app/flows/memory_search_flow.py`
  - Function: `retrieve_memory_context`
  - Dispatch: `tool_dispatch.py`
- **Severity:** minor
- **Notes:**  
  Preserved in core form. Missing optional Rogers scoping fields.

---

## 29) Internal/admin memory tools: `mem_add`, `mem_list`, `mem_delete`

- **Original feature:**  
  Available as internal HTTP endpoints, not MCP-exposed.  
  - File: `rogers-langgraph/server.py`
  - Functions/routes: `/mem_add`, `/mem_list`, `/mem_delete`
  - Logic: `flows/memory_ops.py`
- **New implementation:**  
  - File: `app/flows/memory_admin_flow.py`
  - Dispatch: `app/flows/tool_dispatch.py`
  - MCP exposure: `app/routes/mcp.py`
- **Severity:** none
- **Notes:**  
  Preserved and promoted to MCP.

---

## 30) Internal tool: `mem_extract`

- **Original feature:**  
  Rogers had an internal `/mem_extract` endpoint to trigger extraction from recent conversation messages manually/admin-style.  
  - File: `rogers-langgraph/server.py`
  - Route: `mem_extract_route`
- **New implementation:**  
  - **MISSING**
- **Severity:** minor
- **Notes:**  
  This was not MCP-exposed, but it was real functionality. I do not see an equivalent manual trigger endpoint/tool in the new implementation.

---

## 31) Imperator / agent existence

- **Original feature:**  
  Rogers had `rogers_chat` internal tool/graph using LLM to decide between `conv_search`, `mem_search`, `respond_directly`, `finish`.  
  - File: `rogers-langgraph/flows/imperator.py`
  - Functions: `decide_next_action`, `execute_selected_tool`, `finalize_response`
- **New implementation:**  
  - File: `app/flows/imperator_flow.py`
  - Function: `run_imperator_agent`
  - Routes: `app/routes/chat.py`
  - MCP tool: `broker_chat`
- **Severity:** none
- **Notes:**  
  Preserved and significantly improved:
  - persistent conversation storage
  - tool binding through LangGraph/LangChain
  - OpenAI-compatible chat endpoint
  - checkpointing

---

## 32) Imperator persistence across restarts

- **Original feature:**  
  Rogers Imperator graph did not appear to maintain a persisted dedicated conversation across restarts.  
- **New implementation:**  
  - File: `app/imperator/state_manager.py`
- **Severity:** none
- **Notes:**  
  Superset.

---

## 33) Imperator can use conversation search and memory search tools

- **Original feature:**  
  Rogers Imperator could use `conv_search` and `mem_search`.  
  - File: `rogers-langgraph/flows/imperator.py`
- **New implementation:**  
  - File: `app/flows/imperator_flow.py`
  - Tools: `_conv_search_tool`, `_mem_search_tool`
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 34) Imperator admin/system tools

- **Original feature:**  
  Rogers did not expose config/DB admin tools in the Imperator code shown.
- **New implementation:**  
  - File: `app/flows/imperator_flow.py`
  - Tools: `_config_read_tool`, `_db_query_tool`
- **Severity:** minor
- **Notes:**  
  New code adds admin tooling, but requirements mention read/write config while implementation only supports **read config** and **read-only SQL**. Not a Rogers regression, but slight mismatch to reqs.

---

## 35) Message storage fields: `external_session_id`

- **Original feature:**  
  Stored `external_session_id` on messages and supported it in search filtering.  
  - File: `rogers-langgraph/services/database.py::insert_message`
  - Search: `search_conversations`, `search_messages`
- **New implementation:**  
  - **MISSING**
- **Severity:** major
- **Notes:**  
  This field/behavior is gone entirely:
  - no schema field
  - no input model
  - no search filter support  
  This is one of the clearest preserved-functionality gaps.

---

## 36) Message storage fields: `content_type`

- **Original feature:**  
  Stored `content_type`, used especially for memory extraction gating.  
  - File: `services/database.py::insert_message`
- **New implementation:**  
  - File: `app/flows/message_pipeline.py`
  - Input model: `StoreMessageInput`
- **Severity:** minor
- **Notes:**  
  Stored and preserved, but not used the same way for extraction filtering.

---

## 37) Message storage fields: `priority`

- **Original feature:**  
  Stored priority and used it for extraction queue ordering.  
  - File: `services/database.py::insert_message`
  - Queueing: `flows/embed_pipeline.py::queue_memory_extraction`
- **New implementation:**  
  - File: `app/flows/message_pipeline.py`
  - Input model: `StoreMessageInput`
- **Severity:** minor
- **Notes:**  
  Field preserved, but queue semantics weaker than Rogers.

---

## 38) Build types stored in DB vs config-defined

- **Original feature:**  
  Rogers had `context_window_build_types` table and DB lookups.  
  - File: `rogers-postgres/init.sql`
  - DB function: `get_build_type`
- **New implementation:**  
  - File: `app/config.py::get_build_type_config`
- **Severity:** none
- **Notes:**  
  Architectural change only; do not treat as missing. Functional behavior preserved via config.

---

## 39) Search: BM25 full-text support

- **Original feature:**  
  Rogers used `content_tsv` and BM25-like `ts_rank` for message search.  
  - File: `services/database.py::search_messages`
- **New implementation:**  
  - File: `app/flows/search_flow.py::hybrid_search_messages`
  - Schema: `postgres/init.sql` with `content_tsv`
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 40) Search: reciprocal rank fusion

- **Original feature:**  
  Rogers combined vector + BM25 via RRF.  
  - File: `services/database.py::search_messages`
- **New implementation:**  
  - File: `app/flows/search_flow.py::hybrid_search_messages`
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 41) Search: cross-encoder reranking

- **Original feature:**  
  Rogers reranked with `BAAI/bge-reranker-v2-m3` via peer tool call.  
  - File: `flows/conversation_ops.py::search_messages`
- **New implementation:**  
  - File: `app/flows/search_flow.py::rerank_results`
- **Severity:** none
- **Notes:**  
  Preserved, now local in-process cross-encoder rather than peer call.

---

## 42) Search: recency bias

- **Original feature:**  
  Rogers applied mild recency bias/penalty in hybrid message search.  
  - File: `services/database.py::search_messages`
- **New implementation:**  
  - File: `app/flows/search_flow.py::hybrid_search_messages`
- **Severity:** none
- **Notes:**  
  Preserved and configurable.

---

## 43) Health endpoint behavior

- **Original feature:**  
  Rogers `/health` checked Postgres + Redis, returned healthy/unhealthy.  
  - File: `rogers-langgraph/server.py::health`
- **New implementation:**  
  - File: `app/flows/health_flow.py`
  - Route: `app/routes/health.py`
- **Severity:** none
- **Notes:**  
  Preserved and expanded to include Neo4j degraded status.

---

## 44) Metrics endpoint and MCP metrics tool

- **Original feature:**  
  Rogers exposed `/metrics` and `metrics_get`.  
  - File: `rogers-langgraph/server.py`
- **New implementation:**  
  - File: `app/flows/metrics_flow.py`
  - Route: `app/routes/metrics.py`
  - MCP tool in `tool_dispatch.py`
- **Severity:** none
- **Notes:**  
  Preserved.

---

## 45) Queue retries, dead-letter, sweep

- **Original feature:**  
  Rogers queue worker:
  - retries with backoff
  - dead-letter queue
  - periodic sweep to retry dead letters  
  - File: `services/queue_worker.py`
- **New implementation:**  
  - File: `app/workers/arq_worker.py`
- **Severity:** none
- **Notes:**  
  Preserved and improved with processing queues + delayed queues.

---

## 46) Secret redaction before Mem0 ingestion

- **Original feature:**  
  Rogers used `secret_filter.py` with detect-secrets and custom detectors before Mem0 ingestion.  
  - File: `services/secret_filter.py`
  - Use sites: `flows/memory_extraction.py`, `flows/memory_ops.py`
- **New implementation:**  
  - File: `app/flows/memory_extraction.py`
  - Function: `_redact_secrets`
- **Severity:** minor
- **Notes:**  
  Preserved in principle, but less comprehensive than Rogers.  
  New regex-based redaction likely covers common secrets but is weaker than detect-secrets plugin suite.

---

## 47) OpenAI-compatible chat endpoint

- **Original feature:**  
  Rogers did not expose OpenAI-compatible `/v1/chat/completions`; only internal Imperator path/tooling.
- **New implementation:**  
  - File: `app/routes/chat.py`
- **Severity:** none
- **Notes:**  
  Superset.

---

## 48) MCP transport modes

- **Original feature:**  
  Rogers gateway exposed MCP via gateway library, including sessionless and peer-proxy infrastructure.  
- **New implementation:**  
  - File: `app/routes/mcp.py`
- **Severity:** none
- **Notes:**  
  MCP tool calling preserved. New code explicitly supports GET SSE session + POST sessionless/session-based.

---

# Most important gaps

If you want the short list of real regressions to fix, these are the big ones:

1. **`conv_search` missing Rogers structured filters**  
   - `flow_id`
   - `user_id`
   - `sender_id`
   - `external_session_id`

2. **`conv_search_messages` missing `external_session_id` filter**

3. **Messages no longer store `external_session_id` at all**

4. **`conv_search_context_windows` can’t search/get by `context_window_id`**

5. **Memory extraction trigger semantics changed**
   - Rogers: only for `content_type='conversation'`, after embedding
   - New: all messages immediately enqueue extraction

6. **No manual/internal `mem_extract` equivalent found**

7. **Build-type-specific summarization model selection lost**

---

# Verdict

**Overall:** The rewrite preserves the majority of Rogers’ core behavior and in many areas strengthens it. It is **not** a blocker-level regression overall.

**But it is not a perfect functional preservation**, because several Rogers behaviors are missing or changed in meaningful ways, especially around:

- structured search filters,
- `external_session_id`,
- context-window lookup-by-ID in search,
- memory extraction eligibility/triggering.

If you want, I can turn this into a **CSV-style gap matrix** or a **prioritized remediation checklist** with exact code changes needed in the new implementation.