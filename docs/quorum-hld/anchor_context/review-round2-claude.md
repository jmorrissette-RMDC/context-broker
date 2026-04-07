## Context Broker HLD — Round 2 Review

---

### Issue 1
**Type:** Verification Failure
**Section:** 4 (StateGraph Architecture) / REQ 4.6
**Description:** The Round 1 fix for Gemini Issue 7 changed the HLD to use `ts_rank` instead of BM25 for full-text search, which is technically accurate for PostgreSQL. However, the REQ (Section 4.6, `conv_search_messages` row) still says "Hybrid search (vector + BM25 + reranking)." The HLD now contradicts its authoritative requirements document. Either the REQ should be updated to match, or the HLD should use the REQ's terminology.
**Recommendation:** Flag this as a known divergence requiring a REQ update, or add a parenthetical in the HLD noting that "full-text search (`ts_rank`)" is the PostgreSQL implementation of what the REQ calls BM25-style ranking.

---

### Issue 2
**Type:** Gap
**Section:** 6 (Interface Design)
**Description:** REQ 4.3 defines the authentication posture: the system ships without authentication, is designed for single-user/trusted-network deployment, and supports standard nginx auth (basic auth, mTLS, auth proxy) at the gateway layer without application changes. The HLD is entirely silent on authentication. This is an architectural boundary decision — it defines the trust model for the sole external-facing component — and belongs in the HLD.
**Recommendation:** Add a brief authentication subsection under Section 6 stating the trust model and the extension point at the gateway.

---

### Issue 3
**Type:** Gap
**Section:** 5 (Storage Design)
**Description:** REQ 3.7 specifies that schema migrations must be "forward-only and non-destructive — they must not drop data. If a migration cannot be applied safely, the application must fail with a clear error rather than proceed with an incompatible schema." The HLD (Section 5) mentions "schema versioning for automated migrations on boot" but omits the forward-only, non-destructive constraint and the fail-safe behavior. This is an architectural invariant about data safety, not implementation detail.
**Recommendation:** Add a sentence to the PostgreSQL bullet in Section 5 stating that migrations are forward-only and non-destructive, and that the application refuses to start if a migration cannot be safely applied.

---

### Issue 4
**Type:** Gap
**Section:** 2 (Container Architecture) / 11 (Resilience and Observability)
**Description:** REQ 7.2 specifies that containers start and bind ports without waiting for dependencies, with dependency unavailability handled at request time via degradation and retry. This is an architectural decision that affects startup behavior, resilience, and the absence of `depends_on` with health conditions. The HLD does not mention this startup model.
**Recommendation:** Add a brief note to Section 11 (Resilience) or Section 2 stating that containers start independently without dependency ordering, and unavailability is handled at request time.

---

### Issue 5
**Type:** Clarity
**Section:** 3 (Data Flow)
**Description:** Step 2 states "The system deduplicates the message" as part of the critical ingestion path, but the deduplication key is unspecified. Is it a client-supplied message ID? A content hash? A compound key of (conversation_id, participant, timestamp)? The deduplication strategy affects data integrity and client contract — an agent retrying a failed `conv_store_message` call needs to know whether idempotency is guaranteed and how.
**Recommendation:** Specify the deduplication key (e.g., client-supplied idempotency key, or conversation_id + hash) in the data flow description.

---

### Issue 6
**Type:** Clarity
**Section:** 4 (StateGraph Architecture)
**Description:** Section 4 lists both a "Search" flow ("Hybrid vector + full-text search, combined via RRF, with optional cross-encoder reranking") and Section 6.1 lists two distinct search tools: `conv_search` ("Semantic and structured search across conversations") and `conv_search_messages` ("Hybrid search across messages"). The Search flow in Section 4 only describes message-level hybrid search. There is no description of what `conv_search` does architecturally — searching conversation-level metadata? Semantic search over conversation summaries? The distinction between these two tools is unclear from the HLD.
**Recommendation:** Briefly clarify in Section 4 or Section 6.1 what `conv_search` operates over versus `conv_search_messages`.

---

### Issue 7
**Type:** Clarity
**Section:** 9 (Async Processing Model)
**Description:** The dead-letter mechanism states: "After maximum attempts, they are moved to a `dead_letter_jobs` list, which a background sweep periodically attempts to re-queue." This was introduced in the revision. What drives the sweep — a timer-based worker? A separate consumer? What's the re-queue policy (immediate retry, backoff, manual intervention)? Without this, the dead-letter mechanism is a black box that could silently re-introduce poison jobs in a loop.
**Recommendation:** Add one sentence clarifying the sweep trigger (e.g., "a periodic timer task") and whether re-queued dead-letter jobs have a final-attempt cap or require manual intervention.

---

### Issue 8
**Type:** Clarity
**Section:** 9 (Async Processing Model)
**Description:** The priority scoring mechanism says `memory_extraction_jobs` uses a ZSET "to prioritize live user interactions over background agent prose or bulk migration data." The HLD does not explain how the system determines whether a message originates from a "live user interaction" versus "background agent prose." Is this based on participant role, a flag on the message, or the conversation type? The scoring policy affects extraction latency for end users.
**Recommendation:** Add a brief note on what signal determines the priority score (e.g., participant role, a priority field on the stored message).

---

## Summary

| Type | Count |
|---|---|
| Gap | 3 |
| Clarity | 4 |
| Verification Failure | 1 |
| Denial Challenge | 0 |
| Inconsistency | 0 |
| Technical Concern | 0 |
| Best Practice | 0 |

**Overall Assessment:** The revised HLD is substantially improved from Round 1. All 22 accepted fixes from the resolution log were verified as adequately addressed. The two Codex denials (build system policies and Dockerfile specifics) are reasonable — these are implementation concerns, not architecture. The remaining issues are moderate: three gaps where the REQ defines architectural constraints the HLD doesn't capture (authentication posture, migration safety invariant, independent startup model), four clarity items where newly introduced or existing mechanisms are underspecified, and one REQ-HLD terminology divergence created by a Round 1 fix. None of these are blocking, but the authentication gap (Issue 2) and the migration safety constraint (Issue 3) are the most important to address as they define trust boundaries and data safety guarantees respectively.
