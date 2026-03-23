Here is the compliance audit for the Context Broker source code against the provided requirements documents.

### REQ-001: MAD Engineering Requirements

**1. Code Quality**
*   **§1.1 Code Clarity:** PASS. Code is well-structured, uses descriptive naming, and includes comments explaining architectural decisions (e.g., `M-13`, `M-15`).
*   **§1.2 Code Formatting:** PASS. Code appears to adhere to standard `black` formatting.
*   **§1.3 Code Linting:** PASS. Code appears clean and compliant with standard `ruff` rules.
*   **§1.4 Unit Testing:** **FAIL**. No test files (`tests/` directory or `test_*.py`) are included in the provided source code.
*   **§1.5 Version Pinning:** PASS. `requirements.txt` uses exact version pinning (`==`), and the `Dockerfile` uses pinned base images (e.g., `python:3.12.1-slim`).

**2. LangGraph Architecture**
*   **§2.1 StateGraph Mandate:** PASS. All core logic (message ingestion, retrieval, search, memory admin) is implemented as compiled StateGraphs in `app/flows/`.
*   **§2.2 State Immutability:** PASS. Node functions return new dictionaries containing only updated state variables rather than mutating the state in-place.
*   **§2.3 Checkpointing:** PASS. `app/flows/imperator_flow.py` correctly uses `MemorySaver` for multi-turn state persistence.

**3. Security Posture**
*   **§3.1 No Hardcoded Secrets:** **PARTIAL**. Credentials are correctly loaded from environment variables at runtime. However, the requirement states: *"Repository ships example/template credential files."* The provided source code does not include the required `config/credentials/.env.example` file.
*   **§3.2 Input Validation:** PASS. All MCP tool inputs are validated using Pydantic models in `app/models.py` before being passed to flows.
*   **§3.3 Null/None Checking:** PASS. Explicit `None` checks are used appropriately before attribute access (e.g., `if window is None:`).

**4. Logging and Observability**
*   **§4.1 Logging to stdout/stderr:** PASS. `app/logging_setup.py` configures `logging.StreamHandler(sys.stdout)`.
*   **§4.2 Structured Logging:** PASS. `JsonFormatter` formats logs as single-line JSON objects.
*   **§4.3 Log Levels:** PASS. Log level is configurable via `config.yml` and applied dynamically.
*   **§4.4 Log Content:** PASS. Logs capture lifecycle events, metrics, and errors with context. No secrets are logged.
*   **§4.5 Specific Exception Handling:** PASS. Broad `except Exception:` blocks are avoided in favor of specific exceptions (e.g., `(openai.APIError, httpx.HTTPError, ValueError)`).
*   **§4.6 Resource Management:** PASS. Context managers (`async with`, `with open`) are used for database connections and file handles.
*   **§4.7 Error Context:** PASS. Errors include relevant identifiers (e.g., `window_id`, `conversation_id`) and `exc_info=True`.
*   **§4.8 Pipeline Observability:** PASS. `verbose_log` and `verbose_log_auto` are used to log stage entry/exit and timing, togglable via `tuning.verbose_logging`.

**5. Async Correctness**
*   **§5.1 No Blocking I/O:** **FAIL**. Synchronous file I/O is used inside `async def` functions. 
    *   *Evidence:* In `app/flows/imperator_flow.py`, `_config_read_tool` uses a synchronous `with open(...)` and `f.read()`. In `app/prompt_loader.py`, `load_prompt` uses synchronous `os.stat` and `path.read_text()`, but it is called directly by async nodes like `summarize_message_chunks` in `app/flows/context_assembly.py`. `load_config()` also uses synchronous `open()` and is called by `verbose_log_auto` inside async nodes.

**6. Communication**
*   **§6.1 MCP Transport:** PASS. `app/routes/mcp.py` implements HTTP/SSE transport.
*   **§6.2 Tool Naming:** PASS. Tools use the required domain prefixes (`conv_*`, `mem_*`).
*   **§6.3 Health Endpoint:** PASS. `app/routes/health.py` returns 200/503 with per-dependency status.
*   **§6.4 Prometheus Metrics:** PASS. `app/routes/metrics.py` exposes metrics generated inside StateGraphs.

**7. Resilience**
*   **§7.1 Graceful Degradation:** PASS. Failures in optional components (Neo4j, Mem0, Reranker) are caught and return degraded status without crashing the flow.
*   **§7.2 Independent Startup:** PASS. `app/main.py` catches Postgres connection errors and starts a background retry loop (`_postgres_retry_loop`), allowing the container to start.
*   **§7.3 Idempotency:** PASS. `store_message` uses `ON CONFLICT (idempotency_key) DO NOTHING`. Assembly uses Redis locks and checks for existing summaries.
*   **§7.4 Fail Fast:** PASS. `app/migrations.py` raises `RuntimeError` if a migration fails, preventing startup with a bad schema.

**8. Configuration**
*   **§8.1 Configurable External Dependencies:** PASS. Inference providers and models are configurable in `config.yml`.
*   **§8.2 Externalized Configuration:** PASS. Prompts are loaded from `/config/prompts/` and tuning parameters from `config.yml`.
*   **§8.3 Hot-Reload vs Startup Config:** PASS. `load_config()` checks file `mtime` to hot-reload LLM and tuning settings, while `load_startup_config()` caches infrastructure settings.

---

### REQ-002: pMAD Engineering Requirements

**1. Container Construction**
*   **§1.1 Root Usage Pattern:** PASS. `Dockerfile` creates a user and switches to `USER ${USER_NAME}`.
*   **§1.2 Service Account:** PASS. Runs as UID/GID 1001.
*   **§1.3 File Ownership:** PASS. `COPY --chown=${USER_NAME}:${USER_NAME}` is used.
*   **§1.4 Base Image Pinning:** PASS. `Dockerfile` and `docker-compose.yml` use pinned tags (e.g., `python:3.12.1-slim`, `nginx:1.25.3-alpine`).
*   **§1.5 Dockerfile HEALTHCHECK:** PASS. Present in `Dockerfile`.

**2. Container Architecture**
*   **§2.1 OTS Backing Services:** PASS. Postgres, Neo4j, and Redis use official unmodified images.
*   **§2.2 Thin Gateway:** PASS. `nginx/nginx.conf` is a pure routing layer.
*   **§2.3 Container-Only Deployment:** PASS. Fully containerized via `docker-compose.yml`.

**3. Network Topology**
*   **§3.1 Two-Network Pattern:** PASS. `docker-compose.yml` defines `default` (external) and `context-broker-net` (internal).
*   **§3.2 Service Name DNS:** PASS. Environment variables use service names (e.g., `POSTGRES_HOST=context-broker-postgres`).

**4. Storage**
*   **§4.1 Volume Pattern:** PASS. `/config` and `/data` are mounted via bind mounts.
*   **§4.2 Database Storage:** PASS. Backing services mount to subdirectories under `./data/`.
*   **§4.3 Backup and Recovery:** PASS. All state is under `./data/`. Migrations are forward-only.
*   **§4.4 Credential Management:** **PARTIAL**. Credentials are loaded via `env_file`, but the required `.env.example` file is missing from the repository source.

**5. Deployment**
*   **§5.1 Docker Compose:** PASS. Single `docker-compose.yml` provided.
*   **§5.2 Health Check Architecture:** PASS. Docker HEALTHCHECKs and HTTP `/health` proxying are implemented.
*   **§5.3 Eventual Consistency:** PASS. Postgres is the source of truth; ARQ workers handle async processing with retries and dead-letter queues.

**6. Interface**
*   **§6.1 MCP Endpoint:** PASS. Exposed at `/mcp`.
*   **§6.2 OpenAI-Compatible Chat (optional):** PASS. Exposed at `/v1/chat/completions`.
*   **§6.3 Authentication:** PASS. Designed to rely on gateway auth.

---

### REQ-context-broker: Functional Requirements

**1. Build System**
*   **§1.1 Version Pinning:** PASS.
*   **§1.2 Code Formatting:** PASS.
*   **§1.3 Code Linting:** PASS.
*   **§1.4 Unit Testing:** **FAIL**. No tests provided.
*   **§1.5 StateGraph Package Source:** PASS. `entrypoint.sh` dynamically handles `local`, `pypi`, and `devpi` installations based on `config.yml`.

**2. Runtime Security and Permissions**
*   **§2.1 Root Usage Pattern:** PASS.
*   **§2.2 Service Account:** PASS.
*   **§2.3 File Ownership:** PASS.

**3. Storage and Data**
*   **§3.1 Two-Volume Pattern:** PASS.
*   **§3.2 Data Directory Organization:** **PARTIAL**. The requirement states: *"On first boot, the Imperator creates its conversation via conv_create_conversation and writes the returned ID"*. 
    *   *Evidence:* In `app/imperator/state_manager.py`, `_create_imperator_conversation()` executes a direct SQL `INSERT INTO conversations` rather than invoking the `conv_create_conversation` tool/flow.
*   **§3.3 Config Directory Organization:** PASS.
*   **§3.4 Credential Management:** **PARTIAL**. Missing `.env.example`.
*   **§3.5 Database Storage:** PASS.
*   **§3.6 Backup and Recovery:** PASS.
*   **§3.7 Schema Migration:** PASS.

**4. Communication and Integration**
*   **§4.1 MCP Transport:** PASS.
*   **§4.2 OpenAI-Compatible Chat:** PASS.
*   **§4.3 Authentication:** PASS.
*   **§4.4 Health Endpoint:** PASS.
*   **§4.5 Tool Naming Convention:** PASS.
*   **§4.6 MCP Tool Inventory:** PASS. All required tools are present in `app/routes/mcp.py`.
*   **§4.5 (LangGraph Mandate):** PASS. All logic is in StateGraphs. The deviation to use Mem0's native API for graph traversal is explicitly justified in `app/flows/retrieval_flow.py`.
*   **§4.6 (LangGraph State Immutability):** PASS.
*   **§4.7 Thin Gateway:** PASS. Nginx proxies `/health` to LangGraph, which performs the actual checks.
*   **§4.8 Prometheus Metrics:** PASS.

**5. Configuration**
*   **§5.1 Configuration File:** PASS.
*   **§5.2 Inference Provider Configuration:** PASS.
*   **§5.3 Build Type Configuration:** PASS. `standard-tiered` and `knowledge-enriched` are defined.
*   **§5.4 Token Budget Resolution:** PASS. `app/token_budget.py` correctly prioritizes caller overrides, explicit integers, and `auto` resolution.
*   **§5.5 Imperator Configuration:** PASS.
*   **§5.6 Package Source Configuration:** PASS.

**6. Logging and Observability**
*   **§6.1 to §6.9:** PASS. Logging is structured, configurable, and includes context.

**7. Resilience and Deployment**
*   **§7.1 Graceful Degradation and Eventual Consistency:** PASS. `app/flows/message_pipeline.py` catches Redis connection errors and ensures the message is safely stored in Postgres even if background job enqueuing fails.
*   **§7.2 Independent Container Startup:** PASS.
*   **§7.3 Network Topology:** PASS.
*   **§7.4 Docker Compose:** PASS.
*   **§7.5 Container-Only Deployment:** PASS.
*   **§7.6 Asynchronous Correctness:** **FAIL**. Synchronous file I/O (`open()`, `read_text()`) is used inside async functions (`_config_read_tool`, `load_prompt`, `load_config`), violating the rule against blocking library calls in async contexts.
*   **§7.7 Input Validation:** PASS.
*   **§7.8 Null/None Checking:** PASS.

**8. Documentation**
*   **§8.1 README:** **FAIL**. No `README.md` file is included in the provided source code.
*   **§8.2 Tool Documentation:** **PARTIAL**. Tools are documented in the code (`_get_tool_list()`), but the requirement mandates documentation in the README, which is missing.
*   **§8.3 Config Template:** **PARTIAL**. `config.example.yml` is provided, but `config/credentials/.env.example` is missing.