# Context Broker Test Coverage Report
**Auditor:** Gemini 3.1 Pro
**Methodology:** 
1. **Phase 1:** Analyzed all source code in `app/`, `packages/`, and `alerter/` to build a complete inventory of capabilities, behaviors, and infrastructure touchpoints.
2. **Phase 2:** Parsed and evaluated all files in `tests/` and `tests/integration/` to map coverage and identify the presence of mocks/patches for infrastructure boundaries (DB, HTTP, FileSystem, LLM APIs).
3. **Synthesis:** Cross-referenced capabilities to assess true coverage status (REAL vs MOCK vs NONE).

---

## 1. Core Framework & Infrastructure (`app/`)

| Capability / Module | Infrastructure Touched | Status | Notes |
| :--- | :--- | :--- | :--- |
| **`app.budget`** (Token budget logic) | None (Pure Logic) | REAL | Unit tests in `test_budget.py` cover all calculation paths. |
| **`app.config`** (Config & API key loading) | FileSystem, Environment | MOCK | `test_config.py` patches file reads to simulate YAML loading. |
| **`app.database`** (Postgres/Neo4j checks) | PostgreSQL, Neo4j | REAL | End-to-end tests (`test_e2e_health.py`) hit real databases. |
| **`app.logging_setup`** (JSON formatting) | None (Pure Logic) | REAL | Static checks validate JSON format and structure. |
| **`app.main`** (ASGI app, exception handlers) | HTTP (FastAPI) | REAL | Covered extensively by E2E chat and health check requests. |
| **`app.metrics_registry`** (Prometheus) | HTTP | REAL | Validated against the real `/metrics` endpoint in E2E tests. |
| **`app.migrations`** (Schema DB migrations) | PostgreSQL | MOCK | Tested via `test_migration_tool.py` using patched execution. |
| **`app.models`** (Pydantic validation) | None (Pure Logic) | REAL | Extensive unit tests in `test_models.py` without external mocks. |
| **`app.prompt_loader`** (Loads prompts) | FileSystem | NONE | No tests found covering prompt loading from disk. |
| **`app.stategraph_registry`** (Dynamic registry) | Python `entry_points` | MOCK | `test_build_type_registry.py` mocks module imports. |
| **`app.token_budget`** (Context length resolution) | HTTP (LLM APIs) | MOCK | `test_token_budget.py` mocks provider API endpoints. |
| **`app.utils`** (`stable_lock_id`) | None (Pure Logic) | NONE | No unit tests found for general utility functions. |
| **`app.flows.build_type_registry`** | None (In-memory) | MOCK | Registry logic is tested with mocked graph outputs. |
| **`app.flows.tool_dispatch`** (MCP Routing) | None (StateGraph) | REAL | Tested directly via `test_components.py` against live instance. |
| **`app.flows.install_stategraph`** (PIP installs) | FileSystem, Subprocess | NONE | No tests verify dynamic runtime package installation. |
| **`app.imperator.state_manager`** (ReAct persistence) | PostgreSQL | REAL | `test_e2e_resilience.py` verifies state survives restart. |
| **`app.routes.caller_identity`** (DNS resolution) | Network (DNS) | MOCK | `test_caller_identity.py` mocks `socket.gethostbyaddr`. |
| **`app.routes.chat`** (OpenAI completions) | HTTP (LLM) | REAL | `test_e2e_chat.py` sends live HTTP requests. |
| **`app.routes.health`** (Health endpoint) | HTTP, DB | REAL | `test_e2e_health.py` hits real infrastructure. |
| **`app.routes.mcp`** (MCP tools API) | HTTP | REAL | Integration tests (`test_components.py`) hit the API. |
| **`app.workers.db_worker`** (Background polling) | PostgreSQL, LLM | REAL | `test_e2e_pipeline.py` inserts data and waits for real worker loop. |
| **`app.workers.scheduler`** (Cron parsing) | None (Pure Logic) | REAL | `test_scheduler.py` tests cron math natively. |

---

## 2. Application Engine Flows (`packages/context-broker-ae/`)

| Capability / Module | Infrastructure Touched | Status | Notes |
| :--- | :--- | :--- | :--- |
| **`build_types.tier_scaling`** (Token distrib.) | None (Pure Logic) | REAL | Full logic coverage in `test_tier_scaling.py`. |
| **`conversation_ops_flow`** (CRUD operations) | PostgreSQL | REAL | Verified in integration testing against live DB. |
| **`embed_pipeline`** (Vector generation) | PostgreSQL, LLM | REAL | `test_e2e_pipeline.py` verifies vector storage occurs natively. |
| **`health_flow`** (Status aggregation) | PostgreSQL, Neo4j | REAL | Tested live via `/health`. |
| **`memory_admin_flow`** (MemAdd, MemList) | Mem0 (Neo4j) | REAL | Handled dynamically in `test_components.py`. |
| **`memory_extraction`** (LLM distillation) | Mem0, LLM | REAL | Mocks exist (`test_memory_extraction.py`), but integration test hits real LLM. |
| **`memory_scoring`** (Half-life math) | None (Pure Logic) | REAL | Thoroughly unit tested without mocks (`test_memory_scoring.py`). |
| **`memory_search_flow`** (Knowledge retrieval) | Neo4j (Mem0) | MOCK | `test_e2e_mcp.py` mocks the core search functions. |
| **`message_pipeline`** (Message saving) | PostgreSQL | REAL | Verified by e2e pipeline data persistence. |
| **`metrics_flow`** (Prometheus aggregation) | HTTP | REAL | Live `/metrics` test validates data flow. |
| **`search_flow`** (Hybrid conversation search) | PostgreSQL Vector | MOCK | Unit tests patch semantic/hybrid search methods. |
| **`memory.mem0_client`** (Mem0 initialization) | Neo4j | MOCK | `test_mem0_client.py` mocks out Neo4j connections. |

---

## 3. Tool Engine & Cognitive Agent (`packages/context-broker-te/`)

| Capability / Module | Infrastructure Touched | Status | Notes |
| :--- | :--- | :--- | :--- |
| **`domain_mem0`** (Agent specific knowledge) | Neo4j | MOCK | Tested with mocks (`test_domain_mem0.py`). |
| **`imperator_flow`** (ReAct reasoning loop) | LLM, PostgreSQL | REAL | Fully tested in `test_e2e_chat.py` and `test_components.py`. |
| **`seed_knowledge`** (Operational DB seeder) | PostgreSQL | NONE | No test coverage for the seeding script. |
| **`tools.admin`** (Config write, Inference toggle) | FileSystem, HTTP | MOCK | `test_change_inference.py` mocks external API checks. |
| **`tools.alerting`** (Instruction management) | PostgreSQL | MOCK | DB interactions are mocked in `test_new_tools.py`. |
| **`tools.diagnostic`** (System introspection) | Database | NONE | Configuration gating is tested, but execution logic is not. |
| **`tools.filesystem`** (File Sandbox) | FileSystem | MOCK | `test_new_tools.py` patches actual disk reads/writes. |
| **`tools.notify`** (Webhook dispatching) | HTTP | MOCK | Outbound webhooks are mocked in `test_new_tools.py`. |
| **`tools.operational`** (Learning facts) | Database | NONE | Only gated via config; no execution test. |
| **`tools.scheduling`** (Cron tool control) | Database | NONE | Only gating tested; logic untested. |
| **`tools.system`** (Subprocess execution) | Subprocess (OS) | MOCK | `test_new_tools.py` mocks subprocess output. |
| **`tools.web`** (DuckDuckGo searching) | HTTP | MOCK | External HTTP requests to DDG are mocked. |

---

## 4. Webhook Alerter Sidecar (`alerter/`)

| Capability / Module | Infrastructure Touched | Status | Notes |
| :--- | :--- | :--- | :--- |
| **`alerter.py`** (Webhook Relay & Dispatching) | HTTP (Slack, Discord, Ntfy, Twilio, SMTP), DB | NONE | The entire `alerter.py` module has zero native tests. It maps semantic events and sends real messages across 6+ providers, but no tests verify connection handling, payload formatting, or retry logic. |

---

## Coverage Summary & Conclusion

- **REAL Coverage is Strongest in Core Pipelines:** Essential operations (ASGI application, Postgres migrations/pools, pure data validation models, token budget calculations, and background queue workers) are rigorously tested against live dependencies using the `test_e2e_*` and `test_components.py` suites.
- **MOCK Reliance in Tools and Infrastructure Initializers:** The TE (`context-broker-te`) relies heavily on `pytest.patch`. Most MCP tools (filesystem access, system commands, duckduckgo search, alerting) execute entirely against mock APIs rather than isolated sandbox environments.
- **Critical Omissions (NONE):** 
  - Dynamic graph installation (`app.flows.install_stategraph`).
  - Diagnostic, Operational, and Scheduling tools lack dedicated execution tests.
  - The `alerter.py` sidecar is completely untested. Any errors in the Twilio, Slack, or SMTP formatting payloads will only be discovered in production.