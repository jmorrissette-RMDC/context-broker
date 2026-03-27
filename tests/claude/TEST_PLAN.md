# Test Plan — Claude Test Suite

**Author:** Claude Opus 4.6
**Date:** 2026-03-27
**Scope:** 100% coverage of all 255 capabilities — mock unit tests + live integration tests

---

## Architecture

The suite has two layers:

1. **Mock tests** (315 tests) — unit tests with mocked dependencies. Cover error paths, edge cases, and logic that can't be triggered against live infrastructure. Run anywhere, no infra needed.

2. **Live tests** (123 tests) — integration tests against a **dedicated Docker Compose test stack** (`docker-compose.claude-test.yml`). Use the full Phase 1/2/3 datasets with the nominal inference model set. The test stack runs on port 8081 alongside the production stack with no conflicts.

**Total: 438 tests covering all 255 capabilities.**

## Running

```bash
# Mock tests only (fast, no infrastructure)
pytest tests/claude/ -m "not live" -v

# Full suite (deploys test stack, loads 10,430 messages, exercises everything)
pytest tests/claude/ -v

# Live tests only
pytest tests/claude/live/ -v -m "live"

# Specific phase
pytest tests/claude/live/test_phase_b_core_tools.py -v
```

## Isolated Test Infrastructure

The live tests deploy their own Docker Compose stack:

| Component | Production | Test Stack |
|-----------|-----------|------------|
| Network | `context-broker-net` | `claude-test-net` |
| Gateway | `context-broker` on port 8080 | `claude-test-broker` on port 8081 |
| Data volumes | `./data/` | `./data-test/` |
| Config | `./config/` | `./config-test/` (nominal models baked in) |
| Ollama | included | omitted (nominal uses cloud) |
| UI | included on port 7860 | omitted |

All internal container hostnames are identical (`context-broker-postgres`, `context-broker-langgraph`, etc.) — they resolve only within their own bridge network.

## Nominal Inference Config

The fastest cloud models, baked into `config-test/`:

| Slot | Model | Provider |
|------|-------|----------|
| Imperator | gemini-2.5-pro | Google |
| Summarization | gemini-2.5-flash-lite | Google |
| Extraction | gpt-4.1-mini | OpenAI |
| Embeddings | gemini-embedding-001 (1024 dims) | Google |
| Log embeddings | text-embedding-3-small (256 dims) | OpenAI |
| Reranker | mxbai-rerank-xsmall-v1 | Infinity (local) |

## Session Setup (automatic)

The `conftest_live.py` session fixture:
1. Deploys test stack: `docker compose -f docker-compose.claude-test.yml up -d --build`
2. Waits for `/health` 200 (max 180s)
3. Bulk loads all 10,430 Phase 1 messages via MCP `store_message`
4. Waits for pipeline completion (embeddings, extraction, assembly — max 3600s)
5. Runs all live tests
6. Generates `ISSUES.md` from `issues.json`
7. Tears down stack: `docker compose -f docker-compose.claude-test.yml down -v`

## Issues Log

Tests log unexpected-but-non-fatal findings to `tests/claude/issues.json` at runtime. A session finalizer renders `tests/claude/ISSUES.md` as a Markdown table. Hard failures still raise AssertionError.

---

## Directory Structure

```
tests/claude/
├── TEST_PLAN.md
├── conftest.py                            # Shared fixtures + marker registration
├── issues.json                            # Runtime artifact
├── ISSUES.md                              # Generated from issues.json
│
├── live/                                  # 123 live integration tests
│   ├── conftest_live.py                   # Session lifecycle (deploy/load/teardown)
│   ├── helpers.py                         # mcp_call, docker_exec, chat_call, etc.
│   ├── test_phase_a_infrastructure.py     #  23 tests
│   ├── test_phase_b_core_tools.py         #  11 tests
│   ├── test_phase_c_management.py         #  23 tests
│   ├── test_phase_d_workers.py            #   9 tests
│   ├── test_phase_e_build_types.py        #  10 tests
│   ├── test_phase_f_imperator.py          #  15 tests
│   ├── test_phase_g_imperator_tools.py    #   9 tests
│   ├── test_phase_h_resilience.py         #  11 tests
│   └── test_phase_i_observability.py      #  12 tests
│
├── test_alerter/                          #  63 mock tests (alerter service)
├── test_assembly/                         #  14 mock tests (assembly guards)
├── test_config/                           #  21 mock tests (config system)
├── test_database/                         #  15 mock tests (database + migrations)
├── test_imperator/                        #  18 mock tests (imperator flow + domain seeding)
├── test_lifecycle/                        #  12 mock tests (startup/shutdown)
├── test_log_shipper/                      #  18 mock tests (log shipper)
├── test_logging/                          #   8 mock tests (logging)
├── test_memory/                           #  16 mock tests (memory integration)
├── test_scheduler/                        #  10 mock tests (scheduler execution)
├── test_search/                           #  17 mock tests (search internals)
├── test_state_manager/                    #  16 mock tests (imperator state)
├── test_tools/                            #  55 mock tests (TE tools)
├── test_transport/                        #  17 mock tests (SSE sessions + chat)
└── test_workers/                          #  15 mock tests (background workers)
```

## Test Phases

| Phase | File | Tests | Covers |
|-------|------|-------|--------|
| A | test_phase_a_infrastructure.py | 23 | Health, metrics, MCP protocol, SSE, startup, migrations |
| B | test_phase_b_core_tools.py | 11 | get_context, store_message, search_messages, search_knowledge |
| C | test_phase_c_management.py | 23 | All conv_*, mem_*, imperator_chat, logs, install |
| D | test_phase_d_workers.py | 9 | Pipeline verification: embeddings, extraction, assembly, logs |
| E | test_phase_e_build_types.py | 10 | passthrough, standard-tiered, knowledge-enriched, utilization |
| F | test_phase_f_imperator.py | 15 | Phase 2 multi-turn conversation (10 turns) |
| G | test_phase_g_imperator_tools.py | 9 | Phase 3 diagnostic, search, admin prompts |
| H | test_phase_h_resilience.py | 11 | Hot-reload, load, budget snapping, roundtrip, validation |
| I | test_phase_i_observability.py | 12 | Logging, Prometheus, log shipper, alerter, container security |

## Infrastructure Files

| File | Purpose |
|------|---------|
| `docker-compose.claude-test.yml` | Isolated test stack definition |
| `config-test/config.yml` | AE config with nominal models |
| `config-test/te.yml` | TE config with nominal imperator |
| `config-test/alerter.yml` | Alerter config (log channel only) |
| `config-test/prompts/` | Copy of production prompts |
| `config-test/credentials/` | Copy of production credentials |
