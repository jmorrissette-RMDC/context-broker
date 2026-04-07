**Code Review Findings for Context Broker**

I performed a thorough review of the provided codebase, focusing on bugs, logic errors, race conditions, error handling, performance, security, architecture, and runtime failures. I excluded all items listed in "Known Accepted Items" (e.g., broad `except Exception` for Mem0, intentional design decisions such as parallel extraction/embedding, DB-driven workers, advisory locks replacing Redis, retrieval as structured messages array, etc.).

Below are the findings, grouped by severity. Only substantive issues are reported—no style, naming, or docstring issues.

### Blocker

**None identified.** The system is generally robust, with good use of advisory locks, transactions in most paths, and graceful degradation for optional components.

### Major

**File:** `app/workers/db_worker.py`  
**Function/line:** `_extraction_worker` (~lines 140-170, the `await _get_extraction_flow().ainvoke(...)` call)  
**Severity:** major  
**Description:** Race condition in extraction locking. The worker acquires a Postgres advisory lock (`pg_try_advisory_lock`) on one connection from the pool, then invokes the extraction StateGraph. The graph's `acquire_extraction_lock` node performs its own `pg_try_advisory_lock` (on a potentially different connection). Advisory locks are connection-specific, so the worker's lock is not visible to the flow. This allows multiple workers to process the same conversation concurrently, leading to duplicate extraction, inconsistent `memory_extracted` flags, and potential Mem0/Neo4j corruption. The initial state passes `lock_acquired=True` (with comment "We handle locking here"), but the node ignores it and always attempts to lock again. (The same pattern exists in the assembly path but is mitigated by the assembly graph's own locking.)

**File:** `app/flows/tool_dispatch.py`  
**Function/line:** `_dispatch_tool_inner`, case for `tool_name == "conv_delete_conversation"` (~lines 280-300)  
**Severity:** major  
**Description:** Non-atomic multi-statement delete. The operation issues four separate `DELETE` statements (summaries, windows, messages, conversation) without a transaction. A failure after deleting windows/summaries but before messages (e.g., transient error, constraint violation, or connection drop) leaves the database in an inconsistent state (orphaned messages, missing windows for a still-existing conversation). All deletes must be wrapped in a single `async with conn.transaction()` (or use `ON DELETE CASCADE` with careful ordering). This violates the "all logic in StateGraphs" principle as well, since it bypasses a flow.

**File:** `app/workers/scheduler.py`  
**Function/line:** `scheduler_worker` (the `while True` loop, `is_due` logic, and `last_fired` dict, ~lines 80-130)  
**Severity:** major  
**Description:** Scheduler race condition with multiple workers. `last_fired` is an in-memory dict local to each worker process. With multiple background workers (default in the compose setup), they do not share this state. For cron schedules, all workers can simultaneously see a schedule as due in the same minute and all fire it (the 55-second guard is also per-process). Interval schedules have the same issue. There is no DB-based "last fired" check or advisory lock in the `SELECT` from `schedules`. The `schedule_history` insert happens *after* the decision to fire, so it does not prevent duplicates. This can cause duplicate Imperator invocations or other target messages.

### Minor

**File:** `app/config.py` (and similar in `prompt_loader.py`, `async_load_te_config`)  
**Function/line:** `async_load_config`, `async_load_te_config`, `async_load_prompt` (the `os.stat(...)` calls)  
**Severity:** minor  
**Description:** Synchronous file I/O (`os.stat`) in async code paths. While `os.stat` on local files is very fast, it still blocks the event loop. The code already uses `run_in_executor` for the actual read when mtime changes (good), but the fast-path mtime checks are synchronous. This is a minor performance issue in high-concurrency scenarios. (The comment acknowledges the "fast-path" intent, but the pattern should be consistent with `async_load_prompt`.)

**File:** `app/flows/tool_dispatch.py`  
**Function/line:** `hybrid_search_messages` and `search_conversations_db` (~lines 480-580 and 380-420)  
**Severity:** minor  
**Description:** Dynamic SQL built with f-strings for the `WHERE` clause (conditions are hardcoded, values are parameterized). While safe in the current implementation, this pattern is fragile—if a future change adds user-controlled column names or operators, it becomes an SQL injection risk. Use `asyncpg` prepared statements or a query builder for all dynamic SQL.

**File:** `packages/context-broker-ae/src/context_broker_ae/migrations.py` (the migration file itself)  
**Function/line:** `_migration_012` (the index rename block, ~lines 280-290)  
**Severity:** minor  
**Description:** The index `idx_messages_conversation_sender` is renamed to `idx_messages_conversation_sender_new`. The new name is non-standard and suggests a temporary workaround. Later migrations or manual DBA work may assume the conventional name. This is a one-time migration issue but creates unnecessary inconsistency in the schema.

**File:** `app/workers/db_worker.py`  
**Function/line:** `_embedding_worker` (~lines 70-100, the per-row `UPDATE` loop after `aembed_documents`)  
**Severity:** minor  
**Description:** N+1 `UPDATE` statements after batch embedding. A batch of 50 messages results in 50 individual `UPDATE ... SET embedding = ... WHERE id = ...` statements. This can be replaced with a single `UPDATE ... FROM (VALUES ...)` or `unnest` for a bulk update, reducing round-trips and lock contention. (The embedding call itself is correctly batched.)

**File:** `packages/context-broker-te/src/context_broker_te/imperator_flow.py`  
**Function/line:** `store_user_message` (~lines 280-300)  
**Severity:** minor  
**Description:** Logic for extracting the "user message" content iterates `for msg in state["messages"]` and takes the *first* `HumanMessage` encountered. In the current design each `imperator_chat` invocation starts with only the new user message, so it works. However, if history is ever loaded into the graph state in the future, this will incorrectly use the oldest user message instead of the current one. Should explicitly take the last `HumanMessage` or pass the current user content separately.

**File:** `app/routes/mcp.py`  
**Function/line:** `_get_tool_list` (the `build_type_names` logic, ~lines 480-490)  
**Severity:** minor  
**Description:** Calls `load_te_config()` (which can raise) inside a function that is used to build the MCP tool list returned to clients. The `try/except` only catches `FileNotFoundError`/`RuntimeError`/`OSError`/`ValueError` and falls back to hardcoded defaults. A malformed `te.yml` (e.g., bad YAML) will cause the tool list to be incomplete and the error is swallowed. The MCP `/tools/list` endpoint should not fail silently on config errors.

**File:** `app/config.py`  
**Function/line:** `get_build_type_config` (~lines 280-300)  
**Severity:** minor  
**Description:** Sums tier percentages but only checks keys that exist in the dict. If a build type omits one of the `tier*_pct` keys (or uses `null`/`None`), the sum can be incorrect and the validation `if total_pct > 1.0` can pass erroneously. Should explicitly default missing percentages to 0.0.

**File:** `app/flows/build_type_registry.py`  
**Function/line:** `get_assembly_graph` / `get_retrieval_graph` (the cache check under lock)  
**Severity:** minor  
**Description:** Uses a single lock for both assembly and retrieval graph compilation. Since compilation can be expensive (especially for knowledge-enriched with LLM calls in the graph), a long compilation of one build type blocks compilation of others. Separate locks or an async lock would be better. (Current usage is infrequent, so minor.)

### Summary of Findings

- **3 major issues** (extraction locking race, non-atomic delete, scheduler race with multiple workers). These are the most important to address.
- **9 minor issues** (mostly performance, maintainability, and edge-case robustness).
- No blockers, no credential exposure, no SQL injection, and no obvious runtime import/signature errors.
- The codebase is generally well-structured, with good use of StateGraphs, advisory locks, and eventual consistency. The separation of AE/TE and the configuration-driven build types are cleanly implemented.

The major findings are all related to concurrency across multiple workers/background tasks. Adding proper DB-based coordination (e.g., a `last_fired_at` column with a `WHERE last_fired_at < now() - interval` guard, or using the existing advisory lock pattern for the scheduler) would resolve them.

Let me know if you would like me to expand on any finding, suggest fixes, or review a specific module in more depth.