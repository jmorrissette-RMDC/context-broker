**Code Review Findings**

**File:** `app/config.py`  
**Function/Line:** `async_load_config()` (lines 140-160) and `_apply_config()` (lines 60-75)  
**Severity:** major

The mtime fast-path check and decision to reload (`if _config_cache is not None and current_mtime == _config_mtime`) happens *outside* `_cache_lock`. Multiple concurrent async callers can all pass the check, all call `run_in_executor(_read_and_parse_config)`, and all call `_apply_config`. While the lock inside `_apply_config` serializes the final update, the redundant file reads, YAML parsing, and cache-clearing (`_llm_cache.clear()`, `_embeddings_cache.clear()`) still occur. This defeats the purpose of the mtime cache under load and can cause thundering-herd cache invalidation.

**File:** `app/config.py`  
**Function/Line:** `verbose_log_auto()` (lines 110-118) and `load_config()` (lines 85-105)  
**Severity:** major

`verbose_log_auto` (used by flows that do not carry `config` in state) calls the *synchronous* `load_config()`, which does `os.stat()` + potential file read on every call. This is blocking I/O in the async event loop. The comment in `load_config` acknowledges the blocking nature but says it is "rare in production." When `verbose_logging` is enabled, this becomes a hot path and violates async correctness (REQ-001 §5.1 and §7.6).

**File:** `app/flows/build_types/knowledge_enriched.py` (and duplicated in `memory_admin_flow.py`, `memory_search_flow.py`, `memory_extraction.py`, `imperator_flow.py`)  
**Function/Line:** `ke_inject_knowledge_graph()` (lines 280-300), `add_memory()`, `list_memories()`, `search_memory_graph()`, etc.  
**Severity:** blocker

Multiple Mem0/Neo4j code paths use extremely broad exception handling:

```python
except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:
```

This is explicitly labeled `EX-CB-001: broad catch for Mem0`. It violates REQ-001 §4.5 ("Specific Exception Handling") and §7.1 (graceful degradation must not mask real bugs). It can hide programming errors, authentication failures, or schema mismatches, turning them into silent degradation. The same pattern appears in 5+ files.

**File:** `app/flows/search_flow.py`  
**Function/Line:** `search_conversations_db()` and `hybrid_search_messages()` (the `_build_extra_filters` + f-string construction)  
**Severity:** major

Dynamic SQL is built with f-strings for the `WHERE` clause, even though values are parameterized. The comment claims safety because column names are hardcoded. While currently true, this is a maintenance trap: any future change to the filter builder (or copy-paste into a new query) risks SQL injection. This violates the spirit of REQ-001 §7.7 (input validation) and §3.2.

**File:** `app/flows/imperator_flow.py`  
**Function/Line:** `agent_node()` (lines 280-300) and the message truncation logic  
**Severity:** major

The truncation logic for `imperator_max_react_messages` walks backwards to avoid splitting a `ToolMessage` sequence. It is complex, has no tests shown, and runs on every agent turn. A bug here (off-by-one, incorrect `ToolMessage` detection, or mutation of the shared `messages` list) would corrupt the ReAct loop. The comment acknowledges this is fragile ("M9").

**File:** `app/flows/embed_pipeline.py`  
**Function/Line:** `enqueue_context_assembly()` (lines 170-220) and the batch token query  
**Severity:** minor (but architectural)

The batch token query for `tokens_since` uses a `VALUES` list with `unnest` on two arrays. This is clever but brittle: the arrays must be the same length and in the same order. A future change to the list of windows could silently break the correlation. The comment (R5-M10) shows awareness, but this is a classic source of subtle production bugs.

**File:** `app/database.py`  
**Function/Line:** `check_neo4j_health()` (lines 110-130)  
**Severity:** minor

Uses the HTTP endpoint (`/`) for health checking instead of the Bolt protocol that Mem0 actually uses. The comment (G5-13) justifies it, but this means the health check can pass while the actual data path (used by `mem0_client`) is broken. This is a known anti-pattern for health checks.

**File:** `app/main.py`  
**Function/Line:** lifespan + `_postgres_retry_loop()` and `_redis_retry_loop()` (lines 60-120)  
**Severity:** minor

The retry loops use a fixed sleep interval and do not implement exponential backoff or jitter. Under repeated transient failures (e.g., Postgres flapping), this creates thundering herd load on the dependency. Also, `imperator_initialized` is set on the app state but never reset on failure, so a later successful retry may be skipped.

**File:** `app/flows/tool_dispatch.py`  
**Function/Line:** `dispatch_tool()` for `conv_retrieve_context` (lines 280-310)  
**Severity:** minor

It does a direct SQL lookup for the build type *inside* the tool handler, then calls `get_retrieval_graph()`. This bypasses the normal flow contract (`RetrievalInput`) for the lookup step and mixes concerns. If the window lookup fails, the error is raised as a generic `ValueError` instead of using the flow's error field.

---

### Summary of Severity Distribution

- **Blockers:** 2 (broad `Exception` catching, critical for reliability)
- **Major:** 5 (blocking I/O in async paths, race conditions in config, fragile SQL construction, complex untested truncation logic)
- **Minor:** 4 (health check mismatch, retry loop design, minor architectural mixing of concerns)

The most serious issues are the **broad exception handling** (which directly violates the project's own documented requirements) and the **sync I/O in hot async paths** (`verbose_log_auto` + config). These are likely to cause either silent failures or event-loop blocking under load.

The codebase is otherwise quite careful (many comments reference specific requirements like R5-M11, G5-11, CB-R3-02, etc.), but the exception handling and async discipline are the weakest areas.