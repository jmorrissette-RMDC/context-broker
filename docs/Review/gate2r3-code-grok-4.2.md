**Code Review Findings**

**File:** `app/flows/imperator_flow.py` (lines 312-318, `run_imperator_agent`)  
**Severity:** major  
**Description:** The ReAct loop does `messages.append(response)` and `messages.extend(tool_messages)` on a local list that starts from the system prompt + current-turn input. When `max_iterations` is reached it searches the local list for the last `AIMessage`. However the final return only ever returns the *current-turn* messages via `list(state["messages"]) + [AIMessage...]`. If the agent produced intermediate tool-call messages that contain sensitive data or large payloads, they are never persisted to Postgres (only the final assistant message is stored in `_store_imperator_messages`). This breaks the "conversation history is written to conversation_messages" guarantee (M-14) and means the Imperator's own history visible to future turns via the system prompt can be incomplete. The checkpointed state also diverges from the durable store.

**File:** `app/flows/imperator_flow.py` (lines 58, 430)  
**Severity:** major  
**Description:** `_checkpointer = MemorySaver()` is a module-level unbounded in-memory store. The comment (M-13) acknowledges the limitation but states it is "acceptable for the Imperator's single-conversation use case". When the same Imperator flow is used from multiple MCP sessions or the `broker_chat` tool is called with different `conversation_id` values, the in-memory checkpoint grows without bound across all thread_ids. No eviction, TTL, or persistent checkpointer (PostgresSaver, etc.) is used. This is an inevitable OOM vector in any multi-user or long-lived deployment.

**File:** `app/config.py` (lines 55-65, `load_config`)  
**Severity:** major  
**Description:** When the config file content hash changes, `_llm_cache.clear()` and `_embeddings_cache.clear()` are called while other coroutines may be actively using the cached `ChatOpenAI` / `OpenAIEmbeddings` instances (the caches are shared module globals). There is no lock around the clear + subsequent `get_chat_model`/`get_embeddings_model` paths. A coroutine can receive a model reference that has already been removed from the dict or that was created with the previous provider settings. The comment claiming "Dict operations are atomic under the GIL" does not cover the clear-while-in-use race. This breaks the hot-reload guarantee (M-03) and can cause runtime `KeyError` or use of stale models.

**File:** `app/flows/context_assembly.py` (lines 248-250, `summarize_message_chunks`)  
**Severity:** major  
**Description:** `had_errors = any(summary_text is None for _, summary_text in llm_results)` is set, but the `result` dict only adds `"had_errors": True` *after* the loop that inserts successful summaries. `finalize_assembly` later checks `state.get("had_errors")` and skips the `last_assembled_at` update. Because the incremental logic in `calculate_tier_boundaries` only skips already-summarized ranges, a partial failure leaves the window in a state where the next assembly run will re-process the same chunks again. This creates duplicate `conversation_summaries` rows (the unique index added in migration 007 does not cover the case where a previous run partially succeeded). The `had_errors` flag is also never propagated through all routing functions, so an error in one chunk can be silently dropped.

**File:** `app/flows/message_pipeline.py` (lines 130-140, `store_message`)  
**Severity:** blocker  
**Description:** The duplicate-collapse logic (F-04) does a `SELECT ... LIMIT 1` for the previous message *inside* the advisory-lock transaction, then either updates the prior row or inserts a new one. If two identical messages from the same sender arrive concurrently on different connections, both transactions can see the same "previous" row before either commits. Because the lock is only on the conversation hash, both can decide to collapse, producing an incorrect `repeat_count`. The `ON CONFLICT` path only covers the idempotency_key case, not the collapse case. This violates the idempotency requirement (REQ-001 §7.3) and can corrupt the `repeat_count` column under load.

**File:** `app/flows/search_flow.py` (lines 300-310, `hybrid_search_messages`)  
**Severity:** major  
**Description:** Recency bias is applied *after* the SQL query in Python by mutating the candidate dicts and re-sorting. The SQL already returns `limit * 5` rows for reranking, but the Python sort discards the original RRF ordering for all candidates that fall outside the final `limit`. When `recency_decay_days` and `recency_max_penalty` are configured, the effective result set can be smaller than requested and the scores are no longer the true RRF scores. This breaks the "hybrid search" contract and can return fewer results than the caller asked for.

**File:** `app/workers/arq_worker.py` (lines 340-350, `_consume_queue`)  
**Severity:** major  
**Description:** The broad `except (RuntimeError, ConnectionError, json.JSONDecodeError, OSError, ValueError, KeyError, TypeError)` catches *all* errors from the processor (including those raised by the StateGraph itself) and treats them as job failures. However `process_embedding_job`, `process_assembly_job`, and `process_extraction_job` already catch most expected errors and return `{"error": ...}`. The worker then re-raises, triggering the dead-letter path. This means a single malformed job (bad UUID, missing key, etc.) can cause the consumer loop to log a full stack trace on every retry and poll the dead-letter queue repeatedly. The comment "M-24: Broadened exception handler" acknowledges the change but the broadening removes the ability to distinguish transient vs permanent failures, violating the "specific exception handling" rule (REQ-001 §4.5).

**File:** `app/memory/mem0_client.py` (lines 80-85, `get_mem0_client`)  
**Severity:** minor  
**Description:** The config hash only includes the `llm` and `embeddings` sections. If `vector_store` or `graph_store` settings (host, port, collection_name, embedding dims, etc.) change in `config.yml`, the hash does not change, so the old `Memory` instance is reused. This can lead to connections to the wrong database or wrong embedding dimensions after a hot-reload. The comment claims the client is recreated on config change, but the hash is incomplete.

**File:** `app/flows/retrieval_flow.py` (lines 190-200, `load_recent_messages`)  
**Severity:** minor  
**Description:** `highest_summarized_seq` is fetched with `COALESCE(MAX(summarizes_to_seq), 0)`. The subsequent query uses `sequence_number > highest_summarized_seq`. If a tier-2 summary has `summarizes_to_seq = 0` (possible on the very first assembly), the query returns no messages even though recent messages exist. The tier-3 budget calculation then uses an empty list, producing an incomplete context window on the first assembly of a new window.

**File:** `app/config.py` (lines 130-140, `get_embeddings_model`)  
**Severity:** minor  
**Description:** The cache key is `f"{base_url}:{model}"` but the instantiated `OpenAIEmbeddings` also receives `openai_api_base` and `openai_api_key`. If two different providers share the same base_url+model but differ in API key (or other constructor args), the cache will return the first instance. This is a latent credential-mixing bug when switching between local and cloud providers via hot-reload.

**Summary of Severity**
- 1 blocker (duplicate collapse race under concurrency)
- 5 major (memory leak, cache-clear race, partial-failure handling, ReAct history loss, broad exception handler)
- 3 minor (incomplete config hash, off-by-one in first-assembly, cache-key incompleteness)

The codebase is generally well-structured and follows the LangGraph mandate and the resilience requirements, but the concurrency and state-management issues (especially around caching, locking, and the Imperator's checkpointing) are severe enough to cause incorrect behavior or resource exhaustion in production. The idempotency and partial-failure paths need tightening before this can be considered reliable.