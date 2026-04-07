# Context Broker — High-Level Design (HLD)

**Date:** 2026-03-20
**Status:** Synthesized Draft

## 1. System Overview

The Context Broker is a self-contained context engineering and conversational memory service. It solves a fundamental problem in LLM-driven systems: agents reason within finite context windows, but participate in conversations that accumulate indefinitely. 

The Context Broker bridges this gap by managing the infinite conversation and proactively assembling purpose-built **context windows**. These windows are not merely truncated histories; they are curated views tailored to a specific participant, constructed according to a configured strategy (a **build type**), and strictly bound by a token budget.

Architecturally, the system is a **State 4 MAD (Multipurpose Agentic Duo)**. This pattern strictly separates application infrastructure (Action Engine) from cognitive logic (Thought Engine). All external dependencies—inference providers, package sources, storage paths, and network topology—are configuration choices rather than hardcoded couplings. The same code can run standalone on a single host or within a larger container orchestration environment.

## 2. Container Architecture

The system is deployed as a Docker Compose group of 5 containers, communicating securely over an internal bridge network.

```text
                    ┌─────────────────────────────────────────────┐
                    │           Host Network                      │
                    │                                             │
                    │   Port 8080 ──┐                             │
                    │               ▼                             │
                    │   ┌───────────────────┐                     │
                    │   │  context-broker   │ ◄── Nginx gateway   │
                    │   └────────┬──────────┘                     │
                    └────────────┼────────────────────────────────┘
                                 │
                    ┌────────────┼────────────────────────────────┐
                    │            ▼  Internal Bridge Network       │
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

## 3. Data Flow

The Context Broker optimizes for fast ingestion and rapid retrieval by shifting heavy summarization and extraction to background async pipelines. 

**End-to-End Message Flow:**
1. **Ingestion:** A client calls `conv_store_message`. The system deduplicates the message and persists it to PostgreSQL. It enqueues background jobs to Redis and returns success immediately.
2. **Embedding:** An async worker generates a contextual embedding for the message (using the configured LangChain `OpenAIEmbeddings` model) and updates the database row.
3. **Assembly Trigger:** The embedding worker checks if the context window's token thresholds have been crossed. If so, it enqueues a context assembly job.
4. **Context Assembly:** An async worker builds a new context snapshot (e.g., tier 1 archival summary, tier 2 chunk summaries) via the configured LLM and stores the updated snapshot.
5. **Memory Extraction:** In parallel, an async worker passes the new conversation chunks to Mem0, extracting facts, entities, and relationships into the Neo4j knowledge graph.
6. **Retrieval:** An agent calls `conv_retrieve_context`. The system immediately serves the pre-assembled context window snapshot from the database without waiting for LLM inference.

## 4. StateGraph Architecture

All programmatic and cognitive logic is encapsulated in **LangGraph StateGraphs**. The ASGI web server acts purely as a transport layer, invoking compiled graphs per request.

### Core Flows
- **Message Pipeline:** Synchronous ingestion, deduplication, and database insertion.
- **Embed Pipeline (Background):** Generates `pgvector` embeddings for search and retrieval.
- **Context Assembly (Background):** Executes the progressive compression strategy defined by the build type (Archival → Chunks → Verbatim).
- **Retrieval:** Assembles the final string using pre-computed summaries and, if configured, semantic/knowledge graph queries.
- **Memory Extraction (Background):** Leverages Mem0 to extract structured facts into Neo4j.
- **Search:** Hybrid vector (pgvector) + BM25 search, combined via Reciprocal Rank Fusion (RRF), with optional cross-encoder reranking.
- **Imperator:** A ReAct-style agent loop acting as the system's conversational interface.
- **Metrics:** Exposes Prometheus metrics collected from graph executions.

## 5. Storage Design

The system relies on eventual consistency across three distinct datastores. PostgreSQL is the absolute source of truth; no conversation data is ever lost if background processing is delayed.

- **PostgreSQL (`context-broker-postgres`):**
  - Uses the `pgvector` extension.
  - **Tables:** `conversations`, `conversation_messages` (includes `vector(768)` and `tsvector` columns for hybrid search), `context_windows`, `conversation_summaries`.
  - Maintains schema versioning for automated migrations on boot.
- **Neo4j (`context-broker-neo4j`):**
  - Accessed exclusively via Mem0 APIs.
  - Stores `Entity` nodes, `Fact` nodes, and their relationships (`MENTIONS`, `RELATED_TO`). Enables rich graph traversal queries during knowledge-enriched retrieval.
- **Redis (`context-broker-redis`):**
  - Purely ephemeral. Used for lightweight job queues (Lists and priority-scored Sorted Sets) and distributed locks to prevent concurrent context assembly.

## 6. Interface Design

The Nginx gateway (`context-broker`) routes external traffic to the LangGraph container via two distinct protocols:

### 6.1 MCP Interface (HTTP/SSE)
Programmatic access for agents, running on `/mcp`. Exposes core capabilities as tools:
- `conv_create_conversation`, `conv_store_message`, `conv_retrieve_context`
- `conv_search_messages`, `conv_search_context_windows`
- `mem_search`, `mem_get_context`
- `broker_chat`
- `metrics_get`

### 6.2 OpenAI-Compatible Chat
Running on `/v1/chat/completions`. Allows any standard chat UI (e.g., Chatbot UI) to interact with the Imperator agent. The endpoint parses the standard OpenAI payload, invokes the Imperator StateGraph, and streams back standard OpenAI SSE tokens (`data: {"choices": ...}`).

### 6.3 Health and Metrics
- `GET /health`: Tests all backing service connections (PostgreSQL, Neo4j, Redis) and returns an aggregated status.
- `GET /metrics`: Exposes Prometheus metrics (request counts, queue depths, job durations).

## 7. Configuration System

All external dependencies and tuning parameters are governed by a single file: `/config/config.yml`.

- **Hot-Reloadable Inference:** `llm`, `embeddings`, and `reranker` provider settings are read fresh on each operation. Deployers can swap models or endpoints (e.g., pointing `base_url` to a local vLLM or OpenAI) without container restarts.
- **Token Budget Resolution:** When a context window requests `auto` max tokens, the system queries the configured provider's model endpoint to automatically resolve the context length, falling back to a configured default if unavailable.
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
- **Semantic Retrieval (15%):** Uses `langchain_postgres.PGVector` to find past messages similar to the current topic but outside the Tier 3 window.
- **Knowledge Graph (15%):** Traverses Mem0/Neo4j to extract structural facts and relationships pertinent to the entities identified in the recent context.

## 9. Async Processing Model

The system utilizes a lightweight, native async polling architecture against Redis rather than heavy dependencies like Celery. 

- **Queues:** Three independent async consumers process Redis queues: `embedding_jobs` (List), `context_assembly_jobs` (List), and `memory_extraction_jobs` (Sorted Set).
- **Priority Scoring:** `memory_extraction_jobs` uses a ZSET to prioritize live user interactions over background agent prose or bulk migration data.
- **Dead-Letter / Retry:** Failed jobs are retried with exponential backoff. After maximum attempts, they are moved to a `dead_letter_jobs` list, which a background sweep periodically attempts to re-queue. 

## 10. Imperator Design

The Imperator is the Context Broker's built-in conversational agent. It demonstrates and consumes the service's own capabilities via a ReAct-style loop.

- **Self-Consumption:** The Imperator uses LangChain's `ChatOpenAI.bind_tools()` to grant the LLM access to the exact same MCP tool functions (`conv_search`, `mem_search`) exposed to external callers.
- **Persistent Continuity:** It stores its current `conversation_id` in `/data/imperator_state.json`. Upon reboot, it resumes the exact same continuous context window, ensuring long-term memory across restarts.
- **Admin Capabilities:** Governed by `config.yml`, setting `admin_tools: true` grants the Imperator additional read/write tools to modify configuration files and execute read-only database queries.