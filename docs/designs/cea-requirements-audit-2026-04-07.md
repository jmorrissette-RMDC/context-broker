# CEA Implementation — Requirements Audit
**Date:** 2026-04-07
**Auditors:** Claude, Gemini, Codex (3-CLI stateless review)
**Requirements audited:** ERQ-001, ERQ-002, ERQ-003, d4-agent-optimal-code-architecture
**Status:** CEA implementation requires significant rework before merge

---

## Summary

27 violations found across 4 requirement documents. The most critical category is ERQ-002 §2.1 (StateGraph Mandate) — the core architectural requirement of the entire system. CEA was implemented as procedural code inside nodes rather than as proper StateGraph structure. This is not a minor gap; it requires the entire CEA implementation to be redesigned.

---

## ERQ-002 §2.1 — StateGraph Mandate (CRITICAL)

The graph is the application. All programmatic logic must be implemented as LangGraph StateGraphs. Nodes must not contain loops, sequential multi-step logic, or branching that controls what happens next.

| # | File | Location | Violation |
|---|------|----------|-----------|
| V-01 | standard_tiered.py | :534–577 `compact_tier1` | CEA flow invoked procedurally via `await cea_flow.ainvoke()` inside a `for chunk` loop inside a node. CEA extraction must be a graph node wired with edges. |
| V-02 | standard_tiered.py | :441 `compact_tier1` | `asyncio.gather([_bounded_summarize(c) for c in chunks])` — loop inside a node. Each chunk summarization must be graph structure (map-reduce subgraph). |
| V-03 | standard_tiered.py | :682–713 `run_full_compaction` | Two sequential unrelated LLM calls (`recent_archival` and `header_text`) in one node. Must be two nodes: `generate_recent_archival` and `update_historical_header`. |
| V-04 | standard_tiered.py | :1039–1067 `ret_wait_for_assembly` | `while time.monotonic() < deadline` polling loop inside a node. Must become a conditional edge routing back to the node. |
| V-05 | cea_extraction_flow.py | :245–373 `dispatch_extraction_results` | `for fact in facts` loop with sequential multi-step async I/O per iteration (`wrapper.add`, `wrapper.write_metadata`, `wrapper.record_feedback`). Multi-step logic per fact must be graph structure. |
| V-06 | cea_enrichment_flow.py | :398–414 `record_feedback` | `for event in events` loop with `await feedback_fn()` per iteration. Same class as V-05. |
| V-07 | cea_extraction_flow.py | :185–192 `run_extraction_llm` | Manual JSON parsing (`json.loads` after string splitting) instead of `llm.with_structured_output(ExtractionSchema)`. Standard component exists; must be used. |
| V-08 | install_stategraph.py | :17–107 | Entire function is ~90 lines of procedural multi-step logic (source routing, pip, subprocess, rescan, cache-clear, DB record). Must be a StateGraph with nodes: `resolve_source`, `run_pip_install`, `rescan_packages`, `clear_caches`, `record_install`. |
| V-09 | quality_wrapper.py | :96 | `QualityWrapper` is substantial core logic entirely outside any StateGraph. The add/search/feedback/cleanup operations must be expressed as graph nodes. |
| V-10 | cea_enrichment_flow.py | all CEAc nodes | Loops inside every CEAc node: `decide_search`, `execute_search`, `evaluate_and_rank`, `assemble_context`, `record_feedback`. Every loop must be a graph cycle. |
| V-11 | standard_tiered.py | :256–1244 (assembly + retrieval nodes) | Loops inside multiple assembly and retrieval nodes throughout. Comprehensive list in Codex audit output. |

---

## ERQ-002 §12.3 — TE Package Independence

The TE must not import from or depend on a specific AE implementation.

| # | File | Location | Violation |
|---|------|----------|-----------|
| V-12 | cea_enrichment_flow.py (TE package) | :358–362 `assemble_context` | Imports `app.prompt_loader` — an AE/app-level module. Prompt template must be injected into state or loaded through a TE-owned mechanism. |
| V-13 | cea_extraction_flow.py (AE package) | entire file | Cognitive extraction logic (LLM fact extraction) placed in the AE package. Cognitive functions belong in the TE. The AE should only trigger the TE's extraction capability. |

---

## ERQ-002 §2.2 — State Immutability

Node functions must not modify input state in-place. Each node returns a new dictionary.

| # | File | Location | Violation |
|---|------|----------|-----------|
| V-14 | cea_enrichment_flow.py | :282–287 `execute_search` | `state["search_results"]` mutated in place via `existing.append(r)`. Must return new dict with updated list. |

---

## ERQ-001 §7.2 — Externalized Configuration

Prompt templates must be externalized from application code.

| # | File | Location | Violation |
|---|------|----------|-----------|
| V-15 | cea_enrichment_flow.py | :219–226 `decide_search` | Query refinement prompt hardcoded as inline f-string. Must be a template file injected via config. |
| V-16 | standard_tiered.py | :704–713 `run_full_compaction` | Historical header system prompt hardcoded inline as string literal. Must be externalized. |
| V-17 | config/prompts/ | cea_vector_extraction.md etc. | CEA prompts stored in host filesystem and loaded via host `prompt_loader`. Since these define TE cognitive behavior, they belong in the TE package, not host config. |

---

## ERQ-001 §3.5 — Specific Exception Handling

No blanket catch-all exception handlers.

| # | File | Location | Violation |
|---|------|----------|-----------|
| V-18 | cea_extraction_flow.py | :207–210 | `except Exception` after LLM call. Should catch `openai.APIError`, `httpx.HTTPError`, `TimeoutError`. |
| V-19 | cea_enrichment_flow.py | :236–239, :272–274, :363, :411 | Multiple `except Exception` blanket catches across CEAc nodes. |
| V-20 | quality_wrapper.py | :189, :452, :646 | Blanket `except Exception` over Mem0 add, cleanup, and Neo4j graph search. |

---

## ERQ-001 §4.1 — No Blocking I/O in Async Context

No blocking I/O in async functions.

| # | File | Location | Violation |
|---|------|----------|-----------|
| V-21 | install_stategraph.py | :54–56 | `shutil.rmtree()` and `shutil.copytree()` blocking filesystem calls in async function. Must use `run_in_executor`. |
| V-22 | install_stategraph.py | :86–88 | `shutil.rmtree()` blocking call in async function. |

---

## ERQ-001 §2.2 — Input Validation

All data from external sources must be validated before use.

| # | File | Location | Violation |
|---|------|----------|-----------|
| V-23 | install_stategraph.py | :18–34 | `package_name` accepted and used to construct pip commands without validation or allowlisting. Command injection risk. |
| V-24 | cea_extraction_flow.py | :179–192 | LLM output parsed with `json.loads` without Pydantic model validation. External LLM output requires strict validation. |

---

## ERQ-001 §1.1 — Code Clarity

Small, focused functions — each does one thing well.

| # | File | Location | Violation |
|---|------|----------|-----------|
| V-25 | standard_tiered.py | :375–589 `compact_tier1` | 215-line node doing 5+ unrelated operations. Resolves with V-01/V-02 (proper graph decomposition). |
| V-26 | standard_tiered.py | :409–431 | `_summarize_chunk` nested async function defined inside `compact_tier1`. Hidden logic, not independently testable. Must be module-level or a named graph node. |

---

## ERQ-003 — pMAD Container Requirements

| # | Severity | Req | File | Violation |
|---|----------|-----|------|-----------|
| V-27 | Confirmed | §2.1.1 | docker-compose.yml | Dkron autoprompter entirely absent. Required standard backing service per §2.1.1. NOTE: Gemini found EX-CB-004 exception in `docs/REQ-exception-registry.md` — verify before treating as violation. |
| V-28 | Confirmed | §2.1.1 | docker-compose.yml | Log shipper marked optional ("Remove or comment out if…"). ERQ-003 lists it as required standard infrastructure. |
| V-29 | Confirmed | §1.4 | docker-compose.yml | `pgvector/pgvector:pg16` is unpinned rolling tag. All other images are pinned. Must be `pgvector/pgvector:0.8.0-pg16` or specific version. |
| V-30 | Confirmed | §1.5 | docker-compose.yml | log-shipper, UI, and alerter containers lack HEALTHCHECK directive. |
| V-31 | Confirmed | §1.1 | Dockerfile | `mkdir -p /data/downloads && chown` in root phase is application data setup, not system package installation. Violates "root only for system package installation and user creation." |
| V-32 | Requires inspection | §7.1–7.3 | config.yml | TE/AE configuration separation unverifiable without reading config.yml. |
| V-33 | Requires inspection | §6.1 | nginx/nginx.conf | `/mcp` endpoint routing unverifiable without nginx.conf. |

---

## What this means

The CEA implementation cannot be merged as-is. The architectural violations in ERQ-002 §2.1 are foundational — the entire CEA pipeline (extraction flow, enrichment flow, quality wrapper, compaction integration) must be redesigned as proper StateGraph nodes with explicit edges, cycles, and routing.

The correct approach for the next implementation:
1. Read ERQ-001, ERQ-002, ERQ-003, d4 **before writing any code**
2. Design the StateGraph structure on paper first — nodes, edges, state schema
3. Every loop becomes a graph cycle
4. Every multi-step operation becomes multiple nodes
5. CEA extraction wired as a node in the assembly graph, not called from inside a node
6. QualityWrapper operations expressed as graph nodes
7. TE imports nothing from AE

---

## Audit outputs (raw)
- `3cli-sessions/cea-req-audit/round-3-claude.md` — ERQ-001/002 findings (Claude)
- `3cli-sessions/cea-req-audit/round-3-gemini.md` — ERQ-001/002 findings (Gemini)
- `3cli-sessions/cea-req-audit/round-3-codex.md` — ERQ-001/002 findings (Codex)
- `3cli-sessions/cea-req-audit/round-4-claude.md` — ERQ-003 findings (Claude)
- `3cli-sessions/cea-req-audit/round-4-gemini.md` — ERQ-003 findings (Gemini)
- `3cli-sessions/cea-req-audit/round-4-codex.md` — ERQ-003 findings (Codex)
