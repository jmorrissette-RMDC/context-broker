# Context Broker — High-Level Design (HLD)

**Date:** 2026-03-20
**Status:** Final

## 1. System Overview

The Context Broker is a self-contained context engineering and conversational memory service. It solves a fundamental problem in LLM-driven systems: agents reason within finite context windows, but participate in conversations that accumulate indefinitely. 

The Context Broker bridges this gap by managing the infinite conversation and proactively assembling purpose-built **context windows**. These windows are not merely truncated histories; they are curated views tailored to a specific participant, constructed according to a configured strategy (a **build type**), and strictly bound by a token budget.

Architecturally, the system is a **State 4 MAD (Multipurpose Agentic Duo)**. This pattern strictly separates application infrastructure (Action Engine) from cognitive logic (Thought Engine). All external dependencies—inference providers, package sources, storage paths, and network topology—are configuration choices rather than hardcoded couplings. The same code can run standalone on a single host or within a larger container orchestration environment.

## 2. Container Architecture

The system is deployed as a Docker Compose group of 6 containers, communicating securely over an internal bridge network.

```text
                    ┌─────────────────────────────────────────────┐
                    │           Host Network (default)            │
                    │                                             │
                    │   Port 8080 ──┐                             │
                    │               ▼                             │
                    │   ┌───────────────────┐                     │
                    │   │  context-broker   │ ◄── Nginx gateway   │
                    │   └────────┬──────────┘                     │
                    └────────────┼────────────────────────────────┘
                                 │
                    ┌────────────┼────────────────────────────────┐
                    │            ▼  Internal Network              │
                    │          (context-broker-net)               │
                    │   ┌──────────────────┐                      │
                    │   │ context-broker-  │ ◄── All application  │
                    │   │ langgraph:8000   │     logic            │
                    │   └──────┬───────────┘                      │
                    │          │                                  │
                    │    ┌─────┴─────┬──────────┐                  │
                    │    ▼           ▼          ▼                  │
                    │ ┌──────┐  ┌──────────┐                      │
                    │ │pg:5432│  │neo4j:7687│                      │
                    │ └──────┘  └──────────┘                      │
                    └─────────────────────────────────────────────┘
```

The gateway resides on both the external host-exposed network and the internal `context-broker-net` bridge network. All other containers connect only to `context-broker-net`. This is a standard Docker bridge network (not `internal: true`) — containers have outbound internet access via Docker's host NAT for cloud LLM APIs and package downloads, but no ports are published so they are not directly reachable from outside. The gateway is the sole inbound entry point, publishing port 8080 on the host.

| Container | Role | Image |
| --- | --- | --- |
| `context-broker` | Nginx reverse proxy. Sole network boundary. Routes requests. | `nginx:alpine` |
| `context-broker-langgraph` | Python backend (ASGI server). Runs LangGraph flows and background workers. | Custom (Python 3.12-slim) |
| `context-broker-postgres` | Relational and vector storage (messages, windows, summaries). | `pgvector/pgvector:pg16` |
| `context-broker-neo4j` | Entity and relationship knowledge graph (via Mem0). | `neo4j:5` |
| `context-broker-infinity` (optional) | Local embeddings and reranking via OpenAI-compatible `/v1/embeddings` and `/v1/rerank` APIs. No API keys needed. Remove if using cloud providers. | `michaelf34/infinity` |
| `context-broker-ollama` (optional) | Local LLM inference via OpenAI-compatible API. No API keys needed. Remove if using cloud providers. | `ollama/ollama` |
| `context-broker-log-shipper` (optional) | Discovers containers on context-broker-net via Docker API, tails logs, writes to Postgres `system_logs` table with resolved names. Remove if deployment has its own log collector. | Custom (Python) |

The Infinity container serves embeddings and reranking on the internal network using the Infinity inference engine. It loads configured models at startup (first boot downloads weights) and exposes standard OpenAI-compatible endpoints. The LangGraph container calls it the same way it calls any cloud embedding or reranking provider.

The Ollama container is optional. When present, it provides fully local LLM inference on the internal network — the LangGraph container calls it the same way it calls any cloud provider. When not present, LLM inference routes to configured cloud providers. The same `config.yml` controls the routing.

**Volume Management:**
- Host `./data` is mounted into the containers to persist databases (`/data/postgres`, `/data/neo4j`), Infinity model weights (`/data/infinity`), and the `imperator_state.json` file.
- Host `./config` is mounted to provide `/config/config.yml` and `/config/credentials/.env`.

**Deployment Customization:**
Users customize host paths, exposed ports, and resource limits for their environment via a `docker-compose.override.yml` file, adhering to the architectural pattern that the shipped compose file remains immutable.

## 3. Data Flow

The Context Broker optimizes for fast ingestion and rapid retrieval by shifting heavy summarization and extraction to background async pipelines. Context windows are strictly scoped to a participant-conversation pair, allowing multiple concurrent strategies for the same conversation.

**End-to-End Message Flow:**
1. **Context Window Initialization:** A client calls `conv_create_context_window` to establish a context window for a participant, automatically resolving its token budget via the configured provider.
2. **Ingestion:** A client calls `conv_store_message` (accepting `context_window_id` or `conversation_id`, at least one required). The system persists the message to PostgreSQL, computing `token_count` and `priority` internally, then returns success immediately. Background workers poll for new messages (NULL embeddings, unextracted messages) and process them asynchronously.
3. **Embedding:** An async worker generates a contextual embedding for the message (using the configured LangChain embedding model) and updates the database row.
4. **Assembly Trigger:** After embedding a batch, the worker identifies context windows that need reassembly and triggers assembly for each.
5. **Context Assembly:** An async worker builds a new episodic context snapshot (e.g., tier 1 archival summary, tier 2 chunk summaries) via the configured LLM and stores the updated snapshot.
6. **Memory Extraction:** Operating concurrently with the embedding and assembly pipeline, an async worker passes the new verbatim messages to Mem0, extracting facts, entities, and relationships into the Neo4j knowledge graph.
7. **Retrieval:** An agent calls `conv_retrieve_context`. The system serves the pre-assembled episodic snapshot from the database, dynamically querying and injecting the semantic and knowledge graph layers at request time based on the recent context. The result is a structured **messages array** (list of OpenAI-format message dicts with `role`, `content`, and optionally `tool_calls` / `tool_call_id`), not a concatenated text string.

## 4. StateGraph Architecture

All programmatic and cognitive logic is encapsulated in **LangGraph StateGraphs**. The ASGI web server acts purely as a transport layer, invoking compiled graphs per request. All StateGraph node functions adhere strictly to state immutability, returning new state dictionaries rather than modifying state in-place.

### Core Flows
The following flows constitute the infrastructure-focused **Action Engine (AE)**:
- **Message Pipeline:** Synchronous ingestion and database insertion. Computes `token_count` and `priority` internally.
- **Embed Pipeline (Background):** Generates `pgvector` embeddings for search and retrieval.
- **Context Assembly (Background):** Executes the build-type-specific assembly graph. Each build type registers its own assembly StateGraph (e.g., passthrough simply selects recent messages; standard-tiered runs progressive compression; knowledge-enriched adds semantic and graph layers).
- **Retrieval:** Executes the build-type-specific retrieval graph. Produces a structured messages array using pre-computed episodic summaries and dynamically injected semantic/knowledge graph queries.
- **Memory Extraction (Background):** Leverages Mem0 to extract structured facts into Neo4j.
- **Hybrid Search and Query:** Combines vector search (pgvector) and full-text search (`ts_rank`, PostgreSQL's implementation of BM25-style ranking) via Reciprocal Rank Fusion (RRF), with optional API-based reranking (via the Infinity container or any `/rerank`-compatible provider). Backs the `search_messages` core tool.
- **Database Queries:** Dedicated flows for straightforward structured filtering and retrieval operations (`conv_search_context_windows`, `conv_get_history`).
- **Memory Search:** Dedicated flow for querying the knowledge graph and semantic memory via Mem0 APIs. Backs the `search_knowledge` core tool.
- **Metrics:** Exposes Prometheus metrics collected from graph executions.

The following flow constitutes the cognitive **Thought Engine (TE)**:
- **Imperator:** A graph-based ReAct agent (agent node → tool node → conditional edges) acting as the system's conversational interface. Uses standard LangGraph `MemorySaver` checkpointer for graph execution state (interrupt/resume, tool call tracking). Consumes the Context Broker's own MCP tools (`get_context`, `store_message`, `search_messages`, `search_knowledge`) — the same interface any external agent uses. Long-term conversation persistence is handled by the Context Broker pipeline; `MemorySaver` handles mid-execution state only.

## 5. Storage Design

The system relies on eventual consistency across three distinct datastores. PostgreSQL is the absolute source of truth; no conversation data is ever lost if background processing is delayed.

- **PostgreSQL (`context-broker-postgres`):**
  - Uses the `pgvector` extension.
  - **Tables:** `conversations`, `conversation_messages` (OpenAI-format fields: `role`, `content` (nullable), `sender`, `recipient`, `tool_calls` (JSONB), `tool_call_id`, `token_count`, `priority`; includes `vector` and `tsvector` columns for hybrid search), `context_windows` (scoped to participant-conversation pairs; includes `last_accessed_at` for dormant window detection), `conversation_summaries`. Embeddings dimensions are configuration-dependent.
  - Maintains schema versioning for automated migrations on boot. Schema migrations are strictly forward-only and non-destructive. The application will refuse to start if a migration cannot be safely applied without data loss.
- **Neo4j (`context-broker-neo4j`):**
  - Accessed exclusively via Mem0 APIs.
  - Stores `Entity` nodes, `Fact` nodes, and their relationships (`MENTIONS`, `RELATED_TO`). Enables rich graph traversal queries during knowledge-enriched retrieval.
- **Job State (PostgreSQL):**
  - Background processing state is tracked in the database itself. Messages with `embedding IS NULL` need embedding; messages with `memory_extracted IS NOT TRUE` need extraction. Context windows with stale `last_assembled_at` need reassembly. No external queue is required — the database is the single source of truth for both data and processing state. Concurrent assembly is prevented by PostgreSQL advisory locks.

### 5.1 Backup and Recovery
The system relies entirely on host-level backups. All persistent state is confined to the mapped `/data` directory. Operational recovery is achieved via standard database dumps (`pg_dump`, `neo4j-admin database dump`). The Context Broker does not perform automated internal backups.

## 6. Interface Design

The Nginx gateway (`context-broker`) routes external traffic to the LangGraph container via two distinct protocols:

### 6.1 MCP Interface (HTTP/SSE)
Programmatic access for agents, running on `/mcp`. Exposes core capabilities as tools:
- `conv_create_conversation`, `conv_store_message` (accepts `context_window_id` or `conversation_id`), `conv_retrieve_context`
- `conv_create_context_window`, `conv_get_history`
- `conv_search`, `conv_search_messages`, `conv_search_context_windows`
- `mem_search`, `mem_get_context`, `mem_add`, `mem_list`, `mem_delete`
- `imperator_chat`
- `metrics_get`

### 6.2 OpenAI-Compatible Chat
Running on `/v1/chat/completions`. Allows any standard chat UI (e.g., Chatbot UI) to interact with the Imperator agent. The endpoint parses the standard OpenAI payload, invokes the Imperator StateGraph, and streams back standard OpenAI SSE tokens (`data: {"choices": ...}`).

### 6.3 Health and Metrics
- `GET /health`: Tests all backing service connections (PostgreSQL, Neo4j) and returns an aggregated status.
- `GET /metrics`: Exposes Prometheus metrics (request counts, pipeline status, job durations).

### 6.4 Ingress Validation Boundary
To satisfy the architectural ingress contract, all data from external sources must be validated before affecting application state or reaching the StateGraphs:
- **MCP Endpoints:** Enforce structural validity via their declared `inputSchema`.
- **OpenAI-Compatible Chat:** Validates payloads using Pydantic models.
- **External API Responses:** Responses from external inference providers (LLMs, embedding models) are validated (e.g., via structured output parsers) before they can mutate the StateGraph state.
Malformed data is rejected at the boundary with appropriate HTTP status codes, protecting downstream operations from invalid state transitions.

## 7. Configuration System

Configuration is separated into AE (infrastructure) and TE (cognitive) concerns per REQ-001 §9 and REQ-002 §7:

- **AE Configuration** (`/config/config.yml`): Database connection strings, network topology, worker settings, package source (`local`, `pypi`, or `devpi`), operational parameters (log level, health check intervals). Read at startup; changes require a container restart.
- **TE Configuration** (`/config/te.yml`): Imperator settings (Identity, Purpose, Persona), inference provider assignments per cognitive function (Imperator conversation, summarization, extraction), embeddings, reranker, build type definitions, cognitive tuning parameters. Hot-reloadable; changes take effect without restart.
- **Per-Use Inference:** Different cognitive functions use different LLM configurations. The Imperator's conversational model, summarization model, and extraction model are independently configurable. This enables cost/quality trade-offs (e.g., a capable model for the Imperator, a fast/cheap model for bulk summarization).
- **Provider Abstraction:** Each LLM slot supports a `provider` field (`"openai"` for OpenAI-compatible providers, `"anthropic"` for Anthropic's native API). Defaults to `"openai"`.
- **Token Budget Resolution:** When a context window requests `auto` max tokens, the system queries the configured provider's model endpoint to automatically resolve the context length, falling back to a configured default if unavailable. Callers may override the build type's default token budget with an explicit `max_tokens` at window creation time. Once resolved, the token budget is stored with the window and remains immutable; subsequent model changes do not retroactively affect existing windows.
- **Credential Management:** API keys are never hardcoded in configuration files. They are stored in `/config/credentials/.env` and loaded into the container environment via `env_file` in `docker-compose.yml`. Each inference provider slot includes an `api_key_env` field naming the environment variable that holds its API key. This explicit indirection ensures every provider's key source is visible in config — no magic defaults or auto-detection. Keyless providers (Ollama, Infinity) use `api_key_env: ""`.

## 8. Build Types and Retrieval

A **build type** is a registered pair of StateGraphs (assembly + retrieval) that share a standard contract (input/output state schemas, config interface). Deployers add new build types by writing two graphs and registering them in `config.yml`. Each build type may specify its own LLM configuration for summarization and extraction, allowing cost/quality trade-offs per strategy. The system ships with three build types:

### 8.1 `passthrough` (No Summarization)
Returns recent verbatim messages up to the token budget with no summarization or compression. Zero inference cost. Useful for short conversations, testing, or scenarios where full fidelity of recent messages is preferred over historical depth.

### 8.2 `standard-tiered` (Episodic Only)
Focuses entirely on chronological history with a three-tier progressive compression model. It avoids vector search and knowledge graph queries to minimize inference cost.
- **Tier 1 (8% budget):** Deep archival summary.
- **Tier 2 (20% budget):** Rolling chunk summaries.
- **Tier 3 (72% budget):** Recent verbatim messages.

### 8.3 `knowledge-enriched` (Full Pipeline)
Combines episodic layers with semantically retrieved messages and knowledge graph facts.
- **Episodic Layers (70%):** Tier 1 (5%), Tier 2 (15%), Tier 3 (50%).
- **Semantic Retrieval (15%):** Uses `langchain_postgres.PGVector` to dynamically find past messages similar to the most recent verbatim messages, but outside the Tier 3 window.
- **Knowledge Graph (15%):** Dynamically traverses Mem0/Neo4j to extract structural facts and relationships pertinent to the entities identified in the recent context. At retrieval time, entities are extracted from the recent verbatim messages to serve as graph traversal seeds. Knowledge graph retrieval uses Mem0's native APIs for proper edge-following traversal — this is a justified deviation from the LangChain-first constraint (REQ §4.5) because graph traversal (following entity relationships) is architecturally distinct from vector similarity search, and no standard LangChain retriever supports it.

**Dynamic Tier Scaling:** Tier percentages define target allocations, but actual content may not fill a tier (e.g., a new conversation has no archival summary). Unused budget from under-filled tiers is redistributed to lower tiers (Tier 3 absorbs unused Tier 1/2 budget), ensuring the full token budget is utilized.

**Effective Utilization:** The token budget represents the model's full context size. Build types calculate tier allocations against an effective budget (budget × utilization percentage, default 85%). Models degrade in quality past approximately 85% context fill — the build type enforces this threshold, not the caller.

**Initial Assembly on Long Conversations:** When a new window is created on an existing long conversation, the assembly does not summarize the entire history. It looks back a configurable multiple (e.g., 3×) of the effective budget into the raw messages, summarizes that range into tier 2 and tier 1, and ignores everything older. Older messages remain in the database and are reachable via `search_messages` and `search_knowledge`. Subsequent assemblies are incremental — new messages push into tier 3, displaced messages are summarized into tier 2, and accumulated tier 2 summaries consolidate into tier 1. The pipeline never re-reads raw messages that have already been summarized.

**Memory Confidence Scoring:** Extracted memories carry a confidence score that decays over time via a configurable half-life. Memories re-confirmed by new conversation evidence have their confidence refreshed. Low-confidence memories are deprioritized during knowledge graph retrieval.

## 9. Async Processing Model

Background processing is driven by database state, not external queues. The database is the single source of truth for both data and processing state — no external queue system is required.

- **Embedding Worker:** Polls `SELECT FROM conversation_messages WHERE embedding IS NULL ORDER BY priority DESC LIMIT batch_size`. Embeds messages in batches using the configured embedding API. After embedding, checks for context windows that need reassembly.
- **Extraction Worker:** Polls `SELECT FROM conversation_messages WHERE memory_extracted IS NOT TRUE ORDER BY priority DESC`. Sends message batches to Mem0 for knowledge extraction into the Neo4j graph. Marks messages as extracted after successful processing.
- **Assembly Worker:** Triggered after embedding batches complete. Checks context windows where new tokens have accumulated since last assembly and runs the build-type-specific assembly graph.
- **Concurrency & Locking:** PostgreSQL advisory locks prevent concurrent context assemblies on the same context window.
- **Retry:** Workers naturally retry on the next poll cycle — unprocessed messages remain in the query results until successfully processed. No dead-letter handling needed.

## 10. Imperator Design

The Imperator is the Context Broker's TE — the cognitive agent that owns the service's conversational intelligence. It is a fully autonomous agent with declared Identity and Purpose per REQ-001 §11.

- **Identity and Purpose:** Defined in the system prompt file (e.g., `/config/prompts/imperator_identity.md`), referenced by the `system_prompt` field in the TE configuration (`/config/te.yml`). The system prompt is the primary artifact of the TE package — it carries Identity, Purpose, Persona, and behavioral instructions. These do not change at runtime.
- **Graph-Based ReAct:** Implemented as a LangGraph StateGraph with an agent node (LLM call with `bind_tools()`) and a tool node. Conditional edges route back to the agent when tool calls are present, or to the end node when the response is complete. This is a proper graph-based ReAct pattern (System 3 ReAct), not an imperative loop.
- **Standard Checkpointer:** Uses LangGraph `MemorySaver` for graph execution state — enabling interrupt/resume, human-in-the-loop, tool call tracking, and all native LangGraph capabilities. `MemorySaver` (not `PostgresSaver`) because the TE must have no infrastructure dependencies beyond the AE it runs on — this is essential for eMAD portability. Mid-execution state does not need to survive container restarts.
- **Self-Consumption via MCP:** The Imperator consumes the Context Broker's own MCP tools (`get_context`, `store_message`, `search_messages`, `search_knowledge`) — the same interface any external agent uses. This is the reference implementation: the Imperator proves the system works by using it on itself. Any TE connecting via `langchain-mcp-adapters` gets the same tools automatically.
- **Per-Use Inference:** The Imperator's LLM is configured in `te.yml`, independently from the AE's summarization and extraction LLMs in `config.yml`. All providers use the OpenAI-compatible wire protocol (including Anthropic).
- **Configurable Context:** The Imperator has its own configurable `build_type` and token budget. Budget is snapped to standard buckets for efficient window sharing.
- **Persistent Continuity:** Stores its `conversation_id` in `/data/imperator_state.json`. On reboot, resumes the same conversation. If the state file is missing or the conversation no longer exists, creates a new one automatically.
- **Standalone Operation:** Without a Context Broker endpoint configured, the Imperator operates with `MemorySaver` only — basic but functional. The Context Broker is an upgrade (curated context assembly, knowledge extraction, semantic search), not a dependency.
- **Diagnostic Capabilities (always available):** The Imperator can query MAD container logs from Postgres (collected by the log shipper), introspect assembled context (tier breakdown, token usage, build type), and view pipeline status (pending jobs, last assembly time). These are read-only diagnostic tools that help the Imperator observe and explain its own system's behavior.
- **Admin Capabilities (gated by `admin_tools: true`):** Read and write AE configuration (change LLM models, embedding models, reranker, tuning parameters), toggle verbose pipeline logging, and execute read-only database queries. The Imperator can manage the infrastructure it runs on but cannot modify its own TE configuration (Identity, Purpose, system prompt, its own model) — that is the architect's domain.

## 11. Resilience and Observability

- **Independent Startup:** Containers start independently without strict dependency ordering (`depends_on`). Dependency unavailability is handled dynamically at request time via degradation or retries, enabling faster parallel startup and resilient recovery.
- **Graceful Degradation:** Failure of optional components (such as Neo4j or the local reranker) results in degraded operation rather than a system crash. Core operations continue with reduced capabilities, and the `/health` endpoint surfaces the degraded status.
- **Eventual Consistency:** The system prioritizes PostgreSQL message storage as the absolute source of truth. Background processing (assembly, extraction) is eventually consistent and safely retries upon failure without risking the loss of conversational records.
- **Logging Architecture:** The system emits structured JSON logs to standard output and error, ensuring machine readability. Configurable log levels guarantee that sufficient execution context is captured for debugging without leaking credentials or full payload bodies.

### 11.1 Architectural Trade-offs
The architecture deliberately accepts several constraints to achieve scalable context engineering:
- **Memory Contamination:** The infinite conversation accumulates everything. Bad reasoning can enter the conversation and become context for future invocations, creating a self-reinforcing contamination loop.
- **Progressive Summarization Decay:** The three-tier compression strategy loses fidelity over long timescales, converging toward high-level generalities and losing specific historical details.
- **Scale and Assembly Cost:** Proactive assembly runs after every message. At significant scale, this shifts the bottleneck from inference-at-retrieval to compute-at-ingestion.
- **Error Propagation:** If context assembly produces a systematically skewed view, it will predictably skew the downstream reasoning of any agent relying on that context.

## 12. Security and Authentication

- **Least Privilege Runtime:** The application containers (specifically the custom LangGraph container) enforce a strict least-privilege boundary, running as a dedicated non-root service account. The runtime architecture assumes no root-level file operations or permissions.
- **Authentication Posture:** The Context Broker ships without built-in authentication, adopting a trust model designed for single-user or isolated internal networks. For shared environments, standard authentication (such as basic auth, mutual TLS, or an auth proxy) is delegated to the Nginx gateway layer without requiring modifications to the application itself.