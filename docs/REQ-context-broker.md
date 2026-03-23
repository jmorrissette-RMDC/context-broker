# Context Broker — System Requirements Specification

**Version:** 1.0 (Draft) **Date:** 2026-03-20 **Status:** Draft

## Purpose and Scope

This document defines the requirements for the Context Broker — a self-contained context engineering and conversational memory service. The Context Broker manages infinite conversation history, assembles purpose-built context windows using configurable strategies, extracts knowledge into a graph, and exposes its capabilities via both MCP tools and an OpenAI-compatible conversational interface.

The Context Broker is a **State 4 MAD** (Multipurpose Agentic Duo) — a refactor of the current State 2 Rogers Context Broker that operates within the Joshua26 agentic ecosystem. The MAD architecture defines an evolution from monolithic containers (State 0) through functional decomposition (State 1), off-the-shelf infrastructure with agent patterns (State 2), separated infrastructure and intelligence packages (State 3), to fully configurable external dependencies (State 4). State 4 is what makes this system publishable as a standalone tool: all ecosystem dependencies — inference providers, package sources, storage paths, network topology — become configuration choices rather than hard couplings. The same code runs standalone on any Docker-capable host or inside the ecosystem it was built for.

See Conversationally Cognizant AI concept papers at <https://jmorrissette-rmdc.github.io/projects/concept-papers.html> for more details and clarification.

**Target Audience:**

-   Developers deploying the Context Broker standalone
-   Developers integrating the Context Broker into an existing system
-   Contributors modifying the StateGraph flows or build types

***

## Guiding Philosophy: Code for Clarity

All code, whether written by humans or LLMs, must be clear, readable, and maintainable. Key principles:

-   **Descriptive names:** Clear, unambiguous names for variables, functions, and classes.
-   **Small, focused functions:** Each function does one thing well.
-   **Comment the why, not the what:** Comments explain complex logic or design choices, not the obvious.

***

## Part 1: Architectural Overview

### State 4 MAD Pattern

A State 4 MAD separates infrastructure (AE — Action Engine) from intelligence (TE — Thought Engine), with all external dependencies configurable:

-   **AE** — MCP tool handlers, message routing, database operations, queue processing. Stable, changes infrequently.
-   **TE** — The Imperator (conversational agent) and its cognitive apparatus. Evolves independently of infrastructure.
-   **Configuration** — A single `config.yml` controls inference providers, model selection, package sources, token budgets, and build type definitions. Code reads config fresh on each operation — no restart required after config changes.

### Container Architecture

The Context Broker runs as a group of containers managed by Docker Compose:

| Container                  | Role                                                                        | Image                       |
|----------------------------|-----------------------------------------------------------------------------|-----------------------------|
| `context-broker`           | Nginx gateway — MCP endpoint, OpenAI-compatible chat endpoint, health check | Nginx (OTS)                 |
| `context-broker-langgraph` | All application logic — StateGraph flows, queue worker, Imperator           | Custom (Python)             |
| `context-broker-postgres`  | Conversation storage, vector embeddings (pgvector), build types             | PostgreSQL + pgvector (OTS) |
| `context-broker-neo4j`     | Knowledge graph storage (Mem0)                                              | Neo4j (OTS)                 |
| `context-broker-redis`     | Job queues, assembly locks, ephemeral state                                 | Redis (OTS)                 |
| `context-broker-infinity`  | Embeddings and reranking via OpenAI-compatible APIs                         | Infinity (OTS)              |

Only the LangGraph container is custom. All backing services use official images unmodified.

### Dual Protocol Interface

The Context Broker exposes two interfaces through the gateway:

-   **MCP (HTTP/SSE)** — Programmatic tool access. External agents and applications call `conv_*` and `mem_*` tools via standard MCP protocol.
-   **OpenAI-compatible HTTP (**`/v1/chat/completions`**)** — Conversational access to the Imperator. Any OpenAI-compatible client or chat UI can connect and talk to the Context Broker directly.

Both interfaces are backed by the same internal StateGraph flows.

### Imperator

The Context Broker includes a conversational agent (Imperator) with declared Identity and Purpose, backed by its own persistent conversation. The Imperator:

-   Maintains a single ongoing conversation that persists across restarts
-   Can search conversations and memories, introspect context assembly, report system status
-   Uses the same internal functions that back the MCP tools
-   Reads its LLM provider from `config.yml` like every other flow
-   When `admin_tools` is enabled, can read and modify configuration and query the database directly

***

## Part 2: Requirements by Category

### 1. Build System

**Purpose:** Ensure the project builds reproducibly with quality controls.

**1.1 Version Pinning**

-   All dependencies locked to exact versions.
-   Python: `==` in requirements.txt.
-   Docker base images: pinned version tag (e.g., `FROM python:3.11.7-slim`, not `FROM python:3.11`).

**1.2 Code Formatting**

-   All Python code must be formatted with `black`.
-   Verification: `black --check .` passes without errors.

**1.3 Code Linting**

-   All Python code must pass `ruff check .` without errors.

**1.4 Unit Testing**

-   All programmatic logic must have corresponding `pytest` tests covering the primary success path and common error conditions.

**1.5 StateGraph Package Source**

-   The source for StateGraph packages is configurable:

```yaml
packages:
  source: local        # "local", "pypi", or "devpi"
  local_path: /app/packages
  devpi_url: null      # e.g., http://devpi-host:3141/root/internal/+simple/
```

-   **local** (default): Packages bundled as wheels in the repository. Installed at build time via `pip install /app/packages/*.whl`.
-   **pypi**: Installed from public PyPI.
-   **devpi**: Installed from a private devpi index.

### 2. Runtime Security and Permissions

**Purpose:** Containers run with least privilege and correct ownership.

**2.1 Root Usage Pattern**

-   Root privileges used only for system package installation and user creation.
-   `USER` directive immediately follows user creation in the Dockerfile.
-   No application code, file operations, or runtime tasks run as root.

**2.2 Service Account**

-   The LangGraph container runs as a dedicated non-root user (e.g., `context-broker`).
-   UID and GID are defined in the Dockerfile and consistent across the container group.

**2.3 File Ownership**

-   Use `COPY --chown` instead of `chown -R` for setting file ownership in Dockerfiles.
-   Sets ownership at copy time, avoiding separate chown layers.

### 3. Storage and Data

**Purpose:** Data persistence, correct volume mounting, and secure credential management.

**3.1 Two-Volume Pattern**

-   Containers use two mount points:
    -   `/config` — User-edited configuration and credentials. Mapped to host via bind mount (default: `./config:/config`).
    -   `/data` — System-generated state (databases, queue data). Mapped to host via bind mount (default: `./data:/data`).
-   The paths inside the container are fixed. The host paths are controlled by the bind mount in `docker-compose.yml`.
-   Users change host-side paths in the compose file (or an override file) without modifying the image.

**3.2 Data Directory Organization**

```
/data/
├── postgres/              # PostgreSQL data files
├── neo4j/                 # Neo4j graph data
├── redis/                 # Redis persistence
└── imperator_state.json   # Imperator persistent state
```

-   `imperator_state.json` contains `{"conversation_id": "<uuid>", "context_window_id": "<uuid>"}`. On first boot, the Imperator creates its conversation and context window via `conv_create_conversation` and `conv_create_context_window`, and writes the returned IDs to this file. On subsequent boots, it reads the file and resumes. If the file is missing or the referenced conversation no longer exists, a new conversation and context window are created.

**3.3 Config Directory Organization**

```
/config/
├── config.yml             # All configuration (providers, models, build types)
└── credentials/           # API keys (gitignored)
    └── .env               # LLM_API_KEY, EMBEDDINGS_API_KEY, etc.
```

**3.4 Credential Management**

-   All credentials stored in `/config/credentials/.env` as key-value pairs (e.g., `LLM_API_KEY=sk-...`).
-   The `.env` file is loaded into the container environment via `env_file` in `docker-compose.yml`.
-   Application code reads credentials from environment variables at runtime. The `api_key_env` field in `config.yml` names the environment variable to read (e.g., `api_key_env: LLM_API_KEY`).
-   No credentials in Dockerfiles, application code, or committed files. The `.env` file is gitignored. The repository ships a `.env.example` listing required variable names without values.
-   Verification: grep codebase for hardcoded secrets — must find none. The only place real secrets exist is the user's local `.env` file.

**3.5 Database Storage**

-   All database data files stored under `/data/`.
-   Each technology gets its own subdirectory (`/data/postgres/`, `/data/neo4j/`, `/data/redis/`).
-   Backing service containers use bind mounts at their declared VOLUME paths to prevent Docker from creating anonymous volumes.

**3.5.1 Message Schema**

-   The `conversation_messages` table stores messages in OpenAI-compatible format. Key fields:
    -   `role` (string, required): `system`, `user`, `assistant`, or `tool`.
    -   `content` (text, **nullable**): Message content. Null when an assistant message contains only tool calls.
    -   `sender` / `recipient` (string): Identifies who sent and who receives the message. `recipient` is always populated — defaults from `role` if not explicitly provided (e.g., role `user` defaults recipient to `assistant`).
    -   `tool_calls` (JSONB, nullable): For assistant messages that invoke tools. Stores the OpenAI-format tool call array.
    -   `tool_call_id` (string, nullable): For `tool` role messages, references the tool call this result belongs to.
    -   `token_count` (integer): Computed internally by the system on ingestion, not caller-provided.
    -   `priority` (float): Computed internally based on recency, role, and build type rules. Not caller-provided.
-   Fields **not** present: `content_type`, `idempotency_key`.

**3.5.2 Context Window Fields**

-   Context windows include a `last_accessed_at` timestamp, updated on each retrieval. Used for dormant window detection and cleanup policies.

**3.5.3 Context Retrieval Format**

-   `conv_retrieve_context` returns a structured **messages array** — a list of OpenAI-format message dicts (each with `role`, `content`, and optionally `tool_calls` / `tool_call_id`). It does **not** return a concatenated text string.

**3.5.4 Memory Confidence Scoring**

-   Extracted memories carry a confidence score that decays over time using a configurable half-life. Memories that are re-confirmed by new conversation evidence have their confidence refreshed. Low-confidence memories are deprioritized during retrieval.

**3.6 Backup and Recovery**

-   All persistent state lives under `./data/` on the host. This is the single directory to back up.
-   For consistent snapshots: `pg_dump` for PostgreSQL, `neo4j-admin database dump` for Neo4j. Redis persistence (RDB/AOF) is captured by copying the Redis data directory.
-   Backup frequency and retention are the deployer's responsibility. The Context Broker does not perform automated backups.

**3.7 Schema Migration**

-   Database schema changes between versions must be versioned and applied automatically on application startup.
-   The application checks the current schema version before proceeding and applies any pending migrations.
-   Migrations must be forward-only and non-destructive — they must not drop data. If a migration cannot be applied safely, the application must fail with a clear error rather than proceed with an incompatible schema.

### 4. Communication and Integration

**Purpose:** Standard protocols for tool access and conversational interaction.

**4.1 MCP Transport**

-   MCP uses HTTP/SSE transport.
-   The gateway exposes:
    -   `GET /mcp` — Establishes SSE session.
    -   `POST /mcp?sessionId=xxx` — Routes message to existing session.
    -   `POST /mcp` (no sessionId) — Sessionless mode for simple tool calls.

**4.2 OpenAI-Compatible Chat**

-   The gateway exposes `/v1/chat/completions` following the OpenAI API specification.
-   Accepts `model`, `messages`, `stream`, and standard generation parameters.
-   Streaming responses use SSE format (`data: {...}\n\n`, `data: [DONE]\n\n`).
-   Any OpenAI-compatible client or chat UI can connect without modification.

**4.3 Authentication**

-   The Context Broker ships without authentication. It is designed for single-user or trusted-network deployment.
-   For deployments on shared or public networks, standard nginx authentication (basic auth, mutual TLS, or an auth proxy) can be configured at the gateway layer without modifying the application.

**4.4 Health Endpoint**

-   The gateway exposes `GET /health` returning `200 OK` when healthy, `503` when unhealthy.
-   Response includes per-dependency status: `{"status": "healthy", "database": "ok", "cache": "ok", "neo4j": "ok"}`.
-   Health check verifies connectivity to all backing services.

**4.5 Tool Naming Convention**

-   MCP tools use domain prefixes: `[domain]_[action]`.
-   No collisions with other services when deployed alongside other MCP servers.

**4.6 MCP Tool Inventory**

The Context Broker exposes the following tools via MCP. Full input/output schemas are documented in the README and discoverable via the MCP protocol.

| Tool                          | Description                                                                        |
|-------------------------------|------------------------------------------------------------------------------------|
| `conv_create_conversation`    | Create a new conversation                                                          |
| `conv_store_message`          | Store a message in a conversation (accepts `context_window_id` or `conversation_id`, at least one required; triggers async embedding, assembly, extraction) |
| `conv_retrieve_context`       | Retrieve the assembled context window for a participant                            |
| `conv_create_context_window`  | Create a context window instance with a build type and token budget                |
| `conv_search`                 | Semantic and structured search across conversations                                |
| `conv_search_messages`        | Hybrid search (vector + BM25 + reranking) across messages                          |
| `conv_get_history`            | Retrieve full chronological message sequence for a conversation                    |
| `conv_search_context_windows` | Search and list context windows                                                    |
| `mem_search`                  | Semantic and graph search across extracted knowledge                               |
| `mem_get_context`             | Retrieve relevant memories formatted for prompt injection                          |
| `mem_add`                     | Explicitly add a memory to the knowledge graph                                     |
| `mem_list`                    | List stored memories, optionally filtered                                          |
| `mem_delete`                  | Delete a specific memory by ID                                                     |
| `imperator_chat`              | Conversational interface to the Imperator                                          |
| `metrics_get`                 | Retrieve Prometheus metrics                                                        |

**4.5 LangGraph Mandate**

-   All programmatic and cognitive logic must be implemented as LangGraph StateGraphs.
-   The HTTP server initializes and invokes compiled StateGraphs. No application logic in route handlers.
-   Standard LangChain/LangGraph components used wherever available:
    -   LangChain retrievers and vector stores for semantic search.
    -   LangChain embedding models via config.
    -   LangChain chat models for summarization and extraction via config.
    -   Where a standard LangChain component does not fit the retrieval mechanism (e.g., knowledge graph traversal requires edge-following, not vector similarity), the architecture may use native APIs for that technology. The constraint is to prefer standard components and justify deviations.
-   The Context Broker does **not** use LangGraph checkpointing (`MemorySaver` or equivalent). The `conversation_messages` table with full OpenAI-format fields (role, content, tool_calls, tool_call_id) **is** the persistence layer. `context_window_id` serves as the conceptual equivalent of a LangGraph `thread_id`.

**4.6 LangGraph State Immutability**

-   StateGraph node functions must not modify input state in-place.
-   Each node returns a new dictionary containing only updated state variables.

**4.7 Thin Gateway**

-   The gateway (nginx) is a pure routing layer. No application logic.
-   Routes MCP traffic to the LangGraph container.
-   Routes `/v1/chat/completions` to the LangGraph container.
-   Routes `/health` to the LangGraph container. The LangGraph container performs the actual dependency checks (database, cache, neo4j connectivity) and returns the aggregated status. Nginx proxies the response — it does not perform health checks itself.
-   Serves as the sole network boundary between external traffic and internal containers.

**4.8 Prometheus Metrics**

-   The LangGraph container exposes `GET /metrics` in Prometheus exposition format.
-   Metrics produced inside a StateGraph (not in imperative route handlers).
-   Standard metrics: request counts, durations, error counts, queue depths.
-   The gateway exposes a `metrics_get` MCP tool for programmatic access to metrics.

### 5. Configuration

**Purpose:** All external dependencies and tuning parameters are configurable via a single file.

**5.1 Configuration File**

-   All configuration lives in `/config/config.yml`.
-   Inference providers, models, build type definitions, and token budgets are read from config on each operation. Changes to these take effect immediately without restart.
-   Infrastructure configuration (database connection strings, ports, network settings) is read at startup. Changes to these require a container restart.

**5.2 Inference Provider Configuration**

-   Three independent provider slots: LLM, embeddings, and reranker.
-   Each slot accepts an OpenAI-compatible `base_url`, `model`, and API key reference.
-   The provider interface works with any service that speaks the OpenAI wire protocol (OpenAI, Google, xAI, Groq, Ollama, vLLM, etc.).

```yaml
llm:
  base_url: https://api.openai.com/v1
  model: gpt-4o-mini
  api_key_env: LLM_API_KEY

embeddings:
  base_url: https://api.openai.com/v1
  model: text-embedding-3-small
  api_key_env: EMBEDDINGS_API_KEY

reranker:
  provider: api                # "api" or "none"
  base_url: http://context-broker-infinity:7997
  model: BAAI/bge-reranker-v2-m3
  top_n: 10
```

-   The reranker defaults to the local Infinity container, which serves the `/v1/rerank` endpoint on the internal network. No API key required.
-   `provider: api` hits a `/v1/rerank` endpoint — works with Infinity (local), Together, Cohere, Jina, Voyage, or any compatible provider.
-   Setting `provider: none` disables reranking (raw RRF scores used).

**5.3 Build Type Configuration**

-   A build type is a registered pair of StateGraphs — one for assembly and one for retrieval — that share a standard contract (input state schema, output state schema, config interface). The assembly graph builds the episodic snapshot; the retrieval graph produces the final context window.
-   Deployers add new build types by writing two graphs that satisfy the contract and registering them in `config.yml`. No core code changes required.
-   The Context Broker ships with three build types:

`passthrough` — No summarization. Returns recent verbatim messages up to the token budget. Zero inference cost. Useful for short conversations or testing.

`standard-tiered` — Episodic memory only. Uses the three-tier progressive compression model described in c1. No vector search, no knowledge graph queries. Lower inference cost, suitable as the default.

`knowledge-enriched` — Full retrieval pipeline. Three episodic tiers plus semantically retrieved messages (vector similarity search) plus knowledge graph facts (graph traversal). Demonstrates the complete vision from c1 — episodic and semantic memory layers combined in a purpose-built context window.

```yaml
build_types:
  passthrough:
    # No summarization — recent verbatim messages only
    max_context_tokens: auto
    fallback_tokens: 4096

  standard-tiered:
    # Episodic only — three-tier progressive compression
    tier1_pct: 0.08             # archival summary (oldest, most compressed)
    tier2_pct: 0.20             # chunk summaries (middle layer)
    tier3_pct: 0.72             # recent verbatim messages (newest, full fidelity)
    max_context_tokens: auto
    fallback_tokens: 8192

  knowledge-enriched:
    # Episodic + semantic — full retrieval pipeline
    tier1_pct: 0.05             # archival summary
    tier2_pct: 0.15             # chunk summaries
    tier3_pct: 0.50             # recent verbatim messages
    knowledge_graph_pct: 0.15   # extracted facts and entity relationships from graph traversal (Mem0/Neo4j)
    semantic_retrieval_pct: 0.15 # semantically relevant messages retrieved via vector similarity search (pgvector)
    max_context_tokens: auto
    fallback_tokens: 16000
```

-   `knowledge_graph_pct` and `semantic_retrieval_pct` are distinct retrieval mechanisms. Knowledge graph retrieval returns structured facts and relationships extracted from conversations. Semantic retrieval returns actual messages that are vectorially similar to the current conversation topic but outside the recent verbatim window. A build type can use either, both, or neither.
-   Build types are open-ended. Deployers can define additional build types in `config.yml` to suit their use case (see c1 for examples: sliding window, knowledge-dominant, document injection).

**5.4 Token Budget Resolution**

-   `max_context_tokens` is a build type property.
-   When set to `auto`: on context window creation, query the configured LLM provider's model list endpoint for the model's context length. If the provider does not report it, use `fallback_tokens`.
-   When set to an explicit number: use that value.
-   An explicit `max_tokens` passed by the caller when creating a context window overrides the build type default.
-   Token budget is resolved once at window creation and stored. Model changes do not retroactively affect existing windows.

**5.5 Imperator Configuration**

```yaml
imperator:
  build_type: standard-tiered    # or "knowledge-enriched"
  max_context_tokens: auto
  admin_tools: false             # true = allow config and database modification
```

-   The Imperator is the built-in reference consumer of the Context Broker. It uses the same conversation storage, context assembly, embedding, and knowledge extraction capabilities that external callers access via MCP. It serves as both a conversational interface and a live demonstration of the system's capabilities.
-   `build_type` controls which retrieval layers the Imperator's context window uses. Defaults to `standard-tiered` for lower inference cost. Switching to `knowledge-enriched` activates the full retrieval pipeline including vector similarity search and knowledge graph traversal.
-   `admin_tools: false` (default): Imperator can read system state, search conversations and memories, introspect context assembly.
-   `admin_tools: true`: Imperator can additionally read/write `config.yml` and run read-only database queries.

**5.6 Package Source Configuration**

-   See §1.5. The StateGraph package source is configured in `config.yml`.

### 6. Logging and Observability

**Purpose:** Structured, observable logging and health monitoring.

**6.1 Logging to stdout/stderr**

-   All logs go to stdout (normal) or stderr (errors). No log files inside containers.
-   Docker captures logs via `docker logs`.

**6.2 Structured Logging**

-   JSON format, one object per line.
-   Fields: `timestamp` (ISO 8601), `level` (DEBUG/INFO/WARN/ERROR), `message`, context fields (request_id, tool_name, etc.).

**6.3 Log Levels**

-   DEBUG: Diagnostic detail (off by default).
-   INFO: Normal operations (service started, request completed).
-   WARN: Recoverable issues (retry triggered, degraded mode).
-   ERROR: Failures requiring attention.
-   Default level: INFO. Configurable in `config.yml`.

**6.4 Log Content Standards**

-   Do log: lifecycle events, errors with context, performance metrics.
-   Do not log: secrets/credentials, full request/response bodies, health check successes (only state changes).

**6.5 Dockerfile HEALTHCHECK**

-   Every container's Dockerfile includes a `HEALTHCHECK` directive.
-   Uses `curl` or `wget` against the container's health endpoint.
-   Pattern: `HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 CMD curl -f http://localhost:PORT/health || exit 1`.

**6.6 Health Check Architecture**

-   Two layers:
    -   **Docker HEALTHCHECK** (per container): lightweight process check. Does not check dependencies.
    -   **HTTP** `/health` **endpoint** (gateway): actively tests all backing service connections and returns aggregated status.

**6.7 Specific Exception Handling**

-   No blanket `except Exception:` or `except:` blocks. Catch specific, anticipated exceptions. Unhandled exceptions propagate.

**6.8 Resource Management**

-   All external resources (file handles, database connections) must be reliably closed using context managers (`with`) or `try...finally`.

**6.9 Error Context**

-   All logged errors and raised exceptions must include sufficient context to debug: relevant identifiers, function names, operation being attempted.

### 7. Resilience and Deployment

**Purpose:** Graceful failure handling and correct container deployment.

**7.1 Graceful Degradation and Eventual Consistency**

-   Failure of an optional component (e.g., Neo4j, reranker) causes degraded operation, not a crash.
-   Core operations (store, retrieve, search) continue with reduced capability.
-   Health endpoint reports degraded status.
-   The system is eventually consistent across datastores, not transactionally consistent. Message storage (Postgres) is the source of truth. Background processing — embedding generation, context assembly, knowledge extraction (Neo4j) — is asynchronous and may lag behind the conversation. If a background job fails, it retries with backoff. The conversation record is never lost due to a downstream processing failure.

**7.2 Independent Container Startup**

-   Containers start and bind ports without waiting for dependencies.
-   Dependency unavailability handled at request time (degradation, retry).
-   Allows parallel startup and faster recovery.

**7.3 Network Topology**

-   Two Docker networks per deployment:
    -   **External** (`default`) — the host-exposed network. Only the gateway connects here and publishes ports for inbound access.
    -   **Internal** (`context-broker-net`) — a standard Docker bridge network for all containers. This is NOT marked `internal: true` — containers on this network have outbound internet access via Docker's host NAT for cloud LLM APIs, embedding endpoints, and package downloads. No ports are published on this network, so containers are not reachable from outside.
-   The gateway is the sole inbound boundary — it publishes ports on the external network and routes traffic to the internal network.
-   All other containers (langgraph, backing services, Ollama) connect only to `context-broker-net`. They can reach the internet (outbound) but are not reachable from the host (no published ports).
-   All inter-container communication uses Docker Compose service names, never IP addresses.

```yaml
# docker-compose.yml (network topology example)
services:
  context-broker:          # nginx gateway
    networks:
      - default            # external — publishes ports for inbound access
      - context-broker-net # internal — routes to langgraph
    ports:
      - "8080:8080"        # only published port in the deployment

  context-broker-langgraph:
    networks:
      - context-broker-net # outbound internet via NAT, no inbound from host

  context-broker-postgres:
    networks:
      - context-broker-net # outbound internet via NAT, no inbound from host

  context-broker-neo4j:
    networks:
      - context-broker-net # outbound internet via NAT, no inbound from host

  context-broker-redis:
    networks:
      - context-broker-net # outbound internet via NAT, no inbound from host

networks:
  context-broker-net:
    driver: bridge         # standard bridge — NOT internal:true
```

**7.3.1 Future Option: Forward Proxy for Outbound Access Control**

The default deployment allows unrestricted outbound internet access from `context-broker-net` via Docker's host NAT. For deployments requiring tighter control, a forward proxy container (e.g., Squid or Tinyproxy) can be added to `context-broker-net` via `docker-compose.override.yml`. The network would be set to `internal: true`, and application containers would route outbound traffic through the proxy via `HTTP_PROXY`/`HTTPS_PROXY` environment variables. The proxy can enforce domain allowlists (e.g., only permit connections to configured LLM API endpoints), log all outbound traffic, and block unauthorized destinations. This is not deployed in the current release.

**7.4 Docker Compose**

-   The project ships a single `docker-compose.yml`.
-   Users customize host paths, ports, and resource limits via `docker-compose.override.yml` without modifying the shipped file.
-   Build context is the project root (`.`).

**7.5 Container-Only Deployment**

-   All components run as containers. No bare-metal installation of any service.

**7.6 Asynchronous Correctness**

-   No blocking I/O in async functions. Use `await asyncio.sleep()`, `asyncpg`, `aioredis`, etc.
-   Synchronous calls like `time.sleep()` or blocking library calls in async context are forbidden.

**7.7 Input Validation**

-   All data from external sources (MCP tool input, API responses) must be validated before use.
-   MCP tools enforce this via `inputSchema`. Other inputs validated via Pydantic or manual checks.

**7.8 Null/None Checking**

-   Variables that could be `None` (e.g., database query results) must be explicitly checked before attribute access.

### 8. Documentation

**Purpose:** The project is understandable, deployable, and modifiable with clear documentation.

**8.1 README**

-   The project includes a README covering:
    -   What the Context Broker is and what problem it solves.
    -   Quick start: clone, configure, `docker compose up`, connect.
    -   Configuration reference (config.yml schema).
    -   MCP tool reference (name, description, inputs, outputs).
    -   OpenAI-compatible chat endpoint usage.
    -   Architecture overview (container roles, StateGraph flows, build types).
    -   How to modify StateGraph flows.

**8.2 Tool Documentation**

-   Each MCP tool documented with: name, description, input schema, output schema, examples.
-   Documented in README and in the MCP tool registration (discoverable via MCP protocol).

**8.3 Config Template**

-   The repository includes a `config/config.example.yml` with all options documented and sensible defaults.
-   The repository includes a `config/credentials/.env.example` listing required API key variable names.

***

## Document Metadata

**Version History:**

-   v1.0 (2026-03-20): Initial draft
-   v1.1 (2026-03-22): Align with implementation — build type registry (graph pairs), passthrough type, message schema (sender/recipient, tool_calls, tool_call_id, nullable content, remove content_type/idempotency_key), structured messages array retrieval, no LangGraph checkpointing, conv_store_message accepts context_window_id, broker_chat→imperator_chat, add mem_add/mem_list/mem_delete, last_accessed_at on context windows, internal token_count/priority, memory confidence half-life decay

**Related Documents:**

-   `c1-the-context-broker.md` — Companion concept paper defining the domain model: context windows, build types, three-tier assembly, episodic and semantic memory layers, proactive assembly, and known failure modes
