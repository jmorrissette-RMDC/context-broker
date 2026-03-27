# GPT-5.4 Independent Test Coverage Audit

## Status legend
- **REAL** — exercise touches live infrastructure (HTTP/MCP requests, SSH, Postgres/Redis/Neo4j, actual LLM endpoints, docker logs). These are integration/end-to-end scripts under 	ests/test_e2e_*.py, 	ests/integration/*, 	ests/test_integration_container.py, and 	ests/test_components.py.
- **MOCK** — pure-Python unit tests that run in-memory logic, often using AsyncMock/MagicMock or examining source files. Examples include 	ests/test_* outside the e2e/integration folders and 	ests/test_new_tools.py.
- **NONE** — no automated coverage discovered for the capability.

## 1. Configuration, startup and registry plumbing
- **Dynamic AE/TE config loading + caching + credentials + API-key resolution**
  - Does: reads /config/config.yml and /config/te.yml, caches by mtime/hash, flushes LLM/embedding clients when the file content changes, and resolves API keys from /config/credentials/.env (hot reload) or env vars.
  - Infra: filesystem config files, os.environ, LangChain client factories (langchain_openai, OpenAIEmbeddings).
  - Failure modes: missing files, malformed YAML, invalid build-type declarations, missing embedding_dims, stale caches preventing new keys from being picked up.
  - Coverage: **MOCK** (	ests/test_config.py, 	ests/test_token_budget.py).

- **Structured JSON logging + log-level update**
  - Does: installs JsonFormatter, suppresses health-check noise, and lets pp/main adjust the log level at startup.
  - Infra: stdout, Python logging.
  - Failure modes: malformed formatter, logging misconfiguration breaking observability.
  - Coverage: **MOCK** (	ests/test_static_checks.py).

- **Prompt/template loader**
  - Does: caches /config/prompts/*.md templates with mtime checks and exposes async wrappers.
  - Infra: host filesystem.
  - Failure modes: missing files cause runtime failures when TE tries to load prompts (chat/system prompts, chunk/archival prompts).
  - Coverage: **NONE** (no test imports load_prompt/sync_load_prompt).

- **StateGraph registry and install_stategraph tool**
  - Does: discovers AE/TE packages via importlib.metadata, registers build types/flows, and exposes install_stategraph for pip install + rescanning.
  - Infra: Python entry points, Postgres for recording installs.
  - Failure modes: missing entry points, pip failures, stale caches not cleared.
  - Coverage: **MOCK** for the registry (	ests/test_build_type_registry.py, 	ests/test_static_checks.py), **REAL** for the runtime installer (	ests/integration/test_components.py Group A).

- **Imperator state manager (persistent conversation file /data/imperator_state.json)**
  - Does: reads/writes the state file, verifies the conversation exists in Postgres, recreates it when missing.
  - Infra: filesystem /data, Postgres.
  - Failure modes: state file corrupted, Postgres unavailable, deleted conversations.
  - Coverage: **NONE** (no test hits ImperatorStateManager).

- **Lifespan bootstrap (migrations + Postgres retry + worker kickoff + TE initialization + retry loop + domain knowledge seeding)**
  - Does: runs migrations under advisory lock, marks Postgres availability, starts DB-driven workers, seeds Imperator/domain knowledge, and spins up retry loops.
  - Infra: asyncpg/Postgres migrations, context_broker_te.seed_knowledge, background workers, Domain Mem0 embeddings.
  - Failure modes: migration failure, Postgres downtime, domain LLM errors.
  - Coverage: **NONE**.

## 2. HTTP routes and middleware
- **OpenAI-compatible chat completions (streaming + non-streaming, SSE chunking)**
  - Does: validates ChatCompletionRequest, resolves caller identity, optionally reads context-window headers, routes to the Imperator flow, and streams SSE events when requested.
  - Infra: FastAPI, SSE, config files, Imperator flow.
  - Failure modes: invalid JSON, missing user messages, Imperator errors, SSE not ending with [DONE].
  - Coverage: **REAL** (	ests/test_e2e_chat.py).

- **Health endpoint (Postgres + Neo4j probe)**
  - Does: loads config, runs the health_check flow which checks Postgres/Neo4j, returns 200/503 with service statuses.
  - Infra: Postgres, Neo4j HTTP API.
  - Failure modes: dependency failures, config load failure, degraded responses.
  - Coverage: **REAL** (	ests/test_e2e_health.py, 	ests/test_components.py Group B).

- **Metrics endpoint (Prometheus exposition)**
  - Does: collects Prometheus metrics inside a flow and exposes text format on /metrics.
  - Infra: Prometheus client registry.
  - Failure modes: flow errors returning 500, empty output.
  - Coverage: **REAL** (	ests/test_e2e_health.py, 	ests/test_components.py D6).

- **MCP transport + SSE sessions**
  - Does: implements JSON-RPC with initialize, 	ools/list, 	ools/call, session queueing, and SSE keepalives.
  - Infra: FastAPI, asyncio queues, Postgres (via tool execution), background metrics.
  - Failure modes: malformed requests, unknown methods, queue saturation.
  - Coverage: **REAL** (	ests/test_e2e_mcp.py, 	ests/integration/test_components.py Group A). **Gap:** SSE session handshake itself has no targeted test (**NONE**).

- **Caller identity resolution**
  - Does: accepts explicit user, reverse-DNS lookups, caches results, falls back to IP/unknown.
  - Infra: FastAPI request metadata, socket.gethostbyaddr.
  - Failure modes: DNS failures, missing client info.
  - Coverage: **MOCK** (	ests/test_caller_identity.py).

- **Postgres availability middleware + exception handlers**
  - Does: returns 503 when Postgres is down and formats validation/runtime errors consistently.
  - Infra: FastAPI middleware.
  - Failure modes: Postgres unavailable, unhandled exceptions leaking stack traces.
  - Coverage: **NONE**.

## 3. AE flows & core tools
- **Message pipeline + dedup/collapse logic**
  - Does: resolves context/conversation IDs, uses advisory locks, collapses duplicates, inserts messages with sequence numbers and token counts.
  - Infra: Postgres, advisory locks.
  - Failure modes: Unique sequence conflicts, missing context windows, DB errors.
  - Coverage: **REAL** (	ests/test_e2e_pipeline.py, 	ests/test_e2e_mcp.py, 	ests/test_e2e_resilience.py).

- **Embedding pipeline**
  - Does: polls for null embeddings, batches content, invokes the configured embedding model, writes vector strings, updates metrics.
  - Infra: Postgres, embeddings LLM, metrics registry.
  - Failure modes: LLM timeouts, zero-vector fallback, DB update failures.
  - Coverage: **REAL** (	ests/test_e2e_pipeline.py, 	ests/integration/bulk_load.py).

- **Memory extraction flow (lock ? Mem0 ? mark)**
  - Does: locks per conversation, builds sanitized extraction text, calls Mem0, marks memory_extracted, releases locks.
  - Infra: Postgres, Mem0/Neo4j, LLM text cleaning.
  - Failure modes: lock contention, Mem0 failures, secret leakage.
  - Coverage: **MOCK** (	ests/test_memory_extraction.py).

- **Memory search / mem_get_context**
  - Does: queries Mem0, applies half-life decay scoring, formats context injection text.
  - Infra: Mem0 (Neo4j/PGVector), embeddings.
  - Failure modes: Mem0 unavailable, scoring misconfig.
  - Coverage: **REAL** (	ests/test_e2e_mcp.py, 	ests/test_components.py Group D). 	ests/test_memory_scoring.py ensures scoring math (MOCK).

- **Conversation hub + get_context (build types)**
  - Does: auto-creates conversations/windows, snaps budgets, calls build-type retrieval graphs (passthrough/standard/knowledge-enriched), returns structured tiers.
  - Infra: Postgres, build-type registry, LLM summarization.
  - Failure modes: invalid build type, missing windows, creation race conditions.
  - Coverage: **REAL** (	ests/test_e2e_mcp.py, 	ests/integration/test_components.py, 	ests/integration/test_cross-provider.py). Standard-tiered logic gets **MOCK** support from 	ests/test_tier_scaling.py; **Knowledge-enriched retrieval specifically lacks a direct test of its semantic/KG tiers (**NONE**).**

- **Search flows (conversation/message + log search validation)**
  - Does: hybrid vector/BM25 search, reranking, filter sanitization, log query/search inputs.
  - Infra: Postgres, embeddings, optional reranker API, log tables.
  - Failure modes: invalid dates, missing embeddings, reranker downtime.
  - Coverage: **REAL** for conversation/message search (	ests/test_e2e_mcp.py, 	ests/test_components.py). 	ests/test_log_endpoints.py covers request validation (MOCK); vectorized search_logs is **NONE**.

- **Mem admin tools (mem_add, mem_list, mem_delete)**
  - Does: wrap Mem0 operations for manual memory CRUD.
  - Infra: Mem0/Neo4j.
  - Failure modes: Mem0 unavailability, degraded mode.
  - Coverage: **NONE**.

## 4. Background jobs & scheduler
- **Embedding/extraction/assembly/log worker loops**
  - Does: poll DB for work, invoke embeddings/Mem0/readiness, update assembly status.
  - Infra: Postgres, embeddings, Mem0, metrics.
  - Failure modes: queue starvation, Mem0 errors.
  - Coverage: **Partial** — embeddings/extraction get **REAL** coverage from 	ests/test_e2e_pipeline.py and 	ests/integration/bulk_load.py; worker helpers are unit-tested (	ests/test_memory_extraction.py), but the orchestrated loops are **NONE**.

- **Scheduler worker & cron parsing**
  - Does: polls schedules, claims runs atomically, updates history, dispatches targets.
  - Infra: Postgres, MCP tool dispatch, cron expressions.
  - Failure modes: double-firing, contention.
  - Coverage: **MOCK** (	ests/test_scheduler.py); actual worker loop is **NONE**.

## 5. TE Imperator + tool ecosystem
- **Imperator ReAct agent + persistence**
  - Does: binds tools to LLM, loads system prompt/history, stores user/assistant messages, enforces max iterations.
  - Infra: LangGraph/MemorySaver, LangChain tools, Postgres, config.
  - Failure modes: empty LLM response, tool loops.
  - Coverage: **REAL** (	ests/test_e2e_chat.py, 	ests/integration/run_imperator_conversation.py, 	ests/integration/run_tool_exercises.py, 	ests/integration/test_components.py Groups D/E/F).

- **Admin/diagnostic/scheduling/operational tools**
  - Does: manage config, run SQL, introspect context/pipeline, schedule creation, domain knowledge gating.
  - Infra: filesystem, Postgres, Redis, HTTP.
  - Failure modes: bad YAML writes, SQL errors, Mem0 failures.
  - Coverage: **MOCK** (	ests/test_change_inference.py, 	ests/test_migration_tool.py, 	ests/test_new_tools.py, 	ests/test_tool_organization.py); **REAL** via tool exercises integration scripts.

- **Web/filesystem/system/notify/alerting tools**
  - Does: search/read web pages, sandboxed FS ops, allow-listed commands, notifications, alert instruction CRUD.
  - Infra: HTTP, filesystem, SMTP/Twilio.
  - Failure modes: sandbox escapes, missing dependencies, webhook failures.
  - Coverage: **MOCK** only (	ests/test_new_tools.py, 	ests/test_alerting). Alerter service remains **NONE**.

- **Domain knowledge (Mem0 + seeds)**
  - Does: store/search domain facts, extract to Neo4j, seed initial articles.
  - Infra: Postgres domain_information, Mem0, embeddings.
  - Failure modes: missing embedding_dims, Mem0 errors.
  - Coverage: **MOCK** (	ests/test_domain_mem0.py, 	ests/test_tool_organization.py); **NONE** for actual tool runs and seeding.

## 6. Observability & container posture
- **Static architecture checks (pinning, user directives, network topology, secrets)**
  - Coverage: **MOCK** (	ests/test_static_checks.py).

- **Container runtime properties (non-root, mounts, logging)**
  - Coverage: **REAL** (	ests/test_integration_container.py).

- **Integration resilience + pipeline scripts**
  - Coverage: **REAL** (	ests/test_e2e_pipeline.py, 	ests/test_e2e_resilience.py, 	ests/integration/test_cross_provider.py, 	ests/integration/bulk_load.py, 	ests/integration/reset_databases.py, 	ests/integration/run_all.py, 	ests/integration/analyze_performance.py, 	ests/integration/run_imperator_conversation.py, 	ests/integration/run_tool_exercises.py, 	ests/integration/test_components.py).

## 7. Remaining coverage gaps (NONE)
1. Prompt loader (pp.prompt_loader).
2. Imperator state manager persistence file.
3. Domain knowledge seeding (context_broker_te.seed_knowledge).
4. Mem admin tools (mem_add, mem_list, mem_delete).
5. Vectorized log search (search_logs).
6. Alerter webhook/instruction relay (lerter/alerter.py).
7. Scheduler loop / history writes.
8. Postgres availability middleware + exception handlers. 

---
*Generated by GPT-5.4 test-coverage audit. No other files were modified.*
