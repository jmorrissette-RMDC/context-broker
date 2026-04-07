# Context Broker — High-Level Design (HLD)

**Date:** 2026-03-20
**Status:** Approved for Implementation

## 1. System Overview

The Context Broker is a self-contained context engineering and conversational memory service. Built as a State 4 MAD (Multipurpose Agentic Duo), it completely separates application infrastructure (Action Engine) from cognitive logic (Thought Engine) while treating all external dependencies—inference providers, package sources, and storage paths—as configurable options.

The system solves the problem of the "infinite conversation." As conversational records grow indefinitely, LLMs remain constrained by finite context windows. The Context Broker provides purpose-built context windows using configurable build strategies (progressive compression, semantic search, and knowledge graph traversal). It proactively assembles these windows asynchronously so that they are ready on-demand.

The service provides both an MCP (Model Context Protocol) interface for programmatic agent integration and an OpenAI-compatible `/v1/chat/completions` interface for direct interaction with its internal conversational agent, the Imperator.

---

## 2. Container Architecture

The Context Broker runs as a standalone docker-compose group of 5 containers, communicating securely over an internal bridge network.

```text
External HTTP/SSE
      │
      ▼
┌──────────────────────────┐
│ context-broker (Nginx)   │ Port 80/443 (Gateway)
└──────────┬───────────────┘
           │ routes /mcp, /v1/chat/completions, /health
           ▼
┌──────────────────────────┐
│ context-broker-langgraph │ Port 8000 (Python 3.12 / Quart)
└─────┬────────┬────────┬──┘ (Application logic, StateGraphs, arq worker)
      │        │        │
      ▼        ▼        ▼
┌────────┐ ┌──────┐ ┌───────┐
│postgres│ │neo4j │ │ redis │
└────────┘ └──────┘ └───────┘
```

| Container | Role | Image |
| :--- | :--- | :--- |
| `context-broker` | Nginx reverse proxy. Sole network boundary. Routes requests. | `nginx:alpine` |
| `context-broker-langgraph` | Python backend. Runs Quart ASGI server, LangGraph flows, and the async queue worker. | Custom (Python 3.12-slim) |
| `context-broker-postgres` | Relational and vector storage (messages, windows, summaries). | `pgvector/pgvector:pg16` |
| `context-broker-neo4j` | Entity and relationship knowledge graph (via Mem0). | `neo4j:5` |
| `context-broker-redis` | Async job queues (`arq`), locks, ephemeral state caching. | `redis:7-alpine` |

**Volume Management:**
- `./data` mounted into `/data` inside containers (`/data/postgres`, `/data/neo4j`, `/data/redis`, `/data/imperator_state.json`).
- `./config` mounted into `/config` inside containers (`config.yml` and `/config/credentials/.env`).

---

## 3. Configuration System

The Context Broker eliminates hardcoded inference and infrastructure couplings via a single, hot-reloadable `/config/config.yml` and a `.env` file for secrets.

**Providers and Models**
Instead of custom LLM adapters, the system relies strictly on standard LangChain OpenAI-compatible classes (`ChatOpenAI`, `OpenAIEmbeddings`). The config drives instantiation:

```yaml
llm:
  base_url: https://api.openai.com/v1
  model: gpt-4o-mini
  api_key_env: LLM_API_KEY # References .env

embeddings:
  base_url: https://api.openai.com/v1
  model: text-embedding-3-small
  api_key_env: EMBEDDINGS_API_KEY
```

**State 4 Decoupling**
By pointing `base_url` to any compliant inference server (OpenAI, vLLM, Ollama) and mounting credentials via `.env`, the exact same Docker image can run on an enterprise cluster or a local laptop.

---

## 4. StateGraph Flow Designs

All cognitive and procedural logic is encapsulated in **LangGraph StateGraphs**. The Quart app acts purely as a transport layer. The architecture heavily utilizes `langchain` and `langgraph` standards.

### 4.1 Message Pipeline Flow (`conv_store_message`)
**State Schema:** `{ message: dict, deduplicated: bool, queued_jobs: list, error: str }`
1. **Deduplication Node:** Checks Redis for repeated recent content from the sender.
2. **Persistence Node:** Saves the raw message to PostgreSQL via `asyncpg`.
3. **Dispatch Node:** Enqueues two parallel background `arq` jobs: `embed_pipeline` and `memory_extraction`.
*Returns immediately to the caller after storage and queue dispatch.*

### 4.2 Embed & Context Assembly Flow (Background Job)
**State Schema:** `{ message_id: str, text: str, vector: list, windows_to_update: list }`
1. **Embedding Node:** Uses LangChain `OpenAIEmbeddings` to generate a 768-dim contextual vector using the prior N messages as a prefix.
2. **Vector Store Node:** Updates the message row in Postgres.
3. **Trigger Assembly Node:** Identifies all active `context_windows` associated with the conversation and queues context assembly tasks for them if token thresholds are met.

### 4.3 Retrieval Flow (`conv_retrieve_context`)
**State Schema:** `{ window_id: str, build_type: str, tiers: dict, total_tokens: int }`
1. **Resolve Config Node:** Loads the `build_type` config specifying token allocation.
2. **Episodic Retrieval Node:** Queries PostgreSQL for Tier 1 (Archival), Tier 2 (Chunk), and Tier 3 (Verbatim) summaries.
3. **Semantic Retrieval Node:** If enabled, uses `langchain_postgres.PGVector` for similarity search.
4. **Knowledge Retrieval Node:** If enabled, queries Mem0/Neo4j.
5. **Assembly Node:** Formats the final context string, fitting components strictly within the allocated token budgets.

### 4.4 Memory Extraction Flow (Background Job)
**State Schema:** `{ message_id: str, content: str, facts_extracted: int }`
1. **Mem0 Ingestion Node:** Submits the new message batch to `mem0` configured to use the local Neo4j database and the configured LLM. It extracts entities, relationships, and preferences, updating the graph transparently.

### 4.5 Imperator Flow (Chat interface)
**State Schema:** `{ messages: list, user_id: str, context: str }`
1. **Context Prep Node:** Invokes `conv_retrieve_context` using the Imperator's own context window.
2. **System Prompt Node:** Binds identity, purpose, and the retrieved context into the `ChatOpenAI` system message.
3. **Agent Loop (ReAct):** Uses `ChatOpenAI.bind_tools()` with the internal MCP tools. Loops until a final response is generated.

---

## 5. Storage and Database Schema

### 5.1 PostgreSQL (Episodic Memory & Embeddings)
Uses the `pgvector` extension for similarity search, integrating with `langchain_postgres.PGVector`.

**Key Tables:**
- `conversations` (id, flow_id, title, created_at)
- `messages` (id, conversation_id, sender_id, role, content, sequence_num)
  - Column: `embedding vector(768)` with an HNSW index for fast ANN search.
- `context_windows` (id, conversation_id, participant_id, build_type, token_limit)
- `summaries` (id, context_window_id, tier, content, token_count)

### 5.2 Neo4j (Semantic Memory)
Leveraged via `Mem0` and `langchain_community.graphs.Neo4jGraph`.
- Models users, agents, and their extracted preferences/relationships as graph nodes and edges. Enables traversal queries like "What does user X prefer regarding architecture Y?".

### 5.3 Redis
- **Queue:** Backing store for the `arq` queue.
- **Cache/Locks:** Distributed locks for preventing race conditions during parallel context assembly.

---

## 6. Build Types and Retrieval Pipeline

A `build_type` defines exactly how a context window is assembled. The pipeline is fully dynamic based on `/config/config.yml`.

### 6.1 `standard-tiered` (Episodic Only)
Focuses entirely on chronological history with progressive summarization.
- **Tier 1 (8% budget):** Deep archival summary.
- **Tier 2 (20% budget):** Rolling chunk summaries.
- **Tier 3 (72% budget):** Recent verbatim messages.

### 6.2 `knowledge-enriched` (Full Pipeline)
Combines episodic layers with semantically retrieved messages and knowledge graph facts.
- **Tier 1 (5%) & Tier 2 (15%) & Tier 3 (50%)**
- **Semantic Retrieval (15%):** The retrieval StateGraph utilizes `langchain_postgres.PGVector.similarity_search` to find past messages in the conversation that are vectorially similar to the *current* conversation trajectory but have fallen out of Tier 3.
- **Knowledge Graph (15%):** The retrieval StateGraph uses Mem0's search APIs (backed by Neo4j) to extract structural facts and relationships pertinent to the entities identified in the recent context window.

---

## 7. Queue and Async Processing

**Decision:** Replace the custom Redis polling loop with **`arq` (Async Redis Queue)**.
- **Why:** `arq` is a standard, lightweight, high-performance job queue specifically designed for Python's `asyncio` and `aioredis`. It perfectly aligns with `asyncpg` and the async StateGraphs.
- **Capabilities:** It natively handles exponential backoff, job retries, and dead-letter queues.
- **Implementation:** The `context-broker-langgraph` container runs both the Quart web server and an `arq` worker process.
- **Eventual Consistency:** Embedding generation, context assembly, and Mem0 extraction are strictly processed as background `arq` jobs. The core `conv_store_message` endpoint is never blocked by LLM latency.

---

## 8. Dual Protocol Interface

The Nginx `context-broker` gateway container is the only outward-facing component, mapping two distinct API surfaces to the internal LangGraph container:

### 8.1 Direct MCP (HTTP/SSE)
**Decision:** Replace the intermediate Node.js gateway with direct Nginx proxying to Python.
- Python uses standard LangChain MCP server libraries (e.g., `mcp` FastMCP protocol).
- Exposes tools like `conv_store_message`, `conv_retrieve_context`, `mem_search`, `conv_search`.
- Operates on `/mcp` using Server-Sent Events (SSE).

### 8.2 OpenAI-Compatible Chat (`/v1/chat/completions`)
Allows any standard chat UI (e.g., Chatbot UI, typingmind) to talk directly to the Context Broker's Imperator.
- The route handler translates the incoming payload into a StateGraph invocation for the Imperator.
- Streams back standard OpenAI SSE tokens (`data: {"choices": ...}`).

---

## 9. Imperator Design

The Imperator is the Context Broker's built-in conversational agent. It demonstrates and consumes the service's own capabilities.

- **Persistent Identity:** It stores its current `conversation_id` in `/data/imperator_state.json`. Upon reboot, it resumes the exact same continuous context window.
- **Self-Consumption:** The Imperator is built using LangGraph's ReAct pattern. Its `tools` array is populated with the exact same MCP tools (`conv_search`, `mem_get_context`) exposed to external callers.
- **Configuration-Driven Scope:** If `admin_tools: true` is set in config, it gains additional read-only tools to execute arbitrary Postgres/Neo4j queries and read its own configuration files.
- **LLM Independence:** Like all flows, the Imperator retrieves its LLM credentials and endpoints from the central configuration, strictly utilizing `langchain_openai.ChatOpenAI`.