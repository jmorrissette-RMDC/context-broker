# Context Broker — High-Level Design (HLD)

**Date:** 2026-03-20
**Status:** Final

## 1. System Overview

The Context Broker is a self-contained context engineering and conversational memory service. It solves a fundamental problem in LLM-driven systems: agents reason within finite context windows, but participate in conversations that accumulate indefinitely. 

The Context Broker bridges this gap by managing the infinite conversation and proactively assembling purpose-built **context windows**. These windows are not merely truncated histories; they are curated views tailored to a specific participant, constructed according to a configured strategy (a **build type**), and strictly bound by a token budget.

Architecturally, the system is a **State 4 MAD (Multipurpose Agentic Duo)**. This pattern strictly separates application infrastructure (Action Engine) from cognitive logic (Thought Engine). All external dependencies—inference providers, package sources, storage paths, and network topology—are configuration choices rather than hardcoded couplings. The same code can run standalone on a single host or within a larger container orchestration environment.

## 2. Container Architecture

The system is deployed as a Docker Compose group of 5 containers, communicating securely over an internal bridge network.

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
                    │    ┌─────┴─────┬──────────────┐             │
                    │    ▼           ▼              ▼             │
                    │ ┌──────┐  ┌──────┐    ┌──────────┐          │
                    │ │pg:5432│ │redis │    │neo4j:7687│          │
                    │ └──────┘  └──────┘    └──────────┘          │
                    └─────────────────────────────────────────────┘
```

The gateway resides on both the external host-exposed network and the internal private network (`context-broker-net`). All other containers connect strictly to the internal network.

| Container | Role | Image |
| --- | --- | --- |
| `context-broker` | Nginx reverse proxy. Sole network boundary. Routes requests. | `nginx:alpine` |
| `context-broker-langgraph` | Python backend (ASGI server). Runs LangGraph flows and async queue workers. | Custom (Python 3.12-slim) |
| `context-broker-postgres` | Relational and vector storage (messages, windows, summaries). | `pgvector/pgvector:pg16` |
| `context-broker-neo4j` | Entity and relationship knowledge graph (via Mem0). | `neo4j:5` |
| `context-broker-redis` | Async job queues, locks, and ephemeral state. | `redis:7-alpine` |

**Volume Management:**
- Host `./data` is mounted into the containers to persist databases (`/data/postgres`, `/data/neo4j`, `/data/redis`) and the `imperator_state.json` file.
- Host `./config` is mounted to provide `/config/config.yml` and `/config/credentials/.env`.

**Deployment Customization:**
Users customize host paths, exposed ports, and resource limits for their environment via a `docker-compose.override.yml` file, adhering to the architectural pattern that the shipped compose file remains immutable.

## 3. Data Flow

The Context Broker optimizes for fast ingestion and rapid retrieval by shifting heavy summarization and extraction to background async pipelines. Context windows are strictly scoped to a participant-conversation pair, allowing multiple concurrent strategies for the same conversation.

**End-to-End Message Flow:**
1. **Context Window Initialization:** A client calls `conv_create_context_window` to establish a context window for a participant, automatically resolving its token budget via the configured provider.
2. **Ingestion:** A client calls `conv_store_message`. The system deduplicates the message (using a client-supplied idempotency key) and persists it to PostgreSQL. It enqueues independent background jobs for embedding and memory extraction to Redis, then returns success immediately.
3. **Embedding:** An async worker generates a contextual embedding for the message (using the configured LangChain embedding model) and updates the database row.
4. **Assembly Trigger:** The embedding worker identifies all participant context windows attached to the conversation and proactively enqueues an independent context assembly job for each.
5. **Context Assembly:** An async worker builds a new episodic context snapshot (e.g., tier 1 archival summary, tier 2 chunk summaries) via the configured LLM and stores the updated snapshot.
6. **Memory Extraction:** Operating concurrently with the embedding and assembly pipeline, an async worker passes the new verbatim messages to Mem0, extracting facts, entities, and relationships into the Neo4j knowledge graph.
7. **Retrieval:** An agent calls `conv_retrieve_context`. The system serves the pre-assembled episodic snapshot from the database, dynamically querying and injecting the semantic and knowledge graph layers at request time based on the recent context.

## 4. StateGraph Architecture

All programmatic and cognitive logic is encapsulated in **LangGraph StateGraphs**. The ASGI web server acts purely as a transport layer, invoking compiled graphs per request. All StateGraph node functions adhere strictly to state immutability, returning new state dictionaries rather than modifying state in-place.

### Core Flows
The following flows constitute the infrastructure-focused **Action Engine (AE)**:
- **Message Pipeline:** Synchronous ingestion, deduplication, and database insertion.
- **Embed Pipeline (Background):** Generates `pgvector` embeddings for search and retrieval.
- **Context Assembly (Background):** Executes the progressive compression strategy defined by the build type (Archival → Chunks → Verbatim).
- **Retrieval:** Assembles the final string using pre-computed episodic summaries and dynamically injected semantic/knowledge graph queries.
- **Memory Extraction (Background):** Leverages Mem0 to extract structured facts into Neo4j.
- **Hybrid Search and Query:** Combines vector search (pgvector) and full-text search (`ts_rank`, PostgreSQL's implementation of BM25-style ranking) via Reciprocal Rank Fusion (RRF), with optional cross-encoder reranking. This pipeline handles `conv_search` (conversation metadata) and `conv_search_messages` (individual verbatim messages).
- **Database Queries:** Dedicated flows for straightforward structured filtering and retrieval operations, such as handling `conv_search_context_windows`.
- **Memory Search:** Dedicated flow for querying the knowledge graph and semantic memory via Mem0 APIs (`mem_search`, `mem_get_context`).
- **Metrics:** Exposes Prometheus metrics collected from graph executions.

The following flow constitutes the cognitive **Thought Engine (TE)**:
- **Imperator:** A ReAct-style agent loop acting as the system's conversational interface. Utilizes **LangGraph checkpointing** for continuous state persistence across turns.

## 5. Storage Design

The system relies on eventual consistency across three distinct datastores. PostgreSQL is the absolute source of truth; no conversation data is ever lost if background processing is delayed.

- **PostgreSQL (`context-broker-postgres`):**
  - Uses the `pgvector` extension.
  - **Tables:** `conversations`, `conversation_messages` (includes `vector` and `tsvector` columns for hybrid search), `context_windows` (scoped to participant-conversation pairs), `conversation_summaries`. Embeddings dimensions are configuration-dependent.
  - Maintains schema versioning for automated migrations on boot. Schema migrations are strictly forward-only and non-destructive. The application will refuse to start if a migration cannot be safely applied without data loss.
- **Neo4j (`context-broker-neo4j`):**
  - Accessed exclusively via Mem0 APIs.
  - Stores `Entity` nodes, `Fact` nodes, and their relationships (`MENTIONS`, `RELATED_TO`). Enables rich graph traversal queries during knowledge-enriched retrieval.
- **Redis (`context-broker-redis`):**
  - Used for ephemeral state (distributed locks to prevent concurrent context assembly) and lightweight job queues. It utilizes disk persistence (RDB/AOF) mapped via `/data/redis` to ensure pending background jobs survive container restarts.

### 5.1 Backup and Recovery
The system relies entirely on host-level backups. All persistent state is confined to the mapped `/data` directory. Operational recovery is achieved via standard database dumps (`pg_dump`, `neo4j-admin database dump`) and capturing the Redis persistence directory. The Context Broker does not perform automated internal backups.

## 6. Interface Design

The Nginx gateway (`context-broker`) routes external traffic to the LangGraph container via two distinct protocols:

### 6.1 MCP Interface (HTTP/SSE)
Programmatic access for agents, running on `/mcp`. Exposes core capabilities as tools:
- `conv_create_conversation`, `conv_store_message`, `conv_retrieve_context`
- `conv_create_context_window`, `conv_get_history`
- `conv_search`, `conv_search_messages`, `conv_search_context_windows`
- `mem_search`, `mem_get_context`
- `broker_chat`
- `metrics_get`

### 6.2 OpenAI-Compatible Chat
Running on `/v1/chat/completions`. Allows any standard chat UI (e.g., Chatbot UI) to interact with the Imperator agent. The endpoint parses the standard OpenAI payload, invokes the Imperator StateGraph, and streams back standard OpenAI SSE tokens (`data: {"choices": ...}`).

### 6.3 Health and Metrics
- `GET /health`: Tests all backing service connections (PostgreSQL, Neo4j, Redis) and returns an aggregated status.
- `GET /metrics`: Exposes Prometheus metrics (request counts, queue depths, job durations).

### 6.4 Ingress Validation Boundary
To satisfy the architectural ingress contract, all data from external sources must be validated before affecting application state or reaching the StateGraphs:
- **MCP Endpoints:** Enforce structural validity via their declared `inputSchema`.
- **OpenAI-Compatible Chat:** Validates payloads using Pydantic models.
- **External API Responses:** Responses from external inference providers (LLMs, embedding models) are validated (e.g., via structured output parsers) before they can mutate the StateGraph state.
Malformed data is rejected at the boundary with appropriate HTTP status codes, protecting downstream operations from invalid state transitions.

## 7. Configuration System

All external dependencies and tuning parameters are governed by a single file: `/config/config.yml`.

- **Startup Configuration:** Database connection strings, network topology, and the StateGraph **package source** (`local`, `pypi`, or `devpi`) are read at startup and require a restart to change.
- **Hot-Reloadable Inference:** `llm`, `embeddings`, `reranker` provider settings, and build types are read fresh on each operation. Deployers can swap models or endpoints without container restarts.
- **Token Budget Resolution:** When a context window requests `auto` max tokens, the system queries the configured provider's model endpoint to automatically resolve the context length, falling back to a configured default if unavailable. Callers may override the build type's default token budget with an explicit `max_tokens` at window creation time. Once resolved, the token budget is stored with the window and remains immutable; subsequent model changes do not retroactively affect existing windows.
- **Credential Management:** API keys are never hardcoded in `config.yml`. Instead, the config references environment variables (e.g., `api_key_env: LLM_API_KEY`), which are populated securely via `/config/credentials/.env`.

## 8. Build Types and Retrieval

A **build type** defines the strategy for assembling a context window. Build types are open-ended and configured in `config.yml`. The system ships with two primary strategies:

### 8.1 `standard-tiered` (Episodic Only)
Focuses entirely on chronological history with a three-tier progressive compression model. It avoids vector search and knowledge graph queries to minimize inference cost.
- **Tier 1 (8% budget):** Deep archival summary.
- **Tier 2 (20% budget):** Rolling chunk summaries.
- **Tier 3 (72% budget):** Recent verbatim messages.

### 8.2 `knowledge-enriched` (Full Pipeline)
Combines episodic layers with semantically retrieved messages and knowledge graph facts.
- **Episodic Layers (70%):** Tier 1 (5%), Tier 2 (15%), Tier 3 (50%).
- **Semantic Retrieval (15%):** Uses `langchain_postgres.PGVector` to dynamically find past messages similar to the most recent verbatim messages, but outside the Tier 3 window.
- **Knowledge Graph (15%):** Dynamically traverses Mem0/Neo4j to extract structural facts and relationships pertinent to the entities identified in the recent context. At retrieval time, entities are extracted from the recent verbatim messages to serve as graph traversal seeds. The underlying Mem0/Neo4j data is accessed via Mem0's native APIs to ensure architectural consistency and proper edge-following traversal, rather than relying on vector similarity tools like `Neo4jVector`.

## 9. Async Processing Model

The system utilizes a lightweight, event-driven async architecture against Redis rather than heavy dependencies like Celery. 

- **Queues:** Three independent async consumers process Redis queues using asynchronous blocking reads that yield to the event loop (e.g., `await redis.blpop()`): `embedding_jobs` (List), `context_assembly_jobs` (List), and `memory_extraction_jobs` (Sorted Set).
- **Concurrency & Locking:** Workers process queues concurrently. Distributed locks prevent concurrent context assemblies on the same participant-conversation window.
- **Priority Scoring:** `memory_extraction_jobs` uses a ZSET to prioritize live user interactions over background agent prose or bulk migration data, based on a priority flag provided during message ingestion.
- **Dead-Letter / Retry:** Failed jobs are retried with exponential backoff. After maximum attempts, they are moved to a `dead_letter_jobs` list, which a periodic timer task attempts to re-queue up to a final cap before requiring manual intervention.

## 10. Imperator Design

The Imperator is the Context Broker's built-in conversational agent. It demonstrates and consumes the service's own capabilities via a ReAct-style loop.

- **Self-Consumption:** The Imperator uses LangChain's `ChatOpenAI.bind_tools()` to grant the LLM access to the exact same MCP tool functions (`conv_search`, `mem_search`) exposed to external callers.
- **Configurable Context:** The Imperator has its own configurable `build_type` and token budget, allowing operators to tune its cognitive strategy independently.
- **Persistent Continuity:** It stores its current `conversation_id` in `/data/imperator_state.json`. Upon reboot, it resumes the exact same continuous context window, ensuring long-term memory across restarts. If the state file is missing, or the referenced conversation no longer exists, it automatically creates a new conversation and persists the new ID.
- **Admin Capabilities:** Governed by `config.yml`, setting `admin_tools: true` grants the Imperator additional read/write tools to modify configuration files and execute read-only database queries.

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