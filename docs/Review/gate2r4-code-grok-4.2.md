**Code Review Findings**

**File:** `app/config.py`  
**Function/Line:** `load_config()` (~lines 45-85)  
**Severity:** major  
**Description:** Race condition on global config cache. The mtime fast-path check, file read, hash computation, and final assignment to `_config_cache`/`_config_mtime`/`_config_content_hash` are completely unsynchronized. Only the LLM/embeddings cache clear is locked. Multiple concurrent callers can both decide to reload, both parse the YAML, and overwrite the globals in undefined order. The comment about G5-04 only addresses the LLM cache clear, not the primary config cache. This violates the "hot-reloadable settings take effect immediately" guarantee and can cause inconsistent configuration views.

**File:** `app/prompt_loader.py`  
**Function/Line:** `load_prompt()` (entire function)  
**Severity:** major  
**Description:** Blocking synchronous file I/O (`os.stat` + `read_text`) inside an async codebase. Called from `context_assembly`, `memory_extraction`, `imperator_flow`, etc. The mtime cache mitigates repeated reads but the first load (and any change) blocks the event loop. Directly violates REQ-001 §7.6 and REQ-002 §5.1 ("No blocking I/O in async functions"). The G5-06 comment acknowledges the problem but does not solve it.

**File:** `app/flows/embed_pipeline.py`  
**Function/Line:** `enqueue_context_assembly()` (~lines 140-170)  
**Severity:** major  
**Description:** N+1 query anti-pattern. Fetches all windows, then for every window that has a `last_assembled_at` performs an individual `fetchval` to count tokens since that timestamp. The comment claims "G5-11: Batch the token-since-last-assembly queries" but the implemented code does not batch; it issues one query per window. With many context windows this becomes a serious performance and load issue on Postgres.

**File:** `app/flows/memory_admin_flow.py` (and `memory_extraction.py`, `memory_search_flow.py`)  
**Function/Line:** `add_memory()`, `list_memories()`, `delete_memory()`, `run_mem0_extraction()`, `search_memory_graph()` etc.  
**Severity:** major  
**Description:** Broad `except (..., Exception) as exc:` catching. Explicitly violates REQ-001 §4.5 and §6.7 ("Specific Exception Handling" and "No blanket except Exception"). This masks real bugs (e.g. programming errors, import issues, unexpected Neo4j driver errors) and turns them into generic warnings. The G5-18 comment justifies it for "Mem0/Neo4j failures" but the catch is too broad.

**File:** `app/flows/message_pipeline.py`  
**Function/Line:** `store_message()` (~lines 70-75, 95-100) and `enqueue_background_jobs()`  
**Severity:** major  
**Description:** Missing UUID validation on data coming from Redis jobs. `uuid.UUID(state["conversation_id"])` and similar calls can raise `ValueError` on corrupted/malformed job payloads. While the worker now has a broad handler (M-24), the lack of early validation means jobs that should be dead-lettered early instead crash the consumer loop. Same pattern appears in `process_*_job` functions in `arq_worker.py`.

**File:** `app/flows/imperator_flow.py`  
**Function/Line:** `run_imperator_agent()` (~lines 210-230) and `_store_imperator_messages()`  
**Severity:** major  
**Description:** Uses `MemorySaver` (process-local, unbounded) for checkpointing while claiming persistence via Postgres. The comment (M-13) acknowledges this but the design is fragile for any multi-instance or restart-heavy deployment. The Imperator's own conversation history is stored in Postgres, but the ReAct loop state (tool calls, intermediate reasoning) is not. Also, the advisory lock + two separate INSERTs inside one transaction can still produce gaps in `sequence_number` under high concurrency.

**File:** `app/imperator/state_manager.py`  
**Function/Line:** `_create_imperator_conversation()`  
**Severity:** minor (architectural)  
**Description:** Bypasses the `conv_create_conversation` StateGraph flow and writes directly to the DB. This duplicates logic, skips idempotency handling, title/flow_id/user_id population, and any future changes to conversation creation. The G5-17 comment justifies it for startup ordering, but this creates a maintenance hazard and violates the "all logic lives in StateGraphs" mandate.

**File:** `app/flows/search_flow.py`  
**Function/Line:** `_get_reranker()` and global `_reranker_cache`  
**Severity:** minor  
**Description:** Global in-memory cache of CrossEncoder models with no eviction. While the model name is normally static, any config-driven change or test that uses multiple models will leak memory. The `asyncio.Lock` protects the load but not the cache size.

**File:** `app/main.py`  
**Function/Line:** `lifespan()` and exception handlers (~lines 140-200)  
**Severity:** minor  
**Description:** The "known_exception_handler" only registers for `RuntimeError`, `ValueError`, `OSError`, `ConnectionError`. Several flows can raise `asyncpg.PostgresError`, `httpx.HTTPError`, `openai.APIError`, etc., which are not caught by the specific handlers and would fall through to FastAPI's default 500 behavior (which is not JSON-structured). The broad `known_exception_handler` decorator is incomplete.

**File:** `app/database.py`  
**Function/Line:** `check_neo4j_health()`  
**Severity:** minor  
**Description:** Hard-coded timeout of 3.0 seconds and no retry/backoff. In a degraded network this can cause the entire health check to flap. Also uses `httpx.AsyncClient` without connection pooling or reuse.

**Summary of Severity Distribution**
- **Blockers:** 0 (no outright crashes on normal paths, no credential exposure, no SQL injection)
- **Major:** 6 (race conditions, blocking I/O in async paths, N+1 queries, overly broad exception catching, missing input validation on job data)
- **Minor:** 4 (architectural inconsistencies, incomplete exception handler coverage, unbounded caches, minor resilience gaps)

The codebase is generally well-structured and the LangGraph flows are a strong architectural choice. The most pressing issues are the async correctness violations, the N+1 in the embedding pipeline, the broad `Exception` catches, and the unsynchronized config cache. These should be addressed before production use.