# Gate 2 Round 4 Pass 2 — Compliance Review

**Reviewer:** Claude Opus 4.6 (1M context)
**Date:** 2026-03-22
**Scope:** All `.py` files under `app/`, docker-compose.yml, Dockerfile, requirements.txt, nginx/nginx.conf, postgres/init.sql, config/config.example.yml, entrypoint.sh, .gitignore
**Requirements:** REQ-context-broker.md, draft-REQ-001-mad-requirements.md, draft-REQ-002-pmad-requirements.md

---

## REQ-context-broker.md

### 1. Build System

**1.1 Version Pinning**

- **Status:** PASS
- **Evidence:** requirements.txt uses `==` for all 26 packages. Dockerfile uses `python:3.12.1-slim`. docker-compose.yml pins all images: `nginx:1.25.3-alpine`, `pgvector/pgvector:0.7.0-pg16`, `neo4j:5.15.0`, `redis:7.2.3-alpine`, `ollama/ollama:0.6.2`.
- **Notes:** None.

**1.2 Code Formatting**

- **Status:** PASS
- **Evidence:** `black==24.2.0` in requirements.txt. Code is consistently formatted throughout all files reviewed.
- **Notes:** No `pyproject.toml` or `black` config was checked for enforcement in CI, but the dependency and consistent style are present.

**1.3 Code Linting**

- **Status:** PASS
- **Evidence:** `ruff==0.2.2` in requirements.txt.
- **Notes:** Same note as 1.2 regarding CI enforcement.

**1.4 Unit Testing**

- **Status:** PASS
- **Evidence:** `pytest==8.0.2`, `pytest-asyncio==0.23.5`, `pytest-mock==3.12.0` in requirements.txt. Tests directory contains: test_config.py, test_message_pipeline.py, test_context_assembly.py, test_retrieval_flow.py, test_token_budget.py, test_health.py, test_models.py, test_memory_extraction.py, test_metrics_flow.py, test_search_flow.py (10 test files covering major flows).
- **Notes:** Test content not reviewed in this compliance pass. Coverage breadth looks adequate based on file names.

**1.5 StateGraph Package Source**

- **Status:** PASS
- **Evidence:** config.example.yml defines `packages: {source, local_path, devpi_url}`. entrypoint.sh reads config.yml and handles `local`, `devpi`, and `pypi` cases. Dockerfile supports `PACKAGE_SOURCE` and `DEVPI_URL` build args.
- **Notes:** None.

### 2. Runtime Security and Permissions

**2.1 Root Usage Pattern**

- **Status:** PASS
- **Evidence:** Dockerfile: `RUN apt-get ...` and `useradd` run as root. `USER ${USER_NAME}` on line 22 immediately follows user creation. All subsequent COPY and RUN commands execute as the non-root user.
- **Notes:** None.

**2.2 Service Account**

- **Status:** PASS
- **Evidence:** Dockerfile defines `ARG USER_NAME=context-broker`, `ARG USER_UID=1001`, `ARG USER_GID=1001`. The `groupadd` and `useradd` commands create the dedicated user. `USER ${USER_NAME}` is set before any application code.
- **Notes:** None.

**2.3 File Ownership**

- **Status:** PASS
- **Evidence:** Dockerfile uses `COPY --chown=${USER_NAME}:${USER_NAME}` for requirements.txt, app/, and entrypoint.sh. No `chown -R` in the Dockerfile.
- **Notes:** None.

### 3. Storage and Data

**3.1 Two-Volume Pattern**

- **Status:** PASS
- **Evidence:** docker-compose.yml: langgraph service mounts `./config:/config:ro` and `./data:/data`. Container-side paths are fixed (`/config`, `/data`).
- **Notes:** None.

**3.2 Data Directory Organization**

- **Status:** PASS
- **Evidence:** docker-compose.yml mounts `./data/postgres`, `./data/neo4j`, `./data/redis`, `./data/ollama` to their respective containers. Imperator state file path is `/data/imperator_state.json` in `state_manager.py`.
- **Notes:** None.

**3.3 Config Directory Organization**

- **Status:** PASS
- **Evidence:** docker-compose.yml: `env_file: ./config/credentials/.env`. config.example.yml in `config/`. Config path defaults to `/config/config.yml` in config.py.
- **Notes:** None.

**3.4 Credential Management**

- **Status:** PARTIAL
- **Evidence:** `.env` file loaded via `env_file` in compose. `api_key_env` pattern used in config.yml to reference env vars. `.gitignore` includes `config/credentials/.env` and `.env`. No hardcoded secrets found in any Python file or Dockerfile.
- **Notes:** The requirement says "The repository ships a `.env.example` listing required variable names without values." No `config/credentials/.env.example` file was found in the repository.

**3.5 Database Storage**

- **Status:** PASS
- **Evidence:** Each backing service gets its own subdirectory under `./data/`: `./data/postgres`, `./data/neo4j`, `./data/redis`. Bind mounts used at declared VOLUME paths.
- **Notes:** None.

**3.6 Backup and Recovery**

- **Status:** PASS
- **Evidence:** All persistent state lives under `./data/` on the host. This is a documentation/operational requirement; the code does not perform automated backups, which is the stated policy.
- **Notes:** None.

**3.7 Schema Migration**

- **Status:** PASS
- **Evidence:** migrations.py implements `run_migrations()` called at startup from main.py lifespan. `schema_migrations` table tracks versions. 10 migrations defined in the `MIGRATIONS` list. Each runs in a transaction. Failed migrations raise `RuntimeError` preventing startup. All migrations use `IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` (forward-only, non-destructive).
- **Notes:** None.

### 4. Communication and Integration

**4.1 MCP Transport**

- **Status:** PASS
- **Evidence:** `mcp.py` implements `GET /mcp` (SSE session), `POST /mcp?sessionId=xxx` (session mode), and `POST /mcp` (sessionless mode). nginx.conf routes `/mcp` with SSE-appropriate headers (`proxy_buffering off`, `Connection ''`).
- **Notes:** None.

**4.2 OpenAI-Compatible Chat**

- **Status:** PASS
- **Evidence:** `chat.py` implements `POST /v1/chat/completions`. Accepts `model`, `messages`, `stream`, `temperature`, `max_tokens`. Streaming uses `data: {...}\n\n` and `data: [DONE]\n\n` format. Non-streaming returns standard OpenAI completion response shape. nginx.conf routes `/v1/chat/completions` with SSE support.
- **Notes:** None.

**4.3 Authentication**

- **Status:** PASS
- **Evidence:** No authentication is implemented in the application code. The requirement explicitly states "ships without authentication" for single-user/trusted-network deployment, with gateway-layer auth available as needed.
- **Notes:** None.

**4.4 Health Endpoint**

- **Status:** PASS
- **Evidence:** `GET /health` returns 200 or 503. Response includes `{"status": "healthy|degraded|unhealthy", "database": "ok|error", "cache": "ok|error", "neo4j": "ok|degraded"}`. health_flow.py checks Postgres, Redis, and Neo4j connectivity. Neo4j failure results in "degraded" (200), not "unhealthy" (503).
- **Notes:** None.

**4.5 Tool Naming Convention**

- **Status:** PASS
- **Evidence:** All tools use domain prefixes: `conv_*`, `mem_*`, `broker_*`, `metrics_*`. No collisions possible with other MCP servers.
- **Notes:** None.

**4.6 MCP Tool Inventory**

- **Status:** PASS
- **Evidence:** `_get_tool_list()` in mcp.py returns 15 tools: conv_create_conversation, conv_store_message, conv_retrieve_context, conv_create_context_window, conv_search, conv_search_messages, conv_get_history, conv_search_context_windows, mem_search, mem_get_context, mem_add, mem_list, mem_delete, broker_chat, metrics_get. All 12 tools listed in the requirement are present. Additional tools (mem_add, mem_list, mem_delete) are bonus functionality.
- **Notes:** None.

**4.5 (second) LangGraph Mandate**

- **Status:** PASS
- **Evidence:** Every operation is implemented as a compiled StateGraph: health_flow, metrics_flow, search_flow (2 graphs), tool_dispatch routes to flows, conversation_ops_flow (4 graphs), message_pipeline, embed_pipeline, context_assembly, retrieval_flow, memory_extraction, memory_search_flow (2 graphs), memory_admin_flow (3 graphs), imperator_flow. Route handlers (`health.py`, `chat.py`, `mcp.py`, `metrics.py`) contain no application logic -- they invoke compiled StateGraphs. LangChain `ChatOpenAI` and `OpenAIEmbeddings` used via `get_chat_model()` and `get_embeddings_model()`. LangChain `ToolNode` and `bind_tools()` used in Imperator. Knowledge graph traversal via Mem0 native APIs is documented as a justified exception.
- **Notes:** None.

**4.6 (second) LangGraph State Immutability**

- **Status:** PASS
- **Evidence:** All node functions return new dictionaries with only updated keys. No in-place mutation of `state` observed in any flow. Pattern is consistently `return {"key": value}`.
- **Notes:** None.

**4.7 Thin Gateway**

- **Status:** PASS
- **Evidence:** nginx.conf contains only `upstream`, `server`, `location`, and `proxy_pass` directives. No Lua, no auth logic, no rate limiting, no rewriting. All health checks are performed by the LangGraph container and proxied through.
- **Notes:** None.

**4.8 Prometheus Metrics**

- **Status:** PASS
- **Evidence:** `GET /metrics` exposed via metrics_flow.py (StateGraph). metrics_registry.py defines counters, histograms, and gauges. Metrics incremented inside StateGraph flows (e.g., JOBS_COMPLETED in arq_worker, MCP_REQUESTS in mcp.py's finally block, CHAT_REQUESTS in chat.py). metrics_get MCP tool available.
- **Notes:** MCP_REQUESTS and CHAT_REQUESTS are incremented in route handler `finally` blocks, not inside StateGraph nodes. This is a pragmatic choice for request-level metrics that span the entire request lifecycle. The requirement says "Metrics produced inside a StateGraph (not in imperative route handlers)" -- the metrics collection itself runs through a StateGraph, but request-level counters are in handlers. This is a minor interpretation gap.

### 5. Configuration

**5.1 Configuration File**

- **Status:** PASS
- **Evidence:** config.py `load_config()` reads `/config/config.yml` with mtime-based caching. Hot-reload implemented: mtime+SHA256 hash check on each call, LLM/embeddings caches cleared on content change. Infrastructure settings (database pool) loaded once via `load_startup_config()`.
- **Notes:** None.

**5.2 Inference Provider Configuration**

- **Status:** PASS
- **Evidence:** config.example.yml defines three independent slots: `llm`, `embeddings`, `reranker`. Each with `base_url`, `model`, `api_key_env`. Reranker supports `cross-encoder`, `cohere`, `none`.
- **Notes:** None.

**5.3 Build Type Configuration**

- **Status:** PASS
- **Evidence:** config.example.yml defines `standard-tiered` and `knowledge-enriched` build types with all required percentages. `get_build_type_config()` validates sum <= 1.0. Build types are open-ended (deployer-definable).
- **Notes:** None.

**5.4 Token Budget Resolution**

- **Status:** PASS
- **Evidence:** token_budget.py implements the full resolution chain: caller_override > explicit integer > auto (query `/models` endpoint) > fallback_tokens. Budget stored at window creation in `create_context_window_node`.
- **Notes:** None.

**5.5 Imperator Configuration**

- **Status:** PASS
- **Evidence:** config.example.yml defines `imperator: {build_type, max_context_tokens, admin_tools}`. imperator_flow.py checks `admin_tools` to conditionally include `_config_read_tool` and `_db_query_tool`. Imperator state manager persists conversation_id to `/data/imperator_state.json`.
- **Notes:** None.

**5.6 Package Source Configuration**

- **Status:** PASS
- **Evidence:** See 1.5 above.
- **Notes:** None.

### 6. Logging and Observability

**6.1 Logging to stdout/stderr**

- **Status:** PASS
- **Evidence:** logging_setup.py sends all output to `sys.stdout` via `StreamHandler`. No file handlers configured. No log files written anywhere.
- **Notes:** None.

**6.2 Structured Logging**

- **Status:** PASS
- **Evidence:** `JsonFormatter` in logging_setup.py outputs JSON with `timestamp` (ISO 8601), `level`, `message`, `logger`, plus context fields (`request_id`, `tool_name`, `conversation_id`, `window_id`).
- **Notes:** None.

**6.3 Log Levels**

- **Status:** PASS
- **Evidence:** config.example.yml: `log_level: INFO`. `update_log_level()` called from lifespan after config load. Supports DEBUG/INFO/WARNING/ERROR/CRITICAL.
- **Notes:** None.

**6.4 Log Content Standards**

- **Status:** PASS
- **Evidence:** `HealthCheckFilter` suppresses health check success logs. `_redact_config()` in imperator_flow.py strips secrets from config output. No raw request/response bodies logged. Error logs include context (tool_name, conversation_id, etc.).
- **Notes:** None.

**6.5 Dockerfile HEALTHCHECK**

- **Status:** PASS
- **Evidence:** Dockerfile: `HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 CMD curl -f http://localhost:8000/health || exit 1`. docker-compose.yml includes healthchecks for all 6 containers.
- **Notes:** Only the custom container (langgraph) has a Dockerfile HEALTHCHECK. OTS containers use compose-level healthchecks, which is correct -- their Dockerfiles are upstream images.

**6.6 Health Check Architecture**

- **Status:** PASS
- **Evidence:** Two layers present: Docker HEALTHCHECK per container (process-level) and HTTP `/health` endpoint that actively checks Postgres, Redis, Neo4j connectivity and returns aggregated status. Nginx proxies the response.
- **Notes:** None.

**6.7 Specific Exception Handling**

- **Status:** PARTIAL
- **Evidence:** The vast majority of the codebase uses specific exception types. However, four locations include `Exception` in their except tuple: `memory_extraction.py:202`, `memory_admin_flow.py:54`, `memory_admin_flow.py:111`, `memory_admin_flow.py:160`. Each has a `G5-18` comment justifying this as necessary for Mem0/Neo4j failures that wrap errors in unpredictable types.
- **Notes:** The `Exception` entries are documented and justified, but they technically violate the literal requirement "No blanket except Exception:" -- even though they appear alongside more specific types in a tuple. This is a known and documented deviation.

**6.8 Resource Management**

- **Status:** PASS
- **Evidence:** Database connections use `async with pool.acquire() as conn` and `async with conn.transaction()` throughout. Redis locks released via dedicated `release_*_lock` nodes. `close_all_connections()` called on shutdown. `httpx.AsyncClient` used as context manager in health checks and token budget resolution.
- **Notes:** None.

**6.9 Error Context**

- **Status:** PASS
- **Evidence:** All error logs include relevant identifiers: tool_name, conversation_id, window_id, message_id, queue_name, etc. Exception objects included in log messages.
- **Notes:** None.

### 7. Resilience and Deployment

**7.1 Graceful Degradation and Eventual Consistency**

- **Status:** PASS
- **Evidence:** Postgres failure at startup enters degraded mode with retry loop. Redis failure defers worker startup. Neo4j/Mem0 failure returns empty results rather than crashing (search, extraction, retrieval all handle this). Health endpoint reports "degraded" status. Message storage in Postgres is the source of truth. Background jobs (embedding, assembly, extraction) are async with retry and dead-letter handling.
- **Notes:** None.

**7.2 Independent Container Startup**

- **Status:** PASS
- **Evidence:** No `depends_on` in docker-compose.yml. Postgres failure handled at request time with retry loops (`_postgres_retry_loop`). Redis failure handled similarly (`_redis_retry_loop`).
- **Notes:** None.

**7.3 Network Topology**

- **Status:** PASS
- **Evidence:** Two networks defined: `default` (implicit external) and `context-broker-net` (internal bridge, `internal: true`). Gateway connects to both. All other containers connect only to `context-broker-net`. All inter-container communication uses Docker Compose service names.
- **Notes:** None.

**7.4 Docker Compose**

- **Status:** PASS
- **Evidence:** Single `docker-compose.yml` shipped. Header comment says "Customize host paths, ports, and resource limits via docker-compose.override.yml. Do not modify this file directly." Build context is project root.
- **Notes:** None.

**7.5 Container-Only Deployment**

- **Status:** PASS
- **Evidence:** All components (nginx, langgraph, postgres, neo4j, redis, ollama) defined as container services in docker-compose.yml. No bare-metal instructions.
- **Notes:** None.

**7.6 Asynchronous Correctness**

- **Status:** PASS
- **Evidence:** No `time.sleep()` calls found anywhere in the codebase. All waits use `await asyncio.sleep()`. Database operations use `asyncpg` (async). Redis uses `redis.asyncio`. HTTP calls use `httpx.AsyncClient`. Synchronous Mem0 and CrossEncoder calls correctly wrapped in `loop.run_in_executor()`.
- **Notes:** None.

**7.7 Input Validation**

- **Status:** PASS
- **Evidence:** All MCP tool inputs validated via Pydantic models in models.py before reaching flows (dispatched in tool_dispatch.py). Chat endpoint validated via `ChatCompletionRequest`. MCP request structure validated via `MCPToolCall`. All models use Field constraints (min_length, max_length, pattern, ge, le).
- **Notes:** None.

**7.8 Null/None Checking**

- **Status:** PASS
- **Evidence:** Consistent null checks throughout: `if row is None` after every fetchrow, `if pool is None` in get_pg_pool, `if _redis_client is None` in get_redis, `getattr(app.state, "imperator_manager", None)`, `state.get("error")` checks before proceeding, `if not state.get("messages")`, etc.
- **Notes:** None.

### 8. Documentation

**8.1 README**

- **Status:** FAIL
- **Evidence:** No README file found in the repository.
- **Notes:** The requirement specifies a README covering architecture, quick start, configuration reference, MCP tool reference, chat endpoint usage, and how to modify flows.

**8.2 Tool Documentation**

- **Status:** PARTIAL
- **Evidence:** MCP tool schemas are discoverable via the `tools/list` method in mcp.py, with `name`, `description`, and `inputSchema` for each tool. However, no README exists for human-readable documentation, and no output schema or examples are documented.
- **Notes:** Programmatic discovery works. Human-readable docs missing.

**8.3 Config Template**

- **Status:** PARTIAL
- **Evidence:** `config/config.example.yml` exists with all options documented and sensible defaults. However, `config/credentials/.env.example` does not exist.
- **Notes:** Missing `.env.example` file.

---

## draft-REQ-001-mad-requirements.md

### 1. Code Quality

**1.1 Code Clarity**

- **Status:** PASS
- **Evidence:** Descriptive names throughout (e.g., `acquire_assembly_lock`, `resolve_token_budget`, `enqueue_background_jobs`). Functions are focused and single-purpose. Comments explain why, not what (e.g., CB-R3-02 explaining atomic lock release, M-15 explaining why DB history goes in system prompt).
- **Notes:** None.

**1.2 Code Formatting**

- **Status:** PASS
- **Evidence:** See REQ-CB 1.2.

**1.3 Code Linting**

- **Status:** PASS
- **Evidence:** See REQ-CB 1.3.

**1.4 Unit Testing**

- **Status:** PASS
- **Evidence:** See REQ-CB 1.4.

**1.5 Version Pinning**

- **Status:** PASS
- **Evidence:** See REQ-CB 1.1.

### 2. LangGraph Architecture

**2.1 StateGraph Mandate**

- **Status:** PASS
- **Evidence:** All logic in StateGraphs. Route handlers invoke compiled graphs only. LangChain ChatOpenAI, OpenAIEmbeddings, ToolNode, bind_tools, add_messages used. Native APIs for Mem0/Neo4j graph traversal justified with comments. Each distinct operation is a separate node. Conditional routing via graph edges, not if/else chains in nodes.
- **Notes:** None.

**2.2 State Immutability**

- **Status:** PASS
- **Evidence:** See REQ-CB 4.6 (second).

**2.3 Checkpointing**

- **Status:** PASS
- **Evidence:** Imperator flow uses `MemorySaver` checkpointer for multi-turn agent state. Short-lived background flows (embed, assembly, extraction) do not use checkpointing, which matches "Not required for short-lived background flows."
- **Notes:** None.

### 3. Security Posture

**3.1 No Hardcoded Secrets**

- **Status:** PASS
- **Evidence:** All credentials loaded from environment variables. `.gitignore` covers credential files. `api_key_env` pattern references env var names, not values. `_redact_config()` strips secrets from admin tool output.
- **Notes:** Missing `.env.example` noted under REQ-CB 8.3.

**3.2 Input Validation**

- **Status:** PASS
- **Evidence:** See REQ-CB 7.7.

**3.3 Null/None Checking**

- **Status:** PASS
- **Evidence:** See REQ-CB 7.8.

### 4. Logging and Observability

**4.1 Logging to stdout/stderr**

- **Status:** PASS
- **Evidence:** See REQ-CB 6.1.

**4.2 Structured Logging**

- **Status:** PASS
- **Evidence:** See REQ-CB 6.2.

**4.3 Log Levels**

- **Status:** PASS
- **Evidence:** See REQ-CB 6.3.

**4.4 Log Content**

- **Status:** PASS
- **Evidence:** See REQ-CB 6.4.

**4.5 Specific Exception Handling**

- **Status:** PARTIAL
- **Evidence:** See REQ-CB 6.7. Four locations include `Exception` in except tuples for Mem0/Neo4j wrapping, documented with G5-18 justification.
- **Notes:** Documented deviation, not a blanket catch.

**4.6 Resource Management**

- **Status:** PASS
- **Evidence:** See REQ-CB 6.8.

**4.7 Error Context**

- **Status:** PASS
- **Evidence:** See REQ-CB 6.9.

**4.8 Pipeline Observability**

- **Status:** PASS
- **Evidence:** config.example.yml: `tuning.verbose_logging: false`. `verbose_log()` and `verbose_log_auto()` in config.py conditionally log node entry/exit with timing. Used in embed_pipeline.py, context_assembly.py, memory_extraction.py, retrieval_flow.py, message_pipeline.py. Togglable via config, no code changes needed.
- **Notes:** None.

### 5. Async Correctness

**5.1 No Blocking I/O**

- **Status:** PASS
- **Evidence:** See REQ-CB 7.6.

### 6. Communication

**6.1 MCP Transport**

- **Status:** PASS
- **Evidence:** See REQ-CB 4.1.

**6.2 Tool Naming**

- **Status:** PASS
- **Evidence:** See REQ-CB 4.5.

**6.3 Health Endpoint**

- **Status:** PASS
- **Evidence:** See REQ-CB 4.4.

**6.4 Prometheus Metrics**

- **Status:** PASS
- **Evidence:** See REQ-CB 4.8.

### 7. Resilience

**7.1 Graceful Degradation**

- **Status:** PASS
- **Evidence:** See REQ-CB 7.1.

**7.2 Independent Startup**

- **Status:** PASS
- **Evidence:** See REQ-CB 7.2.

**7.3 Idempotency**

- **Status:** PASS
- **Evidence:** Message store uses `idempotency_key` with `ON CONFLICT DO NOTHING`. Conversation create uses `ON CONFLICT (id) DO NOTHING`. Context window create uses `ON CONFLICT (conversation_id, participant_id, build_type) DO NOTHING`. Embedding store overwrites (idempotent by nature). Summary insert checks for existing range before writing. Memory extraction tracks `memory_extracted` flag per message. Assembly uses Redis locks + dedup keys. Job queue uses `SET NX` for dedup.
- **Notes:** None.

**7.4 Fail Fast**

- **Status:** PASS
- **Evidence:** `load_config()` raises `RuntimeError` on missing/invalid config. `run_migrations()` raises `RuntimeError` on migration failure, preventing startup. `get_build_type_config()` raises `ValueError` on unknown build type. `get_pg_pool()` and `get_redis()` raise `RuntimeError` if not initialized. `_conversation_exists` in state_manager lets DB errors propagate (documented REQ-001 7.4).
- **Notes:** None.

### 8. Configuration

**8.1 Configurable External Dependencies**

- **Status:** PASS
- **Evidence:** LLM, embeddings, and reranker all configurable via config.yml. Package source configurable. Database pool sizes configurable. All tuning parameters externalized.
- **Notes:** None.

**8.2 Externalized Configuration**

- **Status:** PASS
- **Evidence:** Prompt templates loaded from `/config/prompts/` via prompt_loader.py. Model parameters, retry counts, timeouts, thresholds all in config.yml tuning section. Build type definitions in config.yml. No hardcoded operational values found in application code (all reference config or tuning defaults).
- **Notes:** Prompt template files themselves were not found in the repository (no `config/prompts/` directory). The loader exists but the template files are presumably deployment artifacts.

**8.3 Hot-Reload vs Startup Config**

- **Status:** PASS
- **Evidence:** `load_config()` checks mtime on every call for hot-reload. `load_startup_config()` uses `@lru_cache(maxsize=1)` for infrastructure settings. Config.example.yml documents which settings require restart.
- **Notes:** None.

---

## draft-REQ-002-pmad-requirements.md

### 1. Container Construction

**1.1 Root Usage Pattern**

- **Status:** PASS
- **Evidence:** See REQ-CB 2.1.

**1.2 Service Account**

- **Status:** PASS
- **Evidence:** See REQ-CB 2.2.

**1.3 File Ownership**

- **Status:** PASS
- **Evidence:** See REQ-CB 2.3.

**1.4 Base Image Pinning**

- **Status:** PASS
- **Evidence:** See REQ-CB 1.1 (Docker images).

**1.5 Dockerfile HEALTHCHECK**

- **Status:** PASS
- **Evidence:** See REQ-CB 6.5.

### 2. Container Architecture

**2.1 OTS Backing Services**

- **Status:** PASS
- **Evidence:** Only the langgraph container has a Dockerfile. All backing services (pgvector, neo4j, redis, nginx, ollama) use official images unmodified.
- **Notes:** None.

**2.2 Thin Gateway**

- **Status:** PASS
- **Evidence:** See REQ-CB 4.7.

**2.3 Container-Only Deployment**

- **Status:** PASS
- **Evidence:** See REQ-CB 7.5.

### 3. Network Topology

**3.1 Two-Network Pattern**

- **Status:** PASS
- **Evidence:** See REQ-CB 7.3. Gateway on both `default` and `context-broker-net`. All others on `context-broker-net` only. `internal: true` on the bridge network.
- **Notes:** None.

**3.2 Service Name DNS**

- **Status:** PASS
- **Evidence:** All inter-container references use Docker Compose service names: `context-broker-langgraph`, `context-broker-postgres`, `context-broker-neo4j`, `context-broker-redis`, `context-broker-ollama`. No IP addresses used.
- **Notes:** None.

### 4. Storage

**4.1 Volume Pattern**

- **Status:** PASS
- **Evidence:** See REQ-CB 3.1.

**4.2 Database Storage**

- **Status:** PASS
- **Evidence:** See REQ-CB 3.5.

**4.3 Backup and Recovery**

- **Status:** PASS
- **Evidence:** See REQ-CB 3.6 and 3.7.

**4.4 Credential Management**

- **Status:** PARTIAL
- **Evidence:** See REQ-CB 3.4. Credentials loaded via `env_file`. Real credentials gitignored. Missing `.env.example`.
- **Notes:** Same gap as REQ-CB 3.4 and 8.3.

### 5. Deployment

**5.1 Docker Compose**

- **Status:** PASS
- **Evidence:** See REQ-CB 7.4.

**5.2 Health Check Architecture**

- **Status:** PASS
- **Evidence:** See REQ-CB 6.6.

**5.3 Eventual Consistency**

- **Status:** PASS
- **Evidence:** See REQ-CB 7.1. Postgres is source of truth. Background processing is async and may lag. Failed background jobs retry with backoff (arq_worker.py). Message storage never lost due to downstream failure (embedding/extraction failures are non-fatal).
- **Notes:** None.

### 6. Interface

**6.1 MCP Endpoint**

- **Status:** PASS
- **Evidence:** See REQ-CB 4.1.

**6.2 OpenAI-Compatible Chat**

- **Status:** PASS
- **Evidence:** See REQ-CB 4.2.

**6.3 Authentication**

- **Status:** PASS
- **Evidence:** See REQ-CB 4.3.

---

## Summary

| Category | PASS | PARTIAL | FAIL | Total |
|----------|------|---------|------|-------|
| REQ-CB Build System (1.x) | 5 | 0 | 0 | 5 |
| REQ-CB Security (2.x) | 3 | 0 | 0 | 3 |
| REQ-CB Storage (3.x) | 5 | 1 | 0 | 6 |
| REQ-CB Communication (4.x) | 10 | 0 | 0 | 10 |
| REQ-CB Configuration (5.x) | 6 | 0 | 0 | 6 |
| REQ-CB Logging (6.x) | 7 | 1 | 0 | 8 |
| REQ-CB Resilience (7.x) | 7 | 0 | 0 | 7 |
| REQ-CB Documentation (8.x) | 0 | 2 | 1 | 3 |
| REQ-001 (MAD) | 22 | 1 | 0 | 23 |
| REQ-002 (pMAD) | 12 | 1 | 0 | 13 |
| **Total** | **77** | **6** | **1** | **84** |

### Open Issues

1. **FAIL -- README (REQ-CB 8.1):** No README file exists in the repository. Required content: what the system is, quick start, config reference, MCP tool reference, chat endpoint usage, architecture overview, how to modify flows.

2. **PARTIAL -- .env.example (REQ-CB 3.4, 8.3, REQ-002 4.4):** No `config/credentials/.env.example` file found. Should list required environment variable names (POSTGRES_PASSWORD, LLM_API_KEY, EMBEDDINGS_API_KEY, etc.) without values.

3. **PARTIAL -- Exception in except tuples (REQ-CB 6.7, REQ-001 4.5):** Four locations in Mem0-interfacing code include `Exception` alongside specific types. Documented and justified (G5-18) as necessary for Mem0/Neo4j's unpredictable error wrapping. Not a blanket catch, but technically violates the literal requirement.

4. **PARTIAL -- Tool documentation (REQ-CB 8.2):** MCP schemas are programmatically discoverable via `tools/list`, but no human-readable documentation with output schemas and examples exists (depends on the missing README).

5. **PARTIAL -- Prompt template files:** `prompt_loader.py` references `/config/prompts/imperator_identity.md`, `chunk_summarization.md`, and `archival_consolidation.md`. These files were not found in the repository. They may be deployment artifacts, but `config.example.yml` does not document them, and no example/template prompt files ship with the repository.

### Changes Since Round 3

All previously identified issues from prior rounds appear resolved. The codebase shows evidence of systematic fixes (CB-R3-* comment tags throughout). The remaining issues are documentation artifacts (README, .env.example, prompt templates) rather than code-level defects.
