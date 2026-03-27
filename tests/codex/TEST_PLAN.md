# Codex Test Plan (Automated, Real Infrastructure)

## Scope
This plan adds an end-to-end, real-infrastructure test suite under `tests/codex/`.
It focuses on live HTTP endpoints, database schema integrity, and MCP tool flows
that rely on Postgres, Mem0, Neo4j, and LLM/embeddings providers.

## Prerequisites
- Context Broker running and reachable at `CB_BASE_URL` (default: http://localhost:8080)
- Postgres reachable via `POSTGRES_HOST/PORT/DB/USER/PASSWORD`
- Mem0 + Neo4j reachable and configured in `config.yml`/`te.yml`
- Embeddings/LLM endpoints reachable (for Mem0 and domain info tools)
- Docker host reachable via SSH for internal-only services (default: `aristotle9@192.168.1.110`)
- Test data defaults to `Z:\test-data\conversational-memory` if present.
- Optional override via `TEST_DATA_DIR` or `tests/integration/generate_test_data.py`

## Environment Variables
- `CB_BASE_URL` (default: http://localhost:8080)
- `CB_MCP_URL` (default: {CB_BASE_URL}/mcp)
- `CB_CHAT_URL` (default: {CB_BASE_URL}/v1/chat/completions)
- `CB_HEALTH_URL` (default: {CB_BASE_URL}/health)
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- `ALERTER_BASE_URL` (optional)
- `CB_DOCKER_SSH` (optional, defaults to `aristotle9@192.168.1.110`)
- `TEST_DATA_DIR` (optional, defaults to `Z:\test-data\conversational-memory` if available)

## Test Categories and Mapping

### 1) Database Schema and Migrations (integration)
- Verify required tables exist.
- Verify schema migrations applied (version >= 20).
- Verify required columns exist on critical tables.
- File: `tests/codex/integration/test_schema_and_migrations.py`

### 2) MCP Protocol + Core Flow (integration)
- `initialize` and `tools/list` protocol handling.
- `get_context` -> `store_message` -> `search_messages` round trip.
- Conversation lifecycle: create conversation, context window, and history retrieval.
- File: `tests/codex/integration/test_mcp_core_flow.py`

### 3) Memory + Knowledge (integration)
- `mem_add` and `mem_search` through Mem0.
- `mem_get_context` and `search_knowledge` for knowledge retrieval.
- Domain info storage and semantic search.
- File: `tests/codex/integration/test_memory_and_domain_tools.py`

### 4) Imperator Internal Tools (integration)
- Exercise diagnostic, scheduling, web, filesystem, system, alerting, notify, and domain info tools
  via the Imperator chat endpoint.
- File: `tests/codex/integration/test_imperator_internal_tools.py`

### 5) Chat Completions (integration)
- /v1/chat/completions real Imperator flow.
- File: `tests/codex/integration/test_chat_endpoint.py`

### 6) Metrics + Health (integration)
- HTTP `/health` and `/metrics` endpoints.
- MCP `metrics_get` tool.
- File: `tests/codex/integration/test_metrics_and_health.py`

### 7) Log Tools (integration)
- query_logs and search_logs.
- File: `tests/codex/integration/test_log_tools.py`

### 8) Alerter Service (integration, optional)
- `/health` and `/webhook` on the alerter service.
- File: `tests/codex/integration/test_alerter_service.py`

### 9) Internal Services via Docker (integration)
- Direct checks for alerter, infinity, neo4j, and log shipper pipeline via Docker network.
- File: `tests/codex/integration/test_internal_services_via_docker.py`

### 10) Live Data Load (integration)
- Load a small sample from the phase1 dataset and verify store/search.
- File: `tests/codex/integration/test_live_data_sample.py`

### 11) Unit Coverage (unit)
- Token budget snap logic.
- Token budget resolver fallback behavior.
- File: `tests/codex/unit/test_budget_and_token_utils.py`

## Execution
Run all Codex tests:
- `pytest tests/codex -m "integration or unit"`

Run only integration tests:
- `pytest tests/codex/integration`

Run only unit tests:
- `pytest tests/codex/unit`

## Data Management
- Tests use unique IDs and non-production test content.
- DB writes are minimal and scoped to new rows (no destructive deletes).
- If you need seeded datasets, use `tests/integration/generate_test_data.py` and set `TEST_DATA_DIR`.
