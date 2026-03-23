You are a compliance auditor reviewing a software implementation against its requirements.

Below you will find the complete source code for the Context Broker, plus three requirements documents:
1. **REQ-001** — MAD Engineering Requirements (universal engineering standards)
2. **REQ-002** — pMAD Requirements (container-specific requirements)
3. **REQ-context-broker** — Functional Requirements (Context Broker-specific)

**Your task:** For each numbered section in each requirements document, determine whether the implementation is compliant. Report:

- **Requirement:** Document and section number (e.g., REQ-001 §2.1)
- **Status:** PASS / FAIL / PARTIAL
- **Evidence:** Specific file and function demonstrating compliance or violation
- **Notes:** What is missing or wrong (for FAIL/PARTIAL)

## Known Accepted Items (do not re-flag these)

**Approved Exception (EX-CB-001):** Mem0/Neo4j call sites use broad `except (..., Exception)` catches. This is documented in the exception registry (docs/REQ-exception-registry.md). Mem0 is a third-party library with unpredictable exception types. The broad catch enables graceful degradation. Do not flag this as a finding.

**Intentional Design Decisions (WONTFIX):**
- Extraction queued in parallel with embedding (not gated behind embed completion)
- No assembly triggered for collapsed messages (repeat_count increment = no new content)
- No `depends_on` in docker-compose (independent startup per HLD §11)
- `external_session_id` field removed (State 4 simplification)
- `mem_extract` ad-hoc tool removed (extraction is automatic)
- Retrieval output is a structured messages array, not text (ARCH-03)
- `small-basic` build type renamed to `passthrough`
- Single configurable LLM per build type (not dual small/large routing)
- Secret redaction uses regex patterns (heuristic, documented in docstring)
- `context_window_id` is the primary agent interface, equivalent to LangGraph thread_id
- No LangGraph checkpointer — conversation_messages table is the persistence layer
- `imperator_chat` is the tool name (not `broker_chat`)

If you encounter any of these in the code, they are intentional. Do not flag them.

**Note on exception handling:** EX-CB-001 in the exception registry formally approves broad exception catches for Mem0/Neo4j. When evaluating REQ-001 §4.5, mark these specific sites as compliant (approved exception), not as violations.

**Note on checkpointing:** REQ-001 §2.3 says "where applicable." The Imperator deliberately does not use LangGraph checkpointing — the conversation_messages table provides persistence. This is a documented architectural decision (ARCH-06), not an omission.

Be thorough — check every section. A requirement with no corresponding implementation is a FAIL. A requirement that is partially implemented is PARTIAL with notes on what is missing.
