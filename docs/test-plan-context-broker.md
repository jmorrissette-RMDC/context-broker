# Test Plan: Context Broker

This document defines the test plan for the Context Broker, translating its requirements (REQ-context-broker, REQ-001, REQ-002) and architecture (HLD-context-broker) into verifiable assertions. It is written in accordance with REQ-103.

## 1. Engineering Requirements Verification

As a prerequisite for all other testing, the following automated checks must pass. These verify compliance with REQ-001 and REQ-002.

- **Code Formatting:** `black --check .` must pass without errors.
- **Code Linting:** `ruff check .` must pass without errors.
- **Unit Testing:** `pytest` must execute and all unit tests must pass.
- **Version Pinning:** A static check must verify that all dependencies in `requirements.txt` use `==` and all `FROM` instructions in Dockerfiles use a specific version tag (e.g., `python:3.11.7-slim`, not `python:3.11`).
- **Credential File Presence:** A static check must verify the repository contains `config/credentials/.env.example` and that `.gitignore` contains an entry for `.env`.
- **OTS Backing Services:** A static check on `docker-compose.yml` must verify that `postgres` and `neo4j` services use an official `image` and do not have a `build` context.
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
- **Test Case:** Verify Postgres advisory lock prevents concurrent context assembly.
  - **Input:** In an integration test, create a single context window. Trigger two assembly jobs for that same window in rapid succession, simulating a race condition.
  - **Expected Outcome:** By inspecting verbose logs with timestamps, verify that the second job did not start until after the first job completed. This confirms the advisory lock enforced serial execution for the critical section.
- **Test Case:** Verify embedding generation via Infinity.
  - **Input:** Store a message via `conv_store_message`. Wait for the embedding worker to process.
  - **Expected Outcome:** The `vector` column in `conversation_messages` is populated. Query the Infinity `/v1/embeddings` endpoint directly to confirm it returns vectors of the expected dimension for the configured model (nomic-embed-text-v1.5: 768 dimensions).
- **Test Case:** Verify reranking via Infinity `/v1/rerank` API.
  - **Input:** Call `conv_search_messages` with a query that returns multiple results and reranking enabled (`reranker.provider: api`).
  - **Expected Outcome:** Results are reranked by the Infinity reranker. Verify by comparing the order of results with reranking enabled vs disabled (`provider: none`). The reranked order should differ from raw RRF order for a sufficiently diverse result set.

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

### 4.12 Cross-Provider Inference (provider-capabilities.md)

These tests verify that each supported inference provider works correctly with the Context Broker's configuration system. Each test swaps the relevant config section to point at a real provider, makes a single cheap API call, and verifies a valid response.

- **Test Case:** Verify LLM provider compatibility (6 providers).
  - **Providers:** OpenAI, Anthropic (via adapter), Google Gemini, xAI (Grok), Together AI, Ollama (local).
  - **Input:** For each provider, configure `llm.base_url` and `llm.model` to a cheap model. Send a single `/v1/chat/completions` request with a trivial prompt.
  - **Expected Outcome:** Each provider returns a valid chat completion response. The response contains at least one choice with a non-empty message.
- **Test Case:** Verify embedding provider compatibility (5 providers).
  - **Providers:** OpenAI, Google Gemini, Together AI, Ollama (local), Infinity (local).
  - **Input:** For each provider, configure `embeddings.base_url` and `embeddings.model`. Call the embedding endpoint with a short text string.
  - **Expected Outcome:** Each provider returns a valid embedding vector. The vector dimension matches the configured model's expected output.
- **Test Case:** Verify reranking provider compatibility (4 providers).
  - **Providers:** Infinity (local), Together AI, Cohere, Jina.
  - **Input:** For each provider, configure `reranker.base_url` and `reranker.model`. Call `/v1/rerank` with a query and 3 documents.
  - **Expected Outcome:** Each provider returns results with `index` and `relevance_score` fields. Results are ordered by relevance score descending.

### 4.13 Imperator Tool Organization (REQ §5.5)

- **Test Case:** Verify tool discovery from modules.
  - **Input:** Call `_collect_tools` with `admin_tools: false` and `admin_tools: true`.
  - **Expected Outcome:** With `false`: returns core + diagnostic tools. With `true`: additionally returns admin tools. Tool names match expected set.
- **Test Case:** Verify tool gating for operational tools.
  - **Input:** Call `get_operational_tools` with `domain_information.enabled: true/false` and `domain_knowledge.enabled: true/false`.
  - **Expected Outcome:** Tools are included or excluded based on config flags.
- **Test Case:** Verify each tool module exports valid tools.
  - **Input:** Import `get_tools()` from each module (`diagnostic`, `admin`, `operational`).
  - **Expected Outcome:** Each returns a list of callable tool objects with `.name` attributes.

### 4.14 Message Identity — Sender/Recipient (REQ §3.5.1)

- **Test Case:** Verify `resolve_caller` with explicit user field.
  - **Input:** HTTP request with `user` field set to `"jason"`.
  - **Expected Outcome:** Returns `"jason"`.
- **Test Case:** Verify `resolve_caller` with reverse DNS fallback.
  - **Input:** HTTP request with no `user` field, source IP resolvable to a hostname.
  - **Expected Outcome:** Returns the resolved hostname.
- **Test Case:** Verify `resolve_caller` returns IP when DNS fails.
  - **Input:** HTTP request with no `user` field, source IP not resolvable.
  - **Expected Outcome:** Returns the raw IP address.
- **Test Case:** Verify Imperator stores hostname as sender on outgoing messages.
  - **Input:** Send a message to the Imperator, inspect stored messages.
  - **Expected Outcome:** Assistant message has `sender` = MAD hostname, `recipient` = caller identity.
- **Test Case:** Verify Imperator stores caller as sender on incoming messages.
  - **Input:** Send a message with `user: "test-user"`, inspect stored messages.
  - **Expected Outcome:** User message has `sender` = `"test-user"`, `recipient` = MAD hostname.

### 4.15 Participant Filter — conv_list_conversations (REQ §4.6)

- **Test Case:** Verify unfiltered conversation listing.
  - **Input:** Call `conv_list_conversations` with no `participant` parameter.
  - **Expected Outcome:** Returns all conversations ordered by creation date.
- **Test Case:** Verify participant filter returns matching conversations.
  - **Input:** Store messages with known sender/recipient values. Call `conv_list_conversations` with `participant` matching one of those values.
  - **Expected Outcome:** Returns only conversations containing messages where the participant is sender or recipient.
- **Test Case:** Verify participant filter returns empty for unknown participant.
  - **Input:** Call with `participant` set to a value not present in any messages.
  - **Expected Outcome:** Returns empty list.

### 4.16 Log MCP Endpoints (REQ §6.1.2)

- **Test Case:** Verify `query_logs` with no filters.
  - **Input:** Call `query_logs` with only `limit`.
  - **Expected Outcome:** Returns recent log entries up to limit.
- **Test Case:** Verify `query_logs` with container filter.
  - **Input:** Call with `container_name: "langgraph"`.
  - **Expected Outcome:** All returned entries have matching container name.
- **Test Case:** Verify `query_logs` with level filter.
  - **Input:** Call with `level: "ERROR"`.
  - **Expected Outcome:** All returned entries have level ERROR.
- **Test Case:** Verify `query_logs` with time range filter.
  - **Input:** Call with `since` and `until` ISO timestamps.
  - **Expected Outcome:** All returned entries have timestamps within the range.
- **Test Case:** Verify `search_logs` requires log_embeddings config.
  - **Input:** Call `search_logs` when `log_embeddings` is not configured.
  - **Expected Outcome:** Returns error explaining log vectorization is not enabled.
- **Test Case:** Verify `search_logs` returns semantically relevant results.
  - **Input:** Embed some log entries, then call `search_logs` with a natural language query.
  - **Expected Outcome:** Returns entries with similarity scores, ordered by relevance.

### 4.17 Log Vectorization (REQ §6.1.1)

- **Test Case:** Verify log embedding worker is dormant when disabled.
  - **Input:** Start system without `log_embeddings` in config.
  - **Expected Outcome:** Worker sleeps and does not attempt to embed logs.
- **Test Case:** Verify log embedding worker embeds when enabled.
  - **Input:** Configure `log_embeddings` with a model. Insert log entries with NULL embeddings.
  - **Expected Outcome:** Worker polls and populates embedding column in batches.
- **Test Case:** Verify log embeddings use separate model from conversation embeddings.
  - **Input:** Configure `log_embeddings` with a different model/dims than `embeddings`.
  - **Expected Outcome:** Log embeddings use the log-specific model, conversation embeddings unchanged.

### 4.18 Domain Information (REQ §5.7)

- **Test Case:** Verify `store_domain_info` persists content with embedding.
  - **Input:** Call `store_domain_info` with content text.
  - **Expected Outcome:** Row inserted in `domain_information` table with non-null embedding.
- **Test Case:** Verify `search_domain_info` returns semantically relevant results.
  - **Input:** Store several domain info entries. Search with a related query.
  - **Expected Outcome:** Returns entries ordered by similarity score.
- **Test Case:** Verify domain info tools gated by config.
  - **Input:** Call `get_operational_tools` with `domain_information.enabled: false`.
  - **Expected Outcome:** `store_domain_info` and `search_domain_info` not in returned tools.

### 4.19 Domain Knowledge — Mem0/Neo4j KG (REQ-core-domain-knowledge)

- **Test Case:** Verify `extract_domain_knowledge` processes domain info entries.
  - **Input:** Store domain info entries, then call `extract_domain_knowledge`.
  - **Expected Outcome:** Reports extracted count. Neo4j contains new nodes.
- **Test Case:** Verify `search_domain_knowledge` returns graph results.
  - **Input:** After extraction, search for a known entity.
  - **Expected Outcome:** Returns relevant knowledge entries from the domain graph.
- **Test Case:** Verify domain Mem0 uses separate collection from conversation Mem0.
  - **Input:** Inspect `collection_name` in domain Mem0 config.
  - **Expected Outcome:** Uses `"domain_memories"`, not `"mem0_memories"`.
- **Test Case:** Verify domain knowledge tools gated by config.
  - **Input:** Call `get_operational_tools` with `domain_knowledge.enabled: false`.
  - **Expected Outcome:** Domain knowledge tools not in returned tools.

### 4.21 Embedding Migration Tool (REQ-core-imperator-tools)

- **Test Case:** Verify dry run mode.
  - **Input:** Call `migrate_embeddings` with `confirm: false`.
  - **Expected Outcome:** Returns preview of what would happen, no changes made.
- **Test Case:** Verify confirmed migration executes.
  - **Input:** Call `migrate_embeddings` with `confirm: true`, new model and dims.
  - **Expected Outcome:** Config updated, embeddings wiped to NULL, `memory_extracted` flags reset, Mem0 client reset. Workers begin re-embedding.
- **Test Case:** Verify migration updates config.yml.
  - **Input:** After migration, read config.yml.
  - **Expected Outcome:** `embeddings.model` and `embeddings.embedding_dims` match the new values.

### 4.22 Gradio Chat UI (REQ-optional-gradio-chat-ui)

- **Test Case:** Verify UI container starts and serves pages.
  - **Input:** Build and start `context-broker-ui` container.
  - **Expected Outcome:** HTTP GET to port 7860 returns 200 OK with HTML content.
- **Test Case:** Verify MAD selector lists configured MADs.
  - **Input:** Configure two MADs in `config.yml`. Load the UI.
  - **Expected Outcome:** Dropdown contains both MAD names.
- **Test Case:** Verify health indicators update.
  - **Input:** Load UI with one reachable and one unreachable MAD.
  - **Expected Outcome:** Reachable MAD shows green indicator, unreachable shows red.
- **Test Case:** Verify conversation sidebar loads on MAD selection.
  - **Input:** Select a MAD with existing conversations.
  - **Expected Outcome:** Conversation dropdown populates with conversation titles.
- **Test Case:** Verify conversation creation.
  - **Input:** Enter a title, click Create.
  - **Expected Outcome:** New conversation appears in the dropdown.
- **Test Case:** Verify chat streaming.
  - **Input:** Select a conversation, send a message.
  - **Expected Outcome:** Response streams into the chat panel incrementally.
- **Test Case:** Verify conversation history loads on selection.
  - **Input:** Select a conversation with existing messages.
  - **Expected Outcome:** Chat panel populates with prior messages.
- **Test Case:** Verify artifacts panel extracts code blocks.
  - **Input:** Receive an assistant response containing a code block.
  - **Expected Outcome:** Artifacts panel renders the code block separately.
- **Test Case:** Verify log viewer shows entries.
  - **Input:** Click Refresh on the log panel.
  - **Expected Outcome:** Log entries from the selected MAD appear.
- **Test Case:** Verify participant filter on conversation list.
  - **Input:** Switch MADs. Check that conversations listed are filtered by the MAD's hostname.
  - **Expected Outcome:** Only conversations where the selected MAD is a participant appear.

## 5. Integration Test Approach

- Tests will be written using `pytest` and the `httpx` library for making asynchronous HTTP requests.
- The test suite will use `docker-compose` to manage the lifecycle of the Context Broker and its backing services. A dedicated `docker-compose.test.yml` override file may be used to expose ports or configure volumes for testability.
- Pytest fixtures will be used to manage the container lifecycle (setup/teardown) and to provide pre-configured API clients for interacting with the system's endpoints.
- Assertions will be made against API responses. For verifying state changes that are not directly exposed by the API (e.g., background job effects), tests will connect directly to the backing services (e.g., using `psycopg` for Postgres) to inspect the data.

## 6. What Is Not Tested

- **LLM Output Quality:** This plan verifies that the system correctly formats data and communicates with configured LLM providers. It does not qualitatively evaluate the correctness, style, or factual accuracy of the text generated by the external LLMs. Evaluating model output is out of scope.
- **Third-Party Container Security:** The plan verifies that the custom `context-broker-langgraph` container is built and run according to security best practices (e.g., non-root user). It does not include vulnerability scanning of the official, unmodified third-party images (Nginx, Postgres, Redis, Neo4j), which is assumed to be part of a separate supply-chain security process.
- **Large-Scale Performance Testing:** This plan includes tests to ensure the system is functional and resilient. It does not include large-scale load or stress testing to determine maximum requests-per-second, latency at scale, or other performance benchmarks. This requires a dedicated performance test plan.
- **Visual Design:** The Gradio UI is functionally tested via automated browser tests (§4.22), but subjective visual design quality (spacing, color choices, visual hierarchy) is not evaluated programmatically.

## 7. Test Environment

The test environment is fully defined and provisioned by Docker and Docker Compose. No host-level dependencies are required beyond a working Docker installation.

- **Infrastructure:** The environment is defined by the project's `docker-compose.yml` and the associated Dockerfiles.
- **Components:** The environment consists of containers: `context-broker` (gateway), `context-broker-langgraph`, `context-broker-postgres`, `context-broker-neo4j`, `context-broker-log-shipper`, and optionally `context-broker-ollama`, `context-broker-infinity`, `context-broker-ui` (Gradio). Uses exact image versions pinned in the project configuration. UI tests require `context-broker-ui` running with a reachable MAD backend.
- **Execution:** Tests are executed by a Python test runner (`pytest`) from a host or CI environment that has access to the Docker daemon.

## 8. Traceability Matrix

| ID | Scenario | Section | Type | Test File | Status | Last Run | Result | Notes |
|----|----------|---------|------|-----------|--------|----------|--------|-------|
| T-1.1 | Version pinning (requirements.txt ==) | 4.1 | Static | test_static_checks.py::TestVersionPinning | Written | 2026-03-23 | Pass | |
| T-1.2 | Version pinning (Dockerfile FROM tags) | 4.1 | Static | test_static_checks.py::TestVersionPinning | Written | 2026-03-23 | Pass | |
| T-1.3 | Credential files present | 4.1 | Static | test_static_checks.py::TestCredentialFiles | Written | 2026-03-23 | Pass | |
| T-1.4 | OTS backing services (no build context) | 4.1 | Static | test_static_checks.py::TestOTSBackingServices | Written | 2026-03-23 | Pass | |
| T-1.5 | USER directive placement | 4.1 | Static | test_static_checks.py::TestDockerfileUserDirective | Written | 2026-03-23 | Pass | |
| T-1.6 | Local package source build | 4.1 | Integration | | Not started | | Not run | Requires Docker build with local mode |
| T-1.7 | Pypi/devpi package source | 4.1 | Integration | | Not started | | Not run | Requires Docker build |
| T-2.1 | Non-root user execution | 4.2 | Integration | | Not started | | Not run | Requires running container inspection |
| T-2.2 | Two-network topology | 4.2 | Static | test_static_checks.py::TestTwoNetworkTopology | Written | 2026-03-23 | Pass | |
| T-2.3 | File ownership (COPY --chown) | 4.2 | Integration | | Not started | | Not run | Requires running container |
| T-2.4 | HEALTHCHECK directive | 4.2 | Static | test_static_checks.py::TestDockerfileHealthcheck | Written | 2026-03-23 | Pass | |
| T-2.5 | Thin gateway (nginx) | 4.2 | Static | test_static_checks.py::TestThinGateway | Written | 2026-03-23 | Pass | |
| T-2.6 | Service name DNS | 4.2 | Static | test_static_checks.py::TestServiceNameDNS | Written | 2026-03-23 | Pass | |
| T-3.1 | Fail-fast on invalid config | 4.3 | Integration | | Not started | | Not run | Requires container start with bad config |
| T-3.2 | Hot-reload inference provider | 4.3 | Integration | | Not started | | Not run | Requires running system + config file change |
| T-3.3 | Token budget resolution | 4.3 | Unit | test_token_budget.py | Written | 2026-03-23 | Pass | Mocked — needs integration test too |
| T-3.4 | Imperator admin_tools toggle | 4.3 | Integration | | Not started | | Not run | Requires running Imperator |
| T-4.1 | Two-volume pattern | 4.4 | Integration | | Not started | | Not run | Requires running container mount inspection |
| T-4.2 | Idempotency of message storage | 4.4 | E2E | | Not started | | Not run | Store same message twice via MCP |
| T-4.3 | Schema migration on startup | 4.4 | Integration | | Not started | | Not run | Start against old schema |
| T-4.4 | Credential loading from .env | 4.4 | Integration | | Not started | | Not run | Requires container env inspection |
| T-4.5 | No anonymous volumes | 4.4 | Integration | | Not started | | Not run | Docker volume inspection |
| T-5.1 | End-to-end pipeline execution | 4.5 | E2E | | Not started | | Not run | Store message, verify embedding + assembly + extraction |
| T-5.2 | Pipeline intermediate outputs | 4.5 | E2E | | Not started | | Not run | Verify DB state after each pipeline stage |
| T-5.3 | Postgres advisory lock prevents concurrent assembly | 4.5 | Integration | | Not started | | Not run | Race condition test |
| T-6.1 | All MCP tools (valid/invalid/boundary) | 4.6 | E2E | | Not started | | Not run | Full parameter variation per tool |
| T-6.2 | OpenAI-compatible endpoint (stream/non-stream) | 4.6 | E2E | | Not started | | Not run | Requires Ollama running |
| T-6.3 | Health endpoint (healthy/degraded) | 4.6 | E2E | | Not started | | Not run | Stop Neo4j, verify degraded |
| T-6.4 | Metrics endpoint | 4.6 | E2E | | Not started | | Not run | Verify Prometheus format |
| T-6.5 | External API response validation | 4.6 | Unit | | Not started | | Not run | Mock malformed LLM response |
| T-7.1 | Imperator state persistence across restarts | 4.7 | E2E | | Not started | | Not run | Stop/start, verify conversation resumes |
| T-7.2 | Context retrieval with different build types | 4.7 | E2E | | Not started | | Not run | Requires Ollama for LLM calls |
| T-8.1 | Graceful degradation (Neo4j down) | 4.8 | E2E | | Not started | | Not run | Stop Neo4j, verify operation continues |
| T-8.2 | Independent container startup | 4.8 | Integration | | Not started | | Not run | Random startup order |
| T-9.1 | Structured JSON logging | 4.9 | Integration | | Not started | | Not run | Capture docker logs, parse JSON |
| T-9.2 | Pipeline verbose mode | 4.9 | Integration | | Not started | | Not run | Toggle verbose, compare logs |
| T-10.1 | No blocking I/O in async functions | 4.10 | Unit | | Not started | | Not run | Static analysis or runtime detection |
| T-11.1 | LangGraph mandate (routes are thin) | 4.11 | Static | test_static_checks.py::TestStateGraphMandate | Written | 2026-03-23 | Pass | |
| T-11.2 | StateGraph node immutability | 4.11 | Unit | test_state_immutability.py | Written | 2026-03-23 | Pass | Tests scoring/scaling functions only |
| T-5.4 | Embedding generation via Infinity | 4.5 | E2E | | Not started | | Not run | Verify vector populated + correct dimension |
| T-5.5 | Reranking via Infinity /v1/rerank | 4.5 | E2E | | Not started | | Not run | Compare reranked vs raw RRF order |
| T-12.1 | LLM provider compatibility (6 providers) | 4.12 | E2E | | Not started | | Not run | OpenAI, Anthropic, Google, xAI, Together, Ollama |
| T-12.2 | Embedding provider compatibility (5 providers) | 4.12 | E2E | | Not started | | Not run | OpenAI, Google, Together, Ollama, Infinity |
| T-12.3 | Reranking provider compatibility (4 providers) | 4.12 | E2E | | Not started | | Not run | Infinity, Together, Cohere, Jina |
| T-13.1 | Tool discovery from modules | 4.13 | Unit | test_tool_organization.py::TestToolDiscovery | Written | 2026-03-26 | Pass | admin_tools on/off |
| T-13.2 | Operational tool gating | 4.13 | Unit | test_tool_organization.py::TestOperationalToolGating | Written | 2026-03-26 | Pass | 7 gating tests |
| T-13.3 | Tool modules export valid tools | 4.13 | Unit | test_tool_organization.py::TestToolModuleExports | Written | 2026-03-26 | Pass | All 9 modules |
| T-14.1 | resolve_caller with user field | 4.14 | Unit | | Not started | | Not run | |
| T-14.2 | resolve_caller reverse DNS fallback | 4.14 | Unit | | Not started | | Not run | Mock socket.gethostbyaddr |
| T-14.3 | resolve_caller returns IP on DNS failure | 4.14 | Unit | | Not started | | Not run | Mock DNS failure |
| T-14.4 | Imperator stores hostname as sender | 4.14 | Integration | | Not started | | Not run | Inspect stored messages |
| T-14.5 | Imperator stores caller as sender | 4.14 | Integration | | Not started | | Not run | Send with user field, inspect DB |
| T-15.1 | Unfiltered conversation listing | 4.15 | Unit | | Not started | | Not run | |
| T-15.2 | Participant filter matches | 4.15 | Integration | | Not started | | Not run | Store messages, filter by participant |
| T-15.3 | Participant filter empty for unknown | 4.15 | Integration | | Not started | | Not run | |
| T-16.1 | query_logs no filters | 4.16 | Integration | | Not started | | Not run | |
| T-16.2 | query_logs container filter | 4.16 | Integration | | Not started | | Not run | |
| T-16.3 | query_logs level filter | 4.16 | Integration | | Not started | | Not run | |
| T-16.4 | query_logs time range filter | 4.16 | Integration | | Not started | | Not run | |
| T-16.5 | search_logs requires config | 4.16 | Unit | | Not started | | Not run | |
| T-16.6 | search_logs returns relevant results | 4.16 | Integration | | Not started | | Not run | |
| T-17.1 | Log worker dormant when disabled | 4.17 | Unit | | Not started | | Not run | |
| T-17.2 | Log worker embeds when enabled | 4.17 | Integration | | Not started | | Not run | |
| T-17.3 | Log embeddings use separate model | 4.17 | Unit | | Not started | | Not run | |
| T-18.1 | store_domain_info persists with embedding | 4.18 | Integration | | Not started | | Not run | |
| T-18.2 | search_domain_info returns relevant results | 4.18 | Integration | | Not started | | Not run | |
| T-18.3 | Domain info tools gated by config | 4.18 | Unit | | Not started | | Not run | |
| T-19.1 | extract_domain_knowledge processes entries | 4.19 | Integration | | Not started | | Not run | |
| T-19.2 | search_domain_knowledge returns results | 4.19 | Integration | | Not started | | Not run | |
| T-19.3 | Domain Mem0 uses separate collection | 4.19 | Unit | | Not started | | Not run | |
| T-19.4 | Domain knowledge tools gated by config | 4.19 | Unit | | Not started | | Not run | |
| T-21.1 | Migration tool dry run | 4.21 | Unit | test_migration_tool.py::TestMigrationToolDryRun | Written | 2026-03-26 | Pass | |
| T-21.2 | Migration tool confirmed execution | 4.21 | Integration | | Not started | | Not run | |
| T-21.3 | Migration updates config.yml | 4.21 | Integration | | Not started | | Not run | |
| T-22.1 | UI container serves pages | 4.22 | E2E | Malory browser test | Verified | 2026-03-26 | Pass | HTTP 200 on :7860 |
| T-22.2 | MAD selector lists configured MADs | 4.22 | Browser | Malory browser test | Verified | 2026-03-26 | Pass | "Context Broker" with checkmark |
| T-22.3 | Health indicators update | 4.22 | Browser | Malory browser test | Verified | 2026-03-26 | Pass | "healthy database: ok neo4j: ok" |
| T-22.4 | Conversation sidebar loads on MAD select | 4.22 | Browser | Malory browser test | Verified | 2026-03-26 | Partial | Dropdown present, empty on new conv |
| T-22.5 | Conversation creation | 4.22 | Browser | Malory browser test | Verified | 2026-03-26 | Pass | New button works |
| T-22.6 | Chat streaming | 4.22 | Browser | Malory browser test | Verified | 2026-03-26 | Pass | Message sent, Imperator responded |
| T-22.7 | Conversation history loads on select | 4.22 | Browser | | Not started | | Not run | |
| T-22.8 | Artifacts panel extracts code blocks | 4.22 | Browser | | Not started | | Not run | |
| T-22.9 | Log viewer shows entries | 4.22 | Browser | Malory browser test | Verified | 2026-03-26 | Pass | Live log embedding entries |
| T-22.10 | Participant filter on conversation list | 4.22 | Browser | | Not started | | Not run | |
| T-23.1 | web_search returns results | 4.23 | Unit | test_new_tools.py::TestWebSearch | Written | 2026-03-26 | Pass | DuckDuckGo mocked |
| T-23.2 | web_search empty results | 4.23 | Unit | test_new_tools.py::TestWebSearch | Written | 2026-03-26 | Pass | |
| T-23.3 | web_read HTML fallback strips tags | 4.23 | Unit | test_new_tools.py::TestWebRead | Written | 2026-03-26 | Pass | crawl4ai mocked to fail |
| T-23.4 | web_read plain text | 4.23 | Unit | test_new_tools.py::TestWebRead | Written | 2026-03-26 | Pass | |
| T-23.5 | web_search live DuckDuckGo | 4.23 | Integration | Live system test | Verified | 2026-03-26 | Pass | Returned LangGraph docs |
| T-23.6 | web_read live HTTPS | 4.23 | Integration | Live system test | Verified | 2026-03-26 | Pass | example.com via system SSL |
| T-24.1 | file_read sandbox rejection | 4.24 | Unit | test_new_tools.py::TestFilesystemSandbox | Written | 2026-03-26 | Pass | /etc/passwd blocked |
| T-24.2 | file_read sandbox allow | 4.24 | Unit | test_new_tools.py::TestFilesystemSandbox | Written | 2026-03-26 | Pass | /config allowed |
| T-24.3 | file_list sandbox rejection | 4.24 | Unit | test_new_tools.py::TestFilesystemSandbox | Written | 2026-03-26 | Pass | /root blocked |
| T-24.4 | file_write restricted to downloads | 4.24 | Unit | test_new_tools.py::TestFilesystemSandbox | Written | 2026-03-26 | Pass | |
| T-24.5 | file_search sandbox rejection | 4.24 | Unit | test_new_tools.py::TestFilesystemSandbox | Written | 2026-03-26 | Pass | |
| T-24.6 | file_read live /config/te.yml | 4.24 | Integration | Live system test | Verified | 2026-03-26 | Pass | Read model name |
| T-25.1 | run_command rejects disallowed | 4.25 | Unit | test_new_tools.py::TestSystemCommands | Written | 2026-03-26 | Pass | rm -rf blocked |
| T-25.2 | run_command allows hostname | 4.25 | Unit | test_new_tools.py::TestSystemCommands | Written | 2026-03-26 | Pass | |
| T-25.3 | calculate safe math | 4.25 | Unit | test_new_tools.py::TestSystemCommands | Written | 2026-03-26 | Pass | 2+3*4=14 |
| T-25.4 | calculate rejects code injection | 4.25 | Unit | test_new_tools.py::TestSystemCommands | Written | 2026-03-26 | Pass | __import__ blocked |
| T-25.5 | run_command live hostname | 4.25 | Integration | Live system test | Verified | 2026-03-26 | Pass | Returned container name |
| T-26.1 | send_notification CloudEvents format | 4.26 | Unit | test_new_tools.py::TestSendNotification | Written | 2026-03-26 | Pass | Payload has type, data |
| T-26.2 | send_notification default webhook | 4.26 | Unit | test_new_tools.py::TestSendNotification | Written | 2026-03-26 | Pass | Uses alerter URL |
| T-26.3 | send_notification live email | 4.26 | Integration | Live system test | Verified | 2026-03-26 | Pass | 2/2 channels (SMTP+log) |
| T-27.1 | add_alert_instruction invalid JSON | 4.27 | Unit | test_new_tools.py::TestAlertInstructionTools | Written | 2026-03-26 | Pass | |
| T-27.2 | add_alert_instruction channels must be array | 4.27 | Unit | test_new_tools.py::TestAlertInstructionTools | Written | 2026-03-26 | Pass | |
| T-27.3 | add_alert_instruction success | 4.27 | Unit | test_new_tools.py::TestAlertInstructionTools | Written | 2026-03-26 | Pass | Mocked DB |
| T-27.4 | list_alert_instructions empty | 4.27 | Unit | test_new_tools.py::TestAlertInstructionTools | Written | 2026-03-26 | Pass | |
| T-27.5 | delete_alert_instruction nonexistent | 4.27 | Unit | test_new_tools.py::TestAlertInstructionTools | Written | 2026-03-26 | Pass | |
| T-28.1 | change_inference invalid slot | 4.28 | Unit | test_change_inference.py | Written | 2026-03-26 | Pass | |
| T-28.2 | change_inference list models | 4.28 | Unit | test_change_inference.py | Written | 2026-03-26 | Pass | |
| T-28.3 | change_inference empty catalog | 4.28 | Unit | test_change_inference.py | Written | 2026-03-26 | Pass | |
| T-28.4 | change_inference model not in catalog | 4.28 | Unit | test_change_inference.py | Written | 2026-03-26 | Pass | |
| T-28.5 | change_inference endpoint failure blocks switch | 4.28 | Unit | test_change_inference.py | Written | 2026-03-26 | Pass | |
| T-28.6 | change_inference embeddings warns migration | 4.28 | Unit | test_change_inference.py | Written | 2026-03-26 | Pass | |
| T-28.7 | change_inference successful switch | 4.28 | Unit | test_change_inference.py | Written | 2026-03-26 | Pass | |
| T-29.1 | Seed knowledge skips non-empty table | 4.29 | Integration | Live system test | Verified | 2026-03-26 | Pass | Logged "already has 1 entries" |
| T-29.2 | search_domain_info finds seed articles | 4.29 | Integration | Live system test | Verified | 2026-03-26 | Pass | Found embedding model info |
| T-30.1 | Stuck job watchdog embedding timeout | 4.30 | Unit | | Not started | | Not run | Configurable timeout |
| T-30.2 | Stuck job watchdog extraction timeout | 4.30 | Unit | | Not started | | Not run | Configurable timeout |