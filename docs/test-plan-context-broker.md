# Test Plan: Context Broker

This document defines the test plan for the Context Broker, translating its requirements (REQ-context-broker, REQ-001, REQ-002) and architecture (HLD-context-broker) into verifiable assertions. It is written in accordance with REQ-103.

## 1. Engineering Requirements Verification

As a prerequisite for all other testing, the following automated checks must pass. These verify compliance with REQ-001 and REQ-002.

- **Code Formatting:** `black --check .` must pass without errors.
- **Code Linting:** `ruff check .` must pass without errors.
- **Unit Testing:** `pytest` must execute and all unit tests must pass.
- **Version Pinning:** A static check must verify that all dependencies in `requirements.txt` use `==` and all `FROM` instructions in Dockerfiles use a specific version tag (e.g., `python:3.11.7-slim`, not `python:3.11`).
- **Credential File Presence:** A static check must verify the repository contains `config/credentials/.env.example` and that `.gitignore` contains an entry for `.env`.
- **OTS Backing Services:** A static check on `docker-compose.yml` must verify that `postgres`, `neo4j`, and `redis` services use an official `image` and do not have a `build` context.
- **USER Directive Placement:** A static check on the `langgraph` Dockerfile must verify that the `USER` directive immediately follows the `useradd` command.

## 2. Test Strategy

The testing approach is layered to ensure comprehensive coverage from individual functions to the fully deployed system.

- **Unit Tests:** Written with `pytest`, these tests target individual functions and StateGraph nodes in isolation. External dependencies (databases, LLM APIs, etc.) are mocked to ensure tests are fast and focused on the logic under test. Unit tests will include checks for async correctness (detecting blocking calls) and LangGraph state immutability.
- **Integration Tests:** These tests verify the interactions between the `context-broker-langgraph` container and its backing services (Postgres, Redis, Neo4j). The test suite will use Docker Compose to provision a complete, isolated environment for each test run. Tests will interact with the service via its internal API, not through the gateway.
- **End-to-End (E2E) Tests:** These tests simulate an external client interacting with the Context Broker through its public-facing interfaces (`/mcp`, `/v1/chat/completions`). The entire Docker Compose stack is running, and tests verify the system as a whole, from request ingress at the Nginx gateway to data persistence and retrieval.

## 3. Coverage Targets

- **Functional Requirements:** Every testable requirement defined in `REQ-context-broker.md` must be covered by at least one test case.
- **Code:** All programmatic logic (excluding trivial getters/setters and boilerplate) must have unit tests covering the primary success path and common, anticipated error conditions, per REQ-001 §1.4.
- **Interfaces:** All external endpoints (`/mcp` tools, `/v1/chat/completions`, `/health`, `/metrics`) must have integration or E2E tests covering valid inputs, invalid/malformed inputs, and relevant boundary conditions.
- **Resilience:** All specified resilience mechanisms (e.g., graceful degradation, fail-fast) must be explicitly verified by a dedicated integration test.

## 4. Test Cases by Component

### 4.1 Build System & Code Quality (REQ §1)

- **Test Case:** Verify `local` package source configuration.
  - **Input:** `packages.source` set to `local` in `config.yml`.
  - **Expected Outcome:** The container builds successfully, installing wheels from the specified `local_path`.
- **Test Case:** Verify `pypi` and `devpi` package source configurations.
  - **Input:** `packages.source` set to `pypi` or `devpi` (with a mock devpi server).
  - **Expected Outcome:** The container build process attempts to install packages from the configured remote index.

### 4.2 Container Architecture & Security (REQ §2, REQ-002)

- **Test Case:** Verify non-root user execution.
  - **Input:** Start the `context-broker-langgraph` container.
  - **Expected Outcome:** The application process runs under the UID/GID of the `context-broker` service account, not root.
- **Test Case:** Verify two-network topology.
  - **Input:** Inspect the running Docker Compose network configuration.
  - **Expected Outcome:** The `context-broker` (gateway) container is connected to both the external (`default`) and internal (`context-broker-net`) networks. All other containers are connected only to the internal network.
- **Test Case:** Verify file ownership.
  - **Input:** Inspect file permissions inside the `context-broker-langgraph` container.
  - **Expected Outcome:** Application files are owned by the `context-broker` service account, set via `COPY --chown`.
- **Test Case:** Verify Dockerfile `HEALTHCHECK` directive.
  - **Input:** Start the full container stack.
  - **Expected Outcome:** After the start period, `docker inspect` for each container shows its health status as `healthy`.
- **Test Case:** Verify Thin Gateway configuration.
  - **Input:** `exec` into the running `context-broker` (nginx) container.
  - **Expected Outcome:** The contents of `nginx.conf` contain only routing directives (e.g., `location`, `proxy_pass`) and do not contain application logic (e.g., Lua scripts).
- **Test Case:** Verify inter-container communication uses service names.
  - **Input:** Inspect the configuration used by the `context-broker-langgraph` container at runtime.
  - **Expected Outcome:** Connection strings for backing services use Docker Compose service names (e.g., `context-broker-postgres`) and not IP addresses.

### 4.3 Configuration System (REQ §5)

- **Test Case:** Verify fail-fast on invalid startup configuration.
  - **Input:** A `config.yml` with an invalid database connection string.
  - **Expected Outcome:** The `context-broker-langgraph` container fails to start and logs a clear error message about the configuration issue.
- **Test Case:** Verify hot-reload of inference provider configuration.
  - **Input:** While the system is running, modify `config.yml` to point the `llm` provider to a different model.
  - **Expected Outcome:** The next API call that uses the LLM (e.g., via the Imperator) uses the new model without a container restart.
- **Test Case:** Verify token budget resolution.
  - **Input:** Create context windows with `max_context_tokens` set to `auto`, an explicit integer, and with a caller override.
  - **Expected Outcome:** The system correctly resolves the token budget by querying the model endpoint for `auto`, using the explicit value, and prioritizing the caller override.
- **Test Case:** Verify Imperator `admin_tools` toggle.
  - **Input:** Set `imperator.admin_tools` to `true` and `false`.
  - **Expected Outcome:** With `true`, the Imperator can execute admin-level tools (e.g., read config). With `false`, attempts to use these tools are denied.

### 4.4 Storage & Data Persistence (REQ §3)

- **Test Case:** Verify two-volume pattern.
  - **Input:** Start the system with host directories mapped to `/config` and `/data`.
  - **Expected Outcome:** `config.yml` is read from the `/config` mount. Database files and `imperator_state.json` are written to the `/data` mount.
- **Test Case:** Verify idempotency of message storage.
  - **Input:** Call `conv_store_message` twice in a row with the same message content and the same client-provided idempotency key.
  - **Expected Outcome:** The message is stored in the database only once. The second call succeeds without creating a duplicate record.
- **Test Case:** Verify schema migration on startup.
  - **Input:** Start the application against a database with an older schema version and a pending migration script.
  - **Expected Outcome:** The application automatically applies the migration, updates the schema version, and starts successfully. The migration must not drop existing data.
- **Test Case:** Verify credential loading from `.env` file.
  - **Input:** Define a test API key in a test `.env` file and configure `docker-compose.yml` to use it via `env_file`.
  - **Expected Outcome:** Inside the `context-broker-langgraph` container, the key is available as an environment variable and is correctly read by the application.
- **Test Case:** Verify no anonymous volumes are created.
  - **Input:** Start the full Docker Compose stack.
  - **Expected Outcome:** An inspection of Docker volumes (e.g., via `docker volume ls`) shows no anonymous volumes associated with the `context-broker` project. All persistent data for `postgres`, `neo4j`, and `redis` is confirmed to reside within the host-side bind mount directory (`./data`).

### 4.5 Async Processing Pipelines (HLD §4, §9)

- **Test Case:** Verify end-to-end pipeline execution.
  - **Input:** Call `conv_store_message` to add a new message to a conversation.
  - **Expected Outcome:** After a short delay, verify that: (1) an embedding vector is added to the message row in Postgres, (2) a new context assembly snapshot is created, and (3) new entities/facts are extracted to Neo4j.
- **Test Case:** Verify intermediate pipeline outputs (Pipeline End-to-End Verification).
  - **Input:** Store a message and use a debug/test mode to inspect the system state after each background job type completes.
  - **Expected Outcome:** After the `embedding_jobs` worker runs, the `vector` column in `conversation_messages` is populated. After the `context_assembly_jobs` worker runs, the `conversation_summaries` table is updated. After the `memory_extraction_jobs` worker runs, a Cypher query against Neo4j shows new nodes/relationships.
- **Test Case:** Verify Redis lock prevents concurrent context assembly.
  - **Input:** In an integration test, create a single context window. Programmatically enqueue two `context_assembly_jobs` for that same window in rapid succession, simulating a race condition.
  - **Expected Outcome:** By inspecting verbose logs with timestamps, verify that the second job did not start until after the first job completed. This confirms the distributed lock enforced serial execution for the critical section.

### 4.6 Interface & Endpoints (REQ §4)

- **Test Case:** Verify all MCP tools.
  - **Input:** For each tool listed in REQ §4.6 (e.g., `conv_create_conversation`, `mem_search`, `metrics_get`), execute calls with: (a) valid parameters, (b) missing required parameters, and (c) parameters with invalid data types.
  - **Expected Outcome:** (a) The tool executes successfully. (b, c) The MCP endpoint returns a validation error before the tool logic is invoked.
- **Test Case:** Verify OpenAI-compatible endpoint (`/v1/chat/completions`).
  - **Input:** Send requests using an OpenAI client library with `stream=true` and `stream=false`.
  - **Expected Outcome:** The endpoint returns a valid, spec-compliant response in both streaming (SSE) and non-streaming (JSON) formats.
- **Test Case:** Verify Health Endpoint (`/health`).
  - **Input:** (a) All services running. (b) Stop the `context-broker-neo4j` container.
  - **Expected Outcome:** (a) Returns `200 OK` with all dependencies marked `"ok"`. (b) Returns `503 Service Unavailable` with `neo4j` marked as unhealthy.
- **Test Case:** Verify Metrics Endpoint (`/metrics`).
  - **Input:** Make several API calls that invoke a StateGraph, then query `GET /metrics`.
  - **Expected Outcome:** The endpoint returns data in Prometheus exposition format. Relevant counters have incremented. Tracing or code inspection confirms metrics are generated within StateGraph nodes.
- **Test Case:** Verify external API response validation.
  - **Input:** In a unit test, mock an external dependency (e.g., an LLM provider) to return a malformed or unexpected response.
  - **Expected Outcome:** The application catches the invalid response, logs an error, and fails the operation gracefully without corrupting internal state.

### 4.7 Imperator Agent (REQ §4, HLD §10)

- **Test Case:** Verify state persistence across restarts via LangGraph checkpointing.
  - **Input:** Start a conversation with the Imperator. Stop and restart the Docker Compose stack.
  - **Expected Outcome:** The Imperator's `conversation_id` is read from `/data/imperator_state.json`, and it resumes the same conversation. The underlying checkpointing backend contains a corresponding record for the conversation thread, confirming the persistence mechanism.
- **Test Case:** Verify context retrieval with different build types.
  - **Input:** Configure the Imperator with `build_type: standard-tiered` and then with `knowledge-enriched`. Ask it a question that requires deep memory.
  - **Expected Outcome:** The response when using `knowledge-enriched` should reflect information from the knowledge graph and semantic search, which would be absent in the `standard-tiered` response.

### 4.8 Resilience & Deployment (REQ §7, HLD §11)

- **Test Case:** Verify graceful degradation.
  - **Input:** Stop the `context-broker-neo4j` container. Call `conv_retrieve_context` for a window with the `knowledge-enriched` build type.
  - **Expected Outcome:** The system does not crash. It logs a warning, the `/health` endpoint reports a degraded state, and the context window is assembled successfully but without the knowledge graph component.
- **Test Case:** Verify independent container startup.
  - **Input:** Use a script to start the containers in a random order with delays.
  - **Expected Outcome:** The system becomes fully operational once all containers are running, regardless of startup order.

### 4.9 Logging and Observability (REQ §6, REQ-001 §4)

- **Test Case:** Verify structured JSON logging.
  - **Input:** Trigger a standard operation and an error condition.
  - **Expected Outcome:** Capture container logs via `docker logs`. Assert that each log line is a valid JSON object containing `timestamp`, `level`, and `message` fields. The error event must have `level: ERROR` and include relevant context.
- **Test Case:** Verify pipeline observability verbose mode.
  - **Input:** (a) Run a pipeline with verbose logging disabled. (b) Enable verbose logging via `config.yml` and run the same pipeline.
  - **Expected Outcome:** (a) Logs show only standard lifecycle events. (b) Captured logs now include detailed per-stage messages with intermediate outputs and timing data.

### 4.10 Async Correctness (REQ-001 §5)

- **Test Case:** Verify no blocking I/O in async functions.
  - **Input:** Execute unit tests for all `async` functions.
  - **Expected Outcome:** The test suite uses a tool (e.g., a pytest plugin or static analyzer) to detect and fail any test that makes a blocking I/O call (e.g., `time.sleep()`, synchronous DB calls) within an async context.

### 4.11 LangGraph Architecture (REQ-001 §2)

- **Test Case:** Verify LangGraph Mandate.
  - **Input:** Static analysis of the `context-broker-langgraph` source code.
  - **Expected Outcome:** HTTP route handlers are thin wrappers that primarily instantiate and invoke a compiled LangGraph. Substantive application logic resides within StateGraph nodes, not the route handlers.
- **Test Case:** Verify StateGraph node immutability.
  - **Input:** Unit tests for individual StateGraph nodes.
  - **Expected Outcome:** For each node, the test passes a copy of an input state dictionary. After the node function executes, the test asserts that the original dictionary remains unmodified.

## 5. Integration Test Approach

- Tests will be written using `pytest` and the `httpx` library for making asynchronous HTTP requests.
- The test suite will use `docker-compose` to manage the lifecycle of the Context Broker and its backing services. A dedicated `docker-compose.test.yml` override file may be used to expose ports or configure volumes for testability.
- Pytest fixtures will be used to manage the container lifecycle (setup/teardown) and to provide pre-configured API clients for interacting with the system's endpoints.
- Assertions will be made against API responses. For verifying state changes that are not directly exposed by the API (e.g., background job effects), tests will connect directly to the backing services (e.g., using `psycopg` for Postgres) to inspect the data.

## 6. What Is Not Tested

- **LLM Output Quality:** This plan verifies that the system correctly formats data and communicates with configured LLM providers. It does not qualitatively evaluate the correctness, style, or factual accuracy of the text generated by the external LLMs. Evaluating model output is out of scope.
- **Third-Party Container Security:** The plan verifies that the custom `context-broker-langgraph` container is built and run according to security best practices (e.g., non-root user). It does not include vulnerability scanning of the official, unmodified third-party images (Nginx, Postgres, Redis, Neo4j), which is assumed to be part of a separate supply-chain security process.
- **Large-Scale Performance Testing:** This plan includes tests to ensure the system is functional and resilient. It does not include large-scale load or stress testing to determine maximum requests-per-second, latency at scale, or other performance benchmarks. This requires a dedicated performance test plan.
- **Usability:** As the Context Broker is a backend service with no direct user-facing UI, usability testing is not applicable.

## 7. Test Environment

The test environment is fully defined and provisioned by Docker and Docker Compose. No host-level dependencies are required beyond a working Docker installation.

- **Infrastructure:** The environment is defined by the project's `docker-compose.yml` and the associated Dockerfiles.
- **Components:** The environment consists of the five specified containers (`context-broker`, `context-broker-langgraph`, `context-broker-postgres`, `context-broker-neo4j`, `context-broker-redis`), using the exact image versions pinned in the project configuration.
- **Execution:** Tests are executed by a Python test runner (`pytest`) from a host or CI environment that has access to the Docker daemon.