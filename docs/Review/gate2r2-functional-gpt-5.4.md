Below is a functionality-preservation audit of the **new Context Broker** against the **original Rogers** implementation.

I focused on **behavior**, not framework/container differences.

---

# Executive summary

The rewrite preserves **most core Rogers behavior**:

- conversation creation/storage/history/context-window operations
- async embedding pipeline
- async context assembly with Redis locking
- memory extraction and memory search/context
- hybrid message search with BM25 + vector + reranking
- health/metrics exposure
- an Imperator-style built-in agent
- dead-letter/retry background processing
- contextual embeddings
- retrieval blocking while assembly runs

However, there are some **real feature gaps/regressions** from Rogers:

## Highest-risk gaps
1. **`conv_search` lost structured filters Rogers supported** (`flow_id`, `user_id`, `sender_id`, and `external_session_id`) — **major**
2. **`conv_search_context_windows` lost direct lookup by context window ID** — **major**
3. **Rogers internal `mem_extract` capability is not exposed in the new implementation** — **minor/major depending on admin workflows; not MCP-facing**
4. **Imperator behavior changed materially**: Rogers agent could use peer LLM/tooling patterns tuned for the ecosystem; new one is more capable in some ways, but not equivalent in exact tool surface/name and may not preserve Rogers-specific `rogers_chat` semantics — **minor/major depending on compatibility expectations**
5. **Rogers memory extraction had small/large LLM routing by payload size; new version removes that tiered extraction behavior** — **major**

Everything else is largely preserved or improved.

---

# Findings

## 1) Create conversation

- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py::create_conversation`  
  Created a conversation with `conversation_id`, `flow_id`, `flow_name`, `user_id`, `title`; idempotent via `ON CONFLICT DO NOTHING`, returning existing row if present.

- **New implementation:**  
  `app/flows/conversation_ops_flow.py::create_conversation_node`  
  Exposed through `app/flows/tool_dispatch.py` → `conv_create_conversation`

- **Severity:** major

- **Notes:**  
  The new version preserves idempotent create and supports caller-supplied IDs, `flow_id`, `user_id`, `title`.  
  **But `flow_name` is gone** from schema, model, and tool. Rogers stored it. If downstream users relied on `flow_name`, this is a real feature loss.

---

## 2) Create context window

- **Original feature:**  
  `rogers-langgraph/flows/conversation_ops.py::create_context_window`  
  Created a context window for `(conversation_id, build_type_id, max_token_limit)`.

- **New implementation:**  
  `app/flows/conversation_ops_flow.py::resolve_token_budget_node`, `create_context_window_node`  
  Exposed through `conv_create_context_window`

- **Severity:** minor

- **Notes:**  
  New implementation preserves creation and adds participant scoping plus token-budget auto-resolution/override.  
  This is functionally richer.  
  Difference: Rogers used `build_type_id`; new uses `build_type`. That is naming, not behavior loss.

---

## 3) Store message: basic persistence

- **Original feature:**  
  `rogers-langgraph/flows/message_pipeline.py::store_message`  
  Stored message, assigned sequence number, updated conversation counters.

- **New implementation:**  
  `app/flows/message_pipeline.py::store_message`  
  Exposed through `conv_store_message`

- **Severity:** minor

- **Notes:**  
  Core behavior preserved.  
  New implementation adds `recipient_id`, `idempotency_key`, stronger duplicate handling, and transaction-wrapped update.  
  Rogers supported `external_session_id`; new implementation does **not** store this field at all. That is a real metadata loss tied to some search/filter behavior.

---

## 4) Store message by context window ID resolution

- **Original feature:**  
  `rogers-langgraph/flows/message_pipeline.py::resolve_conversation`  
  Allowed `conv_store_message` to accept either `conversation_id` or `context_window_id`, resolving conversation from window.

- **New implementation:**  
  **MISSING**

- **Severity:** major

- **Notes:**  
  New `StoreMessageInput` requires `conversation_id`; there is no `context_window_id` input path.  
  Rogers explicitly supported storing via window reference. If any callers relied on that, this is a behavior regression.

---

## 5) Consecutive duplicate collapse

- **Original feature:**  
  `rogers-langgraph/flows/message_pipeline.py::dedup_check`  
  If same sender posted same content consecutively, Rogers updated existing message content with a visible suffix like `[repeated N times]` and did not insert a new row.

- **New implementation:**  
  `app/flows/message_pipeline.py::store_message`

- **Severity:** minor

- **Notes:**  
  New version preserves duplicate collapse behavior conceptually, but changes storage semantics:
  - Rogers mutated `content` text with a visible repeat suffix.
  - New version increments a `repeat_count` column and leaves content unchanged.
  
  This is probably acceptable architecturally, but if consumers expected repeated-count annotation in retrieved message text, behavior differs.

---

## 6) Idempotent message store

- **Original feature:**  
  Rogers had dedup for consecutive duplicates, but no explicit `idempotency_key`-based atomic idempotency in the message store path.

- **New implementation:**  
  `app/flows/message_pipeline.py::store_message`

- **Severity:** none / improvement

- **Notes:**  
  New implementation improves behavior, no loss.

---

## 7) Message metadata fields

- **Original feature:**  
  `services/database.py::insert_message`  
  Stored `role`, `sender_id`, `content`, `token_count`, `model_name`, `external_session_id`, `content_type`, `priority`.

- **New implementation:**  
  `app/flows/message_pipeline.py::store_message`, schema in `postgres/init.sql`

- **Severity:** major

- **Notes:**  
  Preserved:
  - role
  - sender_id
  - content
  - token_count
  - model_name
  - content_type
  - priority

  Lost:
  - **external_session_id**
  
  Since Rogers also searched/filtered by `external_session_id`, this is a meaningful feature regression, not just schema drift.

---

## 8) Async embedding queueing after store

- **Original feature:**  
  `rogers-langgraph/flows/message_pipeline.py::queue_embed`  
  Stored message then queued embed job.

- **New implementation:**  
  `app/flows/message_pipeline.py::enqueue_background_jobs`

- **Severity:** minor

- **Notes:**  
  Preserved. New version also deduplicates queue entries.

---

## 9) Memory extraction queueing after embedding vs message store

- **Original feature:**  
  Rogers queued memory extraction **after embedding** in `flows/embed_pipeline.py::queue_memory_extraction`.

- **New implementation:**  
  New system queues memory extraction directly in `app/flows/message_pipeline.py::enqueue_background_jobs`

- **Severity:** minor

- **Notes:**  
  Behavior preserved in outcome (message causes extraction), but pipeline order changed:
  - Rogers: embed → queue extraction
  - New: store → queue extraction immediately
  
  This is an architectural/ordering difference, not necessarily a loss, but it could slightly change when extraction runs relative to embedding.

---

## 10) Contextual embeddings with prior-message prefix

- **Original feature:**  
  `rogers-langgraph/flows/embed_pipeline.py::embed_message`  
  Included prior N messages (`embedding.context_window_size`, default 3) as context prefix before embedding.

- **New implementation:**  
  `app/flows/embed_pipeline.py::generate_embedding`

- **Severity:** minor

- **Notes:**  
  Preserved very closely.

---

## 11) Store embeddings in Postgres vector column

- **Original feature:**  
  `services/database.py::store_embedding`

- **New implementation:**  
  `app/flows/embed_pipeline.py::store_embedding`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 12) Trigger context assembly after embedding

- **Original feature:**  
  `rogers-langgraph/flows/embed_pipeline.py::check_context_assembly`  
  Checked all windows for the conversation and queued assembly when token threshold crossed.

- **New implementation:**  
  `app/flows/embed_pipeline.py::enqueue_context_assembly`

- **Severity:** minor

- **Notes:**  
  Preserved and improved:
  - keeps Redis lock skip
  - uses last_assembled_at
  - uses trigger threshold per build type / tuning

---

## 13) Context assembly threshold behavior

- **Original feature:**  
  `rogers-langgraph/flows/embed_pipeline.py::check_context_assembly`  
  Threshold was `window.max_token_limit * build_type.trigger_threshold_percent`.

- **New implementation:**  
  `app/flows/embed_pipeline.py::enqueue_context_assembly`

- **Severity:** minor

- **Notes:**  
  Preserved in spirit.  
  Rogers compared conversation estimated token count against threshold; new version compares **tokens since last assembly** to threshold if already assembled. This is arguably better, but behavior is not identical.

---

## 14) Context assembly distributed lock

- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py::set_assembly_flag`  
  Redis NX+TTL lock prevented concurrent assembly.

- **New implementation:**  
  `app/flows/context_assembly.py::acquire_assembly_lock`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 15) Context assembly loading window/build type

- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py::load_window_config`

- **New implementation:**  
  `app/flows/context_assembly.py::load_window_config`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 16) Incremental summarization of only unsummarized older messages

- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py::calculate_tiers`  
  Used latest active tier-2 summary coverage to only summarize unsummarized messages.

- **New implementation:**  
  `app/flows/context_assembly.py::calculate_tier_boundaries`

- **Severity:** minor

- **Notes:**  
  Preserved closely.

---

## 17) Chunk summarization into tier-2 summaries

- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py::summarize_chunks`

- **New implementation:**  
  `app/flows/context_assembly.py::summarize_message_chunks`

- **Severity:** minor

- **Notes:**  
  Preserved. New version adds duplicate-range idempotency checks and concurrent LLM calls.

---

## 18) Tier-1 archival consolidation

- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py::consolidate_tier1`

- **New implementation:**  
  `app/flows/context_assembly.py::consolidate_archival_summary`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 19) Build-type-specific summarization LLM selection

- **Original feature:**  
  `rogers-langgraph/flows/context_assembly.py::_build_summarization_llm` and `select_llm`  
  Rogers selected different LLMs by build type (`standard-tiered` → Gemini config, others → Sutherland/local).

- **New implementation:**  
  **MISSING**

- **Severity:** major

- **Notes:**  
  New implementation uses one configured `llm` provider for all summarization/consolidation through `get_chat_model(config)`.  
  Rogers had **behavioral distinction by build type** for summarization model selection.  
  This is a real loss if deployments depended on cheaper/smaller local summarization for one build type and cloud model for another.

---

## 20) Retrieval waits while assembly in progress

- **Original feature:**  
  `rogers-langgraph/flows/retrieval.py::check_assembly`

- **New implementation:**  
  `app/flows/retrieval_flow.py::wait_for_assembly`

- **Severity:** minor

- **Notes:**  
  Preserved with configurable timeout/poll interval.

---

## 21) Three-tier retrieval assembly

- **Original feature:**  
  `rogers-langgraph/flows/retrieval.py::{get_summaries,get_recent,assemble}`  
  Built context from tier1, tier2, recent messages.

- **New implementation:**  
  `app/flows/retrieval_flow.py::{load_summaries,load_recent_messages,assemble_context_text}`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 22) Context format markers

- **Original feature:**  
  Retrieval assembled XML-like tags:
  - `<archival_summary>`
  - `<chunk_summaries>`
  - `<recent_messages>`

- **New implementation:**  
  `app/flows/retrieval_flow.py::assemble_context_text`

- **Severity:** minor

- **Notes:**  
  Preserved, with additional sections for semantic context and knowledge graph.

---

## 23) Knowledge-enriched retrieval (semantic + KG)

- **Original feature:**  
  Rogers retrieval was only three-tier episodic retrieval. README describes Mem0 memory separately; retrieval flow itself did not inject semantic/KG into `conv_retrieve_context`.

- **New implementation:**  
  `app/flows/retrieval_flow.py::inject_semantic_retrieval`, `inject_knowledge_graph`

- **Severity:** none / improvement

- **Notes:**  
  New system adds functionality, not a loss.

---

## 24) Search conversations: semantic search grouped by conversation

- **Original feature:**  
  `rogers-langgraph/services/database.py::search_conversations` and `flows/conversation_ops.py::search_conversations`  
  Embedded query, found matching messages, grouped by conversation with best score.

- **New implementation:**  
  `app/flows/search_flow.py::{embed_conversation_query,search_conversations_db}`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 25) Search conversations: structured filters `flow_id`, `user_id`

- **Original feature:**  
  `services/database.py::search_conversations`  
  Supported filters:
  - `flow_id`
  - `user_id`
  - plus date filters
  - plus message-level `sender_id`, `external_session_id` in semantic mode

- **New implementation:**  
  **MISSING**

- **Severity:** major

- **Notes:**  
  New `SearchConversationsInput` only supports:
  - query
  - limit
  - offset
  - date_from/date_to

  Missing Rogers-supported filters:
  - **flow_id**
  - **user_id**
  - **sender_id**
  - **external_session_id**

  This is one of the clearest functional regressions.

---

## 26) Search messages: hybrid vector + BM25 + RRF

- **Original feature:**  
  `services/database.py::search_messages` + `flows/conversation_ops.py::search_messages`  
  Hybrid retrieval, top candidates, recency bias, then reranking.

- **New implementation:**  
  `app/flows/search_flow.py::hybrid_search_messages`

- **Severity:** minor

- **Notes:**  
  Preserved, and generally implemented cleanly.

---

## 27) Search messages: cross-encoder reranking

- **Original feature:**  
  `flows/conversation_ops.py::search_messages`  
  Used reranker tool `llm_rerank` to rerank top candidates.

- **New implementation:**  
  `app/flows/search_flow.py::rerank_results`

- **Severity:** minor

- **Notes:**  
  Preserved functionally, but provider mechanism changed:
  - Rogers used external Sutherland rerank tool
  - New defaults to local `sentence-transformers` CrossEncoder

  Behavior preserved; architecture changed intentionally.

---

## 28) Search messages: structured filters

- **Original feature:**  
  `services/database.py::search_messages`  
  Supported:
  - conversation_id
  - sender_id
  - role
  - external_session_id
  - date_from/date_to

- **New implementation:**  
  `app/flows/search_flow.py::hybrid_search_messages`

- **Severity:** major

- **Notes:**  
  Preserved:
  - conversation_id
  - sender_id
  - role
  - date_from/date_to

  Lost:
  - **external_session_id**

  Since the field is gone entirely, this filter is gone too.

---

## 29) Search context windows by ID

- **Original feature:**  
  `flows/conversation_ops.py::search_context_windows_handler` and `services/database.py::search_context_windows`  
  Allowed search/list/get by:
  - context_window_id
  - conversation_id
  - build_type_id

- **New implementation:**  
  `app/flows/conversation_ops_flow.py::search_context_windows_node`

- **Severity:** major

- **Notes:**  
  New tool supports filters:
  - conversation_id
  - participant_id
  - build_type

  It **does not support lookup by context_window_id**.  
  Rogers explicitly supported "list/get context windows by ID". That capability is missing.

---

## 30) Get full conversation history

- **Original feature:**  
  `flows/conversation_ops.py::get_history`

- **New implementation:**  
  `app/flows/conversation_ops_flow.py::load_conversation_and_messages`

- **Severity:** minor

- **Notes:**  
  Preserved and slightly extended with recipient_id/content_type storage available in schema.

---

## 31) Memory search

- **Original feature:**  
  `rogers-langgraph/flows/memory_ops.py::mem_search`  
  Mem0 search returning memories and relations, degraded mode on failures.

- **New implementation:**  
  `app/flows/memory_search_flow.py::search_memory_graph`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 32) Memory context formatting for prompt injection

- **Original feature:**  
  `rogers-langgraph/flows/memory_ops.py::mem_get_context`

- **New implementation:**  
  `app/flows/memory_search_flow.py::retrieve_memory_context`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 33) Internal mem_add / mem_list / mem_delete admin operations

- **Original feature:**  
  `rogers-langgraph/flows/memory_ops.py::{mem_add,mem_list,mem_delete}`  
  Exposed as internal HTTP endpoints, not MCP.

- **New implementation:**  
  `app/flows/tool_dispatch.py` tool handlers for:
  - `mem_add`
  - `mem_list`
  - `mem_delete`

- **Severity:** minor

- **Notes:**  
  Preserved, and now even exposed via MCP tool list.

---

## 34) Internal mem_extract operation

- **Original feature:**  
  `rogers-langgraph/server.py::mem_extract_route`  
  Manual extraction trigger from recent messages for a conversation.

- **New implementation:**  
  **MISSING**

- **Severity:** minor

- **Notes:**  
  This was not MCP-facing in Rogers, but it was a real internal/admin capability.  
  No equivalent direct trigger endpoint/tool was found.

---

## 35) Memory extraction lock

- **Original feature:**  
  `rogers-langgraph/flows/memory_extraction.py::acquire_extraction_lock`

- **New implementation:**  
  `app/flows/memory_extraction.py::acquire_extraction_lock`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 36) Secret redaction before Mem0 ingestion

- **Original feature:**  
  `services/secret_filter.py::redact_secrets`, used by memory extraction and mem_add

- **New implementation:**  
  `app/flows/memory_extraction.py::_redact_secrets`

- **Severity:** minor

- **Notes:**  
  Preserved in simplified form.  
  Rogers used `detect-secrets` plugin stack and multiple custom detectors; new implementation uses regex patterns only. This is reduced sophistication, but the behavior class still exists.

---

## 37) Memory extraction only for unextracted messages

- **Original feature:**  
  `services/database.py::get_unextracted_messages` and `flows/memory_extraction.py::fetch_unextracted`

- **New implementation:**  
  `app/flows/memory_extraction.py::fetch_unextracted_messages`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 38) Memory extraction newest-first within character budget

- **Original feature:**  
  `rogers-langgraph/flows/memory_extraction.py::build_extraction_text`  
  Selected newest messages first up to char budget, then restored chronological order.

- **New implementation:**  
  `app/flows/memory_extraction.py::build_extraction_text`

- **Severity:** minor

- **Notes:**  
  Preserved closely.

---

## 39) Memory extraction small/large LLM routing based on text size

- **Original feature:**  
  `rogers-langgraph/flows/memory_extraction.py::build_extraction_text` and `services/mem0_setup.py::{get_mem0_small,get_mem0_large}`  
  Routed extraction to small or large Mem0/LLM instance depending on text length.

- **New implementation:**  
  **MISSING**

- **Severity:** major

- **Notes:**  
  New version has a single `get_mem0_client(config)` path.  
  Rogers had explicit extraction-tier routing behavior, which is not preserved.

---

## 40) Memory extraction marks only fully included messages extracted

- **Original feature:**  
  Rogers selected messages up to size budget and marked selected IDs extracted. Oversized truncation handling was more implicit.

- **New implementation:**  
  `app/flows/memory_extraction.py::build_extraction_text`, `mark_messages_extracted`

- **Severity:** none / improvement

- **Notes:**  
  New version is better: partially truncated messages are not marked extracted.

---

## 41) Queue worker independent consumers for embed / assembly / extraction

- **Original feature:**  
  `services/queue_worker.py::run_worker`  
  Three independent async consumer loops plus dead-letter sweep.

- **New implementation:**  
  `app/workers/arq_worker.py::start_background_worker`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 42) Retry with backoff and dead-letter handling

- **Original feature:**  
  `services/queue_worker.py::_handle_failure`, `_sweep_dead_letters`

- **New implementation:**  
  `app/workers/arq_worker.py::_handle_job_failure`, `_sweep_dead_letters`

- **Severity:** minor

- **Notes:**  
  Preserved and arguably improved with processing queues and BLMOVE reliability.

---

## 43) Dead-letter periodic sweep

- **Original feature:**  
  `services/queue_worker.py::_sweep_dead_letters_loop`

- **New implementation:**  
  `app/workers/arq_worker.py::_dead_letter_sweep_loop`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 44) Queue prioritization for memory extraction

- **Original feature:**  
  Rogers used a Redis **ZSET** with timestamp+priority score to prioritize extraction jobs.

- **New implementation:**  
  `app/flows/message_pipeline.py::enqueue_background_jobs`  
  Uses simple list ordering: user role gets `LPUSH`, others `RPUSH`.

- **Severity:** major

- **Notes:**  
  This is a real behavioral downgrade.  
  Rogers had multi-level priority semantics (`priority` 0..3) affecting extraction order.  
  New implementation only distinguishes `role == "user"` vs others and ignores message `priority` field for extraction scheduling.

---

## 45) Message priority stored and used operationally

- **Original feature:**  
  Rogers stored `priority` and used it in extraction queue scoring.

- **New implementation:**  
  `app/flows/message_pipeline.py::store_message` stores `priority`; extraction queue ignores it.

- **Severity:** major

- **Notes:**  
  Priority metadata preserved in DB, but **behavioral use** of priority is largely lost.

---

## 46) Build type storage source

- **Original feature:**  
  Rogers had a DB table `context_window_build_types` seeded in Postgres.

- **New implementation:**  
  `config.yml` build types via `app/config.py::get_build_type_config`

- **Severity:** none

- **Notes:**  
  Intentional architecture change, no behavior loss as long as build types exist. Not flagged missing.

---

## 47) Metrics endpoint and metrics_get tool

- **Original feature:**  
  `server.py::metrics`, `metrics_get`

- **New implementation:**  
  `app/routes/metrics.py`, `app/flows/metrics_flow.py`, `tool_dispatch.py::metrics_get`

- **Severity:** minor

- **Notes:**  
  Preserved.

---

## 48) Health endpoint

- **Original feature:**  
  `server.py::health` checked postgres and redis only.

- **New implementation:**  
  `app/routes/health.py`, `app/flows/health_flow.py`

- **Severity:** none / improvement

- **Notes:**  
  Preserved and expanded to include Neo4j degraded status.

---

## 49) Imperator / built-in agent existence

- **Original feature:**  
  `rogers-langgraph/flows/imperator.py` and tool `rogers_chat`  
  Built-in agent using `conv_search` and `mem_search`.

- **New implementation:**  
  `app/flows/imperator_flow.py`, exposed as `broker_chat` and `/v1/chat/completions`

- **Severity:** minor

- **Notes:**  
  Preserved in spirit, renamed in interface.  
  Since your task says focus on lost behavior rather than intentional architectural changes, rename alone is not an issue if clients are updated.  
  But exact Rogers MCP tool name `rogers_chat` is not preserved.

---

## 50) Imperator persistent conversation across restarts

- **Original feature:**  
  Rogers Imperator state in the provided code did **not** show persistent state manager like the new one.

- **New implementation:**  
  `app/imperator/state_manager.py`

- **Severity:** none / improvement

- **Notes:**  
  Improvement, not a loss.

---

## 51) Imperator admin/system tools

- **Original feature:**  
  Rogers Imperator could use only `conv_search` and `mem_search`.

- **New implementation:**  
  `app/flows/imperator_flow.py` adds optional admin tools `_config_read_tool`, `_db_query_tool`

- **Severity:** none / improvement

- **Notes:**  
  Added capability.

---

## 52) Rogers stats / Apgar internal tool

- **Original feature:**  
  `rogers/config.json` had `rogers_stats` internal tool mapped to `/stats`; README references it for health monitoring.

- **New implementation:**  
  **MISSING**

- **Severity:** minor

- **Notes:**  
  I do not see an equivalent `stats` endpoint/tool.  
  Metrics endpoint exists, which may supersede it, but the exact internal stats behavior is absent.

---

## 53) OpenAI-compatible chat endpoint

- **Original feature:**  
  Rogers did not expose `/v1/chat/completions` in the provided code.

- **New implementation:**  
  `app/routes/chat.py`

- **Severity:** none / improvement

- **Notes:**  
  New functionality.

---

## 54) Search candidate sizing / top-N behavior

- **Original feature:**  
  Rogers hybrid search used a default stage-1 candidate size around 50, reranked top 10.

- **New implementation:**  
  `app/flows/search_flow.py`
  - candidate limit configurable
  - returns `limit` after rerank

- **Severity:** minor

- **Notes:**  
  Preserved behavior class, but exact defaults differ (`search_candidate_limit` 100 in example config). Not a loss.

---

## 55) Conversation search grouped by message match but filterable by sender/session

- **Original feature:**  
  `services/database.py::search_conversations` semantic mode supported sender and external session filters at the message level before grouping.

- **New implementation:**  
  **MISSING**

- **Severity:** major

- **Notes:**  
  This is part of the broader `conv_search` filter regression, but worth calling out separately because it affects practical search quality and scoping.

---

## 56) Conversation schema support for user/flow metadata

- **Original feature:**  
  `conversations` stored `user_id`, `flow_id`, `flow_name`, `title`.

- **New implementation:**  
  `postgres/init.sql` stores `user_id`, `flow_id`, `title`  
  no `flow_name`

- **Severity:** major

- **Notes:**  
  `flow_name` lost; tied to creation/search/display semantics.

---

# Missing / regressed functionality summary

## Blocker
None found.

## Major
1. **`conv_search` missing Rogers structured filters**
   - `flow_id`
   - `user_id`
   - `sender_id`
   - `external_session_id`

2. **`conv_search_messages` missing `external_session_id` filter**

3. **`conv_search_context_windows` missing lookup by `context_window_id`**

4. **`conv_store_message` no longer accepts `context_window_id` as an alternate input**

5. **`external_session_id` message field lost entirely**

6. **`flow_name` conversation field lost**

7. **Context assembly no longer selects summarization LLM by build type**

8. **Memory extraction lost Rogers small/large LLM routing by text size**

9. **Extraction queue prioritization downgraded**
   - Rogers: full priority scoring via ZSET and `priority` field
   - New: simple list ordering, largely ignores `priority`

## Minor
1. `mem_extract` internal admin endpoint missing
2. `rogers_stats` internal tool/endpoint missing
3. Duplicate collapse representation changed from visible content suffix to `repeat_count`
4. Exact Imperator `rogers_chat` tool semantics/name not preserved

---

# Bottom line

The rewrite **does preserve the majority of Rogers core functionality**, especially:
- storage
- async embedding
- context assembly
- retrieval
- memory extraction/search
- hybrid message search
- retries/dead-letter
- metrics/health

But it is **not functionally complete** relative to Rogers.

## Most important gaps to fix for parity
If the goal is true preservation, I would require adding:

1. **Restore `conv_search` filters**
   - `flow_id`
   - `user_id`
   - `sender_id`
   - `external_session_id`

2. **Restore `external_session_id` in schema + message store + search**

3. **Restore `conv_search_context_windows(context_window_id=...)`**

4. **Restore `conv_store_message` support for `context_window_id` input**

5. **Restore build-type-specific summarization model selection**

6. **Restore priority-aware memory extraction scheduling**
   - ideally using ZSET or equivalent weighted queueing

7. **Restore large/small extraction model routing if that cost/performance behavior matters**

If you want, I can turn this into a **machine-readable gap matrix** (CSV/Markdown table) with columns:
`feature | original location | new location | status | severity | remediation`.