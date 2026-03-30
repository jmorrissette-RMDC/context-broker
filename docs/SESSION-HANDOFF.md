# Context Broker — Session Handoff

**Date:** 2026-03-30
**From:** Gate 2 review, rebuild verification, and test infrastructure session
**To:** Compaction redesign implementation session

***

## Previous Session Transcript

The full conversation transcript from the session that produced this handoff is available at:

```
C:\Users\j\.claude\projects\C--Users-j-projects-portfolio-ContextBroker\fd13932c-363e-43d9-a968-48144b0dbcba.jsonl
```

If you have questions about decisions made, context behind a fix, or why something was done a certain way, you can read this file. It contains the complete conversation including all tool calls and results.

You can also resume that session directly to ask it questions:

```bash
claude -r "fd13932c-363e-43d9-a968-48144b0dbcba"
```

## First Thing: Write Your Own Plan File

Before doing anything else, read this document completely, then write a plan file. The plan should cover Phases 2-5 below with enough detail to execute without this handoff doc.

***

## Required Reading

Read these in order before starting work:

1. `docs/designs/HLD-Joshua26-system-overview.md` (in Joshua26 root) and all linked docs
2. `docs/REQ-context-broker.md` — product requirements
3. `docs/HLD-context-broker.md` — high-level design
4. `docs/designs/DESIGN-compaction-v2.md` — the compaction redesign spec (this is what you're implementing)
5. `docs/guides/debugging-and-troubleshooting-guide.md` — the required debug loop
6. `docs/guides/mad-deployment-guide.md` — how code reaches running containers
7. `docs/Review/issue-log.md` — historical issue log (now also on GitHub Issues)

***

## Current State

### What's Done
- **Phase 1 complete:** 377 issues found across 7 review rounds, all fixed except 2 deferred + 4 deferred deps
- **482 tests:** 299 mock + 183 live, all passing
- **39 regression tests** added for all RB-* code bugs
- **Phase 0 fresh deploy test** (`FRESH_DEPLOY=1`) — teardown, rebuild, schema verify
- **Stress test suite** at `tests/stress/` — ready to run after compaction redesign
- **Repo migrated** from `portfolio/ContextBroker` to `Joshua26/state_4_development/context_broker_pmad/`
- **Issues on GitHub** at `rmdevpro/Joshua26` with `component:context-broker` label

### What's Open
- **rmdevpro/Joshua26#362** — RB-27: Inline assembly on new window (deferred to compaction redesign)
- **rmdevpro/Joshua26#373** — RB-32d: Dedup logic rewrite (deferred to compaction redesign)
- **4 deferred dependency upgrades** — LangChain 1.x ecosystem (DEP-19, 20, 22, 26)

### Test Stack
- Running on irina (192.168.1.110), port 8080 (production) / 8081 (test)
- SSH: `aristotle9@192.168.1.110`
- Remote project: `/mnt/storage/projects/portfolio/ContextBroker` (needs updating to new repo path)

***

## Phase 2: Compaction Redesign Implementation

**Design doc:** `docs/designs/DESIGN-compaction-v2.md`

Key changes:
- **Deadband tiered compaction** — tier 1 swings 20%-73%, tier 2 swings 6%-12%, tier 3 fixed at 2%
- **Compaction rhythm** — 4 tier 1 compactions per full cycle, then tier 2→tier 3 consolidation
- **Prefix caching** — static content first in prompt ordering for LLM cache hits
- **Tier naming** — rename tiers (tier 1 = live/recent, tier 3 = archival — current naming is inverted)

Test small after each change. Use the debug loop from the troubleshooting guide.

### Addresses Open Issues
- RB-27: Inline assembly on new window
- RB-32d: Dedup logic rewrite

***

## Phase 3: Tool Rename

Rename `mem_*` MCP tools to `knowledge_*` to clear namespace for future conversation memories feature.

***

## Phase 4: REQ and HLD Updates

Align `docs/REQ-context-broker.md` and `docs/HLD-context-broker.md` with:
- Compaction redesign (new tier layout, deadband, compaction rhythm)
- Tier naming changes
- Tool rename (knowledge_* tools)

***

## Full Code Review (after Phase 4)

Run 3-CLI review against REQ-001, REQ-002, REQ-CB. Use the process from `docs/guides/debugging-and-troubleshooting-guide.md`. Debug everything found.

***

## Phase 5: Full Test Suite

Execute in order:
1. **Fresh deploy** — `FRESH_DEPLOY=1 pytest tests/claude/ -v`
2. **Regular data set** — full 482-test suite
3. **Stress test** — `python tests/stress/stress_test.py` (endurance & scale, budget gradient 8K-1M, concurrent windows)

***

## Key Files

### Application Code
| File | Purpose |
|------|---------|
| `packages/context-broker-ae/src/context_broker_ae/build_types/standard_tiered.py` | Assembly and compaction logic — main target for Phase 2 |
| `packages/context-broker-ae/src/context_broker_ae/conversation_ops_flow.py` | Context window creation, get_context flow |
| `packages/context-broker-te/src/context_broker_te/imperator_flow.py` | Imperator ReAct loop, history loading |
| `app/workers/db_worker.py` | Assembly worker, embedding worker |
| `app/config.py` | Configuration with mtime-based hot-reload |
| `app/migrations.py` | Database schema migrations |

### Infrastructure
| File | Purpose |
|------|---------|
| `Dockerfile` | Langgraph container (UID 1000, /data ownership) |
| `docker-compose.yml` / `docker-compose.claude-test.yml` | Production and test stack |
| `entrypoint.sh` | Package installation, /data setup |
| `nginx/nginx.conf` | Gateway routing (variable upstreams, resolver) |

### Tests
| File | Purpose |
|------|---------|
| `tests/claude/live/conftest.py` | Phase 0 fresh deploy, bulk load, pipeline wait |
| `tests/claude/live/test_phase_k_real_tool_effects.py` | Imperator tool invocation tests |
| `tests/claude/live/test_phase_l_quality_eval.py` | LLM judge quality evaluation |
| `tests/stress/stress_test.py` | Endurance and scale stress test |

***

## Behavioral Rules

These are not suggestions. They are requirements.

1. **Answer questions without acting.** When asked a question, answer it. Do not start fixing things.
2. **Discuss before fixing.** Present findings, get agreement, then act.
3. **Test each change individually.** Never batch untested changes.
4. **Record every issue in GitHub Issues.** Before investigating, create the issue.
5. **Root cause with 3 CLIs.** Claude, Gemini, Codex — all three, every issue. Not optional.
6. **Follow directions exactly.** No scope questions, no debates, no second-guessing.
7. **Never dismiss failures as "LLM non-determinism."** It's almost never the LLM. Consult the CLIs.
