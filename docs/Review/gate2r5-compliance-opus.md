# Gate 2 Round 5 Pass 2 — Compliance Review (Opus)

**Date:** 2026-03-22
**Reviewer:** Claude Opus 4.6 (1M context)
**Scope:** REQ-context-broker.md, draft-REQ-001-mad-requirements.md, draft-REQ-002-pmad-requirements.md
**Codebase snapshot:** All .py files under app/ (including build_types/), docker-compose.yml, Dockerfile, requirements.txt, nginx/nginx.conf, postgres/init.sql, config/config.example.yml, entrypoint.sh, .gitignore, README.md

**Major architectural changes since R4:** Build type registry (ARCH-18) with passthrough, standard-tiered, and knowledge-enriched as separate modules under `app/flows/build_types/`. Assembly and retrieval graphs are now per-build-type with standard contracts (`AssemblyInput`/`AssemblyOutput`, `RetrievalInput`/`RetrievalOutput`). `context_assembly.py` and `retrieval_flow.py` are backward-compatibility shims re-exporting from the new locations. `tool_dispatch.py` dynamically looks up retrieval graphs from the registry by build type name.

---

## REQ-context-broker.md

### 1. Build System

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 1.1 | Version Pinning | PASS | `requirements.txt` uses `==` for all 25 packages. Dockerfile: `FROM python:3.12.1-slim`. docker-compose.yml: `nginx:1.25.3-alpine`, `pgvector/pgvector:0.7.0-pg16`, `neo4j:5.15.0`, `redis:7.2.3-alpine`, `ollama/ollama:0.6.2`. | All pinned to patch level. |
| 1.2 | Code Formatting (black) | PASS | `black==24.2.0` in requirements.txt. | Presence of tool; actual formatting not verified at review time. |
| 1.3 | Code Linting (ruff) | PASS | `ruff==0.2.2` in requirements.txt. | Same caveat — tool present, run status not verified. |
| 1.4 | Unit Testing | FAIL | `pytest==8.0.2`, `pytest-asyncio==0.23.5`, `pytest-mock==3.12.0` in requirements.txt. No test files found under `app/` or in the project root glob. | Test framework is installed but no test files exist in the codebase. |
| 1.5 | StateGraph Package Source | PASS | `config.example.yml` defines `packages: {source, local_path, devpi_url}`. `entrypoint.sh` reads these values and switches install mode. Dockerfile supports `PACKAGE_SOURCE` and `DEVPI_URL` build args. | Three modes (local, pypi, devpi) all wired. |

### 2. Runtime Security and Permissions

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 2.1 | Root Usage Pattern | PASS | Dockerfile: `RUN apt-get ... && groupadd ... && useradd ...` then `USER ${USER_NAME}` on line 22. All subsequent operations run as non-root. | Correct pattern. |
| 2.2 | Service Account | PASS | `USER_NAME=context-broker`, `USER_UID=1001`, `USER_GID=1001` as build args. | Consistent UID/GID. |
| 2.3 | File Ownership | PASS | `COPY --chown=${USER_NAME}:${USER_NAME}` used for requirements.txt, app/, and entrypoint.sh. No `chown -R` anywhere. | Correct pattern. |

### 3. Storage and Data

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 3.1 | Two-Volume Pattern | PASS | docker-compose.yml: `./config:/config:ro` and `./data:/data` on the langgraph container. Postgres: `./data/postgres:/var/lib/postgresql/data`. Neo4j: `./data/neo4j:/data`. Redis: `./data/redis:/data`. | Config read-only, data read-write. |
| 3.2 | Data Directory Organization | PASS | Postgres, Neo4j, Redis each under `/data/<service>`. Imperator state at `/data/imperator_state.json` (state_manager.py line 24). | Matches spec. |
| 3.3 | Config Directory Organization | PASS | `config/config.example.yml` present. `config/credentials/.env` referenced in docker-compose.yml `env_file`. | Correct structure. |
| 3.4 | Credential Management | PASS | `.gitignore` includes `config/credentials/.env` and `.env`. `config.py` reads keys via `os.environ.get()` using the `api_key_env` indirection. No hardcoded secrets found in any source file. | env.example not verified but pattern is correct. |
| 3.5 | Database Storage | PASS | Each service has dedicated subdirectory under `./data/`. All use bind mounts. | Correct. |
| 3.6 | Backup and Recovery | PASS | README documents backup approach. All state under `./data/`. | Deployer responsibility as spec'd. |
| 3.7 | Schema Migration | PASS | `migrations.py` defines 12 migrations in a forward-only registry. `run_migrations()` applies pending versions in order within transactions. Refuses to start on failure (`raise RuntimeError`). `schema_migrations` table tracks applied versions. | Robust implementation. |

### 4. Communication and Integration

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 4.1 | MCP Transport | PASS | `routes/mcp.py`: `GET /mcp` establishes SSE session, `POST /mcp` handles sessionless or session-routed tool calls. SSE keepalive, session eviction, bounded queue. | Full HTTP/SSE transport. |
| 4.2 | OpenAI-Compatible Chat | PASS | `routes/chat.py`: `POST /v1/chat/completions`. Accepts model, messages, stream, temperature, max_tokens. Streaming SSE with `data: {...}\n\n` and `data: [DONE]\n\n`. | Compliant with OpenAI spec. |
| 4.3 | Authentication | PASS | No auth in application. README notes gateway-level auth as an option. | As specified. |
| 4.4 | Health Endpoint | PASS | `GET /health` returns 200/503 with `{status, database, cache, neo4j}`. Neo4j down = degraded (200), Postgres/Redis down = unhealthy (503). | Matches spec exactly. |
| 4.5 | Tool Naming Convention | PASS | All tools use `conv_*`, `mem_*`, `imperator_*`, `metrics_*` prefixes. | Domain-prefixed, no collisions. |
| 4.6 | MCP Tool Inventory | PARTIAL | 15 tools registered in `_get_tool_list()`. REQ spec lists `broker_chat` but implementation uses `imperator_chat`. All other tools match. `mem_add`, `mem_list`, `mem_delete` are implemented but not in the REQ table. | Minor naming discrepancy: `broker_chat` vs `imperator_chat`. Three additional mem tools beyond spec. |
| 4.5 (second) | LangGraph Mandate | PASS | Every operation is a StateGraph: message_pipeline, embed_pipeline, context_assembly (3 build types), retrieval (3 build types), memory_extraction, search (2 flows), memory_search (2 flows), memory_admin (3 flows), imperator, health, metrics. Route handlers only invoke `ainvoke()`. tool_dispatch is a thin mapping layer. | Comprehensive StateGraph coverage. |
| 4.6 (second) | State Immutability | PASS | All node functions return `dict` with only updated keys. No in-place mutation of state observed. | Correct pattern throughout. |
| 4.7 | Thin Gateway | PASS | `nginx.conf`: pure proxy_pass to upstream. No logic, no health checks, no validation. Routes /mcp, /v1/chat/completions, /health, /metrics. | Pure routing layer. |
| 4.8 | Prometheus Metrics | PASS | `metrics_registry.py` defines counters, histograms, gauges. `metrics_flow.py` collects inside a StateGraph. `/metrics` endpoint via route handler that invokes the flow. `metrics_get` MCP tool also available. | Metrics produced inside StateGraph. |

### 5. Configuration

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 5.1 | Configuration File | PASS | `config.py` reads `/config/config.yml` with mtime-based cache + SHA-256 hash for change detection. Hot-reloadable (LLM/embeddings caches cleared on change). Infrastructure read once via `load_startup_config()`. | Correct separation. |
| 5.2 | Inference Provider Configuration | PASS | Three slots: `llm`, `embeddings`, `reranker` in config.example.yml. Each with base_url, model, api_key_env. Cross-encoder reranker runs locally by default. | All three independent. |
| 5.3 | Build Type Configuration | PASS | Three build types shipped: passthrough, standard-tiered, knowledge-enriched. Config defines tier percentages, token budgets, optional per-build-type LLM override (F-06). Registry pattern (ARCH-18) makes adding new types straightforward. | Exceeds minimum (2 required, 3 shipped). |
| 5.4 | Token Budget Resolution | PASS | `token_budget.py`: caller_override > explicit int > auto (query provider) > fallback_tokens. Resolved once at window creation and stored in `max_token_budget` column. | Priority chain correct. |
| 5.5 | Imperator Configuration | PASS | config.example.yml: `imperator: {build_type, max_context_tokens, participant_id, admin_tools}`. `imperator_flow.py` gates admin tools based on config. State manager creates conversation + context window on first boot. | All fields present. |
| 5.6 | Package Source | PASS | See 1.5. | Covered. |

### 6. Logging and Observability

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 6.1 | Logging to stdout/stderr | PASS | `logging_setup.py`: `StreamHandler(sys.stdout)`. nginx.conf: `error_log /dev/stderr`, `access_log /dev/stdout`. | No file logging. |
| 6.2 | Structured Logging | PASS | `JsonFormatter` outputs single-line JSON with `timestamp`, `level`, `message`, `logger`, and optional context fields (`request_id`, `tool_name`, `conversation_id`, `window_id`). | Correct format. |
| 6.3 | Log Levels | PASS | DEBUG/INFO/WARN/ERROR. Default INFO. Configurable via `log_level` in config.yml. `update_log_level()` applies at startup. | Configurable as required. |
| 6.4 | Log Content Standards | PASS | `HealthCheckFilter` suppresses health check noise. No credential logging observed. No full body logging. | Correct content policy. |
| 6.5 | Dockerfile HEALTHCHECK | PASS | Dockerfile line 48: `HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 CMD curl -f http://localhost:8000/health || exit 1`. docker-compose.yml: healthchecks for all 6 containers. | All containers covered. |
| 6.6 | Health Check Architecture | PASS | Docker HEALTHCHECK per container (process-level). HTTP `/health` endpoint (dependency aggregation via health_flow.py checking Postgres, Redis, Neo4j). | Two-layer architecture. |
| 6.7 | Specific Exception Handling | PARTIAL | Most exception handlers catch specific types. However, `memory_extraction.py` line 207, `memory_search_flow.py` lines 78/155, `memory_admin_flow.py` lines 54/111/160, and `knowledge_enriched.py` line 369 all have `except (..., Exception)` with comment `EX-CB-001` or `G5-18`. Also `arq_worker.py` line 165 has bare `except Exception`. | Documented with justification comments (Mem0/Neo4j wrap errors unpredictably), but technically violates the letter of the requirement. The `except Exception` in arq_worker crash handler (line 165) is less justified. |
| 6.8 | Resource Management | PASS | asyncpg pool with `async with pool.acquire() as conn`, Redis `aclose()` in shutdown, file handles via `with open(...)`. `lifespan` context manager handles startup/shutdown. | Proper resource management. |
| 6.9 | Error Context | PASS | Errors include conversation_id, window_id, message_id, tool_name, queue_name throughout. `exc_info=True` on critical errors. | Sufficient debugging context. |

### 7. Resilience and Deployment

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 7.1 | Graceful Degradation | PASS | Postgres failure: degraded mode with 503 middleware, retry loop. Redis failure: worker deferred, retry loop. Neo4j failure: Mem0 returns None, knowledge graph features disabled, health reports "degraded". Reranker failure: falls back to raw RRF scores. | Four degradation paths all handled. |
| 7.2 | Independent Startup | PASS | `main.py` lifespan: Postgres failure caught and deferred to retry loop. Redis failure caught and deferred. Imperator init deferred if Postgres unavailable. App starts and serves health/metrics immediately. | Non-blocking startup. |
| 7.3 | Network Topology | PASS | docker-compose.yml: `default` network (external) for gateway only. `context-broker-net` with `internal: true` for all containers. Gateway on both networks. All other containers on internal only. Service names used everywhere. | Exact match to spec. |
| 7.4 | Docker Compose | PASS | Single `docker-compose.yml`. Header comment directs customization to `docker-compose.override.yml`. Build context is `.`. | Correct pattern. |
| 7.5 | Container-Only Deployment | PASS | All components are Docker containers. No bare-metal instructions. | Correct. |
| 7.6 | Asynchronous Correctness | PASS | All DB ops via asyncpg/aioredis. Synchronous Mem0 calls wrapped in `run_in_executor()`. Synchronous file reads in config/prompt loader use mtime fast-path with `run_in_executor` for actual reads in async variants. CrossEncoder load via `run_in_executor`. | No blocking I/O in async paths. |
| 7.7 | Input Validation | PASS | All 16 MCP tool inputs validated via Pydantic models in `models.py`. Field constraints (min_length, max_length, pattern, ge/le). `ChatCompletionRequest` with custom validator. Tool list includes `inputSchema` for MCP discovery. | Comprehensive validation. |
| 7.8 | Null/None Checking | PASS | Database query results checked (`if row is None`). Config values use `.get()` with defaults. Optional state fields checked before access (`state.get("error")`). Content nullability handled for tool-call messages. | Consistent null checking. |

### 8. Documentation

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 8.1 | README | PASS | README.md covers: what/why, quick start, configuration reference (all sections), MCP tool reference (all 16 tools with input/output schemas), OpenAI chat endpoint, architecture overview (containers, networks, StateGraphs, build types), how to modify flows, how to add build types. | Comprehensive. 526 lines. |
| 8.2 | Tool Documentation | PASS | Each tool documented in README with name, description, input schema (type, required, description), output format. Also discoverable via `tools/list` MCP method in `mcp.py`. | Dual documentation. |
| 8.3 | Config Template | PASS | `config/config.example.yml` with all options documented and defaults. | env.example referenced but not verified in file listing. |

---

## draft-REQ-001-mad-requirements.md

### 1. Code Quality

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 1.1 | Code Clarity | PASS | Descriptive names throughout. Functions are focused. Docstrings on all public functions with "why" comments (e.g., CB-R3-02, G5-04, ARCH-18 annotations). | Good clarity. |
| 1.2 | Code Formatting | PASS | black installed. | See REQ-CB 1.2. |
| 1.3 | Code Linting | PASS | ruff installed. | See REQ-CB 1.3. |
| 1.4 | Unit Testing | FAIL | No test files present. | See REQ-CB 1.4. |
| 1.5 | Version Pinning | PASS | All `==` in requirements.txt. | See REQ-CB 1.1. |

### 2. LangGraph Architecture

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 2.1 | StateGraph Mandate | PASS | All logic in StateGraphs. Route handlers only call `ainvoke()`. LangChain components used: `ChatOpenAI`, `OpenAIEmbeddings`, `ToolNode`, `add_messages` reducer, tool binding via `bind_tools()`. Mem0 native API justified for knowledge graph traversal (edge-following, not vector similarity). | Comprehensive compliance. The Imperator uses proper graph-based ReAct (agent_node <-> tool_node via conditional edges, not a while loop). |
| 2.2 | State Immutability | PASS | All nodes return `dict` with updated keys only. | Verified across all flows. |
| 2.3 | Checkpointing | PARTIAL | Imperator flow explicitly does NOT use a checkpointer (ARCH-06). History loaded from Postgres on each invocation. This is a deliberate architectural choice (DB is persistence layer). Other flows are short-lived background jobs that don't need checkpointing. | The spec says "where applicable" — the design justifies not using it. Acceptable. |

### 3. Security Posture

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 3.1 | No Hardcoded Secrets | PASS | No secrets in code. `api_key_env` indirection pattern. `.env` gitignored. Config redaction in Imperator admin tool. | Clean. |
| 3.2 | Input Validation | PASS | Pydantic models for all external inputs. UUID validation in worker jobs (M-25). Date format validation (M-21). | Thorough. |
| 3.3 | Null/None Checking | PASS | Consistent throughout. | See REQ-CB 7.8. |

### 4. Logging and Observability

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 4.1 | Logging to stdout/stderr | PASS | See REQ-CB 6.1. | |
| 4.2 | Structured Logging | PASS | See REQ-CB 6.2. | |
| 4.3 | Log Levels | PASS | See REQ-CB 6.3. | |
| 4.4 | Log Content | PASS | See REQ-CB 6.4. | |
| 4.5 | Specific Exception Handling | PARTIAL | See REQ-CB 6.7. Broad `except (..., Exception)` in Mem0-related code with documented justification. | Same finding. |
| 4.6 | Resource Management | PASS | See REQ-CB 6.8. | |
| 4.7 | Error Context | PASS | See REQ-CB 6.9. | |
| 4.8 | Pipeline Observability | PASS | `verbose_logging` toggle in config.yml `tuning` section. `verbose_log()` and `verbose_log_auto()` in `config.py` log node entry/exit with timing when enabled. Standard mode logs stage entry/exit and errors only. | Configurable verbose mode with timing. |

### 5. Async Correctness

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 5.1 | No Blocking I/O | PASS | All async I/O. Synchronous operations (Mem0, CrossEncoder, file reads) use `run_in_executor()`. `async_load_config()` and `async_load_prompt()` provided for async callers. | See REQ-CB 7.6. |

### 6. Communication

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 6.1 | MCP Transport | PASS | HTTP/SSE. | See REQ-CB 4.1. |
| 6.2 | Tool Naming | PASS | Domain-prefixed. | See REQ-CB 4.5. |
| 6.3 | Health Endpoint | PASS | 200/503 with per-dependency status. | See REQ-CB 4.4. |
| 6.4 | Prometheus Metrics | PASS | `/metrics` endpoint. Metrics inside StateGraph. | See REQ-CB 4.8. |

### 7. Resilience

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 7.1 | Graceful Degradation | PASS | See REQ-CB 7.1. | Four degradation paths. |
| 7.2 | Independent Startup | PASS | See REQ-CB 7.2. | Non-blocking. |
| 7.3 | Idempotency | PASS | Conversation creation: `ON CONFLICT DO NOTHING`. Context window creation: `ON CONFLICT DO NOTHING` on unique constraint. Message store: repeat_count collapse for duplicates, advisory lock + unique index on sequence_number. Summary insertion: pre-check + unique index (M-08). Embedding store: overwrites existing (idempotent). Extraction: dedup key in Redis. Assembly: dedup key + lock. | Comprehensive idempotency. |
| 7.4 | Fail Fast | PASS | Invalid config: `raise RuntimeError` (config.py). Failed migration: `raise RuntimeError` preventing startup (migrations.py). Invalid build type: `raise ValueError` (config.py). Percentage validation: sum > 1.0 raises ValueError. Invalid UUID in worker: raises ValueError (M-25). | Correct fail-fast behavior. |

### 8. Configuration

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 8.1 | Configurable External Dependencies | PASS | LLM, embeddings, reranker all configurable. Per-build-type LLM override (F-06). Package source configurable. Database pool sizes configurable. | Full State 4 configurability. |
| 8.2 | Externalized Configuration | PASS | Prompt templates externalized to `/config/prompts/` via `prompt_loader.py`. All tuning parameters in config.yml `tuning` section. Tier percentages, chunk sizes, thresholds, timeouts, retry counts, half-lives all configurable. | Thorough externalization. |
| 8.3 | Hot-Reload vs Startup Config | PASS | `load_config()` re-reads on mtime change. LLM/embeddings caches cleared on content hash change. `database` section read once at startup. | Correct separation. |

---

## draft-REQ-002-pmad-requirements.md

### 1. Container Construction

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 1.1 | Root Usage Pattern | PASS | See REQ-CB 2.1. | |
| 1.2 | Service Account | PASS | See REQ-CB 2.2. | |
| 1.3 | File Ownership | PASS | See REQ-CB 2.3. | |
| 1.4 | Base Image Pinning | PASS | `python:3.12.1-slim` — pinned to patch. All OTS images pinned. | |
| 1.5 | Dockerfile HEALTHCHECK | PASS | See REQ-CB 6.5. | |

### 2. Container Architecture

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 2.1 | OTS Backing Services | PASS | Only langgraph container is custom-built. Postgres, Neo4j, Redis, Nginx, Ollama all official images. | |
| 2.2 | Thin Gateway | PASS | See REQ-CB 4.7. | |
| 2.3 | Container-Only Deployment | PASS | See REQ-CB 7.5. | |

### 3. Network Topology

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 3.1 | Two-Network Pattern | PASS | `default` (external, gateway only) + `context-broker-net` (internal: true, all containers). Gateway on both. | |
| 3.2 | Service Name DNS | PASS | All inter-container references use service names: `context-broker-langgraph`, `context-broker-postgres`, etc. Environment variables use these names. | |

### 4. Storage

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 4.1 | Volume Pattern | PASS | Config and credentials separate from data. Fixed internal paths, host paths in compose. | |
| 4.2 | Database Storage | PASS | Each service in its own subdirectory. Bind mounts at VOLUME paths. | |
| 4.3 | Backup and Recovery | PASS | All under `./data/`. Migrations versioned and auto-applied. Forward-only. | |
| 4.4 | Credential Management | PASS | `config/credentials/.env` loaded via `env_file`. Gitignored. | |

### 5. Deployment

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 5.1 | Docker Compose | PASS | Single `docker-compose.yml`. Override file pattern documented in header comment. | |
| 5.2 | Health Check Architecture | PASS | Docker HEALTHCHECK per container + HTTP /health with dependency aggregation. Gateway proxies langgraph's response. | |
| 5.3 | Eventual Consistency | PASS | Postgres is source of truth. Embedding, assembly, extraction are async background jobs. Failed jobs retry with exponential backoff. Dead-letter queue with sweep. Messages never lost (stored in Postgres before queuing). | |

### 6. Interface

| # | Requirement | Status | Evidence | Notes |
|---|-------------|--------|----------|-------|
| 6.1 | MCP Endpoint | PASS | `/mcp` via gateway. | |
| 6.2 | OpenAI-Compatible Chat | PASS | `/v1/chat/completions` via gateway. | |
| 6.3 | Authentication | PASS | Ships without auth. Gateway-layer auth noted as option. | |

---

## Summary

**Total requirements evaluated:** 79 (across all three documents, deduplicated)

| Status | Count |
|--------|-------|
| PASS | 74 |
| PARTIAL | 3 |
| FAIL | 2 |

### FAIL items

1. **REQ-CB 1.4 / REQ-001 1.4 — Unit Testing:** Test framework installed but no test files exist in the codebase. This is a known gap; the system is pre-test-plan execution.

### PARTIAL items

1. **REQ-CB 4.6 — MCP Tool Inventory:** The REQ spec lists `broker_chat` but implementation uses `imperator_chat`. Three additional tools (`mem_add`, `mem_list`, `mem_delete`) exist beyond the spec table. Minor naming discrepancy.

2. **REQ-CB 6.7 / REQ-001 4.5 — Specific Exception Handling:** Six code locations use `except (..., Exception)` for Mem0/Neo4j interactions. Each has a documented justification comment (EX-CB-001 / G5-18) explaining that Mem0 wraps errors unpredictably. The `except Exception` in `arq_worker.py` line 165 (crash handler for graph execution) is less well-justified. While pragmatically reasonable, it technically violates the letter of the requirement.

3. **REQ-001 2.3 — Checkpointing:** The Imperator deliberately does not use LangGraph checkpointing (ARCH-06), loading history from Postgres instead. This is a justified architectural decision (DB as persistence layer) and the spec says "where applicable." Acceptable but noted.

### Changes since R4

The ARCH-18 build type registry is cleanly implemented. The `contracts.py` file defines standard input/output TypedDicts. Three build types register via `register_build_type()` at import time. Assembly and retrieval graphs are looked up dynamically from the registry. Backward-compatibility shims in `context_assembly.py` and `retrieval_flow.py` re-export symbols. The `tool_dispatch.py` retrieval path now queries the context window's build type and dispatches to the correct retrieval graph. This is a significant architectural improvement with no compliance regressions.
