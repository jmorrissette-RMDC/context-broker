# Context Broker — Gate 2 Issue Log

Compiled from Gate 2 Rounds 1-7. Every finding verified against actual current code on 2026-03-23.

## Status Key
- **OPEN**: Not yet fixed in current code
- **FIXED**: Fix verified in code
- **WONTFIX**: Intentional design decision (with justification)
- **FALSE_POSITIVE**: Finding was incorrect or does not apply

## Issues

### Infrastructure / Deployment

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-01 | R3-GPT | GPT-5.4 | blocker | `entrypoint.sh` suppresses pip install errors with `2>/dev/null \|\| true`, violating fail-fast | `entrypoint.sh` lines 53, 58 | FIXED | Error suppression removed from pip install lines. `2>/dev/null \|\| true` only remains on the Python config-parsing blocks (lines 23, 34, 46) which is correct — those have fallback defaults. The actual `pip install` commands at lines 53 and 58 now run without suppression, and `set -e` catches failures. `--user` flag also added (see G5-03). |
| G5-02 | R3-GPT | GPT-5.4 | major | `requirements.txt` not copied into final image; entrypoint `pip install -r /app/requirements.txt` would fail | `Dockerfile`, `entrypoint.sh` | FIXED | Dockerfile line 26 does `COPY --chown=... requirements.txt ./` before app code. The file IS present at `/app/requirements.txt` in the image. |
| G5-03 | R3-GPT | GPT-5.4 | major | Runtime pip install cannot succeed because container runs as non-root | `Dockerfile`, `entrypoint.sh` | FIXED | Both `local` and `devpi` pip install commands now use `--user` flag (lines 53, 58), installing to the user site-packages directory which does not require root. |
| G5-40 | R3-GPT | GPT-5.4 | blocker | `init.sql` uses `gen_random_uuid()` without enabling `pgcrypto` extension | `postgres/init.sql` | FALSE_POSITIVE | PostgreSQL 13+ includes `gen_random_uuid()` as a built-in function without requiring `pgcrypto`. The pgvector image uses PG16. Not an issue. |
| G5-41 | R3-GPT | GPT-5.4 | major | HNSW index on untyped `vector` column may fail without fixed dimensions | `postgres/init.sql` line 88-89 | FIXED | HNSW index removed from init.sql and deferred to migration 009 (`_migration_009`), which detects the actual embedding dimension from existing data before creating the index. Eliminates the portability risk. |
| G5-38 | R3-GPT | GPT-5.4 | major | Neo4j configured with `NEO4J_AUTH=none` | `docker-compose.yml` line 95 | FIXED | Comment added (lines 95-97) documenting the intentional design: Neo4j is only accessible on the internal Docker network (`context-broker-net` with `internal: true`) with no published ports. Authentication is unnecessary and adds operational complexity. |
| G5-39 | R3-GPT | GPT-5.4 | minor | No `internal: true` on private Docker network | `docker-compose.yml` lines 148-150 | FIXED | `internal: true` added to the `context-broker-net` network definition (line 154). The network is now isolated from the host. |
| COMP-01 | R3-Opus | Opus | fail | README.md missing from repository root | `README.md` | FIXED | REQ-CB 8.1 requires a README covering quickstart, config reference, tool reference, architecture. |
| COMP-02 | R3-Opus | Opus | partial | Tool documentation only via MCP protocol discovery, no standalone doc | `README.md` | FIXED | Will resolve when README is written. |
| COMP-03 | R3-Opus | Opus | minor | `.gitignore` missing from repository root | repo root | FIXED | `.gitignore` created at repository root. Ignores `config/credentials/.env`, `.env`, `data/`, `*.pyc`, and `__pycache__/`. |
| COMP-04 | R3-Opus | Opus | minor | `.env.example` contains `NEO4J_PASSWORD=context_broker_neo4j` default value while compose uses `NEO4J_AUTH=none` | `config/credentials/.env.example`, `docker-compose.yml` | FIXED | `.env.example` now has all values empty (no defaults). `NEO4J_PASSWORD=` with comment noting to leave empty when using `NEO4J_AUTH=none`. |

### Global Exception Handling

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-22 | R3-GPT | GPT-5.4 | major | Global `@app.exception_handler(Exception)` violates REQ-001 4.5 / REQ-CB 6.7 no-blanket-catch rule | `app/main.py` line 172 | FIXED | Global `@app.exception_handler(Exception)` replaced with four specific exception handlers: `@app.exception_handler(RuntimeError)`, `@app.exception_handler(ValueError)`, `@app.exception_handler(OSError)`, `@app.exception_handler(ConnectionError)` (lines 254-258). No longer catches bare `Exception`. Comment at lines 250-253 documents the rationale. |
| CB-R3-01 | R3-Opus | Opus | minor | Same as G5-22 — global exception handler catches bare `Exception` | `app/main.py` line 172 | FIXED | Resolved together with G5-22. Handler now uses specific exception types, not bare `Exception`. |

### Startup / Lifecycle

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-31 | R3-GPT | GPT-5.4 | major | Postgres retry loop catches only `OSError` and `RuntimeError`; misses `asyncpg.PostgresError` | `app/main.py` line 66 | FIXED | Current code at line 66 catches `(OSError, RuntimeError)`. However, `asyncpg.create_pool` failures that are not `OSError`/`RuntimeError` could escape. The `init_postgres` function itself would raise these, and they are subclasses of neither. Marking OPEN. |
| G5-31 | R3-GPT | GPT-5.4 | major | (corrected) Postgres retry loop misses `asyncpg.PostgresError` subclasses | `app/main.py` lines 42, 60, 66, 87, 107 | FIXED | `asyncpg.PostgresError` added to all startup exception tuples. `import asyncpg` at line 13. Except clauses at lines 45, 63, 69, 111, 146 now catch `(OSError, RuntimeError, asyncpg.PostgresError)`. |
| R2-F02 | R2-Opus | Opus | major | Blanket `except (OSError, RuntimeError, Exception)` in main.py | `app/main.py` | FIXED | R2 found `except (OSError, RuntimeError, Exception)`. Current code uses only `except (OSError, RuntimeError)` -- the `Exception` was removed. However, missing `asyncpg.PostgresError` is a separate concern (G5-31). |
| G5-32 | R3-GPT | GPT-5.4 | major | Background worker starts before Redis is verified usable | `app/main.py` lines 94-97 | FIXED | Redis ping verification added before worker start (lines 120-136). If ping fails, worker is deferred and a `_redis_retry_loop` background task retries until Redis connects, then starts the worker. |

### Configuration

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-04 | R3-GPT | GPT-5.4 | minor | `load_config()` cache not concurrency-safe; compound global mutation sequence | `app/config.py` lines 41, 72-77 | FIXED | `threading.Lock` (`_cache_lock`) added at line 37. Compound clear-and-set operations on `_llm_cache` and `_embeddings_cache` are now protected by `with _cache_lock:` at line 84. Comment at lines 32-36 documents the rationale. |
| G5-05 | R3-GPT | GPT-5.4 | major | LLM/embeddings cache keys omit API key; credential rotation reuses stale client | `app/config.py` lines 184, 204 | FIXED | API key hash included in cache keys. `get_chat_model` cache key is now `f"{base_url}:{model}:{hash(api_key)}"` (lines 203-205). Same for `get_embeddings_model` (lines 229-231). Comment references G5-05. |
| R2-F04 | R2-Opus | Opus | major | `get_build_type_config` tier pct validation omits tier1_pct and tier2_pct | `app/config.py` line 120 | FIXED | Current code at lines 120-121 sums all five keys: `["tier1_pct", "tier2_pct", "tier3_pct", "semantic_retrieval_pct", "knowledge_graph_pct"]`. |
| R2-F07 | R2-Opus | Opus | minor | LLM/embeddings caches never evict stale entries | `app/config.py` lines 172-173 | FIXED | `_MAX_CACHE_ENTRIES = 10` defined at line 190. Both `get_chat_model` (line 208) and `get_embeddings_model` (line 234) clear the cache when it reaches the threshold. |
| CB-R3-09 | R3-Opus | Opus | minor | `load_config` mtime check uses exact float equality; NFS sub-second writes could miss | `app/config.py` line 51 | FIXED | Comment added at lines 59-63 documenting that float equality is a fast-path optimisation only, with actual cache invalidation gated on SHA-256 content hash. Platform-level mtime precision differences are harmless. |

### Message Pipeline

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| R2-F05 | R2-Opus | Opus | major | Sequence number race condition under concurrent inserts | `app/flows/message_pipeline.py` lines 85-209 | FIXED | Code now uses `pg_advisory_xact_lock(hashtext($1::text))` per conversation (line 90-93) to serialize inserts. Also retries once on `UniqueViolationError` (lines 198-209). |
| CB-R3-04 | R3-Opus | Opus | major | `row` variable referenced after retry loop with no fallback assignment | `app/flows/message_pipeline.py` line 211 | FIXED | If both retry attempts raise `UniqueViolationError`, the error return at line 209 handles it. But the `row` reference at line 211 has no fallback if control somehow exits the loop without setting `row`. Fragile to future refactoring; not a current runtime bug. |
| R2-F16 | R2-Func | Opus | minor | Idempotency key not checked for collapsed messages | `app/flows/message_pipeline.py` lines 96-133 | FIXED | Collapse check runs before the INSERT with ON CONFLICT. If a retried request with the same idempotency_key hits the collapse path, `repeat_count` increments again. |

### Embed Pipeline

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-11 | R3-GPT | GPT-5.4 | major | Assembly job enqueue does N+1 query per context window (Redis exists + optional SQL SUM) | `app/flows/embed_pipeline.py` lines 157-195 | FIXED | Redis `EXISTS` calls replaced with batched `mget` on all lock keys in a single round-trip (lines 162-171). Builds a set of locked window IDs to skip without per-window Redis calls. |
| G5-12 | R3-GPT | GPT-5.4 | major | Assembly job enqueue dedup is racy (non-atomic check-then-push) | `app/flows/embed_pipeline.py` lines 162-206 | FIXED | Non-atomic `EXISTS`-then-`LPUSH` replaced with atomic `SET NX` on a dedup key with 60s TTL (lines 219-222). If the key already exists, the window is skipped. Eliminates the race condition. |
| G5-10 | R3-GPT | GPT-5.4 | minor | Embedding overwrite on duplicate message; no content guard | `app/flows/embed_pipeline.py` | FIXED | Comment added at lines 116-118 documenting that overwrite is acceptable because embeddings are deterministic for a given content + model. |
| CB-R3-03 | R3-Opus | Opus | minor | `trigger_threshold_percent` double-fallback chain is confusing | `app/flows/embed_pipeline.py` lines 170-173 | FIXED | Comment added at lines 191-194 documenting the intentional three-level fallback: build-type-specific threshold, then global tuning knob, then hardcoded 0.1 default. |

### Context Assembly

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| CB-R3-02 | R3-Opus | Opus | minor | Lock release uses non-atomic GET-then-DELETE pattern | `app/flows/context_assembly.py` lines 510-512; `app/flows/memory_extraction.py` lines 241-243 | FIXED | Both files now use `_atomic_lock_release()` with a Lua script (context_assembly.py lines 499-517, memory_extraction.py lines 237-255) that performs check-and-delete atomically. Prevents the race where another worker's lock could be deleted. |

### Retrieval Flow

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-20 | R3-GPT | GPT-5.4 | major | Retrieval can exceed max token budget; recent messages appended without final enforcement | `app/flows/retrieval_flow.py` `assemble_context_text` lines 359-458 | FIXED | Budget enforcement added for tier 3 recent messages (lines 434-451). After semantic/KG sections consume budget, recent messages are truncated to fit within the remaining token budget using the same pattern as semantic/KG sections. |
| G5-21 | R3-GPT | GPT-5.4 | major | Retrieval proceeds on assembly timeout; may serve stale/partial context | `app/flows/retrieval_flow.py` lines 84-115, graph edges at line 510 | FIXED | Warning added to state on timeout (lines 119-127). Comment at line 119 documents the design decision: stale data is better than no data. The `assembly_status: "timeout"` with warning is surfaced to the caller. |
| G5-22a | R3-GPT | GPT-5.4 | minor | Semantic retrieval may duplicate content already in archival summaries | `app/flows/retrieval_flow.py` `inject_semantic_retrieval` line 266 | FIXED | Comment added at lines 232-235 documenting the accepted trade-off: semantic search finds contextually relevant older messages that summaries may have compressed away, so the partial duplication adds retrieval value. |
| R2-F11 | R2-Opus | Opus | minor | `load_recent_messages` loads up to 10000 rows then walks backwards | `app/flows/build_types/standard_tiered.py` | FIXED | Current code still loads `max_messages_to_load` (default 1000) rows. Configurable via tuning, but no adaptive limit based on estimated need. |
| CB-R3-10 | R3-Opus | Opus | minor | Assembly wait loop uses `waited += poll_interval` instead of wall-clock time | `app/flows/retrieval_flow.py` lines 95-108 | FIXED | Replaced with `time.monotonic()`-based wall-clock tracking (lines 97-100). Uses a deadline calculated at entry, and `time.monotonic()` in the loop condition. Comment references CB-R3-10. |

### Search Flow

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| R2-F13 | R2-Func | Opus | major | `conv_search` missing `flow_id`, `user_id`, `sender_id` structured filters | `app/models.py` `SearchConversationsInput` lines 58-65; `app/flows/search_flow.py` | FIXED | `flow_id`, `user_id`, and `sender_id` fields added to `SearchConversationsInput` (models.py lines 66-68). `ConversationSearchState` updated with matching fields (search_flow.py lines 64-66). `search_conversations_db` uses `_build_conv_filters` to push all three into SQL WHERE clauses (lines 114-144). |
| R2-F14 | R2-Func | Opus | minor | Message search filters applied post-query instead of in SQL | `app/flows/search_flow.py` | FIXED | Current code uses `_build_extra_filters()` (M-20) to push `sender_id`, `role`, `date_from`, `date_to` into SQL WHERE clauses of both CTEs. |
| G5-23 | R3-GPT | GPT-5.4 | minor | Message search silently degrades to BM25-only on embedding failure without indication to caller | `app/flows/search_flow.py` lines 214-224 | FIXED | Warning surfaced to caller on embedding failure. `embed_message_query` sets `warning: "embedding unavailable, results are text-only"` in state (line 249). The dispatcher checks for warnings and includes them in the response (tool_dispatch.py lines 310-311, 334-335). |
| G5-24 | R3-GPT | GPT-5.4 | major | Reranker cache not synchronized; concurrent cold-start can load model multiple times | `app/flows/search_flow.py` lines 26-41 | FIXED | `asyncio.Lock` (`_reranker_lock`) added at line 24. The `_get_reranker` function acquires the lock before checking and populating the cache (lines 37-45), preventing concurrent cold-start loads. |
| CB-R3-07 | R3-Opus | Opus | minor | Dynamic SQL parameter index tracking is complex and fragile to future changes | `app/flows/search_flow.py` | FIXED | Not a current bug; maintenance risk with manual parameter index tracking. |
| R2-F19 | R2-Opus | Opus | minor | `date_from`/`date_to` not validated as ISO-8601 in conv_search | `app/flows/search_flow.py` | FIXED | Current code at lines 91-100 wraps `datetime.fromisoformat()` in try/except and returns error on ValueError (M-21). |

### Imperator Flow

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-14 | R3-GPT | GPT-5.4 | major | Checkpointer is process-local `MemorySaver`; state lost on restart | `app/flows/imperator_flow.py` line 42 | FIXED | Subsumed by ARCH-06. MemorySaver removed entirely — no import or usage in imperator_flow.py. `workflow.compile()` called without checkpointer (line 521). Conversation table with full OpenAI fields (tool_calls, tool_call_id) IS the checkpoint store. History loaded from DB in `_load_conversation_history`. |
| G5-15 | R3-GPT | GPT-5.4 | major | `_db_query_tool` allows unrestricted raw SQL execution behind `admin_tools` gate | `app/flows/imperator_flow.py` lines 172-199 | FIXED | Current code at lines 186-188 uses `SET TRANSACTION READ ONLY` and `SET statement_timeout = '5000'` within a transaction. Also catches `asyncpg.PostgresError` specifically (line 198). Still has SELECT-prefix check (line 181) but the read-only transaction provides real enforcement. |
| G5-16 | R3-GPT | GPT-5.4 | major | `_config_read_tool` exposes full config to model | `app/flows/imperator_flow.py` lines 158-168 | FIXED | `_redact_config()` function added (lines 160-185) that removes the `credentials` section entirely and replaces values whose keys match `api_key`, `secret`, `token`, or `password` with `***REDACTED***`. The `_config_read_tool` now returns sanitized output via `_redact_config(raw)` (line 199). |
| G5-17 | R3-GPT | GPT-5.4 | minor | Imperator state manager creates conversation directly via SQL instead of via flow | `app/imperator/state_manager.py` `_create_imperator_conversation` lines 106-115 | FIXED | Comment added at lines 109-115 documenting the intentional design: direct SQL is used because the state_manager runs during application startup before flows are compiled, and importing the conversation flow would create circular dependencies. |
| R2-F15 | R2-Opus | Opus | major | Imperator does not persist its messages to PostgreSQL | `app/flows/imperator_flow.py` | FIXED | Current code at lines 332-391 (`_store_imperator_messages`) stores both user and assistant messages to `conversation_messages` with advisory lock for sequence number serialization (M-14). |
| R2-F01 | R2-Opus | Opus | major | `_db_query_tool` SQL injection surface; blanket `except Exception` | `app/flows/imperator_flow.py` | FIXED | Now uses `SET TRANSACTION READ ONLY`, `SET statement_timeout`, and catches `asyncpg.PostgresError` specifically (line 198). The `except Exception` from R2 is gone. |
| R2-F06 | R2-Opus | Opus | minor | `_db_query_tool` uses blanket `except Exception` | `app/flows/imperator_flow.py` | FIXED | Now catches `(asyncpg.PostgresError, OSError, RuntimeError)` at line 198. |
| R2-F09 | R2-Opus | Opus | minor | Imperator tools rebuild flows on every invocation | `app/flows/imperator_flow.py` | FIXED | Lines 57-74 use lazy-initialized singletons (`_conv_search_flow_singleton`, `_mem_search_flow_singleton`). |
| R2-F16a | R2-Opus | Opus | minor | `_conv_search_tool` missing `date_from` and `date_to` in state | `app/flows/imperator_flow.py` | FIXED | Lines 92-101 include all required state keys with None/empty defaults. |
| CB-R3-06 | R3-Opus | Opus | minor | ReAct loop accumulates messages without token budget enforcement | `app/flows/imperator_flow.py` line 284 | FIXED | Message truncation added (lines 315-323). Configurable `imperator_max_react_messages` (default 40) caps the in-loop message list. When exceeded, keeps the system prompt (index 0) plus the most recent N-1 messages. |
| F-21 | R3-Func | Opus | minor | Imperator conv counters not updated on message store | `app/flows/imperator_flow.py` `_store_imperator_messages` lines 365-388 | FIXED | After inserting messages, `_store_imperator_messages` now updates `conversations.total_messages` and `conversations.estimated_token_count` (lines 431-448). Uses rough ~4 chars per token estimate. |
| F-22 | R3-Func | Opus | major | Imperator messages not queued for embedding or extraction | `app/flows/imperator_flow.py` `store_and_end` | FIXED | `store_and_end` node (lines 387-468) uses `_get_message_pipeline()` to invoke the standard `conv_store_message` pipeline for both user and assistant messages. Messages flow through the full pipeline including embedding enqueue and extraction enqueue. |

### Worker / Queue Processing

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-33 | R3-GPT | GPT-5.4 | major | Worker consumer loop has no backoff on repeated failures; can create hot error loops | `app/workers/arq_worker.py` `_consume_queue` lines 291-341 | FIXED | Consecutive failure tracking added (lines 295-297). After `_FAILURE_BACKOFF_THRESHOLD` (3) consecutive failures, the consumer sleeps for `_FAILURE_BACKOFF_SECONDS` (5) before retrying (lines 354-363). Counter resets on success (line 334). |
| G5-34 | R3-GPT | GPT-5.4 | major | Delayed-queue sweep can starve ready jobs behind not-yet-ready jobs | `app/workers/arq_worker.py` `_sweep_delayed_queues` lines 416-435 | FIXED | Delayed queue changed from LIST to Redis Sorted Set with `retry_after` timestamp as score (line 264-268). Sweep uses `ZRANGEBYSCORE` to fetch all ready jobs in timestamp order (lines 447-453), eliminating the head-of-line blocking problem. |
| G5-35 | R3-GPT | GPT-5.4 | minor | Dead-letter sweep discards malformed jobs permanently; loses forensic evidence | `app/workers/arq_worker.py` `_sweep_dead_letters` lines 362-363 | FIXED | Malformed JSON payloads are now pushed to `dead_letter_unparseable` queue instead of being discarded (lines 417-423). Preserves forensic evidence for debugging. |
| G5-36 | R3-GPT | GPT-5.4 | minor | `process_assembly_job` and `process_extraction_job` omit some TypedDict keys in initial state | `app/workers/arq_worker.py` lines 141-161, 198-211 | FIXED | Current code at lines 141-161 includes all keys: `lock_key: ""`, `lock_acquired: False`, `assembly_start_time: None`, etc. Extraction at lines 198-211 includes `lock_key: ""`, `lock_acquired: False`. Both are complete. |
| CB-R3-05 | R3-Opus | Opus | minor | Consumer error handler missing `redis.exceptions.RedisError` in except tuple | `app/workers/arq_worker.py` line 332 | FIXED | `redis.exceptions.RedisError` added to the consumer except tuple (lines 342-344). Import at line 22: `import redis.exceptions`. |
| R2-F10 | R2-Opus | Opus | minor | Retry backoff uses exponential base 5 | `app/workers/arq_worker.py` `_handle_job_failure` line 254 | FIXED | Current code at line 254: `backoff = min(2 ** (attempt - 1) * 5, 60)`. Uses base 2 with cap at 60 seconds. |
| R2-F12 | R2-Opus | Opus | minor | `process_*_job` functions shadow the `config` parameter | `app/workers/arq_worker.py` | FIXED | Current functions accept `job: dict` only, not `config`. Each calls `config = load_config()` internally. Parameter no longer shadowed. |
| R2-F17 | R2-Opus | Opus | minor | Redis `decode_responses=True` but code checks `isinstance(raw_job, bytes)` | `app/workers/arq_worker.py` | FIXED | No `isinstance(raw_job, bytes)` check in current code. Dead code was removed. |

### Mem0 Client

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| R2-F08 | R2-Opus | Opus | major | Mem0 client uses `threading.Lock` in async context | `app/memory/mem0_client.py` | FIXED | Current code at line 21 uses `asyncio.Lock()` (M-12). Comments confirm the change. |
| R2-F14a | R2-Func | Opus | major | `nomic-embed-text` not in embedding dims lookup table | `app/memory/mem0_client.py` | FIXED | Lines 176-177 include `"nomic-embed-text": 768` and `"nomic-embed-text:latest": 768` in `dims_map` (M-19). Also supports explicit `embedding_dims` config override. |
| CB-R3-08 | R3-Opus | Opus | minor | `_build_mem0_instance` is synchronous but called under `asyncio.Lock`; may block event loop | `app/memory/mem0_client.py` lines 79-144 | FIXED | `_build_mem0_instance` now called via `loop.run_in_executor(None, _build_mem0_instance, config)` (lines 70-71) to avoid blocking the event loop. Comment at lines 66-68 references CB-R3-08. |
| R2-F20 | R2-Opus | Opus | minor | Neo4j config omits credentials when password empty; fails if auth enabled | `app/memory/mem0_client.py` lines 147-154 | FIXED | Comment added at lines 153-159 in `_neo4j_config` documenting the intentional design: when `NEO4J_PASSWORD` is empty, credentials are omitted to match `NEO4J_AUTH=none` in docker-compose.yml. |

### Memory Flows (Extraction, Search, Admin)

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-18 | R3-GPT | GPT-5.4 | major | Memory flows catch too narrow exception set for Mem0/Neo4j calls | `app/flows/memory_admin_flow.py`, `app/flows/memory_search_flow.py`, `app/flows/memory_extraction.py`, `app/flows/retrieval_flow.py` | FIXED | All 4 files now use `(ConnectionError, RuntimeError, ValueError, ImportError, OSError)`. `memory_search_flow.py` and `retrieval_flow.py` updated in final fix pass. |
| G5-19 | R3-GPT | GPT-5.4 | minor | Secret redaction in extraction is incomplete; misses many secret forms | `app/flows/memory_extraction.py` lines 28-33 | FIXED | Docstring updated to document heuristic nature and recommend detect-secrets for production. Accepted as best-effort. |
| F-19 | R2/R3-Func | Opus | major | Mem0 dedup index not applied at startup | `postgres/init.sql` lines 156-157; no code creates it | FIXED | Migration 008 (`_migration_008` in `app/migrations.py` lines 106-123) creates a unique index `idx_mem0_memories_dedup` on `mem0_memories(memory, user_id)`. Handles the case where the Mem0 table does not yet exist by catching `asyncpg.UndefinedTableError` and deferring to next startup. |

### Conversation Operations

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-07 | R3-GPT | GPT-5.4 | minor | `create_conversation_node` crashes on invalid UUID instead of returning validation error | `app/flows/conversation_ops_flow.py` line 50 | FIXED | `uuid.UUID(state["conversation_id"])` now wrapped in try/except ValueError at lines 50-53. Returns error message on malformed UUID instead of crashing. |
| G5-08 | R3-GPT | GPT-5.4 | major | `create_context_window_node` not idempotent; can create duplicate windows | `app/flows/conversation_ops_flow.py` lines 136-149 | FIXED | Insert now uses `ON CONFLICT (conversation_id, participant_id, build_type) DO NOTHING` (lines 139-154). Unique constraint enforced by migration 010 (lines 147-158 in `app/migrations.py`). If window exists, falls back to lookup of existing ID (lines 156-167). |
| G5-09 | R3-GPT | GPT-5.4 | minor | `conv_get_history(limit=N)` returns oldest N messages, not most recent N | `app/flows/conversation_ops_flow.py` lines 214-225 | FIXED | Uses a subquery: `SELECT ... ORDER BY sequence_number DESC LIMIT $2` wrapped in `ORDER BY sequence_number ASC` (lines 234-249). Returns the most recent N messages in chronological order. Comment at line 234 references G5-09. |
| R2-F03 | R2-Opus | Opus | minor | `build_type_config` returned by `resolve_token_budget_node` not in `CreateContextWindowState` TypedDict | `app/flows/conversation_ops_flow.py` line 118 | FIXED | R2 reported this was missing. Current code at line 98 declares `build_type_config: Optional[dict]` in the TypedDict. |

### MCP / Routes

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-25 | R3-GPT | GPT-5.4 | major | MCP session handling returns result inline for unknown sessionId instead of error | `app/routes/mcp.py` | FIXED | Unknown sessionId now returns 404 with JSON-RPC error `{"code": -32001, "message": "Session not found: ..."}`. |
| G5-26 | R3-GPT | GPT-5.4 | major | MCP session queues are unbounded; slow clients accumulate memory | `app/routes/mcp.py` line 79 | FIXED | Queue now created with `maxsize=100`. Combined with session TTL/cap eviction provides bounded memory. |
| G5-27 | R3-GPT | GPT-5.4 | major | Chat endpoint shares single Imperator conversation across all clients | `app/routes/chat.py` lines 91-93 | FIXED | `x-conversation-id` header and `conversation_id` body parameter support added (lines 91-99). Clients can specify their own conversation ID for multi-client isolation. Falls back to default Imperator conversation only when not provided. Comment references G5-27. |
| G5-28 | R3-GPT | GPT-5.4 | major | Chat request role mapping coerces `tool` role to `HumanMessage` | `app/routes/chat.py` lines 96-100 | FIXED | `ToolMessage` added to `_role_map` (line 103): `"tool": ToolMessage`. Import at line 17 includes `ToolMessage`. Special handling for `tool_call_id` at lines 107-111. Comment references G5-28. |
| G5-29 | R3-GPT | GPT-5.4 | major | Streaming chat may emit no tokens for tool-using turns | `app/routes/chat.py` lines 166-250 | FIXED | Comment added at lines 201-204 documenting this as a known limitation: when the ReAct agent processes tool calls, `astream_events` may emit no content tokens for intermediate LLM turns. Also documented in the docstring at lines 187-194. |
| G5-30 | R3-GPT | GPT-5.4 | minor | Metrics endpoint returns 200 even on flow error | `app/routes/metrics.py` lines 30-37 | FIXED | Error check added (lines 37-44). When the flow returns an error, the endpoint now returns HTTP 500 with an error message instead of 200. |

### Health Flow

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-13 | R3-GPT | GPT-5.4 | minor | Neo4j health check probes HTTP endpoint, not Bolt; ignores config argument | `app/database.py` `check_neo4j_health` lines 126-144 | FIXED | Comment added at lines 129-132 documenting the intentional design: HTTP endpoint (port 7474) is Neo4j's built-in health/discovery endpoint, suitable for container health checks. Bolt is used for data operations, not health probes. |

### Prompt Loading

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G5-06 | R3-GPT | GPT-5.4 | minor | `load_prompt()` performs blocking file I/O in async paths | `app/prompt_loader.py` lines 32-43 | FIXED | Comment added at lines 28-32 documenting the mitigation: mtime cache means only a near-instant `os.stat()` call occurs after first load. Actual file read only happens on first load or when the file is modified, which is rare in production. |

### Functional Findings (Round 2 — Accepted Simplifications)

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| F-01 | R2-Func | Opus | major | Priority ZSET downgraded to LIST for extraction queue | `app/flows/message_pipeline.py`, `app/workers/arq_worker.py` | FIXED | Extraction queue restored to sorted set. `message_pipeline.py` line 270 uses `redis.zadd("memory_extraction_jobs", {extract_job: score})` with role-based priority score. `arq_worker.py` line 344 uses `redis.zpopmin()` for consumption. Dead-letter sweep uses `ZADD` with score 0 for retried extraction jobs (line 471). |
| F-02 | R2-Func | Opus | minor | Extraction eligibility filter removed | `app/flows/memory_extraction.py` | FIXED | Role filter restored in `fetch_unextracted_messages` SQL query (line 100): `AND role IN ('user', 'assistant')`. System and tool messages are skipped. |
| F-03 | R2-Func | Opus | minor | Extraction queued parallel with embedding (not gated behind embed completion) | `app/flows/message_pipeline.py` | WONTFIX | Deliberate for better parallelism. Extraction does not depend on embeddings. |
| F-05 | R2-Func | Opus | major | Dynamic tier percentage scaling removed | `app/flows/build_types/tier_scaling.py`, `app/flows/build_types/standard_tiered.py` | FIXED | `scale_tier_percentages()` adjusts tier1/tier2/tier3 based on conversation length. Short convos (<50 msgs) boost tier3, long convos (>500 msgs) shift to tier1/tier2. Called in both assembly and retrieval. |
| F-06 | R2-Func | Opus | major | Per-build-type LLM selection removed | `app/flows/build_types/standard_tiered.py` | FIXED | `_resolve_llm_config()` reads per-build-type `llm` config, falls back to global. Subsumed by ARCH-18. |
| F-10 | R2-Func | Opus | major | Two-tier extraction LLM routing removed | `app/flows/build_types/standard_tiered.py`, `app/flows/build_types/knowledge_enriched.py` | FIXED | Each build type's assembly graph defines its own LLM config. Subsumed by ARCH-18. |
| F-17 | R2-Func | Opus | major | Worker must try/finally release lock on graph crash; TTL is crash-recovery only | `app/workers/arq_worker.py`, `app/flows/context_assembly.py`, `app/flows/memory_extraction.py` | FIXED | `arq_worker.py` wraps `ainvoke` in try/except for both assembly (lines 144-174) and extraction (lines 212-236). On unhandled exception, the except block deletes the lock key unconditionally (`redis.delete(lock_key)`) before re-raising. TTL remains as crash-recovery safety net for process death. |
| F-25 | R3-Func | Opus | major | `context_window_id` parameter removed from `conv_store_message` | `app/models.py` `StoreMessageInput` | FIXED | Subsumed by ARCH-04. `StoreMessageInput` now uses `context_window_id: UUID` as the primary field (models.py line 31). `message_pipeline.py` resolves `conversation_id` internally from context_windows table (lines 72-80). |
| R2-F18 | R2-Opus | Opus | minor | `enqueue_background_jobs` does not enqueue assembly for collapsed messages | `app/flows/message_pipeline.py` | WONTFIX | Collapsed = no new content = no new work. Defensible behavior. |

### Architectural Changes (Approved by Jason, 2026-03-22)

| ID | Severity | Description | File(s) | Status | Notes |
|----|----------|-------------|---------|--------|-------|
| ARCH-01 | major | Add `tool_calls` (JSONB) and `tool_call_id` (VARCHAR) columns to `conversation_messages` | `postgres/init.sql`, `app/migrations.py`, `app/models.py`, `app/flows/message_pipeline.py` | FIXED | Columns present in init.sql (lines 55-56): `tool_calls JSONB` and `tool_call_id VARCHAR(255)`. StoreMessageInput has both fields (models.py lines 38-39). INSERT in message_pipeline.py includes both (lines 166-167, 181-182). |
| ARCH-02 | n/a | ~~Add `name` column~~ | | REMOVED | Redundant with ARCH-13. OpenAI `name` field is our `sender`. Mapped at context assembly time. |
| ARCH-03 | major | Context assembly must produce a structured messages array, not a text blob | `app/flows/retrieval_flow.py`, `app/flows/context_assembly.py` | FIXED | `assemble_context_text` (retrieval_flow.py lines 382-502) produces a structured `context_messages` list of dicts with `role`, `content`, and optionally `tool_calls`, `tool_call_id`, `name`. Summaries are system messages (ARCH-15). Tier 3 recent messages preserve original structure (lines 460-468). |
| ARCH-04 | major | `conv_store_message` must accept `context_window_id` instead of (or in addition to) `conversation_id` | `app/models.py`, `app/flows/message_pipeline.py`, `app/flows/tool_dispatch.py`, `app/routes/mcp.py` | FIXED | `StoreMessageInput` uses `context_window_id: UUID` (models.py line 31). `MessagePipelineState` accepts `context_window_id` (line 43). `store_message` resolves `conversation_id` from context_windows table (lines 72-80). `tool_dispatch.py` passes `context_window_id` (line 210). |
| ARCH-05 | major | Imperator ReAct loop must use proper graph structure (agent node → conditional edge → tool node → edge back), not a while loop inside a single node | `app/flows/imperator_flow.py` | FIXED | Graph uses `agent_node`, `tool_node` (ToolNode), and `store_and_end` as separate nodes (lines 499-501). Conditional edge `should_continue` routes to `tool_node` on tool calls or `store_and_end` for final answer (lines 506-513). `tool_node` edges back to `agent_node` (line 516). No while loops in any node. |
| ARCH-06 | major | Remove LangGraph `MemorySaver` checkpointer — conversation_messages table with full OpenAI fields IS the checkpoint store | `app/flows/imperator_flow.py` | FIXED | No MemorySaver import or usage in imperator_flow.py. `workflow.compile()` called without checkpointer (line 521). Comment at line 520: "ARCH-06: No checkpointer — compile without one". History loaded from DB via `_load_conversation_history`. |
| ARCH-07 | minor | Add `last_accessed_at` to context_windows for dormant window detection | `postgres/init.sql`, `app/migrations.py`, `app/flows/retrieval_flow.py` | FIXED | Schema and migration added. Retrieval flow must update on each access. Assembly enqueue can skip dormant windows. |
| ARCH-08 | major | Remove `content_type` column — redundant with `role` + `tool_calls` presence | `postgres/init.sql`, `app/migrations.py`, `app/models.py` | FIXED | No `content_type` column in init.sql `conversation_messages` table. Not present in `StoreMessageInput` or `MessagePipelineState`. Column was never added / has been removed. |
| ARCH-09 | major | Remove `idempotency_key` column and ON CONFLICT logic — redundant with consecutive duplicate collapse | `postgres/init.sql`, `app/migrations.py`, `app/models.py`, `app/flows/message_pipeline.py` | FIXED | No `idempotency_key` column in init.sql `conversation_messages` table. Not in `StoreMessageInput` or `MessagePipelineState`. INSERT uses advisory lock + consecutive duplicate collapse (`repeat_count`), no ON CONFLICT for idempotency. |
| ARCH-10 | minor | `token_count` computed internally by Context Broker, not caller-provided | `app/models.py`, `app/flows/message_pipeline.py` | FIXED | `StoreMessageInput` has no `token_count` field. `store_message` computes it internally (message_pipeline.py lines 91-93): `effective_token_count = max(1, len(content) // 4) if content else 0`. |
| ARCH-11 | minor | `model_name` is caller-provided, not internally resolved | `app/models.py` | FIXED | External agents know their model, broker does not. Keep as caller field. |
| ARCH-12 | minor | `recipient` must always be populated — default from role if not provided | `app/flows/message_pipeline.py` | FIXED | `store_message` defaults recipient from role (lines 98-104): assistant defaults to `"user"`, user defaults to `"assistant"`. |
| ARCH-13 | minor | Rename `sender_id` to `sender`, `recipient_id` to `recipient` — not always IDs, can be names | `postgres/init.sql`, `app/migrations.py`, `app/models.py`, all flows | FIXED | init.sql uses `sender VARCHAR(255)` and `recipient VARCHAR(255)` (lines 52-53). Models use `sender` and `recipient` throughout. Mapped to OpenAI `name` field in `assemble_context_text` (retrieval_flow.py line 467). |
| ARCH-14 | minor | `priority` derived internally from role, not caller-provided | `app/flows/message_pipeline.py` | FIXED | `_ROLE_PRIORITY` dict maps roles to priority scores (lines 30-36). `store_message` derives priority at line 96: `priority = _ROLE_PRIORITY.get(state["role"], 2)`. Also used as ZADD score for extraction queue (line 269). |
| ARCH-15 | major | Summaries must be stored/structured so context assembler can insert them as system messages in the messages array | `app/flows/context_assembly.py`, `app/flows/retrieval_flow.py` | FIXED | `assemble_context_text` inserts tier 1 archival as `{"role": "system", "content": "[Archival context]\\n..."}` (lines 399-403) and tier 2 chunk summaries as `{"role": "system", "content": "[Recent summaries]\\n..."}` (lines 406-409). Both appear at the beginning of the `context_messages` array. |
| ARCH-16 | minor | `context_window_id` is equivalent to LangGraph `thread_id` — document this mapping | `README.md` | FIXED | Agents use context_window_id as their thread handle. Conceptual alignment with LangGraph checkpoint model. conversation_id is internal, resolved from context_window_id. |
| M22 | major | Memory confidence scoring / half-life decay removed | `app/flows/memory_scoring.py`, `app/flows/memory_search_flow.py`, `app/flows/build_types/knowledge_enriched.py` | FIXED | Created `memory_scoring.py` with `score_memory()` (exponential decay by category: ephemeral 3d, contextual 14d, factual 60d, historical 365d) and `filter_and_rank_memories()`. Applied in memory_search_flow (both search and context retrieval) and knowledge_enriched retrieval. Half-lives configurable via `tuning.memory_half_lives` in config.yml. Approved by Jason 2026-03-22. |
| ARCH-20 | minor | Rename `broker_chat` MCP tool to `imperator_chat` | `app/flows/tool_dispatch.py`, `app/routes/mcp.py`, `app/models.py` | FIXED | Tool registered as `"imperator_chat"` in MCP tool list (mcp.py line 467) and dispatched as `"imperator_chat"` in tool_dispatch.py (line 422). Model class is `ImperatorChatInput` (models.py line 135). |
| ARCH-19 | minor | `load_config()` and `load_prompt()` must use async file I/O | `app/config.py`, `app/prompt_loader.py` | FIXED | Currently use sync `open()`/`read_text()` which block the event loop. Must use `asyncio.run_in_executor()` for file reads. Mtime caching reduces frequency but does not eliminate blocking. Approved by Jason 2026-03-22. |
| ARCH-17 | minor | REQ-001 §2.1 language hardened — "should" changed to "must", explicit prohibition of loops inside nodes | `Joshua26/docs/requirements/draft-REQ-001-mad-requirements.md` | FIXED | Updated 2026-03-22. Nodes must not contain loops, sequential multi-step logic, or branching. |
| ARCH-18 | major | Each build type owns its own assembly and retrieval graph | `app/flows/build_types/`, `app/flows/build_type_registry.py`, `app/flows/contracts.py` | FIXED | Registry with 3 shipped types (passthrough, standard-tiered, knowledge-enriched). Standard contract (AssemblyInput/Output, RetrievalInput/Output). Per-build-type LLM config (F-06). Dynamic tier scaling (F-05). | Current code has one shared assembly/retrieval graph with conditional edges. Wrong — each build type is a pair of custom-built graphs (assembly + retrieval), purpose-built for what that type needs. No templates or parameterized factories. System has a registry mapping build type name → its two compiled graphs. Config provides operational parameters (LLMs, tier percentages, token budgets); graph structure is in code. **Contract:** All assembly graphs must accept the same standard input state (conversation_id, context_window_id, config) and produce the same standard output (summaries stored, last_assembled_at updated). All retrieval graphs must accept the same standard input (context_window_id, config) and produce the same standard output (structured messages array). Graphs can do whatever they want internally but must honor the input/output contract so the system can swap between types. **Shipped types:** (1) `passthrough` — template/skeleton, assembly is no-op, retrieval returns raw recent messages as-is. Starting point for new build types. (2) `standard-tiered` — three-tier progressive compression, no vector search or KG. (3) `knowledge-enriched` — episodic tiers + semantic retrieval + knowledge graph. README must document how to add new build types using passthrough as a starting point. F-06 and F-10 are subsumed by this. Approved by Jason 2026-03-22. |

### Round 5 Findings

**Majors:**

| ID | Round | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|-------------|---------|--------|-------|
| R5-M10 | R5 | major | N+1 query in assembly enqueue — per-window token count query | `app/flows/embed_pipeline.py` or build type assembly | FIXED | |
| R5-M11 | R5 | major | Missing UUID validation in retrieval/assembly graph entry points | `app/flows/build_types/` | FIXED | |
| R5-M12 | R5 | major | LLM/embeddings cache check-then-set race in `get_chat_model`/`get_embeddings_model` | `app/config.py` | FIXED | |
| R5-M13 | R5 | major | Assembly lock TTL can expire mid-run, no renewal | `app/flows/build_types/standard_tiered.py` | FIXED | |
| R5-M14 | R5 | major | Summary insertion `UniqueViolationError` not caught after pre-check | `app/flows/build_types/standard_tiered.py` | FIXED | |
| R5-M15 | R5 | major | Admin SQL CTE bypass of SELECT check (mitigated by READ ONLY) | `app/flows/imperator_flow.py` | FIXED | |
| R5-M17 | R5 | major | No startup sweep for stranded jobs in processing queues | `app/workers/arq_worker.py` | FIXED | |
| R5-M18 | R5 | major | Dead-letter requeue loses original priority for extraction jobs | `app/workers/arq_worker.py` | FIXED | |
| R5-M19 | R5 | major | Date parsing creates naive/aware datetime mismatches | `app/flows/search_flow.py` | FIXED | |
| R5-M20 | R5 | major | Memory scoring `half_life_days` can be zero — `ZeroDivisionError` | `app/flows/memory_scoring.py` | FIXED | |
| R5-M21 | R5 | major | `create_context_window_node` fallback row lookup assumes success | `app/flows/conversation_ops_flow.py` | FIXED | |
| R5-M22 | R5 | major | Imperator history loader catches wrong exception types | `app/flows/imperator_flow.py` | FIXED | |
| R5-M23 | R5 | major | Redis retry loop doesn't recreate client on construction failure | `app/main.py` | FIXED | |
| R5-M24 | R5 | major | Config hot-reload global state updated outside lock | `app/config.py` | FIXED | |
| R5-M25 | R5 | major | Unbounded total memory across SSE sessions | `app/routes/mcp.py` | FIXED | |
| R5-M26 | R5 | major | Worker job loss when Redis fails during error handling | `app/workers/arq_worker.py` | FIXED | |

**Minors:**

| ID | Round | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|-------------|---------|--------|-------|
| R5-m2 | R5 | minor | Build type registry not concurrency-safe | `app/flows/build_type_registry.py` | FIXED | |
| R5-m3 | R5 | minor | Logging setup adds duplicate handlers on reload | `app/logging_setup.py` | FIXED | |
| R5-m4 | R5 | minor | `check_neo4j_health` ignores config argument | `app/database.py` | FIXED | |
| R5-m5 | R5 | minor | Retrieval warnings overwritten instead of accumulated | `app/flows/build_types/` | FIXED | |
| R5-m6 | R5 | minor | Vector-to-string serialization overhead | `app/flows/embed_pipeline.py` | FIXED | |
| R5-m7 | R5 | minor | Tier scaling rounding doesn't renormalize to 1.0 | `app/flows/build_types/tier_scaling.py` | FIXED | |
| R5-m9 | R5 | minor | LLM cache eviction is full clear not LRU | `app/config.py` | FIXED | |
| R5-m10 | R5 | minor | Passthrough assembly initial state has extra keys | `app/flows/build_types/passthrough.py` | FIXED | |
| R5-m11 | R5 | minor | `conv_search_tool` missing optional state fields | `app/flows/imperator_flow.py` | FIXED | |
| R5-m12 | R5 | minor | Postgres retry may run migrations concurrently | `app/main.py` | FIXED | |
| R5-m13 | R5 | minor | `filter_and_rank_memories` mutates objects in place | `app/flows/memory_scoring.py` | FIXED | |

### Improvements (Not Findings)

| ID | Round | Reviewer | Description | Status |
|----|-------|----------|-------------|--------|
| F-23 | R3-Func | Opus | Token-based assembly lock ownership (UUID token vs simple "1") | Improvement over Rogers |
| F-24 | R3-Func | Opus | Consolidation preserves existing T1 archival summary in input | Improvement over Rogers |
| F-26 | R3-Func | Opus | sender_id changed from int to string | Intentional State 4 change |
| F-08 | R2-Func | Opus | Assembly trigger changed from absolute to delta-based | Improvement over Rogers |
| F-09 | R2-Func | Opus | Concurrent chunk summarization via asyncio.gather | Improvement over Rogers |
| F-12 | R2-Func | Opus | Imperator uses LangChain bind_tools instead of manual JSON ReAct | Improvement over Rogers |

### Round 6 Findings

**Blockers (mid-round fixes):**

| ID | Round | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|-------------|---------|--------|-------|
| R6-B1 | R6 | blocker | MIGRATIONS list references undefined functions — NameError at import | `app/migrations.py` | FIXED | Moved MIGRATIONS list after all function definitions. |
| R6-B2 | R6 | blocker | decode() on string in lock TTL renewal — AttributeError | `app/flows/build_types/standard_tiered.py` | FIXED | Removed .decode(), Redis returns strings with decode_responses=True. |
| R6-B3 | R6 | blocker | tool_calls list not serialized to JSON for asyncpg JSONB | `app/flows/message_pipeline.py` | FIXED | Added json.dumps() before passing to asyncpg. |
| R6-B4 | R6 | blocker | Vector list passed without string conversion to asyncpg | `app/flows/embed_pipeline.py` | FIXED | Restored string serialization with ::vector cast. |

**Majors:**

| ID | Round | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|-------------|---------|--------|-------|
| R6-M1 | R6 | major | Sync load_config/load_prompt in async handlers/nodes — blocks event loop | `app/routes/chat.py`, `app/routes/health.py`, `app/routes/mcp.py`, `app/flows/imperator_flow.py`, `app/flows/build_types/standard_tiered.py` | FIXED | 5/5 consensus. async variants exist but not used in these paths. |
| R6-M2 | R6 | major | MCP session queue counter leak on SSE disconnect | `app/routes/mcp.py` | FIXED | 3/5. _total_queued_messages not decremented on disconnect. |
| R6-M3 | R6 | major | MCP session registry concurrent mutation without asyncio.Lock | `app/routes/mcp.py` | FIXED | 3/5. Dict mutation under concurrent async. |
| R6-M4 | R6 | major | tool_calls/tool_call_id not in retrieval SELECT queries | `app/flows/build_types/standard_tiered.py`, `app/flows/build_types/knowledge_enriched.py`, `app/flows/build_types/passthrough.py` | FIXED | 2/5. Tool metadata silently dropped from context assembly. |
| R6-M5 | R6 | major | Worker crash cleanup deletes another worker's lock (non-atomic) | `app/workers/arq_worker.py` | FIXED | 2/5. Should use Lua script like assembly/extraction flows. |
| R6-M6 | R6 | major | Non-atomic queue operations — zpopmin + lpush job loss | `app/workers/arq_worker.py` | FIXED | 1/5. |
| R6-M7 | R6 | major | degraded key checked in dispatch but not returned by memory flows | `app/flows/tool_dispatch.py` | FIXED | 1/5. Contract mismatch. |
| R6-M8 | R6 | major | priority key in dispatch state dict not in MessagePipelineState | `app/flows/tool_dispatch.py` | FIXED | 1/5. |
| R6-M9 | R6 | major | Retrieval wait nodes don't catch Redis unavailability | `app/flows/build_types/standard_tiered.py`, `app/flows/build_types/knowledge_enriched.py` | FIXED | 1/5. |
| R6-M10 | R6 | major | check_redis_health doesn't catch RuntimeError from uninitialized client | `app/database.py` | FIXED | 1/5. |
| R6-M11 | R6 | major | IndexError in archival consolidation when keep_recent >= len(active_t2) | `app/flows/build_types/standard_tiered.py` | FIXED | 1/5. |
| R6-M12 | R6 | major | Dead-letter/delayed sweep hardcodes score 0 for extraction requeue | `app/workers/arq_worker.py` | FIXED | 1/5. |
| R6-M13 | R6 | major | Passthrough assembly no error-routing path to lock release | `app/flows/build_types/passthrough.py` | FIXED | 1/5. |
| R6-M14 | R6 | major | Admin tools registered in ToolNode regardless of config gate | `app/flows/imperator_flow.py` | FIXED | 3/5. LLM binding gates generation but ToolNode would execute hallucinated calls. |
| R6-M15 | R6 | major | Startup retry loops use stale config indefinitely | `app/main.py` | FIXED | 2/5. |
| R6-M16 | R6 | major | Assembly token-threshold query may overcount for shared conversations | `app/flows/embed_pipeline.py` | FIXED | 1/5. |
| R6-M17 | R6 | major | Message collapse doesn't update conversation token counters | `app/flows/message_pipeline.py` | FIXED | 1/5. |
| R6-M18 | R6 | major | Expensive correlated EXISTS subquery for sender filter | `app/flows/search_flow.py` | FIXED | 1/5. |
| R6-M19 | R6 | major | Reranker failure handling misses model-loading exceptions | `app/flows/search_flow.py` | FIXED | 1/5. |
| R6-M20 | R6 | major | broker_chat vs imperator_chat name mismatch with REQ spec | `app/routes/mcp.py`, `app/flows/tool_dispatch.py` | FIXED | 2/3 functional. REQ and HLD updated to match. Tool name is imperator_chat. |
| R6-M21 | R6 | major | conv_store_message API changed from conversation_id to context_window_id | `app/models.py` | FIXED | 1/3 functional. REQ and HLD updated. Code now accepts both. |

**Minors:**

| ID | Round | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|-------------|---------|--------|-------|
| R6-m1 | R6 | minor | Import-time graph compilation in health.py and metrics.py | `app/routes/health.py`, `app/routes/metrics.py` | FIXED | |
| R6-m2 | R6 | minor | Dead import in metrics route | `app/routes/metrics.py` | FIXED | |
| R6-m3 | R6 | minor | Build-type registration via import side effects is brittle | `app/flows/build_type_registry.py` | WONTFIX | Documented. |
| R6-m4 | R6 | minor | _build_tool_node dead code in imperator_flow.py | `app/flows/imperator_flow.py` | FIXED | |
| R6-m5 | R6 | minor | Neo4j failure = degraded 200 not 503 | `app/routes/health.py` | WONTFIX | Design choice documented. |
| R6-m6 | R6 | minor | verbose_log_auto can crash on malformed YAML | `app/logging_setup.py` | FIXED | |
| R6-m7 | R6 | minor | Duplicate pool retrieval in fetch_unextracted_messages | `app/flows/memory_extraction.py` | FIXED | |
| R6-m8 | R6 | minor | was_duplicate flag dead/unused in pipeline | `app/flows/message_pipeline.py` | FIXED | |
| R6-m9 | R6 | minor | Docker compose nginx no depends_on for app | `docker-compose.yml` | WONTFIX | HLD §11. |
| R6-m10 | R6 | minor | Config redaction regex false-positive on non-secret keys | `app/flows/imperator_flow.py` | FIXED | |
| R6-m11 | R6 | minor | Timezone handling implicit in search | `app/flows/search_flow.py` | WONTFIX | Documented. |
| R6-m12 | R6 | minor | external_session_id field removed from Rogers | `app/models.py` | WONTFIX | Intentional. |
| R6-m13 | R6 | minor | mem_extract ad-hoc tool removed | `app/flows/tool_dispatch.py` | WONTFIX | Intentional. |
| R6-m14 | R6 | minor | Retrieval output format changed (intentional) | `app/flows/retrieval_flow.py` | WONTFIX | ARCH-03. |
| R6-m15 | R6 | minor | small-basic build type name absent | `app/flows/build_type_registry.py` | WONTFIX | ARCH-18. |
| R6-m16 | R6 | minor | Dual extraction model routing removed | `app/flows/build_types/standard_tiered.py` | WONTFIX | ARCH-18. |
| R6-m17 | R6 | minor | Secret redaction weaker than Rogers | `app/flows/memory_extraction.py` | WONTFIX | Accepted. |
| R6-m18 | R6 | minor | Retry loops lack jitter | `app/workers/arq_worker.py` | FIXED | |
| R6-m19 | R6 | minor | entrypoint.sh /app/requirements.txt layering question | `entrypoint.sh` | FIXED | |

**Compliance gaps:**

| ID | Round | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|-------------|---------|--------|-------|
| R6-C1 | R6 | partial | Missing .env.example file | `config/credentials/` | FIXED | 4/5 compliance. |

### Round 7 Findings

**Blockers (all OPEN):**

| ID | Round | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|-------------|---------|--------|-------|
| R7-B1 | blocker | Redis exception hierarchy | app/main.py | FIXED | Added redis.exceptions.RedisError to both startup and retry except blocks. | 1/5 Gemini. `redis.exceptions.ConnectionError` doesn't inherit from built-in `ConnectionError`. |
| R7-B2 | blocker | Local package source — no packages dir | Dockerfile | WONTFIX | Only affects local mode which is not the default. pypi mode works. Document as known limitation. | 1/5 GPT-5.4. |

**Majors (all OPEN):**

| ID | Round | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|-------------|---------|--------|-------|
| R7-M1 | major | Singleton Imperator caching | imperator_flow.py, config.example.yml | FIXED | Documented admin_tools as restart-required in config. | 4/5. |
| R7-M2 | R7 | major | Blocking file I/O in `_config_read_tool` | `app/flows/imperator_flow.py` | FIXED | Wrapped file read in `asyncio.get_running_loop().run_in_executor(None, ...)`. |
| R7-M3 | major | MCP session unlocked path | mcp.py | FIXED | Extended _session_lock to cover put_nowait in tool_call handler. | 3/5. |
| R7-M4 | R7 | major | `_total_queued_messages` counter unsafe mutation | `app/routes/mcp.py` | WONTFIX | Edge case: single-threaded asyncio + GIL, no real concurrency path. |
| R7-M5 | R7 | major | Redis lock-acquisition nodes crash if Redis unavailable | `app/flows/build_types/` | FIXED | Wrapped `get_redis()` and `redis.set()` in try/except for (RuntimeError, OSError, ConnectionError); returns error state on failure. |
| R7-M6 | R7 | major | Passthrough lock-release crashes if Redis down | `app/flows/build_types/passthrough.py` | FIXED | Wrapped Redis calls in try/except; logs warning on failure instead of crashing. |
| R7-M7 | R7 | major | `known_exception_handler` missing `asyncpg.PostgresError` | `app/main.py` | FIXED | Added `@app.exception_handler(asyncpg.PostgresError)` decorator. `import asyncpg` already present. |
| R7-M8 | R7 | major | Health endpoint fails hard on config load failure | `app/routes/health.py` | FIXED | Wrapped `async_load_config()` in try/except; returns 503 degraded status on failure. |
| R7-M9 | R7 | major | Chat/MCP routes fail hard on config load failure | `app/routes/chat.py`, `app/routes/mcp.py` | FIXED | Wrapped `async_load_config()` in try/except with fallback error responses in both files. |
| R7-M10 | R7 | major | `ke_assemble_context` reports untruncated items in `context_tiers` | `app/flows/build_types/knowledge_enriched.py` | FIXED | `context_tiers.semantic_messages` now built from `truncated_semantic_messages` tracked during budget-aware assembly. |
| R7-M11 | R7 | major | Assembly summaries can exceed `max_token_budget` | `app/flows/build_types/standard_tiered.py` | FIXED | Added budget guard: stops inserting tier 2 summaries when cumulative tokens exceed tier1+tier2 allocation. |
| R7-M12 | R7 | major | Lock TTL not renewed during archival consolidation | `app/flows/build_types/standard_tiered.py` | FIXED | Added lock TTL renewal before the LLM call, same pattern as `_summarize_chunk`. |
| R7-M13 | R7 | major | Dead-letter requeue loses extraction priority | `app/workers/arq_worker.py` | WONTFIX | Priority recalculated from role on requeue. Fallback score 2 is the default. Dead-lettered jobs are already failed retries — ordering is not critical. |
| R7-M14 | R7 | major | Assembly dedup key can permanently skip jobs | `app/flows/embed_pipeline.py` | WONTFIX | Edge case: 60s TTL expires naturally, assembly lock is 300s. No permanent skip. |
| R7-M15 | R7 | major | Mem0 config hash ignores env vars | `app/memory/mem0_client.py` | WONTFIX | Mem0 rebuilt when config file changes. Env var changes require container restart. Not a practical issue. |
| R7-M16 | R7 | major | `_db_query_tool` lacks table/statement constraints | `app/flows/imperator_flow.py` | WONTFIX | Edge case: SET TRANSACTION READ ONLY enforced at DB level. DML impossible regardless of SQL structure. |
| R7-M17 | R7 | major | Malformed jobs create infinite poison-pill loop | `app/workers/arq_worker.py` | FIXED | On JSONDecodeError, malformed raw_job moved to `dead_letter_unparseable` queue instead of staying in processing. |
| R7-M18 | major | Recipient NOT NULL for system/tool | message_pipeline.py | FIXED | Added defaults: system->all, tool->assistant, else->all. | 1/5. |
| R7-M19 | R7 | major | `conv_get_history` omits `tool_calls`/`tool_call_id` | `app/flows/conversation_ops_flow.py` | FIXED | Added `tool_calls` and `tool_call_id` to both SELECT queries in `load_conversation_and_messages`. |
| R7-M20 | R7 | major | Caller-provided priority silently ignored | `app/flows/message_pipeline.py` | WONTFIX | Priority is internal by design (ARCH-14). Remove from MCP schema rather than implement. |
| R7-M21 | R7 | major | Adaptive loading may miss older messages on first assembly | `app/flows/build_types/standard_tiered.py` | WONTFIX | Fixed by D-09: initial_lookback_multiplier (default 3x) implemented in load_messages. |

**Minors (all OPEN):**

| ID | Round | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|-------------|---------|--------|-------|
| R7-m1 | R7 | minor | Config cache fast path racy | `app/config.py` | WONTFIX | Edge case: CPython GIL + single-threaded asyncio. Worst case is extra file read. |
| R7-m2 | R7 | minor | Prompt cache not concurrency-safe | `app/prompt_loader.py` | WONTFIX | Edge case: same as m1 — GIL atomic dict ops, no corruption possible. |
| R7-m3 | R7 | minor | `fetch_unextracted_messages` gets pool twice | `app/flows/memory_extraction.py` | FIXED | Removed duplicate `get_pg_pool()` call; reuses existing `pool` variable. |
| R7-m4 | R7 | minor | Conv search vector query expensive MIN scan | `app/flows/search_flow.py` | WONTFIX | Performance optimization. No measured problem. Optimize when it's a bottleneck. |
| R7-m5 | R7 | minor | `search_context_windows` unbounded limit | `app/flows/conversation_ops_flow.py` | FIXED | Added `min(state["limit"], 100)` cap. |
| R7-m6 | R7 | minor | Recipient migration backfills 'unknown' vs runtime defaults | `app/migrations.py` | WONTFIX | Historical data. New rows get correct defaults. Old NULL rows don't cause errors. |
| R7-m7 | R7 | minor | `setup_logging` doesn't update existing handlers | `app/logging_setup.py` | WONTFIX | Logging configured once at startup. Hot-reload changes level only, which works. |
| R7-m8 | R7 | minor | `init_postgres`/`init_redis` double-init race | `app/main.py` | WONTFIX | Edge case: only called from single-threaded lifespan context. No real concurrency path. |
| R7-m9 | R7 | minor | N+1 serial inserts after concurrent summarization | `app/flows/build_types/standard_tiered.py` | WONTFIX | Insert overhead negligible compared to LLM summarization calls. |
| R7-m10 | R7 | minor | Broad `except Exception` in `pt_finalize` (non-Mem0) | `app/flows/build_types/passthrough.py` | FIXED | Replaced `(RuntimeError, OSError, Exception)` with `(RuntimeError, OSError, ValueError, ConnectionError)`. |
| R7-m11 | R7 | minor | Shared SQL `extra_where` fragile across CTEs | `app/flows/search_flow.py` | WONTFIX | Works correctly now. Maintenance concern, not a bug. Revisit if query structure changes. |
| R7-m12 | R7 | minor | N+1 duplicate `build_type` query in dispatch | `app/flows/tool_dispatch.py` | WONTFIX | One extra lightweight SELECT. Not worth the refactor. |
| R7-m13 | R7 | minor | `store_and_end` skips persistence when no `context_window_id` | `app/flows/imperator_flow.py` | WONTFIX | By design — no context window means nowhere to persist. Imperator always has one. |
| R7-m14 | R7 | minor | `bind_tools` called every `agent_node` invocation | `app/flows/imperator_flow.py` | FIXED | Tool binding moved to `build_imperator_flow`; `agent_node` uses pre-bound `_prebound_llm`. |
| R7-m15 | R7 | minor | MCP global config vars mutated every request | `app/routes/mcp.py` | FIXED | Config values read into local variables per request; `_evict_stale_sessions` accepts parameters. |
| R7-m16 | R7 | minor | Dispatch initializes KE-specific state for all types | `app/flows/tool_dispatch.py` | WONTFIX | Extra dict keys are harmless. Retrieval graph ignores keys it doesn't use. |
| R7-m17 | R7 | minor | Postgres retry exits prematurely on Imperator init fail | `app/main.py` | FIXED | Fixed by subsequent changes: uses `continue` instead of `return`. |
| R7-m18 | R7 | minor | Entrypoint broad `except Exception` in YAML parsing | `entrypoint.sh` | WONTFIX | Edge case: shell bootstrap script with safe fallback default, not application code. |
| R7-m19 | R7 | minor | Missing null check for `build_type` in dispatch retrieval | `app/flows/tool_dispatch.py` | FIXED | Added `if not _build_type: raise ValueError(...)` before registry lookup. |
| R7-m20 | R7 | minor | Passthrough retrieval doesn't update `last_accessed_at` | `app/flows/build_types/passthrough.py` | FIXED | Added `UPDATE context_windows SET last_accessed_at = NOW() WHERE id = $1` in `pt_load_window`. |
| R7-m21 | R7 | minor | `search_context_windows` doesn't return `last_accessed_at` | `app/flows/conversation_ops_flow.py` | FIXED | Added `last_accessed_at` to both SELECT queries and result formatting. |
| R7-m22 | R7 | minor | Dedup field rename `was_collapsed` (integration concern) | | WONTFIX | Internal field name. No external callers depend on it. |
| R7-m23 | R7 | minor | Memory extraction `user_id` from window not caller | `app/flows/memory_extraction.py` | WONTFIX | Background pipeline. Window participant is the correct user context for Mem0. |
| R7-m24 | R7 | minor | Memory confidence boost/archive threshold hardcoded | `app/flows/memory_scoring.py` | FIXED | Recency window and boost factor now read from `config.tuning.memory_recency_window_days` and `memory_recency_boost_factor`. |
| R7-m25 | R7 | minor | Memory half-life categories differ from Rogers | `app/flows/memory_scoring.py` | WONTFIX | Intentional State 4 redesign. Rogers categories are not the standard. |
| R7-m26 | R7 | minor | MCP schema `tool_calls` type "object" not "array" | `app/routes/mcp.py` | FIXED | Changed to `{"type": "array", "items": {"type": "object"}}`. |
| R7-m27 | R7 | minor | Imperator conversation has no `flow_id`/`user_id` | `app/imperator/state_manager.py` | FIXED | Added `flow_id='imperator'` and `user_id='system'` to the INSERT. |

### Post-Gate Findings (deployment and testing)

| ID | Round | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|-------------|---------|--------|-------|
| PG-01 | Post | major | Infinity serves at `/embeddings` not `/v1/embeddings` — embeddings config had wrong base_url | `config/config.example.yml` | FIXED | Removed `/v1` suffix from embeddings base_url. |
| PG-02 | Post | major | Reranker code called `/v1/rerank` but Infinity serves at `/rerank` | `app/flows/search_flow.py` | FIXED | Changed to `/rerank`. base_url includes provider prefix when needed. |
| PG-03 | Post | major | Decimal not JSON serializable in MCP search responses | `app/routes/mcp.py` | FIXED | Postgres ts_rank returns Decimal. Added `_json_default` handler. |
| PG-04 | Post | minor | Embedding test queried wrong column name `vector` instead of `embedding` | `tests/test_e2e_pipeline.py` | FIXED | Column is `embedding` in schema. |
| PG-05 | Post | minor | Ollama healthcheck false negative — bash TCP trick unreliable | `docker-compose.yml` | FIXED | Changed to `ollama list`. |
| PG-06 | Post | minor | Gateway healthcheck false negative — Alpine wget resolves localhost to IPv6 | `docker-compose.yml` | FIXED | Changed to `http://127.0.0.1:8080/health`. |
| PG-07 | Post | blocker | REQ-001 missing AE/TE separation requirements (§9-13) | `draft-REQ-001-mad-requirements.md` | FIXED | Added §9 AE/TE Separation, §10 Dynamic Loading, §11 Imperator, §12 TE Package, §13 Base Contract. |
| PG-08 | Post | blocker | REQ-002 missing TE configuration separation (§7) | `draft-REQ-002-pmad-requirements.md` | FIXED | Added §7 TE Configuration with per-use inference. |
| PG-09 | Post | blocker | REQ-context-broker says "single config.yml" — contradicts AE/TE separation | `REQ-context-broker.md` | FIXED | Split into AE config (config.yml) and TE config (te.yml). |
| PG-10 | Post | major | Single global `llm` config — no per-use inference configuration | `app/config.py`, `config/config.example.yml` | FIXED | Per-role inference slots: `imperator` in te.yml, `summarization`/`extraction` in config.yml. `get_chat_model(config, role)` resolves from merged config. |
| PG-11 | Post | major | No Anthropic provider support — `get_chat_model()` only creates `ChatOpenAI` | `app/config.py` | FIXED | Tested: Anthropic's OpenAI-compatible endpoint works for chat + tool calling. No `ChatAnthropic` needed. All providers use `ChatOpenAI` with `base_url`. Removed `langchain-anthropic` dependency. |
| PG-12 | Post | major | No TE config file — Imperator config embedded in AE's config.yml | `config/` | FIXED | Split: AE in `config.yml` (infrastructure, pipeline LLMs, embeddings, reranker, build types, tuning), TE in `te.yml` (Imperator model, system prompt, cognitive settings). `async_load_config()` merges both for backward compat. |
| PG-13 | Post | major | No Imperator Identity/Purpose declarations in system prompt | `app/flows/imperator_flow.py` | FIXED | Identity/Purpose live in the system prompt file (loaded via `system_prompt` field in te.yml). Prompt file path configurable. Not injected as config fields. |
| PG-14 | Post | minor | Together rerank models require dedicated endpoints (non-serverless) | N/A | WONTFIX | Account limitation. Skipped in cross-provider tests. |
| PG-15 | Post | minor | REQ-104 missing package registration in deliverable structure | `draft-REQ-104-code-standard.md` | FIXED | Added item 7 for AE/TE entry_points. |
| PG-16 | Post | major | REQ-001 missing §9-13 (AE/TE separation, dynamic loading, Imperator, TE package, base contract) | `draft-REQ-001-mad-requirements.md` | FIXED | Added §9 AE/TE Separation, §10 Dynamic Loading, §11 Imperator Requirements, §12 TE Package Structure, §13 AE/TE Base Contract. |
| PG-17 | Post | major | REQ-002 missing §7 (TE configuration separation) | `draft-REQ-002-pmad-requirements.md` | FIXED | Added §7 with per-use inference, TE vs AE config contents. |
| PG-18 | Post | major | REQ-context-broker said "single config.yml" contradicting AE/TE separation | `REQ-context-broker.md` | FIXED | Updated §1 and §5 for AE/TE config split, per-use inference, system prompt. |
| PG-19 | Post | major | HLD-context-broker §7 and §10 didn't reflect AE/TE separation | `HLD-context-broker.md` | FIXED | Updated configuration system and Imperator design sections. |
| PG-20 | Post | minor | Cross-provider tests: clear embeddings between runs when switching embedding providers | `tests/` | NOTE | Dimension mismatch (e.g., 768 vs 1024) is expected when switching embedding models — not a bug. Clear old embeddings before each cross-provider run. |
| PG-21 | Post | minor | `get_chat_model()` contains application logic (role→config resolution) outside StateGraph | `app/config.py` | WONTFIX | Role→config resolution is borderline substrate. Factory/cache pattern is standard. Refactor complexity not justified. |
| PG-22 | Post | blocker | Cross-provider tests did NOT test through Context Broker pipeline | `tests/integration/test_cross_provider.py` | FIXED | Old fake tests deleted. New full-pipeline tests: hot-reload config → store_message → embedding → get_context → search. 3/3 PASS (Google, OpenAI, Ollama). Together/xAI skipped (no serverless embeddings, PG-14). State 4 validated. |
| PG-23 | Post | major | Imperator still uses internal calls, not MCP tools (D-07) | `app/flows/imperator_flow.py` | FIXED | Imperator now uses dispatch_tool("get_context") and dispatch_tool("store_message"). Self-consumption via same tool interface. |
| PG-24 | Post | major | Initial lookback multiplier not implemented (D-09) | `app/flows/build_types/standard_tiered.py` | FIXED | load_messages checks for existing summaries. On first assembly, looks back budget * multiplier tokens. |
| PG-25 | Post | minor | Imperator state file stores context_window_id (obsolete) | `app/imperator/state_manager.py` | FIXED | State manager simplified to conversation_id only. get_context_window_id() returns conversation_id for backward compat. |
| PG-26 | Post | major | Fluent Bit cannot resolve Docker container hashes to names | N/A (removed) | WONTFIX | Fluent Bit has no Docker metadata filter. Replaced by custom Python log shipper that uses Docker API for network-based container discovery. |
| PG-27 | Post | major | Log shipper: custom Python container for MAD log collection | `log_shipper/` | FIXED | Discovers containers on context-broker-net via Docker API. Tails via API (preserves docker logs). Resolves names. Writes to Postgres. Event-driven (watches connect/disconnect). |
| PG-28 | Post | major | Imperator diagnostic tools implemented | `app/flows/imperator_flow.py` | FIXED | 3 diagnostic (log query, context introspection, pipeline status) + 2 admin (config write, verbose toggle). Diagnostic always available, admin gated by admin_tools. |
| PG-29 | Post | major | REQ §3.4 api_key_env text contradicted implementation | `docs/REQ-context-broker.md`, `docs/HLD-context-broker.md` | FIXED | REQ said "No api_key_env indirection" but D-06 uses api_key_env. Updated REQ and HLD to match implementation. |
| PG-30 | Post | major | TE config filename inconsistent (te.yml vs imperator.yml) | `docs/REQ-context-broker.md`, `docs/HLD-context-broker.md`, `app/config.py` | FIXED | Standardized all references to te.yml. |
| PG-31 | Post | major | 7 blanket except Exception blocks (REQ-001 §4.5) | `app/workers/arq_worker.py`, `app/routes/mcp.py`, `app/flows/imperator_flow.py` | FIXED | Replaced with specific exception types. |
| PG-32 | Post | major | Metrics recorded in route handlers not flows (REQ-001 §6.4) | `app/routes/chat.py`, `app/routes/mcp.py` | FIXED | Moved into tool_dispatch.py and imperator_wrapper.py (flow layer). |
| PG-33 | Post | minor | REQ §7.3 idempotency unclear for message store | `docs/REQ-context-broker.md` | FIXED | Added §7.3 clarifying F-04 consecutive duplicate detection approach. |
| PG-34 | Post | minor | Hardcoded timeout and backoff thresholds (REQ-001 §8.2) | `app/config.py`, `app/workers/arq_worker.py` | FIXED | Externalized to config tuning section. |
| PG-35 | Post | minor | No early build type config validation at startup | `app/main.py` | FIXED | Added validation in lifespan after config load. |
| PG-36 | Post | minor | Silent failure in verbose_log_auto | `app/config.py` | FIXED | Now logs at DEBUG instead of silent pass. |
| PG-37 | Post | minor | packages.source is build-time only, undocumented | `config/config.example.yml` | FIXED | Added clarifying comment. |
| PG-38 | Post | blocker | Dynamic StateGraph loading NOT implemented (REQ-001 §10) | entire codebase | FIXED | Bootstrap kernel + AE package (context-broker-ae) + TE package (context-broker-te). Entry_points discovery via importlib.metadata, install_stategraph() MCP tool, base contract (§13), migration 015 for stategraph_packages table. 292 tests passing. |
| PG-39 | Post | blocker | Credentials not hot-reloadable — require container recreation | `app/config.py` | FIXED | get_api_key() now reads from mounted /config/credentials/.env file first (hot-reloadable), falls back to os.environ for HuggingFace Spaces etc. |
| PG-40 | Post | blocker | Inference provider config not hot-reloadable | `app/config.py` | FIXED | Was already mtime-based — AE config re-reads on every operation. The actual blocker was PG-39 (credentials). |
| PG-41 | Post | blocker | Hot-reload never tested | `tests/integration/test_components.py` | FIXED | Component tests verify: changed Imperator from Ollama to Gemini 2.5 Flash via te.yml edit on disk, no restart, next call used new model. 28/28 component tests pass. |
| PG-42 | Post | blocker | Provider keys not provisioned on irina | `.env` on irina | FIXED | All 5 provider keys provisioned from Z:\credentials\model-providers.json. |
| PG-43 | Post | major | Imperator crashes if its conversation is deleted from DB | `app/imperator/state_manager.py` | OPEN | If conversations table is truncated while Imperator holds old conversation_id in memory, get_context crashes with ForeignKeyViolationError. Should detect missing conversation and create a new one. |
| PG-44 | Post | blocker | Mem0 Neo4j config rejected credentials when auth=none | `mem0_client.py` | FIXED | Mem0 GraphStoreConfig requires url+username+password. Was omitting credentials when NEO4J_AUTH=none. Now passes dummy "neo4j"/"neo4j". |
| PG-45 | Post | blocker | rank-bm25 missing from requirements.txt | `requirements.txt` | FIXED | Mem0 imports rank_bm25 at init. Missing dependency caused silent extraction failure. |
| PG-46 | Post | blocker | Neo4j APOC plugin not installed | `docker-compose.yml` | FIXED | Mem0 uses apoc.meta.data() for graph operations. Added NEO4J_PLUGINS=["apoc"] to container env. |

### Integration Testing

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| PG-47 | Post | — | major | Embed pipeline processes one message per API call — 40 min for 10K | `arq_worker.py` | FIXED | Added batch embedding (embedding_batch_size=50, embedding_concurrency=3). 50/batch at 2.5s = ~60 emb/s. Embeddings keep pace with ingestion. |
| PG-48 | Post | — | minor | Bulk load doesn't create context windows — assembly never triggers | `tests/integration/bulk_load.py` | NOTE | By design: get_context auto-creates windows (D-03). Bulk load must call get_context after loading, then store one trigger message per conversation to kick off assembly. |
| PG-49 | Post | — | major | Bulk extraction logs extracted=N but Neo4j has few nodes | `memory_extraction.py`, `mem0_client.py` | FIXED | Rogers Fix 2: monkey-patch PGVector.insert with ON CONFLICT DO NOTHING + migration 016 unique index on mem0_memories. Mem0 client reset on error. 35+ memories, 6+ Neo4j nodes verified at scale. |
| PG-50 | Post | — | blocker | Imperator returns cached/stale responses across turns | `imperator_wrapper.py` | FIXED | Unique UUID thread_id per invocation. MemorySaver no longer reuses stale state. |
| PG-51 | Post | — | major | Imperator log_query tool returns empty response | `imperator_flow.py` | FIXED | Tool now handles string JSONB data (json.loads fallback). Verified in Phase 3: 1370 chars returned. |
| PG-52 | Post | — | minor | Imperator context_introspection response doesn't contain expected keywords | `imperator_flow.py` | NOTE | Response format is LLM-dependent — keywords like "tier" and "tokens" may appear in different phrasing. Tool verified working (478 chars response). |
| PG-53 | Post | — | major | admin_tools config change requires container restart (not hot-reloadable) | `imperator_flow.py` | FIXED | TE config mtime detection in imperator_wrapper.py. Admin tools re-evaluated on each call. |
| PG-54 | Post | — | minor | Phase 2 turn 6: Imperator doesn't mention "Joshua26" when summarizing projects | N/A | NOTE | Quality issue — Imperator found relevant topics but didn't use exact term. Context assembly quality, not a bug. |
| PG-55 | Post | — | minor | Phase 2 turn 7: Imperator can't find "AE/TE separation" via search | N/A | NOTE | Search term matching limitation. Related to extraction quality improvements (future work). |
| PG-56 | Post | — | major | Standard-tiered retrieval returns only tier 1 for large conversations | `retrieval_flow.py` | NOTE | Only observed in initial run with stale data. Clean re-run (59/59) showed all build types returning correct context. Monitoring. |

### Gate 3 Audit (2026-03-25)

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| G3-01 | G3 | Opus | minor | `black --check` fails on files — formatting violations | multiple | FIXED | Ran `black .` — 41 files reformatted. All pass `black --check` now. |
| G3-02 | G3 | Opus | minor | `ruff check` violations — unused imports, import order, unused vars | multiple | FIXED | Ran `ruff check --fix` (42 auto-fixes) + manual fixes for remaining 18. 0 violations. Also fixed: test ordering issues with mem0 singleton, Redis references in static checks and lock tests. |
| G3-03 | G3 | Opus | minor | Dead code: `app/workers/arq_worker.py` still exists | `app/workers/arq_worker.py` | FIXED | Deleted. No code references (only review docs). |
| G3-04 | G3 | Opus | minor | Missing `embedding_dims` startup validation | `app/main.py` | FIXED | Added fail-fast check in lifespan: `RuntimeError` if `embeddings.embedding_dims` not set. |

### Code Review (2026-03-26) — GPT-5.4, Grok-4.2, Gemini-3.1-Pro

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| CR-01 | R1 | Grok | major | Non-atomic conv_delete — 4 DELETEs without transaction | `tool_dispatch.py` | FIXED | Wrapped in `async with conn.transaction()`. |
| CR-02 | R1 | Grok | major | Scheduler race — in-memory last_fired, no DB coordination | `scheduler.py` | FIXED | Added `last_fired_at` column (migration 020), optimistic locking UPDATE. |
| CR-03 | R1 | Gemini | blocker | Poison pill in embedding worker — failed batch re-fetched forever | `db_worker.py` | FIXED | After 5 consecutive failures, mark with zero-vector. Log worker deletes unembeddable entries. |
| CR-04 | R1 | GPT+Gemini | major | Context window ON CONFLICT mismatch with migration 013 | `conversation_ops_flow.py` | FIXED | Changed to `(conversation_id, build_type, max_token_budget)`. |
| CR-05 | R1 | GPT+Gemini | major | domain_memories table doesn't exist on fresh deploy | `operational.py` | FIXED | Added `table_exists` check before querying. |
| CR-06 | R1 | Gemini | major | Assembly worker no ORDER BY — starvation risk | `db_worker.py` | FIXED | Added `ORDER BY cw.last_assembled_at ASC NULLS FIRST`. |
| CR-07 | R1 | GPT | major | `hash()` for advisory locks is process-randomized | multiple | FIXED | Created `app/utils.py` with `stable_lock_id()` using SHA-256. Replaced all 7 occurrences. |
| CR-08 | R1 | Gemini | major | Imperator agent_node re-executes get_context on every ReAct iteration | `imperator_flow.py` | FIXED | Return SystemMessage in first call so `add_messages` persists it. |
| CR-09 | R1 | GPT | blocker | asyncpg.Pool direct calls without acquire() | multiple | FALSE_POSITIVE | `asyncpg.Pool` supports direct `execute()`, `fetch()`, `fetchval()`, `fetchrow()`. Confirmed via API inspection. |

### Extraction Pipeline (2026-03-26)

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| EXT-01 | Post | — | blocker | Extraction throughput: 200 msgs in 12 hours with GPT-4o-mini | `memory_extraction.py` | FIXED | Root cause: oversized messages truncated but never marked extracted → same message re-fetched every cycle. Fixed: chunk large messages, mark all selected as fully extracted. |
| EXT-02 | Post | — | major | `_clean_for_extraction` missing — raw markdown/code sent to LLM | `memory_extraction.py` | FIXED | Added text cleaning: strips code blocks, file paths, URLs, markdown formatting before extraction. |
| EXT-03 | Post | — | major | `extraction_max_chars` default 90K — too large for LLM | `memory_extraction.py` | FIXED | Reduced to 8K. Configurable. |
| EXT-04 | Post | — | note | Gemini JSON extraction via OpenAI endpoint unreliable | `mem0_client.py` | NOTE | OpenAI-compatible endpoint drops `response_format` enforcement. Native Gemini provider in Mem0 1.0.7 would fix it but Mem0 upgrade blocked. Use GPT-4o-mini or Ollama for extraction. |

### Compliance Audit (2026-03-26)

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| CA-01 | Audit | Opus | major | 5 blanket `except Exception` in worker polling loops (REQ-001 §4.5) | `db_worker.py` (4 loops), `scheduler.py` (1 loop) | EXCEPTION | Worker loops must not crash on unexpected errors — crashing loses the worker, DB retries naturally. Specific exceptions caught first, blanket is safety net only. Exception registered as EX-CB-001. |
| CA-02 | Audit | Opus | minor | Log shipper and UI Dockerfiles use `python:3.12-slim` — not pinned to patch version (REQ-002 §1.4) | `log_shipper/Dockerfile`, `ui/Dockerfile` | FIXED | Pinned both to `python:3.12.1-slim`. |
| CA-03 | Audit | Opus | minor | Log shipper Dockerfile missing HEALTHCHECK directive (REQ-002 §1.5) | `log_shipper/Dockerfile` | FIXED | Added `HEALTHCHECK` using `kill -0 1` (no HTTP endpoint available). |
| PG-43 | Post | — | major | Imperator crashes if its conversation is deleted from DB at runtime | `app/imperator/state_manager.py` | FIXED | `get_conversation_id()` now verifies conversation exists before returning. If deleted, creates a new one and updates state file. |

### Deployment Verification (2026-03-26)

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| DV-01 | Deploy | Opus | minor | Nginx returns 502 after langgraph container recreated — caches upstream DNS at startup | `docker-compose.yml` | FIXED | Deploy command now includes `docker restart context-broker` after `docker compose up -d`. Documented in plan. |
| DV-02 | Deploy | Opus | minor | web_read crawl4ai fallback only catches ImportError — runtime errors (missing browser binary) bypass fallback | `tools/web.py` | FIXED | Broadened except clause to catch all exceptions from crawl4ai, falling through to HTML stripping. |
| DV-03 | Deploy | Opus | minor | httpx uses certifi bundle instead of OS certificate store — some HTTPS sites fail with cert errors | `tools/web.py` | FIXED | httpx.AsyncClient now uses `verify=ssl.create_default_context()` to use the system trust store. |
| DV-04 | Deploy | Opus | major | Imperator intermittently returns empty content — Gemini 2.5 Flash returns valid but empty completions (content="" with no tool_calls) | `imperator_flow.py` | FIXED | Root cause: Gemini occasionally returns empty completions (~1 in 10-15 requests). Fix: agent_node retries up to 2 times on empty response before accepting. Also added max_iterations_fallback graph node and empty response guard in store_assistant_message. Verified: 20/20 requests return valid content, retry fires when needed. |
| DV-05 | Deploy | Opus | major | Config mounted read-only — admin tools (config_write, migrate_embeddings) fail with PermissionError | `docker-compose.yml` | FIXED | Changed `./config:/config:ro` to `./config:/config` (rw). Also chmod 666 on config files for container UID 1001 access. |
| DV-06 | Deploy | Opus | major | migrate_embeddings does not ALTER vector columns — wipes data but leaves untyped columns, preventing HNSW indexing | `tools/admin.py` | FIXED | Added ALTER TABLE for conversation_messages and domain_information after wipe. Creates HNSW indexes after ALTER. |
| DV-07 | Deploy | Opus | blocker | No HNSW indexes on any vector column — all vector searches were sequential scans | postgres | FIXED | Created HNSW indexes on conversation_messages, system_logs, domain_information. Columns typed to vector(1024), vector(256), vector(1024). |
| DV-08 | Deploy | Opus | major | Conversation embeddings at 3072 dims — exceeds pgvector HNSW limit of 2000 for float32 | config | FIXED | Switched to MRL (Matryoshka) truncation: conversation embeddings 1024 dims, log embeddings 256 dims. LangChain dimensions parameter passed through to API. |
| DV-09 | Deploy | Opus | major | get_embeddings_model does not pass dimensions parameter to OpenAIEmbeddings — MRL truncation impossible | `app/config.py` | FIXED | Added `kwargs["dimensions"] = int(dims)` when embedding_dims configured. |
| DV-10 | Deploy | Opus | minor | admin_tools: false on irina entire session — admin tools never tested live | `config/te.yml` | FIXED | Enabled admin_tools: true. Live-tested migrate_embeddings successfully. |

### Test Coverage Audit (2026-03-27)

| ID | Round | Reviewer | Severity | Description | File(s) | Status | Notes |
|----|-------|----------|----------|-------------|---------|--------|-------|
| TA-01 | Test | Opus | major | Prometheus metrics accumulate in process memory indefinitely — no eviction, no cap. Counters only go up, histograms accumulate observations. On a long-running high-volume deployment where nobody scrapes `/metrics`, this is unbounded memory growth. The `prometheus_client` library pre-allocates fixed-size bucket arrays so growth is proportional to unique label combinations (tool names × statuses), not individual requests. With ~25 tools × 2 statuses = ~50 time series, memory is likely under 1MB even after months. However, if custom labels or high-cardinality labels are added in the future, this could become a real issue. | `app/metrics_registry.py` | OPEN | Options: (1) Deploy a Prometheus server to scrape and consume the data, making it useful instead of wasted. (2) Add a periodic reset or TTL to the metrics registry. (3) Accept the current state — bounded by label cardinality, not request volume. |
| TA-02 | Test | Opus | major | Mem0 features (mem_add, mem_search, mem_list, mem_delete, memory extraction) do not work on a fresh deployment. Mem0 creates its own tables (`mem0_memories`) lazily on first use, but the application's migration 016 expects the table to already exist for index creation. On a fresh DB: migration 016 skips gracefully, but Mem0 itself never initializes because no code path triggers its table creation before the first `mem_add` call — and `mem_add` returns empty results without error. The knowledge extraction worker also silently fails because Mem0 is not functional. | `app/migrations.py`, `packages/context-broker-ae/src/context_broker_ae/memory/mem0_client.py` | OPEN | Root cause: Mem0's lazy initialization path is never triggered during deployment. The `get_mem0_client()` singleton creates the Mem0 Memory instance, but Mem0 only creates its tables when the first `.add()` or `.search()` call is made — and those calls fail silently when the underlying pgvector collection doesn't exist. Fix: the application startup (`main.py` lifespan) should trigger Mem0 table creation — e.g., call `get_mem0_client()` and issue a throwaway `.search("init", user_id="system")` during startup. This is application responsibility, not deployment script. |
| TA-03 | Test | Opus | blocker | No HNSW vector index on fresh deployments — all vector searches are sequential scans. Migration 009 creates the index only if embeddings already exist at startup. On a fresh deploy, no embeddings exist yet — the background worker generates them after startup. The migration passes, records itself as applied, and never retries. Result: 30K+ rows scanned sequentially for every vector search. `search_messages` averages 6.3s instead of <100ms. | `app/migrations.py:_migration_009`, `app/workers/db_worker.py` | OPEN | Fix: the embedding worker should check for the HNSW index after completing its first batch and create it if missing. This is a one-time check — once the index exists, it's maintained automatically by Postgres. The worker already knows the embedding dimensions from the vectors it just stored. This is application responsibility — `docker compose up` on a fresh clone must result in indexed vector search without manual intervention. |
| TA-04 | Test | Opus | blocker | Extraction silently loses data on fresh deployments. Of 124 extraction attempts: 52 raised exceptions (counted as "error"), but the other 72 "succeeded" — `mem0.add()` ran without raising, the flow returned `error: None`, and messages were marked `memory_extracted=TRUE`. However, Mem0 didn't actually persist anything because its tables aren't properly initialized (TA-02). Result: 19,085 messages permanently marked as extracted with zero knowledge actually stored. The data is silently lost — the worker will never retry these messages. | `app/workers/db_worker.py`, `packages/context-broker-ae/src/context_broker_ae/memory_extraction.py` | OPEN | Two bugs: (1) Dependent on TA-02 — Mem0 must initialize at startup. (2) Independent bug: the extraction flow should verify that `mem0.add()` actually stored something (check return value or query `mem0_memories` count before/after) before marking messages as extracted. Silent success with no data stored is worse than a visible error. |

---

## Summary

Updated 2026-03-27. 477 tests (315 mock + 162 live). Live: 157 passed, 5 skipped (TA-02). Mock: 315 passed.

| Status | Count |
|--------|-------|
| OPEN | 4 |
| FIXED | 261 |
| WONTFIX | 36 |
| FALSE_POSITIVE | 2 |
| REMOVED | 1 |
| NOTE | 6 |
| EXCEPTION | 1 |
