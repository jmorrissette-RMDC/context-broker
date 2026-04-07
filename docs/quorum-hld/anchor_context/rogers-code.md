# Flattened Repository: rogers

Root: `C:\Users\j\projects\Joshua26\mads\rogers`  
Files included: 33  
Approximate tokens: ~51,267  

---

## README.md

```markdown
# Rogers — Conversation & Memory Service

**Host:** M5 | **Port:** 6380 | **UID:** 2002 | **Network:** joshua-net + rogers-net

Rogers is the unified conversation management service for the Joshua26 ecosystem.
It provides conversation message storage, token-aware three-tier context retrieval,
and Mem0-based knowledge graph memory via a 5-container MAD group.

Replaces: `conversation-watcher → rogers-redis → Henson → Codd`

---

## Containers

| Container | Type | Port |
|---|---|---|
| `rogers` | MCP gateway (Node.js) | 6380 (external) |
| `rogers-langgraph` | Python backend (Quart + LangGraph StateGraph) | 8000 (internal) |
| `rogers-postgres` | PostgreSQL 16 + pgvector | 5432 (internal) |
| `rogers-neo4j` | Neo4j 5 + APOC | 7687 (internal) |
| `rogers-redis` | Redis 7 | 6379 (internal) |

---

## MCP Tools (10 client-facing)

### Conversation (`conv_`)

| Tool | Description |
|---|---|
| `conv_create_conversation` | Create a new conversation, returns `conversation_id` |
| `conv_create_context_window` | Create a context window instance for a build type and token limit |
| `conv_store_message` | Store a single message; triggers full pipeline (dedup, embed, context assembly, memory extraction) |
| `conv_retrieve_context` | Get assembled three-tier context for a context window. Blocks if assembly in progress |
| `conv_search` | General-purpose conversation search: semantic query + structured filters (flow_id, user_id, sender_id, date range) |
| `conv_search_messages` | Search within messages: two-stage hybrid search (vector ANN + BM25 RRF top 50 → cross-encoder reranker top 10) + structured filters (conversation_id, sender_id, role, date range) |
| `conv_get_history` | Get full message sequence for a conversation in chronological order |
| `conv_search_context_windows` | Search/list/get context windows by ID, conversation, or build type |

### Memory (`mem_`)

| Tool | Description |
|---|---|
| `mem_search` | Semantic + graph search across extracted knowledge (mem0). What is known vs what was said |
| `mem_get_context` | Get relevant memories formatted for prompt injection |

4 additional internal tools (`mem_add`, `mem_list`, `mem_delete`, `mem_extract`) are accessible as HTTP endpoints but not exposed via MCP. They are used by the background queue worker and for admin purposes.

---

## Architecture

```
Client → rogers:6380 (MCP gateway, joshua-net + rogers-net)
              ↓
         rogers-langgraph:8000 (Quart + LangGraph StateGraph, rogers-net only)
              ↓
    ┌─────────────────────────────────────┐
    │ rogers-postgres (pgvector + schema) │
    │ rogers-neo4j   (entity graph)       │
    │ rogers-redis   (queue + cache)      │
    └─────────────────────────────────────┘

External peer calls (ADR-053):
    rogers-langgraph → rogers:6380/peer/sutherland/llm_embeddings → Sutherland (m5:11435)
    rogers-langgraph → rogers:6380/peer/sutherland/llm_chat_completions → Sutherland (m5:11435)
    rogers-langgraph → rogers:6380/peer/sutherland/llm_rerank → Sutherland (m5:11435)
```

### Data Model

- **conversations** — top-level entity with flow_id, title, participants derived from messages
- **conversation_messages** — one row per message (role, sender_id, content, embedding, sequence_number)
- **context_window_build_types** — strategy templates (small-basic, standard-tiered)
- **context_windows** — per-participant instances with build type + token limit
- **conversation_summaries** — tiered summaries keyed to context windows (tier 1 = archival, tier 2 = chunk)

### Message Pipeline (conv_store_message)

`conv_store_message` always succeeds immediately. Enrichment is deferred via Redis job queues:

1. **Dedup check** — first step; skips duplicate consecutive messages from same sender
2. **Embedding** — Sutherland `llm_embeddings` → contextual embedding using N prior messages as prefix (config: `embedding.context_window_size`, default 3) → stores 768-dim vector in postgres
3. **Context assembly** — checks all context windows on the conversation; queues three-tier assembly when threshold crossed. LLM selected by build type: Sutherland (local, small-basic) or Gemini Flash-Lite (API, standard-tiered)
4. **Memory extraction** — Mem0 `m.add()` → Neo4j entity graph + pgvector

Steps 3 and 4 queue **in parallel** after embedding completes.

If Sutherland is unavailable, jobs queue in Redis with 3-retry exponential
backoff. Dead-letter queue (`dead_letter_jobs`) captures persistent failures with periodic
sweep (every 60s, re-queues up to 10 jobs).

### Context Retrieval (conv_retrieve_context)

Three-tier context assembly within a context window's token budget:

- **Tier 1** — Archival summary (oldest content, most compressed)
- **Tier 2** — Chunk summaries (intermediate age)
- **Tier 3** — Recent messages verbatim (newest, within remaining budget)

If assembly is in progress when context is requested, the call blocks and waits (up to 50s timeout, under the gateway's 60s routing timeout).

---

## Pre-Deployment Requirements

**Credentials (on M5 before first deploy):**
```bash
# PostgreSQL (bare password)
echo "<password>" > /storage/credentials/rogers/postgres.txt

# Neo4j (TWO keys required: one for the container, one for server.py)
printf "NEO4J_AUTH=neo4j/<password>\nNEO4J_PASSWORD=<password>\n" \
  > /storage/credentials/rogers/neo4j.txt

# Gemini API key (for standard-tiered context assembly)
echo "<api-key>" > /storage/credentials/rogers/gemini_api_key.txt
```

**Data directories (on M5 before first deploy):**
```bash
mkdir -p /mnt/ssd1/workspace/rogers/databases/postgres/data
mkdir -p /mnt/ssd1/workspace/rogers/databases/neo4j/{data,logs}
mkdir -p /mnt/ssd1/workspace/rogers/databases/redis/data
```

**Offline packages (before building images):**
```bash
cd mads/rogers/rogers/packages && bash download-packages.sh
cd mads/rogers/rogers-langgraph/packages && bash download-packages.sh
```

---

## Verification

```bash
# Gateway health
curl -s http://m5:6380/health | jq .

# Container status
docker ps --filter "label=mad.logical_actor=rogers"

# Logs
docker logs rogers --tail 50
docker logs rogers-langgraph --tail 50

# Test tools directly via langgraph backend
docker exec rogers-langgraph python3 -c "
import urllib.request, json
req = urllib.request.Request('http://localhost:8000/conv_create_conversation',
  data=json.dumps({'params':{'flow_id':'test-flow','title':'Test Conversation'}}).encode(),
  headers={'Content-Type':'application/json'}, method='POST')
print(json.loads(urllib.request.urlopen(req).read()))
"

docker exec rogers-langgraph python3 -c "
import urllib.request, json
req = urllib.request.Request('http://localhost:8000/conv_get_history',
  data=json.dumps({'params':{'conversation_id':'<conversation_id>'}}).encode(),
  headers={'Content-Type':'application/json'}, method='POST')
print(json.loads(urllib.request.urlopen(req).read()))
"
```

---

## Deployment Status

**Status:** v3.1 deployed and operational
**Deployed:** 2026-02-25 (Phase 1 search quality upgrade)
**Host:** M5 (192.168.1.120)

**v3.1 changes (2026-02-25):** Phase 1 search quality. Contextual embeddings: 3-message prefix window for all conversation message embeds. Hybrid RRF retrieval: vector ANN + BM25 full-text combined via Reciprocal Rank Fusion with recency bias (top 50 candidates). Cross-encoder reranker: bge-reranker-v2-m3 via Sutherland `llm_rerank` (top 10 final results). Added `content_tsv` GIN index to postgres. All 83,588 messages re-embedded through contextual pipeline.

**v3.0 changes (2026-02-22):** Complete schema redesign (paired turns → single messages), context window architecture, three-tier context assembly, 10 client-facing MCP tools (was 12), LangGraph StateGraph flows, Quart async backend. Full pipeline verified: embedding via Sutherland, memory extraction via Sutherland/Qwen, Neo4j graph relations. Gate 3: 46 passed, 0 failed.

---

## Design Documents

- `docs/REQ-rogers.md` — delta requirements and full design
- `docs/architecture.md` — design rationale and history
- `docs/rogers-plan.md` — complete build log (design → implementation → deployment)

**References:**
- ADR-046: MAD Group (Bounded Context) pattern
- ADR-053: Bidirectional Gateway Peer Proxy
- REQ-000 §7.4.1: Network isolation

```

## docker-compose.yml

```yaml
# NOTE: This file is for local development/testing only.
# The active production configuration is in the root docker-compose.yml.
# All production changes must be made there, not here.
#
# Usage (local test):
#   docker compose -f mads/rogers/docker-compose.yml up -d
#
# Prerequisites:
#   mkdir -p /mnt/nvme/workspace/rogers/databases/postgres/data
#   mkdir -p /mnt/nvme/workspace/rogers/databases/neo4j/{data,logs}
#   mkdir -p /mnt/nvme/workspace/rogers/databases/redis/data
#   echo "devpassword" > /storage/credentials/rogers/postgres.txt
#   printf "NEO4J_AUTH=neo4j/devpassword\nNEO4J_PASSWORD=devpassword\n" > /storage/credentials/rogers/neo4j.txt

services:

  rogers:
    build:
      context: ../..
      dockerfile: mads/rogers/rogers/Dockerfile
    image: joshua26/rogers
    container_name: rogers
    hostname: rogers
    restart: unless-stopped
    networks:
      - joshua-net
      - rogers-net
    volumes:
      - storage:/storage
      - workspace:/workspace
    ports:
      - "6380:6380"
    environment:
      - MCP_PORT=6380
    labels:
      - mad.logical_actor=rogers
      - mad.component=mcp
    healthcheck:
      test: ["CMD", "node", "-e", "fetch('http://localhost:6380/health').then(r => r.ok ? process.exit(0) : process.exit(1)).catch(() => process.exit(1))"]
      interval: 30s
      timeout: 10s
      start_period: 15s
      retries: 3
    depends_on:
      rogers-langgraph:
        condition: service_healthy

  rogers-langgraph:
    build:
      context: ../..
      dockerfile: mads/rogers/rogers-langgraph/Dockerfile
    image: joshua26/rogers-langgraph
    container_name: rogers-langgraph
    hostname: rogers-langgraph
    user: "2002:2001"
    restart: unless-stopped
    networks:
      - rogers-net
    volumes:
      - storage:/storage
      - workspace:/workspace
    environment:
      - POSTGRES_HOST=rogers-postgres
      - POSTGRES_PORT=5432
      - POSTGRES_DB=rogers
      - POSTGRES_USER=rogers
      - REDIS_HOST=rogers-redis
      - REDIS_PORT=6379
      - NEO4J_HOST=rogers-neo4j
      - GATEWAY_URL=http://rogers:6380
      - QUEUE_POLL_INTERVAL=5
      - MEM0_TELEMETRY=False
    labels:
      - mad.logical_actor=rogers
      - mad.component=langgraph
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 30s
      timeout: 10s
      start_period: 30s
      retries: 3
    depends_on:
      rogers-postgres:
        condition: service_healthy
      rogers-redis:
        condition: service_healthy
      rogers-neo4j:
        condition: service_healthy

  rogers-postgres:
    build:
      context: ../..
      dockerfile: mads/rogers/rogers-postgres/Dockerfile
    image: joshua26/rogers-postgres
    container_name: rogers-postgres
    hostname: rogers-postgres
    restart: unless-stopped
    networks:
      - rogers-net
    volumes:
      - storage:/storage
      - /mnt/nvme/workspace/rogers/databases/postgres/data:/var/lib/postgresql/data
    environment:
      - POSTGRES_DB=rogers
      - POSTGRES_USER=rogers
      - POSTGRES_PASSWORD_FILE=/storage/credentials/rogers/postgres.txt
    labels:
      - mad.logical_actor=rogers
      - mad.component=postgres
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "rogers", "-d", "rogers"]
      interval: 30s
      timeout: 10s
      start_period: 30s
      retries: 3

  rogers-neo4j:
    image: neo4j:5
    container_name: rogers-neo4j
    hostname: rogers-neo4j
    restart: unless-stopped
    networks:
      - rogers-net
    volumes:
      - storage:/storage
      - /mnt/nvme/workspace/rogers/databases/neo4j/data:/data
      - /mnt/nvme/workspace/rogers/databases/neo4j/logs:/logs
    env_file:
      - /storage/credentials/rogers/neo4j.txt
    environment:
      - NEO4J_PLUGINS=["apoc"]
      - NEO4J_server_memory_heap_initial__size=512m
      - NEO4J_server_memory_heap_max__size=1G
    labels:
      - mad.logical_actor=rogers
      - mad.component=neo4j
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:7474/"]
      interval: 30s
      timeout: 10s
      start_period: 60s
      retries: 3

  rogers-redis:
    image: redis:7-alpine
    container_name: rogers-redis
    hostname: rogers-redis
    restart: unless-stopped
    networks:
      - rogers-net
    volumes:
      - storage:/storage
      - /mnt/nvme/workspace/rogers/databases/redis/data:/data
    command: redis-server --appendonly yes --dir /data
    labels:
      - mad.logical_actor=rogers
      - mad.component=redis
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 5s
      start_period: 10s
      retries: 3

networks:
  joshua-net:
    external: true
  rogers-net:
    driver: bridge

volumes:
  storage:
    external: true
  workspace:
    external: true

```

## rogers/Dockerfile

```dockerfile
# rogers — MCP gateway container
# State 1 MAD: configuration-driven gateway using 4 shared libraries
#
# Build context: project root (.)
# Build: docker compose build rogers

FROM node:20-slim@sha256:721fb2bf28abf97fe5e59dd2660a9b30edeb746f0952c261500ef08030e1385a

ARG USER_NAME=rogers
ARG USER_UID=2002
ARG USER_GID=2001

# Root phase: create group (if not exists) and user
RUN groupadd --gid ${USER_GID} administrators 2>/dev/null || true \
    && useradd --uid ${USER_UID} --gid ${USER_GID} --shell /bin/bash --create-home ${USER_NAME}

USER ${USER_NAME}
WORKDIR /app

# Copy the 4 gateway libraries (needed for npm ci with file: protocol)
COPY --chown=${USER_NAME}:administrators lib/logging-lib/package.json lib/logging-lib/package-lock.json /app/lib/logging-lib/
COPY --chown=${USER_NAME}:administrators lib/logging-lib/index.js /app/lib/logging-lib/
COPY --chown=${USER_NAME}:administrators lib/logging-lib/lib/ /app/lib/logging-lib/lib/

COPY --chown=${USER_NAME}:administrators lib/routing-lib/package.json lib/routing-lib/package-lock.json /app/lib/routing-lib/
COPY --chown=${USER_NAME}:administrators lib/routing-lib/index.js /app/lib/routing-lib/
COPY --chown=${USER_NAME}:administrators lib/routing-lib/lib/ /app/lib/routing-lib/lib/

COPY --chown=${USER_NAME}:administrators lib/health-aggregator-lib/package.json lib/health-aggregator-lib/package-lock.json /app/lib/health-aggregator-lib/
COPY --chown=${USER_NAME}:administrators lib/health-aggregator-lib/index.js /app/lib/health-aggregator-lib/
COPY --chown=${USER_NAME}:administrators lib/health-aggregator-lib/lib/ /app/lib/health-aggregator-lib/lib/

COPY --chown=${USER_NAME}:administrators lib/mcp-protocol-lib/package.json lib/mcp-protocol-lib/package-lock.json /app/lib/mcp-protocol-lib/
COPY --chown=${USER_NAME}:administrators lib/mcp-protocol-lib/index.js /app/lib/mcp-protocol-lib/
COPY --chown=${USER_NAME}:administrators lib/mcp-protocol-lib/lib/ /app/lib/mcp-protocol-lib/lib/

# Install each lib's own dependencies (in dependency order)
WORKDIR /app/lib/routing-lib
RUN npm install --omit=dev --prefer-offline

WORKDIR /app/lib/health-aggregator-lib
RUN npm install --omit=dev --prefer-offline

WORKDIR /app/lib/mcp-protocol-lib
RUN npm install --omit=dev --prefer-offline

# Copy gateway package manifest and install dependencies
COPY --chown=${USER_NAME}:administrators mads/rogers/rogers/package.json mads/rogers/rogers/package-lock.json /app/mads/rogers/rogers/
WORKDIR /app/mads/rogers/rogers

# Copy npm cache for offline install (ADR-037)
COPY --chown=${USER_NAME}:administrators mads/rogers/rogers/packages/ /tmp/packages/

RUN npm ci --cache /tmp/packages/ --prefer-offline

# Copy application files
COPY --chown=${USER_NAME}:administrators mads/rogers/rogers/server.js ./
COPY --chown=${USER_NAME}:administrators mads/rogers/rogers/config.json ./

EXPOSE 6380

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://localhost:6380/health').then(r=>r.ok?process.exit(0):process.exit(1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]

```

## rogers/config.json

```json
{
  "name": "rogers",
  "port": 6380,
  "tools": {
    "conv_create_conversation": {
      "description": "Create a new conversation, returns conversation_id",
      "target": "rogers-langgraph:8000",
      "endpoint": "/conv_create_conversation"
    },
    "conv_create_context_window": {
      "description": "Create a context window instance for a build type and token limit",
      "target": "rogers-langgraph:8000",
      "endpoint": "/conv_create_context_window"
    },
    "conv_store_message": {
      "description": "Store a single message; triggers full pipeline (dedup, embed, context assembly, memory extraction)",
      "target": "rogers-langgraph:8000",
      "endpoint": "/conv_store_message"
    },
    "conv_retrieve_context": {
      "description": "Get assembled three-tier context for a context window. Blocks if assembly in progress",
      "target": "rogers-langgraph:8000",
      "endpoint": "/conv_retrieve_context"
    },
    "conv_search": {
      "description": "General-purpose conversation search: semantic query against message embeddings grouped by conversation, plus structured filters (flow_id, user_id, sender_id, external_session_id, date range)",
      "target": "rogers-langgraph:8000",
      "endpoint": "/conv_search"
    },
    "conv_search_messages": {
      "description": "Search within messages: semantic query against embeddings plus structured filters (conversation_id, sender_id, role, date range)",
      "target": "rogers-langgraph:8000",
      "endpoint": "/conv_search_messages"
    },
    "conv_get_history": {
      "description": "Get full message sequence for a conversation in chronological order",
      "target": "rogers-langgraph:8000",
      "endpoint": "/conv_get_history"
    },
    "conv_search_context_windows": {
      "description": "Search/list/get context windows by ID, conversation, or build type",
      "target": "rogers-langgraph:8000",
      "endpoint": "/conv_search_context_windows"
    },
    "mem_search": {
      "description": "Semantic and graph search across extracted knowledge (mem0). Different from message search: what is known vs what was said",
      "target": "rogers-langgraph:8000",
      "endpoint": "/mem_search"
    },
    "mem_get_context": {
      "description": "Get relevant memories formatted for prompt injection",
      "target": "rogers-langgraph:8000",
      "endpoint": "/mem_get_context"
    },
    "rogers_stats": {
      "description": "Internal: DB counts and queue depths for Apgar health monitoring",
      "target": "rogers-langgraph:8000",
      "endpoint": "/stats",
      "schema": {"input": {}}
    }
  },
  "peers": {
    "sutherland": { "host": "sutherland", "port": 11435 }
  },
  "dependencies": [
    {
      "name": "rogers-langgraph",
      "host": "rogers-langgraph",
      "port": 8000,
      "type": "http",
      "critical": true,
      "endpoint": "/health"
    },
    {
      "name": "rogers-postgres",
      "host": "rogers-postgres",
      "port": 5432,
      "type": "postgres",
      "critical": true,
      "database": "rogers",
      "user": "rogers",
      "credentials_file": "/storage/credentials/rogers/postgres.env"
    },
    {
      "name": "rogers-neo4j",
      "host": "rogers-neo4j",
      "port": 7474,
      "type": "http",
      "critical": true,
      "endpoint": "/"
    },
    {
      "name": "rogers-redis",
      "host": "rogers-redis",
      "port": 6379,
      "type": "redis",
      "critical": true
    }
  ],
  "logging": {
    "level": "INFO"
  },
  "routing": {
    "timeout": 60000
  }
}

```

## rogers/package.json

```json
{
  "name": "rogers",
  "version": "1.0.0",
  "type": "module",
  "main": "server.js",
  "scripts": {
    "start": "node server.js"
  },
  "dependencies": {
    "logging-lib": "file:../../../lib/logging-lib",
    "routing-lib": "file:../../../lib/routing-lib",
    "health-aggregator-lib": "file:../../../lib/health-aggregator-lib",
    "mcp-protocol-lib": "file:../../../lib/mcp-protocol-lib"
  }
}

```

## rogers/server.js

```javascript
process.umask(0o000);
import { MCPGateway } from 'mcp-protocol-lib';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const configPath = process.env.CONFIG_PATH || join(__dirname, 'config.json');
const config = JSON.parse(readFileSync(configPath, 'utf8'));
const gateway = new MCPGateway(config);
gateway.start();

```

## rogers-langgraph/Dockerfile

```dockerfile
# rogers-langgraph — LangGraph backend container
# Provides HTTP endpoints called by the MCP gateway.
# Implements conversation storage, retrieval, and memory flows.
#
# Build context: project root (.)

FROM python:3.12-slim@sha256:48006ff57afe15f247ad3da166e9487da0f66a94adbc92810b0e189382d79246

ARG USER_NAME=rogers-langgraph
ARG USER_UID=2002
ARG USER_GID=2001

# Root phase: create group (if not exists) and user
RUN groupadd --gid ${USER_GID} administrators 2>/dev/null || true \
    && useradd --uid ${USER_UID} --gid ${USER_GID} --shell /bin/bash --create-home ${USER_NAME}

USER ${USER_NAME}
WORKDIR /app

# Copy packages cache and requirements, install offline
COPY --chown=${USER_NAME}:administrators mads/rogers/rogers-langgraph/packages/ /tmp/packages/
COPY --chown=${USER_NAME}:administrators mads/rogers/rogers-langgraph/requirements.txt ./
RUN pip install --no-cache-dir --no-index --find-links=/tmp/packages/ -r requirements.txt

# Copy application
COPY --chown=${USER_NAME}:administrators mads/rogers/rogers-langgraph/server.py ./
COPY --chown=${USER_NAME}:administrators mads/rogers/rogers-langgraph/mem0_adapters.py ./
COPY --chown=${USER_NAME}:administrators mads/rogers/rogers-langgraph/config.json ./
COPY --chown=${USER_NAME}:administrators mads/rogers/rogers-langgraph/start.sh ./
COPY --chown=${USER_NAME}:administrators mads/rogers/rogers-langgraph/flows/ ./flows/
COPY --chown=${USER_NAME}:administrators mads/rogers/rogers-langgraph/services/ ./services/
COPY --chown=${USER_NAME}:administrators mads/rogers/scripts/ ./scripts/
COPY --chown=${USER_NAME}:administrators mads/rogers/tests/ ./tests/
RUN chmod +x start.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["./start.sh"]

```

## rogers-langgraph/config.json

```json
{
  "log_level": "INFO",
  "llm": {
    "provider": "sutherland",
    "model": "agent-small",
    "temperature": 0.1
  },
  "gemini_llm": {
    "provider": "openai_compatible",
    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    "api_key_file": "/storage/credentials/api-keys/gemini.env",
    "model": "gemini-2.5-flash-lite",
    "temperature": 0.1
  },
  "memory_extraction": {
    "small_llm_max_chars": 90000,
    "large_llm_max_chars": 450000,
    "small_llm_config_key": "llm",
    "large_llm_config_key": "gemini_llm",
    "llm_timeout": 120
  },
  "embedding": {
    "context_window_size": 3
  },
  "search": {
    "hybrid_candidate_limit": 50,
    "reranker_model": "BAAI/bge-reranker-v2-m3",
    "reranker_top_n": 10
  },
  "memory": {
    "recency_decay_days": 90,
    "recency_decay_max_penalty": 0.2,
    "confidence_archive_threshold": 0.1,
    "confidence_boost_on_access": 0.15,
    "half_life_days": {
      "ephemeral":      3,
      "infrastructure": 45,
      "procedural":     90,
      "project":        180,
      "preference":     365,
      "relationship":   730,
      "historical":     null
    }
  },
  "credentials": {
    "postgres_password_file": "/storage/credentials/rogers/postgres.txt",
    "neo4j_password_file": "/storage/credentials/rogers/neo4j.txt"
  }
}

```

## rogers-langgraph/mem0_adapters.py

```python
"""
mem0_adapters.py — Custom Mem0 LLM and Embedder adapters for Rogers.

Rogers-langgraph is on rogers-net only and cannot reach joshua-net directly.
All calls to Sutherland (embeddings) go through the rogers gateway peer proxy
per ADR-053:

    rogers-langgraph -> POST http://rogers:6380/peer/sutherland/llm_embeddings   (rogers-net)
    rogers gateway   -> POST http://sutherland:11435/tool/llm_embeddings         (joshua-net)

LLM calls use the provider configured in config.json:
  - "openai_compatible": calls any OpenAI-compatible API directly (e.g. Gemini)
  - "sutherland": routes via peer proxy to Sutherland's llm_chat_completions tool
    Peer proxy uses MCP JSON-RPC (POST /mcp, tools/call, sessionless) per ADR-053.
    The rogers gateway adds Accept: application/json, text/event-stream and parses
    the FastMCP SSE response automatically.
"""

import json
import logging
import urllib.request
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

_log = logging.getLogger("rogers-langgraph.mem0_adapters")

# ---------------------------------------------------------------------------
# Import Mem0 base classes, with fallback stubs when mem0ai is not installed.
# ---------------------------------------------------------------------------

try:
    from mem0.llms.base import LlmBase as _LlmBase
    from mem0.embeddings.base import EmbeddingBase as _EmbeddingBase
except ImportError:
    try:
        from mem0.llms.base import BaseLlm as _LlmBase  # type: ignore[no-redef]
    except ImportError:
        from abc import ABC, abstractmethod

        class _LlmBase(ABC):  # type: ignore[no-redef]
            @abstractmethod
            def generate_response(
                self, messages, response_format=None, tools=None, tool_choice="auto"
            ): ...

    try:
        from mem0.embeddings.base import BaseEmbedder as _EmbeddingBase  # type: ignore[no-redef]
    except ImportError:
        from abc import ABC, abstractmethod

        class _EmbeddingBase(ABC):  # type: ignore[no-redef]
            @abstractmethod
            def embed(self, text) -> List[float]: ...


# ---------------------------------------------------------------------------
# Internal peer-proxy helper (ADR-053).
# ---------------------------------------------------------------------------


def _call_peer(
    gateway_url: str,
    peer_name: str,
    tool_name: str,
    params: dict,
    timeout: int = 60,
) -> dict:
    """POST a tool call through the rogers gateway peer proxy and return parsed JSON."""
    url = f"{gateway_url}/peer/{peer_name}/{tool_name}"
    payload = json.dumps(params).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# OpenAICompatibleLlmAdapter
# Calls any OpenAI-compatible API directly (Gemini, OpenAI, etc.)
# ---------------------------------------------------------------------------


class OpenAICompatibleLlmAdapter(_LlmBase):
    """Mem0 LLM adapter that calls any OpenAI-compatible API directly.

    Used for Gemini (via Google's OpenAI-compatible endpoint) or any other
    provider that speaks the OpenAI chat completions protocol.

    Config fields (from config.json llm section):
      base_url    — API base URL
      api_key_env — name of the environment variable holding the API key
      model       — model name to request
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: Optional[float] = None,
        timeout: Optional[int] = None,
    ):
        self.config = SimpleNamespace(embedding_dims=None)  # LLM has no embedding_dims
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout or 120

    def generate_response(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Any] = None,
        tools: Optional[List] = None,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
    ):
        """Call the OpenAI-compatible API and return the mem0-expected format.

        Matches mem0's official OpenAI adapter pattern:
        - With tools: returns dict {"content": ..., "tool_calls": [{"name": ..., "arguments": dict}]}
        - Without tools: returns content string (mem0 calls json.loads() on it for fact extraction)

        temperature: per-call override. Falls back to instance default if not provided.
        """
        from openai import OpenAI

        client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
        )

        effective_temp = temperature if temperature is not None else self.temperature
        kwargs: Dict[str, Any] = {"model": self.model, "messages": messages}
        if effective_temp is not None:
            kwargs["temperature"] = effective_temp
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        if response_format is not None:
            kwargs["response_format"] = response_format

        _log.debug(
            "OpenAICompatibleLlmAdapter: %d message(s) tools=%s",
            len(messages),
            bool(tools),
        )
        response = client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        if tools:
            result: Dict[str, Any] = {"content": msg.content, "tool_calls": []}
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    result["tool_calls"].append(
                        {
                            "name": tc.function.name,
                            "arguments": json.loads(tc.function.arguments),
                        }
                    )
            return result
        else:
            return _strip_code_fence(msg.content or "")


# ---------------------------------------------------------------------------
# SutherlandLlmAdapter
# Routes calls to Sutherland via the rogers gateway peer proxy.
# ---------------------------------------------------------------------------


class SutherlandLlmAdapter(_LlmBase):
    """Mem0 LLM adapter that routes to Sutherland via the rogers gateway peer proxy.

    Call chain (MCP peer proxy — ADR-053):
      rogers-langgraph -> POST http://rogers:6380/peer/sutherland/llm_chat_completions
      rogers gateway   -> POST http://sutherland:11435/mcp  (MCP JSON-RPC tools/call)
      sutherland       -> /v1/chat/completions -> full OpenAI response

    router.callMCP() unwraps the MCP content envelope and parses the JSON, so
    _call_peer() returns the full OpenAI response dict directly — no envelope needed.
    """

    def __init__(
        self,
        gateway_url: str = "http://rogers:6380",
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ):
        self.config = SimpleNamespace(embedding_dims=None)
        self.gateway_url = gateway_url
        self.model = model
        self.temperature = temperature
        self.timeout = 120

    def generate_response(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Any] = None,
        tools: Optional[List] = None,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
    ):
        """Forward a chat completion to Sutherland via peer proxy.

        Matches mem0's official OpenAI adapter pattern:
        - With tools: returns dict {"content": ..., "tool_calls": [{"name": ..., "arguments": dict}]}
        - Without tools: returns content string (mem0 calls json.loads() on it for fact extraction)

        Note: OpenAI-format tool_calls have arguments as a JSON string; we parse them to dict
        to match mem0's internal format (as the official mem0 OpenAI adapter does).

        temperature: per-call override. Falls back to instance default if not provided.
        """
        effective_temp = temperature if temperature is not None else self.temperature
        _log.debug(
            "SutherlandLlmAdapter.generate_response: %d message(s) tools=%s model=%s temp=%s",
            len(messages),
            bool(tools),
            self.model,
            effective_temp,
        )

        params: Dict[str, Any] = {"messages": messages}
        if effective_temp is not None:
            params["temperature"] = effective_temp
        if self.model:
            params["model"] = self.model
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice
        if response_format is not None:
            params["response_format"] = response_format

        try:
            completions = _call_peer(
                self.gateway_url,
                "sutherland",
                "llm_chat_completions",
                params,
                timeout=self.timeout,
            )
        except Exception as e:
            _log.error("SutherlandLlmAdapter: peer proxy call failed: %s", e)
            raise

        msg = completions.get("choices", [{}])[0].get("message", {})

        if tools:
            result: Dict[str, Any] = {"content": msg.get("content"), "tool_calls": []}
            for tc in msg.get("tool_calls") or []:
                args = tc.get("function", {}).get("arguments", "{}")
                if isinstance(args, str):
                    args = json.loads(args)
                result["tool_calls"].append(
                    {
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": args,
                    }
                )
            return result
        else:
            return _strip_code_fence(msg.get("content") or "")


def _strip_code_fence(text: str) -> str:
    """Strip markdown code fences (```json...```) that some models add around JSON output.

    Handles both cases:
    - Text starting with ``` (simple fence)
    - Preamble text before ``` (e.g. "Here is the JSON:\n```json\n{...}\n```")
    """
    text = text.strip()
    # Case 1: text starts with a code fence
    if text.startswith("```"):
        lines = text.split("\n")
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        return "\n".join(inner).strip()
    # Case 2: code fence appears after preamble text
    fence_start = text.find("```")
    if fence_start > 0:
        after_fence = text[fence_start:]
        lines = after_fence.split("\n")
        inner = lines[1:]  # skip opening ```json line
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        return "\n".join(inner).strip()
    return text


# ---------------------------------------------------------------------------
# SutherlandEmbedderAdapter
# Routes embed() calls to Sutherland via the rogers gateway peer proxy.
# ---------------------------------------------------------------------------


class SutherlandEmbedderAdapter(_EmbeddingBase):
    """Mem0 embedder adapter that routes calls to Sutherland via the rogers gateway peer proxy.

    Sutherland produces 768-dimensional embeddings via the embed-default alias
    (e.g. BAAI/bge-base-en-v1.5 or nomic-embed-text-v1.5).

    self.config.embedding_dims is read by mem0's telemetry capture_event().
    """

    def __init__(self, gateway_url: str = "http://rogers:6380"):
        self.config = SimpleNamespace(embedding_dims=768)
        self.dims = 768
        self.gateway_url = gateway_url
        self.timeout = 30

    def embed(self, text: str) -> List[float]:
        """Call Sutherland's llm_embeddings tool via peer proxy and return the 768-dim vector."""
        _log.debug("SutherlandEmbedderAdapter.embed: text_len=%d", len(text))

        try:
            result = _call_peer(
                self.gateway_url,
                "sutherland",
                "llm_embeddings",
                {"input": text},
                timeout=self.timeout,
            )
        except Exception as e:
            _log.error("SutherlandEmbedderAdapter: peer proxy call failed: %s", e)
            raise

        embedding = result.get("embedding")
        if not embedding:
            raise ValueError(
                f"SutherlandEmbedderAdapter: no 'embedding' key in response: {result}"
            )

        if len(embedding) != self.dims:
            _log.warning(
                "SutherlandEmbedderAdapter: expected %d dimensions, got %d",
                self.dims,
                len(embedding),
            )

        return embedding


# Backward-compatible alias (Hamilton retired; embeddings now via Sutherland)
HamiltonEmbedderAdapter = SutherlandEmbedderAdapter

```

## rogers-langgraph/requirements.txt

```text
quart==0.19.9
asyncpg==0.30.0
redis==5.2.1
httpx==0.28.1
mem0ai==0.1.29
psycopg2-binary==2.9.9
langgraph==0.2.60
langchain-community==0.3.27
neo4j==6.1.0
rank-bm25==0.2.2
langchain-mcp-adapters==0.2.1
prometheus-client==0.24.1
detect-secrets==1.5.0

```

## rogers-langgraph/server.py

```python
#!/usr/bin/env python3
"""
Rogers LangGraph HTTP Server

Quart HTTP transport layer (per template baseline). Receives requests from
the MCP gateway, invokes LangGraph StateGraph flows or service functions,
returns results. This file is transport only — all programmatic logic is in
flows/ and services/.

REQ-000 §4.1: HTTP framework is transport; LangGraph StateGraph handles orchestration.
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone

os.umask(0o000)  # REQ-000 §2.4: Set umask in application code

from quart import Quart, request, jsonify, Response  # noqa: E402
from prometheus_client import REGISTRY, generate_latest, CONTENT_TYPE_LATEST, Counter, Histogram

# --- Metrics Definitions ---
MCP_REQUESTS = Counter(
    "mcp_requests_total",
    "Total MCP requests",
    ["mad", "tool", "status"],
)
MCP_REQUEST_DURATION = Histogram(
    "mcp_request_duration_seconds",
    "Latency of MCP requests",
    ["mad", "tool"],
)


# --- Logging setup (must be first) ---
from services.logging_setup import setup_logging  # noqa: E402

_log = setup_logging()

# --- Configuration and credentials ---
from services.config import (  # noqa: E402
    load_config,
    load_postgres_password,
    load_neo4j_password,
)

_config = load_config()
_pg_password = load_postgres_password(_config)
_neo4j_password = load_neo4j_password(_config)

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://rogers:6380")

# --- Database and queue worker imports ---
from services import database as db  # noqa: E402
from services import queue_worker  # noqa: E402
from services import mcp_client # noqa: E402

# --- LangGraph flows ---
from flows.message_pipeline import build_message_pipeline  # noqa: E402
from flows.retrieval import build_retrieval_flow  # noqa: E402
from flows.embed_pipeline import build_embed_pipeline  # noqa: E402
from flows.context_assembly import build_context_assembly  # noqa: E402
from flows.memory_extraction import build_memory_extraction  # noqa: E402
from flows.imperator import build_imperator_graph # noqa: E402
import flows.context_assembly as context_assembly_mod  # noqa: E402
import flows.memory_extraction as memory_extraction_mod  # noqa: E402
import flows.embed_pipeline as embed_pipeline_mod  # noqa: E402

# Compile StateGraph flows once at module load
_message_pipeline = build_message_pipeline()
_retrieval_flow = build_retrieval_flow()
_embed_pipeline = build_embed_pipeline()
_context_assembly = build_context_assembly()
_memory_extraction = build_memory_extraction()
_imperator_graph = build_imperator_graph()

# --- Simple operation handlers ---
from flows import conversation_ops, memory_ops  # noqa: E402

# --- Quart app ---
app = Quart(__name__)

# ============================================================
# Apgar Metrics Graph (State 2)
# ============================================================

class MetricsState(TypedDict):
    action: str
    metrics_data: str

async def _node_collect_metrics(state: MetricsState) -> MetricsState:
    """Collect Prometheus metrics from the registry."""
    data = generate_latest(REGISTRY)
    return {**state, "metrics_data": data.decode("utf-8", errors="replace")}

def _build_metrics_graph():
    """Builds the simple single-node graph for metrics collection."""
    g = StateGraph(MetricsState)
    g.add_node("collect_metrics", _node_collect_metrics)
    g.set_entry_point("collect_metrics")
    g.add_edge("collect_metrics", END)
    return g.compile()

_metrics_graph = _build_metrics_graph()



# ============================================================
# Error response helper (O1: structured error schema per template)
# ============================================================


def _error(code: str, message: str, status: int = 400):
    """Return a structured error response per template pattern.

    Format: {"success": false, "error": {"code": "...", "message": "..."}}
    """
    return (
        jsonify({"success": False, "error": {"code": code, "message": message}}),
        status,
    )


# ============================================================
# Lifecycle: startup and shutdown
# ============================================================


@app.before_serving
async def startup():
    """Initialise database pools and start background queue worker."""
    await db.init_postgres(_pg_password)
    db.init_redis()

    await mcp_client.init_mcp_tools()

    # Configure flow modules that need credentials for Mem0/LLM initialisation
    context_assembly_mod.configure(_config, _pg_password, _neo4j_password, GATEWAY_URL)
    memory_extraction_mod.configure(_config, _pg_password, _neo4j_password, GATEWAY_URL)
    embed_pipeline_mod.configure(_config)

    # Configure and start queue worker with compiled StateGraph references
    queue_worker.configure(
        _config, _pg_password, _neo4j_password, GATEWAY_URL,
        embed_graph=_embed_pipeline,
        assembly_graph=_context_assembly,
        extraction_graph=_memory_extraction,
    )
    asyncio.ensure_future(queue_worker.run_worker())
    _log.info("Rogers LangGraph server started")


@app.after_serving
async def shutdown():
    """Gracefully close database connections."""
    await db.close_all()
    _log.info("Rogers LangGraph server stopped")


# ============================================================
# HEALTH & METRICS (Apgar)
# ============================================================

@app.route("/health")
async def health():
    """Health check endpoint — checks postgres + redis."""
    try:
        pool = db.get_pg_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        redis = db.get_redis()
        await redis.ping()
        return jsonify({"status": "healthy"}), 200
    except Exception as exc:
        _log.error("Health check failed: %s", exc)
        return jsonify({"status": "unhealthy", "error": str(exc)}), 503

@app.route("/metrics", methods=["GET"])
async def metrics():
    """Prometheus scrape endpoint (State 2)."""
    initial_state = {"action": "get_metrics", "metrics_data": ""}
    final_state = await _metrics_graph.ainvoke(initial_state)
    return Response(final_state["metrics_data"], mimetype=CONTENT_TYPE_LATEST)

@app.route("/metrics_get", methods=["POST"])
async def metrics_get():
    """MCP tool endpoint for Apgar scraper (State 2)."""
    initial_state = {"action": "get_metrics", "metrics_data": ""}
    final_state = await _metrics_graph.ainvoke(initial_state)
    return jsonify({"metrics": final_state["metrics_data"]})



# ============================================================
# Tool Registry & Graph Invokers (for State 2 MCP Dispatch)
# ============================================================

async def _invoke_message_pipeline(args: dict) -> dict:
    """Wrapper to prepare and invoke the message pipeline graph."""
    # Validation from the old route handler
    context_window_id = args.get("context_window_id")
    conversation_id = args.get("conversation_id")
    if not context_window_id and not conversation_id:
        return {"error": "Either context_window_id or conversation_id must be provided"}

    role = args.get("role")
    sender_id = args.get("sender_id")
    content = args.get("content")
    if not role or sender_id is None or not content:
        return {"error": "role, sender_id, and content are required"}
    
    initial_state = {
        "conversation_id": conversation_id or "", "context_window_id": context_window_id,
        "role": role, "sender_id": int(sender_id), "content": content,
        "token_count": args.get("token_count"), "model_name": args.get("model_name"),
        "external_session_id": args.get("external_session_id"),
        "content_type": args.get("content_type"), "priority": int(args.get("priority", 3)),
        "message_id": None, "sequence_number": None, "deduplicated": False,
        "queued_jobs": [], "error": None,
    }
    result = await _message_pipeline.ainvoke(initial_state)
    if result.get("error"):
        return {"error": result["error"]}
    return {
        "message_id": result.get("message_id"), "conversation_id": result.get("conversation_id"),
        "sequence_number": result.get("sequence_number"), "deduplicated": result.get("deduplicated", False),
        "queued_jobs": result.get("queued_jobs", []),
    }

async def _invoke_retrieval_flow(args: dict) -> dict:
    """Wrapper to prepare and invoke the retrieval graph."""
    context_window_id = args.get("context_window_id")
    if not context_window_id:
        return {"error": "context_window_id is required"}
        
    initial_state = {
        "context_window_id": context_window_id, "conversation_id": None, "build_type": None,
        "window": None, "max_token_limit": 0, "tier1_summary": None, "tier2_summaries": [],
        "recent_messages": [], "context": None, "tiers": None, "total_tokens": 0,
        "assembly_status": "ready", "error": None,
    }
    result = await _retrieval_flow.ainvoke(initial_state)
    if result.get("error"):
        return {"error": result["error"]}
    return {
        "context": result.get("context"), "tiers": result.get("tiers"),
        "total_tokens": result.get("total_tokens", 0),
        "assembly_status": result.get("assembly_status", "ready"),
    }

async def _invoke_mem_search(args: dict) -> dict:
    """Wrapper for memory search op requiring config."""
    return await memory_ops.mem_search(
        config=_config, pg_pass=_pg_password, neo4j_pass=_neo4j_password, gw_url=GATEWAY_URL, **args
    )

async def _invoke_mem_get_context(args: dict) -> dict:
    """Wrapper for memory context op requiring config."""
    return await memory_ops.mem_get_context(
        config=_config, pg_pass=_pg_password, neo4j_pass=_neo4j_password, gw_url=GATEWAY_URL, **args
    )

async def _invoke_imperator(args: dict) -> dict:
    """Wrapper to prepare and invoke the Imperator graph."""
    if not args.get("user_id") or not args.get("messages"):
        return {"error": "user_id and messages are required for rogers_chat"}
    
    initial_state = {
        "messages": [HumanMessage(content=m.get("content","")) for m in args.get("messages", []) if m.get("role") == "user"],
        "user_id": args.get("user_id"),
    }
    result = await _imperator_graph.ainvoke(initial_state)
    return {"response": result.get("final_response", "Imperator finished with no response.")}

# Maps MCP tool names to their implementation
_tool_registry = {
    "rogers_chat": _invoke_imperator,
    "conv_create_conversation": conversation_ops.create_conversation,
    "conv_create_context_window": conversation_ops.create_context_window,
    "conv_store_message": _invoke_message_pipeline,
    "conv_retrieve_context": _invoke_retrieval_flow,
    "conv_search": conversation_ops.search_conversations,
    "conv_search_messages": conversation_ops.search_messages,
    "conv_get_history": conversation_ops.get_history,
    "conv_search_context_windows": conversation_ops.search_context_windows_handler,
    "mem_search": _invoke_mem_search,
    "mem_get_context": _invoke_mem_get_context,
    "metrics_get": metrics_get,
}


# ============================================================
# State 2 MCP Endpoint
# ============================================================

@app.route("/mcp", methods=["POST"])
async def mcp_dispatch():
    """
    State 2 single entry point for all MCP tool calls.
    Nginx proxies all /mcp traffic here. This function parses the JSON-RPC,
    looks up the tool in the registry, invokes it, and returns a JSON-RPC response.
    """
    start_time = time.time()
    status = "error"
    tool_name = "unknown"
    try:
        data = await request.get_json()
        if not data or data.get("jsonrpc") != "2.0":
            return _error("INVALID_REQUEST", "Not a valid JSON-RPC 2.0 request", 400)
        
        method = data.get("method")
        params = data.get("params", {})
        request_id = data.get("id")

        if method != "tools/call":
            return _error("METHOD_NOT_FOUND", f"Only 'tools/call' is supported, got '{method}'", 400)

        tool_name = params.get("name")
        tool_args = params.get("arguments", {})

        tool_func = _tool_registry.get(tool_name)
        if not tool_func:
            status = "not_found"
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Tool not found: {tool_name}"}
            }), 404

        _log.info(f"Invoking tool '{tool_name}' via MCP dispatch")
        result = await tool_func(tool_args)

        if "error" in result:
             status = "tool_error"
             return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": result["error"]}
            }), 400

        status = "success"
        return jsonify({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps(result) if isinstance(result, dict) else str(result)
                }]
            }
        })

    except Exception as exc:
        _log.error(f"/mcp dispatch failed: {exc}", exc_info=True)
        status = "internal_error"
        return _error("INTERNAL_ERROR", str(exc), 500)
    finally:
        duration = time.time() - start_time
        MCP_REQUESTS.labels(mad="rogers", tool=tool_name, status=status).inc()
        MCP_REQUEST_DURATION.labels(mad="rogers", tool=tool_name).observe(duration)




# ============================================================
# INTERNAL TOOLS (not exposed via MCP — for queue worker and admin)
# ============================================================


@app.route("/mem_add", methods=["POST"])
async def mem_add_route():
    """Add a memory (internal tool)."""
    data = await request.get_json()
    params = data.get("params", data)

    content = params.get("content")
    user_id = params.get("user_id")
    if not content or not user_id:
        return _error("VALIDATION_ERROR", "content and user_id are required", 400)

    result = await memory_ops.mem_add(
        content=content,
        user_id=user_id,
        config=_config,
        pg_pass=_pg_password,
        neo4j_pass=_neo4j_password,
        gw_url=GATEWAY_URL,
        agent_id=params.get("agent_id"),
        run_id=params.get("run_id"),
    )
    if "error" in result:
        return _error("INTERNAL_ERROR", result["error"], 503)
    return jsonify(result)


@app.route("/mem_list", methods=["POST"])
async def mem_list_route():
    """List memories (internal/admin tool)."""
    data = await request.get_json()
    params = data.get("params", data)

    user_id = params.get("user_id")
    if not user_id:
        return _error("VALIDATION_ERROR", "user_id is required", 400)

    result = await memory_ops.mem_list(
        user_id=user_id,
        config=_config,
        pg_pass=_pg_password,
        neo4j_pass=_neo4j_password,
        gw_url=GATEWAY_URL,
        agent_id=params.get("agent_id"),
        limit=params.get("limit", 50),
        offset=params.get("offset", 0),
    )
    if "error" in result:
        return _error("INTERNAL_ERROR", result["error"], 503)
    return jsonify(result)


@app.route("/mem_delete", methods=["POST"])
async def mem_delete_route():
    """Delete a memory (internal/admin tool)."""
    data = await request.get_json()
    params = data.get("params", data)

    memory_id = params.get("memory_id")
    if not memory_id:
        return _error("VALIDATION_ERROR", "memory_id is required", 400)

    result = await memory_ops.mem_delete(
        memory_id=memory_id,
        config=_config,
        pg_pass=_pg_password,
        neo4j_pass=_neo4j_password,
        gw_url=GATEWAY_URL,
    )
    if "error" in result:
        return _error("INTERNAL_ERROR", result["error"], 503)
    return jsonify(result)


@app.route("/mem_extract", methods=["POST"])
async def mem_extract_route():
    """Extract memories from conversation (internal tool)."""
    data = await request.get_json()
    params = data.get("params", data)

    conversation_id = params.get("conversation_id")
    user_id = params.get("user_id")
    if not conversation_id or not user_id:
        return _error(
            "VALIDATION_ERROR", "conversation_id and user_id are required", 400
        )

    try:
        messages = await db.get_recent_messages(conversation_id, limit=20)
        if not messages:
            return jsonify({"extracted": 0, "status": "no messages"})

        conversation_text = "\n".join(
            [f"{m['role']} (sender {m['sender_id']}): {m['content']}" for m in messages]
        )

        result = await memory_ops.mem_add(
            content=conversation_text,
            user_id=user_id,
            config=_config,
            pg_pass=_pg_password,
            neo4j_pass=_neo4j_password,
            gw_url=GATEWAY_URL,
            agent_id=params.get("agent_id"),
            run_id=conversation_id,
        )
        return jsonify({"extracted": 1, "status": "extracted", "result": result})
    except Exception as exc:
        _log.error("mem_extract failed: %s", exc)
        return _error("INTERNAL_ERROR", str(exc), 503)


# ============================================================
# Entry point
# ============================================================


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)

```

## rogers-langgraph/start.sh

```bash
#!/bin/bash
umask 000
exec python server.py

```

## rogers-langgraph/flows/__init__.py

```python

```

## rogers-langgraph/flows/context_assembly.py

```python
"""
Context Assembly — LangGraph StateGraph flow.

Orchestrates three-tier context build for a specific context window:
    set_assembly_flag -> load_window_config -> load_messages -> calculate_tiers
    -> select_llm -> summarize_chunks -> consolidate_tier1 -> finalize_assembly
    -> clear_assembly_flag

All error paths route to clear_assembly_flag before END to ensure the Redis
flag is always cleaned up (replaces the try/finally pattern).

Triggered by the queue worker pulling from the context_assembly_jobs Redis list.

REQ-rogers §7.3
"""

import asyncio
import logging
import uuid
from typing import Optional

from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

from services import database as db

_log = logging.getLogger("rogers-langgraph.flows.context_assembly")

# Module-level references set by configure()
_config: dict = {}
_postgres_password: str = ""
_neo4j_password: str = ""
_gateway_url: str = ""


def configure(config: dict, pg_pass: str, neo4j_pass: str, gateway_url: str):
    """Set module-level config needed for LLM adapter construction.

    Called from server.py at startup, before any graph invocations.
    """
    global _config, _postgres_password, _neo4j_password, _gateway_url
    _config = config
    _postgres_password = pg_pass
    _neo4j_password = neo4j_pass
    _gateway_url = gateway_url


def _build_summarization_llm(build_type_id: str):
    """Return the appropriate LLM adapter for context assembly.

    - small-basic (<=30k): Local LLM via Sutherland peer proxy
    - standard-tiered (30k+): Gemini Flash-Lite API (direct, not peer proxy)
    """
    from services.mem0_setup import _build_llm_adapter

    if build_type_id == "standard-tiered":
        gemini_cfg = _config.get("gemini_llm")
        if gemini_cfg:
            return _build_llm_adapter({"llm": gemini_cfg}, _gateway_url)
        _log.warning(
            "standard-tiered requested but gemini_llm not configured; "
            "falling back to default LLM"
        )

    # Default: sutherland (small-basic or fallback)
    return _build_llm_adapter(_config, _gateway_url)


# ============================================================
# State definition (REQ §7.3.3)
# ============================================================


class ContextAssemblyState(TypedDict):
    # Inputs (from job payload)
    conversation_id: str
    context_window_id: str              # UUID string
    build_type_id: str                  # e.g. "small-basic", "standard-tiered"

    # Intermediate (set by nodes)
    window: Optional[dict]              # Context window row from postgres
    build_type: Optional[dict]          # Build type config row
    messages: list                      # All messages for the conversation
    max_token_limit: int                # From window config
    tier3_budget: int                   # Calculated token budget for Tier 3
    tier3_start_seq: int                # Sequence number boundary for Tier 3
    older_messages: list                # Messages that need summarization
    chunks: list                        # Chunked older messages (20 msgs per chunk)
    llm: Optional[object]              # LLM adapter instance (not serializable)
    model_name: str                     # Name of the model used for summarization
    tier2_summaries_written: int        # Count of Tier 2 summaries written
    tier1_consolidated: bool            # Whether Tier 1 consolidation happened

    # Control
    assembly_key: str                   # Redis key for assembly_in_progress flag
    error: Optional[str]


# ============================================================
# Node functions
# ============================================================


async def set_assembly_flag(state: ContextAssemblyState) -> dict:
    """Set Redis assembly_in_progress flag with 120s TTL (nx=True).

    Uses nx=True (set-if-not-exists) to prevent concurrent assembly for
    the same window. If another assembly is already running, this returns
    an error which routes to clear_assembly_flag → END (skip gracefully).

    The retrieval flow checks this flag and blocks/waits if set.
    """
    assembly_key = f"assembly_in_progress:{state['context_window_id']}"
    redis = db.get_redis()
    acquired = await redis.set(assembly_key, "1", ex=120, nx=True)
    if not acquired:
        _log.info(
            "Context assembly: already in progress for window=%s — skipping",
            state["context_window_id"],
        )
        return {"assembly_key": "", "error": "assembly_already_in_progress"}
    return {"assembly_key": assembly_key}


async def load_window_config(state: ContextAssemblyState) -> dict:
    """Fetch window row and build type config from postgres.

    Sets error if either is not found (routes to clear_assembly_flag).
    """
    context_window_id = uuid.UUID(state["context_window_id"])

    window = await db.get_context_window(context_window_id)
    if not window:
        return {"error": f"Context window {state['context_window_id']} not found"}

    build_type = await db.get_build_type(window["build_type_id"])
    if not build_type:
        return {"error": f"Build type {window['build_type_id']} not found"}

    return {
        "window": window,
        "build_type": build_type,
        "max_token_limit": window["max_token_limit"],
    }


async def load_messages(state: ContextAssemblyState) -> dict:
    """Fetch all messages for the conversation."""
    messages = await db.get_conversation_messages(state["conversation_id"])

    _log.info(
        "Context assembly start: window=%s conv=%s build=%s msgs=%d max_tokens=%d",
        state["context_window_id"],
        state["conversation_id"],
        state["build_type_id"],
        len(messages),
        state["max_token_limit"],
    )

    return {"messages": messages}


async def calculate_tiers(state: ContextAssemblyState) -> dict:
    """Calculate tier 3 budget, boundary, and chunk older messages incrementally.

    Tier proportions scale with window size:
      30k:  ~70% Tier 3, ~20% Tier 2, ~10% Tier 1
      200k: ~75% Tier 3, ~22% Tier 2, ~3% Tier 1
      1M:   ~84% Tier 3, ~15% Tier 2, ~1% Tier 1

    Incremental: checks existing active Tier 2 summaries and only includes
    messages with sequence numbers above the highest already-summarized seq.
    This avoids re-summarizing the entire history on every assembly.
    """
    messages = state["messages"]
    max_limit = state["max_token_limit"]

    # Calculate tier proportions
    tier3_pct = 0.70 + 0.14 * min(1.0, max_limit / 1_000_000)  # 70-84%
    tier3_budget = int(max_limit * tier3_pct)

    # Determine Tier 3 boundary: walk backwards from most recent message
    tier3_tokens = 0
    tier3_start_seq = messages[-1]["sequence_number"] + 1  # default: nothing in tier 3
    for msg in reversed(messages):
        msg_tokens = msg.get("token_count") or max(1, len(msg.get("content", "")) // 4)
        if tier3_tokens + msg_tokens <= tier3_budget:
            tier3_tokens += msg_tokens
            tier3_start_seq = msg["sequence_number"]
        else:
            break

    # Messages before tier3_start_seq need summarization
    all_older = [m for m in messages if m["sequence_number"] < tier3_start_seq]

    # Incremental: find what's already covered by existing Tier 2 summaries
    context_window_id = uuid.UUID(state["context_window_id"])
    existing_summaries = await db.get_active_summaries_for_window(context_window_id)
    existing_t2 = [s for s in existing_summaries if s["tier"] == 2]

    max_summarized_seq = 0
    if existing_t2:
        max_summarized_seq = max(s["summarizes_to_seq"] for s in existing_t2)

    # Only chunk messages NOT already covered by existing summaries
    older_messages = [m for m in all_older if m["sequence_number"] > max_summarized_seq]

    if max_summarized_seq > 0 and older_messages:
        _log.info(
            "Incremental assembly: %d existing T2 summaries cover through seq %d, "
            "%d new messages to summarize (seq %d-%d): window=%s",
            len(existing_t2),
            max_summarized_seq,
            len(older_messages),
            older_messages[0]["sequence_number"],
            older_messages[-1]["sequence_number"],
            state["context_window_id"],
        )

    # Chunk older messages into groups of 20
    chunk_size = 20
    chunks = []
    for i in range(0, len(older_messages), chunk_size):
        chunks.append(older_messages[i : i + chunk_size])

    return {
        "tier3_budget": tier3_budget,
        "tier3_start_seq": tier3_start_seq,
        "older_messages": older_messages,
        "chunks": chunks,
    }


async def select_llm(state: ContextAssemblyState) -> dict:
    """Select LLM adapter based on build_type_id.

    small-basic: Sutherland/Qwen (local GPU, free)
    standard-tiered: Gemini Flash-Lite (cloud API)
    """
    build_type_id = state["build_type_id"]
    llm = _build_summarization_llm(build_type_id)

    model_name = (
        _config.get("gemini_llm", {}).get("model")
        if build_type_id == "standard-tiered" and _config.get("gemini_llm")
        else _config.get("llm", {}).get("model", "unknown")
    )

    return {"llm": llm, "model_name": model_name}


async def summarize_chunks(state: ContextAssemblyState) -> dict:
    """LLM-summarize each new chunk incrementally.

    Existing Tier 2 summaries are preserved — calculate_tiers already
    excluded messages covered by them. Only new unsummarized messages
    are chunked and summarized here.

    LLM generate_response is synchronous — runs in thread pool executor.
    """
    context_window_id = uuid.UUID(state["context_window_id"])
    conversation_id = state["conversation_id"]
    llm = state["llm"]
    model_name = state["model_name"]
    chunks = state["chunks"]

    loop = asyncio.get_running_loop()
    written = 0

    for chunk in chunks:
        chunk_text = "\n".join(
            [
                f"{m['role']} (sender {m['sender_id']}): {m['content']}"
                for m in chunk
            ]
        )
        chunk_tokens = sum(
            m.get("token_count") or max(1, len(m.get("content", "")) // 4)
            for m in chunk
        )

        # LLM generate_response is synchronous — run in thread pool
        summary_response = await loop.run_in_executor(
            None,
            lambda ct=chunk_text: llm.generate_response(
                [
                    {
                        "role": "system",
                        "content": (
                            "Summarize this conversation chunk concisely, preserving key facts, "
                            "decisions, and preferences. Keep the summary under 200 words."
                        ),
                    },
                    {"role": "user", "content": ct},
                ]
            ),
        )
        summary_text = (
            summary_response.get("content")
            if isinstance(summary_response, dict)
            else str(summary_response)
        )

        if summary_text:
            await db.insert_summary(
                conversation_id=conversation_id,
                context_window_id=context_window_id,
                summary_text=summary_text,
                tier=2,
                summarizes_from_seq=chunk[0]["sequence_number"],
                summarizes_to_seq=chunk[-1]["sequence_number"],
                message_count=len(chunk),
                original_token_count=chunk_tokens,
                summary_token_count=len(summary_text) // 4,  # rough estimate
                summarized_by_model=model_name,
            )
            written += 1

    _log.info(
        "Context assembly: wrote %d Tier 2 chunk summaries: window=%s conv=%s",
        written,
        state["context_window_id"],
        conversation_id,
    )

    return {"tier2_summaries_written": written}


async def consolidate_tier1(state: ContextAssemblyState) -> dict:
    """If >3 active Tier 2 summaries, consolidate oldest into a Tier 1 archival summary.

    Keeps the 2 most recent Tier 2 chunks active. The oldest Tier 2 chunks
    are deactivated and replaced by a single Tier 1 archival summary.

    LLM generate_response is synchronous — runs in thread pool executor.
    """
    context_window_id = uuid.UUID(state["context_window_id"])
    conversation_id = state["conversation_id"]
    llm = state["llm"]
    model_name = state["model_name"]

    tier2_summaries = await db.get_active_summaries_for_window(context_window_id)
    tier2_only = [s for s in tier2_summaries if s["tier"] == 2]

    if len(tier2_only) <= 3:
        return {"tier1_consolidated": False}

    # Consolidate oldest Tier 2 chunks into Tier 1
    to_consolidate = tier2_only[:-2]  # keep most recent 2 as Tier 2
    consolidation_text = "\n\n".join(
        [s["summary_text"] for s in to_consolidate]
    )

    loop = asyncio.get_running_loop()
    archival_response = await loop.run_in_executor(
        None,
        lambda: llm.generate_response(
            [
                {
                    "role": "system",
                    "content": (
                        "Consolidate these conversation summaries into a single archival summary. "
                        "Preserve all key facts, decisions, and important context. "
                        "This summary will be the long-term memory of the conversation."
                    ),
                },
                {"role": "user", "content": consolidation_text},
            ]
        ),
    )
    archival_text = (
        archival_response.get("content")
        if isinstance(archival_response, dict)
        else str(archival_response)
    )

    if archival_text:
        # Deactivate old Tier 1
        await db.deactivate_summaries_for_window(context_window_id, tier=1)

        # Deactivate the consolidated Tier 2 chunks
        pool = db.get_pg_pool()
        for s in to_consolidate:
            await pool.execute(
                "UPDATE conversation_summaries SET is_active = false WHERE id = $1",
                s["id"],
            )

        await db.insert_summary(
            conversation_id=conversation_id,
            context_window_id=context_window_id,
            summary_text=archival_text,
            tier=1,
            summarizes_from_seq=to_consolidate[0]["summarizes_from_seq"],
            summarizes_to_seq=to_consolidate[-1]["summarizes_to_seq"],
            message_count=sum(
                s.get("message_count") or 0 for s in to_consolidate
            ),
            summarized_by_model=model_name,
        )

    return {"tier1_consolidated": True}


async def finalize_assembly(state: ContextAssemblyState) -> dict:
    """Update window last_assembled_at timestamp."""
    context_window_id = uuid.UUID(state["context_window_id"])
    await db.update_window_last_assembled(context_window_id)

    _log.info(
        "Context assembly complete: window=%s conv=%s",
        state["context_window_id"],
        state["conversation_id"],
    )

    return {}


async def clear_assembly_flag(state: ContextAssemblyState) -> dict:
    """Delete Redis assembly_in_progress flag.

    ALWAYS reached — both normal and error paths route here.
    This replaces the try/finally pattern from the old queue_worker code.
    """
    assembly_key = state.get("assembly_key")
    if assembly_key:
        redis = db.get_redis()
        await redis.delete(assembly_key)

    return {}


# ============================================================
# Routing functions
# ============================================================


def after_set_flag(state: ContextAssemblyState) -> str:
    """Route: if lock not acquired (error), skip to END. Otherwise, proceed."""
    if state.get("error"):
        return END
    return "load_window_config"


def after_load_config(state: ContextAssemblyState) -> str:
    """Route: error → clear_assembly_flag, ok → load_messages."""
    if state.get("error"):
        return "clear_assembly_flag"
    return "load_messages"


def after_load_messages(state: ContextAssemblyState) -> str:
    """Route: no messages → finalize_assembly (skip, not error),
    error → clear_assembly_flag, ok → calculate_tiers.
    """
    if state.get("error"):
        return "clear_assembly_flag"
    if not state.get("messages"):
        _log.info(
            "Context assembly: no messages for conv=%s — skipping",
            state["conversation_id"],
        )
        return "finalize_assembly"
    return "calculate_tiers"


def after_calculate_tiers(state: ContextAssemblyState) -> str:
    """Route: no older messages → finalize_assembly (all fits in tier 3),
    has older → select_llm.
    """
    if not state.get("older_messages"):
        _log.info(
            "Context assembly: all messages fit in Tier 3 — no summaries needed: "
            "window=%s conv=%s",
            state["context_window_id"],
            state["conversation_id"],
        )
        return "finalize_assembly"
    return "select_llm"


# ============================================================
# Graph construction
# ============================================================


def build_context_assembly() -> StateGraph:
    """Build and compile the context assembly StateGraph.

    Called once at module load time in server.py.

    Graph structure:
        [set_assembly_flag]
                |
       [load_window_config]
          /          \\
    (error)        (ok)
        |              |
        |       [load_messages]
        |        /    |     \\
        |  (no msgs) (ok)  (error)
        |     |       |      |
        |     |  [calculate_tiers]
        |     |     /       \\
        |     | (no older)  (has older)
        |     |    |           |
        |     |    |      [select_llm]
        |     |    |           |
        |     |    |    [summarize_chunks]
        |     |    |           |
        |     |    |    [consolidate_tier1]
        |     |    |           |
        |     +----+----[finalize_assembly]
        |                      |
        +---[clear_assembly_flag]
                   |
                  END
    """
    workflow = StateGraph(ContextAssemblyState)

    # Add nodes
    workflow.add_node("set_assembly_flag", set_assembly_flag)
    workflow.add_node("load_window_config", load_window_config)
    workflow.add_node("load_messages", load_messages)
    workflow.add_node("calculate_tiers", calculate_tiers)
    workflow.add_node("select_llm", select_llm)
    workflow.add_node("summarize_chunks", summarize_chunks)
    workflow.add_node("consolidate_tier1", consolidate_tier1)
    workflow.add_node("finalize_assembly", finalize_assembly)
    workflow.add_node("clear_assembly_flag", clear_assembly_flag)

    # Set entry point
    workflow.set_entry_point("set_assembly_flag")

    # Edges
    workflow.add_conditional_edges(
        "set_assembly_flag",
        after_set_flag,
        {"load_window_config": "load_window_config", END: END},
    )

    workflow.add_conditional_edges(
        "load_window_config",
        after_load_config,
        {"load_messages": "load_messages", "clear_assembly_flag": "clear_assembly_flag"},
    )

    workflow.add_conditional_edges(
        "load_messages",
        after_load_messages,
        {
            "calculate_tiers": "calculate_tiers",
            "finalize_assembly": "finalize_assembly",
            "clear_assembly_flag": "clear_assembly_flag",
        },
    )

    workflow.add_conditional_edges(
        "calculate_tiers",
        after_calculate_tiers,
        {"select_llm": "select_llm", "finalize_assembly": "finalize_assembly"},
    )

    workflow.add_edge("select_llm", "summarize_chunks")
    workflow.add_edge("summarize_chunks", "consolidate_tier1")
    workflow.add_edge("consolidate_tier1", "finalize_assembly")
    workflow.add_edge("finalize_assembly", "clear_assembly_flag")
    workflow.add_edge("clear_assembly_flag", END)

    return workflow.compile()

```

## rogers-langgraph/flows/conversation_ops.py

```python
"""
Conversation CRUD operations.

Simple database operations that don't require StateGraph orchestration.
These are single-step handlers called directly from Quart route handlers.
"""

import logging
from typing import Optional

from services import database as db
from services import mcp_client

_log = logging.getLogger("rogers-langgraph.flows.conversation_ops")


async def create_conversation(
    conversation_id: str,
    flow_id: str,
    flow_name: Optional[str] = None,
    user_id: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """Create a new conversation or return existing one."""
    row = await db.insert_conversation(
        conversation_id=conversation_id,
        flow_id=flow_id,
        flow_name=flow_name,
        user_id=user_id,
        title=title,
    )
    return _serialise_conversation(row)


async def create_context_window(
    conversation_id: str,
    build_type_id: str,
    max_token_limit: int,
) -> dict:
    """Create a context window instance for a conversation + build type."""
    row = await db.insert_context_window(
        conversation_id=conversation_id,
        build_type_id=build_type_id,
        max_token_limit=max_token_limit,
    )
    return _serialise_context_window(row)


async def get_history(conversation_id: str) -> dict:
    """Get full conversation + messages in chronological order."""
    conv = await db.get_conversation(conversation_id)
    if not conv:
        return {"error": "Conversation not found"}

    messages = await db.get_conversation_messages(conversation_id)
    return {
        "conversation": _serialise_conversation(conv),
        "messages": [_serialise_message(m) for m in messages],
    }


async def search_conversations(
    query: Optional[str] = None,
    # ... (rest of args)
) -> dict:
    """Search conversations: semantic + structured filters."""
    query_embedding = None
    if query:
        try:
            embedding_tool = mcp_client.get_tool("llm_embeddings")
            result = await embedding_tool.ainvoke({"input": query})
            query_embedding = result.get("embedding")
            if not query_embedding:
                _log.warning("Embedding service returned no embedding for query")
        except Exception as exc:
            _log.warning("Could not embed search query (embedding service unavailable): %s", exc)
            # Fall through to structured-only search

    results = await db.search_conversations(
        query_embedding=query_embedding,
        # ... (rest of db call)
    )
    return {
        "conversations": [_serialise_conversation(r) for r in results],
    }


async def search_messages(
    query: Optional[str] = None,
    # ... (rest of args)
) -> dict:
    """Search messages: hybrid vector + BM25 retrieval with cross-encoder reranking."""
    query_embedding = None
    if query:
        try:
            embedding_tool = mcp_client.get_tool("llm_embeddings")
            result = await embedding_tool.ainvoke({"input": query})
            query_embedding = result.get("embedding")
            if not query_embedding:
                _log.warning("Embedding service returned no embedding for query")
        except Exception as exc:
            _log.warning("Could not embed search query (embedding service unavailable): %s", exc)

    # Stage 1: Hybrid retrieval
    stage1_limit = 50 if query else limit
    results = await db.search_messages(
        query_embedding=query_embedding,
        query_text=query,
        # ... (rest of db call)
    )

    # Stage 2: Cross-encoder reranker
    if query and len(results) > 1:
        try:
            rerank_tool = mcp_client.get_tool("llm_rerank")
            docs = [m["content"] for m in results]
            rerank_result = await rerank_tool.ainvoke({
                "model_name": "BAAI/bge-reranker-v2-m3",
                "query": query,
                "documents": docs,
                "top_n": min(10, len(results)),
            })
            reranked = rerank_result.get("results", [])
            
            # Apply scores and re-sort
            for item in reranked:
                idx = item.get("index")
                if idx is not None and 0 <= idx < len(results):
                    results[idx]["score"] = item.get("relevance_score", 0.0)
            results = sorted(results, key=lambda x: x.get("score", 0.0), reverse=True)[:10]
            _log.debug("Reranker applied: %d candidates → %d results", len(docs), len(results))
        except Exception as exc:
            _log.warning("Reranker unavailable, returning Stage 1 results: %s", exc)
            results = results[:limit]

    return {
        "messages": [_serialise_message(m) for m in results],
    }


async def search_context_windows_handler(
    context_window_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    build_type_id: Optional[str] = None,
) -> dict:
    """Search/list context windows."""
    import uuid as uuid_mod

    cw_id = uuid_mod.UUID(context_window_id) if context_window_id else None
    results = await db.search_context_windows(
        context_window_id=cw_id,
        conversation_id=conversation_id,
        build_type_id=build_type_id,
    )
    return {
        "context_windows": [_serialise_context_window(r) for r in results],
    }


# ============================================================
# Serialisation helpers
# ============================================================


def _serialise_conversation(row: dict) -> dict:
    """Convert a conversation row to JSON-safe dict."""
    result = dict(row)
    for key in ("created_at", "updated_at"):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    # Include best_match_score if present (from semantic search)
    return result


def _serialise_message(row: dict) -> dict:
    """Convert a message row to JSON-safe dict."""
    result = dict(row)
    result["id"] = str(result["id"])
    if result.get("created_at") is not None:
        result["created_at"] = result["created_at"].isoformat()
    # Remove embedding vector from output (large, not useful for display)
    result.pop("embedding", None)
    # Convert score float if present
    if "score" in result and result["score"] is not None:
        result["score"] = float(result["score"])
    return result


def _serialise_context_window(row: dict) -> dict:
    """Convert a context window row to JSON-safe dict."""
    result = dict(row)
    result["id"] = str(result["id"])
    for key in ("created_at", "last_assembled_at"):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    return result

```

## rogers-langgraph/flows/embed_pipeline.py

```python
"""
Embed Pipeline — LangGraph StateGraph flow.

Orchestrates the embedding pipeline for a single message:
    fetch_message -> embed_message -> store_embedding -> check_context_assembly -> queue_memory_extraction

Triggered by the queue worker pulling from the embedding_jobs Redis list.
After embedding, fans out to both context_assembly_jobs and memory_extraction_jobs
queues for downstream processing by their own independent consumers.

REQ-rogers §7.2
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

from services import database as db
from services import mcp_client

_log = logging.getLogger("rogers-langgraph.flows.embed_pipeline")

# Module-level config (set by configure())
_config: dict = {}


def configure(config: dict):
    """Receive runtime config from server.py startup."""
    global _config
    _config = config


# Priority score offsets for memory_extraction_jobs ZSET.
# Each bucket is 10^12 apart (much larger than any realistic unix timestamp ~1.8x10^9),
# guaranteeing that all messages at priority N score higher than all at priority N+1
# regardless of timestamp. Within a bucket, newest message_created_at scores highest.
_PRIORITY_OFFSET = {
    0: 3_000_000_000_000,  # P0: live user interactions  (highest urgency)
    1: 2_000_000_000_000,  # P1: interactive agent comms
    2: 1_000_000_000_000,  # P2: background agent prose
    3: 0,                  # P3: migration / bulk backlog (lowest urgency)
}


# ============================================================
# State definition
# ============================================================


class EmbedPipelineState(TypedDict):
    # Inputs (from job payload)
    message_id: str                     # UUID string of the message to embed
    conversation_id: str                # Conversation this message belongs to

    # Intermediate (set by nodes)
    message: Optional[dict]             # Full message row from postgres
    embedding: Optional[list]           # 768-dim vector from Sutherland

    # Output
    assembly_jobs_queued: list          # List of context_window_ids queued for assembly
    extraction_queued: bool             # Whether memory extraction was queued
    error: Optional[str]                # Error message if any step failed


# ============================================================
# Node functions — each does one thing (async)
# ============================================================


async def fetch_message(state: EmbedPipelineState) -> dict:
    """Fetch the message row from postgres by UUID.

    If the message is not found, sets error and short-circuits.
    """
    message_id = uuid.UUID(state["message_id"])
    msg = await db.get_message_by_id(message_id)
    if not msg:
        return {"error": f"Message {state['message_id']} not found"}
    return {"message": msg}


async def embed_message(state: EmbedPipelineState) -> dict:
    """Call Sutherland's llm_embeddings tool for 768-dim embedding of message content."""
    message = state["message"]
    ctx_size = _config.get("embedding", {}).get("context_window_size", 0)
    embed_text = message["content"]

    if ctx_size > 0:
        try:
            prior = await db.get_prior_messages(
                conversation_id=message["conversation_id"],
                before_seq=message["sequence_number"],
                limit=ctx_size,
            )
            if prior:
                context_lines = "\n".join(
                    f"[{m['role']}] {m['content'][:500]}" for m in prior
                )
                embed_text = f"[Context]\n{context_lines}\n[Current]\n{message['content']}"
        except Exception as exc:
            _log.warning(
                "Could not fetch prior messages for contextual embedding msg=%s: %s",
                state["message_id"], exc,
            )

    try:
        # Get the standard LangChain tool from the MCP client
        embedding_tool = mcp_client.get_tool("llm_embeddings")
        
        # Invoke the tool
        result = await embedding_tool.ainvoke({"input": embed_text})
        
        # The tool returns a dict like {'embedding': [...]}, so extract the vector
        embedding = result.get("embedding")
        if not embedding:
            return {"error": f"Sutherland 'llm_embeddings' tool returned no embedding: {result}"}
            
        return {"embedding": embedding}
    except Exception as exc:
        _log.error(f"Failed to get embedding for message {state['message_id']}: {exc}", exc_info=True)
        return {"error": f"Embedding service call failed: {exc}"}


async def store_embedding(state: EmbedPipelineState) -> dict:
    """Write the embedding vector to the message row in postgres."""
    await db.store_embedding(uuid.UUID(state["message_id"]), state["embedding"])
    return {}


async def check_context_assembly(state: EmbedPipelineState) -> dict:
    """Check all context windows for this conversation and queue assembly if threshold crossed.

    For each window, compares estimated_token_count against the window's
    max_token_limit * trigger_threshold_percent. If crossed AND the message
    represents new data since the last assembly, pushes a context_assembly_jobs
    entry to Redis.

    Two guards prevent queue spam:
    1. Redis assembly_in_progress flag — skip if assembly already running
    2. last_assembled_at vs message created_at — skip if this message is
       already covered by the last assembly run
    """
    conversation_id = state["conversation_id"]
    conv = await db.get_conversation(conversation_id)
    if not conv:
        return {"assembly_jobs_queued": []}

    windows = await db.get_context_windows_for_conversation(conversation_id)
    queued = []
    redis = db.get_redis()
    now = datetime.now(timezone.utc).isoformat()

    # Message created_at for recency comparison
    msg = state.get("message", {})
    msg_created_at = msg.get("created_at")

    for window in windows:
        build_type = await db.get_build_type(window["build_type_id"])
        if not build_type:
            continue
        threshold = window["max_token_limit"] * build_type["trigger_threshold_percent"]
        if conv["estimated_token_count"] >= threshold:
            # Guard 1: Skip if assembly already in progress for this window
            assembly_key = f"assembly_in_progress:{window['id']}"
            if await redis.exists(assembly_key):
                _log.debug(
                    "Skipping assembly queue: already in progress window=%s",
                    window["id"],
                )
                continue

            # Guard 2: Skip if window was assembled after this message was created
            # (this message is already covered by the last assembly run)
            last_assembled = window.get("last_assembled_at")
            if last_assembled and msg_created_at and last_assembled >= msg_created_at:
                _log.debug(
                    "Skipping assembly queue: window=%s last_assembled_at=%s "
                    ">= msg created_at=%s",
                    window["id"],
                    last_assembled,
                    msg_created_at,
                )
                continue

            job = json.dumps({
                "conversation_id": conversation_id,
                "context_window_id": str(window["id"]),
                "build_type_id": window["build_type_id"],
                "job_type": "context_assembly",
                "enqueued_at": now,
                "attempt": 1,
            })
            await redis.lpush("context_assembly_jobs", job)
            queued.append(str(window["id"]))
            _log.info(
                "Queued context assembly: window=%s tokens=%d threshold=%d",
                window["id"],
                conv["estimated_token_count"],
                int(threshold),
            )

    return {"assembly_jobs_queued": queued}


async def queue_memory_extraction(state: EmbedPipelineState) -> dict:
    """Queue a memory extraction job for this conversation if applicable.

    Only queues extraction if the message is content_type='conversation' AND
    memory_extracted is falsy (Fix 8 guard — prevents feedback loop where
    every embed re-adds extraction jobs for already-extracted conversations).

    Uses priority-based scoring in the ZSET so higher-priority messages
    (live user interactions) are extracted before bulk migration data.
    """
    msg = state["message"]
    if msg.get("content_type") != "conversation" or msg.get("memory_extracted"):
        return {"extraction_queued": False}

    redis = db.get_redis()
    msg_ts = (
        msg["created_at"].timestamp()
        if msg.get("created_at")
        else datetime.now(timezone.utc).timestamp()
    )
    priority = msg.get("priority", 3)
    score = _PRIORITY_OFFSET.get(priority, 0) + msg_ts

    extraction_member = json.dumps({
        "conversation_id": state["conversation_id"],
        "job_type": "extract_memory",
    })
    await redis.zadd("memory_extraction_jobs", {extraction_member: score}, gt=True)
    return {"extraction_queued": True}


# ============================================================
# Routing function
# ============================================================


def check_error_after_fetch(state: EmbedPipelineState) -> str:
    """Route: if message not found, go to END. Otherwise, continue to embed."""
    if state.get("error"):
        return END
    return "embed_message"


# ============================================================
# Graph construction
# ============================================================


def build_embed_pipeline() -> StateGraph:
    """Build and compile the embed pipeline StateGraph.

    Called once at module load time in server.py.
    """
    workflow = StateGraph(EmbedPipelineState)

    # Add nodes
    workflow.add_node("fetch_message", fetch_message)
    workflow.add_node("embed_message", embed_message)
    workflow.add_node("store_embedding", store_embedding)
    workflow.add_node("check_context_assembly", check_context_assembly)
    workflow.add_node("queue_memory_extraction", queue_memory_extraction)

    # Set entry point
    workflow.set_entry_point("fetch_message")

    # Add edges
    workflow.add_conditional_edges(
        "fetch_message",
        check_error_after_fetch,
        {"embed_message": "embed_message", END: END},
    )
    workflow.add_edge("embed_message", "store_embedding")
    workflow.add_edge("store_embedding", "check_context_assembly")
    workflow.add_edge("check_context_assembly", "queue_memory_extraction")
    workflow.add_edge("queue_memory_extraction", END)

    return workflow.compile()

```

## rogers-langgraph/flows/imperator.py

```python
import json
import logging
from typing import Annotated, Literal, Optional, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from services import mcp_client

_log = logging.getLogger("rogers-langgraph.imperator")

ToolName = Literal["conv_search", "mem_search", "respond_directly", "finish"]

class ImperatorGraphState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    status: str
    step_count: int
    max_steps: int
    current_tool: Optional[ToolName]
    current_tool_input: Optional[dict]
    last_observation: Optional[str]
    final_response: Optional[str]
    reasoning_log: list[str]

def _parse_json(text: str) -> dict:
    cleaned = text.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned)

def _first_user_message(state: ImperatorGraphState) -> str:
    for msg in state.get("messages", []):
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return ""

def _default_state(state: ImperatorGraphState) -> ImperatorGraphState:
    if "status" not in state: state["status"] = "in_progress"
    if "step_count" not in state: state["step_count"] = 0
    if "max_steps" not in state: state["max_steps"] = 4
    if "reasoning_log" not in state: state["reasoning_log"] = []
    return state

async def decide_next_action(state: ImperatorGraphState) -> dict:
    state = _default_state(state)
    if state["status"] == "completed" or state["step_count"] >= state["max_steps"]:
        return {"status": "completed", "current_tool": "finish"}

    user_request = _first_user_message(state)
    prompt = f"""You are the Rogers Imperator, a tool-using agent for conversation and memory analysis.
Decide which tool to use to answer the user's request.

User Request: "{user_request}"
Last Tool Observation: {state.get('last_observation', 'None')}
Reasoning Log: {state.get('reasoning_log', [])}

Available tools:
- conv_search: Search conversation history. Use for "what was said".
- mem_search: Search extracted knowledge. Use for "what is known".
- respond_directly: Answer without a tool if you have enough info.
- finish: End interaction.

Return ONLY JSON:
{{
  "tool": "conv_search|mem_search|respond_directly|finish",
  "tool_input": {{ "query": "...", "user_id": "{state['user_id']}" }},
  "response": "Answer if responding directly or finishing",
  "reason": "Brief reason for your choice"
}}"""

    try:
        sutherland_chat = mcp_client.get_tool("llm_chat_completions")
        model_resp = await sutherland_chat.ainvoke({"model_alias": "imperator-fast", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1})
        parsed = _parse_json(model_resp["content"][0]["text"])
        tool = parsed.get("tool", "respond_directly")
        reason = parsed.get("reason", "No reason provided.")
        tool_input = parsed.get("tool_input", {})
        response = parsed.get("response", "")
    except Exception as exc:
        _log.error("Imperator decision parse failed: %s", exc)
        tool, reason, tool_input, response = "respond_directly", f"Fallback due to error: {exc}", {}, "I encountered an error while processing your request."

    if tool in ("respond_directly", "finish"):
        return {"status": "completed", "current_tool": tool, "final_response": response or "Action complete.", "reasoning_log": state["reasoning_log"] + [f"{tool}: {reason}"]}

    return {"current_tool": tool, "current_tool_input": tool_input, "reasoning_log": state["reasoning_log"] + [f"{tool}: {reason}"]}

async def execute_selected_tool(state: ImperatorGraphState) -> dict:
    state = _default_state(state)
    tool_name = state.get("current_tool")
    tool_input = state.get("current_tool_input", {})
    if tool_name not in ("conv_search", "mem_search"):
        return {}

    try:
        tool_to_run = mcp_client.get_tool(tool_name)
        result = await tool_to_run.ainvoke(tool_input)
        observation = json.dumps(result)
    except Exception as exc:
        _log.error("Imperator tool %s failed: %s", tool_name, exc)
        observation = f"Tool {tool_name} failed: {exc}"

    return {"step_count": state["step_count"] + 1, "last_observation": observation, "messages": [AIMessage(content=observation)]}

def route_after_decision(state: ImperatorGraphState) -> str:
    return "execute_selected_tool" if state.get("current_tool") in ("conv_search", "mem_search") else "finalize_response"

def route_after_tool(state: ImperatorGraphState) -> str:
    return "decide_next_action" if state.get("step_count", 0) < state.get("max_steps", 4) else "finalize_response"

async def finalize_response(state: ImperatorGraphState) -> dict:
    if state.get("final_response"):
        return {"status": "completed"}
    final_observation = state.get('last_observation') or "Hopper completed the requested action."
    return {"status": "completed", "final_response": final_observation, "messages": [AIMessage(content=final_observation)]}

def build_imperator_graph() -> StateGraph:
    workflow = StateGraph(ImperatorGraphState)
    workflow.add_node("decide_next_action", decide_next_action)
    workflow.add_node("execute_selected_tool", execute_selected_tool)
    workflow.add_node("finalize_response", finalize_response)

    workflow.set_entry_point("decide_next_action")
    workflow.add_conditional_edges("decide_next_action", route_after_decision)
    workflow.add_conditional_edges("execute_selected_tool", route_after_tool)
    workflow.add_edge("finalize_response", END)

    return workflow.compile()

```

## rogers-langgraph/flows/memory_extraction.py

```python
"""
Memory Extraction — LangGraph StateGraph flow.

Orchestrates Mem0 knowledge extraction from unprocessed conversational messages:
    fetch_unextracted -> build_extraction_text -> acquire_extraction_lock
    -> run_mem0_extraction -> mark_extracted -> release_extraction_lock

Triggered by the queue worker pulling from the memory_extraction_jobs Redis ZSET.
Messages are fetched newest-first within a character budget, routed to small/large
LLM based on text length, and marked as extracted after successful mem0.add().

The extraction lock (Fix 2B) prevents concurrent extraction of the same
conversation, saving LLM compute. The database-level unique index (Fix 2A)
handles cross-conversation dedup at the storage layer.

Credentials/secrets are redacted before text reaches Mem0 (Fix 1).

REQ-rogers §7.4
"""

import asyncio
import logging
from typing import Optional

from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

from services import database as db

_log = logging.getLogger("rogers-langgraph.flows.memory_extraction")

# Module-level references set by configure()
_config: dict = {}
_postgres_password: str = ""
_neo4j_password: str = ""
_gateway_url: str = ""


def configure(config: dict, pg_pass: str, neo4j_pass: str, gateway_url: str):
    """Set module-level config needed for Mem0 initialisation.

    Called from server.py at startup, before any graph invocations.
    """
    global _config, _postgres_password, _neo4j_password, _gateway_url
    _config = config
    _postgres_password = pg_pass
    _neo4j_password = neo4j_pass
    _gateway_url = gateway_url


# ============================================================
# State definition (REQ §7.4)
# ============================================================


class MemoryExtractionState(TypedDict):
    # Input (from job payload)
    conversation_id: str

    # Intermediate (set by nodes)
    messages: list                      # Unextracted conversational messages
    user_id: str                        # From conversation row
    conversation_text: str              # Built text for Mem0 input
    selected_message_ids: list          # Message IDs included in the text
    extraction_tier: str                # "small" or "large" — determines Mem0 instance
    extraction_lock_key: str            # Redis key for extraction lock (Fix 2B)

    # Output
    extracted_count: int                # Number of messages marked as extracted
    error: Optional[str]


# ============================================================
# Node functions
# ============================================================


async def fetch_unextracted(state: MemoryExtractionState) -> dict:
    """Get messages where content_type='conversation' AND memory_extracted=FALSE.

    Also fetches the conversation row for user_id. If no unextracted messages
    are found, sets empty messages list (routing function will short-circuit to END).
    """
    conversation_id = state["conversation_id"]

    messages = await db.get_unextracted_messages(conversation_id)
    if not messages:
        return {"messages": [], "extracted_count": 0}

    conv = await db.get_conversation(conversation_id)
    user_id = (conv or {}).get("user_id") or "unknown"

    return {"messages": messages, "user_id": user_id}


async def build_extraction_text(state: MemoryExtractionState) -> dict:
    """Build conversation text from messages newest-first with character budget.

    Routes to small or large Mem0 instance based on text length vs config
    thresholds (small_llm_max_chars, large_llm_max_chars).

    Redacts credentials/secrets (Fix 1) before the text is returned.
    """
    ext_cfg = _config.get("memory_extraction", {})
    small_max = ext_cfg.get("small_llm_max_chars", 90_000)
    large_max = ext_cfg.get("large_llm_max_chars", 450_000)

    messages = state["messages"]

    # Build lines newest-first so we always include the most recent content
    lines = []
    for m in reversed(messages):
        lines.append(
            (m["id"], f"{m['role']} (sender {m['sender_id']}): {m['content']}")
        )

    selected: list[str] = []
    selected_ids: list = []
    total_chars = 0

    for msg_id, line in lines:
        if len(line) > large_max:
            line = line[:large_max]  # pre-truncate oversized individual message
        addition = len(line) + 1     # +1 for the joining newline
        if total_chars + addition > large_max:
            if not selected:
                # Safety: always include at least one message
                selected.append(line[:large_max])
                selected_ids.append(msg_id)
            break
        selected.append(line)
        selected_ids.append(msg_id)
        total_chars += addition

    # Reverse back to chronological order
    selected.reverse()
    selected_ids.reverse()
    conversation_text = "\n".join(selected)

    # Redact credentials/secrets before text reaches Mem0
    from services.secret_filter import redact_secrets

    conversation_text = redact_secrets(conversation_text)

    # Route to small or large LLM based on text size
    tier = "small" if len(conversation_text) <= small_max else "large"

    _log.info(
        "perf.extract.route: tier=%s text_len=%d msgs=%d conv=%s",
        tier,
        len(conversation_text),
        len(selected_ids),
        state["conversation_id"],
    )

    return {
        "conversation_text": conversation_text,
        "selected_message_ids": selected_ids,
        "extraction_tier": tier,
    }


async def acquire_extraction_lock(state: MemoryExtractionState) -> dict:
    """Acquire Redis lock to prevent concurrent extraction of the same conversation.

    Uses nx=True (set-if-not-exists) with 120s TTL. If another extraction is
    already running for this conversation, skips gracefully (routes to END).

    This saves LLM compute — the database unique index (Fix 2A) handles
    correctness at the storage layer regardless.
    """
    lock_key = f"extraction_in_progress:{state['conversation_id']}"
    redis = db.get_redis()
    acquired = await redis.set(lock_key, "1", ex=120, nx=True)
    if not acquired:
        _log.info(
            "Memory extraction: already in progress for conv=%s — skipping",
            state["conversation_id"],
        )
        return {"extraction_lock_key": "", "error": "extraction_already_in_progress"}
    return {"extraction_lock_key": lock_key}


async def run_mem0_extraction(state: MemoryExtractionState) -> dict:
    """Call mem0.add() via the appropriate Mem0 instance.

    Small tier -> Sutherland/Qwen (local LLM via peer proxy).
    Large tier -> Gemini Flash-Lite (cloud API).

    Runs in thread pool executor because Mem0 operations are synchronous.
    """
    from services.mem0_setup import get_mem0_small, get_mem0_large

    tier = state["extraction_tier"]
    mem0_fn = get_mem0_small if tier == "small" else get_mem0_large

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: mem0_fn(
            _config, _postgres_password, _neo4j_password, _gateway_url
        ).add(
            state["conversation_text"],
            user_id=state["user_id"],
            run_id=state["conversation_id"],
        ),
    )

    return {}


async def mark_extracted(state: MemoryExtractionState) -> dict:
    """Mark all selected message IDs as memory_extracted=TRUE in postgres.

    Only called after successful mem0.add() — if extraction fails, the
    messages remain unextracted and will be retried on next queue pick-up.
    """
    selected_ids = state["selected_message_ids"]
    await db.mark_messages_extracted(selected_ids)

    _log.info(
        "perf.extract: tier=%s conv=%s msgs=%d text_len=%d",
        state["extraction_tier"],
        state["conversation_id"],
        len(selected_ids),
        len(state["conversation_text"]),
    )

    return {"extracted_count": len(selected_ids)}


async def release_extraction_lock(state: MemoryExtractionState) -> dict:
    """Release the Redis extraction lock after successful extraction.

    If the lock key is empty (lock was never acquired), this is a no-op.
    """
    lock_key = state.get("extraction_lock_key")
    if lock_key:
        redis = db.get_redis()
        await redis.delete(lock_key)

    return {}


# ============================================================
# Routing functions
# ============================================================


def after_fetch(state: MemoryExtractionState) -> str:
    """Route: if no messages or error, go to END. Otherwise, continue to build text."""
    if state.get("error"):
        return END
    if not state.get("messages"):
        return END
    return "build_extraction_text"


def after_lock(state: MemoryExtractionState) -> str:
    """Route: if lock not acquired, skip to END. Otherwise, proceed to extraction."""
    if state.get("error"):
        return END
    return "run_mem0_extraction"


# ============================================================
# Graph construction
# ============================================================


def build_memory_extraction() -> StateGraph:
    """Build and compile the memory extraction StateGraph.

    Called once at module load time in server.py.

    Graph structure:
        [fetch_unextracted]
           /           \\
     (no messages       (has messages)
      or error -> END)       |
                   [build_extraction_text]
                            |
                  [acquire_extraction_lock]
                     /            \\
               (not acquired       (acquired)
                -> END)                |
                            [run_mem0_extraction]
                                    |
                             [mark_extracted]
                                    |
                          [release_extraction_lock]
                                    |
                                   END
    """
    workflow = StateGraph(MemoryExtractionState)

    # Add nodes
    workflow.add_node("fetch_unextracted", fetch_unextracted)
    workflow.add_node("build_extraction_text", build_extraction_text)
    workflow.add_node("acquire_extraction_lock", acquire_extraction_lock)
    workflow.add_node("run_mem0_extraction", run_mem0_extraction)
    workflow.add_node("mark_extracted", mark_extracted)
    workflow.add_node("release_extraction_lock", release_extraction_lock)

    # Set entry point
    workflow.set_entry_point("fetch_unextracted")

    # Add edges
    workflow.add_conditional_edges(
        "fetch_unextracted",
        after_fetch,
        {"build_extraction_text": "build_extraction_text", END: END},
    )
    workflow.add_edge("build_extraction_text", "acquire_extraction_lock")
    workflow.add_conditional_edges(
        "acquire_extraction_lock",
        after_lock,
        {"run_mem0_extraction": "run_mem0_extraction", END: END},
    )
    workflow.add_edge("run_mem0_extraction", "mark_extracted")
    workflow.add_edge("mark_extracted", "release_extraction_lock")
    workflow.add_edge("release_extraction_lock", END)

    return workflow.compile()

```

## rogers-langgraph/flows/memory_ops.py

```python
"""
Memory operations — wrappers around Mem0.

Simple handlers that don't require StateGraph orchestration.
Called directly from Quart route handlers.

All Mem0 operations (including get_mem0 initialization) are synchronous
and run in thread pool executors to avoid blocking the async event loop.
"""

import asyncio
import logging
import uuid
from typing import Optional

import httpx

_log = logging.getLogger("rogers-langgraph.flows.memory_ops")


async def mem_search(
    query: str,
    user_id: str,
    config: dict,
    pg_pass: str,
    neo4j_pass: str,
    gw_url: str,
    conversation_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """Semantic + graph search across extracted knowledge."""
    try:
        loop = asyncio.get_event_loop()
        kwargs = {"user_id": user_id, "agent_id": agent_id, "limit": limit}
        if conversation_id:
            kwargs["run_id"] = conversation_id

        # Both get_mem0() and m.search() are sync — run entirely in executor (B2 fix)
        def _run():
            from services.mem0_setup import get_mem0

            m = get_mem0(config, pg_pass, neo4j_pass, gw_url)
            return m.search(query, **kwargs)

        raw = await loop.run_in_executor(None, _run)

        # v1.1 returns {"results": [...], "relations": [...]};
        # v1.0 returns a list
        memories = raw.get("results", []) if isinstance(raw, dict) else raw
        relations = raw.get("relations", []) if isinstance(raw, dict) else []
        return {
            "memories": {"results": memories, "relations": relations},
            "degraded": False,
        }
    except (RuntimeError, ValueError, KeyError, httpx.HTTPStatusError, httpx.ConnectError) as exc:
        _log.error("mem_search failed: %s", exc)
        return {
            "memories": {"results": [], "relations": []},
            "degraded": True,
            "error": str(exc),
        }


async def mem_get_context(
    query: str,
    user_id: str,
    config: dict,
    pg_pass: str,
    neo4j_pass: str,
    gw_url: str,
    conversation_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    limit: int = 5,
) -> dict:
    """Get relevant memories formatted for prompt injection."""
    try:
        loop = asyncio.get_event_loop()
        kwargs = {"user_id": user_id, "agent_id": agent_id, "limit": limit}
        if conversation_id:
            kwargs["run_id"] = conversation_id

        # Both get_mem0() and m.search() are sync — run entirely in executor (B2 fix)
        def _run():
            from services.mem0_setup import get_mem0

            m = get_mem0(config, pg_pass, neo4j_pass, gw_url)
            return m.search(query, **kwargs)

        raw = await loop.run_in_executor(None, _run)

        memories = raw.get("results", []) if isinstance(raw, dict) else raw
        if memories:
            context_lines = ["Relevant memories about this user:"]
            for mem in memories:
                text = mem.get("memory") or mem.get("content") or str(mem)
                context_lines.append(f"- {text}")
            context = "\n".join(context_lines)
        else:
            context = ""
        return {"context": context, "memories": memories}
    except (RuntimeError, ValueError, KeyError, httpx.HTTPStatusError, httpx.ConnectError) as exc:
        _log.error("mem_get_context failed: %s", exc)
        return {"context": "", "memories": []}


async def mem_add(
    content: str,
    user_id: str,
    config: dict,
    pg_pass: str,
    neo4j_pass: str,
    gw_url: str,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    """Add a memory to the knowledge graph (internal tool)."""
    try:
        # Redact credentials/secrets before text reaches Mem0
        from services.secret_filter import redact_secrets

        content = redact_secrets(content)

        loop = asyncio.get_event_loop()

        def _run():
            from services.mem0_setup import get_mem0

            m = get_mem0(config, pg_pass, neo4j_pass, gw_url)
            return m.add(content, user_id=user_id, agent_id=agent_id, run_id=run_id)

        result = await loop.run_in_executor(None, _run)
        memory_id = (
            result.get("id", str(uuid.uuid4()))
            if isinstance(result, dict)
            else str(uuid.uuid4())
        )
        return {"memory_id": memory_id, "status": "added"}
    except (RuntimeError, ValueError, KeyError, httpx.HTTPStatusError, httpx.ConnectError) as exc:
        _log.error("mem_add failed: %s", exc)
        return {"error": str(exc)}


async def mem_list(
    user_id: str,
    config: dict,
    pg_pass: str,
    neo4j_pass: str,
    gw_url: str,
    agent_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List all memories for a user (internal/admin tool)."""
    try:
        loop = asyncio.get_event_loop()

        def _run():
            from services.mem0_setup import get_mem0

            m = get_mem0(config, pg_pass, neo4j_pass, gw_url)
            return m.get_all(user_id=user_id, agent_id=agent_id)

        result = await loop.run_in_executor(None, _run)
        memories = result.get("results", []) if isinstance(result, dict) else result
        relations = result.get("relations", []) if isinstance(result, dict) else []
        total = len(memories)
        page = memories[offset : offset + limit]
        return {"memories": page, "relations": relations, "total": total}
    except (RuntimeError, ValueError, KeyError, httpx.HTTPStatusError, httpx.ConnectError) as exc:
        _log.error("mem_list failed: %s", exc)
        return {"error": str(exc)}


async def mem_delete(
    memory_id: str,
    config: dict,
    pg_pass: str,
    neo4j_pass: str,
    gw_url: str,
) -> dict:
    """Delete a memory by ID (internal/admin tool)."""
    try:
        loop = asyncio.get_event_loop()

        def _run():
            from services.mem0_setup import get_mem0

            m = get_mem0(config, pg_pass, neo4j_pass, gw_url)
            return m.delete(memory_id)

        await loop.run_in_executor(None, _run)
        return {"status": "deleted", "deleted_id": memory_id}
    except (RuntimeError, ValueError, KeyError, httpx.HTTPStatusError, httpx.ConnectError) as exc:
        _log.error("mem_delete failed: %s", exc)
        return {"error": str(exc)}

```

## rogers-langgraph/flows/message_pipeline.py

```python
"""
Message Pipeline — LangGraph StateGraph flow.

Orchestrates the conv_store_message pipeline:
    dedup_check -> store_message -> queue_embed -> return result

The embedding, context assembly, and memory extraction happen asynchronously
via the queue worker after this flow completes.

REQ-rogers §7.1
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

from services import database as db

_log = logging.getLogger("rogers-langgraph.flows.message_pipeline")


# ============================================================
# State definition (REQ §7.1)
# ============================================================


class MessagePipelineState(TypedDict):
    # Inputs
    conversation_id: str
    context_window_id: Optional[str]
    role: str
    sender_id: int
    content: str
    token_count: Optional[int]
    model_name: Optional[str]
    external_session_id: Optional[str]
    content_type: Optional[str]
    priority: int                   # Queue priority: 0=live user (highest) .. 3=migration (lowest)

    # Outputs (set by nodes)
    message_id: Optional[str]
    sequence_number: Optional[int]
    deduplicated: bool
    queued_jobs: list
    error: Optional[str]


# ============================================================
# Node functions — each does one thing
# ============================================================


async def resolve_conversation(state: MessagePipelineState) -> dict:
    """Resolve conversation_id from context_window_id if needed."""
    conversation_id = state.get("conversation_id")
    context_window_id = state.get("context_window_id")

    if context_window_id and not conversation_id:
        window = await db.get_context_window(uuid.UUID(context_window_id))
        if not window:
            return {"error": f"Context window {context_window_id} not found"}
        conversation_id = window["conversation_id"]

    if not conversation_id:
        return {"error": "Either conversation_id or context_window_id must be provided"}

    return {"conversation_id": conversation_id}


async def dedup_check(state: MessagePipelineState) -> dict:
    """Check if this message is a consecutive duplicate from the same sender.

    If duplicate: update the existing message's repeat count suffix,
    log the event, and mark as deduplicated.
    """
    conversation_id = state["conversation_id"]
    sender_id = state["sender_id"]
    content = state["content"]

    last_msg = await db.get_last_message_from_sender(conversation_id, sender_id)
    if last_msg is None:
        return {"deduplicated": False}

    # Strip any existing repeat count suffix for comparison
    existing_content = last_msg["content"]
    base_content = re.sub(r"\n\n---\n\[repeated \d+ times\]$", "", existing_content)

    if base_content.strip() != content.strip():
        return {"deduplicated": False}

    # It's a duplicate — update repeat count
    match = re.search(r"\[repeated (\d+) times\]$", existing_content)
    if match:
        count = int(match.group(1)) + 1
        new_content = re.sub(
            r"\[repeated \d+ times\]$",
            f"[repeated {count} times]",
            existing_content,
        )
    else:
        count = 2
        new_content = f"{existing_content}\n\n---\n[repeated {count} times]"

    await db.update_message_content(last_msg["id"], new_content)

    if count <= 2:
        _log.info(
            "Consecutive duplicate: conversation=%s sender=%s count=%d",
            conversation_id,
            sender_id,
            count,
        )
    elif count >= 5:
        _log.warning(
            "Repeated message loop: conversation=%s sender=%s count=%d",
            conversation_id,
            sender_id,
            count,
        )

    return {
        "deduplicated": True,
        "message_id": str(last_msg["id"]),
        "sequence_number": last_msg["sequence_number"],
        "queued_jobs": [],
    }


async def store_message(state: MessagePipelineState) -> dict:
    """Insert the message into conversation_messages and update counters."""
    if state.get("deduplicated"):
        return {}  # Skip — already handled by dedup

    conversation_id = state["conversation_id"]
    seq = await db.get_next_sequence_number(conversation_id)

    msg_row = await db.insert_message(
        conversation_id=conversation_id,
        role=state["role"],
        sender_id=state["sender_id"],
        content=state["content"],
        sequence_number=seq,
        token_count=state.get("token_count"),
        model_name=state.get("model_name"),
        external_session_id=state.get("external_session_id"),
        content_type=state.get("content_type"),
        priority=state.get("priority", 3),
    )

    return {
        "message_id": str(msg_row["id"]),
        "sequence_number": msg_row["sequence_number"],
    }


async def queue_embed(state: MessagePipelineState) -> dict:
    """Queue an embedding job in Redis for the queue worker to process."""
    if state.get("deduplicated"):
        return {}

    queued = []
    try:
        redis = db.get_redis()
        job = json.dumps(
            {
                "message_id": state["message_id"],
                "conversation_id": state["conversation_id"],
                "job_type": "embed",
                "enqueued_at": datetime.now(timezone.utc).isoformat(),
                "attempt": 1,
                "priority": state.get("priority", 3),
            }
        )
        await redis.lpush("embedding_jobs", job)
        queued.append("embed")
    except Exception as exc:
        _log.warning("Could not queue embedding job (redis unavailable): %s", exc)

    return {"queued_jobs": queued}


# ============================================================
# Routing function
# ============================================================


def should_continue_after_dedup(state: MessagePipelineState) -> str:
    """Route: if deduplicated, go straight to END. Otherwise, store the message."""
    if state.get("error"):
        return END
    if state.get("deduplicated"):
        return END
    return "store_message"


def should_continue_after_resolve(state: MessagePipelineState) -> str:
    """Route: if error resolving conversation, go to END."""
    if state.get("error"):
        return END
    return "dedup_check"


# ============================================================
# Graph construction
# ============================================================


def build_message_pipeline() -> StateGraph:
    """Build and compile the message pipeline StateGraph."""
    workflow = StateGraph(MessagePipelineState)

    # Add nodes
    workflow.add_node("resolve_conversation", resolve_conversation)
    workflow.add_node("dedup_check", dedup_check)
    workflow.add_node("store_message", store_message)
    workflow.add_node("queue_embed", queue_embed)

    # Set entry point
    workflow.set_entry_point("resolve_conversation")

    # Add edges
    workflow.add_conditional_edges(
        "resolve_conversation",
        should_continue_after_resolve,
        {"dedup_check": "dedup_check", END: END},
    )
    workflow.add_conditional_edges(
        "dedup_check",
        should_continue_after_dedup,
        {"store_message": "store_message", END: END},
    )
    workflow.add_edge("store_message", "queue_embed")
    workflow.add_edge("queue_embed", END)

    return workflow.compile()

```

## rogers-langgraph/flows/retrieval.py

```python
"""
Context Retrieval — LangGraph StateGraph flow.

Orchestrates the conv_retrieve_context pipeline:
    get_window -> check_assembly -> get_summaries -> get_recent -> assemble

Blocks if context assembly is in progress (with timeout).

REQ-rogers §7.5
"""

import asyncio
import logging
import uuid
from typing import Optional

from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

from services import database as db

_log = logging.getLogger("rogers-langgraph.flows.retrieval")

ASSEMBLY_WAIT_TIMEOUT = 50  # seconds (must be < gateway routing.timeout of 60s)
ASSEMBLY_POLL_INTERVAL = 2  # seconds


# ============================================================
# State definition
# ============================================================


class RetrievalState(TypedDict):
    # Input
    context_window_id: str

    # Intermediate
    conversation_id: Optional[str]
    build_type: Optional[dict]
    window: Optional[dict]
    max_token_limit: int
    tier1_summary: Optional[str]
    tier2_summaries: list
    recent_messages: list

    # Output
    context: Optional[str]
    tiers: Optional[dict]
    total_tokens: int
    assembly_status: str  # 'ready', 'blocked_waiting', 'error'
    error: Optional[str]


# ============================================================
# Node functions
# ============================================================


async def get_window(state: RetrievalState) -> dict:
    """Fetch the context window and its build type configuration."""
    window_id = uuid.UUID(state["context_window_id"])
    window = await db.get_context_window(window_id)
    if not window:
        return {
            "error": f"Context window {state['context_window_id']} not found",
            "assembly_status": "error",
        }

    build_type = await db.get_build_type(window["build_type_id"])
    if not build_type:
        return {
            "error": f"Build type {window['build_type_id']} not found",
            "assembly_status": "error",
        }

    return {
        "window": window,
        "conversation_id": window["conversation_id"],
        "build_type": build_type,
        "max_token_limit": window["max_token_limit"],
    }


async def check_assembly(state: RetrievalState) -> dict:
    """If context assembly is in progress, block and wait (with timeout)."""
    if state.get("error"):
        return {}

    redis = db.get_redis()
    assembly_key = f"assembly_in_progress:{state['context_window_id']}"

    waited = 0
    while waited < ASSEMBLY_WAIT_TIMEOUT:
        in_progress = await redis.get(assembly_key)
        if not in_progress:
            return {"assembly_status": "ready"}

        _log.info(
            "Context assembly in progress for window=%s, waiting (%ds/%ds)",
            state["context_window_id"],
            waited,
            ASSEMBLY_WAIT_TIMEOUT,
        )
        await asyncio.sleep(ASSEMBLY_POLL_INTERVAL)
        waited += ASSEMBLY_POLL_INTERVAL

    _log.warning(
        "Context assembly timeout for window=%s after %ds",
        state["context_window_id"],
        ASSEMBLY_WAIT_TIMEOUT,
    )
    return {"assembly_status": "blocked_waiting"}


async def get_summaries(state: RetrievalState) -> dict:
    """Fetch Tier 1 (archival) and Tier 2 (chunk) summaries for this window."""
    if state.get("error"):
        return {}

    window_id = uuid.UUID(state["context_window_id"])
    summaries = await db.get_active_summaries_for_window(window_id)

    tier1 = None
    tier2_list = []
    for s in summaries:
        if s["tier"] == 1:
            tier1 = s["summary_text"]
        elif s["tier"] == 2:
            tier2_list.append(s["summary_text"])

    return {"tier1_summary": tier1, "tier2_summaries": tier2_list}


async def get_recent(state: RetrievalState) -> dict:
    """Fetch Tier 3 recent messages within the remaining token budget."""
    if state.get("error"):
        return {}

    conversation_id = state["conversation_id"]
    max_limit = state["max_token_limit"]

    # Calculate token budget used by summaries (rough estimate: chars / 4)
    summary_tokens = 0
    if state.get("tier1_summary"):
        summary_tokens += len(state["tier1_summary"]) // 4
    for s in state.get("tier2_summaries") or []:
        summary_tokens += len(s) // 4

    remaining_budget = max_limit - summary_tokens

    # Get messages in reverse chronological order, fill budget
    all_messages = await db.get_conversation_messages(conversation_id)
    recent = []
    used_tokens = 0
    for msg in reversed(all_messages):
        msg_tokens = msg.get("token_count") or 150
        if used_tokens + msg_tokens <= remaining_budget:
            recent.insert(0, msg)  # maintain chronological order
            used_tokens += msg_tokens
        else:
            break

    return {"recent_messages": recent, "total_tokens": summary_tokens + used_tokens}


async def assemble(state: RetrievalState) -> dict:
    """Format the three-tier context with XML markers."""
    if state.get("error"):
        return {}

    parts = []

    # Tier 1: Archival summary
    if state.get("tier1_summary"):
        parts.append(f"<archival_summary>{state['tier1_summary']}</archival_summary>")

    # Tier 2: Chunk summaries
    if state.get("tier2_summaries"):
        tier2_text = "\n\n".join(state["tier2_summaries"])
        parts.append(f"<chunk_summaries>{tier2_text}</chunk_summaries>")

    # Tier 3: Recent messages (verbatim)
    if state.get("recent_messages"):
        msg_lines = []
        for msg in state["recent_messages"]:
            role = msg.get("role", "unknown")
            sender = msg.get("sender_id", "?")
            content = msg.get("content", "")
            msg_lines.append(f"[{role} | sender:{sender}] {content}")
        tier3_text = "\n".join(msg_lines)
        parts.append(f"<recent_messages>{tier3_text}</recent_messages>")

    context = "\n\n".join(parts)

    tiers = {
        "archival_summary": state.get("tier1_summary"),
        "chunk_summaries": state.get("tier2_summaries") or [],
        "recent_messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "sender_id": m["sender_id"],
                "content": m["content"],
                "sequence_number": m["sequence_number"],
            }
            for m in (state.get("recent_messages") or [])
        ],
    }

    return {"context": context, "tiers": tiers}


# ============================================================
# Routing
# ============================================================


def check_error(state: RetrievalState) -> str:
    """Route to END if there's an error."""
    if state.get("error"):
        return END
    return "check_assembly"


# ============================================================
# Graph construction
# ============================================================


def build_retrieval_flow() -> StateGraph:
    """Build and compile the context retrieval StateGraph."""
    workflow = StateGraph(RetrievalState)

    workflow.add_node("get_window", get_window)
    workflow.add_node("check_assembly", check_assembly)
    workflow.add_node("get_summaries", get_summaries)
    workflow.add_node("get_recent", get_recent)
    workflow.add_node("assemble", assemble)

    workflow.set_entry_point("get_window")

    workflow.add_conditional_edges(
        "get_window",
        check_error,
        {"check_assembly": "check_assembly", END: END},
    )
    workflow.add_edge("check_assembly", "get_summaries")
    workflow.add_edge("get_summaries", "get_recent")
    workflow.add_edge("get_recent", "assemble")
    workflow.add_edge("assemble", END)

    return workflow.compile()

```

## rogers-langgraph/services/__init__.py

```python

```

## rogers-langgraph/services/config.py

```python
"""
Configuration and credential management for Rogers LangGraph container.

Credentials are loaded from files on /storage/credentials/ at startup.
Never from environment variables or hardcoded values. (REQ-000 §3.6)

Credential file paths are read from config.json (O2: config-driven pattern
per REQ-langgraph-template), not hardcoded in Python source.
"""

import json
import logging
import os

_log = logging.getLogger("rogers-langgraph.config")

# Default credential paths (used if config.json doesn't specify them)
_DEFAULT_POSTGRES_CREDS = "/storage/credentials/rogers/postgres.txt"
_DEFAULT_NEO4J_CREDS = "/storage/credentials/rogers/neo4j.txt"


def load_config() -> dict:
    """Load rogers-langgraph config.json from the application directory."""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
    )
    try:
        with open(config_path) as f:
            return json.load(f)
    except Exception as exc:
        raise RuntimeError(f"Cannot read config from {config_path}: {exc}") from exc


def load_postgres_password(config: dict = None) -> str:
    """Read postgres password from credentials file.

    Path is read from config['credentials']['postgres_password_file'],
    falling back to the default /storage/credentials/rogers/postgres.txt.
    """
    creds_file = (config or {}).get("credentials", {}).get(
        "postgres_password_file"
    ) or _DEFAULT_POSTGRES_CREDS
    try:
        with open(creds_file) as f:
            return f.read().strip()
    except Exception as exc:
        raise RuntimeError(f"Cannot read credentials from {creds_file}: {exc}") from exc


def load_neo4j_password(config: dict = None) -> str:
    """Read Neo4j password from NEO4J_AUTH=neo4j/<password> in credentials file.

    Path is read from config['credentials']['neo4j_password_file'],
    falling back to the default /storage/credentials/rogers/neo4j.txt.
    """
    creds_file = (config or {}).get("credentials", {}).get(
        "neo4j_password_file"
    ) or _DEFAULT_NEO4J_CREDS
    try:
        with open(creds_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("NEO4J_AUTH="):
                    auth = line.split("=", 1)[1]  # neo4j/password
                    return auth.split("/", 1)[1]  # password
    except Exception as exc:
        raise RuntimeError(f"Cannot read credentials from {creds_file}: {exc}") from exc
    raise RuntimeError(f"NEO4J_AUTH not found in {creds_file}")

```

## rogers-langgraph/services/database.py

```python
"""
Async database connection management for Rogers.

Uses asyncpg for PostgreSQL and redis.asyncio for Redis.
All database operations are async functions callable from
Quart route handlers and LangGraph flow nodes.
"""

import logging
import os
from typing import Optional

import asyncpg
import redis.asyncio as aioredis

_log = logging.getLogger("rogers-langgraph.database")

# Module-level pool references (initialised at app startup)
_pg_pool: Optional[asyncpg.Pool] = None
_redis_client: Optional[aioredis.Redis] = None


async def init_postgres(password: str) -> asyncpg.Pool:
    """Create and return the asyncpg connection pool."""
    global _pg_pool
    _pg_pool = await asyncpg.create_pool(
        host=os.environ.get("POSTGRES_HOST", "rogers-postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        database=os.environ.get("POSTGRES_DB", "rogers"),
        user=os.environ.get("POSTGRES_USER", "rogers"),
        password=password,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    _log.info("PostgreSQL connection pool created")
    return _pg_pool


def init_redis() -> aioredis.Redis:
    """Create and return the async Redis client."""
    global _redis_client
    _redis_client = aioredis.Redis(
        host=os.environ.get("REDIS_HOST", "rogers-redis"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        decode_responses=True,
    )
    _log.info("Redis async client created")
    return _redis_client


def get_pg_pool() -> asyncpg.Pool:
    """Return the initialised asyncpg pool. Raises if not yet init'd."""
    if _pg_pool is None:
        raise RuntimeError(
            "PostgreSQL pool not initialised — call init_postgres() first"
        )
    return _pg_pool


def get_redis() -> aioredis.Redis:
    """Return the initialised async Redis client."""
    if _redis_client is None:
        raise RuntimeError("Redis client not initialised — call init_redis() first")
    return _redis_client


async def close_all():
    """Gracefully close database connections."""
    global _pg_pool, _redis_client
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None
        _log.info("PostgreSQL pool closed")
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        _log.info("Redis client closed")


# ============================================================
# Conversation queries
# ============================================================


async def insert_conversation(
    conversation_id: str,
    flow_id: str,
    flow_name: Optional[str] = None,
    user_id: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """Insert a new conversation and return its row."""
    pool = get_pg_pool()
    row = await pool.fetchrow(
        """INSERT INTO conversations (id, user_id, flow_id, flow_name, title)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (id) DO NOTHING
           RETURNING *""",
        conversation_id,
        user_id,
        flow_id,
        flow_name,
        title,
    )
    if row is None:
        # Already existed (ON CONFLICT DO NOTHING)
        row = await pool.fetchrow(
            "SELECT * FROM conversations WHERE id = $1", conversation_id
        )
    return dict(row)


async def get_conversation(conversation_id: str) -> Optional[dict]:
    """Fetch a single conversation by ID."""
    pool = get_pg_pool()
    row = await pool.fetchrow(
        "SELECT * FROM conversations WHERE id = $1", conversation_id
    )
    return dict(row) if row else None


async def get_next_sequence_number(conversation_id: str) -> int:
    """Get the next sequence number for a conversation."""
    pool = get_pg_pool()
    result = await pool.fetchval(
        "SELECT COALESCE(MAX(sequence_number), 0) FROM conversation_messages WHERE conversation_id = $1",
        conversation_id,
    )
    return result + 1


async def get_last_message_from_sender(
    conversation_id: str, sender_id: int
) -> Optional[dict]:
    """Get the most recent message from a specific sender in a conversation."""
    pool = get_pg_pool()
    row = await pool.fetchrow(
        """SELECT id, content, sequence_number
           FROM conversation_messages
           WHERE conversation_id = $1 AND sender_id = $2
           ORDER BY sequence_number DESC LIMIT 1""",
        conversation_id,
        sender_id,
    )
    return dict(row) if row else None


async def update_message_content(message_id, new_content: str):
    """Update the content of an existing message (for dedup repeat count suffix)."""
    pool = get_pg_pool()
    await pool.execute(
        "UPDATE conversation_messages SET content = $1 WHERE id = $2",
        new_content,
        message_id,
    )


async def insert_message(
    conversation_id: str,
    role: str,
    sender_id: int,
    content: str,
    sequence_number: int,
    token_count: Optional[int] = None,
    model_name: Optional[str] = None,
    external_session_id: Optional[str] = None,
    content_type: Optional[str] = None,
    priority: int = 3,
) -> dict:
    """Insert a message and update conversation counters. Returns the new message row."""
    pool = get_pg_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """INSERT INTO conversation_messages
                   (conversation_id, role, sender_id, content, token_count,
                    model_name, external_session_id, sequence_number, content_type, priority)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   RETURNING id, conversation_id, sequence_number, created_at, priority""",
                conversation_id,
                role,
                sender_id,
                content,
                token_count,
                model_name,
                external_session_id,
                sequence_number,
                content_type or "conversation",
                priority,
            )

            # Update conversation counters.
            # Use provided token_count when available; otherwise estimate from content
            # length (4 chars/token heuristic) so estimated_token_count always grows
            # and context-assembly threshold checks fire correctly.
            effective_tokens = token_count if token_count else max(1, len(content) // 4)
            await conn.execute(
                """UPDATE conversations
                   SET total_messages = total_messages + 1,
                       estimated_token_count = estimated_token_count + $1,
                       updated_at = NOW()
                   WHERE id = $2""",
                effective_tokens,
                conversation_id,
            )

    return dict(row)


async def store_embedding(message_id, embedding: list):
    """Store the embedding vector on a message row."""
    pool = get_pg_pool()
    # asyncpg requires the vector as a string for pgvector
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    await pool.execute(
        "UPDATE conversation_messages SET embedding = $1 WHERE id = $2",
        vec_str,
        message_id,
    )


async def get_message_by_id(message_id) -> Optional[dict]:
    """Fetch a single message by UUID."""
    pool = get_pg_pool()
    row = await pool.fetchrow(
        "SELECT * FROM conversation_messages WHERE id = $1", message_id
    )
    return dict(row) if row else None


# ============================================================
# Context window queries
# ============================================================


async def insert_context_window(
    conversation_id: str, build_type_id: str, max_token_limit: int
) -> dict:
    """Create a context window instance and return its row."""
    pool = get_pg_pool()

    # Validate conversation exists
    conv = await pool.fetchrow(
        "SELECT id FROM conversations WHERE id = $1", conversation_id
    )
    if conv is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    # Validate build type exists
    bt = await pool.fetchrow(
        "SELECT id FROM context_window_build_types WHERE id = $1", build_type_id
    )
    if bt is None:
        raise ValueError(f"Build type {build_type_id} not found")

    row = await pool.fetchrow(
        """INSERT INTO context_windows (conversation_id, build_type_id, max_token_limit)
           VALUES ($1, $2, $3)
           RETURNING *""",
        conversation_id,
        build_type_id,
        max_token_limit,
    )
    return dict(row)


async def get_context_window(context_window_id) -> Optional[dict]:
    """Fetch a context window by UUID."""
    pool = get_pg_pool()
    row = await pool.fetchrow(
        "SELECT * FROM context_windows WHERE id = $1", context_window_id
    )
    return dict(row) if row else None


async def get_context_windows_for_conversation(conversation_id: str) -> list:
    """Get all context windows for a conversation."""
    pool = get_pg_pool()
    rows = await pool.fetch(
        "SELECT * FROM context_windows WHERE conversation_id = $1", conversation_id
    )
    return [dict(r) for r in rows]


async def get_build_type(build_type_id: str) -> Optional[dict]:
    """Fetch a build type configuration."""
    pool = get_pg_pool()
    row = await pool.fetchrow(
        "SELECT * FROM context_window_build_types WHERE id = $1", build_type_id
    )
    return dict(row) if row else None


async def update_window_last_assembled(context_window_id):
    """Mark when context assembly was last completed for a window."""
    pool = get_pg_pool()
    await pool.execute(
        "UPDATE context_windows SET last_assembled_at = NOW() WHERE id = $1",
        context_window_id,
    )


# ============================================================
# Summary queries
# ============================================================


async def get_active_summaries_for_window(context_window_id) -> list:
    """Get all active summaries for a context window, ordered by tier then sequence."""
    pool = get_pg_pool()
    rows = await pool.fetch(
        """SELECT * FROM conversation_summaries
           WHERE context_window_id = $1 AND is_active = true
           ORDER BY tier ASC, summarizes_from_seq ASC""",
        context_window_id,
    )
    return [dict(r) for r in rows]


async def insert_summary(
    conversation_id: str,
    context_window_id,
    summary_text: str,
    tier: int,
    summarizes_from_seq: int,
    summarizes_to_seq: int,
    message_count: int,
    original_token_count: Optional[int] = None,
    summary_token_count: Optional[int] = None,
    summarized_by_model: Optional[str] = None,
) -> dict:
    """Insert a new summary record."""
    pool = get_pg_pool()
    row = await pool.fetchrow(
        """INSERT INTO conversation_summaries
           (conversation_id, context_window_id, summary_text, tier,
            summarizes_from_seq, summarizes_to_seq, message_count,
            original_token_count, summary_token_count, summarized_by_model)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
           RETURNING *""",
        conversation_id,
        context_window_id,
        summary_text,
        tier,
        summarizes_from_seq,
        summarizes_to_seq,
        message_count,
        original_token_count,
        summary_token_count,
        summarized_by_model,
    )
    return dict(row)


async def deactivate_summaries_for_window(
    context_window_id, tier: Optional[int] = None
):
    """Mark summaries as inactive (superseded) for a window, optionally filtered by tier."""
    pool = get_pg_pool()
    if tier is not None:
        await pool.execute(
            """UPDATE conversation_summaries
               SET is_active = false
               WHERE context_window_id = $1 AND tier = $2 AND is_active = true""",
            context_window_id,
            tier,
        )
    else:
        await pool.execute(
            """UPDATE conversation_summaries
               SET is_active = false WHERE context_window_id = $1 AND is_active = true""",
            context_window_id,
        )


# ============================================================
# Search queries
# ============================================================


async def search_conversations(
    query_embedding: Optional[list] = None,
    flow_id: Optional[str] = None,
    user_id: Optional[str] = None,
    sender_id: Optional[int] = None,
    external_session_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    """Search conversations: semantic (embedding similarity) and/or structured filters.

    When query_embedding is provided, finds matching messages via cosine similarity
    and groups results by conversation, ranked by best match score.
    When only structured filters are provided, returns conversations matching those filters.
    """
    pool = get_pg_pool()

    if query_embedding is not None:
        # Semantic search: find matching messages, group by conversation
        vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        # Build WHERE conditions for the message subquery
        conditions = ["m.embedding IS NOT NULL"]
        args = [vec_str]
        arg_idx = 2

        if sender_id is not None:
            conditions.append(f"m.sender_id = ${arg_idx}")
            args.append(sender_id)
            arg_idx += 1

        if external_session_id is not None:
            conditions.append(f"m.external_session_id = ${arg_idx}")
            args.append(external_session_id)
            arg_idx += 1

        if date_from is not None:
            conditions.append(f"m.created_at >= ${arg_idx}::timestamp")
            args.append(date_from)
            arg_idx += 1

        if date_to is not None:
            conditions.append(f"m.created_at <= ${arg_idx}::timestamp")
            args.append(date_to)
            arg_idx += 1

        where_clause = " AND ".join(conditions)

        # Conversation-level filters
        conv_conditions = ["1=1"]
        if flow_id is not None:
            conv_conditions.append(f"c.flow_id = ${arg_idx}")
            args.append(flow_id)
            arg_idx += 1
        if user_id is not None:
            conv_conditions.append(f"c.user_id = ${arg_idx}")
            args.append(user_id)
            arg_idx += 1

        conv_where = " AND ".join(conv_conditions)
        args.extend([limit, offset])

        query = f"""
            SELECT c.*, best.score AS best_match_score
            FROM conversations c
            JOIN (
                SELECT m.conversation_id,
                       MIN(m.embedding <=> $1::vector) AS score
                FROM conversation_messages m
                WHERE {where_clause}
                GROUP BY m.conversation_id
            ) best ON best.conversation_id = c.id
            WHERE {conv_where}
            ORDER BY best.score ASC
            LIMIT ${arg_idx} OFFSET ${arg_idx + 1}
        """
        rows = await pool.fetch(query, *args)
        return [dict(r) for r in rows]

    else:
        # Structured filter only (listing use case)
        conditions = ["1=1"]
        args = []
        arg_idx = 1

        if flow_id is not None:
            conditions.append(f"flow_id = ${arg_idx}")
            args.append(flow_id)
            arg_idx += 1

        if user_id is not None:
            conditions.append(f"user_id = ${arg_idx}")
            args.append(user_id)
            arg_idx += 1

        if date_from is not None:
            conditions.append(f"created_at >= ${arg_idx}::timestamp")
            args.append(date_from)
            arg_idx += 1

        if date_to is not None:
            conditions.append(f"created_at <= ${arg_idx}::timestamp")
            args.append(date_to)
            arg_idx += 1

        where_clause = " AND ".join(conditions)
        args.extend([limit, offset])

        query = f"""
            SELECT * FROM conversations
            WHERE {where_clause}
            ORDER BY updated_at DESC
            LIMIT ${arg_idx} OFFSET ${arg_idx + 1}
        """
        rows = await pool.fetch(query, *args)
        return [dict(r) for r in rows]


async def search_messages(
    query_embedding: Optional[list] = None,
    query_text: Optional[str] = None,
    conversation_id: Optional[str] = None,
    sender_id: Optional[int] = None,
    role: Optional[str] = None,
    external_session_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    """Search messages: hybrid vector + BM25 via Reciprocal Rank Fusion (RRF).

    Modes:
    - Both query_embedding + query_text: hybrid RRF with mild recency bias (best quality)
    - query_embedding only: vector cosine similarity
    - query_text only: BM25 full-text
    - Neither: structured filter listing by recency

    All modes respect conversation_id, sender_id, role, date_from, date_to filters.
    """
    pool = get_pg_pool()

    use_vector = query_embedding is not None
    use_bm25 = bool(query_text and query_text.strip())

    args = []
    arg_idx = 1

    # Reserve leading slots for search params
    vec_param = None
    bm25_param = None

    if use_vector:
        vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
        args.append(vec_str)
        vec_param = f"${arg_idx}"
        arg_idx += 1

    if use_bm25:
        args.append(query_text.strip())
        bm25_param = f"${arg_idx}"
        arg_idx += 1

    # Shared structured filters (applied inside CTEs)
    filter_conditions = []
    if conversation_id is not None:
        filter_conditions.append(f"conversation_id = ${arg_idx}")
        args.append(conversation_id)
        arg_idx += 1
    if sender_id is not None:
        filter_conditions.append(f"sender_id = ${arg_idx}")
        args.append(sender_id)
        arg_idx += 1
    if role is not None:
        filter_conditions.append(f"role = ${arg_idx}")
        args.append(role)
        arg_idx += 1
    if external_session_id is not None:
        filter_conditions.append(f"external_session_id = ${arg_idx}")
        args.append(external_session_id)
        arg_idx += 1
    if date_from is not None:
        filter_conditions.append(f"created_at >= ${arg_idx}::timestamp")
        args.append(date_from)
        arg_idx += 1
    if date_to is not None:
        filter_conditions.append(f"created_at <= ${arg_idx}::timestamp")
        args.append(date_to)
        arg_idx += 1

    filter_sql = (" AND " + " AND ".join(filter_conditions)) if filter_conditions else ""

    args.extend([limit, offset])
    limit_param = f"${arg_idx}"
    offset_param = f"${arg_idx + 1}"

    if use_vector and use_bm25:
        # Hybrid RRF: combine vector ANN + BM25, mild recency bias (max 20% penalty at 90 days)
        sql = f"""
            WITH vector_ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (ORDER BY embedding <=> {vec_param}::vector) AS rank
                FROM conversation_messages
                WHERE embedding IS NOT NULL{filter_sql}
                LIMIT 100
            ),
            bm25_ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           ORDER BY ts_rank(content_tsv, plainto_tsquery('english', {bm25_param})) DESC
                       ) AS rank
                FROM conversation_messages
                WHERE content_tsv @@ plainto_tsquery('english', {bm25_param}){filter_sql}
                LIMIT 100
            ),
            rrf AS (
                SELECT
                    COALESCE(v.id, b.id) AS id,
                    COALESCE(1.0 / (60 + v.rank), 0) + COALESCE(1.0 / (60 + b.rank), 0) AS rrf_score
                FROM vector_ranked v
                FULL OUTER JOIN bm25_ranked b ON v.id = b.id
            )
            SELECT m.id, m.conversation_id, m.role, m.sender_id, m.content,
                   m.token_count, m.model_name, m.external_session_id,
                   m.sequence_number, m.created_at,
                   r.rrf_score * (1.0 - 0.2 * LEAST(1.0,
                       EXTRACT(EPOCH FROM (NOW() - m.created_at)) / (90.0 * 86400)
                   )) AS score
            FROM conversation_messages m
            JOIN rrf r ON m.id = r.id
            ORDER BY score DESC
            LIMIT {limit_param} OFFSET {offset_param}
        """

    elif use_vector:
        # Vector-only (no BM25 column available or no text query)
        sql = f"""
            SELECT id, conversation_id, role, sender_id, content,
                   token_count, model_name, external_session_id,
                   sequence_number, created_at,
                   embedding <=> {vec_param}::vector AS score
            FROM conversation_messages
            WHERE embedding IS NOT NULL{filter_sql}
            ORDER BY score ASC
            LIMIT {limit_param} OFFSET {offset_param}
        """

    elif use_bm25:
        # BM25-only (no embedding provided)
        sql = f"""
            SELECT id, conversation_id, role, sender_id, content,
                   token_count, model_name, external_session_id,
                   sequence_number, created_at,
                   ts_rank(content_tsv, plainto_tsquery('english', {bm25_param})) AS score
            FROM conversation_messages
            WHERE content_tsv @@ plainto_tsquery('english', {bm25_param}){filter_sql}
            ORDER BY score DESC
            LIMIT {limit_param} OFFSET {offset_param}
        """

    else:
        # No search — structured filter listing by recency
        sql = f"""
            SELECT id, conversation_id, role, sender_id, content,
                   token_count, model_name, external_session_id,
                   sequence_number, created_at, 0.0 AS score
            FROM conversation_messages
            WHERE 1=1{filter_sql}
            ORDER BY created_at DESC
            LIMIT {limit_param} OFFSET {offset_param}
        """

    rows = await pool.fetch(sql, *args)
    return [dict(r) for r in rows]


async def get_conversation_messages(conversation_id: str) -> list:
    """Get all messages for a conversation in chronological order."""
    pool = get_pg_pool()
    rows = await pool.fetch(
        """SELECT id, conversation_id, role, sender_id, content,
                  token_count, model_name, external_session_id,
                  sequence_number, content_type, created_at
           FROM conversation_messages
           WHERE conversation_id = $1
           ORDER BY sequence_number ASC""",
        conversation_id,
    )
    return [dict(r) for r in rows]


async def search_context_windows(
    context_window_id=None,
    conversation_id: Optional[str] = None,
    build_type_id: Optional[str] = None,
) -> list:
    """Search/list context windows by various filters."""
    pool = get_pg_pool()
    conditions = []
    args = []
    arg_idx = 1

    if context_window_id is not None:
        conditions.append(f"id = ${arg_idx}")
        args.append(context_window_id)
        arg_idx += 1

    if conversation_id is not None:
        conditions.append(f"conversation_id = ${arg_idx}")
        args.append(conversation_id)
        arg_idx += 1

    if build_type_id is not None:
        conditions.append(f"build_type_id = ${arg_idx}")
        args.append(build_type_id)
        arg_idx += 1

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    rows = await pool.fetch(
        f"SELECT * FROM context_windows WHERE {where_clause} ORDER BY created_at DESC",
        *args,
    )
    return [dict(r) for r in rows]


async def get_messages_in_range(
    conversation_id: str, from_seq: int, to_seq: int
) -> list:
    """Get messages in a sequence number range (inclusive)."""
    pool = get_pg_pool()
    rows = await pool.fetch(
        """SELECT * FROM conversation_messages
           WHERE conversation_id = $1
             AND sequence_number >= $2 AND sequence_number <= $3
           ORDER BY sequence_number ASC""",
        conversation_id,
        from_seq,
        to_seq,
    )
    return [dict(r) for r in rows]


async def get_recent_messages(conversation_id: str, limit: int = 20) -> list:
    """Get the N most recent messages for a conversation."""
    pool = get_pg_pool()
    rows = await pool.fetch(
        """SELECT * FROM conversation_messages
           WHERE conversation_id = $1
           ORDER BY sequence_number DESC LIMIT $2""",
        conversation_id,
        limit,
    )
    return [dict(r) for r in reversed(rows)]  # Return in chronological order


async def get_prior_messages(
    conversation_id: str, before_seq: int, limit: int
) -> list:
    """Fetch the N messages immediately before sequence_number for embedding context.

    Used by embed_pipeline to build composite contextual text before embedding,
    giving semantic meaning to short messages.
    Returns messages in chronological order (oldest first).
    """
    pool = get_pg_pool()
    rows = await pool.fetch(
        """
        SELECT role, content, sequence_number
        FROM conversation_messages
        WHERE conversation_id = $1
          AND sequence_number < $2
          AND content_type = 'conversation'
        ORDER BY sequence_number DESC
        LIMIT $3
        """,
        conversation_id,
        before_seq,
        limit,
    )
    # Reverse so they are in chronological order for the context prefix
    return [dict(r) for r in reversed(rows)]


async def get_unextracted_messages(conversation_id: str) -> list:
    """Get conversational messages that have not been memory-extracted yet."""
    pool = get_pg_pool()
    rows = await pool.fetch(
        """SELECT * FROM conversation_messages
           WHERE conversation_id = $1
             AND content_type = 'conversation'
             AND (memory_extracted IS NOT TRUE)
           ORDER BY sequence_number ASC""",
        conversation_id,
    )
    return [dict(r) for r in rows]


async def mark_messages_extracted(message_ids: list) -> None:
    """Mark messages as memory-extracted after successful extraction."""
    if not message_ids:
        return
    pool = get_pg_pool()
    await pool.execute(
        """UPDATE conversation_messages
           SET memory_extracted = TRUE
           WHERE id = ANY($1::uuid[])""",
        message_ids,
    )

```

## rogers-langgraph/services/logging_setup.py

```python
"""
Structured JSON logging for Rogers LangGraph container.

All application logs go to stdout in JSON format (one entry per line).
Werkzeug /health request noise is filtered out.

Log level is read from config.json at startup:
  "log_level": "INFO"   — everyday use, key pipeline events only
  "log_level": "DEBUG"  — verbose, all decision points and queue activity
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


class _NoHealthFilter(logging.Filter):
    """Suppress noisy /health request logs from werkzeug."""

    def filter(self, record):
        return "/health" not in record.getMessage()


def _read_log_level() -> int:
    """Read log_level from config.json. Defaults to INFO if missing or invalid."""
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        level_str = config.get("log_level", "INFO").upper()
        level = getattr(logging, level_str, None)
        if isinstance(level, int):
            return level
    except (OSError, json.JSONDecodeError):
        pass
    return logging.INFO


def setup_logging():
    """Configure application and werkzeug loggers.

    Log level is read from config.json (log_level field).
    Returns the application logger ('rogers-langgraph').
    """
    level = _read_log_level()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    # Werkzeug logger — filter /health, no propagation
    wz = logging.getLogger("werkzeug")
    wz.handlers = [handler]
    wz.propagate = False
    wz.addFilter(_NoHealthFilter())

    # Application logger
    log = logging.getLogger("rogers-langgraph")
    log.setLevel(level)
    log.addHandler(handler)
    log.propagate = False

    log.info("Logging initialised at level=%s", logging.getLevelName(level))

    return log

```

## rogers-langgraph/services/mcp_client.py

```python
"""
State 2 MCP Tool Client

Uses langchain-mcp-adapters to create standard LangChain tools from peer MADs.
No custom HTTP calls. All logic is in the graph, using these tools.
"""

import logging
from typing import List

from langchain.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

_log = logging.getLogger("rogers-langgraph.mcp_client")

_client = None
_tools = {}

async def init_mcp_tools():
    """
    Initialize the MultiServerMCPClient and load tools from peer MADs.
    Called once at server startup.
    """
    global _client, _tools
    if _client:
        return

    _log.info("Initializing MCP client and loading peer tools...")
    _client = MultiServerMCPClient({
        "sutherland": {
            "url": "http://rogers:6380/proxy/sutherland/mcp",
            "transport": "streamable_http",
        }
    })

    try:
        loaded_tools: List[BaseTool] = await _client.get_tools()
        _tools = {tool.name: tool for tool in loaded_tools}
        _log.info(f"Loaded {len(_tools)} MCP tools: {list(_tools.keys())}")
    except Exception as e:
        _log.error(f"Failed to load MCP tools: {e}")
        _tools = {}

def get_tool(name: str) -> BaseTool:
    """
    Get a loaded LangChain tool by name.
    """
    tool = _tools.get(name)
    if not tool:
        raise ValueError(f"MCP tool '{name}' not found or failed to load. Available: {list(_tools.keys())}")
    return tool

def get_all_tools() -> List[BaseTool]:
    """
    Get all loaded LangChain tools.
    """
    return list(_tools.values())

```

## rogers-langgraph/services/mem0_setup.py

```python
"""
Mem0 lazy initialisation for Rogers.

Thread-safe singleton pattern. Two instances:
  - get_mem0_small(): uses the small/local LLM (config["llm"], default Sutherland/Qwen)
  - get_mem0_large(): uses the large/cloud LLM (config key from memory_extraction.large_llm_config_key)

Both instances share the same pgvector + Neo4j graph store but use different LLM
adapters for fact extraction / entity resolution. Embeddings always go through
Sutherland regardless of which LLM is used.

Mem0 is initialised on first use (not at startup) because it connects to Neo4j
and creates tables in postgres, which may not be ready immediately at container start.

Monkey-patches applied at init time:
  - PGVector.insert: ON CONFLICT DO NOTHING for hash-based deduplication (Fix 2)
  - UPDATE_GRAPH_PROMPT: Qwen-compatible prompt with explicit tool-use directive (Fix 3)
"""

import logging
import os
import threading

_log = logging.getLogger("rogers-langgraph.mem0_setup")

# ---------------------------------------------------------------------------
# Qwen-compatible graph prompt (Fix 3)
#
# Replaces Mem0's UPDATE_GRAPH_PROMPT which tells the LLM to "Provide a list
# of update instructions" as text. Qwen 7B interprets this literally and
# responds in natural language instead of using tool calls. This modified
# prompt preserves all original guidelines but:
#   1. Adds explicit tool-use directive at the top
#   2. Adds guideline 9 for handling empty existing memories
#   3. Removes the "Output: Provide a list..." line
# Tested: 12/12 correct tool_calls with Qwen across 4 scenarios.
# ---------------------------------------------------------------------------

_QWEN_COMPATIBLE_GRAPH_PROMPT = """\
You are an AI expert specializing in graph memory management and optimization. \
Your task is to analyze existing graph memories alongside new information, and \
update the relationships in the memory list to ensure the most accurate, current, \
and coherent representation of knowledge.

IMPORTANT: You MUST respond ONLY by calling the provided tools \
(add_graph_memory, update_graph_memory, or noop). Do NOT write text responses. \
Every response must be one or more tool calls.

Input:
1. Existing Graph Memories: A list of current graph memories, each containing \
source, target, and relationship information.
2. New Graph Memory: Fresh information to be integrated into the existing graph \
structure.

Guidelines:
1. Identification: Use the source and target as primary identifiers when \
matching existing memories with new information.
2. Conflict Resolution:
   - If new information contradicts an existing memory:
     a) For matching source and target but differing content, update the \
relationship of the existing memory.
     b) If the new memory provides more recent or accurate information, update \
the existing memory accordingly.
3. Comprehensive Review: Thoroughly examine each existing graph memory against \
the new information, updating relationships as necessary. Multiple updates may \
be required.
4. Consistency: Maintain a uniform and clear style across all memories. Each \
entry should be concise yet comprehensive.
5. Semantic Coherence: Ensure that updates maintain or improve the overall \
semantic structure of the graph.
6. Temporal Awareness: If timestamps are available, consider the recency of \
information when making updates.
7. Relationship Refinement: Look for opportunities to refine relationship \
descriptions for greater precision or clarity.
8. Redundancy Elimination: Identify and merge any redundant or highly similar \
relationships that may result from the update.
9. New Information: If the existing graph memories list is empty or the new \
information introduces entirely new entities not present in existing memories, \
use add_graph_memory to create new entries.

Task Details:
- Existing Graph Memories:
{existing_memories}

- New Graph Memory: {memory}
"""

# ---------------------------------------------------------------------------
# Monkey-patch flags (applied once globally, not per-instance)
# ---------------------------------------------------------------------------

_patches_applied = False
_patches_lock = threading.Lock()


def _apply_global_patches():
    """Apply monkey-patches to Mem0 internals (once, thread-safe).

    Fix 2 — PGVector.insert: Adds ON CONFLICT DO NOTHING to prevent duplicate
    memories with the same hash+user_id. Works with the unique index on
    mem0_memories ((payload->>'hash'), (payload->>'user_id')).

    Fix 3 — UPDATE_GRAPH_PROMPT: Replaces the default prompt with a
    Qwen-compatible version that uses explicit tool-call directives.
    """
    global _patches_applied
    if _patches_applied:
        return

    with _patches_lock:
        if _patches_applied:
            return

        # --- Fix 2: PGVector.insert dedup ---
        try:
            from mem0.vector_stores.pgvector import PGVector
            from psycopg2.extras import execute_values, Json

            _original_insert = PGVector.insert

            def _dedup_insert(self, vectors, payloads=None, ids=None):
                data = [
                    (id_, vector, Json(payload))
                    for id_, vector, payload in zip(ids, vectors, payloads)
                ]
                execute_values(
                    self.cur,
                    f"INSERT INTO {self.collection_name} (id, vector, payload) "
                    f"VALUES %s "
                    f"ON CONFLICT ((payload->>'hash'), (payload->>'user_id')) "
                    f"DO NOTHING",
                    data,
                )
                self.conn.commit()

            PGVector.insert = _dedup_insert
            _log.info("Monkey-patch applied: PGVector.insert (ON CONFLICT DO NOTHING)")
        except Exception as exc:
            _log.error("Failed to patch PGVector.insert: %s", exc)

        # --- Fix 3: UPDATE_GRAPH_PROMPT ---
        try:
            import mem0.graphs.utils as graph_utils

            graph_utils.UPDATE_GRAPH_PROMPT = _QWEN_COMPATIBLE_GRAPH_PROMPT
            _log.info("Monkey-patch applied: UPDATE_GRAPH_PROMPT (Qwen-compatible)")
        except Exception as exc:
            _log.error("Failed to patch UPDATE_GRAPH_PROMPT: %s", exc)

        _patches_applied = True


# ---------------------------------------------------------------------------
# Small LLM singleton (default: Sutherland / Qwen via peer proxy)
# ---------------------------------------------------------------------------

_mem0_small_instance = None
_mem0_small_lock = threading.Lock()


def get_mem0_small(
    config: dict, postgres_password: str, neo4j_password: str, gateway_url: str
):
    """Return the Mem0 Memory instance using the small/local LLM (lazy, thread-safe singleton).

    Args:
        config: Rogers config.json contents (for LLM provider settings)
        postgres_password: Postgres credential
        neo4j_password: Neo4j credential
        gateway_url: Rogers gateway URL for peer proxy calls
    """
    global _mem0_small_instance

    if _mem0_small_instance is not None:
        return _mem0_small_instance

    with _mem0_small_lock:
        if _mem0_small_instance is not None:
            return _mem0_small_instance

        mem = _build_mem0_instance(config, postgres_password, neo4j_password, gateway_url)

        # Inject small-LLM adapter (uses config["llm"])
        llm_adapter = _build_llm_adapter(config, gateway_url)
        from mem0_adapters import SutherlandEmbedderAdapter
        embed_adapter = SutherlandEmbedderAdapter(gateway_url=gateway_url)

        mem.llm = llm_adapter
        mem.embedding_model = embed_adapter
        if hasattr(mem, "graph") and mem.graph is not None:
            mem.graph.llm = llm_adapter
            mem.graph.embedding_model = embed_adapter

        _mem0_small_instance = mem
        _log.info(
            "Mem0 (small) initialised: provider=%s model=%s",
            config.get("llm", {}).get("provider"),
            config.get("llm", {}).get("model"),
        )

    return _mem0_small_instance


# ---------------------------------------------------------------------------
# Large LLM singleton (default: Gemini Flash-Lite via OpenAI-compatible API)
# ---------------------------------------------------------------------------

_mem0_large_instance = None
_mem0_large_lock = threading.Lock()


def get_mem0_large(
    config: dict, postgres_password: str, neo4j_password: str, gateway_url: str
):
    """Return the Mem0 Memory instance using the large/cloud LLM (lazy, thread-safe singleton).

    The LLM config key is read from config["memory_extraction"]["large_llm_config_key"],
    defaulting to "gemini_llm".
    """
    global _mem0_large_instance

    if _mem0_large_instance is not None:
        return _mem0_large_instance

    with _mem0_large_lock:
        if _mem0_large_instance is not None:
            return _mem0_large_instance

        mem = _build_mem0_instance(config, postgres_password, neo4j_password, gateway_url)

        # Determine which config key to use for the large LLM
        ext_cfg = config.get("memory_extraction", {})
        large_key = ext_cfg.get("large_llm_config_key", "gemini_llm")
        large_llm_cfg = config.get(large_key)
        if not large_llm_cfg:
            _log.warning(
                "get_mem0_large: config key %r not found, falling back to default llm",
                large_key,
            )
            large_llm_cfg = config.get("llm", {})

        llm_adapter = _build_llm_adapter({"llm": large_llm_cfg}, gateway_url)
        from mem0_adapters import SutherlandEmbedderAdapter
        embed_adapter = SutherlandEmbedderAdapter(gateway_url=gateway_url)

        mem.llm = llm_adapter
        mem.embedding_model = embed_adapter
        if hasattr(mem, "graph") and mem.graph is not None:
            mem.graph.llm = llm_adapter
            mem.graph.embedding_model = embed_adapter

        _mem0_large_instance = mem
        _log.info(
            "Mem0 (large) initialised: config_key=%s model=%s",
            large_key,
            large_llm_cfg.get("model"),
        )

    return _mem0_large_instance


# ---------------------------------------------------------------------------
# Backward-compatible alias — used by server.py /mem_extract endpoint
# ---------------------------------------------------------------------------

def get_mem0(
    config: dict, postgres_password: str, neo4j_password: str, gateway_url: str
):
    """Backward-compatible alias. Routes to get_mem0_small."""
    return get_mem0_small(config, postgres_password, neo4j_password, gateway_url)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_mem0_instance(config, postgres_password, neo4j_password, gateway_url):
    """Build a bare Mem0 Memory instance with pgvector + Neo4j (no LLM injected yet)."""
    # Apply global monkey-patches before any Memory construction
    _apply_global_patches()

    from mem0 import Memory
    from mem0.configs.base import (
        MemoryConfig,
        LlmConfig,
        EmbedderConfig,
        VectorStoreConfig,
        GraphStoreConfig,
    )

    # Use "openai" as placeholder provider to satisfy Pydantic validators.
    # All LLM/embedder slots are overwritten after construction.
    mem_config = MemoryConfig(
        version="v1.1",
        llm=LlmConfig(provider="openai", config={"api_key": "placeholder"}),
        embedder=EmbedderConfig(
            provider="openai", config={"api_key": "placeholder"}
        ),
        vector_store=VectorStoreConfig(
            provider="pgvector",
            config={
                "host": os.environ.get("POSTGRES_HOST", "rogers-postgres"),
                "port": int(os.environ.get("POSTGRES_PORT", 5432)),
                "dbname": os.environ.get("POSTGRES_DB", "rogers"),
                "user": os.environ.get("POSTGRES_USER", "rogers"),
                "password": postgres_password,
                "collection_name": "mem0_memories",
                "embedding_model_dims": 768,
                "diskann": False,
            },
        ),
        graph_store=GraphStoreConfig(
            provider="neo4j",
            config={
                "url": f"bolt://{os.environ.get('NEO4J_HOST', 'rogers-neo4j')}:7687",
                "username": "neo4j",
                "password": neo4j_password,
            },
        ),
    )

    return Memory(config=mem_config)


def _build_llm_adapter(config: dict, gateway_url: str):
    """Instantiate the LLM adapter specified in config.json."""
    from mem0_adapters import SutherlandLlmAdapter, OpenAICompatibleLlmAdapter

    llm_cfg = config.get("llm", {})
    provider = llm_cfg.get("provider", "sutherland")

    if provider == "sutherland":
        return SutherlandLlmAdapter(
            gateway_url=gateway_url,
            model=llm_cfg.get("model"),
            temperature=llm_cfg.get("temperature"),
        )
    elif provider == "openai_compatible":
        api_key = ""
        if "api_key_file" in llm_cfg:
            with open(llm_cfg["api_key_file"]) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        api_key = line.split("=", 1)[1]
                        break
        else:
            api_key_env = llm_cfg.get("api_key_env", "OPENAI_API_KEY")
            api_key = os.environ.get(api_key_env, "")

        # Read timeout from memory_extraction config
        ext_cfg = config.get("memory_extraction", {})
        timeout = ext_cfg.get("llm_timeout", 120)

        return OpenAICompatibleLlmAdapter(
            base_url=llm_cfg["base_url"],
            api_key=api_key,
            model=llm_cfg["model"],
            temperature=llm_cfg.get("temperature"),
            timeout=timeout,
        )
    else:
        raise ValueError(f"Unknown LLM provider in config.json: {provider!r}")

```

## rogers-langgraph/services/queue_worker.py

```python
"""
Background queue worker for Rogers.

Runs as an asyncio task in Quart's event loop. Three independent consumer
loops poll their own Redis queues and invoke compiled StateGraph flows.
This replaces the old sequential handler loop — embedding no longer blocks
extraction, and context assembly no longer blocks either.

Queue architecture:
    embedding_jobs         -> Embed Pipeline StateGraph
    context_assembly_jobs  -> Context Assembly StateGraph
    memory_extraction_jobs -> Memory Extraction StateGraph
    dead_letter_jobs       -> failed jobs after MAX_ATTEMPTS; swept periodically

REQ-rogers §7.6
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

from services import database as db

_log = logging.getLogger("rogers-langgraph.queue_worker")

QUEUE_POLL_INTERVAL = max(1, int(os.environ.get("QUEUE_POLL_INTERVAL", "5")))
MAX_ATTEMPTS = 3
DEAD_LETTER_SWEEP_INTERVAL = 60  # seconds between dead-letter sweeps

# Map job_type back to original queue for re-queuing (dead-letter sweep)
_JOB_TYPE_TO_QUEUE = {
    "embed": ("embedding_jobs", "list"),
    "context_assembly": ("context_assembly_jobs", "list"),
    "extract_memory": ("memory_extraction_jobs", "zset"),
}

# Module-level references set by configure()
_config: dict = {}
_postgres_password: str = ""
_neo4j_password: str = ""
_gateway_url: str = ""
_embed_graph = None
_assembly_graph = None
_extraction_graph = None


def configure(
    config: dict,
    pg_pass: str,
    neo4j_pass: str,
    gateway_url: str,
    embed_graph=None,
    assembly_graph=None,
    extraction_graph=None,
):
    """Set module-level config and compiled StateGraph references.

    Called from server.py at startup.
    """
    global _config, _postgres_password, _neo4j_password, _gateway_url
    global _embed_graph, _assembly_graph, _extraction_graph
    _config = config
    _postgres_password = pg_pass
    _neo4j_password = neo4j_pass
    _gateway_url = gateway_url
    _embed_graph = embed_graph
    _assembly_graph = assembly_graph
    _extraction_graph = extraction_graph


# ============================================================
# Shared error handling
# ============================================================


async def _handle_failure(
    job: dict,
    raw: str,
    queue_name: str,
    queue_type: str,
    error: Exception,
):
    """Handle a failed job: retry with backoff or dead-letter.

    - Increments attempt counter
    - If attempt <= MAX_ATTEMPTS: re-enqueue with exponential backoff (5^n seconds)
    - If attempt > MAX_ATTEMPTS: move to dead_letter_jobs queue
    """
    redis = db.get_redis()
    job["attempt"] = job.get("attempt", 1) + 1

    if job["attempt"] <= MAX_ATTEMPTS:
        backoff = 5 ** (job["attempt"] - 1)
        _log.warning(
            "Re-queuing (attempt=%s backoff=%ss): queue=%s error=%s",
            job["attempt"],
            backoff,
            queue_name,
            error,
        )
        if queue_type == "zset":
            score = job.get("message_score", datetime.now(timezone.utc).timestamp())
            await redis.zadd(queue_name, {json.dumps(job): score})
        else:
            await redis.lpush(queue_name, json.dumps(job))
        await asyncio.sleep(backoff)
    else:
        _log.error(
            "Dead-lettering after %s attempts: queue=%s error=%s job=%s",
            MAX_ATTEMPTS,
            queue_name,
            error,
            raw,
        )
        await redis.lpush("dead_letter_jobs", raw)


# ============================================================
# Individual queue consumers
# ============================================================


async def _consume_embedding_queue(graph):
    """Independent consumer loop for embedding_jobs (Redis list).

    Polls embedding_jobs, constructs initial state, invokes the Embed Pipeline
    StateGraph. Errors are handled via _handle_failure.
    """
    _log.info("Embedding consumer started")
    queue_name = "embedding_jobs"

    while True:
        raw = None
        job = None
        try:
            redis = db.get_redis()
            raw = await redis.rpop(queue_name)
            if not raw:
                await asyncio.sleep(QUEUE_POLL_INTERVAL)
                continue

            job = json.loads(raw)
            message_id = job.get("message_id", "")
            conversation_id = job.get("conversation_id", "")

            _log.info(
                "Processing: queue=%s message_id=%s attempt=%s",
                queue_name,
                message_id,
                job.get("attempt", 1),
            )

            start = time.monotonic()
            initial_state = {
                "message_id": message_id,
                "conversation_id": conversation_id,
                "message": None,
                "embedding": None,
                "assembly_jobs_queued": [],
                "extraction_queued": False,
                "error": None,
            }

            result = await graph.ainvoke(initial_state)

            if result.get("error"):
                _log.error(
                    "Embed pipeline error: message_id=%s error=%s",
                    message_id,
                    result["error"],
                )
            else:
                _log.info(
                    "perf.embed: message_id=%s elapsed_ms=%d",
                    message_id,
                    int((time.monotonic() - start) * 1000),
                )

        except Exception as exc:
            _log.error("Embed consumer error: %s", exc)
            if job is not None and raw is not None:
                await _handle_failure(job, raw, queue_name, "list", exc)
            else:
                await asyncio.sleep(QUEUE_POLL_INTERVAL)


async def _consume_context_assembly_queue(graph):
    """Independent consumer loop for context_assembly_jobs (Redis list).

    Polls context_assembly_jobs, constructs initial state, invokes the
    Context Assembly StateGraph.
    """
    _log.info("Context assembly consumer started")
    queue_name = "context_assembly_jobs"

    while True:
        raw = None
        job = None
        try:
            redis = db.get_redis()
            raw = await redis.rpop(queue_name)
            if not raw:
                await asyncio.sleep(QUEUE_POLL_INTERVAL)
                continue

            job = json.loads(raw)
            conversation_id = job.get("conversation_id", "")
            context_window_id = job.get("context_window_id", "")
            build_type_id = job.get("build_type_id", "small-basic")

            _log.info(
                "Processing: queue=%s window=%s attempt=%s",
                queue_name,
                context_window_id,
                job.get("attempt", 1),
            )

            start = time.monotonic()
            initial_state = {
                "conversation_id": conversation_id,
                "context_window_id": context_window_id,
                "build_type_id": build_type_id,
                "window": None,
                "build_type": None,
                "messages": [],
                "max_token_limit": 0,
                "tier3_budget": 0,
                "tier3_start_seq": 0,
                "older_messages": [],
                "chunks": [],
                "llm": None,
                "model_name": "",
                "tier2_summaries_written": 0,
                "tier1_consolidated": False,
                "assembly_key": "",
                "error": None,
            }

            result = await graph.ainvoke(initial_state)

            if result.get("error"):
                _log.error(
                    "Context assembly error: window=%s error=%s",
                    context_window_id,
                    result["error"],
                )
            else:
                _log.info(
                    "perf.assembly: window=%s build=%s elapsed_ms=%d",
                    context_window_id,
                    build_type_id,
                    int((time.monotonic() - start) * 1000),
                )

        except Exception as exc:
            _log.error("Assembly consumer error: %s", exc)
            if job is not None and raw is not None:
                await _handle_failure(job, raw, queue_name, "list", exc)
            else:
                await asyncio.sleep(QUEUE_POLL_INTERVAL)


async def _consume_memory_extraction_queue(graph):
    """Independent consumer loop for memory_extraction_jobs (Redis ZSET).

    Polls memory_extraction_jobs via zpopmax (highest-priority first),
    constructs initial state, invokes the Memory Extraction StateGraph.
    """
    _log.info("Memory extraction consumer started")
    queue_name = "memory_extraction_jobs"

    while True:
        raw = None
        job = None
        try:
            redis = db.get_redis()
            zresult = await redis.zpopmax(queue_name, count=1)
            if not zresult:
                await asyncio.sleep(QUEUE_POLL_INTERVAL)
                continue

            raw = zresult[0][0]
            job = json.loads(raw)
            conversation_id = job.get("conversation_id", "")

            _log.info(
                "Processing: queue=%s conv=%s attempt=%s",
                queue_name,
                conversation_id,
                job.get("attempt", 1),
            )

            start = time.monotonic()
            initial_state = {
                "conversation_id": conversation_id,
                "messages": [],
                "user_id": "",
                "conversation_text": "",
                "selected_message_ids": [],
                "extraction_tier": "",
                "extraction_lock_key": "",
                "extracted_count": 0,
                "error": None,
            }

            graph_result = await graph.ainvoke(initial_state)

            if graph_result.get("error"):
                _log.error(
                    "Memory extraction error: conv=%s error=%s",
                    conversation_id,
                    graph_result["error"],
                )
            else:
                _log.info(
                    "perf.extract: conv=%s extracted=%d elapsed_ms=%d",
                    conversation_id,
                    graph_result.get("extracted_count", 0),
                    int((time.monotonic() - start) * 1000),
                )

        except Exception as exc:
            _log.error("Extraction consumer error: %s", exc)
            if job is not None and raw is not None:
                await _handle_failure(job, raw, queue_name, "zset", exc)
            else:
                await asyncio.sleep(QUEUE_POLL_INTERVAL)


# ============================================================
# Dead-letter retry sweep
# ============================================================


async def _sweep_dead_letters():
    """Move dead-letter jobs back to their original queues for retry.

    Resets attempt counter to 1. Jobs that fail again will dead-letter again
    after MAX_ATTEMPTS, preventing infinite loops.
    """
    redis = db.get_redis()
    swept = 0

    # Process up to 10 dead-letter jobs per sweep
    for _ in range(10):
        raw = await redis.rpop("dead_letter_jobs")
        if not raw:
            break

        try:
            job = json.loads(raw)
            job_type = job.get("job_type", "")
            target = _JOB_TYPE_TO_QUEUE.get(job_type)

            if not target:
                _log.warning(
                    "Dead-letter sweep: unknown job_type=%s, discarding", job_type
                )
                continue

            target_queue, target_type = target

            # Reset attempt counter and re-enqueue
            job["attempt"] = 1
            job["requeued_from_dead_letter"] = True
            job["requeued_at"] = datetime.now(timezone.utc).isoformat()
            job_str = json.dumps(job)
            if target_type == "zset":
                score = job.get(
                    "message_score", datetime.now(timezone.utc).timestamp()
                )
                await redis.zadd(target_queue, {job_str: score})
            else:
                await redis.lpush(target_queue, job_str)
            swept += 1

        except Exception as exc:
            _log.error("Dead-letter sweep: failed to process job: %s", exc)

    if swept > 0:
        _log.info("Dead-letter sweep: re-queued %d jobs", swept)


async def _sweep_dead_letters_loop():
    """Periodic dead-letter sweep loop."""
    _log.info("Dead-letter sweep loop started (interval=%ss)", DEAD_LETTER_SWEEP_INTERVAL)
    while True:
        await asyncio.sleep(DEAD_LETTER_SWEEP_INTERVAL)
        try:
            await _sweep_dead_letters()
        except Exception as exc:
            _log.error("Dead-letter sweep error: %s", exc)


# ============================================================
# Main worker entry point
# ============================================================


async def run_worker():
    """Launch all queue consumers concurrently.

    Each consumer is an independent async loop that polls its own queue
    and invokes its compiled StateGraph. They do not block each other.
    """
    _log.info(
        "Queue worker started (poll_interval=%ss, max_attempts=%s, consumers=3)",
        QUEUE_POLL_INTERVAL,
        MAX_ATTEMPTS,
    )

    await asyncio.gather(
        _consume_embedding_queue(_embed_graph),
        _consume_context_assembly_queue(_assembly_graph),
        _consume_memory_extraction_queue(_extraction_graph),
        _sweep_dead_letters_loop(),
    )

```

## rogers-langgraph/services/secret_filter.py

```python
"""
Secret filter --- redacts credentials before text reaches Mem0.

Uses detect-secrets (Yelp) for regex + entropy detection, plus custom
detectors for patterns common in terminal/shell conversations (sshpass,
connection strings, bearer tokens).

Called from:
  - flows/memory_extraction.py (build_extraction_text) — before conversation
    text is sent to Mem0 for knowledge extraction
  - flows/memory_ops.py (mem_add) — before ad-hoc memory additions via MCP tool
"""

import logging
import re

from detect_secrets.plugins.keyword import KeywordDetector
from detect_secrets.plugins.basic_auth import BasicAuthDetector
from detect_secrets.plugins.base import RegexBasedDetector
from detect_secrets.plugins.high_entropy_strings import (
    Base64HighEntropyString,
    HexHighEntropyString,
)
from detect_secrets.plugins.aws import AWSKeyDetector
from detect_secrets.plugins.private_key import PrivateKeyDetector
from detect_secrets.plugins.jwt import JwtTokenDetector

_log = logging.getLogger("rogers-langgraph.services.secret_filter")


# ============================================================
# Custom detectors
# ============================================================


class SSHPassDetector(RegexBasedDetector):
    """Detects sshpass command-line password arguments.

    Matches:  sshpass -p 'Edgar01760'
              sshpass --password mypass123
    """

    secret_type = "sshpass password"

    denylist = [
        re.compile(r"sshpass\s+-p\s+['\"]?(\S+?)['\"]?(?:\s|$)"),
        re.compile(r"sshpass\s+--password\s+['\"]?(\S+?)['\"]?(?:\s|$)"),
    ]


class ConnectionStringDetector(RegexBasedDetector):
    """Detects passwords in connection strings (://user:pass@host).

    Matches:  postgresql://rogers:xK9mP2vL@rogers-postgres:5432/rogers
              mongodb://admin:s3cret@localhost/mydb
    """

    secret_type = "connection string password"

    denylist = [
        re.compile(r"://\w+:([^@\s]+)@"),
    ]


class BearerTokenDetector(RegexBasedDetector):
    """Detects Authorization/Bearer tokens.

    Matches:  Authorization: Bearer eyJhbG...
              Bearer sk-proj-abc123...
    """

    secret_type = "bearer token"

    denylist = [
        re.compile(r"[Aa]uthorization:\s*[Bb]earer\s+(\S+)"),
    ]


# ============================================================
# Plugin instances (created once at module load)
# ============================================================

_PLUGINS = [
    KeywordDetector(),
    BasicAuthDetector(),
    Base64HighEntropyString(limit=4.5),
    HexHighEntropyString(limit=3.0),
    AWSKeyDetector(),
    PrivateKeyDetector(),
    JwtTokenDetector(),
    SSHPassDetector(),
    ConnectionStringDetector(),
    BearerTokenDetector(),
]


# ============================================================
# Public API
# ============================================================


def redact_secrets(text: str) -> str:
    """Scan text line-by-line and replace detected secrets with [REDACTED].

    Args:
        text: Raw text (typically conversation content before Mem0 ingestion).

    Returns:
        Text with secrets replaced by [REDACTED].
    """
    lines = text.split("\n")
    redacted_lines = []
    total_redactions = 0

    for i, line in enumerate(lines):
        secrets: set = set()

        for plugin in _PLUGINS:
            try:
                results = plugin.analyze_line(
                    filename="memory_text",
                    line=line,
                    line_number=i + 1,
                )
                for potential_secret in results:
                    if potential_secret.secret_value:
                        secrets.add(potential_secret.secret_value)
            except Exception as exc:
                _log.debug(
                    "Detector %s failed on line %d: %s",
                    plugin.__class__.__name__, i + 1, exc,
                )
                continue

        if secrets:
            redacted_line = line
            for secret_val in secrets:
                redacted_line = redacted_line.replace(secret_val, "[REDACTED]")
            redacted_lines.append(redacted_line)
            total_redactions += len(secrets)
        else:
            redacted_lines.append(line)

    if total_redactions > 0:
        _log.info(
            "Redacted %d secret(s) from text (%d lines)", total_redactions, len(lines)
        )

    return "\n".join(redacted_lines)

```

## rogers-postgres/Dockerfile

```dockerfile
# rogers-postgres — PostgreSQL 16 + pgvector backing service
# Uses pgvector/pgvector:pg16 image (postgres:16-alpine base + pgvector pre-installed).
# This is a delta from the template-postgres pattern: pgvector cannot be installed
# at runtime — it requires compile-time build, so we use the pgvector community image.
#
# Build context: project root (.)

# Exception (approved): pgvector/pgvector:pg16 is a Debian-based community image.
# pgvector cannot be installed at runtime on the official postgres image; this is
# the only supported distribution method. Digest must be pinned before production:
#   docker pull pgvector/pgvector:pg16
#   docker inspect --format='{{index .RepoDigests 0}}' pgvector/pgvector:pg16
FROM pgvector/pgvector:pg16@sha256:33198da2828a14c30348d2ccb4750833d5ed9a44c88d840a0e523d7417120337

# Install cron from local cache (ADR-037). Run packages/download-packages.sh before building.
COPY mads/rogers/rogers-postgres/packages/apt/*.deb /tmp/apt-cache/

RUN apt-get update && \
    (dpkg -i /tmp/apt-cache/*.deb 2>/dev/null || true) && \
    apt-get install -y --no-install-recommends cron && \
    rm -rf /var/lib/apt/lists/* /tmp/apt-cache

# Initialise schema on first run (runs all .sql files in alpha order)
COPY mads/rogers/rogers-postgres/init.sql /docker-entrypoint-initdb.d/01-init.sql

# Backup script and cron schedule
COPY mads/rogers/rogers-postgres/backup.sh /backup.sh
COPY mads/rogers/rogers-postgres/backup.cron /etc/cron.d/rogers-backup

RUN chmod +x /backup.sh && chmod 0644 /etc/cron.d/rogers-backup

EXPOSE 5432

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD pg_isready -U ${POSTGRES_USER:-rogers} -d ${POSTGRES_DB:-rogers} || exit 1

# Start cron then hand off to postgres entrypoint
CMD ["sh", "-c", "cron && docker-entrypoint.sh postgres"]

```

## rogers-postgres/backup.cron

```text
0 2 * * * /backup.sh

```

## rogers-postgres/backup.sh

```bash
#!/bin/bash
set -e
BACKUP_DIR=/storage/backups/databases/rogers/postgres
mkdir -p $BACKUP_DIR
pg_dump rogers > $BACKUP_DIR/backup-$(date +%Y%m%d).sql
find $BACKUP_DIR -name "backup-*.sql" -mtime +7 -delete

```

## rogers-postgres/init.sql

```sql
-- Rogers v3.0 database schema
-- Loaded automatically by postgres entrypoint on first run.
-- Requires: pgvector extension (pre-installed in pgvector/pgvector:pg16 image)

CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- conversations
-- Top-level conversation entity. Participants are derived from
-- messages (SELECT DISTINCT sender_id), not stored here.
-- ============================================================

CREATE TABLE conversations (
    id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255),
    flow_id VARCHAR(255),
    flow_name VARCHAR(255),
    title VARCHAR(500),
    total_messages INTEGER DEFAULT 0,
    estimated_token_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_conversations_flow ON conversations(flow_id);
CREATE INDEX idx_conversations_flow_user ON conversations(user_id, flow_id, updated_at DESC);

-- ============================================================
-- conversation_messages
-- One row per message. Any number of participants, any role.
-- sequence_number provides ordering within a conversation.
-- ============================================================

CREATE TABLE conversation_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id VARCHAR(255) NOT NULL REFERENCES conversations(id),
    role VARCHAR(50) NOT NULL,              -- 'user', 'assistant', 'system', 'tool'
    sender_id INTEGER NOT NULL,             -- system UID of participant
    content TEXT NOT NULL,
    token_count INTEGER,
    model_name VARCHAR(100),                -- populated when sender is an LLM
    external_session_id VARCHAR(255),       -- external tool session provenance (nullable)
    embedding vector(768),                  -- single embedding per message
    sequence_number INTEGER NOT NULL,       -- ordering within conversation
    content_type VARCHAR(50) DEFAULT 'conversation',  -- Gemini classification: conversation|tool_output|info|error
    priority SMALLINT DEFAULT 3,                      -- Queue priority: 0=live user (highest), 1=interactive, 2=agent-comms, 3=migration (lowest)
    memory_extracted BOOLEAN DEFAULT FALSE,            -- set TRUE after Mem0 extraction
    created_at TIMESTAMP DEFAULT NOW(),
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED
);

CREATE INDEX idx_messages_conversation ON conversation_messages(conversation_id);
CREATE INDEX idx_messages_conversation_seq ON conversation_messages(conversation_id, sequence_number);
CREATE INDEX idx_messages_conversation_sender ON conversation_messages(conversation_id, sender_id, sequence_number DESC);
CREATE INDEX idx_messages_created ON conversation_messages(created_at);
-- ivfflat: approximate nearest-neighbour index. lists=100 is the recommended
-- starting value for datasets up to ~1M rows (sqrt(rows) heuristic). Increase
-- to 200-500 and run VACUUM ANALYZE after bulk data loads if query precision drops.
CREATE INDEX idx_messages_emb ON conversation_messages USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_messages_tsv ON conversation_messages USING GIN(content_tsv);

-- ============================================================
-- context_window_build_types
-- Strategy templates defining how context windows get built.
-- The build type defines the assembly strategy; the actual
-- token limit is per-instance on context_windows.
-- ============================================================

CREATE TABLE context_window_build_types (
    id VARCHAR(100) PRIMARY KEY,            -- e.g. 'small-basic', 'standard-tiered'
    trigger_threshold_percent FLOAT DEFAULT 0.75,
    recent_window_percent FLOAT DEFAULT 0.20,
    summary_target_percent FLOAT DEFAULT 0.05,
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Seed data: base build types + custom assembly types
INSERT INTO context_window_build_types (id, description) VALUES
  ('small-basic',           'Three-tier assembly for small context windows (<=30k). Summarization via local LLM.'),
  ('standard-tiered',       'Three-tier assembly for all larger context windows (30k+). Summarization via Gemini Flash Lite API. Proportions scale with window size.'),
  ('grace-cag-full-docs',   'Custom assembly for Grace: Injects full architectural doc bundle + recent turns.'),
  ('gunner-rag-mem0-heavy', 'Custom assembly for Gunner: Prioritizes semantic RAG + Mem0 graph traversal + emotional anchors.');

-- ============================================================
-- context_windows
-- Per-participant instances. One LLM participant per conversation
-- creates a context window with a specific build type and token limit.
-- ============================================================

CREATE TABLE context_windows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id VARCHAR(255) NOT NULL REFERENCES conversations(id),
    build_type_id VARCHAR(100) NOT NULL REFERENCES context_window_build_types(id),
    max_token_limit INTEGER NOT NULL,       -- actual token budget for this window
    last_assembled_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_windows_conversation ON context_windows(conversation_id);

-- ============================================================
-- conversation_summaries
-- Keyed on context window + conversation. Different windows on
-- the same conversation produce different summaries because
-- different build types use different strategies.
-- conversation_id is denormalized (derivable from context_windows)
-- for query performance.
-- No ON DELETE CASCADE — intentional safety to prevent accidental
-- cascading data loss.
-- ============================================================

CREATE TABLE conversation_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id VARCHAR(255) NOT NULL REFERENCES conversations(id),
    context_window_id UUID NOT NULL REFERENCES context_windows(id),
    summary_text TEXT NOT NULL,
    summary_embedding vector(768),
    tier INTEGER NOT NULL,                  -- 1 = archival, 2 = chunk
    summarizes_from_seq INTEGER,            -- sequence_number range start
    summarizes_to_seq INTEGER,              -- sequence_number range end
    message_count INTEGER,
    original_token_count INTEGER,
    summary_token_count INTEGER,
    summarized_by_model VARCHAR(100),
    summarized_at TIMESTAMP DEFAULT NOW(),
    superseded_by UUID REFERENCES conversation_summaries(id),
    is_active BOOLEAN DEFAULT true
);

CREATE INDEX idx_summaries_window ON conversation_summaries(context_window_id, is_active);
-- ivfflat: same tuning guidance as idx_messages_emb above.
CREATE INDEX idx_summaries_emb ON conversation_summaries USING ivfflat (summary_embedding vector_cosine_ops) WITH (lists = 100);

-- Note: Mem0 will create its own tables in this same database instance
-- when first initialized (mem0_memories vector store tables, etc.)

-- ============================================================
-- Mem0 deduplication index
-- Prevents storing the same memory twice for the same user.
-- Mem0 hashes each memory and stores the hash in the JSONB payload.
-- The PGVector.insert method is monkey-patched to use ON CONFLICT
-- DO NOTHING against this index (see services/mem0_setup.py).
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS idx_mem0_hash_user
    ON mem0_memories ((payload->>'hash'), (payload->>'user_id'));

```
