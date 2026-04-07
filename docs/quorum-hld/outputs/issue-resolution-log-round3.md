### [Claude] Issue 1
**Status:** Fixed
**Rationale:** Added `conv_create_context_window`, `conv_search`, and `conv_get_history` to the MCP tools inventory in section 6.1 to match the requirements.

### [Claude] Issue 2
**Status:** Fixed
**Rationale:** Added text to Section 3 and Section 8 to explicitly state that context windows are scoped to a participant-conversation pair, enabling multiple independent strategies per conversation.

### [Claude] Issue 3
**Status:** Fixed
**Rationale:** Updated the network diagram and Section 2 text to explicitly define the external and internal Docker networks.

### [Claude] Issue 4
**Status:** Fixed
**Rationale:** Added a new "Resilience and Observability" section (Section 11) to document the graceful degradation model and eventual consistency.

### [Claude] Issue 5
**Status:** Fixed
**Rationale:** Added logging architecture (structured JSON to stdout/stderr, configurable levels) to Section 11. 

### [Claude] Issue 6
**Status:** Fixed
**Rationale:** Updated Section 3, Step 4 to explicitly state that assembly is triggered proactively for all affected participant windows after every message.

### [Claude] Issue 7
**Status:** Fixed
**Rationale:** Mapped the core StateGraphs to the Action Engine (AE) and Thought Engine (TE) in Section 4.

### [Claude] Issue 8
**Status:** Fixed
**Rationale:** Removed the hardcoded `OpenAIEmbeddings` class reference in Section 3, generalizing it to the configured LangChain embedding model.

### [Claude] Issue 9
**Status:** Fixed
**Rationale:** Added "Context Window Initialization" as Step 1 in the Section 3 Data Flow.

### [Claude] Issue 10
**Status:** Fixed
**Rationale:** Added a brief description of the concurrency model and locking mechanisms to Section 9.

### [Claude] Issue 11
**Status:** Fixed
**Rationale:** Distinguished between hot-reloadable and startup-only configuration settings in Section 7.

### [Claude] Issue 12
**Status:** Fixed
**Rationale:** Removed the hardcoded `768` dimension from the `vector` column description in Section 5, noting it is configuration-dependent.

### [Claude] Issue 13
**Status:** Fixed
**Rationale:** Added a note to Section 10 that the Imperator's build type and token budget are configurable.

### [Claude] Issue 14
**Status:** Fixed
**Rationale:** Added the StateGraph immutability contract to Section 4.

### [Claude] Issue 15
**Status:** Fixed
**Rationale:** Clarified in Section 8 that Semantic Retrieval uses the most recent verbatim messages as the query vector.

### [Gemini] Issue 1
**Status:** Fixed
**Rationale:** Addressed alongside Claude Issue 2 and 6. Data flow now explicitly triggers independent assembly jobs for all participant windows attached to the conversation.

### [Gemini] Issue 2
**Status:** Fixed
**Rationale:** Clarified in Section 3 and Section 8 that while episodic tiers are pre-computed, semantic and knowledge graph layers are queried dynamically at request time.

### [Gemini] Issue 3
**Status:** Fixed
**Rationale:** Addressed alongside Claude Issue 1 by adding the missing MCP tools.

### [Gemini] Issue 4
**Status:** Fixed
**Rationale:** Added the StateGraph package source configuration to Section 7.

### [Gemini] Issue 5
**Status:** Fixed
**Rationale:** Addressed alongside Claude Issue 3 by updating the network topology description.

### [Gemini] Issue 6
**Status:** Fixed
**Rationale:** Changed "polling architecture" to event-driven blocking reads (e.g., `BLPOP`) in Section 9.

### [Gemini] Issue 7
**Status:** Fixed
**Rationale:** Updated Sections 4 and 5 to clarify reliance on standard PostgreSQL `ts_rank` full-text search instead of true BM25.

### [Codex] Issue 1
**Status:** Denied
**Rationale:** The HLD describes system architecture. Build system policies, dependency pinning, linting gates, and testing requirements are implementation details and repository management concerns that do not belong in an architectural High-Level Design document.

### [Codex] Issue 2
**Status:** Denied
**Rationale:** Dockerfile specifics (like `COPY --chown` and specific user directives) are LLD/implementation details. The HLD defines the container topology and roles; it should not drift into documenting Dockerfile syntax.

### [Codex] Issue 3
**Status:** Fixed
**Rationale:** While Docker `HEALTHCHECK` directives are implementation details, the logging architecture is an architectural concern. Added the structured logging model to the new Section 11.

### [Codex] Issue 4
**Status:** Fixed
**Rationale:** Addressed alongside Claude Issue 1 by adding the missing MCP tools.

### [Codex] Issue 5
**Status:** Fixed
**Rationale:** Addressed alongside Claude Issue 6 by changing the data flow to trigger assembly after every message.

---

### [Round 2 - Gemini] Issue 1
**Status:** Fixed
**Rationale:** Updated Section 5 to clarify that while Redis holds ephemeral state and queues, it utilizes disk persistence (RDB/AOF) via `/data/redis` to ensure pending background jobs survive reboots.

### [Round 2 - Gemini] Issue 2
**Status:** Fixed
**Rationale:** Added a new "Security and Authentication" section (Section 12) outlining the non-root least-privilege container model and the Nginx-delegated authentication strategy.

### [Round 2 - Gemini] Issue 3
**Status:** Fixed
**Rationale:** Added a "Backup and Recovery" subsection to Section 5, specifying that the system relies on host-level backups of the `/data` volume.

### [Round 2 - Gemini] Issue 4
**Status:** Denied
**Rationale:** Memory quarantine is identified in `c1` as a mitigation for a known failure mode, but it is not mandated by the current REQ v1.0 specification. Adding architectural features not present in the REQ would be out of scope for this version of the HLD.

### [Round 2 - Gemini] Issue 5
**Status:** Fixed
**Rationale:** Clarified in Section 9 that queue processing uses asynchronous blocking reads (e.g., `await redis.blpop()`) that yield to the event loop, satisfying the async correctness mandate.

### [Round 2 - Claude] Issue 1
**Status:** Fixed
**Rationale:** Added a parenthetical in Section 4 noting that `ts_rank` is PostgreSQL's implementation of BM25-style ranking, bridging the technical reality of the database with the terminology used in the REQ.

### [Round 2 - Claude] Issue 2
**Status:** Fixed
**Rationale:** Addressed alongside Gemini Issue 2. The authentication posture is now documented in Section 12.

### [Round 2 - Claude] Issue 3
**Status:** Fixed
**Rationale:** Added the forward-only, non-destructive constraint and fail-safe boot behavior to the PostgreSQL description in Section 5.

### [Round 2 - Claude] Issue 4
**Status:** Fixed
**Rationale:** Added a note to Section 11 detailing the independent startup model and request-time resilience, removing reliance on container dependency ordering.

### [Round 2 - Claude] Issue 5
**Status:** Fixed
**Rationale:** Specified in Section 3 that message deduplication relies on a client-supplied idempotency key.

### [Round 2 - Claude] Issue 6
**Status:** Fixed
**Rationale:** Clarified in Section 4 that `conv_search` operates on conversation-level metadata and summaries, whereas `conv_search_messages` targets verbatim messages.

### [Round 2 - Claude] Issue 7
**Status:** Fixed
**Rationale:** Clarified in Section 9 that the dead-letter sweep is driven by a periodic timer task and requires manual intervention after a cap on re-queues.

### [Round 2 - Claude] Issue 8
**Status:** Fixed
**Rationale:** Clarified in Section 9 that priority scoring is determined by a priority flag provided during message ingestion.

### [Round 2 - Codex] Issue 1
**Status:** Denied
**Rationale:** The rationale from Round 1 stands. An architectural HLD documents system boundaries, runtime topology, and data flows. CI/CD gates, linter configurations (`black`, `ruff`), and test frameworks (`pytest`) are repository management and lifecycle implementation details, not runtime architecture. Applying a consistent standard for implementation-level detail, this remains excluded.

### [Round 2 - Codex] Issue 2
**Status:** Fixed (Partially)
**Rationale:** I agree that the overarching least-privilege boundary (running as a non-root service account) is a core architectural trust boundary. This has been added to the new Security section (converging with Gemini and Claude feedback). However, I continue to deny the inclusion of specific Dockerfile syntax like `COPY --chown`, which remains an implementation detail.

### [Round 2 - Codex] Issue 3
**Status:** Denied
**Rationale:** While `inputSchema` forms part of the API contract, explicit null-checking of datastore lookups and Pydantic validation are low-level code practices and implementation details, not high-level system architecture.

---

### [Round 3 - Gemini] Issue 1
**Status:** Fixed
**Rationale:** Updated Section 3, Step 6 to replace "conversation chunks" with "verbatim messages" to clarify that Mem0 knowledge extraction operates on the raw conversation record independently of participant-specific assemblies.

### [Round 3 - Gemini] Issue 2
**Status:** Fixed
**Rationale:** Added a dedicated "Memory Search" flow to Section 4 to account for the handling of `mem_search` and `mem_get_context`.

### [Round 3 - Claude] Issue 1
**Status:** Fixed
**Rationale:** Updated Section 4 to explicitly state that the Imperator (TE) flow utilizes LangGraph checkpointing for state persistence across its ReAct loop.

### [Round 3 - Claude] Issue 2
**Status:** Fixed
**Rationale:** Added the fallback behaviors to Section 10. If the state file is missing or the referenced conversation no longer exists, the Imperator creates a new conversation and persists the new ID.

### [Round 3 - Claude] Issue 3
**Status:** Fixed
**Rationale:** Updated Section 7 to reflect that callers can override the default token budget at window creation time, and that the budget is immutable once resolved.

### [Round 3 - Claude] Issue 4
**Status:** Fixed
**Rationale:** Added subsection 11.1 "Architectural Trade-offs" acknowledging the four failure modes (memory contamination, progressive summarization decay, scale/assembly cost, error propagation) outlined in `c1`.

### [Round 3 - Claude] Issue 5
**Status:** Fixed
**Rationale:** Clarified in Section 4 that `conv_search_context_windows` is handled alongside other database query tools in the Search and Query flow.

### [Round 3 - Claude] Issue 6
**Status:** Fixed
**Rationale:** Clarified in Section 8.2 that entities are extracted from recent verbatim messages at retrieval time to serve as graph traversal seeds.

### [Round 3 - Codex] Issue 1
**Status:** Fixed
**Rationale:** Accepted the denial challenge. The rationale that input validation constitutes an "architectural ingress contract" is sound. Added a new subsection 6.4 "Ingress Validation Boundary" detailing how MCP `inputSchema` and Pydantic validation guard the StateGraphs from malformed external data.

### [Round 3 - Codex] Issue 2
**Status:** Fixed
**Rationale:** Addressed alongside Claude Issue 1 by documenting the use of LangGraph checkpointing in Section 4.

### [Round 3 - Codex] Issue 3
**Status:** Fixed
**Rationale:** Updated Section 8.2 to explicitly mention that the Mem0/Neo4j graph traversal leverages LangChain's `Neo4jVector` retriever, satisfying the standard components mandate.