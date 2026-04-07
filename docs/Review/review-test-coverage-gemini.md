# Context Broker Test Coverage Report

## Phase 1 & 2 Synthesis: Capability Coverage Matrix

This report cross-references every identified capability, operation, tool, and infrastructure interaction within the Context Broker source code against the existing test suite (`tests/` and `tests/integration/`).

### Coverage Status Definitions
*   **REAL:** Covered by tests that run against actual infrastructure (no patches/mocks).
*   **MOCK:** Covered by tests, but critical infrastructure dependencies (DB, HTTP, LLM, Filesystem) are mocked or patched.
*   **NONE:** No dedicated tests exist for this capability.

---

### 1. Core Infrastructure & Configuration

| Capability | Infrastructure Touched | Potential Failure Paths | Status | Justification / Tests |
| :--- | :--- | :--- | :--- | :--- |
| **Config Loading & Merging** (`app/config.py`) | Filesystem | Missing files, malformed YAML, permission denied. | **MOCK** | `test_config.py` patches file reads to return string data. (Implicitly verified by REAL integration tests). |
| **Credentials & API Keys** (`app/config.py`) | Filesystem (.env) | Missing .env, missing keys, read errors. | **MOCK** | `test_config.py` mocks env var resolution. |
| **Database Connection Pool** (`app/database.py`) | PostgreSQL, Neo4j | Connection timeout, invalid credentials, pool exhaustion. | **REAL** | `test_e2e_health.py` validates actual connections to PG/Neo4j. |
| **Structured Logging** (`app/logging_setup.py`) | Stdout / Disk | Serialization errors (un-serializable types). | **REAL** | `test_static_checks.py`, `test_integration_container.py` verify JSON output on real processes. |
| **Metrics Registry** (`app/metrics_registry.py`) | HTTP | Exposition format errors, registry conflicts. | **REAL** | `test_e2e_health.py` calls the real Prometheus endpoint. |
| **Schema Migrations** (`app/migrations.py`) | PostgreSQL | SQL syntax errors, concurrent migration locks. | **NONE** | No specific test drives the sequential migrations. (Implicitly run in E2E, but not explicitly validated). |
| **Prompt Loader** (`app/prompt_loader.py`) | Filesystem | Missing template file, encoding errors. | **NONE** | No test explicitly exercises loading templates from disk. |
| **Token Budget & Scaling** (`app/token_budget.py`, `app/flows/build_types/...`) | HTTP (LLM API) | Provider API down, unrecognized model name. | **MOCK** | `test_token_budget.py` mocks the provider context length HTTP call. Tier scaling is tested in `test_tier_scaling.py` (REAL, pure logic). |

### 2. API Routing & Handlers

| Capability | Infrastructure Touched | Potential Failure Paths | Status | Justification / Tests |
| :--- | :--- | :--- | :--- | :--- |
| **Caller Identity** (`app/routes/caller_identity.py`)| DNS | DNS timeouts, unresolvable IPs. | **MOCK** | `test_caller_identity.py` mocks DNS resolution. |
| **Chat Completions Endpoint** (`app/routes/chat.py`) | HTTP | Validation errors, stream parsing errors. | **REAL** | `test_e2e_chat.py` interacts with the real FastAPI endpoint and validates SSE formats. |
| **MCP SSE & Tool Dispatch** (`app/routes/mcp.py`, `app/flows/tool_dispatch.py`) | HTTP | Stale session eviction failures, unknown tools. | **MOCK** | `test_e2e_mcp.py` patches certain flow calls or configurations. |

### 3. Background Workers & Schedulers

| Capability | Infrastructure Touched | Potential Failure Paths | Status | Justification / Tests |
| :--- | :--- | :--- | :--- | :--- |
| **DB Embedding & Extraction Poller** (`app/workers/db_worker.py`) | PostgreSQL, LLM API | Transaction deadlocks, advisory lock failures. | **REAL** | `test_e2e_pipeline.py` verifies the real background pipeline processes messages and generates embeddings. |
| **Built-in Scheduler Worker** (`app/workers/scheduler.py`) | PostgreSQL | Cron parsing errors, missed schedules. | **REAL** | `test_scheduler.py` tests cron logic (pure), but firing mechanism coverage is limited. |

### 4. Memory & Context Pipelines (Context Broker AE)

| Capability | Infrastructure Touched | Potential Failure Paths | Status | Justification / Tests |
| :--- | :--- | :--- | :--- | :--- |
| **Conversation CRUD Flow** (`conversation_ops_flow.py`) | PostgreSQL | Constraint violations, missing IDs. | **REAL** | `test_e2e_resilience.py`, `test_models.py`. |
| **Embed Pipeline** (`embed_pipeline.py`) | PostgreSQL, LLM API | LLM rate limits, embedding format changes. | **REAL** | `test_e2e_pipeline.py` executes full end-to-end embeddings on PostgreSQL. |
| **Memory Extraction Flow** (`memory_extraction.py`) | PostgreSQL, Mem0/Neo4j, LLM | Lock contention, Mem0 timeouts, LLM extraction failures. | **MOCK** | `test_memory_extraction.py` mocks the Mem0/LLM calls. |
| **Memory Scoring & Decay** (`memory_scoring.py`) | None | Math errors (zero half-life). | **REAL** | `test_memory_scoring.py` tests pure math/filtering functions. |
| **Message Ingestion Pipeline** (`message_pipeline.py`) | PostgreSQL | Insert failures, counter increment issues. | **REAL** | `test_e2e_pipeline.py` stores messages in the real DB. |
| **Hybrid Search Flow** (`search_flow.py`) | PostgreSQL, LLM API | Vector ANN errors, BM25 issues, Reranker API failure. | **MOCK** | Covered by `test_e2e_mcp.py` which employs patching. |
| **Mem0 Client Init** (`memory/mem0_client.py`) | Neo4j | Bad credentials, unreachable graph DB. | **MOCK** | `test_mem0_client.py` uses mock patches. |
| **Admin Flow (Add/List/Del)** (`memory_admin_flow.py`)| Neo4j | API failures. | **MOCK** | Covered by `test_e2e_mcp.py`. |

### 5. Imperator Agent & Tools (Context Broker TE)

| Capability | Infrastructure Touched | Potential Failure Paths | Status | Justification / Tests |
| :--- | :--- | :--- | :--- | :--- |
| **Imperator ReAct Agent** (`imperator_flow.py`) | LLM API, PostgreSQL | Max iterations reached, malformed tool calls. | **REAL** | `integration/run_imperator_conversation.py` runs the real agent. |
| **Admin Tools** (`tools/admin.py`) | PostgreSQL, Filesystem | Config write errors, DB read-only violations. | **MOCK** | `test_change_inference.py` patches file IO. |
| **Alerting Tools** (`tools/alerting.py`) | PostgreSQL, LLM API | DB constraint errors, API failures. | **MOCK** | `test_new_tools.py` patches dependencies. |
| **Diagnostic Tools** (`tools/diagnostic.py`) | PostgreSQL | Query syntax errors. | **REAL** | `test_log_endpoints.py` tests against real log tables. |
| **Filesystem Tools** (`tools/filesystem.py`) | Filesystem | Path traversal, permission denied. | **MOCK** | `test_new_tools.py` mocks the filesystem. |
| **Notify Tools** (`tools/notify.py`) | HTTP | Webhook delivery timeouts. | **MOCK** | `test_new_tools.py` patches requests. |
| **Operational Tools** (`tools/operational.py`) | PostgreSQL, Neo4j | Failed writes to domain knowledge. | **NONE** | No specific tool test file exists. Only gating logic is tested in `test_tool_organization.py`. |
| **Scheduling Tools** (`tools/scheduling.py`) | PostgreSQL | Missing schedule IDs. | **NONE** | No tool implementation test exists, only cron logic in `test_scheduler.py`. |
| **System Tools** (`tools/system.py`) | Subprocess | Non-allowlisted commands, timeouts. | **MOCK** | `test_new_tools.py` patches subprocess calls. |
| **Web Tools** (`tools/web.py`) | HTTP | DuckDuckGo blocking, target site unreachable. | **MOCK** | `test_new_tools.py` patches HTTP responses. |

### 6. Alerter Sidecar

| Capability | Infrastructure Touched | Potential Failure Paths | Status | Justification / Tests |
| :--- | :--- | :--- | :--- | :--- |
| **Webhook Relay & LLM Format** (`alerter/alerter.py`) | HTTP, PostgreSQL, LLM | Channel API rate limits, semantic search failure, invalid payload. | **NONE** | There are absolutely no tests for the standalone Alerter Python service. |

---

### Executive Summary

1. **High Real Coverage on Core Data Paths:** End-to-end tests effectively validate the actual database structures, message storage, and health checks without mocking.
2. **Heavy Mocking on Tools & Extractions:** The vast majority of complex external integrations (Mem0, File system operations, Web Search, Token budget provider queries) are tested using mocks, leaving a gap in proving they work against the live APIs outside of broad integration exercises.
3. **Critical Missing Coverage (NONE):**
    - The `alerter/alerter.py` sidecar is completely untested.
    - Explicit unit testing for the `migrations.py` upgrade/downgrade sequences is missing.
    - Implementation tests for **Operational Tools** and **Scheduling Tools** do not exist.
    - `prompt_loader.py` lacks dedicated testing for filesystem edge cases.
