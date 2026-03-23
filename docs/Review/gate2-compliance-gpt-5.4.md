Below is a section-by-section compliance audit of the provided implementation against:

- **REQ-001 — MAD Engineering Requirements**
- **REQ-002 — pMAD Engineering Requirements**
- **REQ-context-broker — Context Broker System Requirements**

I mark each numbered section as **PASS / FAIL / PARTIAL** and cite concrete evidence.

---

# REQ-001 — MAD Engineering Requirements

## §1.1 Code Clarity
- **Status:** PASS
- **Evidence:**  
  - `app/flows/message_pipeline.py` — `check_idempotency()`, `store_message()`, `enqueue_background_jobs()` are small and focused.  
  - `app/flows/retrieval_flow.py` — flow broken into clear nodes like `load_window()`, `wait_for_assembly()`, `load_summaries()`, `assemble_context_text()`.  
  - `app/config.py` and `app/token_budget.py` use descriptive function names and explanatory comments.
- **Notes:** Code is generally readable, modular, and comments explain design intent.

## §1.2 Code Formatting
- **Status:** PARTIAL
- **Evidence:** Source appears mostly Black-style formatted.
- **Notes:** No formatter output or CI evidence is provided. Compliance requires `black --check .` to pass; cannot be verified from source alone.

## §1.3 Code Linting
- **Status:** PARTIAL
- **Evidence:** Source is generally lint-friendly, but no lint report is provided.
- **Notes:** Requirement is explicit that `ruff check .` must pass. This cannot be confirmed from code alone.

## §1.4 Unit Testing
- **Status:** FAIL
- **Evidence:** No `tests/` files or pytest test code included anywhere in provided source.
- **Notes:** Requirement demands tests for programmatic logic including success and error cases. None are present.

## §1.5 Version Pinning
- **Status:** PASS
- **Evidence:**  
  - `requirements.txt` pins packages with exact `==` versions.  
  - `Dockerfile` uses `FROM python:3.12.1-slim`.
- **Notes:** Requirement satisfied.

---

## §2.1 StateGraph Mandate
- **Status:** PARTIAL
- **Evidence:**  
  - Logic implemented as StateGraphs in many files: `app/flows/message_pipeline.py`, `app/flows/retrieval_flow.py`, `app/flows/search_flow.py`, `app/flows/context_assembly.py`, etc.  
  - Route handlers are mostly thin and invoke flows: `app/routes/mcp.py`, `app/routes/metrics.py`, `app/routes/chat.py`.  
  - Standard LangChain/LangGraph components are used: `ChatOpenAI`, `OpenAIEmbeddings`, `ToolNode`, `MemorySaver`.
- **Notes:**  
  - Some application logic still exists outside StateGraphs:
    - `app/routes/mcp.py` handles protocol branching (`initialize`, `tools/list`, `tools/call`) imperatively.
    - `app/routes/chat.py` performs request parsing/selection of last user message and streaming formatting logic outside a graph.
    - `app/imperator/state_manager.py` contains business logic not implemented as a StateGraph.
    - `app/workers/arq_worker.py` contains retry/dead-letter orchestration imperatively.
  - Also uses raw `httpx` for provider model discovery in `app/token_budget.py`; this is justified enough because it queries `/models`, not chat inference.

## §2.2 State Immutability
- **Status:** PASS
- **Evidence:**  
  - Nodes consistently return new dicts, e.g. `app/flows/message_pipeline.py::check_idempotency`, `store_message`; `app/flows/retrieval_flow.py::load_window`; `app/flows/memory_extraction.py::build_extraction_text`.
- **Notes:** No in-place mutation of incoming state observed inside node functions.

## §2.3 Checkpointing
- **Status:** PASS
- **Evidence:** `app/flows/imperator_flow.py` uses `MemorySaver()` and `workflow.compile(checkpointer=_checkpointer)`.
- **Notes:** Requirement says checkpointing where applicable; Imperator uses it.

---

## §3.1 No Hardcoded Secrets
- **Status:** PARTIAL
- **Evidence:**  
  - Credentials are read from env vars in `app/config.py::get_api_key`, `app/database.py`, `app/memory/mem0_client.py`.  
  - Compose uses `env_file: ./config/credentials/.env` for app container and Docker secret for Postgres password in `docker-compose.yml`.
- **Notes:**  
  - No `config/credentials/.env.example` is provided in the supplied source.  
  - `docker-compose.yml` hardcodes `NEO4J_AUTH=none`, which avoids secrets but also means Neo4j is unauthenticated. This does not violate the narrow "no hardcoded secrets" clause, but example credential-template requirement is unmet elsewhere.

## §3.2 Input Validation
- **Status:** PARTIAL
- **Evidence:**  
  - MCP inputs validated with Pydantic in `app/flows/tool_dispatch.py`.  
  - Chat request body validated by `app/models.py::ChatCompletionRequest` and `app/routes/chat.py`.  
  - Null checks exist for many DB/API results.
- **Notes:**  
  - API responses are not consistently validated before use. Example: `app/token_budget.py::_query_provider_context_length()` trusts JSON structure from provider; `app/flows/search_flow.py`, `app/flows/retrieval_flow.py` trust embedding API outputs; `mem0.search()` outputs are only loosely type-checked.
  - So external inputs are only partially validated.

## §3.3 Null/None Checking
- **Status:** PASS
- **Evidence:**  
  - `app/flows/retrieval_flow.py::load_window` checks `if window is None`.  
  - `app/flows/message_pipeline.py::store_message` checks conversation existence.  
  - `app/flows/embed_pipeline.py::fetch_message` checks missing row.  
  - `app/imperator/state_manager.py::_read_state_file` and `_conversation_exists`.
- **Notes:** Requirement met broadly.

---

## §4.1 Logging to stdout/stderr
- **Status:** PASS
- **Evidence:** `app/logging_setup.py::setup_logging()` configures `StreamHandler(sys.stdout)`; Nginx logs to `/dev/stdout` and `/dev/stderr` in `nginx/nginx.conf`.
- **Notes:** No app log files used.

## §4.2 Structured Logging
- **Status:** PASS
- **Evidence:** `app/logging_setup.py::JsonFormatter.format()` outputs JSON with `timestamp`, `level`, `message`, `logger`, plus context fields.
- **Notes:** Requirement met.

## §4.3 Log Levels
- **Status:** PARTIAL
- **Evidence:**  
  - Default INFO set in `app/logging_setup.py::setup_logging()`.
  - `app/config.py::get_log_level()` exists.
- **Notes:**  
  - Configurable log level is not actually applied. `setup_logging()` hardcodes root logger and `context_broker` logger to INFO and never consumes `config.yml` value.
  - Also uses Python `WARNING` instead of literal `WARN`, though that is acceptable operationally.

## §4.4 Log Content
- **Status:** PARTIAL
- **Evidence:**  
  - Health check successes are filtered by `app/logging_setup.py::HealthCheckFilter`.  
  - Errors and lifecycle events are logged in `app/main.py`, worker functions, and flows.
- **Notes:**  
  - Full request/response bodies are not logged, good.  
  - But stack traces may include sensitive context if upstream exceptions include it; no redaction strategy exists.  
  - Performance metrics logged only in some places, not consistently.

## §4.5 Specific Exception Handling
- **Status:** FAIL
- **Evidence:** `app/main.py` defines `@app.exception_handler(Exception)` with blanket catch.
- **Notes:** Requirement explicitly forbids blanket `except Exception:`. The global exception handler violates this directly.

## §4.6 Resource Management
- **Status:** PASS
- **Evidence:**  
  - File access via context managers: `app/config.py::load_config`, `app/imperator/state_manager.py::_read_state_file`, `_write_state_file`.  
  - HTTP client uses `async with` in `app/database.py::check_neo4j_health`, `app/token_budget.py::_query_provider_context_length`.  
  - DB transactions managed with `async with` in `app/flows/message_pipeline.py::store_message`, `app/flows/context_assembly.py::consolidate_archival_summary`.
- **Notes:** Good use of context managers and explicit close functions.

## §4.7 Error Context
- **Status:** PASS
- **Evidence:**  
  - `app/flows/context_assembly.py` logs include window IDs.  
  - `app/workers/arq_worker.py` logs message IDs, conversation IDs, queue names.  
  - `app/main.py` logs request method/path in global exception handler.
- **Notes:** Generally sufficient context included.

## §4.8 Pipeline Observability
- **Status:** FAIL
- **Evidence:** Pipelines have some normal logging, e.g. `app/flows/context_assembly.py`, `app/workers/arq_worker.py`.
- **Notes:**  
  - No configurable verbose logging mode exists.
  - No per-stage timing within pipelines.
  - No config toggle enabling richer intermediate-state logging.

---

## §5.1 No Blocking I/O
- **Status:** PARTIAL
- **Evidence:**  
  - Async DB and Redis libraries used: `asyncpg`, `redis.asyncio`, `httpx.AsyncClient`.  
  - `asyncio.sleep()` used rather than `time.sleep()` in async loops.
  - Mem0 synchronous calls are offloaded via `run_in_executor()` in `app/flows/memory_extraction.py` and `app/flows/memory_search_flow.py`.
- **Notes:**  
  - `app/flows/search_flow.py::rerank_results()` instantiates `CrossEncoder(model_name)` inside an async function before offloading prediction. Model loading is synchronous and potentially blocking.
  - `app/flows/embed_pipeline.py` / other flows instantiate SDK clients synchronously, but that is usually minor. The CrossEncoder load is the clearest violation risk.

---

## §6.1 MCP Transport
- **Status:** PASS
- **Evidence:** `app/routes/mcp.py` implements `GET /mcp` SSE and `POST /mcp`; `nginx/nginx.conf` proxies `/mcp` with SSE-friendly headers.
- **Notes:** Requirement met.

## §6.2 Tool Naming
- **Status:** PASS
- **Evidence:** Tool names in `app/routes/mcp.py::_get_tool_list()` use prefixes like `conv_`, `mem_`, `broker_`, `metrics_`.
- **Notes:** Requirement met.

## §6.3 Health Endpoint
- **Status:** PASS
- **Evidence:** `app/routes/health.py::health_check()` returns 200/503 with per-dependency status; checks PostgreSQL, Redis, Neo4j.
- **Notes:** Requirement met.

## §6.4 Prometheus Metrics
- **Status:** PARTIAL
- **Evidence:**  
  - `app/routes/metrics.py` exposes `/metrics`.  
  - `app/flows/metrics_flow.py` generates metrics inside a StateGraph.
- **Notes:**  
  - Request metrics are incremented in route handlers (`app/routes/mcp.py`, `app/routes/chat.py`), contrary to requirement that metrics be produced inside StateGraphs, not imperative handlers.
  - Queue depth metrics are defined in `app/metrics_registry.py` but never updated.

---

## §7.1 Graceful Degradation
- **Status:** PASS
- **Evidence:**  
  - Mem0 unavailability degrades gracefully in `app/memory/mem0_client.py`, `app/flows/memory_search_flow.py`, `app/flows/memory_extraction.py`, `app/flows/retrieval_flow.py::inject_knowledge_graph`.  
  - Reranker failure falls back in `app/flows/search_flow.py::rerank_results`.  
  - Health endpoint reports Neo4j degradation in `app/routes/health.py`.
- **Notes:** Requirement met.

## §7.2 Independent Startup
- **Status:** PARTIAL
- **Evidence:**  
  - Containers do not use `depends_on` health waits in `docker-compose.yml`.  
  - Mem0 client reattempts initialization lazily in `app/memory/mem0_client.py`.
- **Notes:**  
  - Application startup still eagerly initializes Postgres and Redis in `app/main.py::lifespan`; if unavailable, startup fails and app will not bind. That conflicts with strict independent startup.
  - Optional dependency Neo4j is request-time degraded, but core dependencies are startup-blocking.

## §7.3 Idempotency
- **Status:** PARTIAL
- **Evidence:**  
  - Message ingestion supports idempotency keys in `app/flows/message_pipeline.py::check_idempotency`.  
  - Summary insertion avoids duplicates by seq-range check in `app/flows/context_assembly.py::summarize_message_chunks`.  
  - Locking prevents concurrent extraction/assembly in `app/flows/memory_extraction.py` and `app/flows/context_assembly.py`.
- **Notes:**  
  - `conv_store_message` is idempotent only if caller provides `idempotency_key`; retries without it will duplicate messages.
  - Mem0 extraction may still duplicate graph entries if the same extracted text is submitted twice after a failure before mark-complete; no explicit dedup token passed beyond `run_id=conversation_id`, which may or may not dedup.
  - Background jobs are deduplicated only short-term with Redis TTL keys.

## §7.4 Fail Fast
- **Status:** PARTIAL
- **Evidence:**  
  - Invalid config/build type fails clearly in `app/config.py::load_config`, `get_build_type_config`.  
  - Failed migrations abort startup in `app/migrations.py::run_migrations`.  
  - Missing prompts fail with `RuntimeError` in `app/prompt_loader.py::load_prompt`.  
  - `app/imperator/state_manager.py::_conversation_exists` intentionally fails fast on DB errors.
- **Notes:**  
  - Some invalid runtime configuration silently falls back instead of failing clearly, e.g. `app/token_budget.py::_query_provider_context_length()` falls back on provider/config errors. That may be acceptable for resilience but not for the explicit “invalid model name should fail the next operation clearly” language.

---

## §8.1 Configurable External Dependencies
- **Status:** PASS
- **Evidence:** `config/config.example.yml` externalizes LLM, embeddings, reranker, package source, build types; code reads config via `app/config.py`.
- **Notes:** Requirement met.

## §8.2 Externalized Configuration
- **Status:** PARTIAL
- **Evidence:**  
  - Prompts externalized in `/config/prompts` and loaded by `app/prompt_loader.py`.  
  - Many thresholds/timeouts externalized in `config/config.example.yml` and accessed with `get_tuning()`.
- **Notes:**  
  - Several change-prone values remain hardcoded:
    - queue names in `app/workers/arq_worker.py`, `app/flows/message_pipeline.py`, `app/flows/embed_pipeline.py`
    - hardcoded model defaults in multiple files
    - `MemoryConfig(version="v1.1")`, `collection_name="mem0_memories"` in `app/memory/mem0_client.py`
    - paths like `/data/imperator_state.json` and `/config/config.yml` are intentionally fixed; likely acceptable.
  - So only partial compliance.

## §8.3 Hot-Reload vs Startup Config
- **Status:** PARTIAL
- **Evidence:**  
  - `app/config.py::load_config()` is designed for per-operation reload.  
  - Startup config cached via `load_startup_config()`.
- **Notes:**  
  - In practice, many operations use `request.app.state.config` set once at startup in `app/main.py`, then passed into flows. That means inference/model/tuning changes do **not** automatically take effect per operation in current routes and worker startup flow.
  - Workers also receive startup `config` once in `start_background_worker(config)` and never reload.

---

# REQ-002 — pMAD Engineering Requirements

## §1.1 Root Usage Pattern
- **Status:** PASS
- **Evidence:** `Dockerfile` uses root only for apt install and user/group creation, then immediately `USER ${USER_NAME}`.
- **Notes:** Requirement met.

## §1.2 Service Account
- **Status:** PARTIAL
- **Evidence:** `Dockerfile` defines dedicated non-root user/group with UID/GID args, defaults 1001/1001.
- **Notes:**  
  - Custom container runs non-root.  
  - But consistency “across the container group” is not demonstrated; backing services use stock images and no UID/GID harmonization is shown.

## §1.3 File Ownership
- **Status:** PASS
- **Evidence:** `Dockerfile` uses `COPY --chown=${USER_NAME}:${USER_NAME}` for requirements and app code.
- **Notes:** Requirement met.

## §1.4 Base Image Pinning
- **Status:** PASS
- **Evidence:**  
  - `Dockerfile`: `python:3.12.1-slim`
  - `docker-compose.yml`: `nginx:1.25.3-alpine`, `pgvector/pgvector:pg16`, `neo4j:5.15.0`, `redis:7.2.3-alpine`, `ollama/ollama:0.6.2`
- **Notes:** Tags are pinned, though `pg16` is less precise than a full patch tag; still a specific major variant. Accepting as pass.

## §1.5 Dockerfile HEALTHCHECK
- **Status:** FAIL
- **Evidence:**  
  - Only custom app `Dockerfile` includes `HEALTHCHECK`.
  - Nginx/Postgres/Neo4j/Redis healthchecks are defined in `docker-compose.yml`, not Dockerfiles.
- **Notes:** Requirement says every container’s Dockerfile includes a HEALTHCHECK. That is not true for all containers in provided source.

---

## §2.1 OTS Backing Services
- **Status:** PASS
- **Evidence:** `docker-compose.yml` uses official images for nginx, pgvector/postgres, neo4j, redis, ollama; only `context-broker-langgraph` is built from custom `Dockerfile`.
- **Notes:** Requirement met.

## §2.2 Thin Gateway
- **Status:** PASS
- **Evidence:** `nginx/nginx.conf` contains only proxy routing and timeout/buffering settings; no app logic.
- **Notes:** Sole network boundary behavior also reflected in compose networking.

## §2.3 Container-Only Deployment
- **Status:** PASS
- **Evidence:** Entire deployment described in `docker-compose.yml`; no bare-metal instructions or assumptions in code.
- **Notes:** Requirement met.

---

## §3.1 Two-Network Pattern
- **Status:** PASS
- **Evidence:** `docker-compose.yml` places gateway on `default` and `context-broker-net`; all other services only on `context-broker-net`.
- **Notes:** Requirement met.

## §3.2 Service Name DNS
- **Status:** PASS
- **Evidence:**  
  - `app/database.py` defaults use service names like `context-broker-postgres`, `context-broker-redis`.  
  - `nginx/nginx.conf` upstream uses `context-broker-langgraph:8000`.
- **Notes:** Requirement met.

---

## §4.1 Volume Pattern
- **Status:** PASS
- **Evidence:**  
  - `docker-compose.yml` mounts `./config:/config:ro` and `./data:/data` for app container.  
  - Backing service data mounts under `./data/...`.
- **Notes:** Requirement met.

## §4.2 Database Storage
- **Status:** PASS
- **Evidence:**  
  - `docker-compose.yml` mounts:
    - `./data/postgres:/var/lib/postgresql/data`
    - `./data/neo4j:/data`
    - `./data/redis:/data`
- **Notes:** Separate subdirectories are used.

## §4.3 Backup and Recovery
- **Status:** PARTIAL
- **Evidence:**  
  - Persistent state under `./data/...` in compose.  
  - Migrations automated in `app/migrations.py`.
- **Notes:**  
  - “Single host directory” is mostly satisfied for data, but config is outside `./data`.  
  - Backup/recovery procedure is documented in requirements, not implemented; that may be acceptable, but no README/doc evidence provided.
  - Migrations are forward-only/non-destructive in code.

## §4.4 Credential Management
- **Status:** PARTIAL
- **Evidence:**  
  - `docker-compose.yml` uses `env_file: ./config/credentials/.env`.  
  - Code reads env vars in `app/config.py`, `app/database.py`, `app/memory/mem0_client.py`.
- **Notes:**  
  - No `.env.example` file is present in provided source.
  - Real credentials gitignore status cannot be verified.

---

## §5.1 Docker Compose
- **Status:** PASS
- **Evidence:** Single `docker-compose.yml` present; comments explicitly instruct customization via override file.
- **Notes:** Requirement met.

## §5.2 Health Check Architecture
- **Status:** PASS
- **Evidence:**  
  - Docker/container health checks in `Dockerfile` and `docker-compose.yml`.  
  - Aggregated dependency health in `app/routes/health.py`.  
  - Nginx proxies `/health` in `nginx/nginx.conf`.
- **Notes:** Requirement met.

## §5.3 Eventual Consistency
- **Status:** PASS
- **Evidence:**  
  - Primary store in Postgres via `app/flows/message_pipeline.py::store_message`.  
  - Background async embedding/assembly/extraction queues in `enqueue_background_jobs()` and `app/workers/arq_worker.py`.  
  - Retries/backoff/dead-letter handling in `_handle_job_failure()` and `_dead_letter_sweep_loop()`.
- **Notes:** Requirement met.

---

## §6.1 MCP Endpoint
- **Status:** PASS
- **Evidence:** `app/routes/mcp.py`, proxied in `nginx/nginx.conf`.
- **Notes:** Requirement met.

## §6.2 OpenAI-Compatible Chat (optional)
- **Status:** PASS
- **Evidence:** `app/routes/chat.py` implements `/v1/chat/completions`, proxied in `nginx/nginx.conf`.
- **Notes:** Requirement met.

## §6.3 Authentication
- **Status:** PASS
- **Evidence:** No auth in app; `nginx/nginx.conf` is thin and could be extended externally.
- **Notes:** Requirement allows shipping without auth.

---

# REQ-context-broker — Context Broker System Requirements

## Part 2 §1.1 Version Pinning
- **Status:** PASS
- **Evidence:** `requirements.txt` exact pins; `Dockerfile` and `docker-compose.yml` use pinned image tags.
- **Notes:** Met.

## Part 2 §1.2 Code Formatting
- **Status:** PARTIAL
- **Evidence:** `requirements.txt` includes `black==24.2.0`.
- **Notes:** No proof `black --check .` passes.

## Part 2 §1.3 Code Linting
- **Status:** PARTIAL
- **Evidence:** `requirements.txt` includes `ruff==0.2.2`.
- **Notes:** No proof `ruff check .` passes.

## Part 2 §1.4 Unit Testing
- **Status:** FAIL
- **Evidence:** No tests included in provided source.
- **Notes:** Explicit requirement unmet.

## Part 2 §1.5 StateGraph Package Source
- **Status:** PARTIAL
- **Evidence:**  
  - `Dockerfile` supports `ARG PACKAGE_SOURCE=pypi` and `DEVPI_URL`.  
  - `config/config.example.yml` includes `packages.source`, `local_path`, `devpi_url`.
- **Notes:**  
  - Implementation does **not** actually read package source from `config.yml`; it uses Docker build args only.
  - Requirement says source is configurable via config structure shown.
  - Also local mode uses `/app/packages` but Dockerfile never copies that directory into image.

---

## Part 2 §2.1 Root Usage Pattern
- **Status:** PASS
- **Evidence:** `Dockerfile` as above.
- **Notes:** Met.

## Part 2 §2.2 Service Account
- **Status:** PARTIAL
- **Evidence:** `Dockerfile` creates dedicated user `context-broker` UID/GID 1001.
- **Notes:** Cross-container consistency not shown.

## Part 2 §2.3 File Ownership
- **Status:** PASS
- **Evidence:** `Dockerfile` uses `COPY --chown`.
- **Notes:** Met.

---

## Part 2 §3.1 Two-Volume Pattern
- **Status:** PASS
- **Evidence:** `docker-compose.yml` mounts `/config` and `/data` for app; fixed internal paths.
- **Notes:** Met.

## Part 2 §3.2 Data Directory Organization
- **Status:** PASS
- **Evidence:**  
  - `docker-compose.yml` maps:
    - `./data/postgres`
    - `./data/neo4j`
    - `./data/redis`
    - app `/data`
  - `app/imperator/state_manager.py` stores `/data/imperator_state.json`.
- **Notes:**  
  - Imperator creates/reads persistent conversation state file correctly.
  - It creates the conversation directly in DB, not via `conv_create_conversation` as text says, but functional outcome is correct.

## Part 2 §3.3 Config Directory Organization
- **Status:** PARTIAL
- **Evidence:**  
  - `docker-compose.yml` mounts `./config:/config:ro`.
  - `config/config.example.yml` exists.
- **Notes:**  
  - No `config/config.yml` provided, only example.  
  - No `config/credentials/.env` or `.env.example` included in supplied source.

## Part 2 §3.4 Credential Management
- **Status:** PARTIAL
- **Evidence:**  
  - `docker-compose.yml` uses `env_file: ./config/credentials/.env`.  
  - `app/config.py::get_api_key()` reads named env var at runtime.
- **Notes:**  
  - Missing `.env.example`.  
  - Gitignore status cannot be verified.  
  - Postgres password is stored separately as Docker secret rather than `.env`; arguably acceptable but differs from stated “all credentials in .env”.

## Part 2 §3.5 Database Storage
- **Status:** PASS
- **Evidence:** Backing services mount to bind-mounted host directories under `./data`.
- **Notes:** Met.

## Part 2 §3.6 Backup and Recovery
- **Status:** PARTIAL
- **Evidence:** Persistent state is under `./data` in compose.
- **Notes:** Requirement is largely documentation-oriented; no README/docs are included to verify backup guidance.

## Part 2 §3.7 Schema Migration
- **Status:** PASS
- **Evidence:** `app/migrations.py::run_migrations()` checks current version, applies pending forward-only migrations, and fails startup on migration error.
- **Notes:** Met.

---

## Part 2 §4.1 MCP Transport
- **Status:** PASS
- **Evidence:** `app/routes/mcp.py` and `nginx/nginx.conf`.
- **Notes:** Met.

## Part 2 §4.2 OpenAI-Compatible Chat
- **Status:** PARTIAL
- **Evidence:** `app/routes/chat.py` accepts `model`, `messages`, `stream`, `temperature`, `max_tokens`; supports SSE streaming.
- **Notes:**  
  - It accepts only a subset of “standard generation parameters”; e.g. no top_p, stop, n, presence_penalty, frequency_penalty, tools, tool_choice.
  - Good basic compatibility, but not full spec.

## Part 2 §4.3 Authentication
- **Status:** PASS
- **Evidence:** No built-in auth; gateway is nginx and can be configured externally.
- **Notes:** Met.

## Part 2 §4.4 Health Endpoint
- **Status:** PARTIAL
- **Evidence:** `app/routes/health.py` returns 200/503 and includes `database`, `cache`, `neo4j`.
- **Notes:**  
  - Requirement says healthy iff connectivity to **all backing services**. Current code treats Neo4j as degraded optional and still returns 200 when Neo4j is unavailable.
  - This is intentional graceful degradation but does not strictly match this section’s wording.

## Part 2 §4.5 Tool Naming Convention
- **Status:** PASS
- **Evidence:** Tool names in `app/routes/mcp.py::_get_tool_list()`.
- **Notes:** Met.

## Part 2 §4.6 MCP Tool Inventory
- **Status:** PASS
- **Evidence:** All listed tools are present in `_get_tool_list()` and dispatch logic in `app/flows/tool_dispatch.py`.
- **Notes:** Inventory implemented.

## Part 2 §4.5 LangGraph Mandate
- **Status:** PARTIAL
- **Evidence:** Extensive use of StateGraphs and LangChain components across `app/flows/*`.
- **Notes:** Same issues as REQ-001 §2.1:
  - nontrivial logic remains in route handlers, state manager, worker loop.
  - metrics counting also in routes.

## Part 2 §4.6 LangGraph State Immutability
- **Status:** PASS
- **Evidence:** Node functions return new dicts rather than mutating input state.
- **Notes:** Met.

## Part 2 §4.7 Thin Gateway
- **Status:** PASS
- **Evidence:** `nginx/nginx.conf` purely proxies MCP/chat/health/metrics.
- **Notes:** Met.

## Part 2 §4.8 Prometheus Metrics
- **Status:** PARTIAL
- **Evidence:**  
  - `/metrics` route exists in `app/routes/metrics.py`.  
  - `metrics_get` tool exists in `app/routes/mcp.py` and `tool_dispatch.py`.  
  - Metrics flow exists in `app/flows/metrics_flow.py`.
- **Notes:**  
  - Request metrics are generated in route handlers, not inside StateGraphs.  
  - Queue depth gauges are defined but never updated.

---

## Part 2 §5.1 Configuration File
- **Status:** PARTIAL
- **Evidence:**  
  - `app/config.py::load_config()` reads `/config/config.yml` fresh each call.  
  - `load_startup_config()` exists for startup-only caching.
- **Notes:**  
  - Actual routes/workers mostly use startup-loaded config from `app.main` app state instead of reloading each operation.
  - Therefore hot-reload behavior is not realized end-to-end.

## Part 2 §5.2 Inference Provider Configuration
- **Status:** PARTIAL
- **Evidence:**  
  - `config/config.example.yml` defines `llm`, `embeddings`, `reranker`.  
  - `ChatOpenAI` / `OpenAIEmbeddings` use config in multiple flows.
- **Notes:**  
  - LLM and embeddings support `base_url`, `model`, `api_key_env`.  
  - Reranker does **not** support OpenAI-compatible `base_url`/API key reference; only local `cross-encoder` or `none` are implemented in `app/flows/search_flow.py::rerank_results`.
  - `cohere` is mentioned in config comments but not implemented.

## Part 2 §5.3 Build Type Configuration
- **Status:** PARTIAL
- **Evidence:**  
  - `config/config.example.yml` includes `standard-tiered` and `knowledge-enriched`.  
  - `app/flows/retrieval_flow.py` honors `semantic_retrieval_pct` and `knowledge_graph_pct`.  
  - `app/flows/context_assembly.py` uses tiered summaries.
- **Notes:**  
  - Context assembly uses `tier3_pct` directly but largely ignores explicit `tier1_pct` and `tier2_pct` values during assembly/summarization decisions.
  - There is no validation that percentages sum to <= 1.0.

## Part 2 §5.4 Token Budget Resolution
- **Status:** PASS
- **Evidence:** `app/token_budget.py::resolve_token_budget()` implements override > explicit int > auto-query > fallback; `app/flows/conversation_ops_flow.py::resolve_token_budget_node()` stores resolved budget at window creation.
- **Notes:** Met.

## Part 2 §5.5 Imperator Configuration
- **Status:** FAIL
- **Evidence:** `config/config.example.yml` defines `imperator` block, but implementation does not use it meaningfully.
- **Notes:**  
  - Imperator does not create/use its own context window or retrieval build type from config.
  - `admin_tools` is not implemented at all.
  - No config/database modification tools exposed conditionally.

## Part 2 §5.6 Package Source Configuration
- **Status:** PARTIAL
- **Evidence:** `config/config.example.yml` contains package source section.
- **Notes:**  
  - Runtime/app does not consume this config; package source is a Docker build arg concern only.
  - Same issue as §1.5.

---

## Part 2 §6.1 Logging to stdout/stderr
- **Status:** PASS
- **Evidence:** `app/logging_setup.py`, `nginx/nginx.conf`.
- **Notes:** Met.

## Part 2 §6.2 Structured Logging
- **Status:** PASS
- **Evidence:** `app/logging_setup.py::JsonFormatter`.
- **Notes:** Met.

## Part 2 §6.3 Log Levels
- **Status:** PARTIAL
- **Evidence:** `app/config.py::get_log_level()` exists; logging defaults INFO.
- **Notes:** Config value is not applied in setup.

## Part 2 §6.4 Log Content Standards
- **Status:** PARTIAL
- **Evidence:** Health successes filtered; bodies not generally logged.
- **Notes:** No explicit secret redaction, and performance metrics are not consistently logged.

## Part 2 §6.5 Dockerfile HEALTHCHECK
- **Status:** FAIL
- **Evidence:** Only custom app `Dockerfile` has HEALTHCHECK.
- **Notes:** Requirement says every container’s Dockerfile includes HEALTHCHECK.

## Part 2 §6.6 Health Check Architecture
- **Status:** PASS
- **Evidence:** `Dockerfile`/compose health checks + `app/routes/health.py` dependency aggregation + nginx proxy.
- **Notes:** Met.

## Part 2 §6.7 Specific Exception Handling
- **Status:** FAIL
- **Evidence:** `app/main.py` global exception handler catches `Exception`.
- **Notes:** Blanket catch violates requirement.

## Part 2 §6.8 Resource Management
- **Status:** PASS
- **Evidence:** Context managers and close functions throughout code.
- **Notes:** Met.

## Part 2 §6.9 Error Context
- **Status:** PASS
- **Evidence:** IDs and operation names logged in flows and worker functions.
- **Notes:** Met.

---

## Part 2 §7.1 Graceful Degradation and Eventual Consistency
- **Status:** PASS
- **Evidence:**  
  - Degraded Neo4j/Mem0/reranker behavior as above.  
  - Async queues and retries in `message_pipeline.py` and `arq_worker.py`.
- **Notes:** Met.

## Part 2 §7.2 Independent Container Startup
- **Status:** PARTIAL
- **Evidence:** No Compose startup waits; Mem0 lazy init.
- **Notes:** App itself still fails startup if Postgres/Redis unavailable.

## Part 2 §7.3 Network Topology
- **Status:** PASS
- **Evidence:** `docker-compose.yml` exactly matches two-network pattern.
- **Notes:** Met.

## Part 2 §7.4 Docker Compose
- **Status:** PASS
- **Evidence:** Single `docker-compose.yml`; comments specify override customization; build context `.` in app service.
- **Notes:** Met.

## Part 2 §7.5 Container-Only Deployment
- **Status:** PASS
- **Evidence:** Entire architecture containerized.
- **Notes:** Met.

## Part 2 §7.6 Asynchronous Correctness
- **Status:** PARTIAL
- **Evidence:** Async libs used broadly; no `time.sleep()` in async functions.
- **Notes:** Potential blocking synchronous model load in `search_flow.py::rerank_results()`.

## Part 2 §7.7 Input Validation
- **Status:** PARTIAL
- **Evidence:** Pydantic validation for MCP/chat inputs.
- **Notes:** External API responses not fully validated.

## Part 2 §7.8 Null/None Checking
- **Status:** PASS
- **Evidence:** Repeated explicit checks throughout flows.
- **Notes:** Met.

---

## Part 2 §8.1 README
- **Status:** FAIL
- **Evidence:** No README provided in supplied source.
- **Notes:** Cannot verify required documentation; absent from material.

## Part 2 §8.2 Tool Documentation
- **Status:** PARTIAL
- **Evidence:** Tool schemas/descriptions present in `app/routes/mcp.py::_get_tool_list()`.
- **Notes:** README documentation with examples/output schemas is not provided.

## Part 2 §8.3 Config Template
- **Status:** PARTIAL
- **Evidence:** `config/config.example.yml` exists and is well documented.
- **Notes:**  
  - Missing `config/credentials/.env.example`.

---

# Summary of major noncompliance themes

## Clear FAILs
- **No tests**: REQ-001 §1.4, REQ-context-broker §1.4
- **Blanket exception catch**: REQ-001 §4.5, REQ-context-broker §6.7
- **Imperator config/admin tools not implemented**: REQ-context-broker §5.5
- **Per-container Dockerfile HEALTHCHECK requirement unmet**: REQ-002 §1.5, REQ-context-broker §6.5
- **README missing**: REQ-context-broker §8.1

## Repeated PARTIALs
- Hot-reload config is designed but not actually used per operation in routes/workers
- Metrics are partly outside StateGraphs
- Reranker provider support incomplete
- Package source config in `config.yml` not actually wired into build/runtime
- Independent startup only partially satisfied because app startup requires Postgres/Redis
- Missing `.env.example`
- Full OpenAI compatibility only partial

If you want, I can also turn this into a **tabular compliance matrix** grouped by document and section for easier review/export.