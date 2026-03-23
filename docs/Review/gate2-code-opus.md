# Gate 2 Pass 1 — Code Review (Claude Opus 4.6)

**Date:** 2026-03-22
**Reviewer:** Claude Opus 4.6 (1M context)
**Scope:** Full source code review of the Context Broker implementation against REQ-context-broker.md, REQ-001, and REQ-002.

---

## Findings

### CB-OPUS-001 — Sequence number race condition under concurrent inserts

- **File:** `app/flows/message_pipeline.py`, `store_message()`, line 105-113
- **Severity:** Blocker
- **Description:** The sequence number is assigned via a subquery `SELECT COALESCE(MAX(sequence_number), 0) + 1` inside the INSERT. Under concurrent transactions inserting into the same conversation, two transactions can both read the same MAX value and assign the same sequence_number. The unique constraint on `(conversation_id, sequence_number)` does not exist in the schema (`init.sql` only has a non-unique index on `idx_messages_conversation`), so duplicate sequence numbers will be silently inserted. This corrupts message ordering, breaks tier boundary calculations in context assembly, and violates idempotency. Fix: add a `UNIQUE` constraint on `(conversation_id, sequence_number)` in the schema, and use `SELECT ... FOR UPDATE` or an advisory lock, or use a `SERIAL`/`GENERATED ALWAYS AS IDENTITY` column scoped to a sequence per conversation.

### CB-OPUS-002 — Postgres password not resolved from Docker secret

- **File:** `app/database.py`, `init_postgres()`, line 31
- **Severity:** Blocker
- **Description:** `docker-compose.yml` configures PostgreSQL to read its password from a Docker secret file (`POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password`). However, `init_postgres()` reads `os.environ.get("POSTGRES_PASSWORD", "")`. The `POSTGRES_PASSWORD` environment variable is never set in the compose file for the langgraph container. The `.env` file loaded via `env_file` may or may not contain it. Meanwhile, the postgres container uses `POSTGRES_PASSWORD_FILE` (a Docker secrets mechanism), not `POSTGRES_PASSWORD`. This means the langgraph container will connect with an empty password, which will fail authentication against the postgres container. The langgraph container needs `POSTGRES_PASSWORD` set in its environment (e.g., by reading the same secret file at startup) or the compose file needs to pass the password explicitly.

### CB-OPUS-003 — Neo4j AUTH set to `none` but Mem0 client passes a password

- **File:** `docker-compose.yml` line 95, `app/memory/mem0_client.py` line 72, 112-113
- **Severity:** Major
- **Description:** `docker-compose.yml` sets `NEO4J_AUTH=none`, which disables authentication on the Neo4j container. However, `mem0_client.py` reads `NEO4J_PASSWORD` from the environment and passes it in the `GraphStoreConfig`. If `NEO4J_PASSWORD` is empty (which it will be since auth is disabled), the Mem0 Neo4j driver may fail to connect depending on how it handles empty passwords with `bolt://` protocol. More critically, if auth is `none`, the username `neo4j` with any password should work, but some driver versions reject empty-string passwords. The Mem0 `GraphStoreConfig` should either omit the password field or use a sentinel like `"none"`. This is a deployment-time failure risk.

### CB-OPUS-004 — `ChatOpenAI` parameter `openai_api_base` is deprecated / wrong name

- **File:** `app/flows/context_assembly.py` lines 228, 345; `app/flows/imperator_flow.py` line 141; `app/flows/embed_pipeline.py` line 99; `app/flows/search_flow.py` lines 55, 157; `app/flows/retrieval_flow.py` line 223
- **Severity:** Major
- **Description:** The `ChatOpenAI` constructor is called with `openai_api_base=...`. In `langchain-openai==0.0.8` (pinned in requirements.txt), the correct parameter is `base_url` (as of LangChain 0.1.x). The `openai_api_base` parameter was used in older LangChain versions. Depending on the exact version of `langchain-openai==0.0.8`, this may be silently ignored (defaulting to `https://api.openai.com/v1`) or raise a deprecation warning. If it is ignored, all LLM calls will go to OpenAI instead of the configured local Ollama endpoint, which is a critical misconfiguration. Similarly, `OpenAIEmbeddings` uses `openai_api_base` which has the same issue. Verify the correct parameter name for the pinned version and use it consistently.

### CB-OPUS-005 — `OpenAIEmbeddings` passes `openai_api_key=""` which may trigger auth errors

- **File:** `app/flows/embed_pipeline.py` line 100; `app/flows/search_flow.py` lines 56, 159; `app/flows/retrieval_flow.py` line 224
- **Severity:** Major
- **Description:** When no API key is configured (e.g., local Ollama), `get_api_key()` returns `""`. This empty string is passed as `openai_api_key=""` to `OpenAIEmbeddings` and `ChatOpenAI`. The OpenAI Python client (and LangChain's wrapper) may reject an empty string as an invalid API key and raise an `AuthenticationError` before making any HTTP request. For Ollama (which doesn't require authentication), the key should be set to a non-empty dummy value like `"not-needed"` or the parameter should be omitted entirely. This will cause runtime failures for any deployment using local Ollama.

### CB-OPUS-006 — Embedding dimension mismatch between schema and Ollama models

- **File:** `postgres/init.sql` line 55; `app/memory/mem0_client.py` lines 120-139
- **Severity:** Major
- **Description:** The `conversation_messages.embedding` column is declared as `vector(1536)`, hardcoded for OpenAI's embedding dimensions. The default config uses Ollama with `nomic-embed-text`, which produces 768-dimensional embeddings. Attempting to store a 768-dimension vector in a `vector(1536)` column will fail with a PostgreSQL error. The `_get_embedding_dims` lookup table in `mem0_client.py` also only maps OpenAI models and defaults to 1536. The schema's vector dimension must match the configured embedding model, or the column should be untyped (`vector` without a dimension), though this prevents IVFFlat indexing. This needs to be configurable or the init.sql needs to use a migration that sets the dimension based on config.

### CB-OPUS-007 — `check_idempotency` queries directly on pool instead of using connection from pool

- **File:** `app/flows/message_pipeline.py`, `check_idempotency()`, line 59
- **Severity:** Minor
- **Description:** `pool.fetchrow()` is used directly (which acquires and releases a connection). Then in `store_message()`, another connection is acquired for the transaction. Between the idempotency check and the insert, another concurrent request could insert the same idempotency key. The check-then-insert is not atomic across these two nodes. The idempotency INSERT will succeed (since there's no ON CONFLICT for the message INSERT itself) and the unique index on `idempotency_key` will catch duplicates at the database level, but the flow will get an unhandled `asyncpg.UniqueViolationError` which is not in the exception handler. The idempotency check should be combined with the insert using `INSERT ... ON CONFLICT` or the `UniqueViolationError` should be caught in `store_message`.

### CB-OPUS-008 — `_stream_imperator_response` sets `status = "success"` before streaming completes

- **File:** `app/routes/chat.py`, lines 92-98
- **Severity:** Major
- **Description:** In the streaming path, `status = "success"` is set at line 93 and the `StreamingResponse` is returned immediately. The `finally` block then records the metric with duration=~0 and status="success" before the actual streaming has even started. If the streaming generator fails partway through, the metric will have already recorded success. The status and duration metrics for streaming requests are therefore always wrong. The metric recording needs to happen inside the generator or after it completes.

### CB-OPUS-009 — Streaming response is not actually streamed (full response buffered first)

- **File:** `app/routes/chat.py`, `_stream_imperator_response()`, line 147
- **Severity:** Major
- **Description:** The streaming generator calls `await _imperator_flow.ainvoke(initial_state)` which blocks until the entire LLM response is generated, then splits the result into words and yields them one by one. This is a fake streaming implementation -- the client sees the first token only after the full response is complete, defeating the purpose of streaming. True streaming requires using `astream` or `astream_events` on the LangGraph flow, or using LangChain's streaming callbacks. This violates user expectations from the OpenAI streaming API contract.

### CB-OPUS-010 — MCP SSE session dict `_sessions` is a module-level dict with no cleanup on crash

- **File:** `app/routes/mcp.py`, line 35
- **Severity:** Minor
- **Description:** `_sessions` is a plain dict that grows unboundedly if clients disconnect without the `finally` block executing (e.g., server crash, ungraceful connection drops). There is no TTL-based cleanup or periodic sweep. In long-running deployments, this could leak memory. Also, `request.is_disconnected()` may not reliably detect disconnections in all ASGI servers. Consider adding a max session count or TTL-based eviction.

### CB-OPUS-011 — `_handle_job_failure` uses exponential backoff with `asyncio.sleep` that blocks the consumer

- **File:** `app/workers/arq_worker.py`, `_handle_job_failure()`, line 191
- **Severity:** Major
- **Description:** `backoff = 5 ** (attempt - 1)` means attempt 2 sleeps 5s, attempt 3 sleeps 25s, attempt 4 sleeps 125s. This `asyncio.sleep(backoff)` blocks the entire consumer loop for that queue for up to 125 seconds during the third retry. During this time, no other jobs in that queue will be processed. The sleep should be removed and instead the job should be re-queued with a scheduled execution time, or use a separate delayed queue, or at minimum cap the backoff to a reasonable maximum (e.g., 30s). Also, this blocking sleep in an async function violates the spirit of REQ-001 5.1 (no blocking in async context) -- while `asyncio.sleep` is technically non-blocking, it blocks the consumer's logical progress.

### CB-OPUS-012 — Dead-letter sweep resets attempt counter, creating infinite retry loops

- **File:** `app/workers/arq_worker.py`, `_sweep_dead_letters()`, line 269
- **Severity:** Major
- **Description:** `_sweep_dead_letters` sets `job["attempt"] = 1` before re-queuing dead-lettered jobs. This means a job that exhausted all retries and was dead-lettered will be re-queued with `attempt=1`, go through all retries again, get dead-lettered again, get swept again, and so on forever. A permanently failing job will cycle through this loop indefinitely, consuming resources. Dead-lettered jobs should either require manual intervention, have a maximum sweep count, or the sweep should increment a `sweep_count` field and stop after a configurable limit.

### CB-OPUS-013 — `run_mem0_extraction` returns empty dict on success, dropping state

- **File:** `app/flows/memory_extraction.py`, `run_mem0_extraction()`, line 167
- **Severity:** Major
- **Description:** On success, this function returns `{}` (empty dict). LangGraph merges returned dicts into state, so this effectively provides no updates. The next node `mark_messages_extracted` then reads `state["selected_message_ids"]` which was set by the previous `build_extraction_text` node and should still be in state. However, the `error` field is not explicitly cleared. If a previous node set `error` and this node succeeds, the stale `error` value persists. The `route_after_extraction` function checks `state.get("error")` and will route to `release_extraction_lock` instead of `mark_messages_extracted`, skipping the marking step even though extraction succeeded. This should return `{**state, "error": None}` or at minimum `{"error": None}`.

### CB-OPUS-014 — `fetch_unextracted_messages` accesses `window["participant_id"]` without None check

- **File:** `app/flows/memory_extraction.py`, `fetch_unextracted_messages()`, line 86
- **Severity:** Minor
- **Description:** If no context window exists for the conversation (e.g., messages stored but no window created yet), `window` will be `None`. The code handles this with a fallback to `"default"`, which is correct. However, the variable `pool2 = get_pg_pool()` on line 81 is redundant -- it's the same pool as `pool` on line 64. This is harmless but wasteful.

### CB-OPUS-015 — `broker_chat` tool dispatch rebuilds imperator flow on every call

- **File:** `app/flows/tool_dispatch.py`, lines 270-273
- **Severity:** Minor
- **Description:** Unlike all other tools which use pre-compiled flows, the `broker_chat` handler calls `build_imperator_flow()` on every invocation. This recompiles the StateGraph and creates a new MemorySaver checkpointer each time, meaning conversation state is never persisted across calls. The checkpointer in `imperator_flow.py` is a module-level singleton, so the flow will share the same checkpointer, but the flow compilation itself is unnecessary overhead on every call. The flow should be compiled once like the others.

### CB-OPUS-016 — `ChatOpenAI` constructed with `openai_api_key=""` raises error

- **File:** `app/flows/context_assembly.py` line 229, `app/flows/imperator_flow.py` line 142
- **Severity:** Major
- **Description:** Same root cause as CB-OPUS-005 but for `ChatOpenAI`. When `api_key` returns `""`, passing `openai_api_key=""` to `ChatOpenAI` will cause the underlying openai client to raise `openai.OpenAIError: API key must not be empty`. This will break all summarization, consolidation, and Imperator flows when using a provider that doesn't require an API key (like Ollama). A dummy key like `"not-needed"` should be used when the key is empty.

### CB-OPUS-017 — `sentence_transformers` not in `requirements.txt`

- **File:** `app/flows/search_flow.py`, line 333; `requirements.txt`
- **Severity:** Major
- **Description:** The `rerank_results` function imports `from sentence_transformers import CrossEncoder` when the reranker provider is `cross-encoder`. However, `sentence-transformers` is not listed in `requirements.txt`. This will fail with `ImportError` at runtime when a user configures `reranker.provider: cross-encoder` (which is the default in `config.example.yml`). Either add `sentence-transformers` to requirements.txt with a pinned version, or change the default reranker to `none`.

### CB-OPUS-018 — Config is captured at startup and passed by reference, defeating hot-reload

- **File:** `app/main.py` lines 33, 49; `app/routes/chat.py` line 63; `app/routes/mcp.py` line 162
- **Severity:** Major
- **Description:** In `lifespan()`, `config = load_config()` is called once and stored as `application.state.config`. Routes then read `request.app.state.config` which is the same dict loaded at startup. Since `load_config()` is supposed to be called per-operation for hot-reload (per REQ 5.1), storing it once at startup means config changes are never picked up. The routes should call `load_config()` directly on each request for hot-reloadable settings. The startup config should only be used for infrastructure settings. Similarly, the background worker receives `config` once at startup and never re-reads it.

### CB-OPUS-019 — `global_exception_handler` catches `Exception` (blanket handler)

- **File:** `app/main.py`, lines 80-93
- **Severity:** Minor
- **Description:** REQ-001 4.5 and REQ-context-broker 6.7 state "No blanket `except Exception:` or `except:` blocks." The global exception handler at the FastAPI level catches all `Exception` types. However, a global exception handler is arguably a reasonable architectural choice at the framework boundary to prevent 500 errors from leaking stack traces. This is a judgment call -- the intent of the requirement is to prevent blanket catches that silently swallow errors inside business logic. The handler does log the full exception with traceback, so it's not silently swallowing. Flagging as minor since it's borderline.

### CB-OPUS-020 — `check_idempotency` returns `{**state, ...}` which copies the entire state dict

- **File:** `app/flows/message_pipeline.py`, lines 56, 74, 82; and many other nodes across all flows
- **Severity:** Minor
- **Description:** Nearly every StateGraph node returns `{**state, "key": value}` which creates a complete copy of the state dict. Per REQ-001 2.2 and REQ-context-broker 4.6, "Each node returns a new dictionary containing only updated state variables." The nodes should return only the changed fields (e.g., `return {"was_duplicate": False}` instead of `return {**state, "was_duplicate": False}`). While LangGraph's `StateGraph` handles merging partial updates, spreading the full state is wasteful and technically violates the stated requirement. With large message lists in state, this creates unnecessary memory pressure.

### CB-OPUS-021 — No `asyncio` import at module level in `retrieval_flow.py`

- **File:** `app/flows/retrieval_flow.py`, line 1-15
- **Severity:** Blocker
- **Description:** The file imports `asyncio` at line 8, which is used in `inject_knowledge_graph` at line 304. Checking... actually `asyncio` is imported at line 8. This is fine. Withdrawing this finding.

### CB-OPUS-022 — `_consume_queue` uses `rpop` which loses jobs on crash

- **File:** `app/workers/arq_worker.py`, `_consume_queue()`, line 219
- **Severity:** Major
- **Description:** `redis.rpop(queue_name)` removes the job from the queue atomically. If the process crashes after `rpop` but before `processor(job, config)` completes, the job is lost. This violates REQ-context-broker 7.1 ("The conversation record is never lost due to a downstream processing failure"). The standard pattern for at-least-once delivery is `BRPOPLPUSH` (or `BLMOVE` in Redis 6.2+) to atomically move the job to a processing queue, then remove it after successful processing. Alternatively, use a proper job queue library (the module is titled "ARQ" but doesn't actually use the `arq` library listed in requirements.txt).

### CB-OPUS-023 — ARQ library imported but never used

- **File:** `requirements.txt` line 24; `app/workers/arq_worker.py`
- **Severity:** Minor
- **Description:** `arq==0.25.0` is listed in requirements.txt and the file is named `arq_worker.py`, but the actual implementation uses raw Redis `lpush`/`rpop` instead of the ARQ library's job scheduling, retry, and worker infrastructure. This is misleading and misses the retry/backoff/cron features that ARQ provides out of the box. Either use the ARQ library properly or remove it from requirements and rename the module.

### CB-OPUS-024 — Conversation search with embeddings scans all messages (no conversation filter)

- **File:** `app/flows/search_flow.py`, `search_conversations_db()`, lines 74-90
- **Severity:** Minor
- **Description:** The vector search query joins `conversations` with `conversation_messages` and computes `MIN(embedding <=> query)` grouped by conversation. This scans all messages with embeddings across all conversations to find the best match per conversation. With large datasets, this is an expensive query. The IVFFlat index on `embedding` helps with ANN retrieval, but the `GROUP BY` forces a full join. Consider limiting the vector search to top-N messages first, then joining back to conversations.

### CB-OPUS-025 — SQL injection risk via f-string interpolation in search queries

- **File:** `app/flows/search_flow.py`, `hybrid_search_messages()`, lines 193-268
- **Severity:** Minor
- **Description:** The `rrf_k` and `candidate_limit` values are interpolated into SQL via f-strings (e.g., `f"LIMIT {candidate_limit}"`). These values come from `get_tuning()` which reads from config.yml. While not directly user-controllable (config is mounted read-only), if an attacker can modify config.yml they can inject SQL. However, `get_tuning` returns the raw YAML value, and `int()` is called on line 179-180, which would raise `ValueError` on non-numeric input. The `int()` cast provides adequate protection. Flagging as minor for defense-in-depth -- parameterized queries are always preferred.

### CB-OPUS-026 — `logging_setup.py` ignores config.yml log level

- **File:** `app/logging_setup.py`, `setup_logging()`, lines 55-63
- **Severity:** Minor
- **Description:** `setup_logging()` hardcodes `logging.INFO` as the log level. The config.yml has a `log_level` setting and `config.py` has `get_log_level()`, but neither is used. The log level is not configurable at runtime as required by REQ-context-broker 6.3 ("Configurable in config.yml"). `setup_logging()` is called before config is loaded in `main.py`, so it cannot read config. A post-startup log level adjustment is needed.

### CB-OPUS-027 — `enqueue_background_jobs` increments `JOBS_ENQUEUED` even when dedup prevents enqueue

- **File:** `app/flows/message_pipeline.py`, `enqueue_background_jobs()`, lines 169, 188
- **Severity:** Minor
- **Description:** `JOBS_ENQUEUED.labels(job_type=...).inc()` is called regardless of whether `is_new` was True (job actually enqueued) or False (deduplicated). The metric should only be incremented when `is_new` is truthy, otherwise the enqueue count will be inflated relative to actual queue depth.

### CB-OPUS-028 — `finalize_assembly` returns empty dict, does not propagate state

- **File:** `app/flows/context_assembly.py`, `finalize_assembly()`, line 430
- **Severity:** Minor
- **Description:** Returns `{}` instead of propagating state. Same pattern as CB-OPUS-013. Since `finalize_assembly` is followed by `release_assembly_lock` which also returns `{}`, and then END, no downstream node needs the state. Functionally harmless, but inconsistent with other nodes.

### CB-OPUS-029 — Mem0 singleton never invalidated on config change

- **File:** `app/memory/mem0_client.py`, lines 17-45
- **Severity:** Minor
- **Description:** `_mem0_instance` is a module-level singleton initialized once. If the user changes LLM or embedding provider settings in config.yml (hot-reload), the Mem0 client continues using the old settings. Since Mem0 is configured with its own LLM and embedding providers, hot-reload of these settings has no effect on Mem0 operations. This is a silent misconfiguration after config changes.

### CB-OPUS-030 — `Decimal` type from `Decimal` in `relevance_score` column not JSON-serializable

- **File:** `app/flows/search_flow.py`, `search_conversations_db()`, line 104-111
- **Severity:** Minor
- **Description:** The `relevance_score` column from the SQL query (line 78, `MIN(cm.embedding <=> ...)`) returns a PostgreSQL `float8` which asyncpg maps to Python `float`. However, depending on the pgvector operator implementation and asyncpg version, this could be returned as `Decimal`. The `dict(row)` conversion preserves the original type. If it's `Decimal`, `json.dumps()` in the MCP response handler (mcp.py line 175) will raise `TypeError: Object of type Decimal is not JSON serializable`. Similarly, `created_at` is converted to ISO string, but `relevance_score` and other numeric fields are not explicitly cast.

---

## Summary

| Severity | Count |
|----------|-------|
| Blocker  | 1     |
| Major    | 12    |
| Minor    | 10    |
| **Total**| **23** |

### Critical path items (must fix before deployment):

1. **CB-OPUS-002** -- Postgres password not available to the langgraph container. The system cannot start.
2. **CB-OPUS-006** -- Embedding dimension mismatch (1536 in schema vs 768 from Ollama nomic-embed-text). Embedding storage will fail.
3. **CB-OPUS-004 / CB-OPUS-005 / CB-OPUS-016** -- LangChain parameter names and empty API key handling. All LLM and embedding calls may fail or route to wrong provider.
4. **CB-OPUS-017** -- Missing `sentence-transformers` dependency. Default reranker config will crash.
5. **CB-OPUS-018** -- Config hot-reload is broken. Config is captured once at startup.
6. **CB-OPUS-022** -- Jobs lost on crash due to non-atomic dequeue.
7. **CB-OPUS-013** -- Memory extraction silently skips marking messages as extracted on success.
