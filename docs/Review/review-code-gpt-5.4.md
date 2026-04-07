Here are the review findings, focused on correctness/reliability/runtime issues only.

---

### 1) Startup config validation checks the wrong config file
- **File:** `app/main.py`
- **Function/line:** `lifespan()` — startup validation block around `for bt_name in config.get("build_types", {})` and `embeddings_config = config.get("embeddings", {})`
- **Severity:** major
- **Description:** `lifespan()` loads `config = load_config()` (AE-only), but build types and some cognitive settings are described elsewhere in the code as TE/hot-reload config or merged config. Startup validation then checks `build_types` and `embeddings` only in AE config. In deployments where these live in TE/merged config, startup validation silently skips build type validation or fails incorrectly on missing embedding dims. This causes inconsistent behavior between startup and runtime, because runtime paths use `async_load_config()`/merged config.

---

### 2) Background worker never starts after Postgres recovers
- **File:** `app/main.py`
- **Function/line:** `_postgres_retry_loop()` and `lifespan()`
- **Severity:** blocker
- **Description:** If Postgres is unavailable at startup, `lifespan()` does not start `start_background_worker()`. `_postgres_retry_loop()` later reconnects and runs migrations, but never starts the background worker. The service can come back “healthy” while embeddings, extraction, assembly, log embedding, and scheduler never run for the lifetime of the process.

---

### 3) `asyncpg.Pool` is used as if it supports direct `fetch/fetchrow/execute`
- **File:** `app/flows/install_stategraph.py`
- **Function/line:** `_record_package_install()`
- **Severity:** blocker
- **Description:** This calls `await pool.execute(...)` on an `asyncpg.Pool`. Pool objects do not expose query methods directly; queries must run on an acquired connection (`async with pool.acquire() as conn: await conn.execute(...)`). This will fail at runtime the first time package installation recording is attempted.

---

### 4) Same `asyncpg.Pool` misuse across many tool dispatch paths
- **File:** `app/flows/tool_dispatch.py`
- **Function/line:** multiple branches: `conv_delete_conversation`, `conv_list_conversations`, `conv_retrieve_context`, `query_logs`, `search_logs`
- **Severity:** blocker
- **Description:** These branches call `pool.execute`, `pool.fetch`, `pool.fetchrow` directly on the pool returned by `get_pg_pool()`. `asyncpg.Pool` does not provide those methods. These tool paths will fail immediately at runtime when invoked.

---

### 5) Same `asyncpg.Pool` misuse in Imperator state manager
- **File:** `app/imperator/state_manager.py`
- **Function/line:** `_conversation_exists()`, `_create_imperator_conversation()`
- **Severity:** blocker
- **Description:** Both methods call `pool.fetchrow(...)` / `pool.execute(...)` directly on the pool. This breaks Imperator initialization/resume at startup, likely causing the Imperator state manager to fail whenever these methods execute.

---

### 6) Same `asyncpg.Pool` misuse in conversation ops flows
- **File:** `packages/context-broker-ae/src/context_broker_ae/conversation_ops_flow.py`
- **Function/line:** `create_conversation_node()`, `create_context_window_node()`, `load_conversation_and_messages()`, `search_context_windows_node()`, `ensure_conversation_node()`, `find_or_create_window_node()`
- **Severity:** blocker
- **Description:** These functions consistently call `fetchrow`, `fetch`, `fetchval`, `execute` on the pool object rather than an acquired connection. Core flows like conversation creation, history retrieval, context window creation, and `get_context` will fail at runtime.

---

### 7) Same `asyncpg.Pool` misuse in embedding pipeline
- **File:** `packages/context-broker-ae/src/context_broker_ae/embed_pipeline.py`
- **Function/line:** `fetch_message()`, `generate_embedding()`, `store_embedding()`
- **Severity:** blocker
- **Description:** These functions use the pool directly for DB operations. The embedding flow cannot function as written.

---

### 8) Same `asyncpg.Pool` misuse in message pipeline
- **File:** `packages/context-broker-ae/src/context_broker_ae/message_pipeline.py`
- **Function/line:** `store_message()`
- **Severity:** blocker
- **Description:** Early checks use `pool.fetchrow(...)` directly before later correctly using `async with pool.acquire() as conn`. Since the initial pool calls fail, the message pipeline cannot store messages at runtime.

---

### 9) Same `asyncpg.Pool` misuse in memory extraction flow
- **File:** `packages/context-broker-ae/src/context_broker_ae/memory_extraction.py`
- **Function/line:** `acquire_extraction_lock()`, `fetch_unextracted_messages()`, `mark_messages_extracted()`, `release_extraction_lock()`
- **Severity:** blocker
- **Description:** The flow treats the pool as a connection. This breaks extraction end-to-end.

---

### 10) Same `asyncpg.Pool` misuse in search flow
- **File:** `packages/context-broker-ae/src/context_broker_ae/search_flow.py`
- **Function/line:** `search_conversations_db()`, `hybrid_search_messages()`
- **Severity:** blocker
- **Description:** Search flows call `pool.fetch(...)` directly. Both conversation search and message search will fail at runtime.

---

### 11) Same `asyncpg.Pool` misuse in passthrough build type
- **File:** `packages/context-broker-ae/src/context_broker_ae/build_types/passthrough.py`
- **Function/line:** `pt_acquire_lock()`, `pt_finalize()`, `pt_release_lock()`, `pt_load_window()`, `pt_load_recent()`
- **Severity:** blocker
- **Description:** Assembly and retrieval both use pool methods directly. The passthrough build type is non-functional as written.

---

### 12) Same `asyncpg.Pool` misuse in standard-tiered build type
- **File:** `packages/context-broker-ae/src/context_broker_ae/build_types/standard_tiered.py`
- **Function/line:** multiple: `acquire_assembly_lock()`, `load_window_config()`, `load_messages()`, `calculate_tier_boundaries()`, `summarize_message_chunks()`, `finalize_assembly()`, `release_assembly_lock()`, `ret_load_window()`, `ret_wait_for_assembly()`, `ret_load_summaries()`, `ret_load_recent_messages()`
- **Severity:** blocker
- **Description:** Core assembly/retrieval logic repeatedly treats the pool as a connection. The main build type’s assembly and retrieval paths will fail throughout runtime.

---

### 13) Same `asyncpg.Pool` misuse in knowledge-enriched build type
- **File:** `packages/context-broker-ae/src/context_broker_ae/build_types/knowledge_enriched.py`
- **Function/line:** multiple: `ke_load_window()`, `ke_wait_for_assembly()`, `ke_load_summaries()`, `ke_load_recent_messages()`, `ke_inject_semantic_retrieval()`
- **Severity:** blocker
- **Description:** This build type has the same pool/connection bug, breaking retrieval and semantic injection.

---

### 14) Same `asyncpg.Pool` misuse in TE operational/admin/diagnostic tools
- **File:** `packages/context-broker-te/src/context_broker_te/tools/admin.py`, `tools/diagnostic.py`, `tools/operational.py`, `tools/scheduling.py`
- **Function/line:** multiple functions using `pool.fetch*` / `pool.execute`
- **Severity:** blocker
- **Description:** These tools also use the pool incorrectly. Large portions of the Imperator’s admin and diagnostic tooling will fail at runtime.

---

### 15) Worker loops have same `asyncpg.Pool` misuse
- **File:** `app/workers/db_worker.py`
- **Function/line:** `_embedding_worker()`, `_extraction_worker()`, `_check_assembly_needed()`, `_assembly_worker()`, `_log_embedding_worker()`
- **Severity:** blocker
- **Description:** All worker loops call `fetch/fetchval/execute` directly on the pool. Even if the worker is started, none of its DB operations will work.

---

### 16) Scheduler worker has same `asyncpg.Pool` misuse
- **File:** `app/workers/scheduler.py`
- **Function/line:** `_fire_schedule()`, `scheduler_worker()`
- **Severity:** blocker
- **Description:** Schedule insert/update/select operations are performed on the pool instead of a connection, so scheduling is broken.

---

### 17) Wrong entry point group in AE package docstring can hide packaging/config mistakes
- **File:** `packages/context-broker-ae/src/context_broker_ae/register.py`
- **Function/line:** module docstring
- **Severity:** minor
- **Description:** The docstring says discovery uses `entry_points(group="context-broker.ae")`, but the actual code scans `context_broker.ae`. If packaging metadata followed the docstring, the AE package would not be discovered. While this is a comment mismatch, here it directly concerns a runtime registration contract and can cause package discovery failure.

---

### 18) Wrong config section used for token auto-resolution
- **File:** `app/token_budget.py`
- **Function/line:** `_query_provider_context_length()`
- **Severity:** major
- **Description:** Auto-resolution reads from `config.get("llm", {})`, but the rest of the system uses role-specific sections like `imperator`, `summarization`, `extraction`, with `llm` only as a legacy fallback. For current configs, auto-resolution will often miss the actual model/base URL and incorrectly fall back to `fallback_tokens`, causing mis-sized windows.

---

### 19) `verbose_log_auto()` ignores TE config
- **File:** `app/config.py`
- **Function/line:** `verbose_log_auto()`
- **Severity:** minor
- **Description:** It calls `load_config()` (AE-only) rather than merged config, while comments say it is for flows that do not carry config. If `verbose_logging` lives in TE/hot-reload config, this helper silently ignores it. This produces inconsistent observability behavior between flows carrying merged config and flows using this convenience path.

---

### 20) Health route can 500 if AE package is absent
- **File:** `app/routes/health.py`
- **Function/line:** `health_check()`, `_get_health_flow()`
- **Severity:** major
- **Description:** `_get_health_flow()` raises `RuntimeError` when the AE package is not loaded, and `health_check()` does not catch it. Startup explicitly allows “no AE packages found” in degraded mode, but `/health` then fails with 500 instead of returning a structured degraded/unavailable response. This undermines monitoring and startup diagnostics.

---

### 21) Metrics route can 500 if AE package is absent
- **File:** `app/routes/metrics.py`
- **Function/line:** `get_metrics()`, `_get_metrics_flow()`
- **Severity:** major
- **Description:** Same issue as health: absence of AE package is allowed at startup, but `/metrics` raises at request time instead of returning a controlled error response.

---

### 22) Streaming chat does not check HTTP status before parsing SSE body
- **File:** `ui/mad_client.py`
- **Function/line:** `chat_stream()`
- **Severity:** major
- **Description:** The client opens the stream and iterates lines without calling `resp.raise_for_status()`. On 4xx/5xx responses (including JSON error bodies), the UI will silently consume nonsense or produce partial/empty output instead of surfacing a clear error. This makes failures hard to diagnose.

---

### 23) UI startup hard-fails on malformed/missing config
- **File:** `ui/app.py`
- **Function/line:** `load_config()`, module-level `CONFIG = load_config()`
- **Severity:** major
- **Description:** Config is loaded at import time with no error handling. Missing file, unreadable file, or invalid YAML prevents the whole UI process from starting. For a separate optional UI container, this is brittle and should degrade with a clear startup error or fallback config.

---

### 24) MCP sessionless/sessionful access races on `_sessions` reads without lock
- **File:** `app/routes/mcp.py`
- **Function/line:** `mcp_tool_call()`
- **Severity:** minor
- **Description:** The code checks `if session_id not in _sessions` outside `_session_lock`, then later rechecks inside the lock. The second check avoids corruption, so this is not catastrophic, but the unlocked first read can produce inconsistent transient behavior under concurrent session teardown/eviction and should be consolidated under the lock.

---

### 25) Python built-in `hash()` used for advisory lock IDs is process-randomized
- **File:** `app/workers/db_worker.py`, `packages/context-broker-ae/src/context_broker_ae/memory_extraction.py`, `build_types/passthrough.py`, `build_types/standard_tiered.py`, `build_types/knowledge_enriched.py`
- **Function/line:** lock-id generation via `hash(...) & 0x7FFFFFFFFFFFFFFF`
- **Severity:** major
- **Description:** Python’s `hash()` is salted per process. Different processes/containers will compute different advisory lock IDs for the same conversation/window string. That defeats cross-process coordination, which is the whole point of PostgreSQL advisory locks in a multi-worker deployment. Use a stable hash (e.g. SHA-256 truncated to 64 bits) instead.

---

### 26) `config_write()` performs blocking file I/O in async context
- **File:** `packages/context-broker-te/src/context_broker_te/tools/admin.py`
- **Function/line:** `config_write()`
- **Severity:** minor
- **Description:** This async tool does direct file reads/writes and YAML parsing synchronously on the event loop. Admin operations may be infrequent, but this still blocks the server under load and is inconsistent with the rest of the codebase’s use of executors for blocking I/O.

---

### 27) `db_query` is vulnerable to arbitrary read access beyond intended safety boundary
- **File:** `packages/context-broker-te/src/context_broker_te/tools/admin.py`
- **Function/line:** `db_query(sql: str)`
- **Severity:** major
- **Description:** Although the transaction is `READ ONLY`, the tool still accepts arbitrary SQL text from the LLM/user. This permits unrestricted reads from all tables, system catalogs, potentially expensive queries, and other read-only statements that may still be operationally unsafe. Since admin tools are powerful by design this may be partially intentional, but from a security/reliability perspective it is still a significant risk surface and lacks any allowlisting or query-shape restrictions.

---

### 28) `migrate_embeddings()` claims to alter vector dimensions but does not
- **File:** `packages/context-broker-te/src/context_broker_te/tools/admin.py`
- **Function/line:** `migrate_embeddings()`
- **Severity:** major
- **Description:** The dry-run/output says it will “ALTER vector columns to vector(new_dims)”, but the implementation only drops indexes, nulls embeddings, and updates config. It never alters the column type/dimension. This creates a mismatch between config/model output dimensions and stored column constraints/indexability, likely causing later embedding writes or index recreation to fail.

---

### 29) `migrate_embeddings()` references a table that does not exist
- **File:** `packages/context-broker-te/src/context_broker_te/tools/admin.py`
- **Function/line:** `extract_domain_knowledge()`
- **Severity:** blocker
- **Description:** The query references `FROM domain_memories`, but no such table is created anywhere in migrations/schema. The domain Mem0 config uses collection name `domain_memories`, but that is not guaranteed to exist in Postgres unless Mem0 creates it with that exact schema, and this code treats it like an application-managed relational table. This is likely to fail at runtime with `UndefinedTableError`.

---

### 30) Log shipper stores nested JSON as a string, degrading JSONB usefulness
- **File:** `log_shipper/shipper.py`
- **Function/line:** `tail_container()`
- **Severity:** minor
- **Description:** When a message is JSON, the code sets `data = message` (a string) instead of the parsed object; for non-JSON it stores `json.dumps({"raw": message})`. The DB column is JSONB, but this path often inserts JSON strings rather than structured objects, so downstream queries like `data->>'level'` may fail or produce inconsistent results. Several tools assume `row["data"]` is a dict.

---

### 31) Log shipper broad exception usage around critical setup masks actionable failures
- **File:** `log_shipper/shipper.py`
- **Function/line:** `setup()`, various loops
- **Severity:** minor
- **Description:** Critical setup paths use bare `except Exception` and `sys.exit(1)` / generic logging. This isn’t a style issue here; it reduces diagnosability and can mask specific Docker/Postgres API failures. Since this component is infrastructure-facing, narrower handling would materially improve reliability debugging.

---

### 32) OpenAI chat route aliases `conversation_id` to `context_window_id`, but Imperator treats it as conversation ID
- **File:** `app/routes/chat.py`, `packages/context-broker-te/src/context_broker_te/imperator_flow.py`
- **Function/line:** `chat_completions()` context ID resolution; `agent_node()`, `store_user_message()`, `store_assistant_message()`
- **Severity:** major
- **Description:** The route accepts `x-context-window-id` or legacy `x-conversation-id` / `conversation_id`, stores the value into `initial_state["context_window_id"]`, and passes it to the Imperator. But inside the Imperator, this value is treated as a conversation ID when calling `get_context` and `store_message`. If a real context window ID is supplied, downstream calls pass it as `conversation_id`, causing lookup/storage failures or cross-identifier confusion.

---

### 33) Imperator history loader expects a context window ID, but default state manager now returns conversation ID
- **File:** `packages/context-broker-te/src/context_broker_te/imperator_flow.py`, `app/imperator/state_manager.py`
- **Function/line:** `_load_conversation_history()`, `ImperatorStateManager.get_context_window_id()`
- **Severity:** major
- **Description:** `get_context_window_id()` now intentionally returns the conversation ID for compatibility, but `_load_conversation_history()` queries `context_windows WHERE id = $1`, assuming the input is a context window ID. This helper will fail to load history whenever called with the manager’s returned value. Even if currently unused in the main path, it is a latent runtime bug and indicates identifier semantics are inconsistent.

---

### 34) `create_context_window_node()` conflicts with migrated uniqueness semantics
- **File:** `packages/context-broker-ae/src/context_broker_ae/conversation_ops_flow.py`
- **Function/line:** `create_context_window_node()`
- **Severity:** major
- **Description:** Migration 013 changes window identity to `(conversation_id, build_type, max_token_budget)`, but this flow still inserts with `ON CONFLICT (conversation_id, participant_id, build_type)` and looks up existing rows using the old identity. After migration 013, this flow no longer matches schema semantics and can create duplicates, fail conflict handling, or return the wrong existing window.

---

### 35) `conv_search_context_windows` still exposes/filters `participant_id` despite D-03 identity change
- **File:** `app/models.py`, `packages/context-broker-ae/src/context_broker_ae/conversation_ops_flow.py`
- **Function/line:** `SearchContextWindowsInput`, `search_context_windows_node()`
- **Severity:** minor
- **Description:** The code still treats `participant_id` as a search discriminator even after context-window identity moved away from participant. This may be partly for backward compatibility, but it creates architectural inconsistency and can confuse callers because results are now fundamentally shared by conversation/build_type/budget.

---

### 36) `load_merged_config()` shallow merge can silently clobber nested sections
- **File:** `app/config.py`
- **Function/line:** `load_merged_config()`, `async_load_config()`
- **Severity:** major
- **Description:** Both merged-config paths use `{**ae_config, **te_config}`, which is a shallow merge. If both files contain the same top-level key with nested dicts, the TE dict replaces the AE dict entirely. This can silently discard nested settings like `tuning`, `workers`, or provider sections and produce hard-to-debug runtime behavior.

---

### 37) `scan()` does not invalidate cached flow singletons after re-registering packages
- **File:** `app/stategraph_registry.py`, `app/routes/health.py`, `app/routes/metrics.py`, `app/workers/db_worker.py`
- **Function/line:** `scan()`
- **Severity:** major
- **Description:** `scan()` clears the registry but not module-level cached compiled flows outside the registry (`_health_flow`, `_metrics_flow`, worker `_embed_flow`, `_extraction_flow`, etc.). After runtime package install/update, some call sites may continue using stale compiled graphs from old code while others use new registry state. This causes version skew inside one process.

---

### 38) Prompt loader cache is unsynchronized
- **File:** `app/prompt_loader.py`
- **Function/line:** `_prompt_cache` access in `load_prompt()` / `async_load_prompt()`
- **Severity:** minor
- **Description:** Cache mutation is unsynchronized across concurrent requests. In practice this likely causes duplicate reads rather than corruption, but it is still a race on shared mutable state and can produce inconsistent hot-reload visibility.

---

### 39) Logging setup never updates existing handlers on reload
- **File:** `app/logging_setup.py`
- **Function/line:** `setup_logging()`
- **Severity:** minor
- **Description:** The duplicate-handler guard means if logging was already configured by another component/process model, this function will skip adding the JSON handler entirely. That can leave the app emitting non-JSON logs despite the rest of the code assuming structured logging. This is mostly an operational correctness issue.

---

### 40) `check_neo4j_health()` ignores auth-disabled behavior edge cases
- **File:** `app/database.py`
- **Function/line:** `check_neo4j_health()`
- **Severity:** minor
- **Description:** It sends Basic auth whenever `NEO4J_PASSWORD` is set, but deployment intentionally uses `NEO4J_AUTH=none`. If env values drift, health can report false negatives unrelated to service health. Since this health drives degraded status, configuration ambiguity here matters operationally.

---

If you want, I can also produce:
1. a **prioritized fix plan** (blockers first),
2. a **deduplicated root-cause summary** (e.g. “asyncpg pool misuse appears in N files”), or
3. a **patch sketch** for the top issues.