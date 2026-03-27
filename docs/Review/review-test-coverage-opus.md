# Test Coverage Audit — Context Broker

**Auditor:** Claude Opus 4.6 (independent test coverage auditor)
**Date:** 2026-03-26
**Method:** Phase 1 — read all source code and build capability list from code alone. Phase 2 — read all test files and cross-reference.

## Legend

| Status | Meaning |
|--------|---------|
| **REAL** | Tested against actual infrastructure (database, LLM, HTTP, filesystem) |
| **MOCK** | Test exists but mocks/patches critical dependencies |
| **NONE** | No test found for this capability |

---

## 1. Application Lifecycle & Configuration (`app/main.py`, `app/config.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 1.1 | FastAPI app startup with lifespan | Postgres, filesystem | REAL | test_e2e_health, test_components | Health endpoint proves startup succeeded |
| 1.2 | StateGraph package discovery via entry_points | importlib | MOCK | test_build_type_registry, test_static_checks | Registry tests use mock builders; static checks verify entry_points exist |
| 1.3 | Build type config validation at startup | config file | MOCK | test_config::TestGetBuildTypeConfig | Validates tier percentages sum <= 1.0 |
| 1.4 | embedding_dims required validation | config file | MOCK | test_mem0_client::TestGetEmbeddingDims | Tests raise on missing dims |
| 1.5 | PostgreSQL connection with retry loop | Postgres | REAL | test_e2e_health, test_e2e_resilience | Health confirms Postgres connected |
| 1.6 | Postgres retry loop with Imperator re-init | Postgres | NONE | — | No test exercises the startup retry loop with a failing-then-recovering Postgres |
| 1.7 | Background worker startup | Postgres | REAL | test_e2e_pipeline, test_components | Pipeline tests prove workers are running (embeddings appear) |
| 1.8 | Imperator state manager initialization | Postgres, filesystem | NONE | — | No unit or integration test for ImperatorStateManager.initialize() |
| 1.9 | Domain knowledge seeding on first boot | Postgres, embedding API | NONE | — | seed_domain_knowledge() has no direct test |
| 1.10 | Graceful shutdown (task cancellation, pool close) | Postgres | NONE | — | No test exercises shutdown lifecycle |
| 1.11 | Postgres middleware (503 when unavailable) | Postgres | NONE | — | No test starts app without Postgres to verify 503 |
| 1.12 | HTTP exception handler (structured JSON) | HTTP | REAL | test_e2e_chat::test_invalid_json, test_e2e_mcp | Error responses are validated |
| 1.13 | Validation exception handler | HTTP | REAL | test_e2e_chat::test_invalid_role, test_e2e_mcp | Validation errors return 422 |
| 1.14 | Known exception handler (RuntimeError, etc.) | HTTP | NONE | — | No test triggers an unhandled RuntimeError through the global handler |
| 1.15 | AE config loading (YAML, mtime cache, content hash) | filesystem | MOCK | test_config::TestLoadConfig | Reads from tmp_path, validates caching |
| 1.16 | TE config loading (hot-reloadable) | filesystem | MOCK (partial) | test_config covers AE only | TE config load_te_config() has no dedicated test |
| 1.17 | Merged config (AE + TE overlay) | filesystem | NONE | — | load_merged_config() and async_load_config() not directly tested |
| 1.18 | Credentials file hot-reload | filesystem | NONE | — | _load_credentials() with mtime caching not tested |
| 1.19 | get_api_key resolution (credentials file → env) | filesystem, env | MOCK | test_config::TestGetApiKey | Tests env path only, not credentials file path |
| 1.20 | get_chat_model (cached ChatOpenAI factory) | LLM API | NONE | — | No test for cache eviction, role resolution, or instance creation |
| 1.21 | get_embeddings_model (cached factory) | Embedding API | MOCK (partial) | test_log_endpoints::test_get_embeddings_model_accepts_config_key | Only tests config_key parameter acceptance |
| 1.22 | LLM/embeddings cache cleared on config change | in-memory | MOCK | test_config::test_llm_cache_cleared_on_content_change | Verifies caches cleared |
| 1.23 | verbose_log / verbose_log_auto | config file | NONE | — | No test for verbose logging toggle |
| 1.24 | Log level configuration and update | logging | NONE | — | update_log_level() not tested |

---

## 2. HTTP Routes

### 2.1 Chat Route (`app/routes/chat.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 2.1.1 | POST /v1/chat/completions (non-streaming) | LLM API, Postgres | REAL | test_e2e_chat::test_valid_completion | Full round-trip through Imperator |
| 2.1.2 | POST /v1/chat/completions (streaming SSE) | LLM API, Postgres | REAL | test_e2e_chat::test_streaming_returns_sse_chunks | SSE chunk format validated |
| 2.1.3 | Invalid JSON body → 400 | HTTP | REAL | test_e2e_chat::test_invalid_json | |
| 2.1.4 | Missing messages → 400 | HTTP | REAL | test_e2e_chat::test_missing_messages | |
| 2.1.5 | Empty messages array → 400 | HTTP | REAL | test_e2e_chat::test_empty_messages | |
| 2.1.6 | No user message → 400 | HTTP | REAL | test_e2e_chat::test_no_user_message | |
| 2.1.7 | Invalid role → 422 | HTTP | REAL | test_e2e_chat::test_invalid_role | |
| 2.1.8 | Config load failure → 500 | config | NONE | — | Config failure path not tested |
| 2.1.9 | x-context-window-id header routing | HTTP | NONE | — | Custom header multi-client isolation not tested |
| 2.1.10 | ToolMessage conversion (tool role) | LangChain | NONE | — | ToolMessage with tool_call_id not tested |
| 2.1.11 | Caller identity injection | HTTP, DNS | NONE | — | resolve_caller integration with chat not tested end-to-end |
| 2.1.12 | Streaming error recovery (error chunk) | LLM API | NONE | — | Streaming failure fallback not tested |
| 2.1.13 | System + user messages combined | LLM API | REAL | test_e2e_chat::test_system_and_user_messages | |

### 2.2 Health Route (`app/routes/health.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 2.2.1 | GET /health → 200 when all healthy | Postgres, Neo4j | REAL | test_e2e_health::test_returns_200_when_healthy | |
| 2.2.2 | Postgres health status in response | Postgres | REAL | test_e2e_health::test_contains_postgres_status | |
| 2.2.3 | Neo4j health status in response | Neo4j (HTTP) | REAL | test_e2e_health::test_contains_neo4j_status | |
| 2.2.4 | Response time < threshold | HTTP | REAL | test_e2e_health::test_response_time_reasonable | |
| 2.2.5 | Config load failure → 503 degraded | config | NONE | — | Health degraded mode on config failure not tested |
| 2.2.6 | Health flow unavailable (AE not loaded) | StateGraph | NONE | — | RuntimeError when AE package missing not tested |

### 2.3 MCP Route (`app/routes/mcp.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 2.3.1 | POST /mcp — initialize handshake | HTTP | REAL | test_e2e_mcp::test_initialize | |
| 2.3.2 | POST /mcp — tools/list | HTTP | REAL | test_e2e_mcp::test_tools_list | |
| 2.3.3 | POST /mcp — tools/call dispatch | HTTP, Postgres | REAL | test_e2e_mcp (all tool tests) | |
| 2.3.4 | Invalid JSON → parse error | HTTP | REAL | test_e2e_mcp::test_invalid_json | |
| 2.3.5 | Unknown method → -32601 | HTTP | REAL | test_e2e_mcp::test_unknown_method | |
| 2.3.6 | Unknown tool → error | HTTP | REAL | test_e2e_mcp::test_unknown_tool | |
| 2.3.7 | GET /mcp — SSE session establishment | HTTP (SSE) | NONE | — | SSE session lifecycle not tested |
| 2.3.8 | Session-mode: queue and deliver via SSE | HTTP (SSE) | NONE | — | Session-based tool call delivery not tested |
| 2.3.9 | Session eviction (TTL, cap, queue pressure) | in-memory | NONE | — | Stale session eviction not tested |
| 2.3.10 | Session queue full → 503 | in-memory | NONE | — | Queue overflow handling not tested |
| 2.3.11 | Unknown sessionId → 404 | HTTP | NONE | — | Invalid session error not tested |
| 2.3.12 | Caller identity injection into config | HTTP, DNS | NONE | — | resolve_caller injection not tested |
| 2.3.13 | Decimal serialization in JSON response | HTTP | NONE | — | _json_default for Decimal not tested |
| 2.3.14 | Config load failure in tool call → 500 | config | NONE | — | |

### 2.4 Metrics Route (`app/routes/metrics.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 2.4.1 | GET /metrics → Prometheus format | in-memory | REAL | test_e2e_health::test_returns_200, test_e2e_health::test_prometheus_format | |
| 2.4.2 | MCP metrics present | in-memory | REAL | test_e2e_health::test_contains_mcp_metrics | |
| 2.4.3 | Flow error → 500 text | in-memory | NONE | — | Metrics flow error path not tested |

### 2.5 Caller Identity (`app/routes/caller_identity.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 2.5.1 | User field takes priority | — | MOCK | test_caller_identity | |
| 2.5.2 | Reverse DNS lookup | DNS | MOCK | test_caller_identity | socket.gethostbyaddr patched |
| 2.5.3 | DNS result caching | in-memory | MOCK | test_caller_identity | |
| 2.5.4 | DNS failure → IP fallback | DNS | MOCK | test_caller_identity | |
| 2.5.5 | No client → "unknown" | — | MOCK | test_caller_identity | |

---

## 3. Tool Dispatch (`app/flows/tool_dispatch.py`)

| # | Tool Name | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 3.1 | get_context | Postgres, build type registry | REAL | test_e2e_mcp::TestGetContext | Auto-creates conversation and window |
| 3.2 | store_message | Postgres | REAL | test_e2e_mcp::TestStoreMessageCore | |
| 3.3 | search_messages | Postgres, embedding API | REAL | test_e2e_mcp::TestSearchMessagesCore | |
| 3.4 | search_knowledge | Mem0, Neo4j | REAL | test_e2e_mcp::TestSearchKnowledge | |
| 3.5 | conv_create_conversation | Postgres | REAL | test_e2e_mcp::TestConvCreateConversation | |
| 3.6 | conv_delete_conversation | Postgres | REAL | test_e2e_mcp (implicit via cleanup) | Atomic cascade delete |
| 3.7 | conv_list_conversations | Postgres | REAL | test_e2e_mcp::TestConvSearch (partial) | Participant filter tested in model only |
| 3.8 | conv_store_message | Postgres | REAL | test_e2e_mcp::TestConvStoreMessage | |
| 3.9 | conv_retrieve_context | Postgres, build type registry | REAL | test_e2e_mcp::TestConvRetrieveContext | |
| 3.10 | conv_create_context_window | Postgres | REAL | test_e2e_mcp::TestConvCreateContextWindow | |
| 3.11 | conv_search | Postgres, embedding API | REAL | test_e2e_mcp::TestConvSearch | |
| 3.12 | conv_search_messages | Postgres, embedding API | REAL | test_e2e_mcp::TestConvSearchMessages | |
| 3.13 | conv_get_history | Postgres | REAL | test_e2e_mcp::TestConvGetHistory | |
| 3.14 | conv_search_context_windows | Postgres | REAL | test_e2e_mcp::TestConvSearchContextWindows | |
| 3.15 | query_logs | Postgres | MOCK (model only) | test_log_endpoints | Only validates input model; SQL execution not tested |
| 3.16 | search_logs | Postgres, embedding API | MOCK (model only) | test_log_endpoints | Only validates input model; vector search not tested |
| 3.17 | mem_search | Mem0, Neo4j | REAL | test_e2e_mcp::TestMemSearch | |
| 3.18 | mem_get_context | Mem0, Neo4j | REAL | test_e2e_mcp::TestMemGetContext | |
| 3.19 | mem_add | Mem0, Neo4j | NONE | — | Not tested (no test calls mem_add directly) |
| 3.20 | mem_list | Mem0, Neo4j | NONE | — | Not tested directly |
| 3.21 | mem_delete | Mem0, Neo4j | NONE | — | Not tested directly |
| 3.22 | imperator_chat | LLM API, Postgres | REAL | test_e2e_mcp::TestImperatorChat | |
| 3.23 | metrics_get | in-memory | REAL | test_e2e_mcp::TestMetricsGet | |
| 3.24 | install_stategraph | pip, filesystem, Postgres | NONE | — | Runtime package installation not tested |
| 3.25 | Prometheus metrics on every dispatch | in-memory | REAL | test_e2e_health::test_contains_mcp_metrics | MCP counters verified |
| 3.26 | Flow cache invalidation after install | in-memory | NONE | — | invalidate_flow_cache() not tested |

---

## 4. AE Package — Build Types

### 4.1 Passthrough (`build_types/passthrough.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 4.1.1 | Assembly: no-op (just marks assembled) | Postgres | REAL | test_components::test_e1 | Passthrough context verified |
| 4.1.2 | Retrieval: load recent messages, truncate to budget | Postgres | REAL | test_e2e_mcp::TestConvRetrieveContext | |
| 4.1.3 | Advisory lock acquire/release during assembly | Postgres | NONE | — | Lock behavior not isolated in tests |
| 4.1.4 | Token estimation (len/4) | — | NONE | — | Not tested independently |
| 4.1.5 | Graph compilation | — | MOCK | test_build_type_registry::test_shipped_assembly_graphs_compile | |

### 4.2 Standard-Tiered (`build_types/standard_tiered.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 4.2.1 | Assembly: chunk summarization via LLM | Postgres, LLM API | REAL | test_components::test_c1-c4 | Full pipeline with real LLM |
| 4.2.2 | Assembly: archival consolidation | Postgres, LLM API | REAL | test_components (implicit) | Via full pipeline |
| 4.2.3 | Assembly: tier boundary calculation | — | NONE | — | calculate_tier_boundaries() not unit-tested |
| 4.2.4 | Assembly: advisory lock with timeout | Postgres | NONE | — | Lock contention not tested |
| 4.2.5 | Assembly: summary dedup (UniqueViolationError) | Postgres | NONE | — | Concurrent assembly race not tested |
| 4.2.6 | Retrieval: wait for assembly (polling loop) | Postgres | REAL | test_e2e_pipeline::test_retrieve_context_has_messages_structure | |
| 4.2.7 | Retrieval: load summaries (tier1, tier2) | Postgres | REAL | test_components::test_e1, test_e2 | |
| 4.2.8 | Retrieval: load recent messages (tier3) | Postgres | REAL | test_components | |
| 4.2.9 | Retrieval: assemble context with tier structure | Postgres | REAL | test_components::test_e3 | Token utilization checked |
| 4.2.10 | Graph compilation | — | MOCK | test_build_type_registry::test_shipped_assembly_graphs_compile | |
| 4.2.11 | Prompt loading for summarization | filesystem | NONE | — | Prompt template loading not tested in isolation |

### 4.3 Knowledge-Enriched (`build_types/knowledge_enriched.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 4.3.1 | Semantic retrieval injection (vector search) | Postgres, embedding API | REAL | integration/evaluate_quality | Quality eval covers knowledge-enriched |
| 4.3.2 | Knowledge graph injection (Mem0/Neo4j) | Mem0, Neo4j | REAL | integration/evaluate_quality | |
| 4.3.3 | Memory scoring with half-life decay | — | MOCK | test_memory_scoring | Pure computation tests |
| 4.3.4 | Graceful degradation when Mem0 unavailable | Mem0 | NONE | — | Degradation path not tested |
| 4.3.5 | Graph compilation | — | MOCK | test_build_type_registry::test_shipped_retrieval_graphs_compile | |

### 4.4 Tier Scaling (`build_types/tier_scaling.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 4.4.1 | Short conversation tier3 boost | — | MOCK | test_tier_scaling::TestShortConversation | |
| 4.4.2 | Medium conversation no change | — | MOCK | test_tier_scaling::TestMediumConversation | |
| 4.4.3 | Long conversation tier1/tier2 boost | — | MOCK | test_tier_scaling::TestLongConversation | |
| 4.4.4 | Renormalization sums to original | — | MOCK | test_tier_scaling::TestRenormalization | |
| 4.4.5 | Edge cases (zero tiers, missing keys) | — | MOCK | test_tier_scaling::TestEdgeCases | |
| 4.4.6 | Input immutability | — | MOCK | test_tier_scaling::TestImmutability | |

---

## 5. AE Package — Core Flows

### 5.1 Message Pipeline (`message_pipeline.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 5.1.1 | Store message to Postgres | Postgres | REAL | test_e2e_mcp, test_e2e_pipeline | |
| 5.1.2 | Resolve conversation_id from context_window_id | Postgres | REAL | test_e2e_mcp::TestConvStoreMessage | |
| 5.1.3 | Resolve conversation_id from direct input | Postgres | REAL | test_e2e_mcp::TestStoreMessageCore | |
| 5.1.4 | Advisory lock per conversation | Postgres | NONE | — | Lock serialization not isolated |
| 5.1.5 | Consecutive duplicate collapse (repeat_count) | Postgres | REAL | test_e2e_pipeline::test_consecutive_duplicate_collapsed | |
| 5.1.6 | Atomic sequence number assignment | Postgres | NONE | — | Concurrent insert race not tested |
| 5.1.7 | UniqueViolationError retry (once) | Postgres | NONE | — | Retry on conflict not tested |
| 5.1.8 | Token count estimation (len/4) | — | NONE | — | |
| 5.1.9 | Recipient default derivation from role | — | NONE | — | Default recipient logic not tested |
| 5.1.10 | Tool calls stored as JSONB | Postgres | NONE | — | Tool call storage not verified |
| 5.1.11 | Conversation counter update | Postgres | NONE | — | total_messages/estimated_token_count not verified |

### 5.2 Embed Pipeline (`embed_pipeline.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 5.2.1 | Fetch message from Postgres | Postgres | NONE | — | Not tested in isolation |
| 5.2.2 | Generate embedding with contextual prefix | embedding API | REAL | test_e2e_pipeline::test_message_gets_embedding | Embedding presence verified in DB |
| 5.2.3 | Skip embedding for NULL content (tool messages) | — | NONE | — | Not tested |
| 5.2.4 | Store embedding vector to Postgres | Postgres | REAL | test_e2e_pipeline | Embedding column verified |
| 5.2.5 | Embedding API failure handling | embedding API | NONE | — | Error path not tested |

### 5.3 Memory Extraction (`memory_extraction.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 5.3.1 | Advisory lock acquisition | Postgres | MOCK | test_memory_extraction::TestAcquireExtractionLock | |
| 5.3.2 | Fetch unextracted messages | Postgres | MOCK | test_memory_extraction::TestFetchUnextractedMessages | |
| 5.3.3 | Build extraction text (cleaning, chunking) | — | MOCK | test_memory_extraction::TestBuildExtractionText | |
| 5.3.4 | Secret redaction before Mem0 | — | MOCK | test_memory_extraction::TestRedactSecrets | API keys, bearer tokens, sk- keys |
| 5.3.5 | Mem0 add() call for extraction | Mem0, Neo4j, LLM | MOCK | test_memory_extraction::TestRunMem0Extraction | Mem0 client mocked |
| 5.3.6 | Mark messages as extracted | Postgres | MOCK | test_memory_extraction::TestMarkMessagesExtracted | |
| 5.3.7 | Lock release | Postgres | MOCK | test_memory_extraction::TestReleaseExtractionLock | |
| 5.3.8 | Routing logic (all branches) | — | MOCK | test_memory_extraction::TestRouting | |
| 5.3.9 | Mem0 client reset on error | Mem0 | MOCK | test_memory_extraction::test_mem0_exception_triggers_reset | |
| 5.3.10 | Code block / URL / path stripping | — | MOCK | test_memory_extraction (implicit in build text tests) | |
| 5.3.11 | End-to-end extraction on real infra | Postgres, Mem0, Neo4j, LLM | REAL | integration/test_components::test_c3 | Full pipeline confirms extraction runs |

### 5.4 Search Flows (`search_flow.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 5.4.1 | Conversation search (vector + structured) | Postgres, embedding API | REAL | test_e2e_mcp::TestConvSearch | |
| 5.4.2 | Message hybrid search (vector + BM25 + RRF) | Postgres, embedding API | REAL | test_e2e_mcp::TestConvSearchMessages, TestSearchMessagesCore | |
| 5.4.3 | API reranking (/rerank endpoint) | HTTP (reranker) | NONE | — | Reranker call not tested |
| 5.4.4 | Recency bias on RRF scores | — | NONE | — | Recency decay not tested |
| 5.4.5 | Date filter validation (ISO-8601) | — | NONE | — | Invalid date handling not tested |
| 5.4.6 | Embedding failure → BM25 fallback | embedding API | NONE | — | Degradation not tested |
| 5.4.7 | Sender/role/date filter in WHERE clause | Postgres | NONE | — | Structured filters not tested in isolation |

### 5.5 Conversation Ops (`conversation_ops_flow.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 5.5.1 | Create conversation (idempotent) | Postgres | REAL | test_e2e_mcp::TestConvCreateConversation | |
| 5.5.2 | Create context window (budget resolution) | Postgres, LLM provider API | REAL | test_e2e_mcp::TestConvCreateContextWindow | |
| 5.5.3 | Get history (full message sequence) | Postgres | REAL | test_e2e_mcp::TestConvGetHistory | |
| 5.5.4 | Search context windows | Postgres | REAL | test_e2e_mcp::TestConvSearchContextWindows | |
| 5.5.5 | get_context auto-create conversation + window | Postgres | REAL | test_e2e_mcp::TestGetContext::test_auto_create_conversation | |
| 5.5.6 | Budget snapping to nearest bucket | — | MOCK | test_budget | Pure algorithm test |
| 5.5.7 | Token budget resolution (auto/explicit/override) | LLM provider API (HTTP) | MOCK | test_token_budget | HTTP calls mocked |

### 5.6 Memory Search & Admin (`memory_search_flow.py`, `memory_admin_flow.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 5.6.1 | Memory search via Mem0 | Mem0, Neo4j | REAL | test_e2e_mcp::TestMemSearch | |
| 5.6.2 | Memory context retrieval | Mem0, Neo4j | REAL | test_e2e_mcp::TestMemGetContext | |
| 5.6.3 | Memory scoring (half-life decay) | — | MOCK | test_memory_scoring | 21 tests |
| 5.6.4 | mem_add via Mem0 | Mem0, Neo4j | NONE | — | No test calls mem_add |
| 5.6.5 | mem_list via Mem0 | Mem0, Neo4j | NONE | — | No test calls mem_list |
| 5.6.6 | mem_delete via Mem0 | Mem0, Neo4j | NONE | — | No test calls mem_delete |
| 5.6.7 | Graceful degradation when Mem0 unavailable | Mem0 | NONE | — | degraded=True path not tested end-to-end |

### 5.7 Health & Metrics Flows (`health_flow.py`, `metrics_flow.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 5.7.1 | Postgres health check | Postgres | REAL | test_e2e_health | |
| 5.7.2 | Neo4j health check (HTTP probe) | Neo4j | REAL | test_e2e_health | |
| 5.7.3 | Prometheus metrics collection | in-memory | REAL | test_e2e_health | |
| 5.7.4 | Health → degraded when Neo4j down | Neo4j | NONE | — | Degraded state not tested |

---

## 6. AE Package — Mem0 Client (`memory/mem0_client.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 6.1 | Config hash computation | — | MOCK | test_mem0_client::TestComputeConfigHash | 9 tests |
| 6.2 | Neo4j config building | env vars | MOCK | test_mem0_client::TestNeo4jConfig | 7 tests |
| 6.3 | Embedding dims validation | config | MOCK | test_mem0_client::TestGetEmbeddingDims | 6 tests |
| 6.4 | Singleton lifecycle (get/reset/recreate) | — | MOCK | test_mem0_client::TestResetMem0Client, TestGetMem0Client | 8 tests |
| 6.5 | Build Mem0 instance with all providers | Mem0, Neo4j, pgvector | MOCK | test_mem0_client::TestBuildMem0Instance | Memory class mocked |
| 6.6 | Config change triggers recreation | — | MOCK | test_mem0_client::test_recreates_on_config_change | |
| 6.7 | PGVector insert monkey-patch (ON CONFLICT) | Postgres | NONE | — | Dedup patch not tested |
| 6.8 | Real Mem0 initialization against live infra | Mem0, Neo4j, Postgres | NONE | — | All Mem0 client tests mock the Memory class |

---

## 7. TE Package — Imperator (`imperator_flow.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 7.1 | ReAct agent loop with tool binding | LLM API | REAL | test_e2e_chat, test_e2e_mcp::TestImperatorChat | |
| 7.2 | Conversation history loading | Postgres | REAL | integration/run_imperator_conversation | Multi-turn conversation |
| 7.3 | Context loading via get_context dispatch | Postgres, build type | REAL | integration/test_components::test_d1 | |
| 7.4 | Store user/assistant messages after chat | Postgres | REAL | test_e2e_pipeline | Messages appear in DB |
| 7.5 | Max iterations fallback | LLM API | NONE | — | Runaway loop protection not tested |
| 7.6 | Empty LLM response retry (Gemini workaround) | LLM API | NONE | — | Retry logic not tested |
| 7.7 | Tool execution (conv_search, mem_search) | Postgres, Mem0 | REAL | integration/run_tool_exercises | |
| 7.8 | Admin tools gating (config toggle) | config | MOCK | test_tool_organization | |
| 7.9 | Domain knowledge tool gating | config | MOCK | test_tool_organization, test_domain_mem0 | |
| 7.10 | Prompt loading for system prompt | filesystem | NONE | — | Not tested in isolation |
| 7.11 | TE config hot-reload (flow recompilation) | filesystem | REAL | integration/test_components::test_b5 | |

---

## 8. TE Package — Tools

### 8.1 Admin Tools (`tools/admin.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 8.1.1 | config_read (redacted) | filesystem | NONE | — | Not tested |
| 8.1.2 | config_write (hot-reload) | filesystem | NONE | — | Not tested in unit; tested in integration via SSH |
| 8.1.3 | db_query (read-only SQL) | Postgres | NONE | — | Not tested |
| 8.1.4 | verbose_toggle | filesystem | NONE | — | Not tested |
| 8.1.5 | change_inference (model switching) | filesystem, HTTP, Postgres | MOCK | test_change_inference | 7 tests, all mocked |
| 8.1.6 | migrate_embeddings (destructive wipe) | Postgres, Mem0, Neo4j | MOCK (partial) | test_migration_tool | Dry run only tested |
| 8.1.7 | Config redaction (secret masking) | — | NONE | — | _redact_config() not tested |

### 8.2 Alerting Tools (`tools/alerting.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 8.2.1 | add_alert_instruction | Postgres, embedding API | MOCK | test_new_tools::TestAlertInstructionTools | |
| 8.2.2 | list_alert_instructions | Postgres | MOCK | test_new_tools::test_list_instructions_empty | |
| 8.2.3 | update_alert_instruction | Postgres | NONE | — | Not tested |
| 8.2.4 | delete_alert_instruction | Postgres | MOCK | test_new_tools::test_delete_nonexistent | |
| 8.2.5 | Embedding for semantic instruction search | embedding API | NONE | — | _embed_description() not tested |

### 8.3 Diagnostic Tools (`tools/diagnostic.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 8.3.1 | log_query (SQL log search) | Postgres | REAL | integration/run_tool_exercises (diagnostic prompts) | Via Imperator |
| 8.3.2 | context_introspection | Postgres | REAL | integration/run_tool_exercises | Via Imperator |
| 8.3.3 | pipeline_status | Postgres | REAL | integration/run_tool_exercises | Via Imperator |

### 8.4 Filesystem Tools (`tools/filesystem.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 8.4.1 | file_read (sandboxed) | filesystem | MOCK | test_new_tools::TestFilesystemSandbox | Path rejection tested |
| 8.4.2 | file_list (sandboxed) | filesystem | MOCK | test_new_tools::test_file_list_rejects_outside_sandbox | |
| 8.4.3 | file_write (downloads only) | filesystem | MOCK | test_new_tools::test_file_write_only_to_downloads | |
| 8.4.4 | file_search (regex, sandboxed) | filesystem | MOCK | test_new_tools::test_file_search_rejects_outside_sandbox | |
| 8.4.5 | read_system_prompt | filesystem | NONE | — | Not tested |
| 8.4.6 | update_system_prompt (with backup) | filesystem | NONE | — | Not tested |

### 8.5 Notification Tools (`tools/notify.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 8.5.1 | send_notification (CloudEvents webhook) | HTTP | MOCK | test_new_tools::TestSendNotification | httpx mocked |
| 8.5.2 | ntfy.sh format support | HTTP | NONE | — | ntfy format not tested |
| 8.5.3 | Default webhook from config | config | MOCK | test_new_tools::test_no_webhook_uses_default | |

### 8.6 Operational Tools (`tools/operational.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 8.6.1 | store_domain_info | Postgres, embedding API | NONE | — | Not tested |
| 8.6.2 | search_domain_info (vector search) | Postgres, embedding API | NONE | — | Not tested |
| 8.6.3 | extract_domain_knowledge (Mem0 KG) | Mem0, Neo4j, LLM | NONE | — | Not tested |
| 8.6.4 | search_domain_knowledge (Neo4j KG) | Mem0, Neo4j | NONE | — | Not tested |

### 8.7 Scheduling Tools (`tools/scheduling.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 8.7.1 | list_schedules | Postgres | NONE | — | Not tested |
| 8.7.2 | create_schedule (cron + interval) | Postgres | MOCK (partial) | test_scheduler::TestIntervalMinimum | Only input validation tested |
| 8.7.3 | enable_schedule / disable_schedule | Postgres | NONE | — | Not tested |

### 8.8 System Tools (`tools/system.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 8.8.1 | run_command (allowlisted) | subprocess | MOCK | test_new_tools::TestSystemCommands | Command rejection + allowlisting |
| 8.8.2 | calculate (safe math eval) | — | MOCK | test_new_tools::test_calculate_safe_math, test_calculate_rejects_code | |
| 8.8.3 | Command allowlist enforcement | — | MOCK | test_new_tools::test_run_command_rejects_disallowed | |

### 8.9 Web Tools (`tools/web.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 8.9.1 | web_search (DuckDuckGo) | HTTP | MOCK | test_new_tools::TestWebSearch | DDGS mocked |
| 8.9.2 | web_read (URL fetch + text extraction) | HTTP | MOCK | test_new_tools::TestWebRead | httpx mocked |
| 8.9.3 | HTML tag stripping fallback | — | MOCK | test_new_tools::test_html_fallback_strips_tags | |

---

## 9. TE Package — Domain Mem0 & Seed Knowledge

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 9.1 | Domain Mem0 singleton (separate from AE Mem0) | Mem0, Neo4j | MOCK | test_domain_mem0 | Memory class mocked |
| 9.2 | Domain knowledge tool gating | config | MOCK | test_domain_mem0, test_tool_organization | |
| 9.3 | Seed domain knowledge (13 articles) | Postgres, embedding API | NONE | — | Not tested |
| 9.4 | Seed embedding generation per article | embedding API | NONE | — | Not tested |
| 9.5 | Seed idempotency (re-running safe) | Postgres | NONE | — | No idempotency tested |

---

## 10. Background Workers (`app/workers/`)

### 10.1 DB Worker (`workers/db_worker.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 10.1.1 | Embedding worker — batch polling | Postgres, embedding API | REAL | test_e2e_pipeline::test_message_gets_embedding | Proves worker runs |
| 10.1.2 | Embedding worker — poison pill protection | Postgres, embedding API | NONE | — | Zero-vector fallback after 5 failures not tested |
| 10.1.3 | Embedding worker — timeout handling | embedding API | NONE | — | asyncio.TimeoutError path not tested |
| 10.1.4 | Extraction worker — per-conversation polling | Postgres, Mem0 | REAL | integration/test_components | Pipeline confirms extraction |
| 10.1.5 | Extraction worker — advisory lock coordination | Postgres | NONE | — | Lock contention not tested |
| 10.1.6 | Extraction worker — retry with max 3 attempts | Postgres, Mem0 | NONE | — | Retry exhaustion not tested |
| 10.1.7 | Extraction worker — mark failed after max retries | Postgres | NONE | — | Permanent failure marking not tested |
| 10.1.8 | Assembly worker — poll for stale windows | Postgres | REAL | integration/test_components | Assembly confirmed via retrieval |
| 10.1.9 | Assembly worker — trigger threshold check | Postgres | NONE | — | Threshold-based triggering not tested |
| 10.1.10 | Log embedding worker | Postgres, embedding API | NONE | — | Not tested at all |
| 10.1.11 | Log embedding — poison pill deletion | Postgres | NONE | — | Not tested |
| 10.1.12 | Worker cancellation on shutdown | asyncio | NONE | — | Not tested |

### 10.2 Scheduler (`workers/scheduler.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 10.2.1 | Cron expression parsing | — | MOCK | test_scheduler::TestCronParsing | 12 tests |
| 10.2.2 | Cron duplicate-fire prevention (< 55s) | — | NONE | — | Not tested |
| 10.2.3 | Interval schedule due detection | — | NONE | — | Not tested |
| 10.2.4 | Optimistic locking (atomic claim) | Postgres | NONE | — | Not tested |
| 10.2.5 | Schedule firing (dispatch to Imperator) | Postgres, LLM API | NONE | — | _fire_schedule not tested |
| 10.2.6 | Schedule history recording | Postgres | NONE | — | Not tested |
| 10.2.7 | Error recording in schedule_history | Postgres | NONE | — | Not tested |

---

## 11. Imperator State Manager (`app/imperator/state_manager.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 11.1 | Read state file (JSON) | filesystem | NONE | — | |
| 11.2 | Write state file (JSON) | filesystem | NONE | — | |
| 11.3 | Verify conversation exists in DB | Postgres | NONE | — | |
| 11.4 | Create new conversation on first boot | Postgres | NONE | — | |
| 11.5 | Resume existing conversation on restart | Postgres, filesystem | NONE | — | |
| 11.6 | Runtime recovery (deleted conversation) | Postgres, filesystem | NONE | — | PG-43 recovery not tested |
| 11.7 | get_context_window_id (backward compat) | Postgres | NONE | — | |

---

## 12. Database (`app/database.py`, `app/migrations.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 12.1 | PostgreSQL pool initialization | Postgres | REAL | test_e2e_health | Pool works (health check proves it) |
| 12.2 | Pool close | Postgres | NONE | — | |
| 12.3 | Postgres health check | Postgres | REAL | test_e2e_health | |
| 12.4 | Neo4j health check (HTTP) | Neo4j | REAL | test_e2e_health | |
| 12.5 | Schema migration runner (advisory lock) | Postgres | REAL | integration (implicit — migrations run at startup) | |
| 12.6 | Migrations 001-020 (individual) | Postgres | NONE | — | No individual migration tests |
| 12.7 | Migration failure → startup abort | Postgres | NONE | — | |
| 12.8 | Concurrent migration prevention (advisory lock) | Postgres | NONE | — | |

---

## 13. Alerter Service (`alerter/alerter.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 13.1 | POST /webhook — event ingestion | HTTP | NONE | — | No alerter tests exist |
| 13.2 | Instruction semantic search (vector + text) | Postgres, embedding API | NONE | — | |
| 13.3 | LLM formatting with instruction as system prompt | LLM API | NONE | — | |
| 13.4 | Channel fan-out (Slack, Discord, ntfy, SMTP, Twilio, webhook, log) | HTTP, SMTP | NONE | — | |
| 13.5 | Event + delivery history recording | Postgres | NONE | — | |
| 13.6 | Log context enrichment | Postgres | NONE | — | |
| 13.7 | Health endpoint | Postgres | NONE | — | |
| 13.8 | Table auto-creation on startup | Postgres | NONE | — | |
| 13.9 | Config loading | filesystem | NONE | — | |
| 13.10 | Slack webhook send | HTTP | NONE | — | |
| 13.11 | Discord webhook send | HTTP | NONE | — | |
| 13.12 | ntfy send | HTTP | NONE | — | |
| 13.13 | SMTP email send | SMTP | NONE | — | |
| 13.14 | Twilio SMS send | HTTP | NONE | — | |
| 13.15 | Generic webhook send | HTTP | NONE | — | |

---

## 14. Log Shipper (`log_shipper/shipper.py`)

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 14.1 | Docker API connection | Docker API | NONE | — | No log shipper tests exist |
| 14.2 | Network topology discovery | Docker API | NONE | — | |
| 14.3 | Container log tailing (follow mode) | Docker API | NONE | — | |
| 14.4 | Log parsing (timestamp + JSON/raw) | — | NONE | — | |
| 14.5 | Batch write to Postgres | Postgres | NONE | — | |
| 14.6 | High-water mark to prevent duplicate logs | Postgres | NONE | — | |
| 14.7 | Docker event watching (connect/disconnect) | Docker API | NONE | — | |
| 14.8 | Self-exclusion (don't tail own logs) | — | NONE | — | |
| 14.9 | Graceful shutdown (SIGTERM) | OS signals | NONE | — | |
| 14.10 | Log shipping verified end-to-end | Postgres | REAL | integration/test_components::test_g2 | Verified logs appear in system_logs table |

---

## 15. Miscellaneous

| # | Capability | Infrastructure | Test Status | Test File(s) | Notes |
|---|-----------|---------------|-------------|--------------|-------|
| 15.1 | Pydantic model validation (all models) | — | MOCK | test_models | 37 tests |
| 15.2 | Participant filter on ListConversationsInput | — | MOCK | test_participant_filter | 5 tests |
| 15.3 | Budget bucket definitions and snapping | — | MOCK | test_budget | 6 tests |
| 15.4 | State immutability (routing, scaling, scoring) | — | MOCK | test_state_immutability | 10 tests |
| 15.5 | Static checks (Dockerfile, docker-compose, secrets) | filesystem | REAL | test_static_checks | 28 tests |
| 15.6 | Structured JSON logging (JsonFormatter) | — | REAL | test_static_checks, test_integration_container | |
| 15.7 | HealthCheckFilter (suppress /health logs) | — | NONE | — | Filter not tested |
| 15.8 | Prompt loader (mtime cache, async) | filesystem | NONE | — | prompt_loader.py not tested |
| 15.9 | stable_lock_id (deterministic hash) | — | NONE | — | utils.py not tested |
| 15.10 | Metrics registry definitions | — | REAL | test_e2e_health | Metrics presence verified |
| 15.11 | install_stategraph (pip install + rescan) | pip, filesystem, Postgres | NONE | — | Not tested |
| 15.12 | StateGraph registry scan/eviction | importlib | MOCK (partial) | test_build_type_registry | Tests registration but not eviction/reimport |
| 15.13 | Build type registry (register, get, clear) | — | MOCK | test_build_type_registry | |
| 15.14 | Imperator wrapper (invoke_with_metrics) | LLM API | REAL | test_e2e_chat | Implicit via chat endpoint |
| 15.15 | Imperator wrapper (astream_events_with_metrics) | LLM API | REAL | test_e2e_chat::test_streaming | Implicit via streaming |
| 15.16 | Imperator wrapper TE config change detection | filesystem | NONE | — | Not tested |

---

## Summary Statistics

### By Coverage Status

| Status | Count | Percentage |
|--------|-------|-----------|
| REAL | 67 | 35% |
| MOCK | 63 | 33% |
| NONE | 62 | 32% |

### Critical Gaps (NONE)

**Entire subsystems with zero test coverage:**

1. **Alerter service** (13 capabilities) — Zero tests. No unit tests, no integration tests. Webhook ingestion, instruction search, LLM formatting, all 6 channel senders (Slack, Discord, ntfy, SMTP, Twilio, webhook), event history recording — completely untested.

2. **Log shipper** (9 capabilities) — Zero unit tests. Docker API interaction, log parsing, batch writes, event watching, graceful shutdown — all untested. Only verified indirectly via integration (logs appear in system_logs).

3. **Imperator state manager** (7 capabilities) — Zero tests. State file read/write, conversation verification, first-boot creation, restart resume, runtime recovery — all untested.

4. **Operational tools** (4 capabilities) — Zero tests. store_domain_info, search_domain_info, extract_domain_knowledge, search_domain_knowledge — all untested.

5. **Scheduler execution** (6 of 7 capabilities) — Only cron parsing tested. Schedule firing, history recording, optimistic locking, interval detection — all untested.

**High-risk untested paths:**

6. **SSE session lifecycle** — Session creation, queue delivery, eviction, overflow — all untested.
7. **Background worker failure paths** — Poison pill protection, timeout handling, retry exhaustion, concurrent lock contention — all untested.
8. **Database migration individual tests** — 20 migrations run at startup but none tested individually.
9. **Config hot-reload paths** — TE config, credentials file, merged config — mostly untested.
10. **mem_add / mem_list / mem_delete** — Memory admin tools have no end-to-end test.

### Test Count by File

| Test File | Count | Type |
|-----------|-------|------|
| test_models.py | 37 | MOCK |
| test_memory_extraction.py | 35 | MOCK |
| test_static_checks.py | 28 | REAL |
| test_mem0_client.py | 25 | MOCK |
| test_new_tools.py | 21 | MOCK |
| test_memory_scoring.py | 21 | MOCK |
| test_config.py | 20 | MOCK |
| test_tier_scaling.py | 18 | MOCK |
| test_token_budget.py | 14 | MOCK |
| test_scheduler.py | 12 | MOCK |
| test_tool_organization.py | 13 | MOCK |
| test_state_immutability.py | 10 | MOCK |
| test_e2e_mcp.py | ~60 | REAL |
| test_e2e_chat.py | 8 | REAL |
| test_e2e_health.py | 8 | REAL |
| test_e2e_pipeline.py | 6 | REAL |
| test_e2e_resilience.py | 6 | REAL |
| test_build_type_registry.py | 10 | MOCK |
| test_change_inference.py | 7 | MOCK |
| test_caller_identity.py | 6 | MOCK |
| test_budget.py | 6 | MOCK |
| test_participant_filter.py | 5 | MOCK |
| test_domain_mem0.py | 4 | MOCK |
| test_log_endpoints.py | 8 | MOCK |
| test_migration_tool.py | 2 | MOCK |
| test_integration_container.py | 11 | REAL |
| integration/test_components.py | 29 | REAL |
| integration/test_cross_provider.py | 3 | REAL |

**Total: ~450+ test functions**

### Observations

1. **Mock-heavy unit tests, real-heavy integration tests** — The unit test suite comprehensively mocks all external dependencies. The integration suite hits real infrastructure. There is very little middle ground (tests that hit *some* real infrastructure but stub others).

2. **Alerter is a blind spot** — It's a standalone FastAPI app with 6 channel senders, LLM formatting, instruction search, and history recording. Zero tests of any kind.

3. **Worker failure modes are the biggest risk** — The embedding, extraction, and assembly workers have complex failure handling (poison pills, retries, timeouts, advisory locks) that is never tested. These are the paths most likely to cause production incidents.

4. **SSE session management untested** — The MCP SSE session system (creation, eviction, queue pressure, keepalive) has no tests despite being a stateful in-memory system prone to leaks and races.

5. **Imperator state manager untested** — This manages persistent state across container restarts. File corruption, missing conversations, runtime deletion recovery — none tested.

6. **Memory admin tools (add/list/delete) untested** — These tools are exposed via MCP but never called in any test.

7. **Individual migrations untested** — 20 migrations run as a batch at startup. If a single migration has a bug, it would only be caught in production against a specific schema version.
