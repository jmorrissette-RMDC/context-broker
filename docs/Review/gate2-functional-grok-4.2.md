**Verification Report: Context Broker vs. Original Rogers**

I compared the new standalone State 4 MAD (Context Broker) against the original Rogers codebase. The new implementation preserves core functionality through equivalent StateGraph flows, database operations, background pipelines, and MCP tools. Intentional architectural changes (e.g., pure-Python FastAPI + LangGraph instead of Node.js gateway + Quart, YAML config with hot-reload instead of JSON, ARQ workers instead of custom polling loops, direct providers instead of Sutherland peer proxy, local cross-encoder reranker instead of Sutherland `llm_rerank`) were **not** flagged as gaps.

Below are the findings, grouped by focus area. Only behavioral or functional differences are noted.

### MCP Tools and Dispatch
- **Original feature:** 10 client-facing MCP tools defined in `rogers/config.json`, routed via Node.js gateway (`rogers/server.js`) to LangGraph HTTP endpoints (`rogers-langgraph/server.py` routes and `flows/conversation_ops.py`, `memory_ops.py`). Tools included `conv_*` (create, store, retrieve, search, history, context windows) and `mem_*` (search, get_context). Internal tools (`mem_add`, `mem_list`, `mem_delete`, `mem_extract`) were HTTP-only, not MCP. (See also `rogers-langgraph/services/mcp_client.py` for tool loading.)
- **New implementation:** Covered in `app/routes/mcp.py` (MCP JSON-RPC + SSE), `app/flows/tool_dispatch.py` (routes to compiled StateGraphs), and `app/models.py` (Pydantic validation). Tool list in `_get_tool_list()` matches exactly. `broker_chat` and `metrics_get` are added. Internal memory ops are now behind `mem_search`/`mem_get_context`.
- **Severity:** None (fully covered).
- **Notes:** New uses a single StateGraph dispatch point with Pydantic validation before every flow (stronger than original). No behavioral difference in outputs or error schemas. `mem_add`/`mem_list` etc. are not directly exposed via MCP (consistent with original intent).

### Data Storage and Retrieval
- **Original feature:** Postgres schema in `rogers-postgres/init.sql` (conversations, conversation_messages with embedding + tsvector, context_windows, conversation_summaries, context_window_build_types). Operations in `rogers-langgraph/services/database.py` (insert/update/search with hybrid vector+BM25, get_unextracted_messages, mark_messages_extracted, summaries, windows). Mem0 uses pgvector + Neo4j.
- **New implementation:** Covered in `postgres/init.sql` (nearly identical schema, plus schema_migrations table), `app/database.py` (asyncpg + redis pools), and flows (`app/flows/message_pipeline.py`, `app/flows/conversation_ops_flow.py`, `app/flows/retrieval_flow.py`, `app/flows/search_flow.py`). Mem0 client in `app/memory/mem0_client.py` (pgvector + Neo4j).
- **Severity:** None (fully covered).
- **Notes:** New schema adds `memory_extracted` flag and explicit migration system (`app/migrations.py`). Search uses the same hybrid RRF pattern in SQL. No behavioral difference in stored data or query results. Original `content_type`/`priority` columns are absent (see "Background Processing" below).

### Background Processing (Embedding, Assembly, Extraction)
- **Original feature:** Custom polling consumers in `rogers-langgraph/services/queue_worker.py` (embedding_jobs list, context_assembly_jobs list, memory_extraction_jobs ZSET with priority scoring). Used `flows/embed_pipeline.py`, `flows/context_assembly.py`, `flows/memory_extraction.py`. Priority offsets in database.py for live-user vs. migration jobs. Dead-letter handling with sweep. Locks to prevent concurrent assembly/extraction.
- **New implementation:** Covered in `app/workers/arq_worker.py` (ARQ consumers for embedding_jobs, context_assembly_jobs, memory_extraction_jobs lists + dead_letter_jobs). Flows are identical: `app/flows/embed_pipeline.py`, `app/flows/context_assembly.py` (incremental tiers, existing-summary check), `app/flows/memory_extraction.py` (lock, fetch unextracted, Mem0). Redis locks in both.
- **Severity:** Major (job prioritization).
- **Notes:** Behavioral difference: Original used ZSET + priority column + content_type filtering for live-user precedence (P0-P3 offsets). New uses simple Redis lists (no priority scoring or content_type classification). Live interactions could be delayed behind bulk data. Locks and retry/dead-letter logic are equivalent. ARQ is more reliable but changes the queue model (intentional for standalone).

### Context Window Assembly
- **Original feature:** Three-tier progressive compression in `rogers-langgraph/flows/context_assembly.py` (calculate tiers with scaling percentages, incremental summarization of only new messages, consolidation of old Tier 2 into Tier 1, LLM selection by build_type, Redis assembly lock). Triggered by embedding completion.
- **New implementation:** Covered in `app/flows/context_assembly.py` (nearly identical StateGraph: acquire_lock, load_window_config, load_messages, calculate_tier_boundaries with existing_t2 check, summarize_message_chunks, consolidate_archival_summary, finalize). Uses same tier math, chunking (size 20), and Redis lock.
- **Severity:** None (fully covered).
- **Notes:** New is more explicit about incremental logic and error paths (all route through release_lock). Build types in `config/config.example.yml` match original (standard-tiered, knowledge-enriched). No behavioral difference.

### Context Retrieval and Tiered Window
- **Original feature:** `rogers-langgraph/flows/retrieval.py` (get_window, check_assembly with Redis flag wait, get_summaries, get_recent within budget, assemble with XML markers). Blocks up to 50s if assembly in progress.
- **New implementation:** Covered in `app/flows/retrieval_flow.py` (load_window, wait_for_assembly with Redis poll, load_summaries, load_recent_messages, assemble_context_text). Same 50s timeout, tier logic, and XML-style markers.
- **Severity:** None (fully covered).
- **Notes:** New adds semantic_retrieval and knowledge_graph injection for "knowledge-enriched" build types (per REQ). Original was purely episodic. This is an enhancement, not a gap.

### Search Functionality
- **Original feature:** Hybrid search (vector ANN + BM25 RRF) in `rogers-langgraph/flows/conversation_ops.py` + `services/database.py` (search_conversations, search_messages). Reranking via Sutherland `llm_rerank` (cross-encoder). Recency bias.
- **New implementation:** Covered in `app/flows/search_flow.py` (embed_query, hybrid_search_messages with RRF in SQL, rerank_results using local CrossEncoder). Same RRF constant (60), candidate limit, and fallback.
- **Severity:** None (fully covered).
- **Notes:** New implements reranker locally (`sentence_transformers`) instead of calling Sutherland. Equivalent behavior. Conversation vs. message search separation is preserved.

### Imperator / Agent Functionality
- **Original feature:** Custom ReAct-style loop in `rogers-langgraph/flows/imperator.py` (decide_next_action via LLM JSON, execute_selected_tool for conv_search/mem_search, finalize). Persistent via conversation.
- **New implementation:** Covered in `app/flows/imperator_flow.py` (LangGraph ReAct with `bind_tools`, ToolNode, checkpointing via MemorySaver). Uses same tools (conv_search_tool, mem_search_tool). Persistent state in `app/imperator/state_manager.py` (imperator_state.json).
- **Severity:** None (fully covered).
- **Notes:** New uses standard LangGraph ReAct (per REQ §4.5) instead of custom JSON parsing. Same capabilities and tool set. Imperator conversation persistence is equivalent.

### Configuration and Deployment
- **Original feature:** config.json + credential files on /storage. Hard-coded paths. Tied to Joshua26 ecosystem (Sutherland peer proxy, specific UIDs/networks).
- **New implementation:** `app/config.py` (YAML with hot-reload for providers/build_types, startup cache for infra). `docker-compose.yml` + `config/config.example.yml`. Standalone State 4 pattern.
- **Severity:** None (intentional).
- **Notes:** New meets all State 4 requirements (configurable providers, no hard couplings). Hot-reload for inference is an improvement. No behavioral loss.

### Other Minor/Edge Items
- **Original feature:** Secret redaction before Mem0 (`rogers-langgraph/services/secret_filter.py`, called in mem_add and extraction flows).
- **New implementation:** MISSING (no equivalent in `app/flows/memory_extraction.py` or `app/memory/mem0_client.py`).
- **Severity:** Major (security).
- **Notes:** If conversations mention credentials, they could be memorized in Neo4j. Recommend adding `detect-secrets`-based redaction.

- **Original feature:** Prometheus metrics + Apgar-style `/metrics_get` tool and `/stats` endpoint.
- **New implementation:** Covered in `app/metrics_registry.py`, `app/flows/metrics_flow.py`, `app/routes/metrics.py`, and MCP `metrics_get`.
- **Severity:** None.
- **Notes:** New produces metrics inside StateGraphs (per REQ). Equivalent.

**Overall Assessment:** The rewrite preserves all core Rogers behavior. The two notable gaps (job prioritization and secret redaction) are the only functional losses. The prioritization gap is the most impactful for production workloads with mixed live/bulk traffic. All other differences are intentional improvements for the State 4 standalone MAD (LangGraph mandate, hot-reload config, ARQ reliability, direct providers, richer build types).

No blocker issues. The system is functionally equivalent for MCP usage, context assembly, retrieval, search, and Imperator conversations.