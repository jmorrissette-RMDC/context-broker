Here are the review findings focused on correctness, runtime reliability, async safety, security, and architecture.

---

### 1. Runtime failure: passing undeclared state key into `MessagePipelineState`
- **File:** `app/flows/tool_dispatch.py`
- **Function/line:** `dispatch_tool()` → `conv_store_message` branch
- **Severity:** **blocker**
- **Description:** The state passed to `build_message_pipeline().ainvoke(...)` includes `"priority": validated.priority`, but `MessagePipelineState` does not declare a `priority` field and `store_message()` ignores caller priority entirely, deriving it from role. In LangGraph, undeclared/mismatched state keys can cause graph/state validation failures depending on runtime expectations. Even when it doesn’t crash, the API contract is inconsistent: caller supplies priority, dispatcher passes it, but pipeline silently ignores it. This is a correctness/runtime-contract bug.

---

### 2. Runtime failure: `degraded` key checked on flows that never return it
- **File:** `app/flows/tool_dispatch.py`
- **Function/line:** `dispatch_tool()` → `mem_get_context`, `mem_add`, `mem_list`, `mem_delete` branches
- **Severity:** **blocker**
- **Description:** These branches do `if result.get("error") and not result.get("degraded"):` but the corresponding flows (`build_memory_context_flow`, `build_mem_add_flow`, `build_mem_list_flow`, `build_mem_delete_flow`) do not define or return a `degraded` field. This means all errors are treated as non-degraded and raised, which may be unintended; worse, the dispatcher is depending on a state contract that does not exist. This is a graph/dispatcher contract mismatch that will produce incorrect behavior at runtime.

---

### 3. Runtime crash on Redis lock TTL renewal due to wrong response type assumption
- **File:** `app/flows/build_types/standard_tiered.py`
- **Function/line:** `summarize_message_chunks()` → inner `_summarize_chunk()`
- **Severity:** **blocker**
- **Description:** `init_redis()` creates the Redis client with `decode_responses=True`, so `await redis.get(lock_key)` returns a `str`, not `bytes`. The code does `current_val.decode() == lock_token`, which will raise `AttributeError: 'str' object has no attribute 'decode'` during chunk summarization. That can abort assembly in the middle of processing.

---

### 4. Entry point installs from missing requirements file
- **File:** `entrypoint.sh`
- **Function/line:** package install branches (`local`, `devpi`)
- **Severity:** **blocker**
- **Description:** The script runs `pip install ... -r /app/requirements.txt`, but the Dockerfile only copies `requirements.txt` into the working directory before dependency install and does **not** copy it into the final image later. Since `WORKDIR /app` makes the file available as `./requirements.txt`, `/app/requirements.txt` may not exist at runtime depending on build layering/copy behavior. This can break container startup when `packages.source` is `local` or `devpi`.

---

### 5. Import-time graph compilation can fail app startup before dependencies are ready
- **File:** `app/routes/health.py`
- **Function/line:** module scope `_health_flow = build_health_check_flow()`
- **Severity:** **major**
- **Description:** The project requirements explicitly favor independent startup and lazy initialization. Compiling graphs at import time increases startup fragility and can cause import-order issues. While this specific flow is simple, similar eager compilation appears elsewhere and violates the intended startup model. Any future dependency in graph build code can turn this into a startup blocker.

---

### 6. Import-time graph compilation in metrics route violates lazy-init/startup resilience
- **File:** `app/routes/metrics.py`
- **Function/line:** module scope `_metrics_flow = build_metrics_flow()`
- **Severity:** **major**
- **Description:** Same architectural/runtime issue as above. Graphs should be lazily built to avoid import-time side effects and improve startup resilience. This is especially important in this codebase because startup deliberately tolerates missing dependencies.

---

### 7. Blocking file I/O in async request handlers
- **File:** `app/routes/chat.py`
- **Function/line:** `chat_completions()`
- **Severity:** **major**
- **Description:** The route uses synchronous `load_config()` inside an async FastAPI handler. `load_config()` performs `os.stat`, and on cache miss performs blocking file read + YAML parse. The codebase already provides `async_load_config()` specifically to avoid blocking the event loop. Under concurrent load or config changes, this can stall the event loop.

---

### 8. Blocking file I/O in async request handlers
- **File:** `app/routes/health.py`
- **Function/line:** `health_check()`
- **Severity:** **major**
- **Description:** Uses synchronous `load_config()` inside an async route handler. Same event-loop blocking issue as above.

---

### 9. Blocking file I/O in async request handlers
- **File:** `app/routes/mcp.py`
- **Function/line:** `mcp_sse_session()`, `mcp_tool_call()`
- **Severity:** **major**
- **Description:** Both async handlers call synchronous `load_config()`. The config module already has `async_load_config()` for this exact case. This is avoidable blocking I/O in request paths.

---

### 10. Blocking file I/O in async tools executed inside agent loop
- **File:** `app/flows/imperator_flow.py`
- **Function/line:** `_conv_search_tool()`, `_mem_search_tool()`, `_config_read_tool()`
- **Severity:** **major**
- **Description:** These async tools call synchronous config/prompt/file operations (`load_config()`, direct `open(...)`). Since they run inside the graph’s async execution path, they can block the event loop. `_config_read_tool()` in particular does direct synchronous file I/O from an async function.

---

### 11. Lock leak on error path in passthrough assembly
- **File:** `app/flows/build_types/passthrough.py`
- **Function/line:** graph construction in `build_passthrough_assembly()`, node `pt_finalize()`
- **Severity:** **major**
- **Description:** If `pt_finalize()` raises (e.g. DB issue), the graph has no error-routing path to `pt_release_lock()`. The Redis lock can remain until TTL expiry, causing avoidable skipped assemblies and stale state. Standard-tiered has explicit crash cleanup in worker code; passthrough lacks equivalent flow-level protection.

---

### 12. Retrieval can fail hard when Redis is unavailable instead of degrading
- **File:** `app/flows/build_types/standard_tiered.py`
- **Function/line:** `ret_wait_for_assembly()`
- **Severity:** **major**
- **Description:** `get_redis()` or `redis.exists()` can raise if Redis is unavailable. This function does not catch Redis errors and therefore retrieval fails outright, even though waiting for assembly is not a core requirement for returning context. Per requirements, optional component failures should degrade gracefully.

---

### 13. Retrieval can fail hard when Redis is unavailable instead of degrading
- **File:** `app/flows/build_types/knowledge_enriched.py`
- **Function/line:** `ke_wait_for_assembly()`
- **Severity:** **major**
- **Description:** Same issue as standard-tiered retrieval wait node. A Redis outage should not prevent context retrieval entirely.

---

### 14. Search path can crash on invalid UUID input despite external callers not being the only entrypoint
- **File:** `app/flows/conversation_ops_flow.py`
- **Function/line:** `create_context_window_node()`, `load_conversation_and_messages()`, `search_context_windows_node()`
- **Severity:** **major**
- **Description:** These nodes call `uuid.UUID(...)` on state values without catching `ValueError`. While MCP handlers validate via Pydantic, graphs are reusable internally and should validate their own boundaries or fail gracefully. Unhandled `ValueError` here will bubble up and can turn bad input into 500s instead of structured errors.

---

### 15. Search path can crash on invalid UUID input
- **File:** `app/flows/search_flow.py`
- **Function/line:** `hybrid_search_messages()` → `_build_extra_filters()`, `search_conversations_db()` direct UUID conversions
- **Severity:** **major**
- **Description:** `uuid.UUID(conversation_id)` is performed without guarding `ValueError`. Again, graph code should not assume only validated callers. A malformed internal or future caller input crashes the flow.

---

### 16. Startup retry loop uses stale startup config forever
- **File:** `app/main.py`
- **Function/line:** `_postgres_retry_loop()`, `_redis_retry_loop()`
- **Severity:** **major**
- **Description:** Both retry loops receive the startup `config` object and reuse it indefinitely. The system design says hot-reloadable settings should be reread per operation. If startup fails due to bad infra-related config that is later corrected in the mounted config, the retry loop will continue using the stale original config until restart.

---

### 17. Metrics route ignores collected output type/content and imports Prometheus generation twice
- **File:** `app/routes/metrics.py`
- **Function/line:** `get_metrics()`
- **Severity:** **minor**
- **Description:** The route imports `generate_latest` but does not use it; instead it relies on text returned from the flow. Not a style issue—the concern is architectural duplication: metrics collection logic exists both in the flow and route imports, increasing risk of divergence. Today it works, but the route’s behavior depends on string formatting rather than a stronger metrics object contract.

---

### 18. Session queue accounting leaks on SSE disconnect
- **File:** `app/routes/mcp.py`
- **Function/line:** `mcp_sse_session()` → `event_stream()` finally block
- **Severity:** **major**
- **Description:** `_total_queued_messages` is decremented when messages are consumed, but if a session disconnects with items still in its queue, the finally block removes the session without subtracting remaining queued messages. This permanently inflates global queue pressure accounting and can trigger unnecessary eviction of future sessions.

---

### 19. Session registry is mutated concurrently without synchronization
- **File:** `app/routes/mcp.py`
- **Function/line:** module-level `_sessions`, `_evict_stale_sessions()`, `mcp_sse_session()`, `mcp_tool_call()`
- **Severity:** **major**
- **Description:** `_sessions` and `_total_queued_messages` are shared mutable globals modified by concurrent async requests with no lock. FastAPI request handlers can interleave, causing race conditions in eviction, queue accounting, and session existence checks. Examples: session added while eviction iterates; queued message count becoming inconsistent; session removed between existence check and enqueue.

---

### 20. Delayed retry promotion for extraction jobs destroys original priority
- **File:** `app/workers/arq_worker.py`
- **Function/line:** `_sweep_delayed_queues()`
- **Severity:** **major**
- **Description:** For `memory_extraction_jobs`, delayed jobs are promoted back with `await redis.zadd(queue_name, {raw: 0})`, hardcoding score `0`. That overwrites original priority ordering and can reorder retries incorrectly relative to newly queued jobs.

---

### 21. Dead-letter sweep assumes extraction jobs carry `priority`, but enqueue path never sets it
- **File:** `app/workers/arq_worker.py`
- **Function/line:** `_sweep_dead_letters()`
- **Severity:** **major**
- **Description:** Retried extraction jobs are reinserted with `score = job.get("priority", 0)`, but `enqueue_background_jobs()` does not include a `priority` field in extraction jobs. As a result, all dead-lettered extraction jobs are retried at score 0, collapsing intended role-based prioritization.

---

### 22. Worker crash cleanup can delete another worker’s lock
- **File:** `app/workers/arq_worker.py`
- **Function/line:** `process_assembly_job()`, `process_extraction_job()` exception handlers
- **Severity:** **major**
- **Description:** On graph crash, the worker unconditionally does `await r.delete(lock_key)`. If the original lock expired and another worker acquired a new lock for the same key, this cleanup deletes the new owner’s lock and reintroduces concurrent-processing races. The release should use token-checked atomic delete, as elsewhere in the codebase.

---

### 23. Message search reranker failure handling misses common model-loading exceptions
- **File:** `app/flows/search_flow.py`
- **Function/line:** `_get_reranker()`, `rerank_results()`
- **Severity:** **major**
- **Description:** Loading `sentence_transformers.CrossEncoder` can raise import/library/runtime errors not limited to `OSError`, `RuntimeError`, `ValueError`. `_get_reranker()` does not handle them, and `rerank_results()` only catches a narrow subset. A bad local model install can crash search requests instead of degrading to RRF-only results.

---

### 24. `check_redis_health()` misses `RuntimeError` from uninitialized client
- **File:** `app/database.py`
- **Function/line:** `check_redis_health()`
- **Severity:** **major**
- **Description:** `get_redis()` raises `RuntimeError` if Redis has not been initialized, but `check_redis_health()` does not catch it. This can cause the health flow to fail instead of returning `redis_ok=False`. `check_postgres_health()` already catches `RuntimeError`; Redis health should mirror that behavior.

---

### 25. `tool_calls` fields are read from DB rows that were not selected
- **File:** `app/flows/build_types/standard_tiered.py`
- **Function/line:** `ret_load_recent_messages()`, `ret_assemble_context()`
- **Severity:** **major**
- **Description:** `ret_load_recent_messages()` selects `id, role, sender, content, sequence_number, token_count, created_at` but `ret_assemble_context()` later checks `m.get("tool_calls")` and `m.get("tool_call_id")`. Those fields will never be present in retrieved messages, so tool call metadata is silently dropped from assembled context. This is a data loss/logic bug.

---

### 26. Same tool-call metadata loss in knowledge-enriched retrieval
- **File:** `app/flows/build_types/knowledge_enriched.py`
- **Function/line:** `ke_load_recent_messages()`, `ke_assemble_context()`
- **Severity:** **major**
- **Description:** Same issue as standard-tiered: DB query doesn’t fetch `tool_calls`/`tool_call_id`, but assembly expects them. Retrieved recent messages lose structured tool-call data.

---

### 27. Same tool-call metadata loss in passthrough retrieval
- **File:** `app/flows/build_types/passthrough.py`
- **Function/line:** `pt_load_recent()`
- **Severity:** **major**
- **Description:** Query omits `tool_calls` and `tool_call_id`, but output assembly tries to preserve them. Tool messages are therefore not faithfully reconstructed.

---

### 28. Build-type registration import side effects create brittle startup coupling
- **File:** `app/flows/build_types/__init__.py`
- **Function/line:** module import side effects
- **Severity:** **minor**
- **Description:** Importing the package causes registration as a side effect. This creates hidden dependency ordering between imports and runtime behavior. It works today, but it is brittle: forgetting one import path or importing registry before package registration can yield “build type not registered” errors. Reliability would improve with explicit registration during startup.

---

### 29. `load_prompt()` sync I/O used in async flow nodes
- **File:** `app/flows/build_types/standard_tiered.py`
- **Function/line:** `summarize_message_chunks()`, `consolidate_archival_summary()`
- **Severity:** **major**
- **Description:** These async nodes call synchronous `load_prompt()`, which does stat/read on cache miss. The module provides `async_load_prompt()` specifically to avoid blocking. Under prompt changes or cold cache, assembly blocks the event loop.

---

### 30. Imperator graph build helper is dead code and mismatched with actual graph behavior
- **File:** `app/flows/imperator_flow.py`
- **Function/line:** `_build_tool_node()`
- **Severity:** **minor**
- **Description:** The helper builds a config-dependent `ToolNode`, but `build_imperator_flow()` ignores it and always registers all tools. This matters because it increases risk of future inconsistencies: agent binding is config-gated, execution node is not. If tool call fabrication or prompt injection causes an admin tool call when admin tools are disabled, the ToolNode can still execute it because it is registered.

---

### 31. Admin tool exposure gap: execution node always includes admin tools
- **File:** `app/flows/imperator_flow.py`
- **Function/line:** `build_imperator_flow()`
- **Severity:** **major**
- **Description:** Even when `admin_tools` is false, the `ToolNode` includes `_config_read_tool` and `_db_query_tool`. The assumption is that the LLM won’t call tools it wasn’t bound to, but this is not a strong security boundary. A crafted AI message with tool calls or an internal misuse could cause execution of admin-only tools despite config intending them disabled.

---

### 32. `_db_query_tool` enables arbitrary read access without any caller auth boundary
- **File:** `app/flows/imperator_flow.py`
- **Function/line:** `_db_query_tool()`
- **Severity:** **major**
- **Description:** The tool is described as “Admin-only,” but there is no actual request authentication/authorization layer anywhere in the app. If `admin_tools` is enabled, any caller who can reach chat can potentially induce DB reads. This is a real security concern because the deployment intentionally ships without auth by default.

---

### 33. Health semantics inconsistent with documented requirement
- **File:** `app/flows/health_flow.py`
- **Function/line:** `check_dependencies()`
- **Severity:** **minor**
- **Description:** The requirements document says `/health` should verify connectivity to all backing services and return `200` healthy / `503` unhealthy. Current implementation treats Neo4j failure as degraded `200`. This may be intentional, but it diverges from the stated contract and can mislead orchestrators relying on health semantics.

---

### 34. Missing catch for `yaml.YAMLError`/bad top-level type in config read helper used from async path
- **File:** `app/config.py`
- **Function/line:** `_read_and_parse_config()`
- **Severity:** **minor**
- **Description:** `_read_and_parse_config()` raises `ValueError` when top-level YAML is not a dict, but `verbose_log_auto()` only suppresses `(RuntimeError, OSError)`. A malformed config can therefore unexpectedly surface `ValueError` from a logging helper path rather than quietly disabling verbose logging. This is a reliability issue because logging should not destabilize request processing.

---

### 35. Search conversation vector query can become extremely expensive with sender filter
- **File:** `app/flows/search_flow.py`
- **Function/line:** `search_conversations_db()` → `_build_conv_filters()`
- **Severity:** **major**
- **Description:** The sender filter is implemented as a correlated `EXISTS` subquery per conversation in a query already joining all message embeddings and grouping. On large datasets this can become very expensive and negate ANN benefits. This is a real performance issue, not style: user-facing search latency can degrade sharply.

---

### 36. Assembly token-threshold query overcounts tokens for windows sharing a conversation
- **File:** `app/flows/embed_pipeline.py`
- **Function/line:** `enqueue_context_assembly()`
- **Severity:** **major**
- **Description:** The batch token-since-last-assembly query joins all conversation messages newer than each window’s `last_assembled_at`, but windows are per conversation already. If multiple windows on the same conversation have different assembly strategies, the same global conversation token delta is used for all windows. That may be intended, but the trigger threshold is applied as if “tokens since last assembly” were window-specific. This can enqueue assemblies unnecessarily and waste LLM work.

---

### 37. Duplicate DB pool retrieval in same function is unnecessary and can hide future pool swap bugs
- **File:** `app/flows/memory_extraction.py`
- **Function/line:** `fetch_unextracted_messages()`
- **Severity:** **minor**
- **Description:** The function calls `get_pg_pool()` twice (`pool` and `pool2`) for related reads. Not a style issue: if future reconnection/pool swap logic is introduced, separate fetches could observe different pools or states. Using one acquired connection would be more reliable and cheaper.

---

### 38. Message collapse logic updates conversation `updated_at` but not token counters
- **File:** `app/flows/message_pipeline.py`
- **Function/line:** `store_message()`
- **Severity:** **major**
- **Description:** When a duplicate consecutive message is collapsed, `repeat_count` is incremented but `conversations.total_messages` and `estimated_token_count` are not updated. If repeated messages are semantically meaningful for quotas/assembly triggers/history counts, conversation metadata becomes inaccurate. Since the system uses those counters operationally, this can affect downstream behavior.

---

### 39. `was_duplicate` is dead/unused, making retry semantics unclear
- **File:** `app/flows/message_pipeline.py`
- **Function/line:** `store_message()`, `enqueue_background_jobs()`
- **Severity:** **minor**
- **Description:** The code path sets `was_collapsed` but never sets `was_duplicate=True`; yet downstream routing checks both. This suggests an incomplete idempotency design. Not immediately crashing, but it increases risk of incorrect retry behavior because the flags do not reflect a coherent contract.

---

### 40. Compose does not expose health dependency ordering; nginx may serve before app is available
- **File:** `docker-compose.yml`
- **Function/line:** service definitions
- **Severity:** **minor**
- **Description:** With independent startup this is acceptable in principle, but nginx has no explicit dependency on the app container and may start proxying before upstream is ready, causing avoidable early request failures. Given the project’s degraded-startup goals this is not fatal, but it reduces reliability during boot.

---

If you want, I can also provide:
1. a **prioritized top-10 fix list**, or  
2. a **patch plan grouped by file**.