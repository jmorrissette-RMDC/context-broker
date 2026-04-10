# Context Broker — Transition Report

**Date:** 2026-04-10
**Event:** Issue tracker migration from `rmdevpro/Joshua26` to `jmorrissette-RMDC/context-broker`

***

## What happened

All context-broker development was previously tracked as issues on the `rmdevpro/Joshua26` monorepo, prefixed with `[CB]`. Code has always lived in `jmorrissette-RMDC/context-broker`. This session unified code and issues into a single repository.

### Migration summary

| Item | Count |
|------|-------|
| Issues migrated | 126 |
| Closed (history) | 106 |
| Open (backlog) | 20 |
| Source repo | `rmdevpro/Joshua26` |
| Target repo | `jmorrissette-RMDC/context-broker` |
| New issue range | #1 – #127 |

Each migrated issue body contains a note linking to the original Joshua26 issue number. The originals have been deleted from Joshua26.

### Number mapping

Old Joshua26 numbers no longer exist. A mapping file was generated during migration. The pattern is chronological: Joshua26 #13 became context-broker #4, Joshua26 #556 became context-broker #127. References in commit messages (e.g., `#551`, `#556`) refer to the **old** Joshua26 numbers.

***

## Current state of the codebase

### Test suite: 58/58 passing

- **Phase F** (Imperator): multi-turn continuity, coherence, tool dispatch, CEAc enrichment
- **Phase G2** (All tools): all 8 MCP tools verified end-to-end
- **Phase K** (Real effects): config write, verbose toggle, knowledge search with real facts

### CEAc enrichment: operational

The Context Engineering Agent (enrichment variant) is fully functional:

- Compiles as a separate Imperator graph node (init_context → ceac_enrichment → llm_call)
- Injects 3–4 KB of ranked knowledge context per turn
- Compiled with `checkpointer=False` to avoid msgpack serialization of function callables
- Records feedback events for every search result (used/discarded)

### Key commits in this session

| Commit | Description |
|--------|-------------|
| `199f1e9` | Refactor CEAc to comply with ERQ-002 StateGraph mandate |
| `b227bd8` | Enable CEAc + add live integration tests |
| `6364e37` | Extract CEAc into separate Imperator graph node |
| `84758c0` | Fix msgpack serialization (checkpointer=False) |
| `9b073f1` | Make CEAc live tests robust to conversation state |

***

## Open backlog (27 issues)

### ERQ-002 violations (StateGraph mandate)

| Issue | Summary |
|-------|---------|
| #95 | run_full_compaction: two sequential LLM calls in one node |
| #96 | ret_wait_for_assembly: while polling loop inside a node |
| #97 | dispatch_extraction_results: for-fact loop with multi-step I/O |
| #99 | run_extraction_llm: manual JSON parsing instead of with_structured_output |
| #100 | install_stategraph: entirely procedural, not a StateGraph |
| #101 | QualityWrapper: substantial logic outside any StateGraph |
| #104 | cea_extraction_flow.py: cognitive LLM work in AE, belongs in TE |

### ERQ-001 violations (code quality)

| Issue | Summary |
|-------|---------|
| #106 | Query refinement prompt hardcoded inline |
| #107 | Historical header prompt hardcoded inline |
| #108 | CEA prompts stored in host /config/prompts/ not TE package |
| #109 | Blanket except Exception in CEA flows |
| #110 | Blocking shutil calls in async install_stategraph |
| #111 | install_stategraph accepts package_name without validation |
| #112 | LLM output parsed with json.loads without Pydantic validation |
| #113 | compact_tier1 is 215 lines doing 5+ operations |
| #114 | _summarize_chunk nested function hidden inside compact_tier1 |

### ERQ-003 violations (deployment)

| Issue | Summary |
|-------|---------|
| #115 | Dkron autoprompter absent from compose |
| #116 | Log shipper marked optional but is required |
| #117 | pgvector:pg16 is unpinned rolling tag |
| #118 | log-shipper, UI, alerter lack HEALTHCHECK |

### Other open

| Issue | Summary |
|-------|---------|
| #1 | README.md missing from repository root |
| #49 | test_search_knowledge_returns_facts user_id mismatch |
| #58 | Context Engineering Architecture — implementation (epic) |
| #59 | Broken backward-compat shim in retrieval_flow.py |
| #68 | knowledge_feedback schema missing from mcp.py |
| #81 | Natural-key dedup rejects multiple facts from same utterance |
| #94 | Missing knowledge_get_context absent check in test |

***

## Previous working directory

Development was tracked in `Joshua26/state_4_development/context_broker_pmad/`. That directory contains:

- `recent_turns.md` — session logs
- `migrate_issues.sh` — the migration script used
- Plan files and task tracking from the CEAc refactor

This directory can be archived or deleted. All deliverables are in `jmorrissette-RMDC/context-broker`.

***

## Deployment

The `claude-test` stack on Aristotle9 (`192.168.1.110`) is running the latest code. To deploy to production, use the standard deploy script against the production compose file.

Requirements docs governing this codebase:
- `ERQ-001` — Engineering requirements (code quality)
- `ERQ-002` — StateGraph architecture mandate
- `ERQ-003` — Deployment and infrastructure standards
- Located at `C:\Users\j\projects\Joshua26\docs\requirements\`
