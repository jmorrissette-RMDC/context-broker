# Context Broker — High-Level Design

**Version:** 1.0
**Date:** 2026-03-20
**Status:** Draft
**Companion Documents:** REQ-context-broker.md, c1-the-context-broker.md

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Container Architecture](#2-container-architecture)
3. [StateGraph Flow Designs](#3-stategraph-flow-designs)
4. [Database Schema](#4-database-schema)
5. [MCP Tool Interface](#5-mcp-tool-interface)
6. [OpenAI-Compatible Chat Interface](#6-openai-compatible-chat-interface)
7. [Configuration System](#7-configuration-system)
8. [Build Types and Retrieval Pipeline](#8-build-types-and-retrieval-pipeline)
9. [Queue and Async Processing](#9-queue-and-async-processing)
10. [Imperator Design](#10-imperator-design)

---

## 1. System Overview

### 1.1 What the Context Broker Is

The Context Broker is a standalone context engineering and conversational memory service. It stores infinite conversation history, assembles purpose-built context windows using configurable strategies, extracts knowledge into a graph, and exposes its capabilities via both MCP tools and an OpenAI-compatible conversational interface.

The core problem it solves: agents reason within finite context windows, but conversations accumulate without limit. The Context Broker bridges this gap by engineering the right informational environment for each agent at each reasoning step — compressed where compression serves, verbatim where fidelity matters, knowledge-rich where facts are what the agent needs — all within the token budget available.

### 1.2 Domain Model (from c1)

The Context Broker implements the following domain concepts:

- **Infinite Conversation** — Every message, every turn, every participant, stored indefinitely. The conversation is the primary substrate for memory and cognition.
- **Context Window** — A purpose-built view of a conversation, constructed according to a specific strategy (build type) for a specific participant with a specific token budget. Not truncation — curation.
- **Build Type** — A named strategy defining how conversation history is processed, filtered, weighted, and assembled. Open-ended and configurable.
- **Three-Tier Progressive Compression** — The standard assembly strategy: archival summary (Tier 1, oldest, most compressed), chunk summaries (Tier 2, intermediate), recent verbatim messages (Tier 3, newest, full fidelity).
- **Episodic Memory** — The conversation record itself. What was said, by whom, in what order. Managed via progressive compression.
- **Semantic Memory** — Extracted knowledge: facts, entity relationships, preferences, decisions. Stored in a knowledge graph. Queryable by meaning and graph traversal.
- **Per-Participant Windows** — Different participants in the same conversation can have different context windows with different build types and token budgets.
- **Proactive Assembly** — Context is assembled in the background after each message is stored, not on demand. When an agent requests context, it is already waiting.

### 1.3 Architectural Pattern

The Context Broker is a **State 4 MAD** (Multipurpose Agentic Duo):

- **AE (Action Engine)** — MCP tool handlers, message routing, database operations, queue processing. Stable infrastructure.
- **TE (Thought Engine)** — The Imperator (conversational agent) and its cognitive apparatus. Evolves independently.
- **Configuration** — A single `config.yml` controls all external dependencies. The same code runs standalone or in any environment. Only the config file changes.

All programmatic and cognitive logic is implemented as LangGraph StateGraphs. The HTTP server is transport only — it initializes and invokes compiled StateGraphs.

### 1.4 High-Level Data Flow

```
                          ┌──────────────────────────┐
                          │    External Clients       │
                          │  (MCP clients, Chat UIs)  │
                          └────────┬─────────────────┘
                                   │
                          ┌────────▼─────────────────┐
                          │   context-broker (nginx)  │
                          │   Gateway / Routing Layer │
                          │   /mcp  /v1/chat  /health │
                          └────────┬─────────────────┘
                                   │ context-broker-net (internal)
                          ┌────────▼─────────────────┐
                          │ context-broker-langgraph  │
                          │  HTTP Server (Quart)      │
                          │  ┌─────────────────────┐  │
                          │  │   StateGraph Flows   │  │
                          │  │  Message Pipeline    │  │
                          │  │  Embed Pipeline      │  │
                          │  │  Context Assembly    │  │
                          │  │  Retrieval           │  │
                          │  │  Memory Extraction   │  │
                          │  │  Imperator           │  │
                          │  └─────────────────────┘  │
                          │  ┌─────────────────────┐  │
                          │  │   Queue Worker       │  │
                          │  │  3 async consumers   │  │
                          │  └─────────────────────┘  │
                          └────┬───────┬───────┬──────┘
                               │       │       │
                    ┌──────────▼┐ ┌────▼────┐ ┌▼──────────┐
                    │ PostgreSQL │ │  Redis  │ │   Neo4j   │
                    │ + pgvector │ │  Queue  │ │   Graph   │
                    └───────────┘ └─────────┘ └───────────┘
```

---

## 2. Container Architecture

### 2.1 Container Inventory

| Container | Image | Role | Network | Ports |
|---|---|---|---|---|
| `context-broker` | nginx (OTS) | Gateway — routes MCP, chat, and health traffic | default + context-broker-net | 80 (configurable) |
| `context-broker-langgraph` | Custom (Python 3.12) | All application logic — StateGraph flows, queue worker, Imperator | context-broker-net only | 8000 (internal) |
| `context-broker-postgres` | pgvector/pgvector:pg16 (OTS) | Conversation storage, vector embeddings, build types | context-broker-net only | 5432 (internal) |
| `context-broker-neo4j` | neo4j:5 (OTS) | Knowledge graph storage (Mem0 graph store) | context-broker-net only | 7687 (internal) |
| `context-broker-redis` | redis:7-alpine (OTS) | Job queues, assembly locks, ephemeral state | context-broker-net only | 6379 (internal) |

Only the LangGraph container is custom. All backing services use official images unmodified.

### 2.2 Network Topology

```
┌─────────────────────────────────────────────────────────────┐
│  Host Network (default)                                     │
│                                                             │
│  ┌─────────────────────┐                                    │
│  │  context-broker      │◄─── External clients connect here │
│  │  (nginx gateway)     │     port 80 (or configured port)  │
│  └──────────┬──────────┘                                    │
│             │                                               │
│  ┌──────────▼──────────────────────────────────────────┐    │
│  │  context-broker-net (bridge, internal)               │    │
│  │                                                      │    │
│  │  ┌──────────────────────┐  ┌─────────────────────┐  │    │
│  │  │ context-broker-      │  │ context-broker-     │  │    │
│  │  │ langgraph            │  │ postgres            │  │    │
│  │  └──────────────────────┘  └─────────────────────┘  │    │
│  │                                                      │    │
│  │  ┌──────────────────────┐  ┌─────────────────────┐  │    │
│  │  │ context-broker-      │  │ context-broker-     │  │    │
│  │  │ neo4j                │  │ redis               │  │    │
│  │  └──────────────────────┘  └─────────────────────┘  │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

**Key rules:**
- The gateway is the sole boundary between external traffic and internal services.
- LangGraph and all backing services connect only to the internal network.
- All inter-container communication uses Docker Compose service names (DNS), never IP addresses.
- No `depends_on` with `condition: service_healthy` — containers start independently and handle dependency unavailability at request time (REQ §7.2).

### 2.3 Volume Mounts

```yaml
# docker-compose.yml volume topology
services:
  context-broker:
    volumes:
      - ./config/nginx.conf:/etc/nginx/nginx.conf:ro

  context-broker-langgraph:
    volumes:
      - ./config:/config:ro          # config.yml, read at runtime
      - ./data:/data                 # imperator_state.json
    env_file:
      - ./config/credentials/.env    # API keys loaded as env vars

  context-broker-postgres:
    volumes:
      - ./data/postgres:/var/lib/postgresql/data

  context-broker-neo4j:
    volumes:
      - ./data/neo4j/data:/data
      - ./data/neo4j/logs:/logs

  context-broker-redis:
    volumes:
      - ./data/redis:/data
    command: redis-server --appendonly yes --dir /data
```

**Host directory structure:**

```
project-root/
├── docker-compose.yml
├── config/
│   ├── config.yml                    # All configuration
│   ├── config.example.yml            # Documented template
│   ├── nginx.conf                    # Gateway routing rules
│   └── credentials/
│       ├── .env                      # API keys (gitignored)
│       └── .env.example              # Template listing required vars
├── data/                             # All persistent state (backup target)
│   ├── postgres/
│   ├── neo4j/
│   │   ├── data/
│   │   └── logs/
│   ├── redis/
│   └── imperator_state.json
└── context-broker-langgraph/         # Custom container source
    ├── Dockerfile
    ├── requirements.txt
    ├── server.py
    ├── flows/
    ├── services/
    └── packages/                     # Local wheels (when source=local)
```

### 2.4 Nginx Gateway Configuration

The gateway is a pure routing layer with no application logic:

```nginx
# /config/nginx.conf (simplified)

upstream langgraph {
    server context-broker-langgraph:8000;
}

server {
    listen 80;

    # MCP endpoint — HTTP/SSE transport
    location /mcp {
        proxy_pass http://langgraph;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;              # Required for SSE
        proxy_read_timeout 300s;          # Long-lived SSE connections
    }

    # OpenAI-compatible chat endpoint
    location /v1/chat/completions {
        proxy_pass http://langgraph;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;              # Required for streaming
        proxy_read_timeout 300s;
    }

    # Health check — proxied to langgraph for dependency checking
    location /health {
        proxy_pass http://langgraph;
    }

    # Prometheus metrics
    location /metrics {
        proxy_pass http://langgraph;
    }
}
```

### 2.5 LangGraph Container Dockerfile

```dockerfile
FROM python:3.12.8-slim

ARG USER_NAME=context-broker
ARG USER_UID=1000
ARG USER_GID=1000

# Root phase: system packages and user creation
RUN groupadd --gid ${USER_GID} ${USER_NAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} --shell /bin/bash --create-home ${USER_NAME}

USER ${USER_NAME}
WORKDIR /app

# Install dependencies
COPY --chown=${USER_NAME}:${USER_NAME} requirements.txt ./
COPY --chown=${USER_NAME}:${USER_NAME} packages/ /tmp/packages/
RUN pip install --no-cache-dir --no-index --find-links=/tmp/packages/ -r requirements.txt \
    && rm -rf /tmp/packages/

# Copy application
COPY --chown=${USER_NAME}:${USER_NAME} server.py ./
COPY --chown=${USER_NAME}:${USER_NAME} flows/ ./flows/
COPY --chown=${USER_NAME}:${USER_NAME} services/ ./services/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["python", "server.py"]
```

---

## 3. StateGraph Flow Designs

All application logic is implemented as LangGraph StateGraphs. The HTTP server is transport only — route handlers construct initial state and invoke compiled graphs. This section defines each flow's nodes, edges, state schema, and LangChain component usage.

### 3.1 Message Pipeline Flow

**Purpose:** Handle `conv_store_message` — validate, deduplicate, store the message, and queue async enrichment.

**Trigger:** Synchronous, invoked directly from the MCP tool handler.

**State Schema:**

```python
class MessagePipelineState(TypedDict):
    # Inputs
    conversation_id: str
    context_window_id: Optional[str]    # Alternative to conversation_id
    role: str                           # 'user', 'assistant', 'system', 'tool'
    sender_id: str                      # Participant identifier
    content: str
    token_count: Optional[int]          # Caller-provided; estimated if absent
    metadata: Optional[dict]            # Extensible metadata

    # Outputs (set by nodes)
    message_id: Optional[str]           # UUID of stored message
    sequence_number: Optional[int]
    deduplicated: bool
    queued_jobs: list[str]              # Job types queued (e.g., ["embed"])
    error: Optional[str]
```

**Graph Structure:**

```
┌──────────────────────┐
│  resolve_conversation │ ─── Resolve conversation_id from context_window_id if needed
└──────────┬───────────┘
           │ (error → END)
┌──────────▼───────────┐
│     dedup_check      │ ─── Check for consecutive duplicate from same sender
└──────────┬───────────┘
           │ (deduplicated → END)
┌──────────▼───────────┐
│    store_message     │ ─── INSERT into conversation_messages, update counters
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│    queue_embed       │ ─── Push embedding job to Redis embedding_jobs list
└──────────┬───────────┘
           │
          END
```

**Node Details:**

| Node | Responsibility | LangChain Usage |
|---|---|---|
| `resolve_conversation` | If `context_window_id` is provided without `conversation_id`, look up the window and extract its conversation_id | asyncpg query |
| `dedup_check` | Fetch last message from same sender; compare content (stripping repeat suffixes). If duplicate, update repeat count on existing message and short-circuit | asyncpg query + update |
| `store_message` | INSERT message row, estimate tokens if not provided (len(content)//4), update conversation counters in a transaction | asyncpg transaction |
| `queue_embed` | Push JSON job `{message_id, conversation_id, job_type: "embed"}` to Redis `embedding_jobs` list | aioredis LPUSH |

**Design decisions:**
- The pipeline always returns immediately. All enrichment (embedding, assembly, extraction) is deferred to the queue worker.
- Token estimation uses the 4-chars-per-token heuristic when the caller does not provide `token_count`. This is sufficient for threshold checks; precise counting is not required.

### 3.2 Embed Pipeline Flow

**Purpose:** Generate a contextual embedding for a stored message, then fan out to context assembly and memory extraction queues.

**Trigger:** Queue worker, consuming from Redis `embedding_jobs` list.

**State Schema:**

```python
class EmbedPipelineState(TypedDict):
    # Inputs (from job payload)
    message_id: str
    conversation_id: str

    # Intermediate
    message: Optional[dict]             # Full message row
    embedding: Optional[list[float]]    # Vector from embedding model

    # Outputs
    assembly_jobs_queued: list[str]     # Context window IDs queued
    extraction_queued: bool
    error: Optional[str]
```

**Graph Structure:**

```
┌──────────────────┐
│  fetch_message   │ ─── Load message row from PostgreSQL
└────────┬─────────┘
         │ (not found → END)
┌────────▼─────────┐
│  build_context   │ ─── Fetch N prior messages for contextual embedding prefix
└────────┬─────────┘
         │
┌────────▼─────────┐
│  generate_embed  │ ─── Call configured embedding model via LangChain
└────────┬─────────┘
         │ (error → END)
┌────────▼─────────┐
│ store_embedding  │ ─── UPDATE message row with embedding vector
└────────┬─────────┘
         │
┌────────▼──────────────┐
│ check_context_assembly│ ─── For each context window on this conversation,
│                       │     check if assembly threshold crossed; queue if so
└────────┬──────────────┘
         │
┌────────▼──────────────────┐
│ queue_memory_extraction   │ ─── Queue extraction job if content_type='conversation'
│                           │     and not already extracted
└────────┬──────────────────┘
         │
        END
```

**LangChain Component Usage:**

| Current (Rogers) | Context Broker (State 4) |
|---|---|
| `mcp_client.get_tool("llm_embeddings")` — calls peer MAD via MCP | `langchain_openai.OpenAIEmbeddings` or `langchain_community.HuggingFaceEmbeddings` — configured via `config.yml` embeddings provider |
| Hand-rolled contextual prefix assembly | Same pattern retained — fetch N prior messages, prepend as `[Context]` block |
| 768-dim hardcoded | Embedding dimensions read from config; default 768, configurable for different models |

**Contextual embedding** is a key quality feature: before embedding a message, the N prior messages (configurable, default 3) are prepended as context. This gives short messages ("yes", "do that") meaningful vector representations. The composite text format:

```
[Context]
[assistant] Here are three options for the deployment strategy...
[user] I prefer option 2 because it minimizes downtime.
[Current]
Let's go with that.
```

**Embedding model initialization** (called once, cached):

```python
from langchain_openai import OpenAIEmbeddings

def _get_embedding_model(config: dict) -> OpenAIEmbeddings:
    """Create LangChain embedding model from config.yml."""
    emb_cfg = config["embeddings"]
    api_key = os.environ.get(emb_cfg["api_key_env"], "")
    return OpenAIEmbeddings(
        openai_api_base=emb_cfg["base_url"],
        openai_api_key=api_key,
        model=emb_cfg["model"],
    )
```

This replaces the Mem0 `SutherlandEmbedderAdapter` and the MCP peer proxy call chain. Any OpenAI-compatible embedding endpoint works.

### 3.3 Context Assembly Flow

**Purpose:** Build three-tier progressive compression for a context window. This is the core context engineering operation.

**Trigger:** Queue worker, consuming from Redis `context_assembly_jobs` list.

**State Schema:**

```python
class ContextAssemblyState(TypedDict):
    # Inputs (from job payload)
    conversation_id: str
    context_window_id: str
    build_type_id: str

    # Intermediate
    window: Optional[dict]              # Context window row
    build_config: Optional[dict]        # Build type config from config.yml
    messages: list[dict]                # All conversation messages
    max_token_limit: int
    tier_budgets: dict                  # Calculated token budgets per tier
    tier3_start_seq: int                # Sequence number boundary for Tier 3
    older_messages: list[dict]          # Messages needing summarization
    chunks: list[list[dict]]            # Chunked older messages (20 per chunk)
    chat_model: Optional[object]        # LangChain ChatModel instance
    model_name: str
    tier2_summaries_written: int
    tier1_consolidated: bool

    # Control
    assembly_lock_key: str              # Redis key for assembly_in_progress flag
    error: Optional[str]
```

**Graph Structure:**

```
┌────────────────────┐
│ acquire_lock       │ ─── Redis SET NX with 120s TTL
└────────┬───────────┘
         │ (not acquired → END, skip gracefully)
┌────────▼───────────┐
│ load_window_config │ ─── Fetch window row + resolve build type from config.yml
└────────┬───────────┘
         │ (error → release_lock)
┌────────▼───────────┐
│ load_messages      │ ─── Fetch all messages for conversation
└────────┬───────────┘
         │ (no messages → finalize)
┌────────▼───────────┐
│ calculate_tiers    │ ─── Calculate tier budgets from build type percentages,
│                    │     determine Tier 3 boundary, identify older messages,
│                    │     check existing Tier 2 summaries for incremental processing,
│                    │     chunk unsummarized messages into groups of 20
└────────┬───────────┘
         │ (no older messages → finalize)
┌────────▼───────────┐
│ init_chat_model    │ ─── Create LangChain ChatModel from config.yml LLM provider
└────────┬───────────┘
         │
┌────────▼───────────┐
│ summarize_chunks   │ ─── For each chunk: LLM summarize → INSERT Tier 2 summary
└────────┬───────────┘
         │
┌────────▼───────────┐
│ consolidate_tier1  │ ─── If >3 active Tier 2 summaries: consolidate oldest
│                    │     into single Tier 1 archival summary, deactivate sources
└────────┬───────────┘
         │
┌────────▼───────────┐
│ finalize           │ ─── Update window last_assembled_at timestamp
└────────┬───────────┘
         │
┌────────▼───────────┐
│ release_lock       │ ─── Delete Redis assembly lock (always reached)
└────────┬───────────┘
         │
        END
```

**LangChain Component Usage:**

| Current (Rogers) | Context Broker (State 4) |
|---|---|
| `_build_summarization_llm()` → `SutherlandLlmAdapter` or `OpenAICompatibleLlmAdapter` (Mem0 adapter classes) | `langchain_openai.ChatOpenAI` configured from `config.yml` LLM provider |
| `llm.generate_response([messages])` run in thread pool executor | `chat_model.ainvoke([SystemMessage, HumanMessage])` — native async |
| Tier proportions dynamically calculated from window size | Tier proportions read from build type config in `config.yml` (e.g., `tier1_pct: 0.08`) |

**Chat model initialization:**

```python
from langchain_openai import ChatOpenAI

def _get_chat_model(config: dict) -> ChatOpenAI:
    """Create LangChain ChatModel from config.yml LLM provider."""
    llm_cfg = config["llm"]
    api_key = os.environ.get(llm_cfg["api_key_env"], "")
    return ChatOpenAI(
        openai_api_base=llm_cfg["base_url"],
        openai_api_key=api_key,
        model_name=llm_cfg["model"],
        temperature=0.1,
    )
```

**Summarization prompt** (used in `summarize_chunks`):

```python
from langchain_core.messages import SystemMessage, HumanMessage

messages = [
    SystemMessage(content=(
        "Summarize this conversation chunk concisely, preserving key facts, "
        "decisions, and preferences. Keep the summary under 200 words."
    )),
    HumanMessage(content=chunk_text),
]
result = await chat_model.ainvoke(messages)
summary_text = result.content
```

**Incremental assembly:** The `calculate_tiers` node checks existing active Tier 2 summaries and only processes messages with sequence numbers above the highest already-summarized sequence. This avoids re-summarizing the entire history on every assembly pass.

**Error handling:** All error paths route through `release_lock` before END to ensure the Redis assembly lock is always cleaned up. This replaces try/finally — the graph structure guarantees cleanup.

### 3.4 Retrieval Flow

**Purpose:** Retrieve the assembled context window for a participant. This is what agents call to get their context.

**Trigger:** Synchronous, invoked directly from the `conv_retrieve_context` MCP tool handler.

**State Schema:**

```python
class RetrievalState(TypedDict):
    # Input
    context_window_id: str

    # Intermediate
    conversation_id: Optional[str]
    build_config: Optional[dict]        # Build type config from config.yml
    window: Optional[dict]
    max_token_limit: int
    tier1_summary: Optional[str]
    tier2_summaries: list[str]
    recent_messages: list[dict]
    # knowledge-enriched specific
    knowledge_graph_facts: list[str]    # Facts from Neo4j graph traversal
    semantic_messages: list[dict]       # Messages from vector similarity search

    # Output
    context: Optional[str]              # Assembled context string
    tiers: Optional[dict]               # Structured tier breakdown
    total_tokens: int
    assembly_status: str                # 'ready', 'blocked_waiting', 'error'
    error: Optional[str]
```

**Graph Structure:**

```
┌──────────────────┐
│   get_window     │ ─── Fetch window + resolve build type from config.yml
└────────┬─────────┘
         │ (error → END)
┌────────▼─────────┐
│ check_assembly   │ ─── If assembly_in_progress Redis flag is set, poll/wait
│                  │     (2s intervals, 50s timeout, under gateway's 60s timeout)
└────────┬─────────┘
         │
┌────────▼─────────┐
│  get_summaries   │ ─── Fetch active Tier 1 + Tier 2 summaries for this window
└────────┬─────────┘
         │
┌────────▼─────────┐
│  get_recent      │ ─── Fill remaining token budget with recent verbatim messages
└────────┬─────────┘
         │
┌────────▼──────────────────┐
│ get_semantic_retrieval    │ ─── (knowledge-enriched only) Vector similarity search
│                           │     against pgvector for relevant messages outside
│                           │     the recent window
└────────┬──────────────────┘
         │
┌────────▼──────────────────┐
│ get_knowledge_graph       │ ─── (knowledge-enriched only) Query Neo4j/Mem0 for
│                           │     relevant facts and entity relationships
└────────┬──────────────────┘
         │
┌────────▼─────────┐
│    assemble      │ ─── Format all tiers into structured context with XML markers
└────────┬─────────┘
         │
        END
```

**Conditional nodes:** The `get_semantic_retrieval` and `get_knowledge_graph` nodes are conditionally executed based on the build type configuration. For `standard-tiered`, the routing function skips these nodes and goes directly to `assemble`. For `knowledge-enriched`, both nodes execute and their results are included in the assembled context.

**LangChain Component Usage for Semantic Retrieval:**

```python
from langchain_postgres import PGVector

# Initialized once at startup
vectorstore = PGVector(
    connection=pg_connection_string,
    collection_name="conversation_messages",
    embedding_function=embedding_model,    # Same model used for message embeddings
)
retriever = vectorstore.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 20},
)
```

**Assembled context format** (XML markers for clear delineation):

```xml
<archival_summary>
Long-term summary of the full conversation history...
</archival_summary>

<chunk_summaries>
Summary of messages 45-65...

Summary of messages 66-85...
</chunk_summaries>

<knowledge_graph>
- User prefers deployment option 2 for minimal downtime
- Project deadline is March 30
- Database migration requires PostgreSQL 16
</knowledge_graph>

<semantic_retrieval>
[assistant | sender:system] On Feb 15, we decided to use pgvector for...
[user | sender:alice] The key constraint is that we need zero-downtime...
</semantic_retrieval>

<recent_messages>
[user | sender:alice] Let's finalize the deployment plan.
[assistant | sender:system] Based on our previous discussions...
</recent_messages>
```

### 3.5 Memory Extraction Flow

**Purpose:** Extract structured knowledge from conversation messages into the Neo4j knowledge graph via Mem0.

**Trigger:** Queue worker, consuming from Redis `memory_extraction_jobs` sorted set (ZSET).

**State Schema:**

```python
class MemoryExtractionState(TypedDict):
    # Input
    conversation_id: str

    # Intermediate
    messages: list[dict]                # Unextracted conversational messages
    user_id: str                        # From conversation row
    conversation_text: str              # Built text for Mem0 input
    selected_message_ids: list          # Message IDs included in the text
    extraction_lock_key: str            # Redis lock key

    # Output
    extracted_count: int
    error: Optional[str]
```

**Graph Structure:**

```
┌─────────────────────┐
│  fetch_unextracted  │ ─── Get messages where content_type='conversation'
│                     │     AND memory_extracted=FALSE
└────────┬────────────┘
         │ (no messages → END)
┌────────▼────────────┐
│ build_extract_text  │ ─── Build conversation text newest-first within char budget,
│                     │     redact secrets via detect-secrets
└────────┬────────────┘
         │
┌────────▼────────────┐
│  acquire_lock       │ ─── Redis SET NX with 120s TTL (prevents concurrent
│                     │     extraction of same conversation)
└────────┬────────────┘
         │ (not acquired → END, skip gracefully)
┌────────▼────────────┐
│ run_extraction      │ ─── Call Mem0 memory.add() with conversation text
└────────┬────────────┘
         │
┌────────▼────────────┐
│ mark_extracted      │ ─── UPDATE memory_extracted=TRUE for selected message IDs
└────────┬────────────┘
         │
┌────────▼────────────┐
│  release_lock       │ ─── Delete Redis extraction lock
└────────┬────────────┘
         │
        END
```

**Mem0 Integration:**

Mem0 remains the knowledge extraction engine. It handles:
- Fact extraction from conversation text
- Entity resolution and relationship detection
- Storage in Neo4j (graph store) and pgvector (vector store for memory search)

**Mem0 configuration** in the Context Broker replaces the custom adapter classes:

```python
from mem0 import Memory
from mem0.configs.base import (
    MemoryConfig, LlmConfig, EmbedderConfig,
    VectorStoreConfig, GraphStoreConfig,
)

def _build_mem0(config: dict) -> Memory:
    llm_cfg = config["llm"]
    emb_cfg = config["embeddings"]

    mem_config = MemoryConfig(
        version="v1.1",
        llm=LlmConfig(
            provider="openai",
            config={
                "api_key": os.environ.get(llm_cfg["api_key_env"], ""),
                "openai_base_url": llm_cfg["base_url"],
                "model": llm_cfg["model"],
            },
        ),
        embedder=EmbedderConfig(
            provider="openai",
            config={
                "api_key": os.environ.get(emb_cfg["api_key_env"], ""),
                "openai_base_url": emb_cfg["base_url"],
                "model": emb_cfg["model"],
            },
        ),
        vector_store=VectorStoreConfig(
            provider="pgvector",
            config={
                "host": "context-broker-postgres",
                "port": 5432,
                "dbname": "context_broker",
                "user": "context_broker",
                "password": os.environ.get("POSTGRES_PASSWORD", ""),
                "collection_name": "mem0_memories",
                "embedding_model_dims": config.get("embedding_dims", 768),
            },
        ),
        graph_store=GraphStoreConfig(
            provider="neo4j",
            config={
                "url": "bolt://context-broker-neo4j:7687",
                "username": "neo4j",
                "password": os.environ.get("NEO4J_PASSWORD", ""),
            },
        ),
    )
    return Memory(config=mem_config)
```

This replaces the `SutherlandLlmAdapter`, `SutherlandEmbedderAdapter`, and `OpenAICompatibleLlmAdapter` entirely. Mem0's built-in OpenAI provider works with any OpenAI-compatible endpoint via `openai_base_url`.

**Secret redaction** is applied before text reaches Mem0, using the `detect-secrets` library with custom detectors for connection strings, bearer tokens, and shell credentials.

### 3.6 Imperator Flow

See [Section 10: Imperator Design](#10-imperator-design) for the full Imperator specification. The graph structure is defined there.

### 3.7 Metrics Flow

**Purpose:** Collect and serve Prometheus metrics via a StateGraph (per the all-logic-in-graph invariant).

**State Schema:**

```python
class MetricsState(TypedDict):
    action: str
    metrics_data: str
```

**Graph:** Single node `collect_metrics` → END. Reads from the Prometheus client registry and returns exposition-format text.

---

## 4. Database Schema

### 4.1 PostgreSQL Schema

```sql
-- Context Broker database schema
-- Requires: pgvector extension

CREATE EXTENSION IF NOT EXISTS vector;

-- Schema version tracking for migrations
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT NOW(),
    description TEXT
);
INSERT INTO schema_version (version, description) VALUES (1, 'Initial schema');

-- ============================================================
-- conversations
-- Top-level conversation entity.
-- ============================================================

CREATE TABLE conversations (
    id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255),
    title VARCHAR(500),
    metadata JSONB DEFAULT '{}',
    total_messages INTEGER DEFAULT 0,
    estimated_token_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_conversations_user ON conversations(user_id, updated_at DESC);

-- ============================================================
-- conversation_messages
-- One row per message. Any number of participants, any role.
-- ============================================================

CREATE TABLE conversation_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id VARCHAR(255) NOT NULL REFERENCES conversations(id),
    role VARCHAR(50) NOT NULL,
    sender_id VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER,
    metadata JSONB DEFAULT '{}',
    embedding vector(768),                  -- Configurable dimensions
    sequence_number INTEGER NOT NULL,
    content_type VARCHAR(50) DEFAULT 'conversation',
    memory_extracted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    content_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(content, ''))
    ) STORED
);

CREATE INDEX idx_messages_conversation ON conversation_messages(conversation_id);
CREATE INDEX idx_messages_conversation_seq ON conversation_messages(
    conversation_id, sequence_number
);
CREATE INDEX idx_messages_conversation_sender ON conversation_messages(
    conversation_id, sender_id, sequence_number DESC
);
CREATE INDEX idx_messages_created ON conversation_messages(created_at);

-- pgvector ANN index: ivfflat with cosine distance
-- lists=100 is appropriate for datasets up to ~1M rows
CREATE INDEX idx_messages_emb ON conversation_messages
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- BM25 full-text search index
CREATE INDEX idx_messages_tsv ON conversation_messages USING GIN(content_tsv);

-- ============================================================
-- context_windows
-- Per-participant instances with build type and token limit.
-- ============================================================

CREATE TABLE context_windows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id VARCHAR(255) NOT NULL REFERENCES conversations(id),
    build_type_id VARCHAR(100) NOT NULL,
    max_token_limit INTEGER NOT NULL,
    last_assembled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_windows_conversation ON context_windows(conversation_id);

-- ============================================================
-- conversation_summaries
-- Tiered summaries keyed to context windows.
-- tier 1 = archival (full history compressed)
-- tier 2 = chunk (20-message groups)
-- ============================================================

CREATE TABLE conversation_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id VARCHAR(255) NOT NULL REFERENCES conversations(id),
    context_window_id UUID NOT NULL REFERENCES context_windows(id),
    summary_text TEXT NOT NULL,
    summary_embedding vector(768),
    tier INTEGER NOT NULL CHECK (tier IN (1, 2)),
    summarizes_from_seq INTEGER,
    summarizes_to_seq INTEGER,
    message_count INTEGER,
    original_token_count INTEGER,
    summary_token_count INTEGER,
    summarized_by_model VARCHAR(100),
    summarized_at TIMESTAMPTZ DEFAULT NOW(),
    superseded_by UUID REFERENCES conversation_summaries(id),
    is_active BOOLEAN DEFAULT TRUE
);

CREATE INDEX idx_summaries_window ON conversation_summaries(
    context_window_id, is_active
);
CREATE INDEX idx_summaries_emb ON conversation_summaries
    USING ivfflat (summary_embedding vector_cosine_ops) WITH (lists = 100);

-- ============================================================
-- Mem0 deduplication index
-- Applied to the mem0_memories table that Mem0 creates at runtime.
-- Prevents storing the same memory twice for the same user.
-- ============================================================

-- NOTE: This index is created by the application after Mem0 initializes
-- its tables, not in this init script. The application checks for the
-- table's existence and creates the index if missing.
-- CREATE UNIQUE INDEX IF NOT EXISTS idx_mem0_hash_user
--     ON mem0_memories ((payload->>'hash'), (payload->>'user_id'));
```

**Key schema changes from Rogers:**

| Rogers (State 2) | Context Broker (State 4) | Rationale |
|---|---|---|
| `context_window_build_types` table | Build types defined in `config.yml` | Config-driven, no database dependency for strategy definitions |
| `flow_id`, `flow_name` columns on conversations | Removed — replaced by generic `metadata` JSONB | No ecosystem-specific fields; extensible via metadata |
| `sender_id INTEGER` | `sender_id VARCHAR(255)` | Not coupled to ecosystem UID system |
| `TIMESTAMP` columns | `TIMESTAMPTZ` columns | Timezone-aware timestamps for correctness across deployments |
| `external_session_id` column | Moved to `metadata` JSONB | Extensible without schema changes |
| `priority SMALLINT` on messages | Moved to `metadata` JSONB | Queue priority is a processing concern, not storage |

### 4.2 Neo4j Data Model

Neo4j is used exclusively by Mem0 as its graph store. The data model is defined by Mem0's internal schema:

```
(Entity {name, entity_type})
    -[RELATES_TO {relationship, source, target}]->
(Entity {name, entity_type})
```

**Node types** (created by Mem0):
- `Entity` — Named entities extracted from conversations (people, projects, technologies, concepts)
- Properties: `name`, `entity_type`, `created_at`, `updated_at`

**Relationship types** (created by Mem0):
- `RELATES_TO` — Named relationships between entities
- Properties: `relationship` (description), `source`, `target`, `created_at`, `updated_at`

**Usage in context assembly:**
- The `get_knowledge_graph` node in the retrieval flow queries Mem0's `memory.search()` method, which performs both vector similarity search against the Mem0 vector store and graph traversal against Neo4j.
- Results are formatted as structured facts for injection into the context window's `knowledge_graph` section.

### 4.3 Redis Usage

Redis serves three purposes, all ephemeral:

| Key/Pattern | Type | Purpose | TTL |
|---|---|---|---|
| `embedding_jobs` | List | Pending embedding jobs | None (consumed by worker) |
| `context_assembly_jobs` | List | Pending context assembly jobs | None (consumed by worker) |
| `memory_extraction_jobs` | Sorted Set | Pending extraction jobs (priority-ordered) | None (consumed by worker) |
| `dead_letter_jobs` | List | Failed jobs after max retries | None (swept periodically) |
| `assembly_in_progress:{window_id}` | String | Lock preventing concurrent assembly | 120s |
| `extraction_in_progress:{conv_id}` | String | Lock preventing concurrent extraction | 120s |

Redis data is not critical — it can be lost and regenerated. The PostgreSQL conversation record is the source of truth.

---

## 5. MCP Tool Interface

### 5.1 Transport

MCP uses HTTP/SSE transport via the LangGraph container's built-in MCP server (using `langchain-mcp-adapters` or the `mcp` Python SDK):

| Endpoint | Method | Purpose |
|---|---|---|
| `/mcp` | GET | Establish SSE session |
| `/mcp?sessionId=xxx` | POST | Route message to existing session |
| `/mcp` | POST (no sessionId) | Sessionless mode for simple tool calls |

The nginx gateway proxies all `/mcp` traffic to the LangGraph container with SSE-appropriate settings (no buffering, long read timeout).

### 5.2 Tool Inventory

| Tool | Input Schema (key fields) | Output Schema | Description |
|---|---|---|---|
| `conv_create_conversation` | `title`, `user_id?`, `metadata?` | `{conversation_id, created_at}` | Create a new conversation |
| `conv_store_message` | `conversation_id` or `context_window_id`, `role`, `sender_id`, `content`, `token_count?`, `metadata?` | `{message_id, sequence_number, deduplicated, queued_jobs}` | Store a message; triggers async enrichment |
| `conv_retrieve_context` | `context_window_id` | `{context, tiers, total_tokens, assembly_status}` | Retrieve assembled context window |
| `conv_create_context_window` | `conversation_id`, `build_type_id`, `max_tokens?` | `{id, conversation_id, build_type_id, max_token_limit}` | Create a context window for a participant |
| `conv_search` | `query?`, `user_id?`, `date_from?`, `date_to?`, `limit?` | `{conversations: [...]}` | Semantic + structured search across conversations |
| `conv_search_messages` | `query?`, `conversation_id?`, `sender_id?`, `role?`, `date_from?`, `date_to?`, `limit?` | `{messages: [...]}` | Hybrid search (vector + BM25 + reranking) |
| `conv_get_history` | `conversation_id` | `{conversation, messages}` | Full chronological message sequence |
| `conv_search_context_windows` | `context_window_id?`, `conversation_id?`, `build_type_id?` | `{context_windows: [...]}` | Search/list context windows |
| `mem_search` | `query`, `user_id`, `limit?` | `{memories: {results, relations}}` | Semantic + graph search across knowledge |
| `mem_get_context` | `query`, `user_id`, `limit?` | `{context, memories}` | Memories formatted for prompt injection |
| `broker_chat` | `messages` (OpenAI format) | `{response}` | Conversational interface to Imperator |
| `metrics_get` | (none) | `{metrics}` | Prometheus metrics text |

### 5.3 Tool Naming Convention

All tools use `[domain]_[action]` prefixes:
- `conv_*` — Conversation operations
- `mem_*` — Memory/knowledge operations
- `broker_*` — System-level operations
- `metrics_*` — Observability

This prevents name collisions when the Context Broker is deployed alongside other MCP servers.

### 5.4 Tool Registration

Tools are registered using the MCP Python SDK's tool decorator pattern within the LangGraph container:

```python
from mcp.server import Server
from mcp.types import Tool

server = Server("context-broker")

@server.tool()
async def conv_store_message(
    conversation_id: str = None,
    context_window_id: str = None,
    role: str = ...,
    sender_id: str = ...,
    content: str = ...,
    token_count: int = None,
    metadata: dict = None,
) -> dict:
    """Store a message in a conversation. Triggers async embedding,
    context assembly, and knowledge extraction."""
    initial_state = { ... }
    result = await _message_pipeline.ainvoke(initial_state)
    return result
```

Each tool function constructs initial state and invokes the appropriate compiled StateGraph. No application logic in the tool handler.

### 5.5 Hybrid Search Pipeline (conv_search_messages)

The hybrid search is a three-stage pipeline:

**Stage 1: Candidate Retrieval** — Two parallel retrieval strategies:
- **Vector ANN:** pgvector cosine similarity against message embeddings (top 100)
- **BM25 full-text:** PostgreSQL `tsvector` matching (top 100)

**Stage 2: Reciprocal Rank Fusion (RRF)** — Combine the two ranked lists:
```
RRF_score(d) = 1/(k + rank_vector(d)) + 1/(k + rank_bm25(d))
```
where k=60 (standard RRF constant). Apply mild recency bias (max 20% penalty at 90 days).

**Stage 3: Cross-Encoder Reranking** — Top 50 RRF candidates are reranked by a cross-encoder model:
- Default: `BAAI/bge-reranker-v2-m3` running locally on CPU
- Configurable via `config.yml` reranker section
- Produces final top 10 results

```python
from sentence_transformers import CrossEncoder

reranker = CrossEncoder(config["reranker"]["model"])
# pairs = [(query, doc.content) for doc in candidates]
# scores = reranker.predict(pairs)
```

If the reranker is unavailable (provider: none or model load failure), raw RRF scores are used — the system degrades gracefully.

---

## 6. OpenAI-Compatible Chat Interface

### 6.1 Endpoint

```
POST /v1/chat/completions
```

The nginx gateway proxies this to the LangGraph container. Any OpenAI-compatible client (ChatGPT UI, Open WebUI, Cursor, custom applications) can connect without modification.

### 6.2 Request Format

```json
{
  "model": "context-broker",
  "messages": [
    {"role": "user", "content": "What do you know about the deployment strategy?"}
  ],
  "stream": true,
  "temperature": 0.7
}
```

The `model` field is accepted but ignored — the Imperator always uses the LLM configured in `config.yml`. The `messages` array follows the standard OpenAI format.

### 6.3 Response Format

**Non-streaming:**

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1711000000,
  "model": "context-broker",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Based on our conversations..."},
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

**Streaming** (SSE format):

```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1711000000,"model":"context-broker","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1711000000,"model":"context-broker","choices":[{"index":0,"delta":{"content":"Based on "},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1711000000,"model":"context-broker","choices":[{"index":0,"delta":{"content":"our conversations..."},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1711000000,"model":"context-broker","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### 6.4 Chat-to-Imperator Flow

When a request arrives at `/v1/chat/completions`, the handler:

1. Extracts the user message(s) from the `messages` array
2. Stores the user message in the Imperator's persistent conversation via `conv_store_message`
3. Retrieves the Imperator's assembled context via `conv_retrieve_context`
4. Invokes the Imperator StateGraph with the message and context
5. Stores the Imperator's response in the persistent conversation
6. Returns the response in OpenAI format (streaming or non-streaming)

This means the Imperator uses its own context window and benefits from the same progressive compression that all consumers of the Context Broker receive.

---

## 7. Configuration System

### 7.1 Configuration File

All configuration lives in `/config/config.yml`. The application reads this file fresh on each operation that needs it — changes to inference providers, models, build types, and token budgets take effect immediately without restart. Infrastructure settings (database connections, ports) are read at startup and require a container restart to change.

### 7.2 Full Configuration Schema

```yaml
# config.yml — Context Broker Configuration

# ============================================================
# Inference Providers
# ============================================================

llm:
  base_url: https://api.openai.com/v1     # Any OpenAI-compatible endpoint
  model: gpt-4o-mini
  api_key_env: LLM_API_KEY                 # Environment variable name

embeddings:
  base_url: https://api.openai.com/v1
  model: text-embedding-3-small
  api_key_env: EMBEDDINGS_API_KEY
  dimensions: 768                          # Must match pgvector index

reranker:
  provider: cross-encoder                  # "cross-encoder", "cohere", or "none"
  model: BAAI/bge-reranker-v2-m3           # Local model, no API key needed
  top_n: 10                                # Final result count after reranking

# ============================================================
# Build Types
# ============================================================

build_types:
  standard-tiered:
    description: "Episodic only — three-tier progressive compression"
    tier1_pct: 0.08
    tier2_pct: 0.20
    tier3_pct: 0.72
    max_context_tokens: auto
    fallback_tokens: 8192
    trigger_threshold_pct: 0.75            # Assembly triggers at 75% of budget

  knowledge-enriched:
    description: "Episodic + semantic — full retrieval pipeline"
    tier1_pct: 0.05
    tier2_pct: 0.15
    tier3_pct: 0.50
    knowledge_graph_pct: 0.15
    semantic_retrieval_pct: 0.15
    max_context_tokens: auto
    fallback_tokens: 16000
    trigger_threshold_pct: 0.75

# ============================================================
# Imperator
# ============================================================

imperator:
  build_type: standard-tiered
  max_context_tokens: auto
  admin_tools: false                       # true = config/db modification allowed
  identity:
    name: "Context Broker"
    purpose: "Conversational interface to the context engineering service"

# ============================================================
# Embedding
# ============================================================

embedding:
  context_window_size: 3                   # Prior messages for contextual embedding

# ============================================================
# Search
# ============================================================

search:
  hybrid_candidate_limit: 50               # RRF candidates before reranking

# ============================================================
# Memory Extraction
# ============================================================

memory_extraction:
  small_llm_max_chars: 90000               # Text size threshold for LLM routing
  large_llm_max_chars: 450000
  llm_timeout: 120                         # Seconds

# ============================================================
# Packages
# ============================================================

packages:
  source: local                            # "local", "pypi", or "devpi"
  local_path: /app/packages
  devpi_url: null

# ============================================================
# Logging
# ============================================================

logging:
  level: INFO                              # DEBUG, INFO, WARN, ERROR

# ============================================================
# Infrastructure (read at startup only — restart required)
# ============================================================

database:
  host: context-broker-postgres
  port: 5432
  name: context_broker
  user: context_broker
  password_env: POSTGRES_PASSWORD

neo4j:
  host: context-broker-neo4j
  port: 7687
  username: neo4j
  password_env: NEO4J_PASSWORD

redis:
  host: context-broker-redis
  port: 6379

queue:
  poll_interval: 5                         # Seconds between queue polls
  max_attempts: 3                          # Retries before dead-lettering
  dead_letter_sweep_interval: 60           # Seconds
```

### 7.3 Credential Management

Credentials are stored in `/config/credentials/.env` and loaded as environment variables via `env_file` in `docker-compose.yml`:

```env
# /config/credentials/.env
LLM_API_KEY=sk-...
EMBEDDINGS_API_KEY=sk-...
POSTGRES_PASSWORD=...
NEO4J_PASSWORD=...
```

The `api_key_env` fields in `config.yml` name the environment variable to read. Application code never reads credential files directly — it reads environment variables at runtime.

### 7.4 Token Budget Resolution

When a context window is created via `conv_create_context_window`:

1. If the caller provides `max_tokens`, use that value
2. Otherwise, read the build type's `max_context_tokens` from `config.yml`:
   - If `auto`: query the configured LLM provider's model list endpoint for the model's context length. If the provider does not report it, use `fallback_tokens`
   - If an explicit number: use that value
3. The resolved budget is stored on the context window row. Model changes do not retroactively affect existing windows.

### 7.5 Config Hot-Reload

The application reads `config.yml` fresh on each operation via a helper function:

```python
import yaml

_config_path = "/config/config.yml"

def load_config() -> dict:
    """Load config.yml. Called on each operation that needs config."""
    with open(_config_path) as f:
        return yaml.safe_load(f)
```

This means:
- Changing the LLM provider takes effect on the next tool call
- Adding a new build type takes effect on the next `conv_create_context_window` call
- Changing the log level takes effect on the next log statement
- No restart, no signal, no admin command required

Infrastructure settings (database host/port, Redis host/port) are exceptions — these are read once at startup because they initialize connection pools.

---

## 8. Build Types and Retrieval Pipeline

### 8.1 Build Type Architecture

A build type is a named context assembly strategy defined in `config.yml`. It specifies:
- **Tier percentages** — How the token budget is allocated across tiers
- **Active retrieval layers** — Which of the five possible layers are included
- **Token budget defaults** — Default max tokens and fallback values

Build types are open-ended. Deployers can define additional build types for their specific needs.

### 8.2 Standard-Tiered Build Type

```
┌─────────────────────────────────────────────────┐
│              Token Budget (8,192)                │
│                                                 │
│  ┌──────────┐                                   │
│  │  Tier 1  │  8% = 655 tokens                  │
│  │ Archival │  Single summary of full history    │
│  └──────────┘                                   │
│  ┌──────────────────┐                           │
│  │     Tier 2       │  20% = 1,638 tokens       │
│  │ Chunk Summaries  │  Rolling 20-msg summaries  │
│  └──────────────────┘                           │
│  ┌──────────────────────────────────────────┐   │
│  │              Tier 3                       │   │
│  │         Recent Verbatim                   │   │
│  │      72% = 5,898 tokens                   │   │
│  │  Most recent messages, full fidelity      │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

**Assembly process:**
1. Calculate tier budgets from percentages
2. Walk backwards from most recent message, filling Tier 3 until budget exhausted
3. Messages before Tier 3 boundary are chunked (20 per chunk) and summarized
4. If >3 active Tier 2 summaries, consolidate oldest into Tier 1 archival summary

**No vector search, no knowledge graph queries.** This is episodic memory only — lower inference cost, suitable as the default.

### 8.3 Knowledge-Enriched Build Type

```
┌───────────────────────────────────────────────────┐
│              Token Budget (16,000)                 │
│                                                   │
│  ┌──────────┐                                     │
│  │  Tier 1  │  5% = 800 tokens                    │
│  │ Archival │                                     │
│  └──────────┘                                     │
│  ┌──────────────┐                                 │
│  │   Tier 2     │  15% = 2,400 tokens             │
│  │ Chunk Sums   │                                 │
│  └──────────────┘                                 │
│  ┌──────────────────────┐                         │
│  │  Knowledge Graph     │  15% = 2,400 tokens     │
│  │  (Neo4j/Mem0 facts)  │  Structured facts and   │
│  │                      │  entity relationships    │
│  └──────────────────────┘                         │
│  ┌──────────────────────┐                         │
│  │ Semantic Retrieval   │  15% = 2,400 tokens     │
│  │ (pgvector search)    │  Similar messages from   │
│  │                      │  outside recent window   │
│  └──────────────────────┘                         │
│  ┌──────────────────────────────────────────┐     │
│  │              Tier 3                       │     │
│  │         Recent Verbatim                   │     │
│  │       50% = 8,000 tokens                  │     │
│  └──────────────────────────────────────────┘     │
└───────────────────────────────────────────────────┘
```

### 8.4 Knowledge-Enriched Retrieval Pipeline (End-to-End)

This is the full retrieval pipeline for the `knowledge-enriched` build type, demonstrating all five layers working together:

```
conv_retrieve_context(context_window_id)
        │
        ▼
┌─────────────────────────────────────────────┐
│ 1. Load Window + Build Config               │
│    - Fetch context_windows row              │
│    - Read build type from config.yml        │
│    - Calculate token budgets per layer      │
└──────────────────┬──────────────────────────┘
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
┌──────────┐ ┌──────────┐ ┌──────────────────────┐
│ Tier 1+2 │ │ Semantic │ │ Knowledge Graph      │
│ Episodic │ │ Retrieval│ │ Retrieval            │
│          │ │          │ │                      │
│ Fetch    │ │ Embed    │ │ Mem0 memory.search() │
│ active   │ │ recent   │ │   ├─ vector search   │
│ summaries│ │ messages │ │   │  (pgvector)       │
│ from DB  │ │ as query │ │   └─ graph traversal │
│          │ │ vector   │ │      (Neo4j)         │
│          │ │          │ │                      │
│          │ │ PGVector │ │ Returns:             │
│          │ │ retriever│ │  - facts             │
│          │ │ .invoke()│ │  - relations         │
│          │ │          │ │  - entity links      │
│          │ │ Filter:  │ │                      │
│          │ │ exclude  │ │ Budget:              │
│          │ │ messages │ │ knowledge_graph_pct  │
│          │ │ already  │ │ tokens               │
│          │ │ in Tier 3│ │                      │
│          │ │          │ │                      │
│          │ │ Budget:  │ │                      │
│          │ │ semantic_│ │                      │
│          │ │ retrieval│ │                      │
│          │ │ _pct     │ │                      │
│          │ │ tokens   │ │                      │
└────┬─────┘ └────┬─────┘ └──────────┬───────────┘
     │            │                   │
     └────────────┼───────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│ 2. Get Recent Messages (Tier 3)             │
│    - Walk backwards from most recent        │
│    - Fill remaining budget with verbatim    │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ 3. Assemble Context                         │
│    - Format with XML section markers        │
│    - Order: archival → chunks → knowledge   │
│           → semantic → recent               │
│    - Respect per-tier token budgets         │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
              Return assembled
              context + metadata
```

**Semantic Retrieval Node** implementation:

```python
async def get_semantic_retrieval(state: RetrievalState) -> dict:
    """Retrieve semantically relevant messages via pgvector similarity search."""
    config = load_config()
    build_config = state["build_config"]

    if "semantic_retrieval_pct" not in build_config:
        return {"semantic_messages": []}

    budget_tokens = int(
        state["max_token_limit"] * build_config["semantic_retrieval_pct"]
    )

    # Use recent messages as the query signal
    recent = state.get("recent_messages", [])
    if not recent:
        return {"semantic_messages": []}

    query_text = "\n".join(m["content"][-200] for m in recent[-3:])

    # LangChain PGVector retriever
    embedding_model = _get_embedding_model(config)
    vectorstore = PGVector(
        connection=_get_pg_connection_string(config),
        collection_name="conversation_messages",
        embedding_function=embedding_model,
    )

    # Retrieve candidates, excluding messages already in the recent window
    recent_ids = {m["id"] for m in recent}
    docs = await vectorstore.asimilarity_search(query_text, k=20)
    filtered = [d for d in docs if d.metadata["id"] not in recent_ids]

    # Truncate to budget
    semantic_messages = []
    used_tokens = 0
    for doc in filtered:
        doc_tokens = len(doc.page_content) // 4
        if used_tokens + doc_tokens > budget_tokens:
            break
        semantic_messages.append({
            "content": doc.page_content,
            "metadata": doc.metadata,
        })
        used_tokens += doc_tokens

    return {"semantic_messages": semantic_messages}
```

**Knowledge Graph Node** implementation:

```python
async def get_knowledge_graph(state: RetrievalState) -> dict:
    """Retrieve relevant facts from Neo4j knowledge graph via Mem0."""
    config = load_config()
    build_config = state["build_config"]

    if "knowledge_graph_pct" not in build_config:
        return {"knowledge_graph_facts": []}

    budget_tokens = int(
        state["max_token_limit"] * build_config["knowledge_graph_pct"]
    )

    # Build search query from recent conversation context
    recent = state.get("recent_messages", [])
    query_text = "\n".join(m["content"][-200] for m in recent[-3:])

    # Mem0 search returns both vector results and graph relations
    mem0 = _get_mem0(config)
    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(
        None, lambda: mem0.search(query_text, user_id=state.get("user_id", "system"))
    )

    results = raw.get("results", []) if isinstance(raw, dict) else raw
    relations = raw.get("relations", []) if isinstance(raw, dict) else []

    # Format as structured facts
    facts = []
    used_tokens = 0
    for mem in results:
        text = mem.get("memory") or mem.get("content", "")
        fact_tokens = len(text) // 4
        if used_tokens + fact_tokens > budget_tokens:
            break
        facts.append(text)
        used_tokens += fact_tokens

    for rel in relations:
        fact = f"{rel.get('source', '?')} {rel.get('relationship', '?')} {rel.get('target', '?')}"
        fact_tokens = len(fact) // 4
        if used_tokens + fact_tokens > budget_tokens:
            break
        facts.append(fact)
        used_tokens += fact_tokens

    return {"knowledge_graph_facts": facts}
```

---

## 9. Queue and Async Processing

### 9.1 Architecture

The Context Broker uses a Redis-based job queue with three independent consumer loops running as async tasks in the same event loop as the HTTP server.

```
                    ┌─────────────────────────────────────────┐
                    │         Queue Worker (asyncio.gather)    │
                    │                                         │
conv_store_message  │  ┌────────────────┐                     │
  → queue_embed ───►│  │ Embed Consumer │─► Embed Pipeline    │
                    │  │ (Redis LIST)   │   StateGraph        │
                    │  └────────────────┘                     │
                    │                                         │
embed_pipeline      │  ┌────────────────┐                     │
  → queue_assembly ►│  │ Assembly       │─► Context Assembly  │
                    │  │ Consumer       │   StateGraph        │
                    │  │ (Redis LIST)   │                     │
                    │  └────────────────┘                     │
                    │                                         │
embed_pipeline      │  ┌────────────────┐                     │
  → queue_extract ─►│  │ Extraction     │─► Memory Extraction │
                    │  │ Consumer       │   StateGraph        │
                    │  │ (Redis ZSET)   │                     │
                    │  └────────────────┘                     │
                    │                                         │
                    │  ┌────────────────┐                     │
                    │  │ Dead-Letter    │─► Periodic sweep    │
                    │  │ Sweep          │   Re-queue to       │
                    │  │ (every 60s)    │   original queue    │
                    │  └────────────────┘                     │
                    └─────────────────────────────────────────┘
```

### 9.2 Job Types and Lifecycle

| Job Type | Queue | Type | Payload | Priority |
|---|---|---|---|---|
| `embed` | `embedding_jobs` | Redis LIST | `{message_id, conversation_id}` | FIFO |
| `context_assembly` | `context_assembly_jobs` | Redis LIST | `{conversation_id, context_window_id, build_type_id}` | FIFO |
| `extract_memory` | `memory_extraction_jobs` | Redis ZSET | `{conversation_id}` | Score-based (higher = more urgent) |

**Memory extraction priority scoring:**
```
score = priority_offset + message_timestamp

Priority offsets (separated by 10^12 to prevent overlap):
  P0 (live user):      3,000,000,000,000
  P1 (interactive):    2,000,000,000,000
  P2 (background):     1,000,000,000,000
  P3 (migration):      0
```

This ensures all live user messages are extracted before any migration backlog, regardless of timestamp.

### 9.3 Job Lifecycle

```
                ┌─────────┐
                │ Enqueue │
                └────┬────┘
                     │
                ┌────▼────┐
           ┌────│ Process │────┐
           │    └─────────┘    │
           │                   │
      ┌────▼────┐        ┌────▼────┐
      │ Success │        │  Error  │
      └─────────┘        └────┬────┘
                              │
                    attempt <= max_attempts?
                         │           │
                        Yes          No
                         │           │
                    ┌────▼────┐ ┌────▼────────┐
                    │ Re-queue│ │ Dead-letter  │
                    │ (backoff│ │              │
                    │  5^n s) │ └──────┬───────┘
                    └─────────┘        │
                                 ┌─────▼──────┐
                                 │ Sweep      │
                                 │ (every 60s │
                                 │ re-queue   │
                                 │ up to 10)  │
                                 └────────────┘
```

**Retry policy:**
- Max attempts: 3 (configurable)
- Backoff: exponential, 5^(attempt-1) seconds (5s, 25s, 125s)
- After max attempts: move to `dead_letter_jobs`
- Dead-letter sweep: every 60 seconds, re-queue up to 10 jobs with reset attempt counter

### 9.4 Eventual Consistency Model

The system is eventually consistent across datastores, not transactionally consistent:

- **PostgreSQL** is the source of truth. Message storage always succeeds synchronously.
- **Embeddings** are generated asynchronously. A message exists in the database before its embedding is available.
- **Context assembly** runs in the background. The assembled view may lag behind the latest messages.
- **Knowledge extraction** runs in the background. New facts may not appear in Mem0 immediately.

If a background job fails, it retries with backoff. The conversation record is never lost due to a downstream processing failure. If Neo4j is down, messages are still stored and embedded — knowledge extraction retries when Neo4j recovers.

### 9.5 Design Decision: Redis Queue vs. Alternatives

The current Redis LIST/ZSET polling pattern is retained rather than adopting a more complex solution (e.g., Celery, RQ, Redis Streams). Rationale:

- **Simplicity:** Three polling loops with ~50 lines of code each. No additional infrastructure, no broker configuration, no worker process management.
- **Correctness:** RPOP is atomic. ZPOPMAX is atomic. No message loss, no double-processing.
- **Observability:** Queue depths are trivially measurable (`LLEN`, `ZCARD`).
- **Fit for scale:** The Context Broker is a single-user or small-team tool. The three-consumer model handles the expected throughput. If scale becomes an issue, Redis Streams or an external message broker can replace the polling loops without changing the StateGraph flows.

---

## 10. Imperator Design

### 10.1 Identity and Purpose

The Imperator is the Context Broker's built-in conversational agent. It is the reference consumer of the system's own capabilities — it uses the same conversation storage, context assembly, embedding, and knowledge extraction that external callers access via MCP.

**Identity:**
```yaml
# From config.yml
imperator:
  identity:
    name: "Context Broker"
    purpose: "Conversational interface to the context engineering service"
```

**System prompt** (assembled at runtime):

```
You are {name}, a conversational interface to a context engineering service.

Your purpose: {purpose}

You have access to tools that let you search conversations, search extracted
knowledge, introspect context assembly, and report system status. Use these
tools when the user's question requires information from the conversation
history or knowledge graph.

You maintain a persistent conversation that accumulates over time. Your own
messages are stored, embedded, and assembled just like any other conversation
in the system.
```

### 10.2 Persistent Conversation

The Imperator maintains a single ongoing conversation that persists across container restarts:

1. **First boot:** Create a conversation via `conv_create_conversation`. Create a context window with the configured build type. Write `{"conversation_id": "<uuid>", "context_window_id": "<uuid>"}` to `/data/imperator_state.json`.
2. **Subsequent boots:** Read `/data/imperator_state.json`. Verify the conversation and context window exist. Resume.
3. **State file missing or invalid:** Create new conversation and context window. Write new state file.

This means the Imperator accumulates context over time — its archival summary grows, its chunk summaries capture the history of interactions, and its knowledge graph accumulates facts from those interactions.

### 10.3 Tool Belt

The Imperator has access to the same internal functions that back the MCP tools, invoked as LangChain tools:

**Standard tools** (always available):

| Tool | Description |
|---|---|
| `conv_search` | Search conversations by semantic query and/or filters |
| `conv_search_messages` | Hybrid search across messages |
| `conv_get_history` | Full message history for a conversation |
| `conv_search_context_windows` | List and inspect context windows |
| `mem_search` | Search extracted knowledge |
| `mem_get_context` | Get relevant memories formatted for prompt injection |

**Admin tools** (when `imperator.admin_tools: true`):

| Tool | Description |
|---|---|
| `config_read` | Read current `config.yml` contents |
| `config_write` | Modify `config.yml` (hot-reloaded on next operation) |
| `db_query` | Run read-only SQL queries against PostgreSQL |

### 10.4 StateGraph Design

The Imperator uses a LangGraph ReAct-style agent with tool calling:

```python
class ImperatorState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    context: Optional[str]         # Assembled context from its own window
    tool_calls_remaining: int      # Max tool calls per turn (default: 4)
```

**Graph Structure:**

```
┌───────────────────┐
│ load_context      │ ─── Retrieve assembled context from Imperator's
│                   │     own context window (conv_retrieve_context)
└────────┬──────────┘
         │
┌────────▼──────────┐
│ invoke_llm        │ ─── Call configured LLM with:
│                   │     - System prompt (identity + purpose)
│                   │     - Assembled context
│                   │     - Tool definitions
│                   │     - Conversation messages
└────────┬──────────┘
         │
    ┌────┴────────────────┐
    │                     │
    ▼                     ▼
(tool_calls?)        (no tool_calls)
    │                     │
┌───▼────────────┐   ┌───▼──────────┐
│ execute_tools  │   │ store_and    │
│ (parallel)     │   │ respond      │
└───┬────────────┘   └──────────────┘
    │                        │
    ▼                       END
┌───────────────┐
│ invoke_llm    │ ─── Loop back with tool results
└───────────────┘     (up to tool_calls_remaining)
```

**LangChain Component Usage:**

```python
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

# Define tools as LangChain tools
@tool
async def conv_search(query: str, user_id: str = None, limit: int = 10) -> dict:
    """Search conversations by semantic query and/or structured filters."""
    return await conversation_ops.search_conversations(query=query, ...)

@tool
async def mem_search(query: str, user_id: str, limit: int = 10) -> dict:
    """Search extracted knowledge (facts, relationships, preferences)."""
    return await memory_ops.mem_search(query=query, user_id=user_id, ...)

# Build the agent
chat_model = _get_chat_model(config)
tools = [conv_search, conv_search_messages, conv_get_history,
         conv_search_context_windows, mem_search, mem_get_context]

if config["imperator"].get("admin_tools"):
    tools.extend([config_read, config_write, db_query])

imperator_agent = create_react_agent(
    model=chat_model,
    tools=tools,
    state_modifier=system_prompt,
)
```

This replaces the hand-rolled JSON-parsing decision loop in the current Rogers Imperator with LangGraph's standard `create_react_agent`, which handles:
- Tool call parsing from the LLM response
- Tool execution and result formatting
- Looping back to the LLM with tool results
- Termination when the LLM responds without tool calls

### 10.5 Chat Endpoint Integration

The `/v1/chat/completions` endpoint maps directly to the Imperator:

```python
@app.route("/v1/chat/completions", methods=["POST"])
async def chat_completions():
    data = await request.get_json()
    messages = data.get("messages", [])
    stream = data.get("stream", False)

    # Store the user message in the Imperator's conversation
    user_msg = messages[-1]  # Last message is the new user turn
    await _message_pipeline.ainvoke({
        "conversation_id": imperator_conversation_id,
        "role": user_msg["role"],
        "sender_id": "user",
        "content": user_msg["content"],
        ...
    })

    # Invoke the Imperator graph
    result = await _imperator_agent.ainvoke({
        "messages": [HumanMessage(content=user_msg["content"])],
    })

    # Store the assistant response
    response_text = result["messages"][-1].content
    await _message_pipeline.ainvoke({
        "conversation_id": imperator_conversation_id,
        "role": "assistant",
        "sender_id": "imperator",
        "content": response_text,
        ...
    })

    # Return in OpenAI format
    if stream:
        return _stream_response(response_text)
    else:
        return _format_completion(response_text)
```

---

## Appendix A: LangChain/LangGraph Component Mapping

This table summarizes where standard LangChain/LangGraph components replace hand-rolled equivalents from the current implementation:

| Domain | Current (Hand-Rolled) | Context Broker (LangChain/LangGraph) |
|---|---|---|
| **Embedding** | `SutherlandEmbedderAdapter` + MCP peer proxy | `langchain_openai.OpenAIEmbeddings` configured via `config.yml` |
| **LLM (summarization)** | `SutherlandLlmAdapter` / `OpenAICompatibleLlmAdapter` via Mem0 adapter pattern | `langchain_openai.ChatOpenAI` configured via `config.yml` |
| **LLM (Imperator)** | JSON-parsing decision loop calling `llm_chat_completions` via MCP | `langgraph.prebuilt.create_react_agent` with standard tool calling |
| **Vector search** | Raw asyncpg queries with pgvector operators | `langchain_postgres.PGVector` retriever for semantic retrieval |
| **Knowledge graph search** | Direct Mem0 `m.search()` calls | Same (Mem0 is the graph engine; LangChain does not replace it) |
| **MCP gateway** | Custom Node.js gateway with 4 shared libraries | nginx configuration file (pure routing) |
| **MCP server** | Custom JSON-RPC dispatch in `server.py` | `mcp` Python SDK or `langchain-mcp-adapters` server |
| **Tool definitions** | `_tool_registry` dict mapping names to functions | `@tool` decorated functions + MCP tool registration |
| **Mem0 LLM/Embedder** | Custom `_LlmBase` / `_EmbeddingBase` subclasses | Mem0's built-in `openai` provider with `openai_base_url` |
| **Queue worker** | Custom Redis polling loops | Same pattern retained (appropriate for the scale) |
| **StateGraph flows** | Already LangGraph StateGraphs | Same pattern, refined with async LangChain model calls |

## Appendix B: Migration Considerations

For deployers migrating from an existing Rogers installation:

1. **Database schema:** The Context Broker schema is compatible with Rogers v3.x data. Key changes:
   - `sender_id` changes from `INTEGER` to `VARCHAR(255)` — requires a migration
   - `flow_id`, `flow_name` columns are removed — data preserved in `metadata` JSONB
   - `TIMESTAMP` → `TIMESTAMPTZ` — requires migration
   - Build types move from the database to `config.yml` — seed data no longer needed

2. **Embeddings:** If the configured embedding model differs from the previous one, existing embeddings will have different dimensions/distributions. A re-embedding migration may be necessary.

3. **Mem0 data:** The Mem0 tables (`mem0_memories`) and Neo4j graph data are portable. The deduplication index is the same.

4. **Redis queues:** Redis data is ephemeral. Any pending jobs will be lost on migration but will be re-triggered as new messages are stored.

---

## Document Metadata

**Version History:**

- v1.0 (2026-03-20): Initial HLD

**Related Documents:**

- `REQ-context-broker.md` — Requirements specification (authoritative target)
- `c1-the-context-broker.md` — Domain model and concept paper
- `d4-agent-optimal-code-architecture.md` — StateGraph-as-application architecture
- `d5-state-4-mad.md` — State 4 configurable dependency pattern
