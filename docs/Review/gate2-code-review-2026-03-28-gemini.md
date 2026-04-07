# Gate 2 Code Review: Context Broker

**Date:** 2026-03-28
**Reviewer:** Gemini CLI
**Status:** Completed
**Focus:** Public Repository Readiness, REQ-001/002 Compliance, Performance, and Security.

---

## Executive Summary

The Context Broker has reached a high level of maturity, particularly with the implementation of the V2 Query-Driven RAG architecture. The codebase strictly adheres to the LangGraph StateGraph mandate (REQ-001 §4.5) and demonstrates excellent separation between infrastructure (AE) and cognitive logic (TE).

However, as a public repository candidate, several security and scalability issues remain. Most notably, hardcoded default passwords in deployment and worker files violate REQ-001 §8.1. Additionally, the DB-driven worker implementation lacks proper concurrency control (SKIP LOCKED), which will cause redundant work in multi-node deployments.

---

## Findings

### A. Security & Compliance

| ID | File | Line | Severity | Description | Fix |
|:---|:---|:---:|:---:|:---|:---|
| **S-01** | `docker-compose.yml` | 134 | **Blocker** | Hardcoded default password `context-broker123` for Postgres. | Use an environment variable with no default in `docker-compose.yml`. |
| **S-02** | `alerter/alerter.py` | 37 | **Major** | Hardcoded default DSN in environment variable fallback. | Remove hardcoded credentials from code; use `os.environ.get("POSTGRES_DSN")` without a hardcoded default string. |
| **S-03** | `log_shipper/shipper.py` | 21 | **Major** | Hardcoded default DSN in environment variable fallback. | Remove hardcoded credentials from code. |
| **I-01** | `docker-compose.yml` | 178 | **Minor** | `context-broker-net` missing `internal: true`. | Add `internal: true` to the network definition to isolate it from the host network per REQ-002 §3.1. |

### B. Scalability & Performance

| ID | File | Line | Severity | Description | Fix |
|:---|:---|:---:|:---:|:---|:---|
| **P-01** | `app/workers/db_worker.py` | 74, 303 | **Major** | Redundant work in `_embedding_worker` and `_log_embedding_worker`. | Add `FOR UPDATE SKIP LOCKED` to the fetch queries to ensure multiple workers don't process the same batch. |
| **P-02** | `app/workers/db_worker.py` | 247 | **Major** | Redundant work in `_assembly_worker`. | Use `FOR UPDATE SKIP LOCKED` on the `context_windows` selection to coordinate assembly across multiple workers. |

### C. Logic & Architecture

| ID | File | Line | Severity | Description | Fix |
|:---|:---|:---:|:---:|:---|:---|
| **L-01** | `packages/context-broker-te/src/context_broker_te/imperator_flow.py` | 290 | **Major** | Duplicate user message in LLM prompt when using V2 `get_context`. | If `get_context` already stored the user message, it will be returned in the history block. The current turn's message is then added again, causing duplication. Remove the duplicated message from the tail of the list if it's already in the prefix. |
| **L-02** | `entrypoint.sh` | 104 | **Minor** | Error suppression in core package installation. | Remove `|| echo "Warning..."` from StateGraph package installation. If a core package fails to install, the container should fail fast per REQ-001 §7.4. |
| **C-01** | `app/config.py` | 126 | **Minor** | Docstring refers to `imperator.yml` but code uses `te.yml`. | Update docstring to reflect the new `te.yml` naming convention established in REQ-002. |
| **C-02** | `alerter/alerter.py` | 51, 62 | **Minor** | Use of deprecated `@app.on_event("startup")`. | Refactor to use FastAPI's `lifespan` context manager. |

### D. Best Practices

| ID | File | Line | Severity | Description | Fix |
|:---|:---|:---:|:---:|:---|:---|
| **B-01** | `app/models.py` | N/A | **Minor** | Missing validation for `model_name` string length. | Add `min_length=1` and `max_length=100` to Pydantic model fields for `model_name` to prevent unbounded input. |
| **B-02** | `packages/context-broker-ae/src/context_broker_ae/build_types/knowledge_enriched.py` | 425 | **Minor** | Hardcoded prompt for context distillation. | Move the distillation prompt to a markdown file in `config/prompts/` and use `async_load_prompt`. |

---

## Recommendations for Public Release

1. **Secret Management:** Implement a proper `.env` mechanism that is never committed. Provide a `config/credentials/.env.example` with clear instructions.
2. **Database Concurrency:** High-volume deployments will require the `SKIP LOCKED` pattern in all DB-driven workers to avoid the "thundering herd" problem and wasted LLM tokens/embedding costs.
3. **Prompt Hardening:** Externalize the distillation prompt used in the Knowledge-Enriched build type to ensure it can be tuned without code changes.
4. **Network Isolation:** Verify that `internal: true` is set on the private bridge network to prevent external access to Postgres and Neo4j.

**Review Conclusion:** The system is architecturally sound and compliant with State 4 requirements. Addressing the security and concurrency findings will make it production-ready for public distribution.
