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