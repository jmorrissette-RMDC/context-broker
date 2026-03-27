# Test Coverage Audit — Context Broker

**Auditor:** Independent (Claude Opus 4.6)
**Date:** 2026-03-26
**Method:** Phase 1 — full source code read (app/, packages/, alerter/, log_shipper/). Phase 2 — full test suite read (tests/, tests/integration/). Cross-referenced.

**Legend:**
- **REAL** — tested against actual infrastructure (DB, LLM, HTTP, filesystem)
- **MOCK** — test exists but mocks critical dependencies (DB, LLM, HTTP, embedding)
- **NONE** — no test found

---

## 1. HTTP Endpoints & Transport

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 1.1 | `GET /health` — aggregated dependency check | app/routes/health.py | REAL | test_e2e_health.py, test_components.py (F1) | Returns postgres_ok, neo4j_ok, http_status |
| 1.2 | `GET /metrics` — Prometheus exposition | app/routes/metrics.py | REAL | test_e2e_health.py, test_components.py (G1) | Validates HELP/TYPE annotations |
| 1.3 | `GET /mcp` — SSE session establishment | app/routes/mcp.py:93 | REAL | test_e2e_mcp.py | Session creation, keepalive |
| 1.4 | `POST /mcp` — sessionless tool call | app/routes/mcp.py:156 | REAL | test_e2e_mcp.py | All tools exercised |
| 1.5 | `POST /mcp?sessionId=X` — session-mode tool call | app/routes/mcp.py:156 | NONE | — | Session queue push, unknown session 404, queue-full 503 — never tested |
| 1.6 | `POST /v1/chat/completions` — non-streaming | app/routes/chat.py:33 | REAL | test_e2e_chat.py | OpenAI-compatible response |
| 1.7 | `POST /v1/chat/completions` — streaming SSE | app/routes/chat.py:189 | REAL | test_e2e_chat.py | Chunk structure, [DONE] sentinel |
| 1.8 | Postgres-unavailable middleware (503) | app/main.py:213 | NONE | — | Middleware returns 503 for non-health routes when PG down — not tested |
| 1.9 | HTTP exception handler (structured JSON) | app/main.py:232 | REAL | test_e2e_mcp.py, test_e2e_chat.py | Exercised via validation errors |
| 1.10 | Validation exception handler (422) | app/main.py:251 | REAL | test_e2e_mcp.py, test_e2e_chat.py | Invalid request payloads |
| 1.11 | Known exception handler (500 catch-all) | app/main.py:275 | NONE | — | RuntimeError/ValueError/OSError/ConnectionError/PostgresError — not directly tested |
| 1.12 | MCP `initialize` handshake | app/routes/mcp.py:194 | REAL | test_e2e_mcp.py | Protocol version, capabilities |
| 1.13 | MCP `tools/list` | app/routes/mcp.py:211 | REAL | test_e2e_mcp.py | Lists all tools with schemas |
| 1.14 | MCP unknown method handling | app/routes/mcp.py:221 | REAL | test_e2e_mcp.py | Returns -32601 |
| 1.15 | MCP parse error (malformed JSON) | app/routes/mcp.py:168 | REAL | test_e2e_mcp.py | Returns -32700 |
| 1.16 | SSE session eviction (TTL / cap / queue pressure) | app/routes/mcp.py:59 | NONE | — | _evict_stale_sessions never tested |
| 1.17 | Chat endpoint — x-context-window-id header | app/routes/chat.py:99 | NONE | — | Multi-client isolation never tested |
| 1.18 | Chat endpoint — ToolMessage handling | app/routes/chat.py:119 | NONE | — | tool_call_id passthrough never tested |

---

## 2. MCP Tools — Core (D-02)

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 2.1 | `get_context` — auto-create conv + window, retrieve context | app/flows/tool_dispatch.py:117 | REAL | test_e2e_mcp.py, test_components.py (C3, E1, E3) | Budget snapping, build type dispatch |
| 2.2 | `store_message` — core tool | app/flows/tool_dispatch.py:152 | REAL | test_e2e_mcp.py, test_components.py (C1) | Triggers async pipeline |
| 2.3 | `search_messages` — hybrid search | app/flows/tool_dispatch.py:179 | REAL | test_e2e_mcp.py, test_components.py (C4, E2) | Vector + BM25 + rerank |
| 2.4 | `search_knowledge` — knowledge graph search | app/flows/tool_dispatch.py:209 | REAL | test_e2e_mcp.py | Mem0/Neo4j graph query |

---

## 3. MCP Tools — Management

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 3.1 | `conv_create_conversation` | tool_dispatch.py:235 | REAL | test_e2e_mcp.py | Caller-supplied ID (F-13) |
| 3.2 | `conv_delete_conversation` — cascade delete | tool_dispatch.py:254 | REAL | test_e2e_mcp.py | Atomic transaction |
| 3.3 | `conv_list_conversations` | tool_dispatch.py:281 | REAL | test_e2e_mcp.py | |
| 3.4 | `conv_list_conversations` — participant filter | tool_dispatch.py:288 | MOCK | test_participant_filter.py | Pydantic validation only; SQL query not tested |
| 3.5 | `conv_store_message` — management tool | tool_dispatch.py:336 | REAL | test_e2e_mcp.py, bulk_load.py | |
| 3.6 | `conv_retrieve_context` | tool_dispatch.py:373 | REAL | test_e2e_mcp.py | Build-type lookup + retrieval graph |
| 3.7 | `conv_create_context_window` | tool_dispatch.py:430 | REAL | test_e2e_mcp.py | |
| 3.8 | `conv_search` — conversation search | tool_dispatch.py:451 | REAL | test_e2e_mcp.py | Semantic + structured filters |
| 3.9 | `conv_search_messages` — message search (management) | tool_dispatch.py:477 | REAL | test_e2e_mcp.py | |
| 3.10 | `conv_get_history` | tool_dispatch.py:507 | REAL | test_e2e_mcp.py | |
| 3.11 | `conv_search_context_windows` | tool_dispatch.py:525 | REAL | test_e2e_mcp.py | |
| 3.12 | `query_logs` — SQL log filtering | tool_dispatch.py:550 | REAL | test_e2e_mcp.py | |
| 3.13 | `search_logs` — semantic log search | tool_dispatch.py:611 | REAL | test_e2e_mcp.py | Requires log_embeddings config |
| 3.14 | `mem_search` | tool_dispatch.py:682 | REAL | test_e2e_mcp.py | |
| 3.15 | `mem_get_context` | tool_dispatch.py:704 | REAL | test_e2e_mcp.py | |
| 3.16 | `mem_add` | tool_dispatch.py:756 | REAL | test_e2e_mcp.py | |
| 3.17 | `mem_list` | tool_dispatch.py:773 | REAL | test_e2e_mcp.py | |
| 3.18 | `mem_delete` | tool_dispatch.py:790 | REAL | test_e2e_mcp.py | |
| 3.19 | `imperator_chat` | tool_dispatch.py:725 | REAL | test_e2e_mcp.py, test_components.py (D2) | |
| 3.20 | `metrics_get` | tool_dispatch.py:806 | REAL | test_e2e_mcp.py | |
| 3.21 | `install_stategraph` — runtime pip install | tool_dispatch.py:819, install_stategraph.py | REAL | test_components.py (A3) | Partial — response checked, not full install flow |
| 3.22 | Unknown tool — ValueError | tool_dispatch.py:834 | REAL | test_e2e_mcp.py | |

---

## 4. Configuration System

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 4.1 | `load_config()` — mtime cache, content hash | app/config.py:89 | MOCK | test_config.py | Writes temp files, tests cache behavior |
| 4.2 | `load_config()` — missing file error | app/config.py:102 | MOCK | test_config.py | |
| 4.3 | `load_config()` — invalid YAML error | app/config.py:59 | MOCK | test_config.py | |
| 4.4 | `load_te_config()` — TE hot-reload | app/config.py:219 | REAL | test_components.py (B5) | SSH+sed modifies te.yml, verifies effect |
| 4.5 | `load_merged_config()` — AE+TE merge | app/config.py:115 | NONE | — | Not tested |
| 4.6 | `async_load_config()` — async wrapper | app/config.py:138 | NONE | — | Not tested (used extensively in production) |
| 4.7 | `get_build_type_config()` — validation | app/config.py:334 | MOCK | test_config.py | Tier pct validation |
| 4.8 | `get_api_key()` — credentials file + env fallback | app/config.py:309 | MOCK | test_config.py | Tests env var; credentials file path not tested |
| 4.9 | `get_tuning()` — tuning/workers/locks fallback | app/config.py:369 | MOCK | test_config.py | |
| 4.10 | `verbose_log()` / `verbose_log_auto()` | app/config.py:394 | NONE | — | Not tested |
| 4.11 | `get_chat_model()` — cached ChatOpenAI | app/config.py:438 | NONE | — | Cache eviction, key hashing never tested |
| 4.12 | `get_embeddings_model()` — cached OpenAIEmbeddings | app/config.py:478 | NONE | — | Cache eviction, MRL dimensions never tested |
| 4.13 | LLM/embeddings cache clear on config change | app/config.py:76 | MOCK | test_config.py | Tests cache invalidation |
| 4.14 | Credentials hot-reload (mtime) | app/config.py:272 | NONE | — | File re-read on mtime change not tested |

---

## 5. Database & Migrations

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 5.1 | `init_postgres()` — pool creation | app/database.py:22 | REAL | Implicit in all e2e tests | |
| 5.2 | `get_pg_pool()` — raises if uninitialized | app/database.py:43 | NONE | — | Not tested |
| 5.3 | `close_all_connections()` | app/database.py:52 | NONE | — | Not tested |
| 5.4 | `check_postgres_health()` | app/database.py:62 | REAL | test_e2e_health.py | Implicit via /health |
| 5.5 | `check_neo4j_health()` — HTTP probe | app/database.py:74 | REAL | test_e2e_health.py | Implicit via /health |
| 5.6 | `run_migrations()` — 20 migrations | app/migrations.py:556 | REAL | Implicit at startup | Advisory lock serialization |
| 5.7 | Migration 009 — HNSW vector index | app/migrations.py:118 | NONE | — | Deferred index creation not directly tested |
| 5.8 | Migration 012 — schema alignment (renames, drops) | app/migrations.py:160 | NONE | — | Complex multi-step migration not unit tested |
| 5.9 | Migration idempotency (IF NOT EXISTS guards) | app/migrations.py | NONE | — | No test verifies re-running migrations is safe |

---

## 6. Background Workers

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 6.1 | Embedding worker — batch embed + store vectors | app/workers/db_worker.py:62 | REAL | test_components.py (B4, C2, E2), bulk_load.py | Verified by polling DB for non-null embeddings |
| 6.2 | Embedding worker — timeout handling | db_worker.py:116 | NONE | — | asyncio.TimeoutError path not tested |
| 6.3 | Embedding worker — poison pill (zero-vector after 5 failures) | db_worker.py:130 | NONE | — | Failure recovery not tested |
| 6.4 | Embedding worker — backoff after 3 failures | db_worker.py:180 | NONE | — | |
| 6.5 | Extraction worker — Mem0 extraction per conversation | db_worker.py:191 | REAL | bulk_load.py | Pipeline completion verified |
| 6.6 | Extraction worker — advisory lock per conversation | db_worker.py:250 | NONE | — | Lock contention not tested |
| 6.7 | Extraction worker — per-conversation retry cap (3) | db_worker.py:231 | NONE | — | Mark-as-extracted after max failures not tested |
| 6.8 | Extraction worker — timeout handling | db_worker.py:305 | NONE | — | |
| 6.9 | Assembly worker — poll for stale windows | db_worker.py:414 | REAL | bulk_load.py, test_components.py (C3) | Assembly completion verified |
| 6.10 | Assembly worker — trigger threshold | db_worker.py:336 | NONE | — | trigger_threshold_percent logic not tested |
| 6.11 | Log embedding worker — batch embed system logs | db_worker.py:498 | NONE | — | Not tested at all |
| 6.12 | Log embedding worker — disabled when no config | db_worker.py:513 | NONE | — | |
| 6.13 | Log embedding worker — poison pill (delete after 5 failures) | db_worker.py:557 | NONE | — | |
| 6.14 | `start_background_worker()` — asyncio.gather all 5 loops | db_worker.py:596 | REAL | Implicit at startup | |

---

## 7. Scheduler

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 7.1 | `_cron_is_due()` — cron expression parsing | app/workers/scheduler.py:25 | MOCK | test_scheduler.py | Pure logic, no infra |
| 7.2 | Interval schedule — elapsed check | scheduler.py:141 | MOCK | test_scheduler.py | Minimum interval (30s) validated |
| 7.3 | `_fire_schedule()` — dispatch to imperator | scheduler.py:53 | NONE | — | Not tested |
| 7.4 | `scheduler_worker()` — poll loop + atomic claim | scheduler.py:106 | NONE | — | Optimistic locking (UPDATE WHERE) not tested |
| 7.5 | Schedule history recording | scheduler.py:58 | NONE | — | DB inserts for running/completed/error not tested |
| 7.6 | Double-fire prevention (same cron minute) | scheduler.py:137 | NONE | — | |

---

## 8. Application Lifecycle

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 8.1 | Startup — StateGraph package scan | app/main.py:97 | REAL | test_components.py (A1, A2) | |
| 8.2 | Startup — build type config validation | app/main.py:113 | NONE | — | Fail-fast on bad tier percentages not tested |
| 8.3 | Startup — embedding_dims required | app/main.py:121 | NONE | — | RuntimeError on missing dims not tested |
| 8.4 | Startup — Postgres retry loop | app/main.py:31 | NONE | — | Degraded mode + retry not tested |
| 8.5 | Startup — Imperator state initialization | app/main.py:150 | NONE | — | Only implicit via e2e |
| 8.6 | Startup — domain knowledge seeding | app/main.py:172 | NONE | — | seed_domain_knowledge() not tested |
| 8.7 | Shutdown — cancel workers, close connections | app/main.py:185 | NONE | — | Graceful shutdown not tested |

---

## 9. StateGraph Registry

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 9.1 | `scan()` — entry_points discovery (AE + TE) | app/stategraph_registry.py:31 | REAL | test_components.py (A1, A2), test_static_checks.py | |
| 9.2 | `scan()` — module eviction (Kaiser pattern) | stategraph_registry.py:135 | NONE | — | Hot-reload module eviction not tested |
| 9.3 | `get_flow_builder()` / `get_imperator_builder()` | stategraph_registry.py:113 | REAL | Implicit in all tool calls | |
| 9.4 | `install_stategraph()` — pip install + rescan | app/flows/install_stategraph.py | REAL | test_components.py (A3) | Partial — tool responds, not full install |
| 9.5 | `_record_package_install()` — DB record | install_stategraph.py:100 | NONE | — | |

---

## 10. Build Type Registry

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 10.1 | Register + retrieve assembly/retrieval graphs | app/flows/build_type_registry.py | MOCK | test_build_type_registry.py | Pure registry logic |
| 10.2 | Lazy compilation caching | build_type_registry.py | MOCK | test_build_type_registry.py | Builder called once |
| 10.3 | Three shipped types (passthrough, standard-tiered, knowledge-enriched) | build_type_registry.py | MOCK + REAL | test_build_type_registry.py, test_components.py (A4) | Registry + deployed |
| 10.4 | `clear_compiled_cache()` | build_type_registry.py | NONE | — | |

---

## 11. AE Package — Message Pipeline

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 11.1 | `store_message()` — resolve conv from window ID | message_pipeline.py | REAL | test_e2e_mcp.py | |
| 11.2 | Consecutive duplicate collapse (repeat_count) | message_pipeline.py | REAL | test_e2e_pipeline.py | |
| 11.3 | Atomic sequence number assignment | message_pipeline.py | REAL | Implicit in bulk_load | |
| 11.4 | Enqueue background jobs (embed, extract, assembly) | message_pipeline.py | REAL | bulk_load.py | Verified by pipeline completion |

---

## 12. AE Package — Embed Pipeline

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 12.1 | `fetch_message()` — load message from DB | embed_pipeline.py | REAL | Implicit via worker | |
| 12.2 | `generate_embedding()` — call embedding model | embed_pipeline.py | REAL | test_components.py (B4, C2) | |
| 12.3 | `store_embedding()` — write vector to DB | embed_pipeline.py | REAL | test_components.py (C2) | |
| 12.4 | `enqueue_context_assembly()` — trigger assembly | embed_pipeline.py | REAL | Implicit via worker | |

---

## 13. AE Package — Memory Extraction

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 13.1 | `acquire_extraction_lock()` — advisory lock | memory_extraction.py | MOCK | test_memory_extraction.py | |
| 13.2 | `fetch_unextracted_messages()` | memory_extraction.py | MOCK | test_memory_extraction.py | |
| 13.3 | `build_extraction_text()` — chronological + max_chars | memory_extraction.py | MOCK | test_memory_extraction.py | |
| 13.4 | `run_mem0_extraction()` — call Mem0 | memory_extraction.py | MOCK | test_memory_extraction.py | Mem0 mocked |
| 13.5 | `mark_messages_extracted()` | memory_extraction.py | MOCK | test_memory_extraction.py | |
| 13.6 | `release_extraction_lock()` | memory_extraction.py | MOCK | test_memory_extraction.py | |
| 13.7 | Secret redaction (API keys, tokens, passwords) | memory_extraction.py | MOCK | test_memory_extraction.py | |
| 13.8 | Routing logic (after_lock, after_fetch, etc.) | memory_extraction.py | MOCK | test_memory_extraction.py | |
| 13.9 | Full extraction flow end-to-end | memory_extraction.py | REAL | bulk_load.py | Pipeline completion verified |

---

## 14. AE Package — Search Flows

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 14.1 | Conversation search — embed query + DB search | search_flow.py | REAL | test_e2e_mcp.py | |
| 14.2 | Message search — hybrid (vector + BM25) | search_flow.py | REAL | test_e2e_mcp.py, test_components.py (C4) | |
| 14.3 | Reciprocal Rank Fusion (RRF) scoring | search_flow.py | NONE | — | RRF math not unit tested |
| 14.4 | Reranking via external API (Infinity/Together/Cohere/Jina/Voyage) | search_flow.py | NONE | — | Reranker dispatch not tested |
| 14.5 | Recency bias (F-10) in RRF | search_flow.py | NONE | — | Age-based score boost not tested |
| 14.6 | Structured filters (sender, role, date_from, date_to) | search_flow.py | REAL | test_e2e_mcp.py | |

---

## 15. AE Package — Context Assembly (Build Types)

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 15.1 | Passthrough assembly — no-op + timestamp | build_types/passthrough.py | REAL | test_components.py (E1) | Verbatim messages returned |
| 15.2 | Passthrough retrieval — load recent messages | passthrough.py | REAL | test_components.py (E1) | |
| 15.3 | Standard-tiered assembly — 3-tier compression | build_types/standard_tiered.py | REAL | evaluate_quality.py | LLM summarization |
| 15.4 | Standard-tiered — tier boundary calculation | standard_tiered.py | NONE | — | Not unit tested |
| 15.5 | Standard-tiered — chunk summarization | standard_tiered.py | REAL | evaluate_quality.py | Sonnet-evaluated quality |
| 15.6 | Standard-tiered — archival consolidation | standard_tiered.py | REAL | evaluate_quality.py | |
| 15.7 | Standard-tiered — D-09 initial lookback | standard_tiered.py | NONE | — | First assembly deeper lookback not tested |
| 15.8 | Standard-tiered — M-09 partial failure tracking | standard_tiered.py | NONE | — | last_assembled_at skip on error not tested |
| 15.9 | Standard-tiered — R7-M11 budget guard | standard_tiered.py | NONE | — | Oversized summary prevention not tested |
| 15.10 | Knowledge-enriched retrieval — semantic search injection | build_types/knowledge_enriched.py | REAL | evaluate_quality.py | |
| 15.11 | Knowledge-enriched retrieval — KG traversal injection | knowledge_enriched.py | REAL | evaluate_quality.py | |
| 15.12 | Tier scaling (F-05) — dynamic tier pct adjustment | build_types/tier_scaling.py | MOCK | test_tier_scaling.py | 23 tests, pure math |
| 15.13 | Effective utilization (85% cap) | budget.py | REAL | test_components.py (E3) | Validates total_tokens <= 85% |

---

## 16. AE Package — Memory (Mem0/Neo4j)

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 16.1 | `get_mem0_client()` — lazy singleton | memory/mem0_client.py | MOCK | test_mem0_client.py | Mem0 constructor mocked |
| 16.2 | `_apply_mem0_patches()` — ON CONFLICT DO NOTHING | mem0_client.py | MOCK | test_mem0_client.py | |
| 16.3 | Config hash change — recreate client | mem0_client.py | MOCK | test_mem0_client.py | |
| 16.4 | `reset_mem0_client()` | mem0_client.py | MOCK | test_mem0_client.py | |
| 16.5 | Neo4j config resolution (env vars, password handling) | mem0_client.py | MOCK | test_mem0_client.py | |
| 16.6 | Memory search flow — `search_memory_graph()` | memory_search_flow.py | REAL | test_e2e_mcp.py | |
| 16.7 | Memory context flow — `retrieve_memory_context()` | memory_search_flow.py | REAL | test_e2e_mcp.py | |
| 16.8 | Memory admin — `add_memory()` | memory_admin_flow.py | REAL | test_e2e_mcp.py | |
| 16.9 | Memory admin — `list_memories()` | memory_admin_flow.py | REAL | test_e2e_mcp.py | |
| 16.10 | Memory admin — `delete_memory()` | memory_admin_flow.py | REAL | test_e2e_mcp.py | |
| 16.11 | Memory scoring — half-life decay | memory_scoring.py | MOCK | test_memory_scoring.py | 25 tests, pure math |
| 16.12 | Memory scoring — recency boost | memory_scoring.py | MOCK | test_memory_scoring.py | |
| 16.13 | M-22 decay applied at retrieval time | memory_search_flow.py | NONE | — | Integration of scoring into search flow not verified |
| 16.14 | Degraded mode when Neo4j/Mem0 unavailable | memory_search_flow.py | NONE | — | Graceful degradation not tested |

---

## 17. AE Package — Conversation Operations

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 17.1 | Create conversation flow (F-13 idempotent) | conversation_ops_flow.py | REAL | test_e2e_mcp.py | |
| 17.2 | Create context window flow | conversation_ops_flow.py | REAL | test_e2e_mcp.py | |
| 17.3 | Get history flow | conversation_ops_flow.py | REAL | test_e2e_mcp.py | |
| 17.4 | Search context windows flow | conversation_ops_flow.py | REAL | test_e2e_mcp.py | |
| 17.5 | `get_context` flow — auto-create + budget snap + retrieve | conversation_ops_flow.py | REAL | test_e2e_mcp.py, test_components.py | |

---

## 18. AE Package — Health & Metrics Flows

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 18.1 | Health check flow — Postgres + Neo4j probes | health_flow.py | REAL | test_e2e_health.py | |
| 18.2 | Metrics flow — Prometheus collection | metrics_flow.py | REAL | test_e2e_health.py | |

---

## 19. TE Package — Imperator Flow

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 19.1 | ReAct agent loop (ARCH-05 graph edges) | imperator_flow.py | REAL | test_e2e_chat.py, test_components.py (D1) | |
| 19.2 | System prompt loading from file | imperator_flow.py | NONE | — | Not tested |
| 19.3 | Conversation history loading from DB | imperator_flow.py | REAL | run_imperator_conversation.py | Multi-turn continuity |
| 19.4 | Tool binding at compile time (R7-m14) | imperator_flow.py | REAL | test_tool_organization.py | Tool count verification |
| 19.5 | Max iterations fallback | imperator_flow.py | NONE | — | Max iteration cap not tested |
| 19.6 | Empty response retry | imperator_flow.py | NONE | — | |
| 19.7 | Message truncation for context limits | imperator_flow.py | NONE | — | |
| 19.8 | store_user_message / store_assistant_message persistence | imperator_flow.py | REAL | Implicit via chat tests | |

---

## 20. TE Package — Imperator Tools

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 20.1 | `log_query` — SQL log filtering | tools/diagnostic.py | REAL | test_components.py (D4) | |
| 20.2 | `context_introspection` — tier breakdown | tools/diagnostic.py | REAL | test_components.py (D5) | |
| 20.3 | `pipeline_status` — pending jobs | tools/diagnostic.py | REAL | test_components.py (D3) | |
| 20.4 | `list_schedules` | tools/scheduling.py | MOCK | test_scheduler.py | Pydantic + cron parsing only |
| 20.5 | `create_schedule` | tools/scheduling.py | NONE | — | DB insert not tested |
| 20.6 | `enable_schedule` / `disable_schedule` | tools/scheduling.py | NONE | — | |
| 20.7 | `store_domain_info` | tools/operational.py | NONE | — | |
| 20.8 | `search_domain_info` | tools/operational.py | NONE | — | |
| 20.9 | `extract_domain_knowledge` | tools/operational.py | NONE | — | |
| 20.10 | `search_domain_knowledge` | tools/operational.py | NONE | — | |
| 20.11 | Operational tool conditional gating | tools/operational.py | MOCK | test_tool_organization.py, test_domain_mem0.py | Checks enabled/disabled config |
| 20.12 | `run_command` — allowlisted shell commands | tools/system.py | MOCK | test_new_tools.py | Allowlist enforced, subprocess mocked |
| 20.13 | `calculate` — safe math eval | tools/system.py | MOCK | test_new_tools.py | |
| 20.14 | `file_read` — sandbox enforcement | tools/filesystem.py | MOCK | test_new_tools.py | Path validation mocked |
| 20.15 | `file_list` / `file_search` | tools/filesystem.py | NONE | — | |
| 20.16 | `file_write` — /data/downloads/ only | tools/filesystem.py | MOCK | test_new_tools.py | |
| 20.17 | `read_system_prompt` / `update_system_prompt` | tools/filesystem.py | NONE | — | |
| 20.18 | `web_search` — DuckDuckGo | tools/web.py | MOCK | test_new_tools.py | DDGS mocked |
| 20.19 | `web_read` — crawl4ai + fallback | tools/web.py | MOCK | test_new_tools.py | HTTP mocked |
| 20.20 | `send_notification` — webhook dispatch | tools/notify.py | MOCK | test_new_tools.py | HTTP mocked |
| 20.21 | `add_alert_instruction` | tools/alerting.py | MOCK | test_new_tools.py | DB mocked |
| 20.22 | `list_alert_instructions` | tools/alerting.py | MOCK | test_new_tools.py | DB mocked |
| 20.23 | `update_alert_instruction` | tools/alerting.py | NONE | — | |
| 20.24 | `delete_alert_instruction` | tools/alerting.py | MOCK | test_new_tools.py | DB mocked |
| 20.25 | `config_read` — redacted config | tools/admin.py | NONE | — | |
| 20.26 | `db_query` — read-only SQL | tools/admin.py | NONE | — | |
| 20.27 | `config_write` — hot-reload config | tools/admin.py | NONE | — | |
| 20.28 | `verbose_toggle` | tools/admin.py | NONE | — | |
| 20.29 | `change_inference` — model switching | tools/admin.py | MOCK | test_change_inference.py | Config write + endpoint test mocked |
| 20.30 | `migrate_embeddings` — destructive migration | tools/admin.py | MOCK | test_migration_tool.py | Dry-run only |
| 20.31 | Admin tool gating (admin_tools: true) | tools/admin.py | MOCK | test_tool_organization.py | |

---

## 21. TE Package — Domain Knowledge

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 21.1 | `seed_domain_knowledge()` — 12 seed articles | seed_knowledge.py | NONE | — | Not tested |
| 21.2 | Domain Mem0 client — separate collection | domain_mem0.py | MOCK | test_domain_mem0.py | Mem0 constructor mocked |

---

## 22. Imperator State Manager

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 22.1 | `initialize()` — read state file + verify conversation | state_manager.py:35 | NONE | — | |
| 22.2 | State file read/write (/data/imperator_state.json) | state_manager.py:90 | NONE | — | |
| 22.3 | Conversation auto-recreation on deletion (PG-43) | state_manager.py:59 | NONE | — | Runtime recovery not tested |
| 22.4 | `get_context_window_id()` backward compat | state_manager.py:79 | NONE | — | |

---

## 23. Caller Identity

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 23.1 | User field priority | caller_identity.py | MOCK | test_caller_identity.py | Request object mocked |
| 23.2 | Reverse DNS lookup | caller_identity.py | MOCK | test_caller_identity.py | socket.gethostbyaddr mocked |
| 23.3 | DNS cache | caller_identity.py | MOCK | test_caller_identity.py | |
| 23.4 | Fallback to "unknown" | caller_identity.py | MOCK | test_caller_identity.py | |

---

## 24. Token Budget

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 24.1 | `snap_budget()` — bucket snapping | app/budget.py | MOCK | test_budget.py | 6 tests, pure math |
| 24.2 | `resolve_token_budget()` — caller override, auto query | app/token_budget.py | MOCK | test_token_budget.py | Provider query mocked |
| 24.3 | Provider context length query (httpx) | app/token_budget.py | MOCK | test_token_budget.py | HTTP mocked |

---

## 25. Pydantic Models

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 25.1 | All input models (39 test cases) | app/models.py | MOCK | test_models.py | Pure Pydantic validation |
| 25.2 | StoreMessageInput — at-least-one-ID validator | models.py:37 | MOCK | test_models.py | |
| 25.3 | QueryLogsInput / SearchLogsInput validation | models.py:209 | MOCK | test_log_endpoints.py | |

---

## 26. Logging

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 26.1 | JsonFormatter — structured JSON output | app/logging_setup.py | REAL | test_static_checks.py, test_integration_container.py (T-9.1) | Docker logs validated |
| 26.2 | HealthCheckFilter — suppress /health noise | logging_setup.py:37 | NONE | — | |
| 26.3 | `update_log_level()` — runtime level change | logging_setup.py:67 | NONE | — | |

---

## 27. Alerter Service

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 27.1 | `POST /webhook` — receive CloudEvents | alerter/alerter.py:244 | NONE | — | No test file for alerter |
| 27.2 | Instruction semantic search (vector) | alerter.py:190 | NONE | — | |
| 27.3 | Instruction text fallback search | alerter.py:221 | NONE | — | |
| 27.4 | LLM-based message formatting | alerter.py:368 | NONE | — | |
| 27.5 | Slack channel delivery | alerter.py:438 | NONE | — | |
| 27.6 | Discord channel delivery | alerter.py:447 | NONE | — | |
| 27.7 | ntfy channel delivery | alerter.py:458 | NONE | — | |
| 27.8 | SMTP/email delivery | alerter.py:476 | NONE | — | |
| 27.9 | Twilio SMS delivery | alerter.py:512 | NONE | — | |
| 27.10 | Generic webhook delivery | alerter.py:537 | NONE | — | |
| 27.11 | Log context enrichment | alerter.py:327 | NONE | — | |
| 27.12 | Event + delivery recording (audit trail) | alerter.py:558 | NONE | — | |
| 27.13 | `GET /health` — alerter health check | alerter.py:137 | NONE | — | |
| 27.14 | `_ensure_tables()` — schema creation | alerter.py:89 | NONE | — | |
| 27.15 | `_embed_text()` — embedding helper | alerter.py:157 | NONE | — | |

---

## 28. Log Shipper

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 28.1 | Docker network discovery (self-inspection) | log_shipper/shipper.py:38 | NONE | — | No test file |
| 28.2 | Container log tailing (follow mode) | shipper.py:112 | NONE | — | |
| 28.3 | Structured JSON log parsing | shipper.py:154 | NONE | — | |
| 28.4 | High-water mark (resume from last timestamp) | shipper.py:91 | NONE | — | |
| 28.5 | Batch Postgres writes | shipper.py:215 | REAL | test_components.py (G2) | Verified via system_logs count |
| 28.6 | Docker event watcher (join/leave network) | shipper.py:310 | NONE | — | |
| 28.7 | Graceful shutdown (SIGTERM) | shipper.py:401 | NONE | — | |

---

## 29. Infrastructure & Deployment

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 29.1 | Non-root container user | Dockerfile | REAL | test_integration_container.py | SSH + docker exec |
| 29.2 | File ownership (/app owned by context-broker) | Dockerfile | REAL | test_integration_container.py | |
| 29.3 | Data volume structure (/data subdirs) | docker-compose.yml | REAL | test_integration_container.py | |
| 29.4 | No anonymous volumes | docker-compose.yml | REAL | test_integration_container.py | |
| 29.5 | Container health check | Dockerfile | MOCK | test_static_checks.py | File content check |
| 29.6 | Two-network topology | docker-compose.yml | MOCK | test_static_checks.py | File content check |
| 29.7 | OTS backing services (no custom builds) | docker-compose.yml | MOCK | test_static_checks.py | |
| 29.8 | No hardcoded secrets in source | app/**/*.py | MOCK | test_static_checks.py | Regex scan |
| 29.9 | Version pinning (requirements.txt) | requirements.txt | MOCK | test_static_checks.py | |
| 29.10 | StateGraph mandate (no business logic in routes) | app/routes/*.py | MOCK | test_static_checks.py | AST/regex check |

---

## 30. Cross-Provider Validation

| # | Capability | Source | Coverage | Test File(s) | Notes |
|---|-----------|--------|----------|--------------|-------|
| 30.1 | Google Gemini (embedding + LLM) | config + pipeline | REAL | test_cross_provider.py | Full pipeline |
| 30.2 | OpenAI (embedding + LLM) | config + pipeline | REAL | test_cross_provider.py | Full pipeline |
| 30.3 | Ollama local (embedding + LLM) | config + pipeline | REAL | test_cross_provider.py | Full pipeline |
| 30.4 | Config hot-reload between providers | config.py + SSH | REAL | test_cross_provider.py | |

---

## Summary Statistics

| Category | REAL | MOCK | NONE | Total |
|----------|------|------|------|-------|
| HTTP Endpoints & Transport | 10 | 0 | 8 | 18 |
| MCP Tools — Core | 4 | 0 | 0 | 4 |
| MCP Tools — Management | 17 | 1 | 0 | 18 |
| Configuration System | 1 | 5 | 8 | 14 |
| Database & Migrations | 3 | 0 | 6 | 9 |
| Background Workers | 3 | 0 | 11 | 14 |
| Scheduler | 0 | 2 | 4 | 6 |
| Application Lifecycle | 1 | 0 | 6 | 7 |
| StateGraph Registry | 2 | 0 | 3 | 5 |
| Build Type Registry | 1 | 2 | 1 | 4 |
| Message Pipeline | 4 | 0 | 0 | 4 |
| Embed Pipeline | 4 | 0 | 0 | 4 |
| Memory Extraction | 1 | 8 | 0 | 9 |
| Search Flows | 2 | 0 | 4 | 6 |
| Context Assembly | 6 | 1 | 6 | 13 |
| Memory (Mem0/Neo4j) | 5 | 7 | 2 | 14 |
| Conversation Operations | 5 | 0 | 0 | 5 |
| Health & Metrics Flows | 2 | 0 | 0 | 2 |
| Imperator Flow | 3 | 1 | 5 | 9 |
| Imperator Tools | 3 | 10 | 18 | 31 |
| Domain Knowledge | 0 | 1 | 1 | 2 |
| Imperator State Manager | 0 | 0 | 4 | 4 |
| Caller Identity | 0 | 4 | 0 | 4 |
| Token Budget | 0 | 3 | 0 | 3 |
| Pydantic Models | 0 | 3 | 0 | 3 |
| Logging | 1 | 0 | 2 | 3 |
| Alerter Service | 0 | 0 | 15 | 15 |
| Log Shipper | 1 | 0 | 6 | 7 |
| Infrastructure | 4 | 6 | 0 | 10 |
| Cross-Provider | 4 | 0 | 0 | 4 |
| **TOTAL** | **86** | **54** | **115** | **255** |

**Coverage rate:** 33.7% REAL, 21.2% MOCK, 45.1% NONE

---

## Critical Gaps

### Completely Untested Subsystems
1. **Alerter service** (15 capabilities) — zero tests of any kind. Webhook receipt, instruction matching, LLM formatting, 6 channel delivery types, audit trail.
2. **Imperator state manager** (4 capabilities) — state file I/O, conversation recovery on deletion (PG-43), backward-compat wrapper.
3. **Log shipper** (6/7 capabilities) — only verified indirectly via system_logs row count. Docker network discovery, container tailing, log parsing, event watcher, graceful shutdown all untested.

### High-Risk Untested Paths
1. **Postgres-unavailable degraded mode** — middleware 503 + retry loop (app/main.py:31, 213). The system's primary resilience feature has zero test coverage.
2. **Background worker failure/recovery** — embedding timeout, poison pill zero-vector, extraction retry cap, log embedding failures. 11 of 14 worker capabilities untested.
3. **Scheduler execution** — `_fire_schedule()`, history recording, double-fire prevention, optimistic locking. Only cron parsing is tested; the entire fire-and-record path is not.
4. **SSE session management** — eviction by TTL/cap/queue pressure, session-mode dispatch. 3 gaps.
5. **Search internals** — RRF scoring math, reranker dispatch, recency bias. 4 gaps.
6. **Assembly guard rails** — budget guard (R7-M11), partial failure tracking (M-09), initial lookback (D-09). 3 gaps.
7. **Admin tools** — config_read, db_query, config_write, verbose_toggle. 4 of 6 admin tools untested.
8. **Domain knowledge tools** — all 4 operational tools (store, search info, extract knowledge, search knowledge) untested.
9. **Application startup validation** — build type config fail-fast, embedding_dims required, domain knowledge seeding. 5 of 7 lifecycle items untested.

### MOCK-Only (No Real Infrastructure Test)
1. **Memory extraction flow** — every node individually mocked; only bulk_load.py confirms the composed flow works against real Mem0/Neo4j.
2. **Caller identity** — DNS lookup always mocked; never tested in Docker networking where it actually matters.
3. **All TE tool implementations** (web, filesystem, system, notify, alerting) — HTTP/subprocess/DB all mocked. Would catch interface changes but not runtime behavior.
4. **change_inference** — config file write, endpoint reachability test, model catalog load all mocked.
5. **migrate_embeddings** — dry-run only; actual destructive migration (wipe vectors, clear Neo4j) never tested.
