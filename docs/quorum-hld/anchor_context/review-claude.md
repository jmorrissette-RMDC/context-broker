# Context Broker HLD — Review Issue Log

---

### Issue 1
**Type:** Inconsistency
**Section:** §6.1 MCP Interface
**Description:** The HLD tool list is missing three tools that appear in REQ §4.6: `conv_create_context_window`, `conv_search`, and `conv_get_history`. These are not aliases — each has a distinct purpose. `conv_create_context_window` is particularly important because it is the mechanism by which a participant establishes its own window with a chosen build type and token budget, which is central to the per-participant model from c1.
**Recommendation:** Add all three missing tools to the HLD tool list. `conv_create_context_window` deserves a sentence in the data flow or build types section explaining when and how it is called.

---

### Issue 2
**Type:** Gap
**Section:** §3 Data Flow / §8 Build Types
**Description:** c1's core architectural idea is that context windows are **per-participant** — different participants in the same conversation can have different build types and token budgets. The HLD never states this. The data flow describes a single "context window" per conversation, and the build types section describes strategies without connecting them to the participant-conversation relationship. A reader of the HLD alone would not understand that a single conversation can have multiple concurrent context windows with different strategies.
**Recommendation:** Add a brief paragraph (in §3, §5, or §8) explicitly stating that context windows are scoped to a participant-conversation pair, not to the conversation as a whole. Reference `conv_create_context_window` as the mechanism.

---

### Issue 3
**Type:** Inconsistency
**Section:** §2 Container Architecture
**Description:** REQ §7.3 specifies a dual-network topology: an external `default` network (host-exposed, gateway only) and an internal `context-broker-net` bridge (all containers). The HLD diagram shows "Host Network" and "Internal Bridge Network" but does not describe two Docker networks. The diagram implies the gateway connects to both, but this is implicit rather than explicit, and the terminology doesn't match the REQ. A developer implementing from this HLD could create a single-network deployment.
**Recommendation:** Add a brief note or update the diagram to explicitly show the two named Docker networks and which containers attach to which, matching REQ §7.3.

---

### Issue 4
**Type:** Gap
**Section:** (No corresponding section)
**Description:** REQ §7.1 requires graceful degradation: failure of Neo4j or the reranker must cause degraded operation, not a crash. Core operations (store, retrieve, search) must continue with reduced capability, and the health endpoint must report degraded status. The HLD does not discuss degradation behavior anywhere. §5 mentions eventual consistency but does not address what happens when a backing service is completely unavailable.
**Recommendation:** Add a subsection (to §5 Storage Design or a new §11) describing the degradation model: which components are optional, what reduced capability looks like for each failure mode, and how degraded status is surfaced.

---

### Issue 5
**Type:** Gap
**Section:** (No corresponding section)
**Description:** REQ §6 defines nine requirements for logging and observability: structured JSON logging, log levels, content standards, specific exception handling, resource management, and error context. The HLD mentions Prometheus metrics in §4 and §6.3 but says nothing about the logging architecture itself. For a system with three async background pipelines and multiple datastores, the logging model is an architectural concern, not just an implementation detail.
**Recommendation:** Add a brief section covering the logging approach: structured JSON to stdout/stderr, configurable log levels, and the principle that metrics are produced inside StateGraphs. This doesn't need to enumerate every log line — just establish the architectural pattern.

---

### Issue 6
**Type:** Inconsistency
**Section:** §3 Data Flow (step 3)
**Description:** The HLD states that context assembly is triggered only when "the context window's token thresholds have been crossed." c1 states that context is "assembled proactively — triggered in the background after each message is stored." These are different strategies. The threshold-based approach is a reasonable optimization, but it means a retrieval call shortly after message storage might return a stale snapshot if the threshold hasn't been crossed — contradicting c1's promise that "when an agent calls for context, the assembled view is already waiting."
**Recommendation:** Clarify the intended trigger semantics. If threshold-based, explain why and note that retrieval may serve a slightly stale snapshot between threshold crossings. If the intent is to match c1's "every message" model, update step 3 accordingly.

---

### Issue 7
**Type:** Clarity
**Section:** §1 System Overview
**Description:** The HLD mentions "State 4 MAD" and describes the AE/TE separation in one sentence, but never maps HLD components to AE or TE. The REQ explicitly defines: AE = MCP tool handlers, message routing, database operations, queue processing; TE = Imperator and its cognitive apparatus. A developer reading the HLD would not know which StateGraphs belong to the AE and which to the TE.
**Recommendation:** Add a sentence or two mapping the core flows (§4) to AE or TE. For example: "The Message Pipeline, Embed Pipeline, Context Assembly, Retrieval, Memory Extraction, Search, and Metrics flows constitute the AE. The Imperator flow constitutes the TE."

---

### Issue 8
**Type:** Clarity
**Section:** §3 Data Flow (step 2)
**Description:** Step 2 references "the configured LangChain `OpenAIEmbeddings` model" as though the embedding provider is always OpenAI. REQ §5.2 and §4.5 specify that the embedding provider is configurable (OpenAI, HuggingFace, etc.) via LangChain abstractions. Naming a specific class here contradicts the configurability principle.
**Recommendation:** Change to "the configured LangChain embedding model" or similar provider-neutral phrasing.

---

### Issue 9
**Type:** Gap
**Section:** §3 Data Flow
**Description:** The data flow describes message ingestion through retrieval but does not describe the **context window creation** flow. Before context can be assembled for a participant, a context window must be created (via `conv_create_context_window`) with a build type and token budget. This includes the token budget resolution logic described in REQ §5.4 (querying the provider for model context length when set to `auto`). This is a distinct flow that precedes assembly.
**Recommendation:** Add a step 0 or a separate paragraph describing context window creation: who initiates it, when it happens, and how the token budget is resolved.

---

### Issue 10
**Type:** Technical Concern
**Section:** §9 Async Processing Model
**Description:** The HLD describes three independent queue consumers but does not address concurrency control beyond "distributed locks to prevent concurrent context assembly" (§5, Redis). Key questions left unresolved: Can multiple embedding workers run concurrently? Is there ordering guarantee between embedding completion and context assembly enqueueing? What happens if a memory extraction job and a context assembly job race on the same conversation chunk? For a system with three parallel async pipelines writing to three different datastores, the concurrency model is an architectural concern.
**Recommendation:** Add a brief description of the concurrency model: how many workers per queue, ordering guarantees (or lack thereof), and how the lock in Redis prevents specific race conditions (e.g., two assembly jobs for the same window).

---

### Issue 11
**Type:** Gap
**Section:** §7 Configuration System
**Description:** REQ §5.1 distinguishes two categories of configuration: hot-reloadable settings (inference providers, models, build types, token budgets — read fresh per operation) and cold settings (database connection strings, ports, network settings — read at startup, require restart). The HLD §7 mentions hot-reloading for inference settings but does not acknowledge the cold/hot distinction or identify which settings require restart.
**Recommendation:** Add a sentence distinguishing hot-reloadable vs startup-only configuration, consistent with REQ §5.1.

---

### Issue 12
**Type:** Inconsistency
**Section:** §5 Storage Design
**Description:** The HLD specifies `vector(768)` for the embedding column dimension. The REQ §5.2 shows `text-embedding-3-small` as the default embedding model, which produces 1536-dimensional vectors (not 768). If the intent is to use a dimensionality-reduced variant, this should be stated. Otherwise the hardcoded dimension contradicts the default model configuration.
**Recommendation:** Either remove the hardcoded dimension (let it be determined by the configured model) or note that the dimension is configuration-dependent and 768 is illustrative.

---

### Issue 13
**Type:** Gap
**Section:** §10 Imperator Design
**Description:** REQ §5.5 specifies that the Imperator's `build_type` is configurable (defaulting to `standard-tiered`) and that switching to `knowledge-enriched` activates the full retrieval pipeline. The HLD §10 mentions the Imperator uses "the exact same MCP tool functions" but does not mention that the Imperator has its own configurable build type, which determines how rich its own context is. This is a meaningful architectural detail — the Imperator is a consumer of its own service, and its context strategy is tunable.
**Recommendation:** Add a note that the Imperator's build type and token budget are configurable per REQ §5.5.

---

### Issue 14
**Type:** Best Practice
**Section:** §4 StateGraph Architecture
**Description:** REQ §4.6 (LangGraph State Immutability) requires that StateGraph node functions never modify input state in-place and always return a new dictionary with only updated fields. This is a common source of bugs in LangGraph applications and is significant enough to be called out as a requirement. The HLD's StateGraph section does not mention this constraint.
**Recommendation:** Add a brief architectural note in §4 stating the immutability contract for StateGraph nodes.

---

### Issue 15
**Type:** Clarity
**Section:** §8 Build Types and Retrieval
**Description:** The `knowledge-enriched` build type allocates 15% to "Semantic Retrieval" described as finding "past messages similar to the current topic but outside the Tier 3 window." The phrase "current topic" is vague — it's unclear what serves as the query vector. Is it the most recent message? A summary of the Tier 3 window? An entity extraction? This ambiguity could lead to different implementations with very different retrieval quality.
**Recommendation:** Clarify what "current topic" means operationally — what text is embedded and used as the similarity query for semantic retrieval.

---

## Summary

| Type | Count |
|---|---|
| Gap | 6 |
| Inconsistency | 4 |
| Clarity | 3 |
| Technical Concern | 1 |
| Best Practice | 1 |
| **Total** | **15** |

**Overall Assessment:** The HLD is structurally sound and covers the major components, data flow, technology choices, and deployment model well. The container architecture, async processing model, and build type system are clearly described. However, it needs **moderate revision** in two areas:

1. **REQ alignment** — Three tools are missing from the inventory, and several REQ-specified behaviors (graceful degradation, logging architecture, hot/cold config distinction, state immutability) have no representation in the HLD. These are not implementation details — they are architectural commitments that belong at HLD level.

2. **c1 fidelity** — The per-participant context window model, which is arguably c1's most important architectural idea, is never stated in the HLD. The assembly trigger semantics also diverge from c1 without explanation.

None of these issues require a structural rewrite. The HLD's skeleton is correct. The gaps are additive — they can be addressed by adding paragraphs and clarifying existing text.
