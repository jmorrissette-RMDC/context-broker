# Context Broker Exception Registry

**Document ID:** REQ-exception-registry
**Version:** 1.0
**Date:** 2026-03-22
**Status:** Active

---

## Purpose

This registry tracks all approved exceptions to REQ-001 (MAD Engineering Requirements), REQ-002 (pMAD Requirements), and REQ-context-broker (Context Broker Functional Requirements).

**Exception vs N/A:**
- **Exception (EX):** Component cannot comply with requirement, needs formal approval and mitigation
- **N/A:** Requirement does not apply to this component

---

## Active Exceptions

| Exception ID | Requirement | Reason | Mitigation | Approved By | Date | Status |
|--------------|-------------|--------|------------|-------------|------|--------|
| EX-CB-001 | REQ-001 §4.5 (Specific Exception Handling) | Mem0 is a third-party library wrapping Neo4j drivers, pgvector, and LLM calls. Its internal exceptions are unpredictable — driver errors, connection failures, and other exceptions don't map to a fixed set of specific types. Catching only known types risks crashing the flow on unexpected Mem0 exceptions instead of degrading gracefully. | Four Mem0 call sites use `except (..., Exception)` in `memory_admin_flow.py` (3 locations) and `memory_extraction.py` (1 location). Each is documented with a G5-18 justification comment. All other exception handlers in the codebase use specific types. Mem0 failures degrade gracefully (return empty results, log warning) rather than crashing. | J | 2026-03-22 | Active |
| EX-CB-002 | ERQ-003 §3.1 (Internal private bridge) | `context-broker-net` is NOT declared `internal: true` because containers need outbound internet access for cloud inference APIs, embedding endpoints, and package downloads in standalone deployment. No ports are published on this network — containers are not reachable from outside. | For ecosystem deployment where outbound traffic routes through Sutherland, `internal: true` should be added via docker-compose.override.yml. | J | 2026-04-01 | Active |
| EX-CB-003 | ERQ-003 §7 (TE configuration separation) | Build type definitions (assembly strategies, compaction parameters) are in AE config (`config.yml`) not TE config (`te.yml`). Build types define infrastructure behavior (how context is assembled, stored, and retrieved) which is AE domain. The TE config owns Imperator settings (Identity, Purpose, model, tools). | Build type selection (`build_type` field) is in TE config — the TE chooses which strategy to use. The strategy implementation details stay in AE config. | J | 2026-04-01 | Active |
| EX-CB-004 | ERQ-003 §2.1.1 (Dkron autoprompter) | The Context Broker uses database-backed scheduling instead of Dkron. The scheduling tools (`create_schedule`, `list_schedules`, `enable_schedule`, `disable_schedule`) with a scheduling worker in `db_worker.py` provide equivalent functionality (cron expressions, one-shot reminders, enable/disable) without requiring an additional container. | Functional replacement: DB-backed scheduling with optimistic locking for safe multi-worker coordination. Tools accessible via MCP. No loss of capability. | J | 2026-04-01 | Active |

---

## Resolved Exceptions

| Exception ID | Requirement | Reason | Resolution | Resolved By | Date |
|--------------|-------------|--------|------------|-------------|------|
| | | | | | |

---

## Exception Request Template

**Exception ID:** EX-CB-[###]
**Requirement:** [document and section]
**Reason:** [Detailed explanation why compliance is impossible]
**Impact:** [What risks does this create]
**Mitigation:** [How risks are reduced]
**Requested By:** [Name]
**Date:** [YYYY-MM-DD]

**Approval:**
- [ ] Approved by Jason
- [ ] Date: [YYYY-MM-DD]
- [ ] Added to Active Exceptions table
