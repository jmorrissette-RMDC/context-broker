# CLI Discovery Audit — Context Broker CEA (SDLC-02 Step 8, WPR-103 S1a)

## Scope
- Codebase: `state_4_development/context_broker_pmad/`
- Files read: all `.py` in `app/`, `packages/context-broker-ae/src/context_broker_ae/`, `packages/context-broker-te/src/context_broker_te/`, `alerter/`, `log_shipper/`; `packages/mem0-fork/mem0/memory/{main.py,graph_memory.py,quality_gate.py,expiration.py}`; `postgres/init.sql`; all `.md` in `config/prompts/` and `config-test/prompts/`.
- Tests checked for coverage: `tests/claude/` (unit + live suites as described in `tests/claude/TEST_PLAN.md`).

## 1. Functions (public API surface)
Format per row: Function | What it does | Inputs/Outputs | Error paths | Tests needed | Coverage in `tests/claude`

### app/
| Function | What it does | Inputs/Outputs | Error paths | Tests needed | Coverage in tests/claude |
|---|---|---|---|---|---|
| `app.budget.snap_budget` | Snap requested token budget to nearest bucket (ceil). | in: int requested; out: int bucket. | None. | bucket boundary tests (exact, between, > max). | Covered by `tests/claude/test_config` + live phase H (budget snapping). |
| `app.config.invalidate_config_cache` | Forces next config read from disk. | no args; out: None. | None. | cache invalidation + subsequent read uses new file. | Covered in `tests/claude/test_config/test_config_gaps.py`. |
| `app.config.load_config` | Load AE config with mtime+hash cache. | in: none; out: dict. | File missing, YAML parse, bad top-level type. | missing file, bad YAML, cache hit/miss. | Covered in `tests/claude/test_config`. |
| `app.config.async_load_config` | Async merged AE+TE load with mtime cache. | in: none; out: dict. | file missing, YAML parse, bad type; executor errors. | hot-reload TE, AE cache hit, missing TE. | Covered in `tests/claude/test_config`, live phase H. |
| `app.config.load_te_config` / `async_load_te_config` | Load TE config with cache. | in: none; out: dict. | file missing, YAML parse. | TE missing, malformed, cache hit/miss. | Covered in `tests/claude/test_config`. |
| `app.config.load_merged_config` | Merge AE + TE (TE overrides). | in: none; out: dict. | TE missing handled. | TE absent fallback, overlay precedence. | Covered in `tests/claude/test_config`. |
| `app.config.get_api_key` | Resolve API key via credentials file or env. | in: provider_config; out: str (possibly empty). | credentials file read failures. | precedence test, missing env key. | Covered in `tests/claude/test_config`. |
| `app.config.get_build_type_config` | Validate build type config and tier allocation. | in: config dict, build type name; out: build type dict. | missing build type, invalid utilization. | error on invalid allocation, valid configs. | Covered in `tests/claude/test_config`. |
| `app.config.get_tuning` | Read tuning param from tuning/workers/locks. | in: config, key, default; out: Any. | None. | precedence order, default fallback. | Covered in `tests/claude/test_config`. |
| `app.config.get_log_level` | Return log level. | in: config; out: str. | None. | default INFO, casing. | Covered in `tests/claude/test_logging`. |
| `app.config.verbose_log` / `verbose_log_auto` | Conditional logging when verbose enabled. | in: config, logger, message. | config load errors (auto). | no-op on disabled; handles bad YAML. | Covered in `tests/claude/test_logging/test_logging_gaps.py`. |
| `app.config.get_chat_model` | Cache ChatOpenAI clients by role. | in: config, role, streaming; out: ChatOpenAI. | missing provider config, cache eviction. | cache hit/miss, streaming flag, eviction. | Partial in `tests/claude/test_config` (mock), no streaming coverage. |
| `app.config.get_embeddings_model` | Cache OpenAIEmbeddings by config. | in: config, config_key; out: OpenAIEmbeddings. | bad dims, cache eviction. | dims handling, cache reuse. | Partial in `tests/claude/test_config`. |
| `app.config.validate_cea_config` | Validate CEA config (REQ-CEA-A05). | in: config; out: None. | invalid ranges / types. | invalid retrieval_scope, thresholds, ceac limits. | **Not covered** in tests/claude. |
| `app.database.init_postgres` | Initialize asyncpg pool. | in: config; out: pool. | connection failure. | success + reinit closes previous pool. | Covered in `tests/claude/test_database/test_database_gaps.py` + live phase A. |
| `app.database.get_pg_pool` | Return pool or error. | out: pool. | RuntimeError if not init. | error path and success. | Covered in `tests/claude/test_database`. |
| `app.database.close_all_connections` | Close pool. | out: None. | OSError from pool close. | close with/without pool. | Covered in `tests/claude/test_lifecycle`. |
| `app.database.check_postgres_health` | Health probe via SELECT 1. | out: bool. | Postgres errors, pool missing. | failure -> false. | Covered in live phase A. |
| `app.database.check_neo4j_health` | HTTP probe to Neo4j. | out: bool. | http errors. | auth header, failure -> false. | Covered in live phase A. |
| `app.logging_setup.JsonFormatter.format` | JSON log formatting. | in: LogRecord; out: str. | None. | JSON fields present. | Covered in `tests/claude/test_logging`. |
| `app.logging_setup.HealthCheckFilter.filter` | Filter /health logs. | in: LogRecord; out: bool. | None. | log suppression behavior. | Covered in `tests/claude/test_logging`. |
| `app.logging_setup.setup_logging` / `update_log_level` | Configure logging and apply log level. | in: log level; out: None. | invalid level. | update after config load. | Covered in `tests/claude/test_logging`. |
| `app.main._postgres_retry_loop` | Background retry for Postgres + Imperator init. | in: app, config; out: None. | asyncpg errors. | retry success/failure, imperator reinit. | Covered in `tests/claude/test_lifecycle`. |
| `app.main.lifespan` | Startup/shutdown lifecycle. | in: app; out: context manager. | config errors, invalid build types, embedding dims missing. | startup failfast, degraded mode, shutdown cancels tasks. | Covered in `tests/claude/test_lifecycle` + live phase A/H. |
| `app.main.check_postgres_middleware` | 503 when Postgres down (except /health,/metrics). | in: Request; out: Response. | None. | exempt paths, non-exempt. | Covered in `tests/claude/test_transport`. |
| `app.main.http_exception_handler` / `validation_exception_handler` / `known_exception_handler` | Structured error responses. | in: Request, exc; out: JSONResponse. | None. | error code mapping. | Covered in `tests/claude/test_transport`. |
| `app.prompt_loader.load_prompt` / `async_load_prompt` | Prompt loading with cache. | in: name; out: str. | File missing, read errors. | cache hit/miss, file change. | Covered in `tests/claude/test_config` + `test_imperator`. |
| `app.stategraph_registry.scan` | Discover AE/TE entry points; register flows. | out: dict of packages. | Import/load errors. | hot-reload evict, TE flow registration. | Covered in live phase H; **TE flow scanning not explicitly covered**. |
| `app.stategraph_registry.get_flow_builder` / `get_imperator_builder` | Return builders from registry. | out: callable/None. | None. | builder lookup. | Covered in `tests/claude/test_imperator`. |
| `app.stategraph_registry.get_package_metadata` / `is_loaded` | Package metadata and loaded state. | out: dict/bool. | None. | metadata after scan. | Not explicitly covered. |
| `app.token_budget.resolve_token_budget` | Resolve budget (override, fixed, auto). | in: config, build_type_config, override; out: int. | invalid values. | override precedence, auto fallback. | Covered in `tests/claude/test_config` + live phase H. |
| `app.token_budget._query_provider_context_length` | Query provider /models for context length. | out: int. | http errors, missing model. | fallback path, 401 handling. | Not covered. |
| `app.utils.stable_lock_id` | Deterministic advisory lock ID. | in: str; out: int. | None. | determinism, range. | Covered in `tests/claude/test_workers`. |
| `app.flows.build_type_registry.register_build_type` | Register build type builders. | in: name, builders. | None. | list/register, overwrite. | Covered in `tests/claude/test_assembly`. |
| `app.flows.build_type_registry.get_assembly_graph` / `get_retrieval_graph` | Compile & cache graphs. | in: build type; out: compiled graph. | missing build type. | lazy compile, cache hit, missing build type. | Covered in `tests/claude/test_assembly`. |
| `app.flows.build_type_registry.list_build_types` / `clear_compiled_cache` | Inspect/clear registry cache. | out: list/None. | None. | cache cleared. | Not explicitly covered. |
| `app.flows.imperator_wrapper.invoke_with_metrics` | Invoke Imperator flow with metrics. | in: state; out: state. | flow errors. | increments metrics, error path. | Covered in live phase F + test_imperator. |
| `app.flows.imperator_wrapper.astream_events_with_metrics` | Stream Imperator events + metrics. | in: state; out: async generator. | streaming errors. | stream + fallback. | Covered in live phase F; streaming fallback in `test_transport`. |
| `app.flows.install_stategraph.install_stategraph` | pip install package + rescan registry. | in: package, version; out: result. | pip failure, record failure. | install success/fail. | Covered in live phase C. |
| `app.flows.tool_dispatch.dispatch_tool` | Validate inputs and route to flow. | in: tool, args, config; out: dict. | ValueError, runtime errors. | each tool success/error. | Covered in live phases B/C and mock tests in `tests/claude/test_tools` + `test_search`. |
| `app.routes.mcp.mcp_sse_session` | SSE session creation for MCP. | in: Request; out: StreamingResponse. | None. | session TTL, queue cap, keepalive. | Covered in `tests/claude/test_transport/test_sse_sessions.py`. |
| `app.routes.mcp.mcp_tool_call` | MCP JSON-RPC tool calls. | in: Request; out: JSONResponse. | invalid JSON, invalid method, tool errors. | parse errors, session not found, queue full. | Covered in `tests/claude/test_transport` + live phase A/B/C. |
| `app.routes.mcp._get_tool_list` | Tool list schema. | out: list[dict]. | config load errors tolerated. | schema includes new knowledge_* tools. | Covered in `tests/claude/test_tools/test_operational_tools.py` (partial). |
| `app.routes.chat.chat_completions` | OpenAI-compatible chat endpoint. | in: Request; out: JSON/SSE. | invalid JSON, validation error, config errors. | streaming + non-streaming. | Covered in `tests/claude/test_transport/test_chat_extras.py` + live phase F. |
| `app.routes.health.health_check` | Health endpoint (flow-based). | out: JSONResponse. | config load failure returns degraded. | healthy/unhealthy + degraded. | Covered in live phase A. |
| `app.routes.metrics.get_metrics` | Prometheus metrics endpoint. | out: Response. | flow error -> 500. | metrics success + failure path. | Covered in live phase I. |
| `app.routes.caller_identity.resolve_caller` | Determine caller identity. | in: Request, user field; out: str. | reverse DNS errors. | user field precedence. | Covered in `tests/claude/test_transport`. |
| `app.workers.db_worker.start_background_worker` | Start embedding/extraction/assembly/log loops. | in: config; out: None. | loop cancellations, DB errors. | start/stop, backoff, partial failures. | Covered in `tests/claude/test_workers`. |
| `app.imperator.state_manager.ImperatorStateManager` methods | Manage persistent Imperator convo/window IDs. | in: config; out: ids/None. | file read/write errors, DB missing. | create/read state, missing conversation, file corruption. | Covered in `tests/claude/test_state_manager`. |

### packages/context-broker-ae (selected key public functions)
| Function | What it does | Inputs/Outputs | Error paths | Tests needed | Coverage |
|---|---|---|---|---|---|
| `context_broker_ae.register.register` | Register AE build types and flows. | out: dict with build_types, flows. | import errors. | registration contents, missing flow. | Covered in live phase H. |
| `context_broker_ae.build_types.passthrough.build_passthrough_*` | Build passthrough assembly/retrieval graphs. | in: state; out: state. | DB errors, lock failure. | lock acquire/release, no-op assembly. | Covered in live phase E. |
| `context_broker_ae.build_types.standard_tiered.*` | Standard tiered assembly/retrieval nodes. | in: state; out: state. | DB errors, LLM errors, lock timeouts. | compaction thresholds, tier transitions, summarization errors. | Covered in live phase E + `tests/claude/test_assembly`. |
| `context_broker_ae.build_types.knowledge_enriched.*` | Knowledge-enriched retrieval (semantic + KG). | in: state; out: state. | embedding/LLM errors, KG failures. | semantic retrieval, KG injection, distillation. | Covered in live phase E (partial), **CEA additions not covered**. |
| `context_broker_ae.build_types.tier_scaling.extract_deadband_config` | Read tier allocation config. | in: build type config; out: dict. | None. | correct defaults. | Covered in `tests/claude/test_assembly`. |
| `context_broker_ae.message_pipeline.store_message` | Insert message and update counters. | in: state; out: state. | DB errors, invalid IDs. | message insert, tool call fields, priority. | Covered in live phase B/C, `tests/claude/test_workers`. |
| `context_broker_ae.embed_pipeline.build_embed_pipeline` + nodes | Fetch message, embed, store. | in: state; out: state. | OpenAI/httpx errors, null content. | batch embedding, timeouts. | Covered in live phase D + `tests/claude/test_workers`. |
| `context_broker_ae.search_flow.build_*` + nodes | Conversation/message search + rerank. | in: state; out: results. | embedding errors, rerank API errors. | vector search, BM25, RRF, rerank fallback. | Covered in `tests/claude/test_search` + live phase B. |
| `context_broker_ae.conversation_ops_flow.build_*` + nodes | CRUD conv/window/history/logs. | in: state; out: records. | DB errors, invalid IDs. | create/rename/delete, list/filter. | Covered in live phase C + `tests/claude/test_database`. |
| `context_broker_ae.memory_extraction.build_memory_extraction` + nodes | Extract facts via Mem0. | in: state; out: counts. | lock fail, LLM errors, Mem0 errors. | extraction text chunking, redaction. | Covered in live phase D + `tests/claude/test_memory`. |
| `context_broker_ae.memory_search_flow.build_*` + nodes | Mem0 search + context formatting. | in: state; out: memories/relations. | Mem0 errors. | degraded path, ranking. | Covered in live phase B/C (knowledge search). |
| `context_broker_ae.memory_admin_flow.build_*` + nodes | Mem0 add/list/delete flows. | in: state; out: results. | Mem0 errors. | add/list/delete. | Covered in live phase C. |
| `context_broker_ae.memory_scoring.score_memory` / `filter_and_rank_memories` | Apply decay & ranking. | in: memories; out: ranked list. | None. | decay correctness. | Covered in `tests/claude/test_search/test_search_internals.py`. |
| `context_broker_ae.memory.mem0_client.get_mem0_client` | Initialize Mem0 with config. | out: Memory instance. | missing dims, config errors. | singleton reuse, reset. | Covered in `tests/claude/test_memory/test_memory_integration.py`. |
| `context_broker_ae.memory.quality_wrapper` (CEA) | Quality wrapper around Mem0. | in: content/query; out: enriched results. | asyncpg errors, Mem0 errors. | dedup, metadata write, feedback, expiration cleanup. | **Not covered** in tests/claude. |
| `context_broker_ae.cea_extraction_flow.build_cea_extraction_flow` (CEA) | CEAs extraction StateGraph. | in: tier1 content; out: dispatch counts. | JSON parse errors, LLM errors, wrapper errors. | LLM JSON validity, dispatch relationships. | **Not covered** in tests/claude. |

### packages/context-broker-te
| Function | What it does | Inputs/Outputs | Error paths | Tests needed | Coverage |
|---|---|---|---|---|---|
| `context_broker_te.register.register` | Register Imperator + CEAc flow. | out: dict. | import errors. | tools_required list includes knowledge_* tools. | Covered in live phase H (partial). |
| `context_broker_te.imperator_flow.build_imperator_flow` | Build Imperator ReAct graph. | in: config; out: compiled graph. | tool binding errors. | tool loop, max iteration fallback. | Covered in live phase F/G + `tests/claude/test_imperator`. |
| `context_broker_te.imperator_flow.init_context_node` | Load prompt, get_context, optional CEAc. | in: state; out: state. | prompt load failure, get_context errors. | CEAc integration paths. | Partially covered in live phase F; **CEAc not covered**. |
| `context_broker_te.imperator_flow.llm_call_node` | LLM call with tools. | in: state; out: state. | OpenAI/httpx errors, empty responses. | tool call vs content, retry on empty. | Covered in live phase F. |
| `context_broker_te.imperator_flow.store_user_message/store_assistant_message` | Persist messages via tool. | in: state; out: state. | dispatch_tool errors, empty content. | skip duplicate, fallback text behavior. | Covered in live phase F. |
| `context_broker_te.cea_enrichment_flow.build_ceac_enrichment_flow` (CEA) | CEAc ReAct enrichment subgraph. | in: tiers, query, tool fns; out: enriched_context. | search_fn errors, feedback_fn errors. | ranking logic, exploration, feedback recording. | **Not covered** in tests/claude. |
| `context_broker_te.domain_mem0.get_domain_mem0` | Domain Mem0 singleton. | out: Memory or None. | config invalid, missing dims. | config hash change, init failure. | Covered in `tests/claude/test_imperator/test_domain_knowledge_seeding.py` (partial). |
| `context_broker_te.seed_knowledge.seed_domain_knowledge` | Seed domain_information. | out: count. | DB errors, embedding errors. | seed with/without embeddings. | Covered in `tests/claude/test_imperator`. |
| TE tools (admin/alerting/diagnostic/filesystem/notify/operational/system/web) | Imperator tool functions. | in: tool params; out: str. | DB/HTTP/fs errors. | success + invalid inputs. | Covered in `tests/claude/test_tools` + live phase G. |

### packages/mem0-fork/mem0/memory
| Function | What it does | Inputs/Outputs | Error paths | Tests needed | Coverage |
|---|---|---|---|---|---|
| `mem0.memory.main.Memory.add` | Extract/store memory (vector+graph). | in: messages, ids, metadata; out: dict results. | validation, embed, vector/graph errors. | _skip_graph path, dedup, invalid inputs. | **Not covered** in tests/claude (Mem0 treated as black box). |
| `mem0.memory.main.Memory.search/get/get_all/update/delete/delete_all/history/reset` | Core Mem0 operations. | inputs per method; outputs dict/list. | vector store errors, missing ids. | success/error paths, graph search on/off. | **Not covered** in tests/claude. |
| `mem0.memory.graph_memory.MemoryGraph.*` | Neo4j entity extraction, search, add, delete. | in: text, filters; out: dict/list. | LLM tool errors, Neo4j query errors. | elementId mapping, threshold behavior. | **Not covered** in tests/claude. |
| `mem0.memory.quality_gate.*` | Side-channel metadata extraction + rejection. | in: facts/meta; out: filtered lists. | invalid regex, invalid expires_at. | rejects too-short, regex, bad date. | **Not covered** in tests/claude. |
| `mem0.memory.expiration.*` | Expiration cleanup + filtering. | in: vector_store, db; out: deleted count. | unsupported list filters, DB errors. | cleanup interval, idempotent cleanup. | **Not covered** in tests/claude. |

### alerter/
| Function | What it does | Inputs/Outputs | Error paths | Tests needed | Coverage |
|---|---|---|---|---|---|
| `alerter._startup/_shutdown` | Load config, connect Postgres. | none. | asyncpg errors. | retry behavior. | Covered in `tests/claude/test_alerter`. |
| `alerter.webhook` | CloudEvents webhook → instruction → channel fanout. | in: Request; out: JSONResponse. | invalid JSON, missing type, channel errors. | idempotency, default channels, instruction match. | Covered in `tests/claude/test_alerter`. |
| `alerter._find_instruction` | Vector or text search over instructions. | in: event; out: dict/None. | embed errors, DB errors. | vector search + fallback. | Covered in `tests/claude/test_alerter_logic`. |
| `alerter._send_*` | Channel delivery (slack/discord/ntfy/smtp/twilio/webhook). | in: config, message; out: None. | http/smtp errors. | missing config validation. | Covered in `tests/claude/test_alerter_logic`. |

### log_shipper/
| Function | What it does | Inputs/Outputs | Error paths | Tests needed | Coverage |
|---|---|---|---|---|---|
| `log_shipper.LogShipper.setup` | Connect Postgres + Docker, discover network. | out: None. | Docker errors, Postgres errors. | network discovery fallback. | Covered in `tests/claude/test_log_shipper`. |
| `log_shipper.LogShipper.tail_container` | Tail Docker logs into queue. | in: container id; out: None. | parse errors, queue full. | JSON log parsing, timestamp parse. | Covered in `tests/claude/test_log_shipper`. |
| `log_shipper.LogShipper.postgres_writer_loop` | Batch write logs to DB. | out: None. | DB errors, timeout. | batch flush conditions. | Covered in `tests/claude/test_log_shipper`. |
| `log_shipper.LogShipper.event_watcher_loop` | Watch Docker network connect/disconnect. | out: None. | Docker errors. | task start/stop on connect/disconnect. | Covered in `tests/claude/test_log_shipper`. |

## 2. MCP endpoints/tools (schema + dispatch + error handling)
Tools list is defined in `app.routes.mcp._get_tool_list`, dispatched in `app.flows.tool_dispatch._dispatch_tool_inner`.

| Tool | Schema highlights | Dispatch path | Error handling | Tests needed | Coverage |
|---|---|---|---|---|---|
| `get_context` | build_type (enum), budget (int), conversation_id?, user_prompt?, model?, domain_context? | `tool_dispatch` → flow `get_context` | ValueError on flow error. | new v2 params; domain_context injection. | Covered in live phase B; no explicit domain_context test. |
| `store_message` | conversation_id, role, sender, content?, tool_calls? | `message_pipeline` flow | validation errors. | tool_calls/tool_call_id persistence. | Covered in live phase B/C. |
| `search_messages` | query required, conversation_id?, role?, sender? | `message_search` flow | flow error. | rerank optional. | Covered in live phase B + `test_search`. |
| `conv_create_conversation` | optional conversation_id/title/flow_id/user_id | `create_conversation` flow | flow error. | idempotent create. | Covered live phase C. |
| `conv_delete_conversation` | conversation_id | `delete_conversation` flow | flow error. | cascade delete. | Covered live phase C. |
| `conv_rename_conversation` | conversation_id/title | `rename_conversation` flow | flow error. | rename no-op. | Covered live phase C. |
| `conv_list_conversations` | participant?, limit/offset | direct SQL in `tool_dispatch` | asyncpg errors. | participant filter vs no filter. | Covered live phase C + `tests/claude/test_database`. |
| `conv_store_message` | context_window_id? or conversation_id? | `message_pipeline` flow | validation error if both missing. | context window resolution path. | Covered live phase C. |
| `conv_retrieve_context` | context_window_id | build_type lookup → retrieval graph | missing window/build_type. | retrieval warnings surfaced. | Covered live phase C/E. |
| `conv_create_context_window` | conversation_id, participant_id, build_type, max_tokens? | `create_context_window` flow | flow error. | budget override. | Covered live phase C. |
| `conv_search` | query?, limit/offset, filters | `conversation_search` flow | flow error. | date range filters. | Covered live phase C. |
| `conv_search_messages` | query, filters | `message_search` flow | flow error. | filter combinations. | Covered live phase C. |
| `conv_get_history` | conversation_id, limit? | `get_history` flow | flow error. | ordering, limit. | Covered live phase C. |
| `conv_search_context_windows` | filters | `search_context_windows` flow | flow error. | filter combos. | Covered live phase C. |
| `query_logs` | container, level, since/until, keyword | SQL in `tool_dispatch` | asyncpg errors. | filter combinations. | Covered live phase I. |
| `search_logs` | query required, filters | embed query + SQL vector search | missing log_embeddings config. | semantic search with vector. | Covered live phase I. |
| `knowledge_search` (CEA) | query, user_id?, limit | quality_wrapper.search | wrapper errors. | global search path + graph fallback. | **Not covered** in tests/claude. |
| `knowledge_add` (CEA) | content, user_id, conversation_id?, durability?, confidence?, source_type?, original_utterance? | quality_wrapper.add + write_metadata | wrapper rejects; metadata write errors. | dedup, quality gate, metadata. | **Not covered** in tests/claude. |
| `knowledge_list` (CEA) | user_id?, limit | quality_wrapper.list_facts | wrapper errors. | global list direct pgvector query. | **Not covered** in tests/claude. |
| `knowledge_feedback` (CEA) | target_type/id, event_type, agent_id, context? | quality_wrapper.record_feedback | asyncpg errors, dedup. | duplicate event idempotency. | **Not covered** in tests/claude. |
| `imperator_chat` | message, context_window_id? | TE Imperator flow | flow error. | streaming and non-streaming. | Covered live phase F. |
| `metrics_get` | no args | metrics flow | flow error. | metrics output. | Covered live phase I. |
| `install_stategraph` | package_name, version? | install_stategraph + cache invalidation | pip errors. | invalid package. | Covered live phase C. |

## 3. Pipeline stages (StateGraph nodes)
Each node requires tests for success + failure + edge conditions. Coverage references tests/claude (live + mock).

### CEA (new)
| Flow | Node | What it does | Failure modes | Tests needed | Coverage |
|---|---|---|---|---|---|
| `cea_extraction_flow` | `search_existing_facts` | query quality wrapper for existing facts by scope | wrapper errors, user_id extraction failure | scope=user/global/conversation; tier2 query logic | **Not covered** |
| `cea_extraction_flow` | `run_extraction_llm` | call LLM with `cea_vector_extraction` prompt | JSON parse error, invalid schema, LLM error | invalid JSON, missing keys, structured output validity | **Not covered** |
| `cea_extraction_flow` | `dispatch_results` | add facts to Mem0 via wrapper, write metadata, feedback on supersedes/conflicts | wrapper add failure, metadata insert errors | relationship handling, dedup by utterance | **Not covered** |
| `cea_extraction_flow` | `handle_error` | logs error and returns counts | none | error path coverage | **Not covered** |
| `cea_enrichment_flow` | `decide_search` | LLM-driven query selection for CEAc | LLM errors, DONE path | iterative query behavior | **Not covered** |
| `cea_enrichment_flow` | `execute_search` | call injected search_fn | search errors | user_id propagation, dedup results | **Not covered** |
| `cea_enrichment_flow` | `evaluate_and_rank` | ranking and budget enforcement | none | trustworthiness threshold, exploration | **Not covered** |
| `cea_enrichment_flow` | `assemble_context` | format results with template | prompt load failures | output template fallback | **Not covered** |
| `cea_enrichment_flow` | `record_feedback` | send used/discarded events | feedback errors | idempotency, partial failures | **Not covered** |

### Core AE flows (selected)
| Flow | Node | What it does | Failure modes | Tests needed | Coverage |
|---|---|---|---|---|---|
| `message_pipeline` | `store_message` | insert message + update counters | DB errors | tool_calls, sequence_number. | Covered live phase B/C. |
| `embed_pipeline` | `fetch_message` | load message row | missing message | missing id. | Covered live phase D. |
| `embed_pipeline` | `generate_embedding` | call embeddings model | timeout, OSError, API errors | timeout path + retry. | Covered live phase D. |
| `embed_pipeline` | `store_embedding` | update embedding column | DB errors | vector formatting. | Covered live phase D. |
| `conversation_search` | `embed_conversation_query` | embed query | embed errors | embedding None path. | Covered live phase B. |
| `conversation_search` | `search_conversations_db` | SQL search | DB errors | filters, ordering. | Covered live phase B/C. |
| `message_search` | `hybrid_search_messages` | vector + BM25 + RRF | DB errors | tsvector and vector. | Covered live phase B + `test_search`. |
| `memory_extraction` | `build_extraction_text` | chunk and clean text | invalid input | chunking, redaction. | Covered in `tests/claude/test_memory`. |
| `memory_extraction` | `run_mem0_extraction` | Mem0 add | Mem0 errors | _skip_graph path, latency. | Covered in `tests/claude/test_memory`. |
| `metrics_flow` | `collect_metrics` | dump Prometheus registry | registry errors | metrics output. | Covered live phase I. |
| `health_flow` | `check_dependencies` | postgres+neo4j health | network errors | degraded status. | Covered live phase A. |
| `build_types.standard_tiered` | `acquire_assembly_lock` | Postgres advisory lock | lock fail | concurrent assembly. | Covered `test_assembly`. |
| `build_types.standard_tiered` | `calculate_compaction_state` | compute tier boundaries | invalid config | tier allocations. | Covered `test_assembly`. |
| `build_types.standard_tiered` | `compact_tier1` | summarize tier1 and store tier2 | LLM errors | chunk summaries. | Covered live phase E. |
| `build_types.standard_tiered` | `run_full_compaction` | consolidate archival | LLM errors | consolidation logic. | Covered live phase E. |
| `build_types.standard_tiered` | `finalize_assembly` | persist last_assembled_at, metrics | DB errors | last_assembled_at. | Covered live phase E. |
| `build_types.knowledge_enriched` | `inject_semantic_retrieval` | semantic search for query | embeddings errors | top-k logic. | Partial in live phase E. |
| `build_types.knowledge_enriched` | `inject_knowledge_graph` | Mem0 graph retrieval | Mem0 errors | graph format. | Partial in live phase E. |

## 4. Worker behaviors
| Worker loop | Polling logic | Retry behavior | Failure modes | Tests needed | Coverage |
|---|---|---|---|---|---|
| DB embedding worker (`_embedding_worker`) | Polls messages with `embedding IS NULL` every `worker_poll_interval_seconds` | timeout on embedding, backoff after failures; poison pill after 5 failures writes zero-vector | embedding model errors, DB errors, HNSW creation failure | timeout + poison pill, HNSW creation, batch size. | Covered in `tests/claude/test_workers/test_db_worker.py` + live phase D. |
| DB extraction worker (`_extraction_worker`) | Polls messages with `memory_extracted IS NOT TRUE` | per-conversation retries (max 3), then mark extracted true | Mem0 errors, lock failure, timeout | conv retry cap, advisory lock concurrency. | Covered in `tests/claude/test_workers` + live phase D. |
| DB assembly check (`_check_assembly_needed`) | triggers assembly when new tokens > threshold | no retry; catches timeouts | assembly timeout, graph errors | threshold logic, last_assembled_at. | Covered in `tests/claude/test_workers`. |
| DB assembly worker (`_assembly_worker`) | poll windows with stale tokens | retries with backoff | graph errors, lock failure | window selection and ordering. | Covered in `tests/claude/test_workers`. |
| Log embedding worker (`_log_embedding_worker`) | poll system_logs with `embedding IS NULL` | backoff after failures | embedding errors | log embedding path. | Covered in live phase I. |
| Log shipper (`LogShipper.postgres_writer_loop`) | queue drain with timeout `FLUSH_INTERVAL_SEC` | on DB error sleep 1s | DB errors, queue overflow | batch size/flush, drop policy. | Covered in `tests/claude/test_log_shipper`. |

## 5. Configuration options
Only options referenced in code; include defaults and invalid handling.

### app/config.py
- `CONFIG_PATH` (env): default `/config/config.yml`; missing → RuntimeError.
- `TE_CONFIG_PATH` (env): default `/config/te.yml`; missing TE treated as legacy, unless direct TE load.
- `CREDENTIALS_PATH`: `/config/credentials/.env`; missing → fallback to env only.
- CEA validation (`cea.*`):
  - `retrieval_scope`: default `conversation`; invalid → RuntimeError.
  - `pre_extraction_fact_limit`: default 20; invalid (<0/non-int) → RuntimeError.
  - `expiration_cleanup_interval`: default 3600s; invalid (<0) → RuntimeError.
  - `ranking.exploration_rate`: default 0.1; outside 0-1 → RuntimeError.
  - `ranking.usefulness_crossover_events`: default 10; <=0 → RuntimeError.
  - `ranking.trustworthiness_min_threshold`: default 0.1; outside 0-1 → RuntimeError.
  - `ceac.max_iterations`: default 3; <1 → RuntimeError.
  - `ceac.max_memories`: default 50; <1 → RuntimeError.
  - `ceac.max_token_budget`: default 8000; <100 → RuntimeError.
  - `source_type_weights.*`: each 0-1; invalid → RuntimeError.

### app/main.py + workers
- `log_level`: default INFO.
- `workers.worker_poll_interval_seconds`: default 2s.
- `workers.embedding_batch_size`: default 50.
- `workers.embedding_timeout_seconds`: default 300.
- `workers.extraction_timeout_seconds`: default 600.
- `workers.assembly_timeout_seconds`: default 600.
- `workers.trigger_threshold_percent`: default 0.1.
- `workers.postgres_retry_interval_seconds`: default 10.
- `workers.log_embedding_batch_size`: default 50 (from code, if present).

### Token budgets
- `build_types.*.max_context_tokens`: int or `auto`; invalid → fallback.
- `build_types.*.fallback_tokens`: default 8192.
- `build_types.*` tier allocation: `tier1_floor_pct`, `tier2_chunk_pct`, `tier2_min_chunks`, `tier2_max_chunks`, `tier3_pct`, `semantic_retrieval_pct`, `knowledge_graph_pct`, `effective_utilization`.
  Invalid total_pct > utilization → ValueError at startup (fail fast).

### MCP session tuning
- `tuning.mcp_max_sessions`: default 1000.
- `tuning.mcp_session_ttl_seconds`: default 3600.
- `tuning.mcp_max_total_queued`: default 10000.

### TE config keys (used in code)
- `imperator.system_prompt` (default `imperator_identity`).
- `imperator.build_type` (default `tiered-summary`).
- `imperator.max_context_tokens` (default 8192).
- `imperator.max_react_messages` (default 40).
- `imperator.max_iterations` (default 5).
- `imperator.user_id` (optional) for CEAc.
- `imperator.notification_webhook`.
- `imperator.admin_tools` (bool).
- `domain_information.enabled` (default true for tools).
- `domain_knowledge.enabled` (default false for tools).
- `cea.ceac.enabled` (bool).

### Alerter config (`/config/alerter.yml`)
- `llm.base_url`, `llm.model`, `llm.api_key_env`.
- `embeddings.base_url`, `embeddings.model`, `embeddings.api_key_env`.
- `default_channels` list; missing → fallback to log.
- `log_context.enabled/level/limit/minutes`.
- Invalid values handled by returning errors or fallback default channels.

### Log shipper env
- `POSTGRES_DSN` required; missing → exit(1).
- `BATCH_SIZE` default 100.
- `FLUSH_INTERVAL_SEC` default 1.0.

## 6. Error paths / degradation modes (high-signal)
- Config load failures in MCP/chat/health return 500/503 with structured error.
- MCP session queue full → 503 with explicit error.
- Embedding worker poison pill after 5 consecutive failures writes zero-vector and resets failure count.
- Extraction worker caps retries per conversation; after max retries, marks messages extracted to stop retry storms.
- Assembly worker catches timeouts and errors and records warnings.
- CEAs extraction: JSON parse errors mark failure but allow compaction to proceed.
- Quality wrapper: global graph search errors are caught and logged; returns empty relations.
- Imperator flow: empty LLM responses trigger retries and fallback message; max iterations fallback to a safe response.
- Log shipper: queue overflow drops logs; DB errors drop batch and continue.
- Alerter: per-channel failure recorded; partial success still returns processed.

## 7. Database operations (SQL / schema / constraints)
### init.sql (base schema)
- Tables: `schema_migrations`, `conversations`, `conversation_messages`, `context_windows`, `conversation_summaries`, `system_logs`.
- Key constraints: `idx_messages_conversation_seq_unique`, `idx_context_windows_identity`, `conversation_summaries_tier_check` (tier 2/3), FK constraints to conversations/context_windows.

### Migrations (app/migrations.py)
- 001–022: schema alignment, logs, schedules, alert instructions, Mem0 dedup, domain info.
- 023 (CEA): `cea_quality_metadata`, `cea_feedback_events`, indexes (user_id, conversation_id, expires_at).
- 024 (CEA): `idx_cea_metadata_natural_dedup` (user_id, conversation_id, original_utterance), `idx_cea_events_target_type_id`.

### Query highlights (non-exhaustive but all unique query sites)
- Message insert/update, embedding updates, memory_extracted flags, summary insert/compaction, HNSW index creation.
- Search: vector + tsvector + RRF; domain info semantic search; logs query + vector search; knowledge wrapper direct pgvector query on `mem0_memories`.
- CEA tables: insert metadata with ON CONFLICT DO NOTHING; feedback insert with dedup_key.
- Alerter: instruction search (vector + text), event/delivery inserts.
- Log shipper: batch insert to `system_logs`.

## 8. Integration points (cross-module + external services)
- AE↔TE: `KernelTEContext` injected during `stategraph_registry.scan()`; TE uses `dispatch_tool`, `async_load_config`, `get_pool`.
- Mem0 (fork) integration: AE uses Mem0 for vector+graph, CEA wrapper uses Mem0 internal tables (`mem0_memories`) and Neo4j elementId usage (graph_memory changes).
- External services: LLM providers via OpenAI-compatible endpoints, embeddings providers, Neo4j (bolt + HTTP), Docker API (log shipper), HTTP webhooks (alerter notify channels), SMTP, Twilio.
- CEA: quality wrapper interacts with `cea_quality_metadata` and `cea_feedback_events` (Postgres) and Mem0/Neo4j.

## Coverage gaps (CEA focus)
- No tests in `tests/claude` explicitly exercise: `quality_wrapper.py`, `cea_extraction_flow.py`, `cea_enrichment_flow.py`, knowledge_* tool paths, or Mem0 fork changes (elementId mapping, side-channel removal).
- Add unit tests for wrapper dedup, metadata, feedback, expiration cleanup, global search paths; and integration tests verifying CEAc enrichment injection in Imperator.

