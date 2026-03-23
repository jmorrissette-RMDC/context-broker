Findings comparing **Context Broker** vs **Rogers** for preserved functionality.

---

## 1) MCP tool `rogers_stats`
- **Original feature:** `rogers/config.json` + `rogers-langgraph/server.py` `_tool_registry["rogers_stats"]` exposed internal stats/queue-depth DB counts for Apgar monitoring.
- **New implementation:** **MISSING**
- **Severity:** minor
- **Notes:** New system has `/metrics` and `metrics_get`, which is better observability overall, but the specific internal MCP tool `rogers_stats` is gone.

---

## 2) Internal HTTP endpoint `mem_extract`
- **Original feature:** `rogers-langgraph/server.py` `/mem_extract` endpoint manually triggered memory extraction from recent conversation messages.
- **New implementation:** **MISSING**
- **Severity:** minor
- **Notes:** Background memory extraction flow exists and is stronger, but this ad hoc admin/internal endpoint is not present.

---

## 3) MCP/OpenAI Imperator tool name change
- **Original feature:** `rogers-langgraph/server.py` `_tool_registry["rogers_chat"]` provided conversational Imperator access.
- **New implementation:** `app/flows/tool_dispatch.py` via `imperator_chat`; OpenAI route at `app/routes/chat.py`
- **Severity:** minor
- **Notes:** Behavior exists, but MCP tool name changed from `rogers_chat` to `imperator_chat`. Since your requested focus is preserved behavior, not naming, this is covered functionally, but any clients expecting the old tool name will break.

---

## 4) `conv_create_context_window` participant scoping semantics changed
- **Original feature:** `rogers-langgraph/flows/conversation_ops.py:create_context_window` and DB schema created context windows by `(conversation_id, build_type_id)` with no participant identifier.
- **New implementation:** `app/flows/conversation_ops_flow.py:create_context_window_node` requires `participant_id` and uniqueness is `(conversation_id, participant_id, build_type)`.
- **Severity:** minor
- **Notes:** This is an intentional architectural/domain expansion, not loss. But old clients that created windows without participant semantics would need adaptation. Functionality is preserved and generalized.

---

## 5) `conv_store_message` input compatibility gap (`conversation_id` fallback)
- **Original feature:** `rogers-langgraph/server.py:_invoke_message_pipeline` accepted either `context_window_id` **or** `conversation_id`; `flows/message_pipeline.py:resolve_conversation` resolved conversation from context window if needed.
- **New implementation:** `app/models.py:StoreMessageInput` requires `context_window_id`; `app/flows/message_pipeline.py` only supports `context_window_id`.
- **Severity:** major
- **Notes:** Core storage behavior exists, but an accepted calling mode from Rogers is gone. This is a real compatibility gap for callers that only know conversation ID.

---

## 6) `conv_store_message` requires non-empty content less flexibly than Rogers
- **Original feature:** `rogers-langgraph/server.py:_invoke_message_pipeline` required `content`; schema stored `content TEXT NOT NULL`.
- **New implementation:** `app/models.py:StoreMessageInput.content` is optional and storage supports null; `app/flows/message_pipeline.py`
- **Severity:** none / not a gap
- **Notes:** New implementation is more capable, not missing. Included here only because behavior differs in a beneficial way.

---

## 7) `conv_store_message` priority parameter ignored
- **Original feature:** `rogers-langgraph/flows/message_pipeline.py` accepted `priority` input and persisted it; downstream extraction queue ordering used it (`flows/embed_pipeline.py:_PRIORITY_OFFSET`).
- **New implementation:** `app/flows/tool_dispatch.py` passes `validated.priority`, but `app/flows/message_pipeline.py:store_message` ignores caller priority and derives priority solely from role using `_ROLE_PRIORITY`.
- **Severity:** major
- **Notes:** This is a substantive behavior loss. In Rogers, callers could influence processing priority (e.g. migration vs live). In new code, that control is gone.

---

## 8) Memory extraction queue prioritization lost
- **Original feature:** `rogers-langgraph/flows/embed_pipeline.py:queue_memory_extraction` used Redis ZSET scores with message priority + timestamp so urgent/live messages were extracted first.
- **New implementation:** `app/flows/message_pipeline.py:enqueue_background_jobs` uses `zadd("memory_extraction_jobs", {extract_job: score})` where score is derived from role only; `app/workers/arq_worker.py` consumes with `zpopmin`.
- **Severity:** major
- **Notes:** New system still prioritizes extraction jobs, but no longer preserves caller-specified priority. Only coarse role-based priority remains.

---

## 9) `conv_search` missing `external_session_id` filter
- **Original feature:** `rogers/README.md`, `rogers/config.json`, and DB/search code supported conversation search filters including `external_session_id`.
- **New implementation:** **MISSING**
- **Severity:** major
- **Notes:** `app/models.py:SearchConversationsInput` and `app/flows/search_flow.py:ConversationSearchState/search_conversations_db` support query/date/flow_id/user_id/sender, but not `external_session_id`.

---

## 10) `conv_search_messages` missing `external_session_id` filter
- **Original feature:** `rogers-langgraph/services/database.py:search_messages` supported `external_session_id`.
- **New implementation:** **MISSING**
- **Severity:** major
- **Notes:** `app/models.py:SearchMessagesInput` and `app/flows/search_flow.py:hybrid_search_messages` omit this filter entirely.

---

## 11) Message history retrieval limit behavior changed
- **Original feature:** `rogers-langgraph/flows/conversation_ops.py:get_history` returned full history; no limit parameter.
- **New implementation:** `app/flows/conversation_ops_flow.py:load_conversation_and_messages` supports optional `limit`.
- **Severity:** none
- **Notes:** Superset behavior; preserved.

---

## 12) Search candidate count changed from fixed Rogers behavior
- **Original feature:** `rogers/README.md`, `services/database.py:search_messages` used hybrid retrieval with top ~50 candidates before reranking top 10.
- **New implementation:** `app/flows/search_flow.py:hybrid_search_messages` uses configurable `search_candidate_limit` default 100 and reranks `limit * 5`.
- **Severity:** minor
- **Notes:** Feature preserved; operational behavior differs. Not a loss.

---

## 13) Cross-encoder reranking provider support differs
- **Original feature:** Rogers used Sutherland peer tool `llm_rerank` for cross-encoder reranking (`flows/conversation_ops.py:search_messages`).
- **New implementation:** `app/flows/search_flow.py:rerank_results` supports local `sentence_transformers.CrossEncoder`; config mentions `cohere|cross-encoder|none`, but only `cross-encoder` and fallback behavior are implemented.
- **Severity:** minor
- **Notes:** Core reranking exists. If Rogers relied on remote reranker/provider indirection, that exact deployment behavior is replaced, but functionality is preserved.

---

## 14) Context retrieval output format changed from XML string to message array
- **Original feature:** `rogers-langgraph/flows/retrieval.py:assemble` returned a string context with XML-ish tags plus structured `tiers`.
- **New implementation:** `app/flows/build_types/*:ret_assemble_context` / `ke_assemble_context` return OpenAI-style `context_messages` plus `context_tiers`.
- **Severity:** minor
- **Notes:** This is an intentional contract change aligned to requirements. The useful content is preserved, but any client depending on old XML text in `context` would need adaptation.

---

## 15) `conv_retrieve_context` no longer returns exactly same top-level field names
- **Original feature:** `rogers-langgraph/server.py:_invoke_retrieval_flow` returned `context`, `tiers`, `total_tokens`, `assembly_status`.
- **New implementation:** `app/flows/tool_dispatch.py` also returns `context`, `tiers`, `total_tokens`, `assembly_status`
- **Severity:** none
- **Notes:** Preserved at tool boundary despite internal changes.

---

## 16) Retrieval waiting semantics preserved
- **Original feature:** `rogers-langgraph/flows/retrieval.py:check_assembly` blocked up to 50s polling every 2s.
- **New implementation:** `app/flows/build_types/standard_tiered.py:ret_wait_for_assembly` and `knowledge_enriched.py:ke_wait_for_assembly`
- **Severity:** none
- **Notes:** Preserved and made configurable.

---

## 17) Retrieval stale-status behavior differs slightly
- **Original feature:** On timeout, Rogers set `assembly_status = "blocked_waiting"`.
- **New implementation:** `ret_wait_for_assembly` / `ke_wait_for_assembly` return `assembly_status = "timeout"` with warning.
- **Severity:** minor
- **Notes:** Functionality preserved; status token changed.

---

## 18) Tiered context assembly preserved
- **Original feature:** `rogers-langgraph/flows/context_assembly.py` did 3-tier assembly: recent verbatim, chunk summaries, archival summary.
- **New implementation:** `app/flows/build_types/standard_tiered.py` assembly + retrieval
- **Severity:** none
- **Notes:** Preserved and improved with locks, partial-failure handling, dynamic scaling.

---

## 19) Incremental chunk summarization preserved
- **Original feature:** `rogers-langgraph/flows/context_assembly.py:calculate_tiers` only summarized unsummarized older messages beyond highest summarized seq.
- **New implementation:** `app/flows/build_types/standard_tiered.py:calculate_tier_boundaries`
- **Severity:** none
- **Notes:** Preserved.

---

## 20) Tier 1 consolidation preserved
- **Original feature:** `rogers-langgraph/flows/context_assembly.py:consolidate_tier1`
- **New implementation:** `app/flows/build_types/standard_tiered.py:consolidate_archival_summary`
- **Severity:** none
- **Notes:** Preserved; new code additionally incorporates existing Tier 1 summary into consolidation.

---

## 21) Build-type-specific LLM selection preserved
- **Original feature:** `rogers-langgraph/flows/context_assembly.py:_build_summarization_llm` selected local/default for small-basic and Gemini for standard-tiered.
- **New implementation:** `app/flows/build_types/standard_tiered.py:_resolve_llm_config` with per-build-type `llm` override; `knowledge_enriched` reuses same assembly.
- **Severity:** none
- **Notes:** Preserved in a more generic config-driven way.

---

## 22) Rogers build type `small-basic` missing
- **Original feature:** `rogers-postgres/init.sql` seeded `small-basic`; context assembly logic explicitly mentioned it.
- **New implementation:** **MISSING**
- **Severity:** major
- **Notes:** New build types are `passthrough`, `standard-tiered`, `knowledge-enriched`. If existing Rogers data or clients reference `small-basic`, that build type is absent. This is a real compatibility gap unless migrated externally.

---

## 23) Rogers custom build types missing (`grace-cag-full-docs`, `gunner-rag-mem0-heavy`)
- **Original feature:** `rogers-postgres/init.sql` seeded custom build types.
- **New implementation:** **MISSING**
- **Severity:** major
- **Notes:** These are concrete behaviors/build strategies present in Rogers schema, even if not fully implemented in code here. New broker allows custom build types in config, but those seeded compatibility IDs are not present by default.

---

## 24) Semantic retrieval added, but Rogers did not have retrieval-layer semantic injection in assembled context
- **Original feature:** Rogers retrieval was tiered-only (`flows/retrieval.py`), while search had hybrid retrieval separately.
- **New implementation:** `app/flows/build_types/knowledge_enriched.py` adds semantic and KG retrieval into context assembly.
- **Severity:** none
- **Notes:** Superset capability, not a gap.

---

## 25) `mem_search` behavior preserved
- **Original feature:** `rogers-langgraph/flows/memory_ops.py:mem_search` searched Mem0 and returned memories/relations with degraded mode on failure.
- **New implementation:** `app/flows/memory_search_flow.py:search_memory_graph`; routed via `tool_dispatch.py`
- **Severity:** none
- **Notes:** Preserved; new code adds half-life scoring.

---

## 26) `mem_get_context` behavior preserved
- **Original feature:** `rogers-langgraph/flows/memory_ops.py:mem_get_context`
- **New implementation:** `app/flows/memory_search_flow.py:retrieve_memory_context`
- **Severity:** none
- **Notes:** Preserved.

---

## 27) Internal `mem_add` preserved and newly MCP-exposed
- **Original feature:** `rogers-langgraph/flows/memory_ops.py:mem_add` existed as internal/admin HTTP endpoint only.
- **New implementation:** `app/flows/memory_admin_flow.py:add_memory`; exposed via `tool_dispatch.py` and MCP tool list.
- **Severity:** none
- **Notes:** Superset behavior.

---

## 28) Internal `mem_list` preserved and newly MCP-exposed
- **Original feature:** `rogers-langgraph/flows/memory_ops.py:mem_list`
- **New implementation:** `app/flows/memory_admin_flow.py:list_memories`
- **Severity:** none
- **Notes:** Preserved.

---

## 29) Internal `mem_delete` preserved and newly MCP-exposed
- **Original feature:** `rogers-langgraph/flows/memory_ops.py:mem_delete`
- **New implementation:** `app/flows/memory_admin_flow.py:delete_memory`
- **Severity:** none
- **Notes:** Preserved.

---

## 30) Secret redaction before Mem0 ingestion preserved
- **Original feature:** `rogers-langgraph/services/secret_filter.py:redact_secrets` used detect-secrets plus custom detectors.
- **New implementation:** `app/flows/memory_extraction.py:_redact_secrets`
- **Severity:** minor
- **Notes:** Behavior preserved in principle, but detection is much simpler regex-based now. This is a weaker implementation and could miss more secrets.

---

## 31) Memory extraction “newest-first within size budget” preserved
- **Original feature:** `rogers-langgraph/flows/memory_extraction.py:build_extraction_text`
- **New implementation:** `app/flows/memory_extraction.py:build_extraction_text`
- **Severity:** none
- **Notes:** Preserved, including not marking partially truncated messages extracted.

---

## 32) Two-tier extraction model routing lost
- **Original feature:** `rogers-langgraph/flows/memory_extraction.py` routed extraction to small vs large Mem0/LLM based on text length (`small_llm_max_chars`, `large_llm_max_chars`), using `get_mem0_small` / `get_mem0_large`.
- **New implementation:** **MISSING**
- **Severity:** major
- **Notes:** New code has a single Mem0 client (`app/memory/mem0_client.py`) and no extraction-tier routing. This is a meaningful functionality reduction in background processing.

---

## 33) Separate small/large Mem0 instances lost
- **Original feature:** `rogers-langgraph/services/mem0_setup.py:get_mem0_small`, `get_mem0_large`
- **New implementation:** `app/memory/mem0_client.py:get_mem0_client` only
- **Severity:** major
- **Notes:** Same gap as above, but specifically at memory/knowledge extraction architecture behavior.

---

## 34) Mem0 graph prompt monkey-patch behavior missing
- **Original feature:** `rogers-langgraph/services/mem0_setup.py:_apply_global_patches` monkey-patched Mem0 graph prompt for Qwen-compatible tool calling and patched PGVector insert dedup.
- **New implementation:** **MISSING**
- **Severity:** major
- **Notes:** This may materially affect memory extraction quality and dedup behavior depending on Mem0 internals. New code relies on stock Mem0.

---

## 35) Mem0 dedup insert monkey-patch missing
- **Original feature:** `rogers-langgraph/services/mem0_setup.py:_apply_global_patches` patched `PGVector.insert` to `ON CONFLICT DO NOTHING`; `rogers-postgres/init.sql` added unique hash/user index.
- **New implementation:** `app/migrations.py:_migration_008` creates `idx_mem0_memories_dedup` on `(memory, user_id)` if table exists
- **Severity:** major
- **Notes:** Not equivalent. Rogers deduped on Mem0 payload hash + user_id with patched insert conflict handling. New index is on `(memory, user_id)` and no insert monkey-patch is present. Duplicate memory insertion semantics may differ or fail noisily.

---

## 36) Contextual embeddings preserved
- **Original feature:** `rogers-langgraph/flows/embed_pipeline.py:embed_message` prepended prior N messages (`embedding.context_window_size`, default 3).
- **New implementation:** `app/flows/embed_pipeline.py:generate_embedding`
- **Severity:** none
- **Notes:** Preserved.

---

## 37) Embedding skip for non-text/tool-call messages improved
- **Original feature:** Rogers embedded message `content`; schema required text.
- **New implementation:** `app/flows/embed_pipeline.py:generate_embedding` explicitly skips null-content/tool-call messages.
- **Severity:** none
- **Notes:** Superset.

---

## 38) Parallel downstream fanout after embedding preserved in effect
- **Original feature:** Rogers embed pipeline queued context assembly and memory extraction after embedding.
- **New implementation:** `app/flows/embed_pipeline.py:enqueue_context_assembly` queues assembly; `app/flows/message_pipeline.py:enqueue_background_jobs` independently queues extraction at store time.
- **Severity:** minor
- **Notes:** End result preserved, but sequencing changed: extraction is enqueued immediately on store, not after embedding. This may slightly change eventual-consistency timing.

---

## 39) Assembly trigger threshold behavior preserved
- **Original feature:** `rogers-langgraph/flows/embed_pipeline.py:check_context_assembly` queued assembly only after threshold crossed.
- **New implementation:** `app/flows/embed_pipeline.py:enqueue_context_assembly`
- **Severity:** none
- **Notes:** Preserved and improved with dedup keys.

---

## 40) Rogers check “already covered by last assembly” mostly preserved, but by different logic
- **Original feature:** `embed_pipeline.py:check_context_assembly` skipped if `last_assembled_at >= message.created_at`.
- **New implementation:** `app/flows/embed_pipeline.py:enqueue_context_assembly` checks tokens since `last_assembled_at` against threshold.
- **Severity:** minor
- **Notes:** Same intent, slightly different semantics. New code could queue/not queue in different edge cases.

---

## 41) Dead-letter retry sweep preserved
- **Original feature:** `rogers-langgraph/services/queue_worker.py:_sweep_dead_letters`
- **New implementation:** `app/workers/arq_worker.py:_sweep_dead_letters`
- **Severity:** none
- **Notes:** Preserved and expanded with delayed queues.

---

## 42) Retry/backoff behavior preserved
- **Original feature:** `queue_worker.py:_handle_failure` retried up to MAX_ATTEMPTS with exponential backoff and dead-letter.
- **New implementation:** `app/workers/arq_worker.py:_handle_job_failure`
- **Severity:** none
- **Notes:** Preserved.

---

## 43) Queue processing isolation preserved
- **Original feature:** `queue_worker.py:run_worker` ran independent consumers for embedding, assembly, extraction.
- **New implementation:** `app/workers/arq_worker.py:start_background_worker`
- **Severity:** none
- **Notes:** Preserved.

---

## 44) Crash-safe lock cleanup preserved
- **Original feature:** Rogers used simple delete cleanup patterns in queue worker / flows.
- **New implementation:** `app/workers/arq_worker.py:process_assembly_job/process_extraction_job` plus atomic Lua release in flows
- **Severity:** none
- **Notes:** Preserved and improved.

---

## 45) Health endpoint Neo4j criticality changed
- **Original feature:** `rogers-langgraph/server.py:/health` only checked Postgres + Redis; README/deps implied Neo4j critical.
- **New implementation:** `app/flows/health_flow.py` checks Postgres, Redis, Neo4j; overall status is degraded if Neo4j fails, unhealthy only if Postgres/Redis fail.
- **Severity:** minor
- **Notes:** More nuanced degraded-mode behavior. Not a missing feature.

---

## 46) OpenAI-compatible chat endpoint is new, not in Rogers
- **Original feature:** none
- **New implementation:** `app/routes/chat.py`
- **Severity:** none
- **Notes:** Superset capability.

---

## 47) Imperator persistence across restarts is new and stronger
- **Original feature:** `rogers-langgraph/flows/imperator.py` had no persistent conversation/context-window state manager.
- **New implementation:** `app/imperator/state_manager.py`
- **Severity:** none
- **Notes:** Superset capability.

---

## 48) Imperator toolset partially different
- **Original feature:** `rogers-langgraph/flows/imperator.py` only reasoned over `conv_search`, `mem_search`, `respond_directly`, `finish`.
- **New implementation:** `app/flows/imperator_flow.py` uses ReAct tools `_conv_search_tool`, `_mem_search_tool`, plus optional admin tools.
- **Severity:** none
- **Notes:** Preserved and more robust.

---

## 49) Imperator persistence of dialogue to conversation history preserved/improved
- **Original feature:** Rogers Imperator did not clearly persist through standard message pipeline.
- **New implementation:** `app/flows/imperator_flow.py:store_and_end` uses standard message pipeline for user + assistant messages.
- **Severity:** none
- **Notes:** Superset.

---

## 50) `conv_search` semantic grouping by conversation preserved
- **Original feature:** `services/database.py:search_conversations` did vector search over messages and grouped by conversation with best score.
- **New implementation:** `app/flows/search_flow.py:search_conversations_db`
- **Severity:** none
- **Notes:** Preserved.

---

## 51) `conv_search_messages` hybrid search preserved
- **Original feature:** `services/database.py:search_messages` supported hybrid vector + BM25 via RRF, plus vector-only/BM25-only fallbacks.
- **New implementation:** `app/flows/search_flow.py:hybrid_search_messages`
- **Severity:** none
- **Notes:** Preserved.

---

## 52) Structured filters in message search preserved except `external_session_id`
- **Original feature:** `services/database.py:search_messages` filtered by `conversation_id`, `sender_id`, `role`, `external_session_id`, `date_from`, `date_to`.
- **New implementation:** `app/flows/search_flow.py:hybrid_search_messages` filters by `conversation_id`, `sender`, `role`, `date_from`, `date_to`.
- **Severity:** major
- **Notes:** Main gap is missing `external_session_id`; sender field renamed semantically from numeric sender_id to string sender.

---

## 53) Search-by-sender semantics changed from integer sender_id to string sender
- **Original feature:** Rogers stored/search filtered on integer `sender_id`.
- **New implementation:** `app/models.py`, DB schema, search flows use string `sender`.
- **Severity:** minor
- **Notes:** Functionality exists but compatibility differs for old callers/data.

---

## 54) Conversation/message schema fields `flow_name`, `external_session_id`, `content_type` not preserved
- **Original feature:** Rogers schema in `rogers-postgres/init.sql` and service methods used these fields.
- **New implementation:** **MISSING**
- **Severity:** major
- **Notes:**  
  - `flow_name`: absent from conversations.  
  - `external_session_id`: absent from messages and search filters.  
  - `content_type`: absent from messages; new system relies on nullable content/tool metadata instead.  
These are real schema/behavior losses relative to Rogers.

---

## 55) `model_name` on stored messages preserved
- **Original feature:** `services/database.py:insert_message`
- **New implementation:** `app/flows/message_pipeline.py:store_message`
- **Severity:** none
- **Notes:** Preserved.

---

## 56) Duplicate-message collapse preserved, but representation differs
- **Original feature:** `flows/message_pipeline.py:dedup_check` updated existing message content by appending `[repeated N times]`.
- **New implementation:** `app/flows/message_pipeline.py:store_message` increments `repeat_count` column and leaves content unchanged.
- **Severity:** minor
- **Notes:** Functional dedup preserved, but retrieval/display behavior may differ because repeated suffix is no longer injected into content.

---

## 57) Consecutive duplicate warnings/loop detection lost
- **Original feature:** `rogers-langgraph/flows/message_pipeline.py:dedup_check` logged info/warning at repeat thresholds.
- **New implementation:** **MISSING**
- **Severity:** minor
- **Notes:** Convenience/observability gap only.

---

## 58) Build-time and runtime config hot reload improved over Rogers
- **Original feature:** `services/config.py:load_config` read once at startup from `config.json`.
- **New implementation:** `app/config.py:load_config/async_load_config` hot-reloads config and clears client caches on change.
- **Severity:** none
- **Notes:** Superset.

---

## 59) Deployment package-source flexibility preserved/improved
- **Original feature:** Rogers used offline package caches in Docker build.
- **New implementation:** `Dockerfile` + `entrypoint.sh` support `local`, `pypi`, `devpi`.
- **Severity:** none
- **Notes:** Superset.

---

## 60) Backup behavior from Rogers Postgres container not preserved
- **Original feature:** `rogers-postgres/Dockerfile`, `backup.sh`, `backup.cron` performed nightly pg_dump backups and retention.
- **New implementation:** **MISSING**
- **Severity:** minor
- **Notes:** Requirements explicitly shift backup responsibility to deployer, so this may be intentional. Still, it is a lost runtime behavior compared to Rogers.

---

# Summary of material gaps

### Blocker
- None identified.

### Major
1. `conv_store_message` no longer accepts `conversation_id`-only mode.
2. Caller-supplied message priority is ignored.
3. Memory extraction prioritization no longer preserves caller priority.
4. `conv_search` missing `external_session_id` filter.
5. `conv_search_messages` missing `external_session_id` filter.
6. Rogers build type `small-basic` missing.
7. Rogers seeded custom build types missing.
8. Two-tier small/large memory extraction routing missing.
9. Separate small/large Mem0 instances missing.
10. Mem0 graph prompt patch missing.
11. Mem0 dedup insert behavior not equivalently preserved.
12. Rogers schema fields `flow_name`, `external_session_id`, `content_type` not preserved.

### Minor
- `rogers_stats` MCP tool missing.
- `mem_extract` admin endpoint missing.
- Duplicate-message display semantics changed.
- Simpler secret redaction than Rogers.
- Retrieval timeout status token changed.
- Loss of backup cron behavior.
- Some compatibility renames (`rogers_chat` → `imperator_chat`, sender_id → sender, context format changes).

If you want, I can turn this into a **CSV-style audit table** or a **pass/fail checklist by tool and subsystem**.