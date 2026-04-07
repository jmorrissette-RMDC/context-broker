You are a senior software engineer verifying that a rewrite preserves all functionality of the original system.

Below you will find:
1. The **new implementation** — the Context Broker, a complete rewrite as a standalone State 4 MAD
2. The **original implementation** — Rogers, the previous Context Broker running within the Joshua26 ecosystem
3. The **requirements documents** that govern the new implementation

**Your task:** Compare the new implementation against the original Rogers code and verify that all functionality is preserved. For each finding, report:

- **Original feature:** What Rogers did (file, function, behavior)
- **New implementation:** Where this is covered in the new code (file, function) — or MISSING if not found
- **Severity:** blocker (core functionality lost) / major (significant feature gap) / minor (edge case or convenience feature missing)
- **Notes:** Any behavioral differences, even if the new code covers the feature

Focus on:
- MCP tools and their behavior — does the new code handle all the same operations?
- Data storage and retrieval — are all database operations preserved?
- Background processing — embedding, context assembly, memory extraction pipelines
- Context window assembly — tiered compression, semantic retrieval, knowledge graph
- Search functionality — hybrid search, reranking
- Imperator/agent functionality
- Configuration and deployment patterns

## Intentional Changes from Rogers

The following differences from Rogers are intentional architectural decisions for the State 4 standalone deployment. Do not flag them as missing functionality:

- `external_session_id` field removed
- `rogers_chat` renamed to `imperator_chat`
- `rogers_stats` replaced by Prometheus metrics endpoint
- `mem_extract` manual trigger removed (automatic extraction only)
- `small-basic` build type renamed to `passthrough`
- `conv_store_message` uses `context_window_id` as primary interface (also accepts `conversation_id`)
- Retrieval returns structured messages array instead of text
- Single LLM per build type instead of dual small/large routing
- Secret redaction via regex instead of detect-secrets library
- Build types defined as code (graph pairs) not database rows
- Extraction queued parallel with embedding
- Priority queue uses Redis sorted set instead of ZSET with 4 named levels
- Reranking uses API-based `/v1/rerank` (Infinity container) instead of local CrossEncoder model
- Embeddings served by Infinity container instead of Ollama or local sentence-transformers

Do NOT flag intentional architectural changes (e.g., different framework choices, different container layout) as missing functionality — those are expected. Focus on lost *behavior*.
