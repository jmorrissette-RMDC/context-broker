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
