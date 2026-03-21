# Context Broker — High-Level Design

**Version:** 1.0
**Date:** 2026-03-20
**Status:** Draft
**Implements:** REQ-context-broker.md v1.0

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Container Architecture](#2-container-architecture)
3. [StateGraph Flow Designs](#3-stategraph-flow-designs)
4. [Database Schema](#4-database-schema)
5. [MCP Tool Interface](#5-mcp-tool-interface)
6. [OpenAI-Compatible Chat Interface](#6-openai-compatible-chat-interface)
7. [Configuration System](#7-configuration-system)
8. [Build Type and Retrieval Pipeline](#8-build-type-and-retrieval-pipeline)
9. [Queue and Async Processing](#9-queue-and-async-processing)
10. [Imperator Design](#10-imperator-design)

---

## 1. System Overview

### 1.1 What the Context Broker Is

The Context Broker is a self-contained context engineering and conversational memory service. It solves the fundamental problem that LLM agents reason within finite context windows but participate in conversations that accumulate without limit. The Context Broker bridges this gap: it stores the infinite conversation, assembles purpose-built context views for each participant, extracts knowledge into a persistent graph, and makes the assembled context available instantly when an agent needs it.

The system implements the domain model defined in the companion concept paper (c1):

- **Infinite conversation** — every message stored completely, indefinitely, with no session boundaries
- **Context windows as curated views** — not truncated history but purpose-built assemblies tailored to a specific participant, build type, and token budget
- **Build types** — named strategies that define how conversation history is processed, filtered, and assembled (e.g., three-tier progressive compression, knowledge-enriched retrieval)
- **Two memory layers** — episodic (the conversation record, progressively compressed) and semantic (extracted knowledge in a graph, queryable by meaning and relationship)
- **Per-participant windows** — different participants in the same conversation can receive different context because their reasoning needs differ
- **Proactive assembly** — context is assembled in the background after each message, not on demand; when an agent asks for context, it is already waiting

### 1.2 Architectural Identity

The Context Broker is a State 4 MAD (Multipurpose Agentic Duo). This means:

- **AE/TE separation** — infrastructure (Action Engine: gateway, database ops, queue processing, connection management) is separated from intelligence (Thought Engine: the Imperator conversational agent and its cognitive apparatus). The AE is stable. The TE evolves independently.
- **All logic in StateGraphs** — every programmatic and cognitive operation is implemented as a LangGraph StateGraph. Route handlers are transport only. The graphs are the application.
- **Configurable dependencies** — inference providers, package sources, storage paths, and network topology are all configuration choices in `config.yml`. The same code runs standalone on any Docker host or inside a larger ecosystem.

### 1.3 What It Exposes

Two interfaces through a single gateway:

- **MCP (HTTP/SSE)** — programmatic tool access for external agents and applications (`conv_*`, `mem_*`, `broker_chat`, `metrics_get`)
- **OpenAI-compatible HTTP** (`/v1/chat/completions`) — conversational access to the Imperator; any OpenAI-compatible client or chat UI can connect

Both interfaces are backed by the same internal StateGraph flows.

---

## 2. Container Architecture

### 2.1 Container Inventory

| Container | Role | Image | Custom? |
|---|---|---|---|
| `context-broker` | Nginx gateway — MCP endpoint, `/v1/chat/completions`, `/health`, `/metrics` | `nginx:1.27-alpine` (pinned) | Config only |
| `context-broker-langgraph` | All application logic — StateGraph flows, queue worker, Imperator | Custom (Python 3.12-slim, pinned) | Yes |
| `context-broker-postgres` | Conversation storage, vector embeddings (pgvector) | `pgvector/pgvector:pg16` (pinned) | Schema init only |
| `context-broker-neo4j` | Knowledge graph storage (Mem0 graph store) | `neo4j:5` (pinned) | No |
| `context-broker-redis` | Job queues, assembly locks, ephemeral state | `redis:7-alpine` (pinned) | No |

Only the LangGraph container contains custom application code. All backing services use official images with configuration and schema initialization only.

### 2.2 Network Topology

```
                    ┌─────────────────────────────────────────────┐
                    │           Host Network                       │
                    │                                              │
                    │   Port 8080 ──┐                              │
                    │               ▼                              │
                    │   ┌───────────────────┐                     │
                    │   │  context-broker    │ ◄── nginx gateway   │
                    │   │  (default +        │                     │
                    │   │   context-broker-  │                     │
                    │   │   net)             │                     │
                    │   └────────┬──────────┘                     │
                    └────────────┼──────────────────────────────────┘
                                 │
                    ┌────────────┼──────────────────────────────────┐
                    │            │  context-broker-net (bridge)     │
                    │            ▼                                  │
                    │   ┌──────────────────┐                       │
                    │   │ context-broker-   │                       │
                    │   │ langgraph:8000    │ ◄── all application   │
                    │   └──────┬───────────┘     logic             │
                    │          │                                    │
                    │    ┌─────┴─────┬──────────────┐              │
                    │    ▼           ▼              ▼              │
                    │ ┌──────┐  ┌──────┐    ┌──────────┐          │
                    │ │pg:5432│  │redis │    │neo4j:7687│          │
                    │ │      │  │:6379 │    │          │          │
                    │ └──────┘  └──────┘    └──────────┘          │
                    └──────────────────────────────────────────────┘
```

Two Docker networks per REQ §7.3:

- **`default`** — host-exposed. Only the nginx gateway connects here and exposes the external port.
- **`context-broker-net`** — private bridge. All five containers connect here. All inter-container communication uses Docker Compose service names.

The gateway is the sole boundary between external traffic and internal services. The LangGraph container and all backing services are on the internal network only.

### 2.3 Volume Mounts

Per REQ §3.1, two mount points:

| Mount | Purpose | Host Default | Container Path |
|---|---|---|---|
| Config | User-edited config and credentials | `./config` | `/config` |
| Data | System-generated state (databases, Imperator state) | `./data` | `/data` |

```
Host ./config/                    Host ./data/
├── config.yml                    ├── postgres/
└── credentials/                  ├── neo4j/
    └── .env                      ├── redis/
                                  └── imperator_state.json
```

Each backing service container mounts its data subdirectory to its declared `VOLUME` path:

| Container | Host Path | Container Path |
|---|---|---|
| `context-broker-postgres` | `./data/postgres` | `/var/lib/postgresql/data` |
| `context-broker-neo4j` | `./data/neo4j` | `/data` |
| `context-broker-redis` | `./data/redis` | `/data` |
| `context-broker-langgraph` | `./data` | `/data` (reads `imperator_state.json`) |

### 2.4 Docker Compose Structure

```yaml
services:
  context-broker:
    image: nginx:1.27-alpine  # pinned digest
    container_name: context-broker
    networks:
      - default
      - context-broker-net
    ports:
      - "${GATEWAY_PORT:-8080}:8080"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:8080/health"]
      interval: 30s
      timeout: 3s
      start_period: 10s
      retries: 3

  context-broker-langgraph:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: context-broker-langgraph
    user: "10000:10000"
    networks:
      - context-broker-net
    volumes:
      - ./config:/config:ro
      - ./data:/data
    env_file:
      - ./config/credentials/.env
    environment:
      - POSTGRES_HOST=context-broker-postgres
      - POSTGRES_PORT=5432
      - POSTGRES_DB=contextbroker
      - POSTGRES_USER=contextbroker
      - REDIS_HOST=context-broker-redis
      - REDIS_PORT=6379
      - NEO4J_HOST=context-broker-neo4j
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 30s
      timeout: 3s
      start_period: 60s
      retries: 3

  context-broker-postgres:
    image: pgvector/pgvector:pg16  # pinned digest
    container_name: context-broker-postgres
    networks:
      - context-broker-net
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
    environment:
      - POSTGRES_DB=contextbroker
      - POSTGRES_USER=contextbroker
      - POSTGRES_PASSWORD_FILE=/run/secrets/pg_password
    secrets:
      - pg_password
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "contextbroker"]
      interval: 30s
      timeout: 3s
      start_period: 30s
      retries: 3

  context-broker-neo4j:
    image: neo4j:5  # pinned digest
    container_name: context-broker-neo4j
    networks:
      - context-broker-net
    volumes:
      - ./data/neo4j:/data
    env_file:
      - ./config/credentials/.env
    environment:
      - NEO4J_PLUGINS=["apoc"]
      - NEO4J_server_memory_heap_initial__size=256m
      - NEO4J_server_memory_heap_max__size=512m
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:7474/"]
      interval: 30s
      timeout: 3s
      start_period: 60s
      retries: 3

  context-broker-redis:
    image: redis:7-alpine  # pinned digest
    container_name: context-broker-redis
    networks:
      - context-broker-net
    volumes:
      - ./data/redis:/data
    command: redis-server --appendonly yes --dir /data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 3s
      start_period: 10s
      retries: 3

networks:
  context-broker-net:
    driver: bridge

secrets:
  pg_password:
    file: ./config/credentials/pg_password.txt
```

### 2.5 Nginx Gateway Configuration

The gateway is a pure routing layer with no application logic (REQ §4.7). It routes three paths:

```nginx
upstream langgraph {
    server context-broker-langgraph:8000;
}

server {
    listen 8080;

    # MCP endpoint (HTTP/SSE)
    location /mcp {
        proxy_pass http://langgraph;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header X-Request-ID $request_id;
        proxy_read_timeout 120s;
        # SSE support
        proxy_buffering off;
        proxy_cache off;
    }

    # OpenAI-compatible chat
    location /v1/chat/completions {
        proxy_pass http://langgraph;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header X-Request-ID $request_id;
        proxy_read_timeout 300s;
        proxy_buffering off;
    }

    # Health check
    location /health {
        proxy_pass http://langgraph;
        proxy_read_timeout 10s;
    }

    # Prometheus metrics
    location /metrics {
        proxy_pass http://langgraph;
        proxy_read_timeout 10s;
    }
}
```

---

## 3. StateGraph Flow Designs

All application logic is implemented as LangGraph StateGraphs. The HTTP server compiles graphs once at startup and invokes them per-request. No logic in route handlers.

### 3.1 Flow Inventory

| Flow | Trigger | Purpose |
|---|---|---|
| Message Pipeline | `conv_store_message` MCP call | Dedup, store, queue async jobs |
| Embed Pipeline | Queue worker (embedding_jobs) | Generate contextual embedding, trigger assembly + extraction |
| Context Assembly | Queue worker (context_assembly_jobs) | Three-tier progressive compression |
| Retrieval | `conv_retrieve_context` MCP call | Assemble and return context window |
| Memory Extraction | Queue worker (memory_extraction_jobs) | Extract knowledge to Neo4j via Mem0 |
| Search | `conv_search_messages` MCP call | Hybrid vector + BM25 + reranker search |
| Imperator | `broker_chat` / `/v1/chat/completions` | Conversational agent with tool use |
| Metrics | `metrics_get` / `/metrics` | Prometheus metrics collection |

### 3.2 Message Pipeline Flow

**Purpose:** Synchronous message ingestion. Stores the message and queues async enrichment. Returns immediately — the caller never waits for embedding, assembly, or extraction.

**State Schema:**

```python
class MessagePipelineState(TypedDict):
    # Inputs
    conversation_id: str
    context_window_id: Optional[str]   # Alternative to conversation_id
    role: str                           # 'user', 'assistant', 'system', 'tool'
    sender_id: str                      # Participant identifier
    content: str
    token_count: Optional[int]
    model_name: Optional[str]
    content_type: Optional[str]         # 'conversation' (default), 'tool_output', etc.
    priority: int                       # 0=highest .. 3=lowest (queue priority)

    # Outputs
    message_id: Optional[str]
    sequence_number: Optional[int]
    deduplicated: bool
    queued_jobs: list[str]
    error: Optional[str]
```

**Graph:**

```
[resolve_conversation] ──error──> END
         │
         ok
         ▼
    [dedup_check] ──deduplicated──> END
         │
         new message
         ▼
    [store_message]
         │
         ▼
    [queue_embed] ──> END
```

**Node Details:**

| Node | Responsibility | LangChain/LangGraph Component |
|---|---|---|
| `resolve_conversation` | If `context_window_id` provided instead of `conversation_id`, look up the associated conversation. Validate existence. | Direct asyncpg query (simple FK lookup, no retriever needed) |
| `dedup_check` | Compare against last message from same sender. If consecutive duplicate, update repeat count and short-circuit. | Direct asyncpg query |
| `store_message` | INSERT into `conversation_messages`, update conversation counters (`total_messages`, `estimated_token_count`). Token count estimated as `len(content) // 4` if not provided. | Direct asyncpg query (transactional write) |
| `queue_embed` | Push embedding job to Redis `embedding_jobs` list with message_id, conversation_id, priority. | Direct Redis LPUSH |

**Design Decision — asyncpg vs LangChain stores:** The message pipeline performs transactional writes (INSERT + UPDATE counters in a single transaction) and deduplication logic that has no LangChain equivalent. Direct `asyncpg` is appropriate here. LangChain retrievers and stores are used in the search and retrieval flows where their abstractions add value.

### 3.3 Embed Pipeline Flow

**Purpose:** Generate a contextual embedding for a single message, then fan out to context assembly and memory extraction queues.

**State Schema:**

```python
class EmbedPipelineState(TypedDict):
    # Inputs
    message_id: str
    conversation_id: str

    # Intermediate
    message: Optional[dict]
    embedding: Optional[list[float]]

    # Outputs
    assembly_jobs_queued: list[str]
    extraction_queued: bool
    error: Optional[str]
```

**Graph:**

```
[fetch_message] ──error──> END
       │
       ok
       ▼
[build_embed_text]
       │
       ▼
[generate_embedding]
       │
       ▼
[store_embedding]
       │
       ▼
[check_context_assembly]
       │
       ▼
[queue_memory_extraction] ──> END
```

**Node Details:**

| Node | Responsibility | LangChain/LangGraph Component |
|---|---|---|
| `fetch_message` | Load message row from PostgreSQL by UUID | Direct asyncpg query |
| `build_embed_text` | Build contextual embedding text: fetch N prior messages (configurable `embedding.context_window_size`, default 3), prepend as `[Context]` block before `[Current]` message content. This contextual prefix gives semantic meaning to short messages. | Direct asyncpg query for prior messages |
| `generate_embedding` | Call the configured embeddings provider to produce a vector | **`langchain.embeddings.OpenAIEmbeddings`** (or provider-appropriate class) instantiated from `config.yml` `embeddings` section. Uses `embed_query()` method. |
| `store_embedding` | Write embedding vector to `conversation_messages.embedding` column | Direct asyncpg UPDATE (pgvector column) |
| `check_context_assembly` | For each context window on this conversation: check if token threshold crossed AND assembly not already in progress AND message is newer than last assembly. If so, queue a `context_assembly_jobs` entry. | Direct asyncpg queries + Redis checks |
| `queue_memory_extraction` | If `content_type='conversation'` and not already extracted, add to `memory_extraction_jobs` ZSET with priority-based score | Direct Redis ZADD |

**Design Decision — LangChain embeddings:** The current code calls Sutherland's MCP tool for embeddings. The State 4 design uses LangChain's `OpenAIEmbeddings` (or equivalent) configured with the `base_url`, `model`, and `api_key_env` from `config.yml`. This works with any OpenAI-compatible embedding endpoint. The embedding model is instantiated fresh from config on each invocation (config changes take effect immediately without restart).

```python
from langchain_openai import OpenAIEmbeddings

def get_embeddings_model(config: dict) -> OpenAIEmbeddings:
    """Instantiate embedding model from config.yml. Called per-operation."""
    cfg = config["embeddings"]
    return OpenAIEmbeddings(
        openai_api_base=cfg["base_url"],
        model=cfg["model"],
        openai_api_key=os.environ[cfg["api_key_env"]],
    )
```

### 3.4 Context Assembly Flow

**Purpose:** Build the three-tier progressive compression for a context window. Triggered asynchronously by the queue worker after embedding detects a threshold crossing.

**State Schema:**

```python
class ContextAssemblyState(TypedDict):
    # Inputs (from job payload)
    conversation_id: str
    context_window_id: str
    build_type_id: str

    # Intermediate
    window: Optional[dict]
    build_type_config: Optional[dict]      # Tier percentages from config.yml
    messages: list[dict]
    max_token_limit: int
    tier3_budget: int
    tier3_start_seq: int
    older_messages: list[dict]             # Messages needing summarization
    chunks: list[list[dict]]              # 20-message chunks
    llm: Optional[object]                  # LangChain ChatModel instance
    model_name: str
    tier2_summaries_written: int
    tier1_consolidated: bool
    assembly_key: str                      # Redis lock key
    error: Optional[str]
```

**Graph:**

```
[set_assembly_flag] ──already running──> END
         │
         acquired
         ▼
[load_window_config] ──error──> [clear_assembly_flag] ──> END
         │
         ok
         ▼
[load_messages] ──no messages──> [finalize_assembly]
    │    │                              │
    │   error──> [clear_assembly_flag]  │
    │                                   │
    ok                                  │
    ▼                                   │
[calculate_tiers] ──all in tier3──> [finalize_assembly]
    │                                   │
    has older                           │
    ▼                                   │
[select_llm]                            │
    │                                   │
    ▼                                   │
[summarize_chunks]                      │
    │                                   │
    ▼                                   │
[consolidate_tier1]                     │
    │                                   │
    ▼                                   │
[finalize_assembly] <───────────────────┘
    │
    ▼
[clear_assembly_flag] ──> END
```

**Node Details:**

| Node | Responsibility | LangChain Component |
|---|---|---|
| `set_assembly_flag` | Redis SET NX with 120s TTL. Prevents concurrent assembly for same window. | Direct Redis |
| `load_window_config` | Load window row from PostgreSQL, load build type config from `config.yml` (not database — build types are now config-driven per REQ §5.3). | Direct asyncpg + config read |
| `load_messages` | Fetch all messages for the conversation | Direct asyncpg |
| `calculate_tiers` | Calculate tier 3 token budget from build type percentages. Walk backwards from newest message to find tier 3 boundary. Identify messages needing summarization. Check existing tier 2 summaries for incremental processing (only summarize messages newer than the last covered sequence). Chunk older messages into groups of 20. | Pure computation |
| `select_llm` | Instantiate the LLM from `config.yml` `llm` section | **`langchain_openai.ChatOpenAI`** configured with `base_url`, `model`, `api_key` from config |
| `summarize_chunks` | For each chunk: format messages as text, call LLM with summarization prompt, store resulting tier 2 summary in `conversation_summaries`. | `ChatOpenAI.ainvoke()` with summarization prompt |
| `consolidate_tier1` | If >3 active tier 2 summaries: consolidate oldest into a single tier 1 archival summary. Deactivate consolidated tier 2 rows. Deactivate old tier 1. Insert new tier 1. | `ChatOpenAI.ainvoke()` with consolidation prompt |
| `finalize_assembly` | Update `context_windows.last_assembled_at` timestamp | Direct asyncpg |
| `clear_assembly_flag` | Delete Redis assembly lock. Always reached on both success and error paths. | Direct Redis |

**Design Decision — LangChain ChatModel for summarization:** The current code uses hand-rolled Mem0 LLM adapters (Sutherland peer proxy, OpenAI-compatible). The State 4 design uses standard `langchain_openai.ChatOpenAI` configured from `config.yml`. This supports any OpenAI-compatible provider transparently:

```python
from langchain_openai import ChatOpenAI

def get_chat_model(config: dict) -> ChatOpenAI:
    """Instantiate chat model from config.yml. Called per-operation."""
    cfg = config["llm"]
    return ChatOpenAI(
        base_url=cfg["base_url"],
        model=cfg["model"],
        api_key=os.environ[cfg["api_key_env"]],
        temperature=0.1,
    )
```

**Tier Proportion Calculation:**

Build type config in `config.yml` provides explicit percentages:

```yaml
# standard-tiered
tier1_pct: 0.08    # archival summary
tier2_pct: 0.20    # chunk summaries
tier3_pct: 0.72    # recent verbatim
```

The tier 3 budget is `max_token_limit * tier3_pct`. The calculate_tiers node walks backwards from the newest message, accumulating token counts, until the tier 3 budget is exhausted. Everything before the tier 3 boundary needs summarization.

### 3.5 Retrieval Flow

**Purpose:** Assemble and return the context window for a participant. Called synchronously by `conv_retrieve_context`.

**State Schema:**

```python
class RetrievalState(TypedDict):
    # Input
    context_window_id: str

    # Intermediate
    conversation_id: Optional[str]
    build_type_config: Optional[dict]
    window: Optional[dict]
    max_token_limit: int
    tier1_summary: Optional[str]
    tier2_summaries: list[str]
    recent_messages: list[dict]
    semantic_results: list[dict]        # Vector similarity results (knowledge-enriched)
    knowledge_graph_results: list[str]  # Graph traversal results (knowledge-enriched)

    # Output
    context: Optional[str]
    tiers: Optional[dict]
    total_tokens: int
    assembly_status: str                # 'ready', 'blocked_waiting', 'error'
    error: Optional[str]
```

**Graph (standard-tiered):**

```
[get_window] ──error──> END
      │
      ok
      ▼
[check_assembly] ──(wait up to 50s if assembly in progress)
      │
      ▼
[get_summaries]
      │
      ▼
[get_recent_messages]
      │
      ▼
[assemble_context] ──> END
```

**Graph (knowledge-enriched) — adds two parallel retrieval branches:**

```
[get_window] ──error──> END
      │
      ok
      ▼
[check_assembly]
      │
      ▼
[get_summaries]
      │
      ├──────────────────────────────────────┐
      ▼                                      ▼
[get_recent_messages]          [retrieve_semantic + retrieve_knowledge_graph]
      │                                      │
      └──────────────┬───────────────────────┘
                     ▼
            [assemble_context] ──> END
```

**Node Details:**

| Node | Responsibility | LangChain Component |
|---|---|---|
| `get_window` | Fetch context window and build type config | Direct asyncpg + config read |
| `check_assembly` | Poll Redis for `assembly_in_progress:{window_id}` flag. Wait up to 50s (2s poll interval) if assembly is running. Return `blocked_waiting` on timeout. | Direct Redis polling |
| `get_summaries` | Fetch active tier 1 and tier 2 summaries from `conversation_summaries` | Direct asyncpg |
| `get_recent_messages` | Fill remaining tier 3 budget with recent verbatim messages (walk backwards from newest until budget exhausted) | Direct asyncpg |
| `retrieve_semantic` | (knowledge-enriched only) Vector similarity search for messages relevant to recent conversation topic but outside the tier 3 window. Budget: `semantic_retrieval_pct * max_token_limit` tokens. | **`langchain_postgres.PGVector`** as retriever with `similarity_search_with_score()` |
| `retrieve_knowledge_graph` | (knowledge-enriched only) Mem0 graph traversal for extracted facts and entity relationships relevant to the conversation. Budget: `knowledge_graph_pct * max_token_limit` tokens. | **Mem0 `search()`** with graph store |
| `assemble_context` | Format all tiers with XML markers and return assembled context string | Pure formatting |

**Assembly Output Format:**

```xml
<archival_summary>
[Tier 1: archival summary covering full history]
</archival_summary>

<chunk_summaries>
[Tier 2: chunk summaries of intermediate-age content]
</chunk_summaries>

<semantic_retrieval>
[Knowledge-enriched only: vectorially relevant messages from outside the recent window]
</semantic_retrieval>

<knowledge_graph>
[Knowledge-enriched only: extracted facts and entity relationships]
</knowledge_graph>

<recent_messages>
[Tier 3: recent verbatim messages, newest content]
</recent_messages>
```

**Design Decision — LangChain PGVector retriever for semantic search:** The current code uses raw SQL with pgvector's `<=>` cosine distance operator. The State 4 design uses `langchain_postgres.PGVector` as a retriever for the semantic retrieval path in `knowledge-enriched` builds. This provides standard LangChain retriever semantics (`.as_retriever()`, similarity search with metadata filtering) while still backed by the same pgvector indexes. Direct asyncpg remains for the core CRUD operations (message storage, summary management) where transactional control is needed.

### 3.6 Memory Extraction Flow

**Purpose:** Extract knowledge from unprocessed conversation messages into the Mem0 knowledge graph (Neo4j + pgvector).

**State Schema:**

```python
class MemoryExtractionState(TypedDict):
    # Input
    conversation_id: str

    # Intermediate
    messages: list[dict]
    user_id: str
    conversation_text: str
    selected_message_ids: list
    extraction_lock_key: str

    # Output
    extracted_count: int
    error: Optional[str]
```

**Graph:**

```
[fetch_unextracted] ──no messages──> END
         │
         has messages
         ▼
[build_extraction_text]
         │
         ▼
[redact_secrets]
         │
         ▼
[acquire_extraction_lock] ──not acquired──> END
         │
         acquired
         ▼
[run_mem0_extraction]
         │
         ▼
[mark_extracted]
         │
         ▼
[release_extraction_lock] ──> END
```

**Node Details:**

| Node | Responsibility | LangChain Component |
|---|---|---|
| `fetch_unextracted` | Query messages where `content_type='conversation'` AND `memory_extracted=FALSE` | Direct asyncpg |
| `build_extraction_text` | Build chronological text from messages (newest-first within character budget). Character budget configurable. | Pure computation |
| `redact_secrets` | Scan text for credentials, API keys, tokens using `detect-secrets` + custom detectors (connection strings, bearer tokens). Replace with `[REDACTED]`. | `detect-secrets` library |
| `acquire_extraction_lock` | Redis SET NX with 120s TTL per conversation | Direct Redis |
| `run_mem0_extraction` | Call `mem0.add(conversation_text, user_id=..., run_id=conversation_id)`. Mem0 extracts facts, entities, relationships and stores them in Neo4j graph + pgvector. | **Mem0 `Memory.add()`** configured with LangChain LLM and embeddings from config |
| `mark_extracted` | SET `memory_extracted=TRUE` on all selected message IDs | Direct asyncpg |
| `release_extraction_lock` | Delete Redis lock | Direct Redis |

**Mem0 Configuration for State 4:**

Mem0 is initialized with the configurable providers from `config.yml`. The LLM and embedder are injected after Mem0 construction:

```python
from mem0 import Memory
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

def build_mem0_instance(config: dict) -> Memory:
    mem_config = MemoryConfig(
        version="v1.1",
        vector_store=VectorStoreConfig(
            provider="pgvector",
            config={
                "host": os.environ["POSTGRES_HOST"],
                "port": int(os.environ["POSTGRES_PORT"]),
                "dbname": os.environ["POSTGRES_DB"],
                "user": os.environ["POSTGRES_USER"],
                "password": os.environ["POSTGRES_PASSWORD"],
                "collection_name": "mem0_memories",
                "embedding_model_dims": embedding_dims,
            },
        ),
        graph_store=GraphStoreConfig(
            provider="neo4j",
            config={
                "url": f"bolt://{os.environ['NEO4J_HOST']}:7687",
                "username": "neo4j",
                "password": os.environ["NEO4J_PASSWORD"],
            },
        ),
    )
    mem = Memory(config=mem_config)
    # Inject configurable LLM and embedder
    mem.llm = build_mem0_llm_adapter(config)
    mem.embedding_model = build_mem0_embed_adapter(config)
    if mem.graph:
        mem.graph.llm = mem.llm
        mem.graph.embedding_model = mem.embedding_model
    return mem
```

The Mem0 LLM and embedder adapters wrap LangChain models to satisfy Mem0's adapter interface (`generate_response()` and `embed()` methods). This is necessary because Mem0 does not natively accept LangChain model instances — it requires adapters that match its internal API contract.

### 3.7 Search Flow

**Purpose:** Hybrid vector + BM25 search with optional cross-encoder reranking. Powers `conv_search_messages`.

**State Schema:**

```python
class SearchState(TypedDict):
    # Inputs
    query: Optional[str]
    conversation_id: Optional[str]
    sender_id: Optional[str]
    role: Optional[str]
    date_from: Optional[str]
    date_to: Optional[str]
    limit: int
    offset: int

    # Intermediate
    query_embedding: Optional[list[float]]
    stage1_results: list[dict]

    # Output
    results: list[dict]
    error: Optional[str]
```

**Graph:**

```
[embed_query] ──(skip if no query text)
      │
      ▼
[hybrid_retrieval]     (vector ANN + BM25 RRF, top 50)
      │
      ▼
[rerank] ──(skip if reranker.provider=none)──> [format_results] ──> END
      │
      ▼
[format_results] ──> END
```

**Node Details:**

| Node | Responsibility | LangChain Component |
|---|---|---|
| `embed_query` | Embed the search query using the configured embeddings provider | `OpenAIEmbeddings.embed_query()` |
| `hybrid_retrieval` | Execute hybrid search: vector ANN (cosine similarity via pgvector ivfflat index) + BM25 full-text (PostgreSQL `tsvector`/`ts_rank`), combined via Reciprocal Rank Fusion (RRF) with mild recency bias (max 20% penalty at 90 days). Top 50 candidates. | Direct asyncpg (hybrid SQL with CTEs — the RRF+recency scoring is custom SQL not available in any LangChain retriever) |
| `rerank` | Cross-encoder reranking of stage 1 results. Configurable: local cross-encoder model (default, CPU, no API key) or Cohere API or disabled. Top 10 final results. | **`langchain.retrievers.document_compressors.CrossEncoderReranker`** or **`langchain_cohere.CohereRerank`** per config |
| `format_results` | Serialize results, strip embeddings, convert timestamps | Pure formatting |

**Design Decision — hybrid search as raw SQL:** The RRF combination of vector ANN + BM25 with recency bias is a custom scoring formula not available in any standard LangChain retriever. The SQL uses CTEs for each retrieval path, FULL OUTER JOINs them, and applies `1/(60+rank)` RRF scoring with a time-decay multiplier. This is kept as direct asyncpg because abstracting it behind a LangChain retriever would lose the recency bias and multi-signal fusion. The cross-encoder reranker, however, uses the standard LangChain reranker interface.

### 3.8 Imperator Flow

Described in detail in [Section 10: Imperator Design](#10-imperator-design).

### 3.9 Metrics Flow

**Purpose:** Collect and return Prometheus metrics.

**State Schema:**

```python
class MetricsState(TypedDict):
    action: str
    metrics_data: str
```

**Graph:**

```
[collect_metrics] ──> END
```

Single node that calls `prometheus_client.generate_latest(REGISTRY)` and returns the text. Exposed both as `GET /metrics` (Prometheus scrape) and as the `metrics_get` MCP tool.

---

## 4. Database Schema

### 4.1 PostgreSQL Schema

#### conversations

The top-level conversation entity. Participants are derived from messages, not stored here.

```sql
CREATE TABLE conversations (
    id            VARCHAR(255) PRIMARY KEY,
    user_id       VARCHAR(255),           -- Owner/initiator
    title         VARCHAR(500),
    total_messages     INTEGER DEFAULT 0,
    estimated_token_count INTEGER DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_conversations_user ON conversations(user_id, updated_at DESC);
```

**Change from current:** Removed `flow_id` and `flow_name` (ecosystem-specific). Added `TIMESTAMPTZ` instead of `TIMESTAMP` for timezone awareness.

#### conversation_messages

One row per message, any number of participants, any role.

```sql
CREATE TABLE conversation_messages (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id     VARCHAR(255) NOT NULL REFERENCES conversations(id),
    role                VARCHAR(50) NOT NULL,          -- 'user', 'assistant', 'system', 'tool'
    sender_id           VARCHAR(255) NOT NULL,         -- Participant identifier (string, not ecosystem UID)
    content             TEXT NOT NULL,
    token_count         INTEGER,
    model_name          VARCHAR(100),
    embedding           vector(768),                   -- Configurable dimensions (matches embeddings.model)
    sequence_number     INTEGER NOT NULL,
    content_type        VARCHAR(50) DEFAULT 'conversation',
    priority            SMALLINT DEFAULT 3,
    memory_extracted    BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    content_tsv         tsvector GENERATED ALWAYS AS (
                            to_tsvector('english', coalesce(content, ''))
                        ) STORED
);

CREATE INDEX idx_messages_conversation ON conversation_messages(conversation_id);
CREATE INDEX idx_messages_conversation_seq ON conversation_messages(conversation_id, sequence_number);
CREATE INDEX idx_messages_conversation_sender ON conversation_messages(conversation_id, sender_id, sequence_number DESC);
CREATE INDEX idx_messages_created ON conversation_messages(created_at);
CREATE INDEX idx_messages_emb ON conversation_messages
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_messages_tsv ON conversation_messages USING GIN(content_tsv);
```

**Change from current:** `sender_id` is now `VARCHAR(255)` (not `INTEGER`) — removes ecosystem UID dependency. Callers provide their own participant identifiers.

**Note on embedding dimensions:** The schema uses `vector(768)` as the default. If the configured embedding model produces a different dimensionality, the init script should be parameterized or a migration applied. The dimension is recorded in `config.yml` and validated at startup.

#### context_windows

Per-participant instances linking a conversation to a build type and token budget.

```sql
CREATE TABLE context_windows (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id   VARCHAR(255) NOT NULL REFERENCES conversations(id),
    build_type_id     VARCHAR(100) NOT NULL,     -- References config.yml build_types key
    max_token_limit   INTEGER NOT NULL,
    last_assembled_at TIMESTAMPTZ,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_windows_conversation ON context_windows(conversation_id);
```

**Change from current:** `build_type_id` no longer references a database table — build types are defined in `config.yml` and validated at context window creation time.

#### conversation_summaries

Tiered summaries keyed to context windows.

```sql
CREATE TABLE conversation_summaries (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id     VARCHAR(255) NOT NULL REFERENCES conversations(id),
    context_window_id   UUID NOT NULL REFERENCES context_windows(id),
    summary_text        TEXT NOT NULL,
    summary_embedding   vector(768),
    tier                INTEGER NOT NULL,          -- 1 = archival, 2 = chunk
    summarizes_from_seq INTEGER,
    summarizes_to_seq   INTEGER,
    message_count       INTEGER,
    original_token_count INTEGER,
    summary_token_count  INTEGER,
    summarized_by_model  VARCHAR(100),
    summarized_at       TIMESTAMPTZ DEFAULT NOW(),
    is_active           BOOLEAN DEFAULT TRUE
);

CREATE INDEX idx_summaries_window ON conversation_summaries(context_window_id, is_active);
CREATE INDEX idx_summaries_emb ON conversation_summaries
    USING ivfflat (summary_embedding vector_cosine_ops) WITH (lists = 100);
```

#### schema_version

Forward-only migration tracking (REQ §3.7).

```sql
CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMPTZ DEFAULT NOW(),
    description TEXT
);

INSERT INTO schema_version (version, description) VALUES (1, 'Initial schema');
```

On startup, the application checks `MAX(version)` and applies any pending migrations before accepting requests.

#### Mem0 tables

Mem0 creates its own tables on first initialization:

- `mem0_memories` — vector store for extracted facts (hash-based dedup via unique index)
- Various Neo4j-managed graph tables via the graph store

The dedup index is applied in the init script:

```sql
-- Applied after Mem0 first creates mem0_memories table
-- (or as a migration if table does not yet exist at init time)
CREATE UNIQUE INDEX IF NOT EXISTS idx_mem0_hash_user
    ON mem0_memories ((payload->>'hash'), (payload->>'user_id'));
```

### 4.2 Neo4j Data Model

Neo4j is used exclusively by Mem0 as the graph store for extracted knowledge. The schema is managed by Mem0:

**Nodes:**
- Entity nodes with `name`, `type`, and metadata properties
- Created/updated by Mem0's graph memory operations

**Relationships:**
- Named relationship edges between entities (e.g., `WORKS_ON`, `PREFERS`, `DECIDED`)
- Carry `relationship` description, `source`, `target`, timestamps

**Indexes:**
- Mem0 creates necessary indexes on initialization
- APOC plugin enabled for graph traversal operations

The Context Broker does not directly query Neo4j. All knowledge graph access goes through Mem0's API (`search()`, `add()`, `get_all()`).

### 4.3 Redis Usage

Redis serves three purposes with no persistent application data — all authoritative state is in PostgreSQL/Neo4j:

| Key Pattern | Type | Purpose | TTL |
|---|---|---|---|
| `embedding_jobs` | LIST | Embedding job queue | None (consumed) |
| `context_assembly_jobs` | LIST | Assembly job queue | None (consumed) |
| `memory_extraction_jobs` | ZSET | Extraction job queue (priority-scored) | None (consumed) |
| `dead_letter_jobs` | LIST | Failed jobs awaiting retry sweep | None (swept) |
| `assembly_in_progress:{window_id}` | STRING | Assembly lock (prevents concurrent assembly) | 120s |
| `extraction_in_progress:{conv_id}` | STRING | Extraction lock (prevents concurrent extraction) | 120s |

Redis persistence is enabled (`appendonly yes`) to survive restarts, but job loss is tolerable — the system is eventually consistent and jobs will be re-triggered on the next message.

---

## 5. MCP Tool Interface

### 5.1 Transport

MCP uses HTTP/SSE transport per REQ §4.1. The LangGraph container implements the MCP server directly using `langchain-mcp-adapters` or the `mcp` Python package:

- `GET /mcp` — Establishes SSE session
- `POST /mcp?sessionId=xxx` — Routes message to existing session
- `POST /mcp` (no sessionId) — Sessionless mode for simple tool calls

The nginx gateway proxies `/mcp` to the LangGraph container with SSE-compatible settings (no buffering, long read timeout).

### 5.2 Tool Inventory

| Tool | Description | Input Schema (key fields) | Output |
|---|---|---|---|
| `conv_create_conversation` | Create a new conversation | `title`, `user_id` (optional) | `{conversation_id, created_at}` |
| `conv_store_message` | Store a message; triggers async pipeline | `conversation_id` or `context_window_id`, `role`, `sender_id`, `content`, `priority` (0-3) | `{message_id, sequence_number, deduplicated, queued_jobs}` |
| `conv_retrieve_context` | Get assembled context window | `context_window_id` | `{context, tiers, total_tokens, assembly_status}` |
| `conv_create_context_window` | Create context window instance | `conversation_id`, `build_type_id`, `max_token_limit` (optional, defaults to build type config) | `{id, conversation_id, build_type_id, max_token_limit}` |
| `conv_search` | Semantic + structured conversation search | `query` (optional), `user_id`, `date_from`, `date_to`, `limit` | `{conversations: [...]}` |
| `conv_search_messages` | Hybrid search across messages | `query` (optional), `conversation_id`, `sender_id`, `role`, `date_from`, `date_to`, `limit` | `{messages: [...]}` |
| `conv_get_history` | Full chronological message history | `conversation_id` | `{conversation: {...}, messages: [...]}` |
| `conv_search_context_windows` | List/search context windows | `context_window_id`, `conversation_id`, `build_type_id` (all optional) | `{context_windows: [...]}` |
| `mem_search` | Semantic + graph knowledge search | `query`, `user_id`, `conversation_id` (optional), `limit` | `{memories: {results: [...], relations: [...]}}` |
| `mem_get_context` | Memories formatted for prompt injection | `query`, `user_id`, `limit` | `{context: "...", memories: [...]}` |
| `broker_chat` | Conversational Imperator access | `messages: [{role, content}]` | `{response: "..."}` |
| `metrics_get` | Prometheus metrics | (none) | `{metrics: "..."}` |

### 5.3 Tool Naming Convention

All tools use domain prefixes (`conv_`, `mem_`, `broker_`, `metrics_`) per REQ §4.5. This prevents collisions when deployed alongside other MCP servers.

### 5.4 MCP Server Implementation

The LangGraph container implements the MCP server using the `mcp` Python SDK (or `FastMCP`). Tool definitions are registered at startup with their `inputSchema` for automatic validation:

```python
from mcp.server import Server
from mcp.types import Tool

server = Server("context-broker")

@server.tool()
async def conv_store_message(
    conversation_id: str = "",
    context_window_id: str = "",
    role: str = "",
    sender_id: str = "",
    content: str = "",
    priority: int = 3,
    # ...
) -> dict:
    """Store a message in a conversation."""
    result = await _message_pipeline.ainvoke({...})
    return result
```

This replaces the current pattern of a Node.js gateway forwarding JSON-RPC to Quart HTTP endpoints. The MCP protocol is now handled natively in Python with nginx as a transparent proxy.

---

## 6. OpenAI-Compatible Chat Interface

### 6.1 Endpoint

`POST /v1/chat/completions` — follows the OpenAI API specification (REQ §4.2).

### 6.2 Request Format

```json
{
  "model": "context-broker",
  "messages": [
    {"role": "user", "content": "What do you know about the deployment last week?"}
  ],
  "stream": false
}
```

The `model` field is accepted but ignored — the Imperator uses the LLM configured in `config.yml`. This maintains compatibility with clients that require a model field.

### 6.3 Response Format (non-streaming)

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1711000000,
  "model": "context-broker",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Based on the conversation history..."},
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

### 6.4 Streaming Response Format

When `stream: true`:

```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1711000000,"model":"context-broker","choices":[{"index":0,"delta":{"role":"assistant","content":"Based"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1711000000,"model":"context-broker","choices":[{"index":0,"delta":{"content":" on"},"finish_reason":null}]}

...

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1711000000,"model":"context-broker","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### 6.5 Implementation

The `/v1/chat/completions` endpoint is a route in the LangGraph HTTP server that:

1. Extracts the user messages from the request
2. Invokes the Imperator StateGraph (same as `broker_chat` MCP tool)
3. Formats the response in OpenAI API format
4. For streaming: uses SSE chunked transfer with the Imperator's streamed output

The Imperator stores all messages (both user and its own responses) in its persistent conversation via `conv_store_message`, so the full interaction history is preserved and subject to the same embedding, assembly, and extraction pipeline as any other conversation.

---

## 7. Configuration System

### 7.1 Configuration File

All configuration lives in `/config/config.yml` (REQ §5.1). The application reads this file fresh on each operation for inference and build type settings. Infrastructure settings (database connections, ports) are read at startup.

### 7.2 Full Configuration Schema

```yaml
# =============================================================================
# Context Broker Configuration
# =============================================================================

# --- Inference Providers ---------------------------------------------------

llm:
  base_url: https://api.openai.com/v1     # Any OpenAI-compatible endpoint
  model: gpt-4o-mini
  api_key_env: LLM_API_KEY                 # Environment variable name

embeddings:
  base_url: https://api.openai.com/v1
  model: text-embedding-3-small
  api_key_env: EMBEDDINGS_API_KEY
  dimensions: 768                          # Must match vector(N) in schema

reranker:
  provider: cross-encoder                  # "cross-encoder", "cohere", or "none"
  model: BAAI/bge-reranker-v2-m3          # Local model (cross-encoder) or API model (cohere)
  api_key_env: RERANKER_API_KEY           # Only needed for cohere
  top_n: 10                                # Final results after reranking

# --- Build Types -----------------------------------------------------------

build_types:
  standard-tiered:
    description: "Three-tier progressive compression (episodic only)"
    tier1_pct: 0.08
    tier2_pct: 0.20
    tier3_pct: 0.72
    max_context_tokens: auto
    fallback_tokens: 8192
    trigger_threshold_pct: 0.75           # Assembly triggers at this % of budget

  knowledge-enriched:
    description: "Full retrieval pipeline (episodic + semantic + knowledge graph)"
    tier1_pct: 0.05
    tier2_pct: 0.15
    tier3_pct: 0.50
    knowledge_graph_pct: 0.15
    semantic_retrieval_pct: 0.15
    max_context_tokens: auto
    fallback_tokens: 16000
    trigger_threshold_pct: 0.75

# --- Embedding Pipeline ----------------------------------------------------

embedding:
  context_window_size: 3                   # N prior messages prepended for contextual embedding

# --- Search ----------------------------------------------------------------

search:
  hybrid_candidate_limit: 50              # Stage 1 candidates for RRF
  recency_decay_days: 90                  # Days until max recency penalty
  recency_decay_max_penalty: 0.20         # Max penalty factor (20%)

# --- Memory Extraction -----------------------------------------------------

memory_extraction:
  small_llm_max_chars: 90000              # Text under this → use llm config
  large_llm_max_chars: 450000            # Text under this → use large_llm config
  large_llm:                               # Optional separate LLM for large extractions
    base_url: https://api.openai.com/v1
    model: gpt-4o-mini
    api_key_env: LLM_API_KEY

# --- Imperator -------------------------------------------------------------

imperator:
  build_type: standard-tiered
  max_context_tokens: auto
  admin_tools: false                       # true = config/database modification

# --- Packages --------------------------------------------------------------

packages:
  source: local                            # "local", "pypi", or "devpi"
  local_path: /app/packages
  devpi_url: null

# --- Logging ---------------------------------------------------------------

logging:
  level: INFO                              # DEBUG, INFO, WARN, ERROR
```

### 7.3 Token Budget Resolution

Per REQ §5.4:

1. If `max_context_tokens` is an explicit number → use it
2. If `auto` → query the LLM provider's model list endpoint for the model's context length
3. If the provider doesn't report it → use `fallback_tokens`
4. An explicit `max_tokens` passed by the caller to `conv_create_context_window` overrides the build type default
5. Token budget is resolved once at window creation and stored in the `context_windows` row

```python
async def resolve_token_budget(config: dict, build_type_id: str, caller_max: Optional[int]) -> int:
    """Resolve the token budget for a new context window."""
    if caller_max is not None:
        return caller_max

    bt = config["build_types"][build_type_id]
    max_tokens = bt.get("max_context_tokens", "auto")

    if isinstance(max_tokens, int):
        return max_tokens

    # auto: query provider
    try:
        llm_cfg = config["llm"]
        from openai import OpenAI
        client = OpenAI(base_url=llm_cfg["base_url"], api_key=os.environ[llm_cfg["api_key_env"]])
        models = client.models.list()
        for model in models.data:
            if model.id == llm_cfg["model"]:
                return getattr(model, "context_window", bt["fallback_tokens"])
    except Exception:
        pass

    return bt["fallback_tokens"]
```

### 7.4 Credential Management

Per REQ §3.4:

- All credentials in `/config/credentials/.env` as key-value pairs
- Loaded via `env_file` in `docker-compose.yml`
- Application reads via `os.environ[config["llm"]["api_key_env"]]`
- Repository ships `.env.example` with variable names, no values
- `.env` is gitignored

Example `.env`:

```
LLM_API_KEY=sk-...
EMBEDDINGS_API_KEY=sk-...
RERANKER_API_KEY=...
POSTGRES_PASSWORD=...
NEO4J_AUTH=neo4j/...
NEO4J_PASSWORD=...
```

### 7.5 Config Hot-Reload

Inference providers, models, build types, and token budgets are read fresh from `config.yml` on each operation. The pattern:

```python
def load_config() -> dict:
    """Read config.yml. Called per-operation for inference/build settings."""
    with open("/config/config.yml") as f:
        return yaml.safe_load(f)
```

This means changing the LLM provider or adding a new build type takes effect on the next request without a container restart. Infrastructure settings (database connection strings, ports) are read once at startup and require a restart to change.

---

## 8. Build Type and Retrieval Pipeline

### 8.1 Build Type Definitions

Build types are named strategies defined in `config.yml` that control how context windows are assembled. The system ships with two:

#### standard-tiered

Episodic memory only. Uses the three-tier progressive compression model:

```
┌─────────────────────────────────────────────────┐
│ Tier 1 (8%): Archival Summary                    │
│   Single consolidated summary of full history    │
├─────────────────────────────────────────────────┤
│ Tier 2 (20%): Chunk Summaries                    │
│   Rolling 20-message chunk summaries             │
├─────────────────────────────────────────────────┤
│ Tier 3 (72%): Recent Verbatim                    │
│   Most recent messages at full fidelity          │
└─────────────────────────────────────────────────┘
```

No vector search, no knowledge graph queries. Lower inference cost. Suitable as the default for most use cases.

#### knowledge-enriched

Full retrieval pipeline. Three episodic tiers plus two additional retrieval layers:

```
┌─────────────────────────────────────────────────┐
│ Tier 1 (5%): Archival Summary                    │
├─────────────────────────────────────────────────┤
│ Tier 2 (15%): Chunk Summaries                    │
├─────────────────────────────────────────────────┤
│ Knowledge Graph (15%): Extracted Facts           │
│   Entity relationships, decisions, preferences   │
│   from Mem0/Neo4j graph traversal                │
├─────────────────────────────────────────────────┤
│ Semantic Retrieval (15%): Relevant Messages      │
│   Vectorially similar messages from outside the  │
│   recent window (pgvector similarity search)     │
├─────────────────────────────────────────────────┤
│ Tier 3 (50%): Recent Verbatim                    │
└─────────────────────────────────────────────────┘
```

### 8.2 knowledge-enriched Retrieval Pipeline (End-to-End)

This is the full retrieval pipeline that combines all five layers:

```
conv_retrieve_context(context_window_id)
         │
         ▼
[1] Load window config from PostgreSQL
    Load build type from config.yml
    Determine: build_type_id = "knowledge-enriched"
         │
         ▼
[2] Check assembly lock (Redis)
    Wait up to 50s if assembly in progress
         │
         ▼
[3] Load episodic tiers (PostgreSQL)
    ├── Tier 1: SELECT archival summary WHERE tier=1 AND is_active=TRUE
    ├── Tier 2: SELECT chunk summaries WHERE tier=2 AND is_active=TRUE
    └── Calculate token budgets from percentages
         │
         ▼
[4] Parallel retrieval branches:
    │
    ├── [4a] Semantic Retrieval (pgvector)
    │   Budget: max_token_limit * 0.15
    │   │
    │   ├── Extract topic from recent messages (last 5)
    │   ├── Embed topic via configured embeddings provider
    │   ├── Query: SELECT id, content, 1 - (embedding <=> $query) AS score
    │   │          FROM conversation_messages
    │   │          WHERE conversation_id = $conv_id
    │   │            AND sequence_number < $tier3_start_seq  -- outside recent window
    │   │            AND embedding IS NOT NULL
    │   │          ORDER BY embedding <=> $query ASC
    │   │          LIMIT 20
    │   └── Fill semantic_retrieval budget with top results
    │
    └── [4b] Knowledge Graph Retrieval (Mem0/Neo4j)
        Budget: max_token_limit * 0.15
        │
        ├── Extract topic from recent messages (same as 4a)
        ├── Call mem0.search(topic, user_id=conv.user_id)
        │   → Returns: {results: [{memory, score}], relations: [{source, target, relationship}]}
        ├── Format facts as structured text:
        │     "- [fact] (confidence: 0.85)"
        │     "- [entity] --[relationship]--> [entity]"
        └── Fill knowledge_graph budget with top results
         │
         ▼
[5] Get recent messages (PostgreSQL)
    Budget: max_token_limit * 0.50
    Walk backwards from newest, fill budget
         │
         ▼
[6] Assemble context with XML markers
    Order: archival → chunks → semantic → knowledge → recent
         │
         ▼
    Return assembled context
```

**Topic Extraction for Retrieval:**

For steps 4a and 4b, the system needs a query representing the current conversation topic. This is extracted from the most recent messages:

```python
async def extract_topic(conversation_id: str, limit: int = 5) -> str:
    """Extract a topic query from recent messages for retrieval."""
    recent = await db.get_recent_messages(conversation_id, limit=limit)
    # Concatenate recent content as the search query
    return "\n".join(m["content"][-200] for m in recent)
```

This concatenated text is then embedded (for vector search) or used directly (for Mem0 semantic search). The approach is simple and effective — the most recent conversation content is the best signal for what the participant currently needs to know.

### 8.3 Custom Build Types

Build types are open-ended per the concept paper. Deployers can define additional build types in `config.yml`:

```yaml
build_types:
  # ... standard-tiered and knowledge-enriched as above ...

  sliding-window:
    description: "No summarization. Most recent N tokens verbatim."
    tier1_pct: 0.0
    tier2_pct: 0.0
    tier3_pct: 1.0
    max_context_tokens: 4096
    trigger_threshold_pct: 0.0      # Never triggers assembly

  knowledge-dominant:
    description: "Primarily knowledge graph + minimal recent context"
    tier1_pct: 0.0
    tier2_pct: 0.0
    tier3_pct: 0.20
    knowledge_graph_pct: 0.60
    semantic_retrieval_pct: 0.20
    max_context_tokens: auto
    fallback_tokens: 16000
```

The Context Assembly flow reads the build type config and adapts its behavior:

- If `tier1_pct` and `tier2_pct` are both 0 → skip summarization entirely
- If `knowledge_graph_pct` is absent or 0 → skip knowledge graph retrieval
- If `semantic_retrieval_pct` is absent or 0 → skip vector similarity search
- If `trigger_threshold_pct` is 0 → never trigger proactive assembly (sliding window case)

### 8.4 Assembly Trigger Logic

Proactive assembly is triggered when:

1. A message is stored (`conv_store_message`)
2. The embed pipeline completes and checks all context windows on the conversation
3. For each window: `conversation.estimated_token_count >= window.max_token_limit * build_type.trigger_threshold_pct`
4. AND the assembly lock is not held
5. AND the message is newer than the last assembly

This ensures context is assembled in the background during idle time between reasoning steps, as described in c1.

---

## 9. Queue and Async Processing

### 9.1 Architecture

The queue system uses Redis lists and sorted sets, consumed by independent async loops running in the LangGraph container's event loop. Three independent consumers process their queues in parallel:

```
┌─────────────────────────────────────────────────────────────┐
│                    LangGraph Container                       │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Embedding    │  │  Assembly     │  │  Extraction       │  │
│  │  Consumer     │  │  Consumer     │  │  Consumer         │  │
│  │  (async loop) │  │  (async loop) │  │  (async loop)     │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────────┘  │
│         │                  │                  │              │
│         ▼                  ▼                  ▼              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                    Redis                               │   │
│  │                                                        │   │
│  │  embedding_jobs          (LIST)                        │   │
│  │  context_assembly_jobs   (LIST)                        │   │
│  │  memory_extraction_jobs  (ZSET, priority-scored)       │   │
│  │  dead_letter_jobs        (LIST)                        │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────┐                                       │
│  │  Dead-Letter      │  (sweeps every 60s, re-queues ≤10)  │
│  │  Sweep Loop       │                                       │
│  └──────────────────┘                                       │
└─────────────────────────────────────────────────────────────┘
```

### 9.2 Job Types

| Queue | Type | Job Payload | Consumer | StateGraph Invoked |
|---|---|---|---|---|
| `embedding_jobs` | LIST | `{message_id, conversation_id, priority, attempt, enqueued_at}` | Embedding consumer | Embed Pipeline |
| `context_assembly_jobs` | LIST | `{conversation_id, context_window_id, build_type_id, attempt, enqueued_at}` | Assembly consumer | Context Assembly |
| `memory_extraction_jobs` | ZSET | `{conversation_id, job_type}` scored by priority offset + timestamp | Extraction consumer | Memory Extraction |
| `dead_letter_jobs` | LIST | Original job payload with failure metadata | Dead-letter sweep | Re-queued to original |

### 9.3 Job Lifecycle

```
Message stored
    │
    ▼
embedding_jobs ──RPOP──> Embedding Consumer
    │                         │
    │                    [Embed Pipeline Graph]
    │                         │
    │                    ┌────┴────┐
    │                    ▼         ▼
    │    context_assembly_jobs   memory_extraction_jobs
    │         │                       │
    │    ──RPOP──>               ──ZPOPMAX──>
    │         │                       │
    │    [Assembly Graph]        [Extraction Graph]
    │         │                       │
    │         ▼                       ▼
    │    Context assembled       Knowledge extracted
    │    (PostgreSQL)            (Neo4j + pgvector)
    │
    └── On failure: retry with backoff (5^attempt seconds)
        After 3 attempts: dead_letter_jobs
        Sweep every 60s: re-queue up to 10 dead-letter jobs
```

### 9.4 Priority System

Memory extraction uses a ZSET with priority-based scoring to ensure live user interactions are extracted before bulk/migration data:

| Priority | Description | Score Offset |
|---|---|---|
| 0 | Live user interactions | 3,000,000,000,000 + timestamp |
| 1 | Interactive agent communications | 2,000,000,000,000 + timestamp |
| 2 | Background agent prose | 1,000,000,000,000 + timestamp |
| 3 | Migration / bulk backlog | 0 + timestamp |

Each bucket is 10^12 apart — all messages at priority N score higher than all at priority N+1 regardless of timestamp. Within a bucket, newest `created_at` scores highest.

### 9.5 Retry and Dead-Letter

- **Retry:** On failure, the job's `attempt` counter is incremented. If `attempt <= 3`, the job is re-queued with exponential backoff (`5^(attempt-1)` seconds: 1s, 5s, 25s).
- **Dead-letter:** After 3 failed attempts, the job moves to `dead_letter_jobs`.
- **Sweep:** Every 60 seconds, up to 10 dead-letter jobs are re-queued to their original queues with `attempt` reset to 1. Jobs that fail again will dead-letter again after 3 more attempts, preventing infinite loops.

### 9.6 Eventual Consistency Model

Per REQ §7.1:

- **Source of truth:** PostgreSQL (`conversation_messages`). A stored message is never lost.
- **Background processing:** Embedding, assembly, and extraction are asynchronous and may lag behind the conversation.
- **Failure tolerance:** If embedding fails, the message exists without an embedding. If assembly fails, context is served from the last successful assembly. If extraction fails, the knowledge graph may be slightly behind. All failures retry with backoff.
- **No transactional consistency across stores.** PostgreSQL, Neo4j, and Redis are eventually consistent with each other.

### 9.7 Design Decision — Redis Queue Pattern

**Question:** The current code uses a custom queue worker polling Redis lists. Is there a more standard approach?

**Answer:** The current pattern is appropriate for this use case. The alternatives considered:

- **Celery/RQ:** Add significant dependency weight and operational complexity (separate worker processes, broker configuration) for a single-consumer workload. The Context Broker has exactly one consumer per queue type, all running in the same process.
- **Redis Streams:** Would provide consumer groups and acknowledgment, but the current LIST/ZSET pattern with dead-letter retry provides equivalent reliability with simpler code.
- **LangGraph background tasks:** LangGraph does not provide a built-in job queue primitive. The StateGraph is the right abstraction for the processing logic; the queue is the right abstraction for the scheduling.

The pattern is: **Redis for scheduling, StateGraph for processing.** Each consumer loop is a simple `while True: rpop → ainvoke → sleep`. The StateGraph handles all the complex logic. The queue worker is ~50 lines of straightforward async code per consumer.

---

## 10. Imperator Design

### 10.1 Identity and Purpose

The Imperator is the Context Broker's built-in conversational agent. It serves as:

1. **A reference consumer** — it uses the same conversation storage, context assembly, embedding, and knowledge extraction capabilities that external callers access via MCP
2. **A conversational interface** — users can talk to the Context Broker directly via `/v1/chat/completions` or the `broker_chat` MCP tool
3. **A system administrator** — when `admin_tools: true`, it can inspect and modify configuration

### 10.2 Persistent Conversation

The Imperator maintains a single ongoing conversation that persists across container restarts:

1. On first boot: creates a conversation via `conv_create_conversation`, creates a context window using the configured build type and token budget, writes `{"conversation_id": "<uuid>", "context_window_id": "<uuid>"}` to `/data/imperator_state.json`
2. On subsequent boots: reads the state file, validates the conversation and context window still exist, resumes
3. If the state file is missing or references a non-existent conversation: creates new ones

Every user message and Imperator response is stored via `conv_store_message`, triggering the full pipeline (embedding, assembly, extraction). The Imperator's conversation accumulates the same way any other conversation does.

### 10.3 System Prompt

The Imperator's system prompt defines its identity and capabilities:

```
You are the Context Broker Imperator — a conversational agent with access to
a conversation and memory management system.

You can:
- Search conversation history to find what was discussed
- Search extracted knowledge to find what is known
- Retrieve context windows to see how context is assembled
- Report on system status (conversations, messages, memories)

You maintain a persistent conversation. Everything discussed here is stored,
embedded, and available for future reference.

When answering questions:
- Use conv_search or conv_search_messages to find relevant conversations or messages
- Use mem_search to find extracted knowledge and entity relationships
- Cite specific conversations or memories when possible
- Be direct about what you found or didn't find
```

### 10.4 StateGraph Design

The Imperator uses a ReAct-style loop with tool use:

**State Schema:**

```python
class ImperatorState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # LangGraph message accumulator
    user_id: str
    step_count: int
    max_steps: int                    # Configurable, default 6
    final_response: Optional[str]
    status: str                       # 'in_progress', 'completed'
```

**Graph:**

```
[store_user_message]
         │
         ▼
[retrieve_context]           ◄── Assembles context window for the Imperator
         │
         ▼
[reason] ──tool_call──> [execute_tool] ──> [reason]  (loop up to max_steps)
    │
    respond_directly
    ▼
[store_response]
    │
    ▼
   END
```

**Node Details:**

| Node | Responsibility |
|---|---|
| `store_user_message` | Store the user's message in the Imperator's persistent conversation via `conv_store_message` |
| `retrieve_context` | Call `conv_retrieve_context` on the Imperator's context window to get the assembled context (including history, summaries, and optionally knowledge graph/semantic retrieval if using `knowledge-enriched`) |
| `reason` | Call the configured LLM with: system prompt + assembled context + current messages. LLM can respond directly or request a tool call. Uses **`langchain_openai.ChatOpenAI`** with `.bind_tools()` for structured tool use. |
| `execute_tool` | Execute the requested tool (one of the internal tool functions that back the MCP tools). Return observation to the message history. |
| `store_response` | Store the Imperator's final response in the persistent conversation via `conv_store_message` |

**Design Decision — LangChain tool binding:** The current Imperator uses a hand-rolled JSON parsing approach where the LLM is prompted to return structured JSON selecting a tool. The State 4 design uses LangChain's `ChatOpenAI.bind_tools()` which provides native function calling through the OpenAI API's `tools` parameter. This is more reliable, supports any OpenAI-compatible provider that implements function calling, and eliminates the JSON parsing fragility.

### 10.5 Tool Belt

The Imperator has access to these internal functions (the same functions backing MCP tools):

| Tool | Description |
|---|---|
| `conv_search` | Search conversations by semantic query + structured filters |
| `conv_search_messages` | Hybrid search across messages |
| `conv_get_history` | Get full message sequence for a conversation |
| `conv_search_context_windows` | List/search context windows |
| `conv_retrieve_context` | Get assembled context for a window |
| `mem_search` | Search extracted knowledge |
| `mem_get_context` | Get memories formatted for prompt injection |

When `admin_tools: true` in config, additional tools are available:

| Admin Tool | Description |
|---|---|
| `config_read` | Read the current `config.yml` contents |
| `config_write` | Modify a specific config key (writes to `/config/config.yml`) |
| `db_query` | Execute a read-only SQL query against PostgreSQL |

### 10.6 Context Assembly for the Imperator

The Imperator's context window uses whatever build type is configured in `imperator.build_type`. With `standard-tiered`, it gets three-tier episodic context. With `knowledge-enriched`, it gets the full retrieval pipeline including vector-similar messages and knowledge graph facts.

This means the Imperator's reasoning improves as the knowledge graph accumulates — it can recall facts, entity relationships, and decisions from past conversations even if those conversations are far outside its recent verbatim window.

### 10.7 Conversation Flow Example

```
User: "What do we know about the API migration?"

Imperator:
  1. [store_user_message] → stores in persistent conversation
  2. [retrieve_context] → gets assembled context (which may already contain
     relevant history from the archival/chunk summaries)
  3. [reason] → decides to search for more specific information
     → tool_call: conv_search_messages(query="API migration")
  4. [execute_tool] → runs hybrid search, returns top 10 results
  5. [reason] → decides to also check knowledge graph
     → tool_call: mem_search(query="API migration")
  6. [execute_tool] → returns relevant extracted facts and relationships
  7. [reason] → has enough information, responds directly
     "Based on the conversation history and extracted knowledge:
      The API migration was discussed in conversations X and Y.
      Key decisions: [from knowledge graph]. Current status: [from messages]."
  8. [store_response] → stores response in persistent conversation
```

---

## Appendix A: LangChain Component Mapping

This table summarizes where standard LangChain/LangGraph components replace hand-rolled code:

| Current (State 2) | New (State 4) | Rationale |
|---|---|---|
| `SutherlandLlmAdapter` / `OpenAICompatibleLlmAdapter` | `langchain_openai.ChatOpenAI` | Standard provider interface, configurable via `base_url` |
| `SutherlandEmbedderAdapter` | `langchain_openai.OpenAIEmbeddings` | Standard embedding interface |
| Custom Mem0 LLM/embedder adapters | Thin wrappers around LangChain models | Mem0 requires its own adapter interface; wrappers delegate to LangChain |
| Node.js MCP gateway with `mcp-protocol-lib` | nginx (routing) + Python `mcp` SDK (protocol) | Eliminates custom gateway container |
| `langchain-mcp-adapters.MultiServerMCPClient` for peer calls | Direct LangChain model instantiation | No peer MADs in standalone — providers are direct |
| Hand-rolled JSON tool selection in Imperator | `ChatOpenAI.bind_tools()` | Native function calling, more reliable |
| Raw asyncpg for all queries | asyncpg for writes/CRUD + `langchain_postgres.PGVector` for semantic retrieval | LangChain retrievers where they add value; raw SQL where they don't |
| Custom cross-encoder reranker via Sutherland MCP tool | `langchain.retrievers.document_compressors.CrossEncoderReranker` | Standard reranker interface, runs locally on CPU |
| Build types as database rows | Build types in `config.yml` | Config-driven, hot-reloadable, no migration needed for new types |

## Appendix B: Deployment Quick Start

```bash
# 1. Clone and configure
git clone <repo>
cd context-broker
cp config/config.example.yml config/config.yml
cp config/credentials/.env.example config/credentials/.env

# 2. Edit config.yml with your provider settings
# Edit .env with your API keys

# 3. Create data directories
mkdir -p data/{postgres,neo4j,redis}

# 4. Start
docker compose up -d

# 5. Verify
curl http://localhost:8080/health
# {"status": "healthy", "database": "ok", "cache": "ok", "neo4j": "ok"}

# 6. Connect any OpenAI-compatible client
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"context-broker","messages":[{"role":"user","content":"Hello"}]}'
```

## Appendix C: Key Design Decisions Summary

| Decision | Choice | Why |
|---|---|---|
| Gateway technology | nginx (config only) | Eliminates custom Node.js container. Pure routing layer per REQ §4.7. |
| MCP server implementation | Python `mcp` SDK in LangGraph container | Native protocol handling, no gateway translation layer |
| Database access pattern | asyncpg for writes + LangChain stores for retrieval | Transactional writes need raw SQL; retrieval benefits from LangChain abstractions |
| Queue technology | Redis LIST/ZSET with async polling | Appropriate for single-consumer workloads; avoids Celery/RQ complexity |
| Build type storage | config.yml (not database) | Hot-reloadable, no migration for new types, deployer-editable |
| Embedding provider | LangChain `OpenAIEmbeddings` via config | Standard interface, works with any OpenAI-compatible endpoint |
| LLM provider | LangChain `ChatOpenAI` via config | Standard interface, supports function calling, works with any compatible endpoint |
| Reranker | Local cross-encoder (default) or Cohere API | No API key needed for default; optional upgrade path |
| Mem0 integration | Mem0 with injected LangChain-wrapped adapters | Mem0 provides graph memory management; LangChain models provide the inference |
| Imperator tool calling | `ChatOpenAI.bind_tools()` | Native function calling replaces fragile JSON parsing |
| Credential management | `.env` file → environment variables → `api_key_env` config reference | Standard Docker pattern, gitignored, no secrets in code |

---

## Document Metadata

**Version History:**

- v1.0 (2026-03-20): Initial HLD

**Implements:**

- REQ-context-broker.md v1.0

**References:**

- c1-the-context-broker.md — Domain model and concept paper
- d4-agent-optimal-code-architecture.md — StateGraph-as-application, AE/TE separation
- d5-state-4-mad.md — State 4 configurable dependencies pattern
