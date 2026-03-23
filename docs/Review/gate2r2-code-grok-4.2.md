**Code Review Findings for Context Broker**

**File:** `app/main.py` (lifespan context manager, ~lines 70-90)  
**Severity:** blocker  
**Description:** If `init_postgres()` fails (sets `postgres_available=False` and schedules retry), the code continues to `await imperator_manager.initialize()`. That method calls `get_pg_pool()` (which raises `RuntimeError`) and `_conversation_exists()`. The exception is unhandled, crashing the entire lifespan and preventing the app from starting in degraded mode. This violates the documented graceful-degradation and independent-startup requirements (REQ-001 §7.2, draft-REQ-002 §7.2).

**File:** `app/config.py` (`get_chat_model`, `get_embeddings_model`, lines ~140-170)  
**Severity:** major  
**Description:** Global module-level caches (`_llm_cache`, `_embeddings_cache`) are keyed only on `(base_url, model)`. `load_config()` is documented as hot-reloadable for inference providers, yet the caches are never invalidated. Changing `llm.model` or `embeddings.model` in `config.yml` has no effect until container restart, breaking the hot-reload guarantee stated in the module docstring and REQ-001 §8.1 / §5.2.

**File:** `app/flows/context_assembly.py` (`load_window_config`, `load_messages`, `calculate_tier_boundaries`, etc.) and `app/flows/embed_pipeline.py` (`fetch_message`, `generate_embedding`, `enqueue_context_assembly`)  
**Severity:** major  
**Description:** Multiple nodes call `uuid.UUID(...)` on values originating from Redis job JSON with no `try/except`. A malformed or corrupted job (possible after a crash, bad enqueue, or manual Redis edit) causes an unhandled `ValueError` that crashes the ARQ worker process. Background jobs have no input validation layer (unlike MCP dispatch which uses Pydantic). Violates REQ-001 §7.4 (fail-fast with clear error) and §3.2 (input validation).

**File:** `app/workers/arq_worker.py` (`process_embedding_job`, `process_assembly_job`, `process_extraction_job`)  
**Severity:** major  
**Description:** Each processor immediately does `config = load_config()` (overwriting the `config` parameter passed from `_consume_queue`). The passed config is the one captured at worker startup; the functions ignore it. This is confusing, defeats the intent of passing config, and makes hot-reload behavior non-obvious. Also, the BLMOVE + shared `:processing` queue design is unsafe if the langgraph container is ever scaled to >1 process (multiple consumers contending for the same processing queue).

**File:** `app/flows/imperator_flow.py` (module level, `_checkpointer = MemorySaver()`) and `build_imperator_flow`  
**Severity:** major  
**Description:** `MemorySaver` is a process-local in-memory checkpoint. The module-level singleton is shared across all Imperator turns and across all uvicorn workers (if `--workers > 1`). Combined with the comment that "persistent state is handled by imperator_state.json", this creates two conflicting persistence models and potential state leakage between unrelated conversations. The checkpoint is also never cleared, leading to unbounded memory growth for long-lived sessions.

**File:** `app/database.py` (`check_neo4j_health`) and `app/flows/health_flow.py`  
**Severity:** minor  
**Description:** `check_neo4j_health` takes a `config` parameter that is never used; it only reads `os.environ`. The function also re-imports `httpx` inside the function even though it is imported at module level. This is dead code and inconsistent with the rest of the health-check pattern.

**File:** `app/flows/retrieval_flow.py` (`inject_semantic_retrieval`, ~line 280)  
**Severity:** minor  
**Description:** When `recent_messages` is empty, `tier3_min_seq` is set to the magic number `999999999`. The subsequent vector query uses `sequence_number < 999999999`, which works but is fragile and undocumented. A clearer sentinel (e.g., `None` + adjusted query) or early return would be safer.

**File:** `app/memory/mem0_client.py` (`get_mem0_client`, `_build_mem0_instance`)  
**Severity:** minor  
**Description:** The singleton uses a `threading.Lock` while the rest of the application is async. Although the current uvicorn configuration is single-process, any future use of multiple threads or `asyncio.to_thread` could deadlock or race on the lock. An `asyncio.Lock` (with proper `run_in_executor` handling) would be more consistent.

**File:** `app/flows/search_flow.py` (`hybrid_search_messages`, rerank path)  
**Severity:** minor  
**Description:** The cross-encoder reranker is loaded via a global `_reranker_cache` on first use. `sentence_transformers.CrossEncoder` is CPU-heavy and not released on cache eviction or config change. Under sustained load with many different reranker models, this can cause uncontrolled memory growth. No cache size limit or TTL is enforced.

**File:** `app/config.py` (`load_config`) and `app/flows/tool_dispatch.py` (all `_get_*_flow` helpers)  
**Severity:** minor  
**Description:** `load_config()` (which reads from disk) is called on every tool dispatch and inside several verbose logging helpers. While the file is small, this creates unnecessary I/O on the hot path for every MCP call and every background job. The "hot-reloadable" design is correct in principle but the implementation is naïve (no mtime caching).

**Architectural note (not a per-line finding):** The codebase correctly follows the LangGraph mandate for the most part, but several flows (especially `imperator_flow.py` and the admin tools) mix LangGraph nodes with direct synchronous calls and global state. This creates tight coupling between the graph and external singletons (`_llm_cache`, `_reranker_cache`, `MemorySaver`, Mem0 singleton). A future change to any of these caches will require touching many flows, violating the "thin gateway / all logic in graphs" goal.

**Summary of severity distribution:** 1 blocker, 4 major, 6 minor. The blocker prevents degraded-mode startup; the majors break hot-reload, allow worker crashes on bad jobs, and create unsafe multi-process behavior. All other findings are relatively easy to address but indicate gaps in error handling and config-change robustness that should be fixed before production use.