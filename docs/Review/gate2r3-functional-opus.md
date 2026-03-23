# Gate 2 Round 3 — Functional Review (Opus)

**Reviewer:** Claude Opus 4.6 (1M context)
**Date:** 2026-03-22
**Scope:** Behavioral comparison of new Context Broker against original Rogers code
**Pass:** 3

---

## Round 2 Major Findings — Resolution Status

| R2 Finding | Status | Notes |
|---|---|---|
| F-11: Mem0 dedup index not applied at startup | **OPEN** | Still no migration or startup hook to create the index on Mem0's tables. Comment in `init.sql` (line 157) says "The application attempts to create this index after Mem0 initializes" but no code does this. |
| F-13: conv_search missing flow_id/user_id/sender_id filters | **OPEN** | `SearchConversationsInput` in `models.py` still only has query/limit/offset/date_from/date_to. The `conversations` table has `flow_id` and `user_id` columns but the search flow does not expose them. |
| F-14: Message search filters applied post-query | **FIXED** | `search_flow.py` now pushes sender_id, role, date_from, date_to into SQL WHERE clauses of both CTEs via `_build_extra_filters()` (M-20). |
| F-18: nomic-embed-text dimension mismatch | **FIXED** | `mem0_client.py` `_get_embedding_dims()` now includes `nomic-embed-text: 768` and `nomic-embed-text:latest: 768` in the dims_map (M-19). Also supports explicit `embedding_dims` config override. |

---

## New Findings (Round 3)

### F-19: Mem0 dedup index still not applied at startup

- **Original feature:** Rogers applied a deduplication index on Mem0's internally-created `mem0_memories` table to prevent duplicate knowledge entries from overlapping extraction runs.
- **New implementation:** MISSING. The `init.sql` has a comment (line 156-157) acknowledging the need, but neither `migrations.py` nor any startup code creates the index. Mem0's tables are created lazily on first use, so they do not exist at init.sql time. The application must create the index after Mem0 initializes its tables.
- **Severity:** major
- **Notes:** Carried forward from R2 F-11 unchanged. Without this index, repeated extraction runs on overlapping message windows can produce duplicate memories in the knowledge graph.

### F-20: conv_search still missing flow_id, user_id, sender_id structured filters

- **Original feature:** Rogers' `conv_search` accepted `flow_id`, `user_id`, `sender_id`, and `external_session_id` as SQL WHERE filters, enabling callers to scope conversation searches to specific flows, users, or senders.
- **New implementation:** MISSING. `SearchConversationsInput` only has `query`, `limit`, `offset`, `date_from`, `date_to`. The `conversations` table has `flow_id` and `user_id` columns (migration 005), and messages have `sender_id`, but the search flow does not expose any of these as filters.
- **Severity:** major
- **Notes:** Carried forward from R2 F-13 unchanged. The MCP tool schema in `mcp.py` also lacks these filter parameters. This is a functional regression: callers cannot scope conversation searches by flow or user.

### F-21: Imperator conversation counters not updated on message store

- **Original feature:** In the main `store_message` flow (`message_pipeline.py` lines 184-195), storing a message updates `conversations.total_messages` and `conversations.estimated_token_count`. This happens inside the transactional insert.
- **New implementation:** The Imperator's `_store_imperator_messages()` in `imperator_flow.py` (lines 365-388) inserts user and assistant messages directly via raw SQL but does NOT update `total_messages` or `estimated_token_count` on the conversations table. It also does not set `token_count` on the inserted messages (the column defaults to NULL).
- **Severity:** minor
- **Notes:** This means the Imperator's conversation will show `total_messages=0` and `estimated_token_count=0` regardless of actual content. This affects the assembly trigger threshold check (which uses `estimated_token_count`) and any status queries. The Imperator's messages also have NULL `token_count`, which propagates into tier calculations that fall back to `len(content) // 4`.

### F-22: Imperator messages not queued for embedding or extraction

- **Original feature:** In Rogers, all messages stored via `conv_store_message` went through the full pipeline: embed, assemble, extract. The Imperator used `conv_store_message` indirectly via its own conversation.
- **New implementation:** The Imperator's `_store_imperator_messages()` writes directly to `conversation_messages` via raw SQL, bypassing the `message_pipeline` flow entirely. No embedding job, no extraction job, and no assembly job is queued for Imperator messages.
- **Severity:** minor
- **Notes:** This means: (1) Imperator messages have no embeddings and won't appear in vector similarity searches. (2) Imperator conversations are never memory-extracted to the knowledge graph. (3) Context windows on the Imperator's conversation never get assembled. The Imperator uses its own history-loading mechanism (last N messages from Postgres) so it functions, but the Imperator's conversation is invisible to the retrieval and knowledge layers. This is arguably intentional for a standalone system (the Imperator is a system-internal conversation), but it differs from Rogers where all messages went through the full pipeline.

### F-23: Assembly lock does not use token-based ownership in Rogers

- **Original feature:** Rogers used a simple `set(key, "1", ex=120, nx=True)` for the assembly lock and `delete(key)` to release it. No ownership verification on release.
- **New implementation:** The Context Broker uses token-based lock ownership: `set(key, lock_token, ex=300, nx=True)` where `lock_token` is a unique UUID. On release, it checks `if current == lock_token` before deleting. This prevents a worker from accidentally releasing a lock that was re-acquired by another worker after TTL expiry.
- **Severity:** not a finding (improvement)
- **Notes:** The new token-based lock is strictly more correct than Rogers' simple flag approach. Rogers could have a race condition where worker A's lock expires, worker B acquires, then worker A finishes and deletes worker B's lock. The new code prevents this.

### F-24: Consolidation does not include existing tier 1 summary in Rogers

- **Original feature:** Rogers' `consolidate_tier1` built consolidation text from only the tier 2 summaries being consolidated: `consolidation_text = "\n\n".join([s["summary_text"] for s in to_consolidate])`. The existing tier 1 was simply deactivated and replaced.
- **New implementation:** The Context Broker's `consolidate_archival_summary` (M-16) explicitly includes the existing tier 1 summary in the consolidation input: it prepends `[Existing archival summary]\n{existing_t1}` before the tier 2 chunks being consolidated. This preserves older archival context that would otherwise be lost when the old tier 1 is replaced.
- **Severity:** not a finding (improvement)
- **Notes:** Rogers' approach caused information loss on each consolidation cycle: the old archival summary was discarded and only the tier 2 chunks were re-summarized. The new approach preserves the chain of archival summaries.

### F-25: Rogers had context_window_id as alternative to conversation_id in store_message

- **Original feature:** Rogers' `conv_store_message` accepted either `context_window_id` or `conversation_id`. If only a context_window_id was provided, the system resolved the conversation_id from the window. This allowed callers to store messages by referencing only the context window.
- **New implementation:** `StoreMessageInput` requires `conversation_id` as a mandatory field. There is no `context_window_id` parameter on the store message tool.
- **Severity:** minor
- **Notes:** This is a minor API simplification. Callers must know their conversation_id, which is the more fundamental identifier. The context window is a derived concept. Any caller that previously used context_window_id would need to resolve the conversation_id first.

### F-26: Rogers sender_id was integer; Context Broker uses string

- **Original feature:** Rogers cast `sender_id` to `int(sender_id)` in the message pipeline invocation, indicating sender_id was treated as a numeric identifier (matching the ecosystem's agent ID system).
- **New implementation:** `sender_id` is a `str` field with `min_length=1, max_length=255` in `StoreMessageInput`. The database column is `VARCHAR(255)`.
- **Severity:** not a finding (intentional State 4 change)
- **Notes:** String sender IDs are more flexible for standalone deployment (can be names, UUIDs, or numeric IDs). The schema supports this correctly.

---

## Previously Identified Minor Findings — Status

These were identified in R2 and remain unchanged. They are intentional architectural simplifications, not regressions requiring fixes.

| R2 Finding | Status |
|---|---|
| F-01: Priority ZSET downgraded to LIST | Accepted simplification |
| F-02: content_type guard removed from extraction | Accepted (broader extraction) |
| F-03: Extraction queued parallel with embedding | Accepted (better parallelism) |
| F-05: Dynamic tier scaling removed | Accepted (config-driven) |
| F-06: Per-build-type LLM selection removed | Accepted (single LLM) |
| F-10: Two-tier extraction LLM routing removed | Accepted (single LLM) |
| F-16: Idempotency not checked for collapsed msgs | Open minor |
| F-17: Assembly lock TTL increased to 300s | Accepted (configurable) |

---

## Summary Table

| # | Finding | Severity | Category |
|---|---------|----------|----------|
| F-19 | Mem0 dedup index not applied at startup | major | missing behavior (carried from R2) |
| F-20 | conv_search missing structured filters | major | regression (carried from R2) |
| F-21 | Imperator conv counters not updated | minor | incomplete implementation |
| F-22 | Imperator messages bypass pipeline | minor | architectural choice |
| F-23 | Token-based assembly lock | n/a | improvement |
| F-24 | Consolidation preserves existing T1 | n/a | improvement |
| F-25 | context_window_id removed from store_message | minor | API simplification |
| F-26 | sender_id changed from int to string | n/a | intentional |

**Blockers:** 0
**Majors:** 2 (F-19, F-20) — both carried from Round 2
**Minors:** 3 new (F-21, F-22, F-25) + carried minors from R2
