# Test Coverage Audit - ContextBroker

Legend:
- REAL: exercised against real infrastructure or deployed system
- MOCK: unit tests or tests that patch/mimic filesystem/DB/HTTP/LLM
- NONE: no test found

Scope:
- Phase 1: app/, packages/, alerter/
- Phase 2: tests/ and tests/integration/

================================================================================
APP RUNTIME AND HTTP ROUTES
================================================================================

1) FastAPI app lifecycle (startup/shutdown)
- What it does: loads config, validates build types and embeddings dims, initializes Postgres, runs migrations, starts background workers, initializes Imperator state, seeds domain knowledge, handles retry loop
- Infra: filesystem (/config), Postgres, background workers, TE package discovery, seed embeddings/LLM
- What could go wrong: missing config, invalid YAML, missing embedding_dims, Postgres unavailable, migration failures, TE package missing, seed failures
- Coverage: NONE

2) Middleware: Postgres availability gate
- What it does: returns 503 for non-/health and non-/metrics when Postgres unavailable
- Infra: app state, HTTP
- What could go wrong: false positives/negatives, blocks needed routes
- Coverage: NONE

3) Exception handlers (HTTP, validation, known runtime errors)
- What it does: normalizes errors into JSON responses
- Infra: HTTP
- What could go wrong: errors not caught, wrong status code
- Coverage: REAL
- Tests: tests/test_e2e_chat.py, tests/test_e2e_mcp.py

4) GET /health
- What it does: loads config, invokes health_check StateGraph
- Infra: config filesystem, Postgres, Neo4j (via flow), HTTP
- What could go wrong: config load failure, DB/Neo4j down, wrong status aggregation
- Coverage: REAL
- Tests: tests/test_e2e_health.py, tests/integration/test_components.py (B2, F1)

5) GET /metrics
- What it does: invokes metrics StateGraph, returns Prometheus data
- Infra: Prometheus registry, HTTP
- What could go wrong: flow error, empty output, wrong content-type
- Coverage: REAL
- Tests: tests/test_e2e_health.py, tests/integration/test_components.py (D6, G1)

6) POST /v1/chat/completions (non-streaming)
- What it does: validates request, loads config, resolves caller identity, invokes Imperator flow, returns OpenAI-compatible response
- Infra: config, Imperator flow (LLM), HTTP
- What could go wrong: invalid JSON, validation errors, no user message, flow errors
- Coverage: REAL
- Tests: tests/test_e2e_chat.py, tests/integration/test_components.py (A5, D1)

7) POST /v1/chat/completions (streaming SSE)
- What it does: streams tokens from Imperator via astream_events
- Infra: Imperator flow (LLM), HTTP streaming
- What could go wrong: streaming errors, missing DONE, empty stream
- Coverage: REAL
- Tests: tests/test_e2e_chat.py::TestChatStreaming

8) GET /mcp (SSE session)
- What it does: creates SSE session, manages per-session queue, eviction
- Infra: in-memory session store, HTTP streaming
- What could go wrong: session leaks, queue full, eviction logic errors
- Coverage: NONE

9) POST /mcp (JSON-RPC)
- What it does: handles initialize/tools/list/tools/call; routes to tool dispatch; optional session queueing
- Infra: HTTP, config, tool dispatch, optional SSE sessions
- What could go wrong: invalid JSON, invalid method, validation errors, queue overflow, config load failure
- Coverage: REAL for initialize/tools/list/tools/call + invalid JSON/method; NONE for session queueing
- Tests: tests/test_e2e_mcp.py (protocol + tool calls)

================================================================================
CONFIGURATION, LOGGING, PROMPTS
================================================================================

10) Config load + cache (AE config)
- What it does: load /config/config.yml with mtime cache and hash invalidation; clears LLM/embedding caches on content change
- Infra: filesystem, in-memory caches
- What could go wrong: missing file, invalid YAML, stale cache
- Coverage: REAL (unit, no mocks of file IO)
- Tests: tests/test_config.py::TestLoadConfig

11) Config load + cache (TE config)
- What it does: load /config/te.yml with mtime cache; async load
- Infra: filesystem
- What could go wrong: missing file, invalid YAML
- Coverage: NONE

12) Config merge (AE + TE)
- What it does: overlay TE on AE for merged config
- Infra: filesystem
- What could go wrong: missing TE config, merge precedence
- Coverage: NONE

13) Credentials file hot-reload
- What it does: reads /config/credentials/.env; falls back to env
- Infra: filesystem, environment
- What could go wrong: missing file, stale cache, parse errors
- Coverage: NONE

14) get_api_key
- What it does: resolves API key from credentials file or env
- Infra: filesystem, environment
- What could go wrong: missing env var, empty key
- Coverage: REAL (unit)
- Tests: tests/test_config.py::TestGetApiKey

15) get_build_type_config validation
- What it does: fetches build type config, validates tier percentages
- Infra: none
- What could go wrong: unknown build type, invalid percentages
- Coverage: REAL (unit)
- Tests: tests/test_config.py::TestGetBuildTypeConfig

16) get_tuning
- What it does: returns tuning value from config with defaults
- Infra: none
- What could go wrong: missing tuning sections
- Coverage: REAL (unit)
- Tests: tests/test_config.py::TestGetTuning

17) get_chat_model cache
- What it does: returns cached ChatOpenAI client with eviction
- Infra: LLM API config
- What could go wrong: cache collision, wrong config, missing API key
- Coverage: NONE

18) get_embeddings_model cache
- What it does: returns cached embeddings client; supports config_key; handles dimensions
- Infra: embeddings API config
- What could go wrong: missing embedding_dims, wrong config_key
- Coverage: PARTIAL REAL (signature check only)
- Tests: tests/test_log_endpoints.py::TestLogEmbeddingsConfig

19) verbose_log / verbose_log_auto
- What it does: conditional logging based on config
- Infra: config filesystem
- What could go wrong: config load errors
- Coverage: NONE

20) Logging setup (JSON formatting, health filter)
- What it does: JSON logs with structured fields; filters health noise
- Infra: stdout logging
- What could go wrong: non-JSON logs, missing fields
- Coverage: REAL
- Tests: tests/test_static_checks.py::TestStructuredLogging, tests/test_integration_container.py::TestStructuredLogging

21) Prompt loader (sync/async)
- What it does: read prompt files from /config/prompts with mtime caching
- Infra: filesystem
- What could go wrong: missing files, stale cache
- Coverage: NONE

================================================================================
DATABASE, MIGRATIONS, HEALTH
================================================================================

22) Postgres pool init/get/close
- What it does: create asyncpg pool and manage lifecycle
- Infra: Postgres
- What could go wrong: connection failure, not initialized
- Coverage: NONE

23) Postgres health check
- What it does: SELECT 1 health probe
- Infra: Postgres
- What could go wrong: connection errors
- Coverage: REAL (via /health e2e)
- Tests: tests/test_e2e_health.py, tests/integration/test_components.py

24) Neo4j health check
- What it does: HTTP probe to Neo4j
- Infra: HTTP, Neo4j
- What could go wrong: HTTP errors, auth mismatch
- Coverage: REAL (via /health e2e)
- Tests: tests/test_e2e_health.py, tests/integration/test_components.py

25) Schema migrations
- What it does: advisory-lock serialized migrations; many schema updates and indexes
- Infra: Postgres
- What could go wrong: migration failure, lock contention, missing tables
- Coverage: NONE

================================================================================
STATEGRAPH REGISTRY AND BUILD TYPE REGISTRY
================================================================================

26) StateGraph package discovery (entry_points scan)
- What it does: discovers AE/TE packages, registers build types and flows, hot-reload via sys.modules eviction
- Infra: Python entry_points, in-process registry
- What could go wrong: missing packages, load errors, stale modules
- Coverage: REAL (unit)
- Tests: tests/test_build_type_registry.py::TestShippedBuildTypes

27) Build type registry (register/get/list/cache)
- What it does: registers build types, lazy compilation and caching
- Infra: in-process registry
- What could go wrong: missing build types, cache misuse
- Coverage: REAL (unit)
- Tests: tests/test_build_type_registry.py

================================================================================
CORE MCP TOOL DISPATCH (app/flows/tool_dispatch.py)
================================================================================

28) get_context core tool
- What it does: auto-create conversation/window, retrieve context via build type retrieval graph
- Infra: Postgres, build type graphs
- What could go wrong: invalid build type, missing window, retrieval error
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestGetContext, tests/integration/test_components.py (C3, E1, E3)

29) store_message core tool
- What it does: store message via message_pipeline flow
- Infra: Postgres
- What could go wrong: validation errors, conversation missing
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestStoreMessageCore

30) search_messages core tool
- What it does: hybrid search flow on messages
- Infra: Postgres, embeddings, reranker (optional)
- What could go wrong: embedding failures, SQL errors, reranker API failure
- Coverage: REAL for baseline search; NONE for reranker API path
- Tests: tests/test_e2e_mcp.py::TestSearchMessagesCore, tests/integration/test_components.py (C4, E2)

31) search_knowledge core tool
- What it does: Mem0 knowledge search
- Infra: Mem0, Neo4j, pgvector
- What could go wrong: Mem0 unavailable, degraded mode
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestSearchKnowledge

32) conv_create_conversation
- What it does: creates conversation via flow
- Infra: Postgres
- What could go wrong: invalid UUID, DB errors
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestConvCreateConversation

33) conv_delete_conversation
- What it does: deletes conversation + summaries + windows + messages (transaction)
- Infra: Postgres
- What could go wrong: partial deletion, transaction errors
- Coverage: NONE

34) conv_list_conversations
- What it does: list conversations with optional participant filter
- Infra: Postgres
- What could go wrong: query errors
- Coverage: NONE for real DB; MOCK for input validation
- Tests: tests/test_participant_filter.py

35) conv_store_message
- What it does: store message via message_pipeline; returns collapsed info
- Infra: Postgres
- What could go wrong: missing IDs, collapse logic
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestConvStoreMessage, tests/test_e2e_pipeline.py::TestDuplicateDetection

36) conv_retrieve_context
- What it does: retrieve context by window using build type
- Infra: Postgres, build type retrieval graphs
- What could go wrong: window not found, build_type missing
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestConvRetrieveContext, tests/test_e2e_pipeline.py::TestContextRetrievalFormat

37) conv_create_context_window
- What it does: create context window with resolved token budget
- Infra: Postgres, token budget resolver
- What could go wrong: invalid build type, conversation missing
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestConvCreateContextWindow

38) conv_search
- What it does: conversation search flow (vector or structured)
- Infra: Postgres, embeddings
- What could go wrong: embedding failures, invalid date filters
- Coverage: REAL (baseline) and MOCK (input validation only)
- Tests: tests/test_e2e_mcp.py::TestConvSearch

39) conv_search_messages
- What it does: message search flow (hybrid)
- Infra: Postgres, embeddings, reranker
- What could go wrong: embedding failures, invalid date filters
- Coverage: REAL (baseline)
- Tests: tests/test_e2e_mcp.py::TestConvSearchMessages

40) conv_get_history
- What it does: returns conversation metadata + messages
- Infra: Postgres
- What could go wrong: conversation not found, limit logic
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestConvGetHistory

41) conv_search_context_windows
- What it does: search context windows by filters or ID
- Infra: Postgres
- What could go wrong: invalid filters
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestConvSearchContextWindows

42) query_logs tool
- What it does: SQL query on system_logs with filters
- Infra: Postgres (system_logs)
- What could go wrong: SQL errors, no logs
- Coverage: MOCK (input model only)
- Tests: tests/test_log_endpoints.py::TestQueryLogsInput

43) search_logs tool
- What it does: semantic search on log embeddings
- Infra: Postgres (vector), embeddings model
- What could go wrong: log_embeddings not configured, embedding failure
- Coverage: MOCK (input model only)
- Tests: tests/test_log_endpoints.py::TestSearchLogsInput

44) mem_search tool
- What it does: Mem0 search via memory_search flow
- Infra: Mem0, Neo4j, pgvector
- What could go wrong: degraded mode, Mem0 unavailable
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestMemSearch

45) mem_get_context tool
- What it does: Mem0 search and format for prompt injection
- Infra: Mem0, Neo4j
- What could go wrong: degraded mode, Mem0 unavailable
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestMemGetContext

46) imperator_chat tool
- What it does: sends message to Imperator flow
- Infra: LLM, Postgres (store_message)
- What could go wrong: TE not installed, flow errors
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestImperatorChat, tests/integration/test_components.py::test_d2

47) mem_add tool
- What it does: add memory to Mem0 via StateGraph
- Infra: Mem0, Neo4j, pgvector
- What could go wrong: Mem0 unavailable
- Coverage: NONE

48) mem_list tool
- What it does: list Mem0 memories
- Infra: Mem0
- What could go wrong: Mem0 unavailable
- Coverage: NONE

49) mem_delete tool
- What it does: delete Mem0 memory
- Infra: Mem0
- What could go wrong: Mem0 unavailable
- Coverage: NONE

50) metrics_get tool
- What it does: returns Prometheus metrics via flow
- Infra: Prometheus registry
- What could go wrong: flow errors
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestMetricsGet

51) install_stategraph tool
- What it does: pip install stategraph package, rescan entry_points, clear caches, record in DB
- Infra: subprocess pip, filesystem, Postgres
- What could go wrong: pip failure, package not found, DB errors
- Coverage: REAL (integration component test uses deployed system)
- Tests: tests/integration/test_components.py::test_a3

================================================================================
BACKGROUND WORKERS (app/workers)
================================================================================

52) Embedding worker loop
- What it does: batch embed conversation_messages with NULL embeddings, update DB, metrics
- Infra: Postgres, embeddings model
- What could go wrong: embedding timeouts, repeated failures, poison pill logic
- Coverage: REAL (pipeline e2e checks embeddings)
- Tests: tests/test_e2e_pipeline.py::TestPipelineEmbedding

53) Extraction worker loop
- What it does: batch Mem0 extraction for unextracted messages, advisory locks, retries
- Infra: Postgres, Mem0, Neo4j
- What could go wrong: lock contention, Mem0 errors, infinite retries
- Coverage: NONE (no real worker test)

54) Assembly worker loop
- What it does: triggers context assembly for windows with new messages
- Infra: Postgres, build type assembly graphs
- What could go wrong: timeouts, graph errors
- Coverage: NONE (no explicit worker test)

55) Log embedding worker loop
- What it does: embed system_logs, store vectors
- Infra: Postgres, embeddings model
- What could go wrong: embedding failures, deletes entries after repeated failures
- Coverage: NONE

56) Scheduler worker loop
- What it does: polls schedules table and fires due schedules
- Infra: Postgres, tool_dispatch, Imperator
- What could go wrong: double firing, claim failure, schedule parsing
- Coverage: NONE (worker), PARTIAL for cron parsing in unit test
- Tests: tests/test_scheduler.py::TestCronParsing

================================================================================
AE STATEGRAPHS AND DATA FLOWS (packages/context-broker-ae)
================================================================================

57) Message pipeline flow (store_message)
- What it does: insert message with sequence number, collapse duplicates, update counters
- Infra: Postgres
- What could go wrong: missing conversation/window, unique violation, duplicate collapse errors
- Coverage: REAL (e2e MCP store_message)
- Tests: tests/test_e2e_mcp.py::TestConvStoreMessage, tests/test_e2e_pipeline.py::TestDuplicateDetection

58) Embed pipeline flow
- What it does: fetch message, build contextual embedding, store embedding
- Infra: Postgres, embeddings model
- What could go wrong: missing message, embedding failure
- Coverage: REAL (pipeline e2e)
- Tests: tests/test_e2e_pipeline.py::TestPipelineEmbedding

59) Memory extraction flow
- What it does: lock, fetch unextracted messages, clean/redact, chunk, Mem0 add, mark extracted
- Infra: Postgres, Mem0, Neo4j
- What could go wrong: Mem0 failures, lock contention, partial extraction
- Coverage: MOCK (extensive unit tests with mocked DB/Mem0)
- Tests: tests/test_memory_extraction.py

60) Memory search flow (mem_search)
- What it does: Mem0 search + half-life decay ranking
- Infra: Mem0
- What could go wrong: Mem0 unavailable, degraded mode
- Coverage: REAL (e2e mem_search) + REAL (unit scoring)
- Tests: tests/test_e2e_mcp.py::TestMemSearch, tests/test_memory_scoring.py

61) Memory context flow (mem_get_context)
- What it does: Mem0 search + format for prompt injection
- Infra: Mem0
- What could go wrong: Mem0 unavailable, degraded mode
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestMemGetContext

62) Memory admin flows (mem_add/mem_list/mem_delete)
- What it does: Mem0 add/list/delete via StateGraph
- Infra: Mem0
- What could go wrong: Mem0 unavailable
- Coverage: NONE

63) Conversation search flow
- What it does: vector search over conversations or structured filters
- Infra: Postgres, embeddings
- What could go wrong: embedding failure, invalid date parsing
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestConvSearch

64) Message search flow (hybrid vector + BM25 + rerank)
- What it does: hybrid retrieval, RRF fusion, recency bias, optional reranker API
- Infra: Postgres, embeddings, reranker HTTP
- What could go wrong: embedding failure, reranker failure, invalid date parsing
- Coverage: REAL for baseline hybrid; NONE for reranker API path
- Tests: tests/test_e2e_mcp.py::TestConvSearchMessages

65) Conversation ops: create conversation
- What it does: insert conversation (idempotent)
- Infra: Postgres
- What could go wrong: invalid UUID
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestConvCreateConversation

66) Conversation ops: create context window
- What it does: resolve token budget, create window (idempotent)
- Infra: Postgres, token budget resolution
- What could go wrong: invalid build type, conversation missing
- Coverage: REAL (overall) / MOCK (token budget details)
- Tests: tests/test_e2e_mcp.py::TestConvCreateContextWindow, tests/test_token_budget.py

67) Conversation ops: get history
- What it does: returns conversation metadata + messages
- Infra: Postgres
- What could go wrong: missing conversation
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestConvGetHistory

68) Conversation ops: search context windows
- What it does: filter context windows by ids/attrs
- Infra: Postgres
- What could go wrong: invalid IDs
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestConvSearchContextWindows

69) get_context flow
- What it does: auto create conversation, snap budget, create/find window, invoke retrieval
- Infra: Postgres, build type retrieval
- What could go wrong: build type invalid, conversation deleted
- Coverage: REAL
- Tests: tests/test_e2e_mcp.py::TestGetContext

70) Health check flow
- What it does: check Postgres and Neo4j, aggregate status
- Infra: Postgres, Neo4j
- What could go wrong: dependency down, wrong status
- Coverage: REAL
- Tests: tests/test_e2e_health.py

71) Metrics flow
- What it does: generate Prometheus metrics from registry
- Infra: Prometheus
- What could go wrong: registry errors
- Coverage: REAL
- Tests: tests/test_e2e_health.py

72) Context assembly shim (context_assembly.py)
- What it does: re-exports standard-tiered assembly
- Infra: none
- What could go wrong: import errors
- Coverage: NONE

73) Retrieval shim (retrieval_flow.py)
- What it does: re-exports knowledge-enriched retrieval
- Infra: none
- What could go wrong: import errors
- Coverage: NONE

================================================================================
BUILD TYPES (packages/context-broker-ae/build_types)
================================================================================

74) Passthrough assembly
- What it does: advisory lock, update last_assembled_at
- Infra: Postgres
- What could go wrong: lock failure, update failure
- Coverage: NONE

75) Passthrough retrieval
- What it does: load window, update last_accessed_at, return recent messages
- Infra: Postgres
- What could go wrong: invalid UUID, window missing
- Coverage: REAL (via get_context/conv_retrieve_context passthrough)
- Tests: tests/test_e2e_mcp.py::TestConvRetrieveContext, tests/integration/test_components.py::test_e1

76) Standard-tiered assembly
- What it does: lock, load window, load messages, chunk summarize, consolidate archival, update last_assembled_at
- Infra: Postgres, LLM (summarization), prompt files
- What could go wrong: LLM failures, prompt missing, lock contention, budget guard
- Coverage: NONE

77) Standard-tiered retrieval
- What it does: wait for assembly, load summaries, load recent messages, build context tiers
- Infra: Postgres
- What could go wrong: assembly timeout, invalid UUID, missing build type
- Coverage: NONE

78) Knowledge-enriched retrieval
- What it does: standard-tiered retrieval + semantic retrieval + Mem0 facts
- Infra: Postgres, embeddings, Mem0
- What could go wrong: embedding failure, Mem0 unavailable
- Coverage: NONE

79) Tier scaling
- What it does: adjust tier1/2/3 percentages by message count
- Infra: none
- What could go wrong: negative percentages, drift
- Coverage: REAL (unit)
- Tests: tests/test_tier_scaling.py

================================================================================
TE IMPERATOR FLOW AND TOOLS (packages/context-broker-te)
================================================================================

80) Imperator flow invocation (ReAct)
- What it does: LLM + tool loop, returns response_text
- Infra: LLM API, tool dispatch, Postgres for storing messages
- What could go wrong: LLM errors, tool errors, max iterations
- Coverage: REAL
- Tests: tests/test_e2e_chat.py, tests/test_e2e_mcp.py::TestImperatorChat, tests/integration/test_components.py (A5, D1, D2)

81) Imperator streaming via astream_events
- What it does: stream events for SSE
- Infra: LLM API
- What could go wrong: missing token events
- Coverage: REAL (via chat streaming)
- Tests: tests/test_e2e_chat.py::TestChatStreaming

82) Imperator tool discovery and gating
- What it does: collects tools based on config (admin_tools, domain flags)
- Infra: none
- What could go wrong: missing tools
- Coverage: REAL (unit)
- Tests: tests/test_tool_organization.py

83) Imperator store_user_message + store_assistant_message
- What it does: persist messages via core store_message tool
- Infra: Postgres
- What could go wrong: empty response text, tool dispatch errors
- Coverage: NONE (not directly asserted)

84) Imperator system prompt loading + context self-consumption
- What it does: loads prompt from /config/prompts, calls get_context on first turn
- Infra: filesystem, tool dispatch, Postgres
- What could go wrong: missing prompt, get_context error
- Coverage: NONE

85) Imperator retry on empty LLM response
- What it does: retries up to 2 times on empty response
- Infra: LLM
- What could go wrong: still empty response
- Coverage: NONE

86) Imperator max-iteration fallback
- What it does: injects fallback response when tool loop exceeds limit
- Infra: none
- What could go wrong: empty response
- Coverage: NONE

87) TE register() metadata
- What it does: exposes identity/purpose and tools_required
- Infra: none
- What could go wrong: missing entry_points
- Coverage: REAL (indirect via build type registry scan)
- Tests: tests/test_build_type_registry.py::TestShippedBuildTypes

88) Domain Mem0 client (TE)
- What it does: builds separate Mem0 instance with domain_memories collection
- Infra: Postgres, Neo4j, LLM/embeddings
- What could go wrong: missing embedding_dims, Mem0 init failure
- Coverage: MOCK
- Tests: tests/test_domain_mem0.py

89) Seed domain knowledge on startup
- What it does: inserts seed articles into domain_information, embeddings
- Infra: Postgres, embeddings
- What could go wrong: embedding failures, table missing
- Coverage: NONE

================================================================================
TE TOOLS
================================================================================

90) Admin tools - config_read
- What it does: read config.yml with redaction
- Infra: filesystem
- What could go wrong: file missing, YAML error
- Coverage: NONE

91) Admin tools - db_query (read-only)
- What it does: executes SQL in read-only transaction
- Infra: Postgres
- What could go wrong: SQL errors, timeout
- Coverage: NONE

92) Admin tools - config_write
- What it does: writes config.yml key/value with type conversion
- Infra: filesystem
- What could go wrong: invalid key path, YAML write failure
- Coverage: NONE

93) Admin tools - verbose_toggle
- What it does: toggles verbose_logging in config
- Infra: filesystem
- What could go wrong: config read/write error
- Coverage: NONE

94) Admin tools - change_inference
- What it does: list/switch models, validate endpoints, warns for embeddings
- Infra: filesystem, HTTP, Postgres (embedding counts)
- What could go wrong: endpoint unreachable, catalog missing
- Coverage: MOCK
- Tests: tests/test_change_inference.py

95) Admin tools - migrate_embeddings
- What it does: destructive migration of embeddings and reset Mem0
- Infra: filesystem, Postgres, Mem0
- What could go wrong: partial DB failures
- Coverage: MOCK (dry run only)
- Tests: tests/test_migration_tool.py

96) Alerting tools - add_alert_instruction
- What it does: validate JSON channels, embed description, insert instruction
- Infra: Postgres, embeddings
- What could go wrong: invalid JSON, embedding failure, DB failure
- Coverage: MOCK
- Tests: tests/test_new_tools.py::TestAlertInstructionTools

97) Alerting tools - list_alert_instructions
- What it does: list instructions
- Infra: Postgres
- What could go wrong: DB failure
- Coverage: MOCK
- Tests: tests/test_new_tools.py::TestAlertInstructionTools

98) Alerting tools - update_alert_instruction
- What it does: update fields, re-embed
- Infra: Postgres, embeddings
- What could go wrong: invalid JSON, embedding failure
- Coverage: NONE

99) Alerting tools - delete_alert_instruction
- What it does: delete instruction
- Infra: Postgres
- What could go wrong: DB failure
- Coverage: MOCK
- Tests: tests/test_new_tools.py::TestAlertInstructionTools

100) Diagnostic tools - log_query
- What it does: query system_logs
- Infra: Postgres
- What could go wrong: DB errors, no logs
- Coverage: REAL (integration chat tool exercises)
- Tests: tests/integration/test_components.py::test_d4

101) Diagnostic tools - context_introspection
- What it does: introspect window tiering and counts
- Infra: Postgres
- What could go wrong: missing window
- Coverage: REAL (integration chat tool exercises)
- Tests: tests/integration/test_components.py::test_d5

102) Diagnostic tools - pipeline_status
- What it does: returns pipeline queue stats
- Infra: Postgres
- What could go wrong: DB errors
- Coverage: REAL (integration chat tool exercises)
- Tests: tests/integration/test_components.py::test_d3

103) Filesystem tools - file_read
- What it does: read file with sandbox checks
- Infra: filesystem
- What could go wrong: sandbox escape, file not found
- Coverage: MOCK
- Tests: tests/test_new_tools.py::TestFilesystemSandbox

104) Filesystem tools - file_list
- What it does: list directory with sandbox checks
- Infra: filesystem
- What could go wrong: sandbox escape
- Coverage: MOCK
- Tests: tests/test_new_tools.py::TestFilesystemSandbox

105) Filesystem tools - file_search
- What it does: grep-like search with regex
- Infra: filesystem
- What could go wrong: regex errors, sandbox escape
- Coverage: MOCK
- Tests: tests/test_new_tools.py::TestFilesystemSandbox

106) Filesystem tools - file_write
- What it does: write under /data/downloads
- Infra: filesystem
- What could go wrong: sandbox escape, permission error
- Coverage: MOCK
- Tests: tests/test_new_tools.py::TestFilesystemSandbox

107) Filesystem tools - read_system_prompt
- What it does: read active prompt file
- Infra: filesystem
- What could go wrong: missing prompt file
- Coverage: NONE

108) Filesystem tools - update_system_prompt
- What it does: overwrite prompt file with backup
- Infra: filesystem
- What could go wrong: short content, write failure
- Coverage: NONE

109) Notify tool - send_notification
- What it does: send CloudEvents or webhook/ntfy payloads
- Infra: HTTP
- What could go wrong: webhook failures
- Coverage: MOCK
- Tests: tests/test_new_tools.py::TestSendNotification

110) Operational tools - store_domain_info
- What it does: embed and store domain info
- Infra: Postgres, embeddings
- What could go wrong: embedding failure
- Coverage: NONE

111) Operational tools - search_domain_info
- What it does: semantic search over domain_information
- Infra: Postgres, embeddings
- What could go wrong: embedding failure
- Coverage: NONE

112) Operational tools - extract_domain_knowledge
- What it does: Mem0 add for domain info
- Infra: Mem0, Postgres
- What could go wrong: Mem0 unavailable
- Coverage: NONE (gating only tested)

113) Operational tools - search_domain_knowledge
- What it does: Mem0 search for domain
- Infra: Mem0
- What could go wrong: Mem0 unavailable
- Coverage: NONE (gating only tested)

114) Scheduling tools - list_schedules
- What it does: list schedules and history
- Infra: Postgres
- What could go wrong: DB errors
- Coverage: NONE

115) Scheduling tools - create_schedule
- What it does: validate and insert schedule
- Infra: Postgres
- What could go wrong: invalid interval/cron, DB errors
- Coverage: MOCK (validation only)
- Tests: tests/test_scheduler.py::TestIntervalMinimum

116) Scheduling tools - enable_schedule
- What it does: set enabled true
- Infra: Postgres
- What could go wrong: invalid UUID, DB errors
- Coverage: NONE

117) Scheduling tools - disable_schedule
- What it does: set enabled false
- Infra: Postgres
- What could go wrong: invalid UUID, DB errors
- Coverage: NONE

118) System tools - run_command (allowlist)
- What it does: execute allowlisted shell commands
- Infra: subprocess
- What could go wrong: command injection, timeout
- Coverage: MOCK
- Tests: tests/test_new_tools.py::TestSystemCommands

119) System tools - calculate
- What it does: safe math eval
- Infra: none
- What could go wrong: injection
- Coverage: MOCK (unit)
- Tests: tests/test_new_tools.py::TestSystemCommands

120) Web tools - web_search
- What it does: DuckDuckGo search
- Infra: HTTP
- What could go wrong: dependency missing
- Coverage: MOCK
- Tests: tests/test_new_tools.py::TestWebSearch

121) Web tools - web_read
- What it does: read and extract web page content, crawl4ai fallback
- Infra: HTTP, SSL, optional crawl4ai
- What could go wrong: HTTP errors, crawler missing
- Coverage: MOCK
- Tests: tests/test_new_tools.py::TestWebRead

================================================================================
ALERTER SERVICE (alerter/alerter.py)
================================================================================
122) Alerter config loader
- What it does: read server/env config, validate required fields
- Infra: filesystem/env
- What could go wrong: missing/invalid config
- Coverage: NONE

123) Alerter Slack send
- What it does: send Slack message if token+channel present
- Infra: Slack HTTP
- What could go wrong: HTTP errors, rate limits
- Coverage: NONE

124) Alerter Email send
- What it does: send SMTP email if host+from+to present
- Infra: SMTP
- What could go wrong: auth/connection failures
- Coverage: NONE

125) Alerter HTTP server
- What it does: Flask app with /health and /alert endpoints
- Infra: HTTP
- What could go wrong: bad payloads, server errors
- Coverage: NONE

126) Alerter CLI loop
- What it does: poll checks and send alerts periodically
- Infra: HTTP, Slack, SMTP, time
- What could go wrong: runaway loop, failures not surfaced
- Coverage: NONE

================================================================================
NOTES ON REAL VS MOCK
================================================================================
- The suite is dominated by unit tests that mock DB connections, HTTP clients, and external SDKs. These validate control flow and error handling but do not prove real infrastructure compatibility.
- The E2E tests exercise HTTP routes, but they still mock the LLM client and sometimes patch DB access. They are not true system tests against a live Postgres/Mem0/Neo4j stack.
- tests/integration contains runnable scripts for real infra, but they are not structured as assertions and do not appear in CI as tests. Treat them as manual checks, not automated coverage.

================================================================================
MAJOR GAPS (HIGH IMPACT)
================================================================================
- Real DB coverage for core data flows (migrations, memory store/search, schedule processing, audit logging) is missing.
- Real Mem0 coverage for extract/search/store flows is missing; only stubs or gating checks exist.
- Real provider coverage for LLM chat, embeddings, and streaming is missing; the LLM is mocked.
- Real HTTP coverage for external endpoints (Slack, web search/read, conversation ops callbacks) is missing.
- Background workers (db_worker, scheduler) are untested against live DB and event loops.
