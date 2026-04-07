**Code Review Findings for Context Broker (R7)**

I performed a full pass over the flattened codebase, cross-referenced against the three requirements documents (REQ-context-broker.md, draft-REQ-001-mad-requirements.md, draft-REQ-002-pmad-requirements.md), the known accepted items (EX-CB-001 and the WONTFIX list), and the intentional design decisions. I ignored style, naming, and missing docstrings per instructions. Focus was strictly on correctness, reliability, security, performance, and runtime safety.

The codebase is unusually high-quality for its scope. It demonstrates careful attention to race conditions (Redis Lua locks, advisory locks, atomic check-and-set), async correctness (run_in_executor for blocking work, no blocking I/O in async paths), graceful degradation (Mem0/Neo4j, Redis unavailability), incremental assembly, and hot-reload safety. LangGraph usage follows the mandate (no logic in routes, proper conditional edges, no checkpointer). The State 4 MAD pattern is cleanly realized.

Only a small number of issues were found. None are blockers. All are minor.

---

**Finding 1**

**File:** `app/flows/build_types/passthrough.py`  
**Function/line:** `pt_finalize` (~line 65)  
**Severity:** minor  

**Description:** Catches `except (RuntimeError, OSError, Exception)`. This is not a Mem0 call site, so it is not covered by the accepted broad-exception exception (EX-CB-001). A truly unexpected error (e.g., a bug in the update statement or connection pool) would be masked. The function is small and the route already guarantees the lock is released via conditional edges (R6-M13), but the broad `Exception` violates the "specific exception handling" guidance in REQ-001 §6.5. Change to only the expected exceptions.

---

**Finding 2**

**File:** `app/main.py`  
**Function/line:** `known_exception_handler` (the decorator stack at the bottom)  
**Severity:** minor  

**Description:** The last-resort handler only registers for `RuntimeError`, `ValueError`, `OSError`, and `ConnectionError`. Several flows legitimately raise `asyncpg.PostgresError` (or subclasses) in non-Mem0 paths (e.g., migration failures, query errors in `conv_search_context_windows_node`, `search_conversations_db`). These will fall through to FastAPI's default handler instead of the structured JSON error response. Add `asyncpg.PostgresError` (and possibly `redis.exceptions.RedisError`) to the handler for consistency with the "known exception families" intent (CB-R3-01 / G5-22).

---

**Finding 3**

**File:** `app/flows/search_flow.py`  
**Function/line:** `hybrid_search_messages` (the `_build_extra_filters` helper and the two CTEs that reuse `extra_where`)  
**Severity:** minor  

**Description:** The dynamic WHERE fragment is correctly built with bind parameters (good, no injection), but the same `extra_where` string (with the same `$N` placeholders) is interpolated into both the `vector_ranked` and `bm25_ranked` CTEs. The parameter values are supplied only once in the `execute` call. While PostgreSQL accepts the same parameter number multiple times, this is fragile and makes the query harder to read/maintain. If the filter list ever diverges between the two CTEs, it will break. Extract the common filters once and build two separate clause/arg tuples, or use a single set of parameter indices that both CTEs can reference safely.

---

**Finding 4**

**File:** `app/flows/tool_dispatch.py`  
**Function/line:** `dispatch_tool` (the `conv_retrieve_context` branch, ~line 280)  
**Severity:** minor (performance)  

**Description:** Performs a separate `fetchrow` to discover the `build_type` for the window, then calls `get_retrieval_graph` and invokes the graph. The chosen graph's `load_window` / `ke_load_window` node performs the same lookup again. This is an N+1 query on every retrieval. The `RetrievalInput` contract intentionally only takes `context_window_id` and `config`, but the extra round-trip is unnecessary. Either cache the build_type → graph mapping more aggressively or pass the already-known build_type into the state so the load node can skip the query.

---

**Finding 5**

**File:** `app/flows/imperator_flow.py`  
**Function/line:** `store_and_end` (~line 280) and the call sites in `agent_node` / chat route  
**Severity:** minor  

**Description:** When `context_window_id` is not supplied (possible in some `imperator_chat` usages), the function silently skips persistence and returns only the response text. This is the only place in the entire codebase where a message is generated but not stored through the standard `conv_store_message` pipeline. While the WONTFIX list says the DB is the persistence layer, this path bypasses embedding, assembly, extraction, metrics, and collapse logic. Either require a context window for Imperator chats or explicitly document this as a "best-effort, non-persisted" mode with a warning log.

---

**Summary**

- **Blockers:** 0  
- **Majors:** 0  
- **Minors:** 5 (all easy to address)  

The architecture is sound, the resilience story is strong (degraded mode, lock TTLs, retry with backoff, stranded-job recovery, hot-reload config), and the LangGraph usage is correct. The only real weaknesses are a few overly broad exception catches outside Mem0 and some duplicated retrieval logic between `standard_tiered` and `knowledge_enriched` (which appears intentional per the build-type registry design).

The codebase is ready for production use with these minor cleanups. Excellent work.