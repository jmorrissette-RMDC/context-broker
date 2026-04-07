**Code Review Findings**

**File:** `app/flows/embed_pipeline.py`  
**Function/Line:** `enqueue_context_assembly` (~140-170)  
**Severity:** major  
**Description:** N+1 query pattern. For every context window attached to a conversation that has a `last_assembled_at`, the code executes an individual `SELECT COALESCE(SUM(token_count), 0)` query. When a conversation has many windows this becomes a serious performance and load problem on the PostgreSQL instance. The Redis batching for lock checks (G5-11) was implemented, but the token-since-last-assembly batching mentioned in the comment was never actually done. This runs on every message ingestion.

**File:** `app/config.py`  
**Function/Line:** `verbose_log_auto` (~140) and callers in `standard_tiered.py:280,380`, `imperator_flow.py`  
**Severity:** major  
**Description:** Synchronous blocking file I/O (`load_config()` + `os.stat()`) is called from inside async StateGraph nodes and from `verbose_log_auto`. Even though the mtime fast-path exists, the call is still synchronous on every invocation. Under load this blocks the event loop. The file explicitly documents that async callers should use `async_load_prompt`/`async_load_config`, but the async versions are not used in the summarization, consolidation, or Imperator nodes.

**File:** `app/flows/build_types/knowledge_enriched.py` (and similar in `standard_tiered.py`, `passthrough.py`)  
**Function/Line:** `ke_load_window`, `ret_load_window`, `pt_load_window` (~60, ~340, ~180)  
**Severity:** major  
**Description:** `uuid.UUID(state["context_window_id"])` with no try/except. If a malformed UUID reaches the flow (bad Redis job, bad MCP input, etc.) the entire retrieval or assembly graph fails with an unhandled `ValueError`. The newer "M-25" validation was added only to the ARQ worker paths; the hot retrieval paths still lack it. This turns a validation error into a 500 for the caller.

**File:** `app/flows/build_types/tier_scaling.py`  
**Function/Line:** `scale_tier_percentages` (~50-90)  
**Severity:** minor  
**Description:** After adjusting percentages for conversation length the function rounds to 4 decimal places but does not renormalize so the three tier percentages still sum to exactly 1.0. Combined with the validation in `get_build_type_config` (which only checks the static config), it is possible for the runtime percentages to exceed 1.0 or leave a small gap. The code treats the scaled values as authoritative.

**File:** `app/flows/imperator_flow.py`  
**Function/Line:** `agent_node` (~220) and `_load_conversation_history` (~150)  
**Severity:** major  
**Description:** `load_prompt("imperator_identity")` (synchronous) and a direct PostgreSQL query are performed inside the LLM node of a ReAct loop. Both are blocking. Additionally, the history query is executed on every agent turn when no system message is present yet, creating unnecessary load and latency on the critical path.

**File:** `app/flows/memory_extraction.py`  
**Function/Line:** `run_mem0_extraction` (~140) and `ke_inject_knowledge_graph` (knowledge_enriched.py:220)  
**Severity:** major  
**Description:** Extremely broad `except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception)` (explicitly labeled EX-CB-001). While graceful degradation for Mem0/Neo4j is intended, catching `Exception` masks programming errors, bad UUIDs, and other bugs that should fail the job visibly. The same pattern appears in several Mem0 call sites.

**File:** `app/flows/search_flow.py`  
**Function/Line:** `hybrid_search_messages` (~210) and `_build_extra_filters`  
**Severity:** minor  
**Description:** Complex dynamic SQL is built with f-strings and manually tracked parameter indices. While the column names are static (so not injectable), the construction is fragile. A future change that adds a filter could silently break parameter numbering. The comment claims it is safe, but the complexity makes it a maintenance hazard.

**File:** `app/config.py`  
**Function/Line:** `get_chat_model` / `get_embeddings_model` (~280, ~310)  
**Severity:** minor  
**Description:** Cache eviction is an unconditional `_llm_cache.clear()` (and same for embeddings) when the cache grows beyond `_MAX_CACHE_ENTRIES`. This throws away all other provider/model combinations instead of LRU eviction. Under multi-provider usage the cache will thrash.

**Summary of Risk**

- **Blocking I/O in async code** (multiple files) is the most immediate reliability risk under load.
- **N+1 queries** on the hot message-ingestion path will become a scaling problem as conversation volume grows.
- **Missing UUID validation** in retrieval/assembly flows turns bad data into unhandled exceptions.
- The broad `except Exception` blocks for Mem0 make debugging real errors harder.

The architecture (StateGraph + registry + config-driven providers) is sound, but the implementation still contains several classic async Python and database anti-patterns that will surface in production.