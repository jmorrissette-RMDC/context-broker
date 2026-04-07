# Gate 2 Round 7 Pass 3 — Functional Review (Opus)

**Reviewer:** Claude Opus 4.6 (1M context)
**Date:** 2026-03-22
**Scope:** All `.py` files under `app/` (including `build_types/`), `docker-compose.yml`, `postgres/init.sql`, `config/config.example.yml` compared against the original Rogers codebase and REQ-context-broker.md.

Intentional changes from the review prompt are excluded from findings.

---

## Findings

### F-01: conv_get_history does not return tool_calls / tool_call_id fields

- **Original feature:** Rogers `get_history` (via `_serialise_message`) returned all fields from the message row including any tool-related fields present in the schema.
- **New implementation:** `conv_get_history` in `conversation_ops_flow.py` (line 243-263) SELECT only fetches `id, role, sender, recipient, content, sequence_number, token_count, model_name, created_at`. The `tool_calls` and `tool_call_id` columns are omitted from the query.
- **Severity:** major
- **Notes:** Messages with `role=tool` or assistant messages with tool_calls will have incomplete data in history output. The schema has these columns and `conv_store_message` writes them; they should be returned by `conv_get_history` for OpenAI-format completeness.

### F-02: conv_search_context_windows does not return last_accessed_at

- **Original feature:** Rogers `_serialise_context_window` returned `last_assembled_at`. The new schema adds `last_accessed_at` (REQ 3.5.2).
- **New implementation:** `search_context_windows_node` in `conversation_ops_flow.py` (line 315-332) SELECT includes `last_assembled_at` but not `last_accessed_at`. The serialization also only handles `last_assembled_at` and `created_at`.
- **Severity:** minor
- **Notes:** `last_accessed_at` is written on every retrieval (knowledge_enriched.py line 99-102) but never surfaced to callers via this search tool. Passthrough retrieval also does not update `last_accessed_at`.

### F-03: Passthrough retrieval does not update last_accessed_at

- **Original feature:** N/A (new feature). REQ 3.5.2 states context windows include `last_accessed_at`, updated on each retrieval.
- **New implementation:** `ke_load_window` (knowledge_enriched.py line 99-102) updates `last_accessed_at` on retrieval. `pt_load_window` (passthrough.py line 184-211) does NOT update it.
- **Severity:** minor
- **Notes:** Passthrough windows will never have `last_accessed_at` set, breaking dormant window detection for that build type.

### F-04: Memory extraction enqueued parallel with embedding (not after embedding)

- **Original feature:** Rogers `embed_pipeline.py` pipeline was: `fetch_message -> embed_message -> store_embedding -> check_context_assembly -> queue_memory_extraction`. Memory extraction was queued AFTER embedding completed.
- **New implementation:** `message_pipeline.py` `enqueue_background_jobs` (line 234-290) enqueues BOTH `embed_message` and `extract_memory` jobs simultaneously from the store message flow. The embed pipeline (`embed_pipeline.py`) does NOT queue extraction; it only queues context assembly.
- **Severity:** N/A (intentional per review prompt: "Extraction queued parallel with embedding")
- **Notes:** Documented for completeness. The sequencing change is intentional.

### F-05: Rogers memory extraction checked content_type='conversation' guard

- **Original feature:** Rogers `queue_memory_extraction` in `embed_pipeline.py` (line 2596) only queued extraction `if msg.get("content_type") != "conversation" or msg.get("memory_extracted")`. This prevented extraction of non-conversation content types and already-extracted messages.
- **New implementation:** `content_type` field was intentionally removed from the schema (REQ 3.5.1: "Fields not present: content_type"). The `memory_extracted` guard is preserved in `fetch_unextracted_messages` (memory_extraction.py line 99: `memory_extracted IS NOT TRUE`). The new code also filters `role IN ('user', 'assistant')` which achieves similar filtering.
- **Severity:** N/A (covered)
- **Notes:** The `content_type` guard is replaced by the `role IN ('user', 'assistant')` filter, which is a reasonable equivalent. No lost behavior.

### F-06: Rogers Imperator was a JSON-prompting agent; new is a proper ReAct agent

- **Original feature:** Rogers `imperator.py` used a custom JSON-prompting approach where the LLM returned `{"tool": "...", "tool_input": {...}}` JSON that was parsed manually. Tools were invoked via `mcp_client.get_tool()`.
- **New implementation:** `imperator_flow.py` uses LangGraph's `ToolNode` with `bind_tools()` for proper ReAct-style tool calling. Tools are native `@tool` decorated functions.
- **Severity:** N/A (architectural improvement, not lost behavior)
- **Notes:** The new implementation is strictly superior. Tool capabilities are equivalent (conv_search, mem_search).

### F-07: Rogers Imperator had no persistent conversation state

- **Original feature:** Rogers `_invoke_imperator` (server.py line 1351-1361) created a fresh ImperatorGraphState on every call with no persistent conversation_id or context_window. The Imperator had no memory across invocations.
- **New implementation:** `imperator/state_manager.py` manages a persistent `imperator_state.json` with conversation_id and context_window_id. The `store_and_end` node persists messages to PostgreSQL via the standard message pipeline. `agent_node` loads prior history from the DB.
- **Severity:** N/A (improvement, not lost behavior)
- **Notes:** This is a significant capability addition required by REQ 3.2.

### F-08: Rogers dedup was skip-based; new is collapse-based

- **Original feature:** Rogers `message_pipeline.py` deduplicated by skipping consecutive identical messages from the same sender entirely (`deduplicated: True` returned, no row stored).
- **New implementation:** `message_pipeline.py` `store_message` (line 140-171) uses collapse logic: increments `repeat_count` on the existing message instead of inserting a new row. Returns `was_collapsed: True`.
- **Severity:** minor
- **Notes:** Behavioral difference: Rogers discarded duplicates silently; the new code tracks them via `repeat_count`. This is an improvement in information preservation but callers expecting `deduplicated` field will find `was_collapsed` instead. The field name change may affect integrations.

### F-09: Rogers conv_store_message required content to be non-empty

- **Original feature:** Rogers `_invoke_message_pipeline` (server.py line 1297) validated `if not role or sender_id is None or not content`, rejecting messages with null/empty content.
- **New implementation:** `StoreMessageInput` model has `content: Optional[str] = Field(None)` — content is nullable. The store_message node handles null content correctly (line 104: `effective_token_count = max(1, len(content) // 4) if content else 0`).
- **Severity:** N/A (intentional per ARCH-01: nullable content for tool-call messages)
- **Notes:** Schema change documented in REQ 3.5.1: "content (text, nullable)".

### F-10: conv_search filter sender_id renamed to sender

- **Original feature:** Rogers `conv_search` config.json description mentions `sender_id` filter. Rogers `search_conversations` used `sender_id` parameter.
- **New implementation:** `SearchConversationsInput` uses `sender` field (models.py line 76). The `search_conversations_db` in `search_flow.py` filters on `sm.sender` column.
- **Severity:** N/A (schema change: `sender_id` -> `sender` is consistent with ARCH-13)
- **Notes:** Callers using `sender_id` will need to switch to `sender`.

### F-11: Rogers conv_search had sender_id as a direct column filter; new uses EXISTS subquery

- **Original feature:** Rogers presumably filtered by `sender_id` as a column on `conversations` or joined to messages.
- **New implementation:** `search_flow.py` line 141: `EXISTS (SELECT 1 FROM conversation_messages sm WHERE sm.conversation_id = {prefix}id AND sm.sender = ${})`. This is semantically correct.
- **Severity:** N/A (correct implementation)
- **Notes:** Properly uses the indexed `idx_messages_conversation_sender` index.

### F-12: Rogers priority was caller-provided (0-3); new derives from role

- **Original feature:** Rogers `_invoke_message_pipeline` took `priority: int(args.get("priority", 3))` directly from the caller. Priority levels 0-3 mapped to specific urgency tiers used for extraction scoring.
- **New implementation:** `message_pipeline.py` line 31-36: `_ROLE_PRIORITY = {"user": 1, "assistant": 2, "system": 3, "tool": 4}`. Priority is derived from role, not caller-provided. The `StoreMessageInput` still accepts `priority` but it's never used in `store_message` — the role-based derivation overrides it.
- **Severity:** major
- **Notes:** The `priority` field on the Pydantic model (models.py line 45) is accepted from the caller but ignored in the store_message node. The DB column stores the role-derived value. This means: (a) callers who set priority explicitly have no effect, and (b) the priority semantics changed from urgency-based to role-based. Rogers' 4-tier priority scoring for extraction (P0: live user, P1: interactive agent, P2: background agent, P3: bulk migration) is lost.

### F-13: Rogers embed pipeline error on embed still stored the message, skipped assembly

- **Original feature:** Rogers `embed_pipeline.py` had linear edges: `fetch_message -> embed_message -> store_embedding -> check_context_assembly -> queue_memory_extraction`. If `embed_message` failed (error set), the graph went to END via conditional edges, skipping everything downstream.
- **New implementation:** `embed_pipeline.py` `route_after_embed` (line 286-292): if error, goes to END. If embedding is None (null content), goes to `enqueue_context_assembly` (skips store but still queues assembly). If embedding succeeded, goes to `store_embedding -> enqueue_context_assembly`.
- **Severity:** N/A (correct behavior)
- **Notes:** The new code correctly handles null-content messages by skipping embedding storage but still triggering assembly. This is an improvement.

### F-14: Rogers Imperator tools included admin capabilities via direct tool invocation

- **Original feature:** Rogers Imperator had access to `conv_search` and `mem_search` via `mcp_client.get_tool()`. No admin tools (config_read, db_query) existed.
- **New implementation:** Imperator flow includes `_config_read_tool` and `_db_query_tool` gated by `config["imperator"]["admin_tools"]` flag.
- **Severity:** N/A (new feature per REQ 5.5)
- **Notes:** Enhancement, not lost behavior.

### F-15: Schema migration table exists but no versioned migration logic beyond v1

- **Original feature:** Rogers used `init.sql` for initial schema with no explicit migration framework.
- **New implementation:** `migrations.py` exists (not fully read) and is called during startup (`run_migrations()`). The `schema_migrations` table in `init.sql` starts at version 1.
- **Severity:** N/A (placeholder for future migrations)
- **Notes:** REQ 3.7 requires forward-only non-destructive migrations. The framework is present.

### F-16: Memory extraction user_id resolution differs

- **Original feature:** Rogers `mem_extract_route` (server.py line 1531-1566) took `user_id` as an explicit parameter from the caller.
- **New implementation:** `memory_extraction.py` `fetch_unextracted_messages` (line 109-116) derives `user_id` from the first context window's `participant_id` on the conversation. Falls back to `"default"` if no window exists.
- **Severity:** minor
- **Notes:** If a conversation has no context windows (pure API usage without creating windows first), extraction will use `user_id="default"`. In Rogers, the caller always specified the user_id. The new approach is reasonable but may produce unexpected results for conversations that span multiple participants.

### F-17: Rogers had confidence_boost_on_access and confidence_archive_threshold in memory config

- **Original feature:** Rogers `config.json` memory section included `confidence_boost_on_access: 0.15` and `confidence_archive_threshold: 0.1`.
- **New implementation:** `memory_scoring.py` has a hardcoded 30% boost for recently accessed memories (line 60: `score = min(1.0, score * 1.3)`) and a default `min_score=0.1` threshold. These are not configurable via config.yml.
- **Severity:** minor
- **Notes:** The values are similar in effect but are hardcoded rather than configurable. The config.example.yml `memory_half_lives` section covers the half-life values but not the boost/threshold. Low severity because the behavior is preserved, just not tunable.

### F-18: Rogers memory half-life categories differ from new implementation

- **Original feature:** Rogers `config.json` defined categories: `ephemeral, infrastructure, procedural, project, preference, relationship, historical` with `historical: null` (never decays).
- **New implementation:** `memory_scoring.py` DEFAULT_HALF_LIVES: `ephemeral, contextual, factual, historical, default`. Config example: `ephemeral: 3, contextual: 14, factual: 60, historical: 365, default: 30`.
- **Severity:** minor
- **Notes:** Category names changed (infrastructure -> not present, procedural -> not present, project -> not present, preference -> not present, relationship -> not present). Rogers' `historical: null` (infinite) is now `historical: 365`. Memories categorized under Rogers' taxonomy will fall to `default: 30` in the new system. Since Mem0 assigns categories, the impact depends on whether Mem0 uses the same category names. In practice, most memories will likely get `default`.

### F-19: Rogers had separate large/small LLM routing for memory extraction

- **Original feature:** Rogers `config.json` defined `memory_extraction.small_llm_config_key` and `memory_extraction.large_llm_config_key` with different LLM providers for different text sizes.
- **New implementation:** Single LLM per build type (intentional change per review prompt). Memory extraction uses the global LLM via Mem0's own LLM config.
- **Severity:** N/A (intentional: "Single LLM per build type instead of dual small/large routing")

### F-20: MCP tool_calls schema declares type "object" instead of "array"

- **Original feature:** The `tool_calls` field in OpenAI format is an array of tool call objects.
- **New implementation:** `mcp.py` `_get_tool_list()` line 352: `"tool_calls": {"type": "object"}` in the `conv_store_message` inputSchema.
- **Severity:** minor
- **Notes:** The Pydantic model correctly defines `tool_calls: Optional[list[dict]]` (models.py line 47), so runtime validation is correct. Only the MCP schema advertisement is wrong. Clients relying on the advertised schema for validation would see an incorrect type.

### F-21: conv_store_message does not pass conversation_id_input through correctly when using context_window_id

- **Original feature:** Rogers resolved conversation_id from context_window_id in the message pipeline flow.
- **New implementation:** In `tool_dispatch.py` line 206: `"conversation_id_input": str(validated.conversation_id) if validated.conversation_id else None`. In `store_message` (message_pipeline.py line 74-90): resolves from context_window_id first, then falls back to conversation_id_input. But the store_message node does NOT pass conversation_id_input to `enqueue_background_jobs` — the `conversation_id` field is set by `store_message` return value (line 226-230). This works correctly since `conversation_id` is populated after resolution.
- **Severity:** N/A (correct behavior upon closer inspection)

### F-22: Rogers flow_id and user_id not set during Imperator conversation creation

- **Original feature:** Rogers had no persistent Imperator conversation.
- **New implementation:** `state_manager.py` `_create_imperator_conversation` (line 196) creates with only `title`: `INSERT INTO conversations (id, title) VALUES ($1, $2)`. The `flow_id` and `user_id` columns are NULL.
- **Severity:** minor
- **Notes:** The Imperator's conversation won't appear in `conv_search` results filtered by `flow_id` or `user_id`. Consider setting `flow_id="imperator"` and `user_id="imperator"` for discoverability.

### F-23: No HNSW vector index creation in init.sql or migrations

- **Original feature:** Rogers had pgvector working with embeddings (v3.1 re-embedded all 83,588 messages).
- **New implementation:** `init.sql` line 81-83 comments state: "Vector similarity index is created by the application after the first embedding is stored and the dimension is known. See migrations.py." The `embedding` column is declared as untyped `vector` (no dimension).
- **Severity:** N/A (by design)
- **Notes:** The comment references `migrations.py` for dynamic HNSW index creation. This is correct since the embedding dimension depends on the configured model and isn't known at schema creation time.

### F-24: Rogers search_messages included recency_decay; new also includes it

- **Original feature:** Rogers v3.1 hybrid search included recency bias in RRF scoring.
- **New implementation:** `search_flow.py` `hybrid_search_messages` (line 417-436) applies recency bias with configurable `recency_decay_days` and `recency_max_penalty`.
- **Severity:** N/A (preserved)

### F-25: Standard-tiered assembly does not load ALL messages

- **Original feature:** Rogers `load_messages` in `context_assembly.py` loaded ALL messages for the conversation (`await db.get_conversation_messages(state["conversation_id"])`).
- **New implementation:** `standard_tiered.py` `load_messages` (line 144-179) uses an adaptive limit: `adaptive_limit = max(50, tier3_budget // tokens_per_message)`. This loads only enough messages to fill tier3 plus some buffer.
- **Severity:** major
- **Notes:** The adaptive limit is based on tier3_budget, but summarization needs messages OLDER than tier 3. If a conversation has 10,000 messages and tier3 holds the last 50, the adaptive limit might only load ~55 messages, missing the older messages that need summarization. The incremental logic (checking `max_summarized_seq`) mitigates this for conversations that have been assembled before, but for the FIRST assembly of a long conversation, many messages that should be in tier1/tier2 summaries will be missed entirely. Rogers loaded all messages to ensure complete coverage.

### F-26: Imperator store_and_end stores via pipeline but does not pass conversation_id_input

- **Original feature:** N/A (new feature).
- **New implementation:** `imperator_flow.py` `store_and_end` (line 460-475) invokes the message pipeline with `context_window_id` set but `"conversation_id": None`. The pipeline's `store_message` node will look up the conversation_id from the context_window.
- **Severity:** N/A (correct — uses context_window_id path)

---

## Summary

| Severity | Count |
|----------|-------|
| Blocker  | 0     |
| Major    | 2     |
| Minor    | 7     |

**Major findings:**
1. **F-01:** `conv_get_history` omits `tool_calls` and `tool_call_id` from its SELECT query, producing incomplete message data for OpenAI-format consumers.
2. **F-12:** Caller-provided `priority` field is accepted but silently ignored; role-based derivation always overrides it. Rogers' 4-tier urgency-based extraction priority scoring is lost.

**Minor findings:**
1. **F-02:** `conv_search_context_windows` does not return `last_accessed_at`.
2. **F-03:** Passthrough retrieval does not update `last_accessed_at`.
3. **F-08:** Dedup field renamed from `deduplicated` to `was_collapsed` (integration impact).
4. **F-16:** Memory extraction user_id derived from context window instead of caller-supplied.
5. **F-17:** Memory confidence boost and archive threshold are hardcoded, not configurable.
6. **F-18:** Memory half-life categories differ between Rogers and Context Broker.
7. **F-20:** MCP schema for `tool_calls` declares `"type": "object"` instead of `"type": "array"`.
8. **F-22:** Imperator conversation has no `flow_id` or `user_id` set.
9. **F-25:** Standard-tiered assembly adaptive message loading may miss older messages on first assembly of long conversations.

**Note on F-25:** Reclassified as listed under minor but is borderline major. The adaptive limit improves performance for ongoing conversations (incremental assembly covers prior messages), but a first-time assembly on a migrated long conversation would silently under-summarize.
