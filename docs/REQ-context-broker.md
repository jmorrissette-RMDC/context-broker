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
-   **Configuration** — Separated into AE configuration (infrastructure settings, database connections, queue config, package sources) and TE configuration (Imperator settings, inference provider assignments per cognitive function, build type definitions, cognitive parameters). Both are hot-reloadable for inference and tuning settings; infrastructure settings require restart.

### Container Architecture

The Context Broker runs as a group of containers managed by Docker Compose:

| Container                  | Role                                                                        | Image                       |
|----------------------------|-----------------------------------------------------------------------------|-----------------------------|
| `context-broker`           | Nginx gateway — MCP endpoint, OpenAI-compatible chat endpoint, health check | Nginx (OTS)                 |
| `context-broker-langgraph` | All application logic — StateGraph flows, queue worker, Imperator           | Custom (Python)             |
| `context-broker-postgres`  | Conversation storage, vector embeddings (pgvector), build types             | PostgreSQL + pgvector (OTS) |
| `context-broker-neo4j`     | Knowledge graph storage (Mem0)                                              | Neo4j (OTS)                 |
| `context-broker-redis`     | Job queues, assembly locks, ephemeral state                                 | Redis (OTS)                 |
| `context-broker-infinity`  | (Optional) Embeddings and reranking via OpenAI-compatible APIs. Remove if using cloud providers. | Infinity (OTS)              |
| `context-broker-ollama`    | (Optional) Local LLM inference via OpenAI-compatible API. Remove if using cloud providers.       | Ollama (OTS)                |
| `context-broker-log-shipper` | (Optional) Collects logs from all MAD containers on context-broker-net via Docker API, writes to Postgres. Remove if deployment has its own log collector. | Custom (Python)            |

Only the LangGraph container is custom. All backing services use official images unmodified.

### Dual Protocol Interface

The Context Broker exposes two interfaces through the gateway:

-   **MCP (HTTP/SSE)** — Programmatic tool access. External agents and applications call `conv_*` and `mem_*` tools via standard MCP protocol.
-   **OpenAI-compatible HTTP (**`/v1/chat/completions`**)** — Conversational access to the Imperator. Any OpenAI-compatible client or chat UI can connect and talk to the Context Broker directly.

Both interfaces are backed by the same internal StateGraph flows.

### Imperator

The Context Broker includes a conversational agent (Imperator) with declared Identity and Purpose, backed by its own persistent conversation. The Imperator:

-   Uses standard LangGraph `MemorySaver` checkpointer for graph execution state (interrupt/resume, tool call tracking). Long-term conversation persistence is handled by the Context Broker's own pipeline.
-   Maintains a single ongoing conversation that persists across restarts
-   Can search conversations and memories, introspect context assembly, report system status
-   Consumes the Context Broker's own MCP tools — the same interface any other agent would use
-   Reads its LLM provider from `te.yml` (TE configuration)
-   When `admin_tools` is enabled, can read and modify configuration and query the database directly
-   Without a Context Broker configured (standalone TE deployment), the `MemorySaver` provides basic conversation continuity. The Context Broker is an upgrade, not a dependency.

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

-   All credentials stored in `/config/credentials/.env` as key-value pairs (e.g., `OPENAI_API_KEY=sk-...`).
-   The `.env` file is loaded into the container environment via `env_file` in `docker-compose.yml`.
-   Application code reads credentials from environment variables at runtime. Each inference provider slot in configuration includes an `api_key_env` field that names the environment variable holding its API key (e.g., `api_key_env: OPENAI_API_KEY`). This explicit indirection ensures no magic defaults or LangChain auto-detection — every provider's key source is visible in config. For keyless providers (Ollama, Infinity), `api_key_env` is set to `""` (empty string).
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

The Context Broker exposes tools via MCP in two categories. Full input/output schemas are documented in the README and discoverable via the MCP protocol.

**Core tools** — used by agents in their reasoning loop. Clean names, designed for drop-in use via `langchain-mcp-adapters`:

| Tool                 | Required Inputs                                      | Optional              | Description                                                                        |
|----------------------|------------------------------------------------------|-----------------------|------------------------------------------------------------------------------------|
| `get_context`        | `build_type` (enum), `budget` (snapped to bucket)    | `conversation_id`     | Returns assembled context. Auto-creates conversation + window if needed. Returns `conversation_id`. |
| `store_message`      | `conversation_id`, `role`, `content`                 | `sender`, `tool_calls` | Store a message. Internally triggers async embedding, extraction, assembly.        |
| `search_messages`    | `query`                                              | `conversation_id`     | Hybrid search (vector + BM25 + reranking) across messages.                         |
| `search_knowledge`   | `query`, `user_id`                                   | `limit`               | Search extracted facts and relationships from knowledge graph.                     |

-   `get_context` without `conversation_id` creates a new conversation and window, returning the `conversation_id` for the caller to persist.
-   `get_context` with `conversation_id` reuses the existing conversation. If no matching window exists for that `build_type` + `budget`, one is created automatically.
-   `context_window_id` is an internal implementation detail — not exposed in the core tool interface. Window identity = conversation + build_type + budget_bucket.
-   `build_type` is an enum populated dynamically from registered build types in config. `budget` is snapped to the nearest bucket (always up).
-   `search_knowledge` replaces both `mem_search` and `mem_get_context` — same search, one tool.

**Management tools** — used for administration and setup. Prefixed per naming convention:

| Tool                          | Description                                                     |
|-------------------------------|-----------------------------------------------------------------|
| `conv_create_conversation`    | Create a new conversation explicitly                            |
| `conv_get_history`            | Retrieve full chronological message sequence for a conversation |
| `conv_search_context_windows` | Search and list context windows                                 |
| `mem_add`                     | Explicitly add a memory to the knowledge graph                  |
| `mem_list`                    | List stored memories, optionally filtered                       |
| `mem_delete`                  | Delete a specific memory by ID                                  |
| `imperator_chat`              | Conversational interface to the Imperator                       |
| `metrics_get`                 | Retrieve Prometheus metrics                                     |

**4.5 LangGraph Mandate**

-   All programmatic and cognitive logic must be implemented as LangGraph StateGraphs.
-   The HTTP server initializes and invokes compiled StateGraphs. No application logic in route handlers.
-   Standard LangChain/LangGraph components used wherever available:
    -   LangChain retrievers and vector stores for semantic search.
    -   LangChain embedding models via config.
    -   LangChain chat models for summarization and extraction via config.
    -   Where a standard LangChain component does not fit the retrieval mechanism (e.g., knowledge graph traversal requires edge-following, not vector similarity), the architecture may use native APIs for that technology. The constraint is to prefer standard components and justify deviations.
-   The Imperator uses standard LangGraph `MemorySaver` checkpointing for graph execution state (interrupt/resume, tool call tracking, mid-execution persistence). The `conversation_messages` table provides long-term conversation persistence via the Context Broker's own pipeline. These are complementary: `MemorySaver` handles the graph, the Context Broker handles the conversation.
-   **Substrate vs application logic:** Config file loading, YAML parsing, client factories, and connection pool management are substrate — they make StateGraphs possible but are not themselves StateGraphs. Application logic that makes decisions about what the system does must be in StateGraphs. The line: did we write the decision logic, or are we calling an external library?

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

**Purpose:** All external dependencies and tuning parameters are configurable. Configuration is separated into AE (infrastructure) and TE (cognitive) concerns per REQ-001 §9 and REQ-002 §7.

**5.1 Configuration Separation**

-   Configuration is split into two files:
    -   **AE configuration** (`/config/config.yml`): Infrastructure settings (database connections, ports, network), queue configuration, package source, operational settings (log level, health check intervals). Read at startup; changes require restart.
    -   **TE configuration** (`/config/te.yml`): Imperator settings (Identity, Purpose, Persona), inference provider assignments per cognitive function, build type definitions, cognitive parameters (token budgets, tier percentages, tuning). Hot-reloadable; changes take effect without restart.
-   The TE configuration is conceptually part of the TE package. When the TE is upgraded or replaced, its configuration changes without touching AE infrastructure settings.

**5.2 Inference Provider Configuration**

-   Inference providers are configured per cognitive function, not globally. Different functions have different requirements (the Imperator needs a capable conversational model; summarization needs a fast/cheap model; extraction has its own needs).
-   Each inference slot accepts a `base_url`, `model`, and optional `provider` field. The `provider` field defaults to `"openai"` (OpenAI-compatible wire protocol) but can be set to `"anthropic"` for Anthropic's native API.
-   Embedding and reranker slots are independent of LLM slots.

```yaml
# TE configuration (te.yml)
inference:
  imperator:
    provider: anthropic          # "openai" (default) or "anthropic"
    model: claude-haiku-4-5-20251001
    # API key via ANTHROPIC_API_KEY env var

  summarization:
    base_url: http://context-broker-ollama:11434/v1
    model: qwen2.5:7b

  extraction:
    base_url: http://context-broker-ollama:11434/v1
    model: qwen2.5:7b

embeddings:
  base_url: http://context-broker-infinity:7997
  model: nomic-ai/nomic-embed-text-v1.5

reranker:
  provider: api                  # "api" or "none"
  base_url: http://context-broker-infinity:7997
  model: mixedbread-ai/mxbai-rerank-xsmall-v1
  top_n: 10
```

-   The reranker defaults to the local Infinity container, which serves the `/rerank` endpoint on the internal network. No API key required.
-   `provider: api` hits a `/rerank` endpoint — works with Infinity (local), Together, Cohere, Jina, Voyage, or any compatible provider. The `base_url` includes any path prefix the provider requires.
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

-   Token budgets are snapped to standard buckets: 4K, 8K, 16K, 32K, 64K, 128K, 200K, 256K, 512K, 1M, 2M. Always snap up (never give less than requested).
-   Bucketing enables window sharing: multiple agents with similar budgets on the same conversation and build type share pre-built summaries instead of each triggering separate assembly.
-   The `budget` parameter in `get_context` represents the model's full context size. The build type owns the logic for safe utilization — models degrade past approximately 85% context fill, so the build type calculates tier allocations against the effective budget (budget × utilization percentage), not the raw number.
-   `fallback_tokens` in build type config is used when the caller doesn't specify a budget and auto-resolution from the provider fails.
-   Budget is resolved once at window creation and stored. Changes do not retroactively affect existing windows.

**5.4.1 Initial Assembly on New Windows**

-   When a new context window is created on a long-running conversation, the assembly pipeline does not attempt to summarize the entire conversation history. Instead, it looks back a configurable multiple of the window budget (e.g., 3× the effective budget) into the raw message history.
-   That lookback range is chunked and summarized into tier 2, then consolidated into tier 1. Everything older than the lookback is not included in the assembled context — it remains in the database and is searchable via `search_messages` and `search_knowledge`.
-   The lookback multiplier is a tuning parameter in the build type config (e.g., `initial_lookback_multiplier: 3`).
-   Subsequent assemblies are incremental: new messages enter tier 3, displaced messages are summarized into tier 2, and accumulated tier 2 summaries consolidate into tier 1. The assembly never re-reads raw messages that have already been summarized.

**5.5 Imperator Configuration**

```yaml
# TE configuration (te.yml)
imperator:
  base_url: http://context-broker-ollama:11434/v1
  model: qwen2.5:7b
  api_key_env: ""                # env var for API key (empty for keyless)
  system_prompt: imperator_identity   # prompt file name (loaded from /config/prompts/)
  build_type: standard-tiered
  max_context_tokens: auto
  admin_tools: false             # true = allow config and database modification (requires restart)
  max_iterations: 5
  temperature: 0.3
```

-   The Imperator's Identity, Purpose, and Persona are defined in its system prompt file (e.g., `/config/prompts/imperator_identity.md`), referenced by the `system_prompt` field. The system prompt is the primary artifact that defines who the Imperator is — it is part of the TE package. See REQ-001 §11.2.
-   The Imperator uses standard LangGraph `MemorySaver` checkpointing for graph execution state. It consumes the Context Broker's own MCP tools (`get_context`, `store_message`, `search_messages`, `search_knowledge`) — the same interface any external agent uses. This is self-consumption: the Imperator proves the system works by using it on itself.
-   `build_type` controls which retrieval layers the Imperator's context window uses. Defaults to `standard-tiered` for lower inference cost. Switching to `knowledge-enriched` activates the full retrieval pipeline including vector similarity search and knowledge graph traversal.
-   `admin_tools: false` (default): Imperator has diagnostic tools (always available):
    -   Query MAD container logs from Postgres (collected by the log shipper)
    -   Introspect assembled context (tier breakdown, token usage, build type)
    -   View pipeline status (pending jobs, queue depths, last assembly time)
    -   Search conversations and memories
    -   View health status and metrics
-   `admin_tools: true`: Imperator additionally has write capabilities:
    -   Read and write AE configuration (change LLM models, embedding models, reranker, tuning parameters)
    -   Toggle verbose pipeline logging
    -   Run read-only database queries
-   The Imperator cannot modify its own TE configuration (Identity, Purpose, system prompt, its own model). TE config is the architect's domain.

**5.6 Package Source Configuration**

-   See §1.5. The StateGraph package source is configured in `config.yml`.

### 6. Logging and Observability

**Purpose:** Structured, observable logging and health monitoring.

**6.1 Logging to stdout/stderr**

-   All logs go to stdout (normal) or stderr (errors). No log files inside containers.
-   Docker captures logs via `docker logs`.
-   The optional log shipper container discovers all containers on `context-broker-net` via the Docker API, tails their logs in real-time, and writes to a `system_logs` table in Postgres with resolved container names. This enables the Imperator to query logs across all MAD containers via SQL. Logs survive container rebuilds since they persist in the database. Deployments with their own log collection infrastructure can remove the log shipper container.

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

**7.3 Idempotency**

-   Per REQ-001 §7.3, operations that may be retried must be safe to execute more than once.
-   Conversation creation uses `ON CONFLICT DO NOTHING` — fully idempotent with a caller-supplied ID.
-   Message storage does not use an idempotency key (§3.5.1 — `idempotency_key` is not in the schema). Instead, consecutive duplicate detection (same role, content, and sender as the immediately preceding message) collapses duplicates on ingestion. This provides practical idempotency for the common retry case (network timeout → client resends the same message) without requiring callers to generate and track idempotency tokens.
-   Background jobs (embedding, assembly, extraction) are safe to re-execute: embedding is an overwrite, assembly holds a distributed lock (duplicate jobs wait and skip), and extraction via Mem0 is additive and deduplicated at the graph level.

**7.4 Network Topology**

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
