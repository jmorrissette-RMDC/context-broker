# Gate 2 Round 4 Pass 3 — Functional Review

**Reviewer:** Claude Opus 4.6 (1M context)
**Date:** 2026-03-22
**Scope:** Comparison of original Rogers (State 2) vs new Context Broker (State 4) — feature parity and lost behavior analysis.
**Method:** Full read of all `.py` files under `app/`, `docker-compose.yml`, `postgres/init.sql`, `config/config.example.yml`, original Rogers flattened code, and REQ-context-broker.md.

---

## Findings

### F-01: `rogers_stats` tool removed

| Field | Value |
|---|---|
| **Original** | `rogers_stats` — internal tool returning DB counts and queue depths for Apgar health monitoring. Exposed via MCP tool list in `config.json` and handled in `server.py`. |
| **New** | MISSING as a dedicated tool. Queue depth data is available via Prometheus metrics (`metrics_get` tool and `/metrics` endpoint), but there is no single-call tool that returns structured DB row counts + queue depths in one response. |
| **Severity** | minor |
| **Notes** | Intentional architectural change. The Prometheus metrics endpoint covers queue depths via gauges (`EMBEDDING_QUEUE_DEPTH`, `ASSEMBLY_QUEUE_DEPTH`, `EXTRACTION_QUEUE_DEPTH`). DB row counts (conversation count, message count) are not surfaced by `metrics_get` but could be queried via the Imperator's `_db_query_tool` when `admin_tools: true`. Not a functional regression for external consumers since `rogers_stats` was internal-only in Rogers. |

### F-02: `external_session_id` field removed from messages

| Field | Value |
|---|---|
| **Original** | `conversation_messages` had an `external_session_id` column. `conv_store_message` accepted it. Search tools (`conv_search`, `conv_search_messages`) supported filtering by `external_session_id`. |
| **New** | MISSING. No `external_session_id` column in `init.sql`, no field in `StoreMessageInput`, no filter in search flows. |
| **Severity** | minor |
| **Notes** | REQ-context-broker.md does not mention `external_session_id`. The Context Broker uses `flow_id` and `user_id` on the conversations table plus `idempotency_key` on messages for external correlation. This is an intentional simplification for standalone deployment vs. ecosystem-specific Rogers. |

### F-03: `context_window_build_types` database table removed

| Field | Value |
|---|---|
| **Original** | Rogers had a `context_window_build_types` database table storing build type definitions (id, name, tier percentages, token limits). Build types were database entities. `conv_create_context_window` looked up the build type from this table by ID. |
| **New** | Build types are defined exclusively in `config.yml` under `build_types:`. No database table. `get_build_type_config()` reads from config. Context windows store the build type name as a `VARCHAR(100)`, not a foreign key. |
| **Severity** | minor |
| **Notes** | Intentional State 4 design per REQ section 5.3. Configuration lives in YAML, not in the database. Build types cannot be created via API but are hot-reloadable on config file change. |

### F-04: Memory confidence scoring and half-life decay system removed

| Field | Value |
|---|---|
| **Original** | Rogers `config.json` had a `memory` section with `confidence_archive_threshold: 0.1`, `confidence_boost_on_access: 0.15`, and per-category `half_life_days` (ephemeral: 3, infrastructure: 45, procedural: 90, project: 180, preference: 365, relationship: 730, historical: null). This implemented time-decaying memory confidence that would archive low-confidence memories and boost scores on access. |
| **New** | MISSING. No confidence scoring, no half-life decay, no memory categories. Mem0's built-in memory management is used as-is. The `recency_decay_days` and `recency_max_penalty` tuning params apply only to RRF message search scoring, not to knowledge graph memory confidence. |
| **Severity** | major |
| **Notes** | This was a significant Rogers feature for managing memory staleness over time. Without it, extracted memories accumulate indefinitely without quality degradation. REQ-context-broker.md does not specify this feature, suggesting it was intentionally scoped out for v1.0. However, for long-lived deployments, memory quality will degrade as stale facts are never archived or deprioritized. Should be tracked as a future enhancement. |

### F-05: Dual LLM configuration for memory extraction removed

| Field | Value |
|---|---|
| **Original** | Rogers had separate `llm` and `gemini_llm` config sections. Memory extraction used a dual-model strategy: `small_llm_config_key` for small extractions (Sutherland local, up to 90K chars) and `large_llm_config_key` for large ones (Gemini Flash-Lite, up to 450K chars). Context assembly also selected LLM by build type. |
| **New** | Single `llm` config section for all LLM operations. Memory extraction uses `extraction_max_chars: 90000` as the sole limit. No dual-model routing based on payload size or build type. |
| **Severity** | minor |
| **Notes** | Intentional simplification for State 4. REQ section 5.2 specifies three independent provider slots (LLM, embeddings, reranker) — not two LLM slots. Users configure one LLM. The trade-off is that deployers cannot automatically route large extractions to a cloud model while keeping small ones local. Workaround: change the LLM config as needed. |

### F-06: `detect-secrets` library replaced with regex patterns

| Field | Value |
|---|---|
| **Original** | Rogers `requirements.txt` included `detect-secrets==1.5.0` for scanning content before Mem0 ingestion. |
| **New** | Replaced with regex-based `_SECRET_PATTERNS` in `memory_extraction.py`. Covers API keys, bearer tokens, `sk-` keys, and cloud provider secrets. Code comments acknowledge this is heuristic. |
| **Severity** | minor |
| **Notes** | Reduces a dependency. The docstring in `_redact_secrets()` explicitly recommends `detect-secrets` for production use with sensitive data. Acceptable trade-off for standalone deployment. |

### F-07: Custom Mem0 LLM/Embedder adapters replaced with standard config

| Field | Value |
|---|---|
| **Original** | Rogers had `mem0_adapters.py` with `SutherlandLlmAdapter`, `SutherlandEmbedderAdapter`, `OpenAICompatibleLlmAdapter`. These routed Mem0 calls through the gateway peer proxy to Sutherland (ADR-053) or directly to OpenAI-compatible APIs. |
| **New** | `mem0_client.py` configures Mem0 using its native `MemoryConfig` with `provider="openai"` for both LLM and embedder, pointed at the configured `base_url` from `config.yml`. No custom adapters. |
| **Severity** | minor |
| **Notes** | Intentional State 4 change. Peer proxy routing was ecosystem-specific. The new approach uses Mem0's built-in OpenAI provider with configurable base URLs, which works with Ollama, OpenAI, or any OpenAI-compatible endpoint. Functionally equivalent for standalone deployment. |

### F-08: `rogers_chat` tool renamed to `broker_chat` with simplified input

| Field | Value |
|---|---|
| **Original** | `rogers_chat` required `user_id` and `messages` (array of message objects with role/content). |
| **New** | `broker_chat` takes `message` (single string) and optional `conversation_id`. |
| **Severity** | minor |
| **Notes** | Naming change is intentional. Input simplification: the original accepted a messages array for multi-message input. The new Imperator maintains its own conversation via PostgreSQL persistence (M-14/M-15), so conversation context is loaded from the database rather than passed in each call. The `conversation_id` field is a new capability that allows multi-client isolation. |

### F-09: Node.js MCP gateway replaced with nginx + native FastAPI MCP

| Field | Value |
|---|---|
| **Original** | Custom Node.js gateway using shared ecosystem libraries (`mcp-protocol-lib`, `routing-lib`, `health-aggregator-lib`, `logging-lib`). Handled MCP protocol, health aggregation, peer proxying, and 60s routing timeout. |
| **New** | Nginx as a thin reverse proxy (OTS image). MCP protocol handled directly in FastAPI (`routes/mcp.py`). No peer proxy. |
| **Severity** | minor |
| **Notes** | Intentional State 4 change. Removes ecosystem dependencies. The routing timeout was 60s in Rogers (config.json `routing.timeout`); the new system relies on nginx proxy timeouts. |

### F-10: Imperator persistence model significantly improved

| Field | Value |
|---|---|
| **Original** | Rogers' Imperator used `user_id` parameter and LangGraph state. No explicit conversation persistence to PostgreSQL. History lost on restart. |
| **New** | `ImperatorStateManager` creates and persists a single conversation in `/data/imperator_state.json`. Messages explicitly stored to `conversation_messages` (M-14). History loaded from PostgreSQL on each turn and injected into system prompt (M-15). Survives restarts. |
| **Severity** | minor |
| **Notes** | This is improved, not lost behavior. The original had no persistence guarantee. |

### F-11: `depends_on` container startup ordering removed

| Field | Value |
|---|---|
| **Original** | Rogers `docker-compose.yml` used `depends_on` with `condition: service_healthy` — langgraph container waited for all backing services to be healthy. |
| **New** | No `depends_on`. Containers start independently. Degraded-mode startup with retry loops (`_postgres_retry_loop`, `_redis_retry_loop` in `main.py`). |
| **Severity** | minor |
| **Notes** | Intentional per REQ section 7.2: "Containers start and bind ports without waiting for dependencies." Retry loops implement graceful degradation correctly. |

### F-12: `sender_id` type changed from integer to string

| Field | Value |
|---|---|
| **Original** | Rogers cast `sender_id` to `int(sender_id)` in `_invoke_message_pipeline`. |
| **New** | `sender_id` is `VARCHAR(255)`. Validated as string in `StoreMessageInput`. |
| **Severity** | minor |
| **Notes** | String is more flexible. Any integer sender_id from Rogers works as a string. REQ does not specify type. |

### F-13: `priority` field default changed from 3 to 0

| Field | Value |
|---|---|
| **Original** | `priority: int(args.get("priority", 3))` — default 3 (midpoint). |
| **New** | `priority` defaults to 0 in `StoreMessageInput` and `init.sql`. |
| **Severity** | minor |
| **Notes** | Different default. Priority is used only for extraction queue ordering (user messages get LPUSH regardless). External callers that relied on default of 3 would see different behavior, but the functional impact is negligible. |

### F-14: Quart replaced with FastAPI

| Field | Value |
|---|---|
| **Original** | Quart (async Flask-compatible). |
| **New** | FastAPI with Pydantic models, structured exception handlers, OpenAPI docs. |
| **Severity** | minor |
| **Notes** | Intentional framework change. Both ASGI. FastAPI provides stronger input validation via Pydantic models (`models.py`). No functional regression. |

### F-15: Internal memory tools (`mem_add`, `mem_list`, `mem_delete`) now exposed via MCP

| Field | Value |
|---|---|
| **Original** | Rogers had 4 internal tools (`mem_add`, `mem_list`, `mem_delete`, `mem_extract`) accessible only as HTTP endpoints, explicitly not exposed via MCP. |
| **New** | `mem_add`, `mem_list`, `mem_delete` are included in the MCP tool list (`_get_tool_list()` in `mcp.py`). `mem_extract` does not exist as a tool (extraction is automatic via background worker). |
| **Severity** | minor |
| **Notes** | The original explicitly stated these were internal-only. The new implementation exposes them via MCP, which broadens the API surface. This is a behavioral change but not a regression — it gives external consumers more capability. The REQ tool inventory (section 4.6) does not list these tools, so they are extras beyond the spec. |

### F-16: `mem_extract` standalone tool removed

| Field | Value |
|---|---|
| **Original** | Rogers had a `mem_extract` internal HTTP endpoint for triggering memory extraction on demand. |
| **New** | MISSING as a standalone tool. Memory extraction is triggered automatically by `enqueue_background_jobs` after message storage. No way to manually trigger extraction for a specific conversation via tool call. |
| **Severity** | minor |
| **Notes** | The automatic pipeline covers the primary use case. Manual re-extraction could be achieved via the Imperator's `_db_query_tool` to check `memory_extracted` status, but there is no direct tool to re-trigger extraction. Low impact since extraction runs automatically. |

---

## Summary

| Severity | Count |
|---|---|
| Blocker | 0 |
| Major | 1 |
| Minor | 15 |

### Major finding

**F-04 — Memory confidence scoring and half-life decay:** The per-category memory half-life system (ephemeral 3 days through historical permanent) and confidence boost-on-access logic from Rogers is not replicated. For long-lived deployments, extracted memories will accumulate without quality degradation or archival. REQ-context-broker.md does not require this feature, so it appears intentionally deferred, but it should be tracked for future implementation.

### Overall assessment

No blockers. The Context Broker faithfully implements all 12 MCP tools from the requirements spec and preserves the core Rogers pipeline: message storage, dedup/collapse, contextual embedding, three-tier context assembly, hybrid search with RRF and cross-encoder reranking, Mem0 knowledge extraction, and the Imperator conversational agent. All minor findings represent intentional architectural decisions for State 4 standalone deployment (ecosystem decoupling, config-driven build types, single LLM provider, framework modernization) or improved behavior (Imperator persistence, OpenAI-compatible chat endpoint, full MCP protocol support). The codebase is functionally ready for deployment.
