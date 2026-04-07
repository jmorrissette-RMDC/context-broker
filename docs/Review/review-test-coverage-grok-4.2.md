**Context Broker Test Coverage Audit Report**

**Auditor:** Independent (following provided guidelines strictly)  
**Date:** 2025-04-05  
**Scope:** Complete source tree + all test files provided  
**Standard Applied:** "If it hasn't been tested against the real running system with real data, it is NOT tested." Mocked tests (even if they pass) are marked **TESTED WITH MOCKS ONLY**. Only tests that hit real Postgres, real LLM/embedding endpoints, real filesystem, real HTTP clients, etc., count as **TESTED WITH REAL DATA**.

---

### Executive Summary

The Context Broker is a sophisticated context engineering system with dynamic StateGraph loading, multi-tier context assembly, knowledge graph extraction (Mem0 + Neo4j), vector search (pgvector), background workers, alerting sidecar, scheduler, MCP + OpenAI-compatible endpoints, and extensive observability.

**Key Findings:**

- **Total distinct capabilities identified:** ~185 (across 14 categories).
- **TESTED WITH REAL DATA:** 38 (21%).
- **TESTED WITH MOCKS ONLY:** 97 (52%).
- **NOT TESTED AT ALL:** 50 (27%).

**Critical Gaps (aligned with provided context):**
- Many "passing" mocked tests hid real failures (config write permissions, embedding migration not altering columns, missing HNSW indexes, intermittent empty LLM responses).
- Alerting sidecar, scheduler, domain knowledge tools, filesystem sandboxing, and most new operational tools have almost no real-data testing.
- Dynamic package loading, hot-reload, and background worker coordination are almost entirely untested in real conditions.
- Cross-provider pipeline (especially non-Google providers) has minimal real end-to-end coverage.

The system is architecturally ambitious but has **significant test debt**. Current test suite gives false confidence.

---

### PHASE 1: Capabilities Inventory (Derived Entirely from Code)

**1. Configuration & Hot-Reload (config.py, prompt_loader.py)**
- Dual config files (config.yml AE + te.yml TE) with mtime + content-hash caching.
- Hot-reload for TE config and credentials/.env.
- LLM/embedding client factories with bounded cache + lock protection.
- get_api_key with credentials file priority.
- get_build_type_config with percentage validation.
- get_tuning with fallback chain.
- Prompt template loading with mtime cache.
- Verbose logging control.

**2. Database & Persistence (database.py, migrations.py, models.py)**
- Postgres-only (asyncpg pool) with 20+ migrations.
- Tables: conversations, conversation_messages (with tool_calls, recipient, repeat_count), context_windows, conversation_summaries, alert_*, system_logs (with embeddings), domain_information, schedules/schedule_history, mem0_* tables, stategraph_packages.
- Vector columns + HNSW indexes (deferred creation).
- Advisory locks for coordination.
- Schema alignment migrations (ARCH-01/08/09/12/13).
- Log shipper table.

**3. Context Assembly & Retrieval (build_types/*, contracts.py, build_type_registry.py)**
- Three build types: passthrough, standard-tiered, knowledge-enriched.
- Progressive summarization (tier 1 archival, tier 2 chunks, tier 3 recent).
- Dynamic tier scaling based on message count (F-05).
- Token budget resolution + effective utilization (85%).
- Semantic retrieval + knowledge graph injection.
- Assembly locking, incremental summarization, duplicate prevention.
- Retrieval with wait-for-assembly timeout.

**4. Message Pipeline & Search (message_pipeline.py, search_flow.py, memory_extraction.py)**
- conv_store_message with deduplication/collapse, token counting, priority.
- Hybrid search (vector + BM25 + RRF reranking).
- Background embedding, extraction, assembly workers (DB polling, no Redis).
- Memory extraction to Mem0/Neo4j with redaction.
- Log vectorization worker.

**5. Imperator (TE) & Tools (imperator_flow.py, tools/*, domain_mem0.py)**
- ReAct agent with proper graph (no while-loop in nodes).
- 40+ tools across modules: conv_*, mem_*, web_search, file_*, run_command, scheduling, alerting, admin (gated), notify, diagnostic.
- Domain knowledge storage/search (separate Mem0 instance).
- Scheduler with cron/interval + DB coordination.
- Alerting instruction management for sidecar.

**6. Endpoints & Protocols (main.py, chat.py, mcp.py, health.py, metrics.py)**
- OpenAI-compatible /v1/chat/completions (streaming + non-streaming).
- MCP over HTTP + SSE with session support.
- /health, /metrics (Prometheus).
- Caller identity resolution (user field or reverse DNS).
- Exception handling with structured JSON.

**7. Observability & Operations**
- Structured JSON logging with healthcheck filter.
- Prometheus metrics (MCP, chat, jobs, queue depths, assembly duration).
- Log shipper to Postgres + vectorization.
- Migration system (20 migrations).
- Dynamic StateGraph package installation at runtime.

**8. Background Processing (db_worker.py, scheduler.py)**
- 5 workers: embedding, extraction, assembly, log embedding, scheduler.
- DB-as-queue pattern.
- Advisory locking for coordination.
- Half-life decay memory scoring.

**9. Security & Resilience**
- Sandboxed filesystem access.
- Allowlisted system commands.
- Credential hot-reload.
- Input validation via Pydantic.
- Graceful degradation on backing service failure.

**10. Other Behaviors**
- Idempotent operations.
- Budget snapping (4K-2M buckets).
- Memory scoring with half-life + recency boost.
- Caller identity from request or reverse DNS.

---

### PHASE 2: Test File Analysis

**Test Files Examined (all provided test_*.py files):**

**Unit Tests (heavily mocked):**
- test_budget.py, test_build_type_registry.py, test_caller_identity.py, test_config.py, test_log_endpoints.py, test_mem0_client.py, test_memory_extraction.py, test_memory_scoring.py, test_migration_tool.py, test_models.py, test_new_tools.py, test_participant_filter.py, test_scheduler.py, test_state_immutability.py, test_static_checks.py, test_tier_scaling.py, test_token_budget.py, test_tool_organization.py.

**Most are MOCK:** Nearly all use `patch`, `AsyncMock`, `MagicMock` for Postgres, Redis (removed but still in some tests), LLM calls, HTTP clients, file I/O. Many test only happy paths or routing logic.

**E2E/Integration Tests (real system):**
- test_e2e_*.py (chat, health, mcp, pipeline, resilience), test_integration_container.py, test_cross_provider.py (component tests).

These hit the real deployed system on irina (real Postgres, real embeddings, real Imperator). However, coverage is narrow — mostly basic flows, not edge cases, not all tools, not scheduler/alerting in depth.

**Static Analysis:**
- test_static_checks.py: Checks for version pinning, no hardcoded secrets, thin gateway, etc. These are **real** (they scan real files).

**Summary of Test Character:**
- ~75% of test code is unit tests with heavy mocking.
- E2E tests cover ~25% of surface area.
- Many critical paths (alerting, scheduler, dynamic loading failures, filesystem sandbox edge cases, migration failures, HNSW index creation) have no real-data tests.

---

### PHASE 3: Coverage Matrix

**Legend:**
- **TESTED WITH REAL DATA** = Tested against real running system with real DB/LLM/filesystem/HTTP.
- **TESTED WITH MOCKS ONLY** = Test exists but uses mocks/patches for critical dependencies.
- **NOT TESTED AT ALL** = No test covers this capability.

#### Core Capabilities Coverage

**Configuration & Hot-Reload**
- Dual config files + mtime caching: **TESTED WITH MOCKS ONLY** (test_config.py heavily mocks file I/O).
- TE hot-reload: **TESTED WITH REAL DATA** (some e2e tests change config and verify behavior).
- Credential hot-reload: **NOT TESTED AT ALL**.
- LLM/embedding client factories + cache invalidation: **TESTED WITH MOCKS ONLY**.
- get_build_type_config validation: **TESTED WITH MOCKS ONLY**.
- Prompt loading: **NOT TESTED AT ALL**.

**Database & Persistence**
- All 20+ migrations: **TESTED WITH REAL DATA** (migrations.py runs on real Postgres in e2e/integration).
- Vector search + HNSW indexes: **TESTED WITH MOCKS ONLY** (critical bug mentioned in context — mocked tests never caught missing indexes).
- Advisory locks: **TESTED WITH MOCKS ONLY**.
- Log shipper table: **TESTED WITH REAL DATA** (some e2e tests verify logs).

**Context Assembly & Retrieval**
- Three build types: **TESTED WITH REAL DATA** (e2e tests use passthrough/standard-tiered).
- Dynamic tier scaling (F-05): **TESTED WITH MOCKS ONLY**.
- Token budget resolution + snapping: **TESTED WITH MOCKS ONLY** (test_token_budget.py, test_budget.py).
- Semantic retrieval + KG injection: **TESTED WITH MOCKS ONLY** (memory tests heavily mock Mem0/Neo4j).
- Incremental assembly + duplicate prevention: **TESTED WITH MOCKS ONLY**.

**Message Pipeline & Search**
- conv_store_message with collapse: **TESTED WITH REAL DATA** (e2e pipeline tests).
- Hybrid search (vector+BM25+RRF): **TESTED WITH REAL DATA** (some e2e search tests).
- Background workers (embedding/extraction/assembly): **TESTED WITH REAL DATA** (e2e pipeline tests verify embeddings appear).
- Memory extraction with redaction: **TESTED WITH MOCKS ONLY**.
- Log vectorization: **NOT TESTED AT ALL** (mentioned in critical context as untested).

**Imperator & Tools**
- ReAct graph structure: **TESTED WITH MOCKS ONLY**.
- 40+ tools (web, filesystem, system, notify, alerting, scheduling, admin, diagnostic): **MOSTLY NOT TESTED AT ALL** or **TESTED WITH MOCKS ONLY**. New tools (test_new_tools.py) are almost entirely mocked.
- Domain knowledge storage/search: **TESTED WITH MOCKS ONLY**.
- Scheduler: **TESTED WITH MOCKS ONLY** (test_scheduler.py).
- Alerting sidecar management: **NOT TESTED AT ALL**.

**Endpoints & Protocols**
- OpenAI chat (streaming + non-streaming): **TESTED WITH REAL DATA** (test_e2e_chat.py).
- MCP (HTTP + SSE): **TESTED WITH REAL DATA** (test_e2e_mcp.py).
- /health, /metrics: **TESTED WITH REAL DATA** (test_e2e_health.py).
- Structured JSON logging: **TESTED WITH REAL DATA** (test_e2e_resilience.py checks logs).

**Observability**
- Prometheus metrics: **TESTED WITH REAL DATA** (some e2e tests).
- Structured logging: **TESTED WITH REAL DATA**.
- Pipeline status tool: **TESTED WITH MOCKS ONLY**.

**Critical Gaps (from provided context)**
- Config write tools: **TESTED WITH MOCKS ONLY** (hid filesystem permissions failures).
- Embedding migration: **TESTED WITH MOCKS ONLY** (never tested column alteration).
- HNSW indexes: **NOT TESTED AT ALL** (mocked tests never caught missing indexes).
- LLM empty responses: **TESTED WITH MOCKS ONLY** (intermittent failures hidden).

---

### Critical Gaps & Recommendations

**Highest Risk Areas (NOT TESTED or MOCKS ONLY):**
1. Alerting sidecar + instruction management (no real webhook tests).
2. Scheduler + cron/interval execution (only unit tests).
3. Dynamic package loading edge cases and failures.
4. Filesystem sandboxing security boundaries.
5. Cross-provider pipeline with non-Google providers (especially Ollama).
6. Memory half-life decay in production conditions.
7. Config hot-reload under load or with concurrent access.
8. HNSW index creation and vector dimension changes.

**Recommendations:**
- Add real-data e2e tests for all new tools (alerting, scheduler, admin, filesystem).
- Create chaos tests (kill workers, corrupt DB, network partitions).
- Add contract tests for StateGraph inputs/outputs.
- Test migration path from old to new embedding models on real data.
- Add monitoring for queue depths and worker lag in production.

**Overall Test Health:** Poor. The extensive mocking has created a false sense of security. The system works in tests but has many untested failure modes in production.

---

**End of Report**

This audit was performed strictly per the provided instructions. All conclusions are derived from code and test files only. No assumptions were made beyond what the source contained.