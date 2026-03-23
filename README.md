# Context Broker

A self-contained context engineering and conversational memory service for LLM agents.

## The Problem

LLM agents reason within finite context windows but participate in conversations that accumulate indefinitely. The Context Broker bridges this gap by managing the infinite conversation and proactively assembling purpose-built **context windows** -- curated views tailored to a specific participant, constructed according to a configured strategy (a **build type**), and strictly bound by a token budget.

The Context Broker stores every message, generates embeddings for hybrid search, extracts knowledge into a graph, and assembles multi-tier context on demand. It exposes its capabilities via MCP tools for programmatic access and an OpenAI-compatible chat endpoint for conversational interaction.

## Quick Start

```bash
# Clone the repository
git clone https://github.com/jmorrissette-rmdc/ContextBroker.git
cd ContextBroker

# Copy configuration templates
cp config/config.example.yml config/config.yml
cp config/credentials/.env.example config/credentials/.env

# Edit config.yml to point at your inference provider (Ollama, OpenAI, etc.)
# Edit .env with any required API keys

# Start all containers
docker compose up -d

# Verify health
curl http://localhost:8080/health
```

The default configuration uses the optional Ollama container for LLM inference and the Infinity container for embeddings and reranking — fully local, no API keys needed. To use cloud providers instead, update the `llm`, `embeddings`, and `reranker` sections in `config/config.yml` and set the corresponding API keys in `config/credentials/.env`.

### Connecting

- **MCP**: Point any MCP client at `http://localhost:8080/mcp`
- **Chat UI**: Point any OpenAI-compatible client at `http://localhost:8080/v1/chat/completions`
- **Health**: `GET http://localhost:8080/health`

## Configuration Reference

All configuration lives in a single file: `config/config.yml`. Inference provider settings and build types are hot-reloadable (no restart needed). Infrastructure settings (database) require a container restart.

### `log_level`

Log verbosity. One of `DEBUG`, `INFO`, `WARN`, `ERROR`. Default: `INFO`.

### `llm`

Primary LLM provider for summarization, extraction, and the Imperator agent.

| Field | Description |
|-------|-------------|
| `base_url` | OpenAI-compatible API endpoint |
| `model` | Model name |
| | API keys: set the standard platform env var (e.g., `OPENAI_API_KEY`) in `config/credentials/.env`. LangChain reads it automatically. No config needed for keyless providers like Ollama. |

### `embeddings`

Embedding provider for vector search and contextual embeddings.

| Field | Description |
|-------|-------------|
| `base_url` | OpenAI-compatible API endpoint |
| `model` | Embedding model name |
| | API keys: set the standard platform env var (e.g., `OPENAI_API_KEY`) in `config/credentials/.env`. LangChain reads it automatically. |
| `context_window_size` | Number of prior messages to include as context prefix when embedding (default: 3) |

### `reranker`

API-based reranker for hybrid search result refinement. Default: local Infinity container.

| Field | Description |
|-------|-------------|
| `provider` | `"api"` (hits `/v1/rerank` endpoint — works with Infinity, Together, Cohere, Jina, Voyage) or `"none"` to disable |
| `base_url` | Reranker API endpoint (default: `http://context-broker-infinity:7997`) |
| `model` | Model name (default: `mixedbread-ai/mxbai-rerank-xsmall-v1`) |
| `top_n` | Number of top results to return after reranking (default: 10) |

### `build_types`

Defines context assembly strategies. Each named build type specifies tier percentages, token budgets, and optional retrieval layers. See [Build Types](#build-types) below for details.

### `imperator`

Configuration for the built-in conversational agent.

| Field | Description |
|-------|-------------|
| `build_type` | Which build type the Imperator's context window uses |
| `max_context_tokens` | `"auto"` or explicit integer |
| `participant_id` | Participant ID for the Imperator's context window |
| `admin_tools` | `false` = read-only; `true` = can modify config and query DB |

### `packages`

Controls where Python StateGraph packages are installed from at build time.

| Field | Description |
|-------|-------------|
| `source` | `"local"`, `"pypi"`, or `"devpi"` |
| `local_path` | Path to local wheel files (used when source is `"local"`) |
| `devpi_url` | Private devpi index URL (used when source is `"devpi"`) |

### `tuning`

Operational tuning parameters. All are hot-reloadable.

| Field | Description |
|-------|-------------|
| `verbose_logging` | Enable detailed pipeline node timing logs |
| `assembly_lock_ttl_seconds` | TTL for Redis assembly locks (default: 300) |
| `chunk_size` | Messages per chunk for tier 2 summarization (default: 20) |
| `consolidation_threshold` | Tier 2 count before consolidating to tier 1 (default: 3) |
| `consolidation_keep_recent` | Tier 2 summaries to keep after consolidation (default: 2) |
| `summarization_temperature` | LLM temperature for summarization (default: 0.1) |
| `trigger_threshold_percent` | Min % of token budget change before re-assembly (default: 0.1) |
| `extraction_lock_ttl_seconds` | TTL for memory extraction locks (default: 180) |
| `extraction_max_chars` | Max characters sent to memory extraction (default: 90000) |
| `memory_half_lives` | Decay rates by memory category (ephemeral, contextual, factual, historical) |
| `assembly_wait_timeout_seconds` | How long retrieval waits for in-progress assembly (default: 50) |
| `assembly_poll_interval_seconds` | Poll interval during assembly wait (default: 2) |
| `tokens_per_message_estimate` | Estimated tokens per message for adaptive load limits (default: 150) |
| `content_truncation_chars` | Max characters for content in search results (default: 500) |
| `query_truncation_chars` | Max characters for query text (default: 200) |
| `rrf_constant` | Reciprocal Rank Fusion constant k (default: 60) |
| `search_candidate_limit` | Max candidates per search retrieval method (default: 100) |
| `recency_decay_days` | Messages older than this get a score penalty (default: 90) |
| `recency_max_penalty` | Max recency score penalty as a fraction (default: 0.2) |
| `imperator_max_iterations` | Max ReAct loop iterations for the Imperator (default: 5) |
| `imperator_temperature` | LLM temperature for Imperator responses (default: 0.3) |
| `worker_poll_interval_seconds` | ARQ worker poll interval (default: 2) |
| `max_retries` | Max retries for failed background jobs (default: 3) |
| `dead_letter_sweep_interval_seconds` | Interval for dead-letter queue sweep (default: 60) |

### `database`

Infrastructure settings (requires restart).

| Field | Description |
|-------|-------------|
| `pool_min_size` | Minimum connection pool size (default: 2) |
| `pool_max_size` | Maximum connection pool size (default: 10) |

## MCP Tool Reference

All tools are accessible via MCP (HTTP/SSE) at `/mcp`. Tool schemas are also discoverable via the MCP `tools/list` method.

### `conv_create_conversation`

Create a new conversation.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `conversation_id` | string (uuid) | No | Caller-supplied ID for idempotent creation |
| `title` | string | No | Conversation title |
| `flow_id` | string | No | Flow identifier |
| `user_id` | string | No | User identifier |

**Output:** Conversation record with `id`, `title`, `created_at`.

### `conv_store_message`

Store a message in a conversation. Triggers async embedding, context assembly, and knowledge extraction.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `context_window_id` | string (uuid) | Yes | Target context window |
| `role` | string | Yes | One of `user`, `assistant`, `system`, `tool` |
| `sender` | string | Yes | Sender identifier |
| `recipient` | string | No | Recipient identifier |
| `content` | string | No | Message content |
| `priority` | integer | No | Priority for extraction ordering (default: 0) |
| `model_name` | string | No | Model that generated this message |
| `tool_calls` | object | No | Tool call data |
| `tool_call_id` | string | No | Tool call ID for tool-role messages |

**Output:** Message record with `id`, `sequence_number`, `created_at`.

### `conv_retrieve_context`

Retrieve the assembled context window for a participant.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `context_window_id` | string (uuid) | Yes | Context window to retrieve |

**Output:** `context_messages` (OpenAI-format messages array), `context_tiers` (breakdown by tier), `total_tokens_used`, `warnings`.

### `conv_create_context_window`

Create a context window instance with a build type and token budget.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `conversation_id` | string (uuid) | Yes | Conversation to attach to |
| `participant_id` | string | Yes | Participant identifier |
| `build_type` | string | Yes | Build type name (e.g., `standard-tiered`) |
| `max_tokens` | integer | No | Override the build type's default token budget |

**Output:** Context window record with `id`, `build_type`, `max_token_budget`.

### `conv_search`

Semantic and structured search across conversations.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | No | Semantic search query |
| `limit` | integer | No | Max results (default: 10) |
| `offset` | integer | No | Pagination offset (default: 0) |
| `date_from` | string | No | ISO-8601 date lower bound |
| `date_to` | string | No | ISO-8601 date upper bound |
| `flow_id` | string | No | Filter by flow identifier |
| `user_id` | string | No | Filter by user identifier |
| `sender` | string | No | Filter by sender |

**Output:** Array of conversation records with `id`, `title`, `created_at`, `total_messages`, and optional `relevance_score`.

### `conv_search_messages`

Hybrid search (vector + BM25 + API-based reranking) across messages.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | Yes | Search query |
| `conversation_id` | string (uuid) | No | Scope to a specific conversation |
| `sender` | string | No | Filter by sender |
| `role` | string | No | Filter by role |
| `date_from` | string | No | ISO-8601 date lower bound |
| `date_to` | string | No | ISO-8601 date upper bound |
| `limit` | integer | No | Max results (default: 10) |

**Output:** Array of message records with `id`, `conversation_id`, `role`, `sender`, `content`, `score`.

### `conv_get_history`

Retrieve full chronological message sequence for a conversation.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `conversation_id` | string (uuid) | Yes | Conversation ID |
| `limit` | integer | No | Max messages to return |

**Output:** Array of messages in chronological order.

### `conv_search_context_windows`

Search and list context windows.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `context_window_id` | string (uuid) | No | Look up a specific context window |
| `conversation_id` | string (uuid) | No | Filter by conversation |
| `participant_id` | string | No | Filter by participant |
| `build_type` | string | No | Filter by build type |
| `limit` | integer | No | Max results (default: 10) |

**Output:** Array of context window records.

### `mem_search`

Semantic and graph search across extracted knowledge (Mem0/Neo4j).

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | Yes | Search query |
| `user_id` | string | Yes | User scope |
| `limit` | integer | No | Max results (default: 10) |

**Output:** Array of memory records with content and metadata.

### `mem_get_context`

Retrieve relevant memories formatted for prompt injection.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | Yes | Query to match relevant memories |
| `user_id` | string | Yes | User scope |
| `limit` | integer | No | Max memories (default: 5) |

**Output:** Formatted string of relevant memories suitable for system prompt injection.

### `mem_add`

Directly add a memory to the knowledge graph.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | string | Yes | Memory content |
| `user_id` | string | Yes | User scope |

**Output:** Confirmation with memory ID.

### `mem_list`

List all memories for a user.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | Yes | User scope |
| `limit` | integer | No | Max results (default: 50) |

**Output:** Array of memory records.

### `mem_delete`

Delete a specific memory by ID.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `memory_id` | string | Yes | Memory ID to delete |

**Output:** Confirmation of deletion.

### `imperator_chat`

Conversational interface to the Imperator (the Context Broker's built-in agent).

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | Yes | Message to send |
| `conversation_id` | string (uuid) | No | Conversation ID (uses Imperator's persistent conversation if omitted) |

**Output:** Imperator's response text.

### `metrics_get`

Retrieve Prometheus metrics.

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| (none) | | | |

**Output:** Prometheus exposition format metrics text.

## OpenAI-Compatible Chat Endpoint

The Context Broker exposes `/v1/chat/completions` following the OpenAI API specification. Any OpenAI-compatible client or chat UI can connect without modification.

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "context-broker",
    "messages": [{"role": "user", "content": "What conversations do you remember?"}],
    "stream": true
  }'
```

- Accepts `model`, `messages`, `stream`, and standard generation parameters.
- Streaming responses use SSE format (`data: {...}\n\n`, `data: [DONE]\n\n`).
- All messages are routed to the Imperator, which uses the same conversation storage, context assembly, and knowledge extraction as the MCP tools.

## Architecture Overview

### Container Roles

The Context Broker runs as a Docker Compose group of containers communicating over an internal bridge network:

| Container | Role | Image |
|-----------|------|-------|
| `context-broker` | Nginx gateway. Sole network boundary. Routes MCP, chat, and health traffic. | `nginx:1.25.3-alpine` |
| `context-broker-langgraph` | All application logic: StateGraph flows, async queue workers, Imperator agent. | Custom (Python 3.12) |
| `context-broker-postgres` | Conversation storage, vector embeddings (pgvector), context windows, summaries. | `pgvector/pgvector:0.7.0-pg16` |
| `context-broker-neo4j` | Knowledge graph storage via Mem0. Entity nodes, fact nodes, relationships. | `neo4j:5.15.0` |
| `context-broker-redis` | Async job queues (ARQ), assembly locks, ephemeral state. | `redis:7.2.3-alpine` |
| `context-broker-infinity` | (Optional) Local embeddings and reranking via OpenAI-compatible APIs. CPU only, torch engine. Remove if using cloud providers. | `michaelf34/infinity:0.0.77` |
| `context-broker-ollama` | (Optional) Local LLM inference via Ollama. No API keys needed. Remove if using cloud providers. | `ollama/ollama:0.6.2` |

Only the LangGraph container is custom-built. All backing services use official images unmodified.

### Network Topology

- **External network** (`default`): Only the Nginx gateway connects here. Exposes port 8080 to the host.
- **Internal network** (`context-broker-net`): Private bridge for all containers. The LangGraph container and backing services are only accessible on this network.

### StateGraph Flows

All programmatic and cognitive logic is implemented as LangGraph StateGraphs. The ASGI server acts purely as a transport layer.

**Action Engine (AE) flows** -- infrastructure, stable:
- Message pipeline (ingestion, deduplication, storage)
- Embed pipeline (background embedding generation)
- Context assembly (background progressive compression)
- Retrieval (assembles final context from pre-computed tiers)
- Memory extraction (background Mem0/Neo4j knowledge extraction)
- Hybrid search (vector + BM25 + RRF + reranking)
- Database queries (structured filtering)
- Memory search (Mem0 knowledge graph queries)
- Metrics (Prometheus exposition)

**Thought Engine (TE) flow** -- cognitive, evolves independently:
- Imperator (ReAct-style agent loop with tool access and LangGraph checkpointing)

### context_window_id = thread_id (ARCH-16)

A `context_window_id` is the conceptual equivalent of a `thread_id`. It uniquely identifies a participant's view of a conversation. Multiple participants in the same conversation each get their own context window, each with its own build type, token budget, and assembled context. When an external system references a "thread," that maps directly to a context window in the Context Broker.

## Build Types

A **build type** defines the strategy for assembling a context window. Build types are configured in `config.yml` and are open-ended -- you can define as many as needed.

### Shipped Build Types

#### `passthrough`

No summarization, no LLM calls. Loads recent messages as-is up to the token budget. Useful for testing, debugging, or simple integrations that do not need progressive compression.

```yaml
passthrough:
  max_context_tokens: auto
  fallback_tokens: 8192
```

#### `standard-tiered`

Episodic memory only. Three-tier progressive compression:

- **Tier 1** (`tier1_pct: 0.08`): Archival summary -- oldest messages, most compressed. A single rolling summary of the entire deep history.
- **Tier 2** (`tier2_pct: 0.20`): Chunk summaries -- groups of older messages summarized into paragraph-length chunks.
- **Tier 3** (`tier3_pct: 0.72`): Recent verbatim messages -- newest messages at full fidelity.

No vector search, no knowledge graph. Lower inference cost. Suitable as the default.

```yaml
standard-tiered:
  tier1_pct: 0.08
  tier2_pct: 0.20
  tier3_pct: 0.72
  max_context_tokens: auto
  fallback_tokens: 8192
```

#### `knowledge-enriched`

Full retrieval pipeline. Three episodic tiers plus two additional retrieval layers:

- **Tier 1** (`tier1_pct: 0.05`): Archival summary.
- **Tier 2** (`tier2_pct: 0.15`): Chunk summaries.
- **Tier 3** (`tier3_pct: 0.50`): Recent verbatim messages.
- **Semantic retrieval** (`semantic_retrieval_pct: 0.15`): Past messages retrieved via vector similarity (pgvector) that are relevant to the current topic but outside the tier 3 window.
- **Knowledge graph** (`knowledge_graph_pct: 0.15`): Structured facts and entity relationships extracted from conversations, retrieved via Mem0/Neo4j graph traversal.

```yaml
knowledge-enriched:
  tier1_pct: 0.05
  tier2_pct: 0.15
  tier3_pct: 0.50
  knowledge_graph_pct: 0.15
  semantic_retrieval_pct: 0.15
  max_context_tokens: auto
  fallback_tokens: 16000
```

### Adding a New Build Type

The simplest path is to copy the passthrough build type and extend it. Each build type needs:

1. **A config entry** in `config.yml` under `build_types`.
2. **A Python module** in `app/flows/build_types/` that defines assembly and retrieval StateGraphs.
3. **Registration** via `register_build_type()` at module import time.
4. **An import** in `app/flows/build_types/__init__.py` to trigger registration.

**Step-by-step using passthrough as a starting point:**

1. Copy `app/flows/build_types/passthrough.py` to `app/flows/build_types/my_build_type.py`.

2. Define your assembly and retrieval StateGraph state classes. Your graphs must accept the standard input contracts (`AssemblyInput` / `RetrievalInput` from `app/flows/contracts.py`) and produce the standard output contracts (`AssemblyOutput` / `RetrievalOutput`):

   ```python
   # AssemblyInput: context_window_id, conversation_id, config
   # AssemblyOutput: error (optional)
   # RetrievalInput: context_window_id, config
   # RetrievalOutput: context_messages, context_tiers, total_tokens_used, warnings, error
   ```

3. Implement your assembly graph (what happens after a message is stored) and retrieval graph (what happens when context is requested). Each must be a function that returns a compiled `StateGraph`.

4. Register at the bottom of your module:

   ```python
   from app.flows.build_type_registry import register_build_type
   register_build_type("my-build-type", build_my_assembly, build_my_retrieval)
   ```

5. Add the import to `app/flows/build_types/__init__.py`:

   ```python
   import app.flows.build_types.my_build_type  # noqa: F401
   ```

6. Add the config entry to `config.yml`:

   ```yaml
   build_types:
     my-build-type:
       max_context_tokens: auto
       fallback_tokens: 8192
       # your custom fields here
   ```

The registry (`app/flows/build_type_registry.py`) lazily compiles graphs on first use and caches them. The assembly and retrieval graphs for your build type are fully independent -- they share no state beyond what the standard contracts define.

## Modifying StateGraph Flows

All application logic lives in StateGraph flows under `app/flows/`. To modify a flow:

1. **Locate the flow module.** Build-type-specific flows are in `app/flows/build_types/`. Cross-cutting flows (search, embedding, memory) are directly in `app/flows/`.

2. **Understand the state contract.** Each flow defines a `TypedDict` state class. Node functions receive the full state and return a dict containing only the keys they update. Node functions must not modify input state in-place.

3. **Add or modify nodes.** Each node is an async function that takes the state and returns a partial state update dict:

   ```python
   async def my_node(state: MyFlowState) -> dict:
       # Read from state
       value = state["some_key"]
       # Return only updated keys
       return {"result_key": computed_value}
   ```

4. **Wire the graph.** Use `workflow.add_node()`, `workflow.add_edge()`, and `workflow.add_conditional_edges()` to define the execution flow. Conditional edges use routing functions that return the name of the next node.

5. **Compile and test.** The graph builder function returns `workflow.compile()`. The compiled graph is invoked via `await graph.ainvoke(input_state)`.

Configuration is read fresh on each invocation via `load_config()`, so config changes take effect without restarting the service or recompiling graphs.
