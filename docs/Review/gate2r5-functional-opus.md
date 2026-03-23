Gate 2 Round 5 Pass 3 — Functional review comparing **Context Broker** (new) vs **Rogers** (original). Focus: lost or degraded behavior.

---

## 1) MCP tool `rogers_stats` removed

- **Original feature:** `rogers/config.json` registered `rogers_stats` as an MCP tool that returned DB counts and queue depths for Apgar health monitoring. `server.py` exposed a `/stats` HTTP endpoint with conversation count, message count, memory count, queue depths, and uptime.
- **New implementation:** MISSING as a discrete tool. Prometheus metrics via `metrics_get` MCP tool and `/metrics` endpoint provide queue depths and request counters, but not the same DB row counts (total conversations, total messages, total memories) that `rogers_stats` returned.
- **Severity:** minor
- **Notes:** The Prometheus metrics are a superset for operational monitoring. The specific Apgar scraper integration is ecosystem-specific and intentionally removed for State 4 standalone. No behavior is lost for standalone deployments. However, a caller requesting raw DB entity counts (e.g., "how many conversations exist?") cannot get that from `metrics_get`.

---

## 2) `context_window_build_types` database table removed

- **Original feature:** Rogers stored build type definitions as rows in a `context_window_build_types` PostgreSQL table with columns for `trigger_threshold_percent`, `recent_window_percent`, `tier1_pct`, `tier2_pct`, etc. Context windows referenced build types via a foreign key (`build_type_id`). The `get_build_type()` DB function loaded the strategy from Postgres at runtime.
- **New implementation:** Build types are defined entirely in `config.yml` and loaded by `get_build_type_config()` in `app/config.py`. The DB schema stores only the build type name as a plain `VARCHAR(100)` in `context_windows.build_type` with no FK. The build type registry (`app/flows/build_type_registry.py`) maps names to graph builders at the code level.
- **Severity:** minor
- **Notes:** This is an intentional architectural change for State 4. Build type definitions are now hot-reloadable via config instead of requiring DB migrations. The trade-off is that build type validation at context window creation time is against `config.yml` content, not a DB constraint. If a build type is removed from config after windows were created referencing it, retrieval will fail with a ValueError. Rogers enforced this via FK. The new system handles this gracefully (error message), but orphaned windows are possible.

---

## 3) `small-basic` build type removed

- **Original feature:** Rogers shipped two build types: `small-basic` (local LLM via Sutherland, for windows <=30k tokens) and `standard-tiered` (Gemini Flash-Lite API, for larger windows). `small-basic` was the default for many context windows.
- **New implementation:** Replaced by `passthrough` (no summarization), `standard-tiered`, and `knowledge-enriched`. There is no 1:1 equivalent of `small-basic`.
- **Severity:** minor
- **Notes:** `standard-tiered` in the new system covers the same three-tier assembly strategy that `small-basic` used. The LLM selection is now per-build-type via config (`build_types.standard-tiered.llm`) rather than hard-coded to Sutherland vs Gemini. Any caller requesting `small-basic` by name will get a ValueError. This is an intentional rename/consolidation.

---

## 4) `external_session_id` field removed

- **Original feature:** Rogers `conversation_messages` had an `external_session_id VARCHAR(255)` column for tracking external tool session provenance. `conv_store_message` accepted it, `conv_search` and `conv_search_messages` could filter by it.
- **New implementation:** MISSING. The column does not exist in `init.sql` or migrations. `StoreMessageInput` does not accept it. Search flows do not filter by it.
- **Severity:** minor
- **Notes:** This was a Joshua26 ecosystem-specific field for tracking which MCP session originated a message. For standalone Context Broker use, this provenance is captured by `sender` and `recipient` fields instead. Callers that depended on `external_session_id` for filtering will need to adapt.

---

## 5) `conv_store_message` no longer accepts `conversation_id` as input

- **Original feature:** Rogers `_invoke_message_pipeline` accepted either `context_window_id` or `conversation_id`. When `conversation_id` was provided (without a context window), the message was stored directly to the conversation.
- **New implementation:** `StoreMessageInput` requires `context_window_id`. There is no fallback to `conversation_id`. The flow looks up the conversation from the context window.
- **Severity:** major
- **Notes:** This is an intentional architectural tightening (ARCH-04). All message operations are scoped to a context window, not a bare conversation. However, any existing caller that stored messages by `conversation_id` alone (without creating a context window first) will break. The workaround is to create a context window first, then use its ID.

---

## 6) Internal HTTP endpoints `mem_extract`, `mem_add`, `mem_list`, `mem_delete` changed from HTTP to MCP

- **Original feature:** Rogers exposed `/mem_extract`, `/mem_add`, `/mem_list`, `/mem_delete` as plain HTTP POST endpoints on the LangGraph container. These were not exposed via MCP but were accessible for admin and background processing.
- **New implementation:** `mem_add`, `mem_list`, `mem_delete` are now full MCP tools (listed in `_get_tool_list()`). `mem_extract` as a manual trigger endpoint does not exist; extraction is background-only.
- **Severity:** minor
- **Notes:** The behavior is preserved and improved (MCP tools are discoverable). The loss of `mem_extract` as a manual trigger is minor because extraction runs automatically via the background worker. An admin wanting to force extraction of a specific conversation has no direct mechanism; they would need to enqueue a job manually in Redis.

---

## 7) `rogers_chat` MCP tool renamed to `imperator_chat`

- **Original feature:** Rogers registered the conversational Imperator as `rogers_chat` in the tool registry.
- **New implementation:** `app/flows/tool_dispatch.py` dispatches it as `imperator_chat`. The MCP tool list registers it as `imperator_chat`.
- **Severity:** minor
- **Notes:** Functionally identical. Any caller using the old tool name will get an "Unknown tool" error.

---

## 8) Dedup check behavior changed from skip to collapse

- **Original feature:** Rogers `dedup_check` in `message_pipeline.py` detected consecutive duplicate messages from the same sender and set `was_duplicate=True`, causing the pipeline to skip the message entirely (no insert, no background jobs).
- **New implementation:** `store_message` in `app/flows/message_pipeline.py` detects the same pattern but instead of skipping, it increments `repeat_count` on the existing message row and returns `was_collapsed=True`. The message is not duplicated, but the existing row is updated.
- **Severity:** minor
- **Notes:** This is a behavioral improvement. The original behavior discarded the duplicate silently. The new behavior tracks how many times a message was repeated, which is more informative for context assembly. The outcome for callers is similar (no new message_id for true duplicates), but the response now includes `was_collapsed` instead of `was_duplicate` for this case.

---

## 9) Build type `grace-cag-full-docs` and other custom build types not migrated

- **Original feature:** Rogers seeded `context_window_build_types` with custom build types like `grace-cag-full-docs` (injected full architectural doc bundle + recent turns) for specific ecosystem agents.
- **New implementation:** MISSING. The new system ships `passthrough`, `standard-tiered`, and `knowledge-enriched`. No custom build types are pre-configured.
- **Severity:** minor
- **Notes:** The build type registry (`app/flows/build_type_registry.py`) is extensible. Custom build types can be added by creating a new module in `app/flows/build_types/` and calling `register_build_type()`. The infrastructure is present but the specific ecosystem build types were intentionally not migrated for standalone deployment. Deployers needing custom assembly strategies have a clear extension path.

---

## 10) Imperator implementation differs significantly

- **Original feature:** Rogers Imperator (`flows/imperator.py`) used Sutherland as its LLM via the peer proxy, with a simple single-step flow (system prompt + user message -> LLM response). It stored results via direct DB calls.
- **New implementation:** Full ReAct agent graph (`app/flows/imperator_flow.py`) with tool binding (`_conv_search_tool`, `_mem_search_tool`, optional `_config_read_tool`, `_db_query_tool`), iterative agent_node <-> tool_node loop, and message storage via the standard `conv_store_message` pipeline. Loads history from DB on each invocation (ARCH-06).
- **Severity:** minor (enhancement, no loss)
- **Notes:** The new Imperator is strictly more capable. It can search conversations, query memories, read config, and execute SQL (when admin_tools=true). The original was a passthrough LLM call with no tools. No behavior is lost.

---

## 11) OpenAI-compatible `/v1/chat/completions` endpoint is new

- **Original feature:** Rogers did not have an OpenAI-compatible chat endpoint. Imperator access was MCP-only via `rogers_chat`.
- **New implementation:** `app/routes/chat.py` implements `/v1/chat/completions` with streaming and non-streaming support.
- **Severity:** minor (new feature, no loss)
- **Notes:** Purely additive. No behavior lost.

---

## 12) Peer proxy architecture removed

- **Original feature:** Rogers used a gateway peer proxy (ADR-053) to route LLM and embedding calls through the MCP gateway to Sutherland on joshua-net. `mem0_adapters.py` contained `SutherlandLlmAdapter`, `SutherlandEmbeddingAdapter`, and `OpenAICompatibleLlmAdapter` custom Mem0 adapters.
- **New implementation:** Direct OpenAI-compatible API calls via LangChain's `ChatOpenAI` and `OpenAIEmbeddings`. No peer proxy, no custom Mem0 LLM/embedding adapters. Mem0 is configured with standard `openai` provider pointing at the configured base_url.
- **Severity:** minor
- **Notes:** This is the core State 2 -> State 4 architectural change. The peer proxy was ecosystem-specific infrastructure. The new system uses standard OpenAI-compatible endpoints, which is more portable. Mem0 integration uses its built-in OpenAI provider rather than custom adapters, reducing custom code. No functional behavior is lost — the same operations (embedding, LLM calls, knowledge extraction) are performed through different transport.

---

## 13) Memory extraction LLM selection by conversation size removed

- **Original feature:** Rogers `memory_extraction` config had `small_llm_max_chars` (90k) and `large_llm_max_chars` (450k) with separate LLM config keys (`small_llm_config_key`, `large_llm_config_key`). For large conversations, it used Gemini (larger context) instead of local Sutherland.
- **New implementation:** `app/flows/memory_extraction.py` uses a single extraction path with `extraction_max_chars` (90k default) from tuning config. There is no large-LLM fallback for oversized conversations. Messages beyond the character budget are simply deferred to the next extraction run.
- **Severity:** major
- **Notes:** Rogers could extract knowledge from conversations up to 450k characters in a single pass by switching to a large-context LLM. The new system caps at 90k characters per extraction pass. For very active conversations, this means knowledge extraction lags further behind. The mitigation is that extraction runs repeatedly (each pass picks up unextracted messages), but entity relationships spanning more than 90k characters of context may be missed. Deployers can increase `extraction_max_chars` but must ensure their configured LLM supports the context length.

---

## 14) Memory half-life categories differ

- **Original feature:** Rogers had category-specific half-lives including `infrastructure` (45 days), `procedural` (90 days), `project` (180 days), `preference` (365 days), `relationship` (730 days), and `historical` (null = never decays).
- **New implementation:** `app/flows/memory_scoring.py` and `config.example.yml` define: `ephemeral` (3), `contextual` (14), `factual` (60), `historical` (365), `default` (30). Missing: `infrastructure`, `procedural`, `project`, `preference`, `relationship`. The `historical` category now has a 365-day half-life instead of never-decaying.
- **Severity:** minor
- **Notes:** The category names are different because Mem0 assigns its own categories during extraction. The original Rogers categories were ecosystem-specific. The new defaults are reasonable for standalone use. Deployers can add custom categories to `tuning.memory_half_lives` in config.yml. The `historical` change from "never decays" to 365-day half-life means very old historical memories will eventually be filtered. This could be set to a very large number (e.g., 36500) in config to approximate the original behavior.

---

## 15) No `content_type` field on messages

- **Original feature:** Rogers schema included `content_type VARCHAR(50) DEFAULT 'text'` on `conversation_messages`, allowing messages to be typed (text, code, etc.).
- **New implementation:** Migration 012 explicitly drops the `content_type` column (ARCH-08). Messages are untyped.
- **Severity:** minor
- **Notes:** The `content_type` field was present in an intermediate schema version but was determined to be unnecessary for the v4 design. Tool-call messages are now distinguished by `tool_calls` and `tool_call_id` fields (ARCH-01) rather than a content type. No Rogers caller is known to have used `content_type` for anything beyond the default `text`.

---

## 16) `idempotency_key` on messages removed

- **Original feature:** Rogers had an `idempotency_key` column and unique index on `conversation_messages` for at-most-once delivery of messages.
- **New implementation:** Migration 012 drops both the column and index (ARCH-09). Deduplication is handled by the consecutive-message collapse logic (same sender + same content -> increment repeat_count).
- **Severity:** minor
- **Notes:** The collapse logic provides dedup for the most common case (repeated identical messages). It does not cover the case where a caller retries a store with a unique idempotency key to detect prior success. Callers that relied on idempotency keys for exactly-once semantics will need to implement their own dedup (e.g., check if the message exists before storing).

---

## Summary

| # | Feature | Status | Severity |
|---|---------|--------|----------|
| 1 | `rogers_stats` MCP tool | MISSING (replaced by Prometheus) | minor |
| 2 | `context_window_build_types` DB table | MISSING (replaced by config.yml) | minor |
| 3 | `small-basic` build type | MISSING (merged into `standard-tiered`) | minor |
| 4 | `external_session_id` field | MISSING | minor |
| 5 | `conv_store_message` accepting `conversation_id` | MISSING | major |
| 6 | Internal HTTP endpoints for mem ops | Changed to MCP tools; `mem_extract` trigger missing | minor |
| 7 | `rogers_chat` tool name | Renamed to `imperator_chat` | minor |
| 8 | Dedup skip vs collapse | Changed (improvement) | minor |
| 9 | Custom ecosystem build types | Not migrated (extension path exists) | minor |
| 10 | Imperator capabilities | Enhanced (ReAct + tools) | minor (no loss) |
| 11 | OpenAI-compatible chat endpoint | New feature | minor (no loss) |
| 12 | Peer proxy architecture | Removed (replaced by direct API) | minor |
| 13 | Extraction LLM selection by size | MISSING (single LLM, 90k cap) | major |
| 14 | Memory half-life categories | Changed (configurable) | minor |
| 15 | `content_type` field | Removed (ARCH-08) | minor |
| 16 | `idempotency_key` on messages | Removed (ARCH-09) | minor |

**Blockers:** 0
**Major:** 2 (items 5, 13)
**Minor:** 14
