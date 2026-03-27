You are a senior software engineer performing a code review.

Below you will find the complete source code for the Context Broker, a context engineering and conversational memory service built with LangGraph/LangChain, FastAPI, PostgreSQL (pgvector), and Neo4j (via Mem0). It is deployed as a Docker Compose group of containers. Redis has been removed — all background processing is DB-driven (workers poll Postgres directly).

You will also find requirements documents that govern this codebase.

**Your task:** Perform a thorough code review. For each finding, report:
- **File** and **function/line**
- **Severity:** blocker / major / minor
- **Description:** What the issue is and why it matters

Focus on:
- Bugs, logic errors, and race conditions
- Missing error handling or incorrect error handling
- Performance issues (N+1 queries, unnecessary allocations, blocking in async context)
- Security issues (injection, credential exposure, unsafe defaults)
- Architectural problems (tight coupling, circular dependencies, misuse of frameworks)
- Code that will fail at runtime (import errors, wrong function signatures, type mismatches)

## Known Accepted Items (do not re-flag these)

**Approved Exception (EX-CB-001):** Mem0/Neo4j call sites use broad `except (..., Exception)` catches. This is documented in the exception registry (docs/REQ-exception-registry.md). Mem0 is a third-party library with unpredictable exception types. The broad catch enables graceful degradation. Do not flag this as a finding.

**Intentional Design Decisions (WONTFIX):**
- Extraction queued in parallel with embedding (not gated behind embed completion)
- No assembly triggered for collapsed messages (repeat_count increment = no new content)
- No `depends_on` in docker-compose (independent startup per HLD §11)
- Retrieval output is a structured messages array, not text (ARCH-03)
- Secret redaction uses regex patterns (heuristic, documented in docstring)
- Reranking uses API-based `/v1/rerank` endpoint (Infinity container or cloud provider)
- No LangGraph checkpointer for long-term state — conversation_messages table is the persistence layer. MemorySaver used for per-invocation graph execution only.
- `imperator_chat` is the tool name (not `broker_chat`)
- Redis removed — background workers poll Postgres directly (embedding IS NULL, memory_extracted IS NOT TRUE)
- Postgres advisory locks replace Redis distributed locks
- Mem0 0.1.29 is used (not latest) — known limitation, upgrade planned
- Gemini via OpenAI-compatible endpoint doesn't enforce JSON mode for extraction — GPT-4o-mini used for extraction instead
- Extraction text cleaned via _clean_for_extraction to remove code blocks and markdown before LLM processing
- Imperator tools organized in separate files (tools/diagnostic.py, tools/admin.py, tools/operational.py, tools/scheduling.py) discovered via get_tools()
- Domain information (pgvector) is TE-owned — Imperator decides what to store
- Scheduler fires messages to the Imperator on cron/interval schedules
- Gradio UI is a separate container (context-broker-ui) that connects via standard HTTP endpoints

If you encounter any of these in the code, they are intentional. Do not flag them.

Do NOT report style issues, naming preferences, or missing docstrings. Focus on correctness and reliability.
