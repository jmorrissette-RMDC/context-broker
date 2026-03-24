# Context Broker — Test Results Grid

All tests must PASS with hard assertions before the system is considered functional.
"ACCEPTABLE" from an LLM is not a PASS. Only concrete data assertions are PASS.

## Unit Tests (pytest)

| Test | Expected | Actual | Status | Date |
|------|----------|--------|--------|------|
| pytest tests/ (292 tests) | 292 pass | 292 pass | PASS | 2026-03-24 |

## Component Tests (test_components.py — 28 tests against live irina)

| ID | Test | Expected | Actual | Status | Date |
|----|------|----------|--------|--------|------|
| A1 | scan() discovers AE package | AE registered | AE registered | PASS | 2026-03-24 |
| A2 | scan() discovers TE package | TE registered | TE registered | PASS | 2026-03-24 |
| A3 | install_stategraph responds | status=installed | status=installed | PASS | 2026-03-24 |
| A4 | All 3 build types registered | 3 types in tool list | 3 found | PASS | 2026-03-24 |
| A5 | Imperator via chat endpoint | non-empty response | response OK | PASS | 2026-03-24 |
| B1 | Credentials in mounted file | >=5 keys | 7 keys | PASS | 2026-03-24 |
| B2 | Health endpoint | 200 healthy | 200 healthy | PASS | 2026-03-24 |
| B3 | store_message works | message_id returned | message_id OK | PASS | 2026-03-24 |
| B4 | Embedding generated | >0 embeddings | embeddings OK | PASS | 2026-03-24 |
| B5 | TE config hot-reload | get_context works | works | PASS | 2026-03-24 |
| C1 | Store through MCP pipeline | >=5 messages | 5 stored | PASS | 2026-03-24 |
| C2 | Embeddings for stored messages | >=3 embedded | 3+ embedded | PASS | 2026-03-24 |
| C3 | get_context returns context | context non-empty | context OK | PASS | 2026-03-24 |
| C4 | search_messages finds results | >0 results | results found | PASS | 2026-03-24 |
| D1 | Imperator responds coherently | >10 chars | response OK | PASS | 2026-03-24 |
| D2 | imperator_chat MCP tool | response non-empty | response OK | PASS | 2026-03-24 |
| D3 | Pipeline status tool | response >20 chars | response OK | PASS | 2026-03-24 |
| D4 | Log query tool | response >20 chars | response OK | PASS | 2026-03-24 |
| D5 | Context introspection tool | response >20 chars | response OK | PASS | 2026-03-24 |
| D6 | Metrics endpoint | context_broker_ metrics | metrics found | PASS | 2026-03-24 |
| E1 | Passthrough returns verbatim | test message in context | found | PASS | 2026-03-24 |
| E2 | search_messages with embeddings | >0 results | results found | PASS | 2026-03-24 |
| E3 | Effective utilization <=85% | tokens <= 3481 (85% of 4096) | within limit | PASS | 2026-03-24 |
| F1 | Health reports all deps | database, cache, neo4j | all present | PASS | 2026-03-24 |
| F2 | Store message resilience | message_id returned | message_id OK | PASS | 2026-03-24 |
| G1 | Prometheus flow metrics | mcp_requests_total | found | PASS | 2026-03-24 |
| G2 | Log shipper collected logs | >0 system_logs | logs found | PASS | 2026-03-24 |
| G3 | Structured JSON logging | JSON with timestamp+level+message | valid JSON | PASS | 2026-03-24 |

## Cross-Provider Tests (test_cross_provider.py — full pipeline per provider)

| Provider | store_message | embedding | get_context | search | Status | Date |
|----------|--------------|-----------|-------------|--------|--------|------|
| Google | OK | 3/3 | context OK | results OK | PASS | 2026-03-24 |
| OpenAI | OK | 3/3 | context OK | results OK | PASS | 2026-03-24 |
| Ollama | OK | 3/3 | context OK | results OK | PASS | 2026-03-24 |
| Together | — | — | — | — | SKIP (no serverless embeddings) | 2026-03-24 |
| xAI | — | — | — | — | SKIP (no embedding API) | 2026-03-24 |

## Integration Tests (bulk load + pipeline)

### Phase 1: Bulk Load

| Metric | Expected | Actual | Status | Date |
|--------|----------|--------|--------|------|
| Messages stored | 10,430 | 10,099 | PASS (331 null-content skipped) | 2026-03-24 |
| Embeddings generated | == messages | 10,099/10,099 | PASS | 2026-03-24 |
| Embedding batch performance | >10/sec | ~60/sec (50/batch @ 2.5s) | PASS | 2026-03-24 |
| Dead letters | 0 | 0 | PASS | 2026-03-24 |
| Context windows created | 9 (3 conv x 3 types) | 9 | PASS | 2026-03-24 |
| Windows assembled | 9/9 | 9/9 | PASS | 2026-03-24 |
| Tier 2 summaries created | >0 | 461 | PASS | 2026-03-24 |
| Tier 1 archival summaries | >0 per window | present | PASS | 2026-03-24 |
| Knowledge graph facts extracted | >0 per conversation | **0 in Neo4j** | **FAIL** | 2026-03-24 |
| search_knowledge returns results | >0 | **0** | **FAIL** | 2026-03-24 |
| Mem0 memories in pgvector | >0 | **0** | **FAIL** | 2026-03-24 |

### Phase 2: Imperator Conversation (10 turns)

| Turn | Prompt | Expected | Actual | Status | Date |
|------|--------|----------|--------|--------|------|
| 1 | What conversations stored? | lists conversations | listed 3 | PASS | 2026-03-24 |
| 2 | Search MAD architecture | search results | results found | PASS | 2026-03-24 |
| 3 | Context Broker summary | mentions CB | mentioned CB | PASS | 2026-03-24 |
| 4 | Imperator pattern | mentions Imperator | found | PASS | 2026-03-24 |
| 5 | Public exposure strategy | search results | results found | PASS | 2026-03-24 |
| 6 | Main projects | mentions CB | mentioned CB | PASS | 2026-03-24 |
| 7 | AE/TE separation | mentions AE | found via search | PASS | 2026-03-24 |
| 8 | Deployment infra | irina, Docker | found | PASS | 2026-03-24 |
| 9 | Recall earlier discussion | references MAD | referenced | PASS | 2026-03-24 |
| 10 | Summarize work | coherent summary | summary OK | PASS | 2026-03-24 |

### Phase 3: Tool Exercises (9 tests)

| Tool | Prompt | Expected | Actual | Status | Date |
|------|--------|----------|--------|--------|------|
| log_query | Show log entries | log data returned | 1457 chars | PASS | 2026-03-24 |
| context_introspection | Context breakdown | context info | 156 chars | PASS | 2026-03-24 |
| pipeline_status | Pending jobs | pipeline info | 214 chars | PASS | 2026-03-24 |
| search_messages (Joshua26) | Search results | results found | 155 chars | PASS | 2026-03-24 |
| search_messages (Docker) | Search results | results found | 191 chars | PASS | 2026-03-24 |
| search_messages (Imperator) | Search results | results found | 194 chars | PASS | 2026-03-24 |
| search_knowledge (State 3/4) | Knowledge results | **>0 results** | **0 results** | **FAIL** | 2026-03-24 |
| config_read (admin) | Shows model | gemini in response | config shown | PASS | 2026-03-24 |
| verbose_toggle (admin) | Toggles logging | toggle confirmed | toggled | PASS | 2026-03-24 |

### Phase 4a: Quality Evaluation (Sonnet CLI)

| Conversation | Build Type | Expected | Rating | Status | Date |
|-------------|-----------|----------|--------|--------|------|
| dd69aa9d | standard-tiered | GOOD or ACCEPTABLE | GOOD | PASS | 2026-03-24 |
| dd69aa9d | knowledge-enriched | GOOD or ACCEPTABLE, **KG facts >0** | ACCEPTABLE, **KG facts=0** | **FAIL** | 2026-03-24 |
| c3164a6a | standard-tiered | GOOD or ACCEPTABLE | GOOD | PASS | 2026-03-24 |
| c3164a6a | knowledge-enriched | GOOD or ACCEPTABLE, **KG facts >0** | ACCEPTABLE, **KG facts=0** | **FAIL** | 2026-03-24 |

### Phase 4b: Performance

| Metric | Value | Status | Date |
|--------|-------|--------|------|
| store_message avg | 56ms | PASS | 2026-03-24 |
| get_context avg | 242ms | PASS | 2026-03-24 |
| embedding batch avg | 266ms/50 texts | PASS | 2026-03-24 |
| Embedding coverage | 100% | PASS | 2026-03-24 |
| Extraction success | **0%** | **FAIL** | 2026-03-24 |

## Summary

| Category | Pass | Fail | Total |
|----------|------|------|-------|
| Unit tests | 292 | 0 | 292 |
| Component tests | 28 | 0 | 28 |
| Cross-provider | 3 | 0 | 3 (+2 skip) |
| Integration Phase 1 | 9 | 3 | 12 |
| Integration Phase 2 | 10 | 0 | 10 |
| Integration Phase 3 | 8 | 1 | 9 |
| Integration Phase 4a | 2 | 2 | 4 |
| Integration Phase 4b | 4 | 1 | 5 |
| **TOTAL** | **356** | **7** | **363** |

**7 FAILURES — all related to knowledge extraction (Mem0/Neo4j).** Knowledge graph is non-functional at scale. Must be fixed before declaring any test phase complete.
