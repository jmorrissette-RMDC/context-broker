# Gate 2 Code Review — Pass 3: Context Broker

**Date:** 2026-03-28
**Reviewer:** Gemini CLI
**Status:** Completed
**Focus:** Architectural Decoupling, Async Correctness, and Schema Integrity.

---

## Executive Summary

The third-pass review identifies significant architectural decoupling and async correctness issues. While the system is functional, it violates key State 4 MAD mandates regarding Action Engine (AE) and Thought Engine (TE) separation. Furthermore, several critical fixes from previous rounds (specifically regarding the Alerter's SQL queries and unpinned dependencies) remain unaddressed.

---

## NEW Findings

### A. Architectural & Structural Gaps

| ID | File | Line | Severity | Description | Fix |
|:---|:---|:---:|:---:|:---|:---|
| **S-01** | `packages/context-broker-te/` | - | **Critical** | **Improper AE/TE Separation.** The TE package directly imports from the AE kernel (`app.database`, `app.config`). This violates REQ-001 §12.3 and §13.2, making the TE non-portable. | Pass infrastructure (pool, config) via `State` or a context provider instead of static imports. |
| **S-02** | `app/flows/tool_dispatch.py` | 197-250 | **Major** | **Logic in Dispatcher instead of StateGraphs.** Management tools (`conv_delete_conversation`, `query_logs`) contain direct DB logic. REQ-001 §2.1 mandates all logic reside in StateGraphs. | Refactor management operations into dedicated AE StateGraphs. |
| **S-03** | `packages/context_broker_te/imperator_flow.py` | 260-350 | **Major** | **Sequential Multi-step Logic in Nodes.** `agent_node` performs Domain RAG, prompt loading, and retrieval sequentially. REQ-001 §2.1 requires distinct operations to be separate nodes. | Break `agent_node` into a chain: `rag_node` -> `context_node` -> `llm_node`. |
| **S-04** | `app/flows/build_type_registry.py` | 60-90 | **Major** | **Blocking Graph Compilation.** Lazy compilation of StateGraphs inside a `threading.Lock` blocks the event loop for all concurrent requests during the compile phase. | Compile graphs in a thread pool or pre-compile all registered graphs at startup. |

### B. Logic & Correctness Bugs

| ID | File | Line | Severity | Description | Fix |
|:---|:---|:---:|:---:|:---|:---|
| **L-01** | `alerter/alerter.py` | 196-205 | **Blocker** | **Broken SQL in Log Context (Regression).** Query uses `timestamp` and `level` columns, but `system_logs` uses `log_timestamp` and `data->>'level'`. Webhooks will crash. | Update query to: `SELECT ..., (data->>'level') as level, log_timestamp as timestamp`. |
| **L-02** | `app/workers/db_worker.py` | 315-375 | **Major** | **Redundant Advisory Locking.** Both worker and `memory_extraction` flow attempt to acquire the same lock. While re-entrant, it adds DB overhead and complicates tracing. | Remove redundant lock acquisition from either the worker or the flow. |
| **L-03** | `packages/.../message_pipeline.py` | 110-115 | **Minor** | **Rough Token Estimation.** `len(content) // 4` is highly inaccurate for code/non-English, risking context overflows. | Use `tiktoken` for more accurate token counting, at least for final assembly. |

### C. Resource Management & Performance

| ID | File | Line | Severity | Description | Fix |
|:---|:---|:---:|:---:|:---|:---|
| **R-01** | `entrypoint.sh` | 80-100 | **Major** | **Inefficient Startup.** `pip install --user` runs on every restart even for pre-built wheels. For `pypi` source, this forces a network call on every boot. | Modify `entrypoint.sh` to prefer local wheels if available regardless of `PKG_SOURCE`. |
| **R-02** | `log_shipper/Dockerfile` | 8 | **Major** | **Unpinned Dependencies.** `aiodocker` and `asyncpg` are installed without version pins, violating REQ-001 §1.5. | Use `aiodocker==0.23.0` and `asyncpg==0.30.0`. |
| **R-03** | `app/config.py` | 400-430 | **Minor** | **Synchronous Config Loading.** `load_config()` is sync but called from many async nodes, blocking the event loop. | Use `async_load_config()` or cache config more aggressively to avoid I/O in nodes. |

### D. Security & Best Practices

| ID | File | Line | Severity | Description | Fix |
|:---|:---|:---:|:---:|:---|:---|
| **D-01** | `alerter/alerter.py` | 63-65 | **Minor** | **Deprecated FastAPI Events.** Uses `@app.on_event("startup")` which is deprecated. | Refactor to use the `lifespan` context manager. |
| **D-02** | `packages/.../memory_extraction.py`| 35-45 | **Minor** | **Unbounded Regex.** `_redact_secrets` uses complex regex without length limits, presenting a minor ReDoS risk on malicious content. | Apply redaction only to chunks of manageable size. |

---

## Conclusion

The **S-01 Critical Coupling** and **L-01 Blocker** are the highest priority items. The system's architectural integrity depends on a clean separation between the AE Host (kernel) and the TE Package. Additionally, the transition from synchronous to asynchronous I/O must be completed to ensure State 4 production readiness.
