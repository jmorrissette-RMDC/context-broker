# Context Broker — Test Results Grid

**Run ID:** (timestamp)
**Date:** (date)
**Config:** (provider config summary)
**Run By:** (who/what executed)

---

## Unit Tests

| Test ID | Test Name | Expected | Actual | Pass/Fail (pass=100% perfect, fail=anything else) | Timestamp | Error |
|---------|-----------|----------|--------|---------------------------------------------------|-----------|-------|
| U1 | pytest tests/ | 292 pass, 0 fail | | pass=100% perfect, fail=anything else | | |

## Component Tests (test_components.py)

| Test ID | Test Name | Expected | Actual | Pass/Fail (pass=100% perfect, fail=anything else) | Timestamp | Error |
|---------|-----------|----------|--------|---------------------------------------------------|-----------|-------|
| A1 | scan() discovers AE package | "context-broker-ae" in logs | | pass=100% perfect, fail=anything else | | |
| A2 | scan() discovers TE package | "context-broker-te" in logs | | pass=100% perfect, fail=anything else | | |
| A3 | install_stategraph responds | status=installed | | pass=100% perfect, fail=anything else | | |
| A4 | All 3 build types registered | passthrough, standard-tiered, knowledge-enriched in tool list | | pass=100% perfect, fail=anything else | | |
| A5 | Imperator via chat endpoint | non-empty response >10 chars | | pass=100% perfect, fail=anything else | | |
| B1 | Credentials in mounted file | >=5 keys in file | | pass=100% perfect, fail=anything else | | |
| B2 | Health endpoint | 200, status=healthy | | pass=100% perfect, fail=anything else | | |
| B3 | store_message works | message_id returned | | pass=100% perfect, fail=anything else | | |
| B4 | Embedding generated | >0 embeddings in DB | | pass=100% perfect, fail=anything else | | |
| B5 | TE config hot-reload | get_context works after config change | | pass=100% perfect, fail=anything else | | |
| C1 | Store through MCP pipeline | >=5 messages in DB | | pass=100% perfect, fail=anything else | | |
| C2 | Embeddings for stored messages | >=3 embedded | | pass=100% perfect, fail=anything else | | |
| C3 | get_context returns context | context non-empty, total_tokens >0 | | pass=100% perfect, fail=anything else | | |
| C4 | search_messages finds results | >0 results | | pass=100% perfect, fail=anything else | | |
| D1 | Imperator responds coherently | response >10 chars | | pass=100% perfect, fail=anything else | | |
| D2 | imperator_chat MCP tool | response non-empty | | pass=100% perfect, fail=anything else | | |
| D3 | Pipeline status tool | response >20 chars, contains "pipeline" | | pass=100% perfect, fail=anything else | | |
| D4 | Log query tool | response >20 chars, contains "log" | | pass=100% perfect, fail=anything else | | |
| D5 | Context introspection tool | response >20 chars, contains "context" | | pass=100% perfect, fail=anything else | | |
| D6 | Metrics endpoint | contains "context_broker_" metrics | | pass=100% perfect, fail=anything else | | |
| E1 | Passthrough returns verbatim | test message found in context | | pass=100% perfect, fail=anything else | | |
| E2 | search_messages with embeddings | >0 results | | pass=100% perfect, fail=anything else | | |
| E3 | Effective utilization <=85% | total_tokens <= 85% of budget | | pass=100% perfect, fail=anything else | | |
| F1 | Health reports all deps | database, cache, neo4j all present | | pass=100% perfect, fail=anything else | | |
| F2 | Store message resilience | message_id returned | | pass=100% perfect, fail=anything else | | |
| G1 | Prometheus flow metrics | mcp_requests_total present | | pass=100% perfect, fail=anything else | | |
| G2 | Log shipper collected logs | >0 rows in system_logs | | pass=100% perfect, fail=anything else | | |
| G3 | Structured JSON logging | valid JSON with timestamp, level, message | | pass=100% perfect, fail=anything else | | |

## Cross-Provider Tests (test_cross_provider.py)

| Test ID | Test Name | Expected | Actual | Pass/Fail (pass=100% perfect, fail=anything else) | Timestamp | Error |
|---------|-----------|----------|--------|---------------------------------------------------|-----------|-------|
| CP-G | Google full pipeline | store + embed + get_context + search all succeed | | pass=100% perfect, fail=anything else | | |
| CP-O | OpenAI full pipeline | store + embed + get_context + search all succeed | | pass=100% perfect, fail=anything else | | |
| CP-L | Ollama full pipeline | store + embed + get_context + search all succeed | | pass=100% perfect, fail=anything else | | |

## Integration Phase 1: Bulk Load

| Test ID | Test Name | Expected | Actual | Pass/Fail (pass=100% perfect, fail=anything else) | Timestamp | Error |
|---------|-----------|----------|--------|---------------------------------------------------|-----------|-------|
| P1-01 | Messages stored | 10,430 (or close, null-content skipped) | | pass=100% perfect, fail=anything else | | |
| P1-02 | Embeddings generated | == stored messages (100%) | | pass=100% perfect, fail=anything else | | |
| P1-03 | Dead letters | 0 | | pass=100% perfect, fail=anything else | | |
| P1-04 | Context windows created | 9 (3 conv x 3 build types) | | pass=100% perfect, fail=anything else | | |
| P1-05 | Windows assembled | 9/9 | | pass=100% perfect, fail=anything else | | |
| P1-06 | Tier 2 summaries created | >0 per standard-tiered/knowledge-enriched window | | pass=100% perfect, fail=anything else | | |
| P1-07 | Tier 1 archival summaries | >0 per standard-tiered/knowledge-enriched window | | pass=100% perfect, fail=anything else | | |
| P1-08 | Knowledge graph facts in Neo4j | >0 nodes | | pass=100% perfect, fail=anything else | | |
| P1-09 | search_knowledge returns results | >0 results for known topic | | pass=100% perfect, fail=anything else | | |
| P1-10 | Mem0 memories stored | >0 in pgvector | | pass=100% perfect, fail=anything else | | |
| P1-11 | Embedding batch performance | >10 embeddings/sec | | pass=100% perfect, fail=anything else | | |

## Integration Phase 2: Imperator Conversation

| Test ID | Test Name | Expected | Actual | Pass/Fail (pass=100% perfect, fail=anything else) | Timestamp | Error |
|---------|-----------|----------|--------|---------------------------------------------------|-----------|-------|
| P2-01 | What conversations stored | lists conversations | | pass=100% perfect, fail=anything else | | |
| P2-02 | Search MAD architecture | search results returned | | pass=100% perfect, fail=anything else | | |
| P2-03 | Context Broker summary | mentions Context Broker | | pass=100% perfect, fail=anything else | | |
| P2-04 | Imperator pattern | mentions Imperator | | pass=100% perfect, fail=anything else | | |
| P2-05 | Public exposure strategy | search results returned | | pass=100% perfect, fail=anything else | | |
| P2-06 | Main projects | mentions Context Broker | | pass=100% perfect, fail=anything else | | |
| P2-07 | AE/TE separation | mentions AE | | pass=100% perfect, fail=anything else | | |
| P2-08 | Deployment infrastructure | mentions irina, Docker | | pass=100% perfect, fail=anything else | | |
| P2-09 | Recall earlier discussion | references MAD | | pass=100% perfect, fail=anything else | | |
| P2-10 | Summarize work | coherent summary >100 chars | | pass=100% perfect, fail=anything else | | |

## Integration Phase 3: Tool Exercises

| Test ID | Test Name | Expected | Actual | Pass/Fail (pass=100% perfect, fail=anything else) | Timestamp | Error |
|---------|-----------|----------|--------|---------------------------------------------------|-----------|-------|
| P3-01 | Log query tool | response >20 chars, contains "log" | | pass=100% perfect, fail=anything else | | |
| P3-02 | Context introspection | response >20 chars, contains "context" | | pass=100% perfect, fail=anything else | | |
| P3-03 | Pipeline status | response >20 chars, contains "pipeline" | | pass=100% perfect, fail=anything else | | |
| P3-04 | Search messages (Joshua26) | >0 results | | pass=100% perfect, fail=anything else | | |
| P3-05 | Search messages (Docker) | >0 results | | pass=100% perfect, fail=anything else | | |
| P3-06 | Search messages (Imperator) | >0 results | | pass=100% perfect, fail=anything else | | |
| P3-07 | Search knowledge (State 3/4) | >0 results | | pass=100% perfect, fail=anything else | | |
| P3-08 | Config read (admin) | contains model name | | pass=100% perfect, fail=anything else | | |
| P3-09 | Verbose toggle (admin) | toggle confirmed | | pass=100% perfect, fail=anything else | | |

## Integration Phase 4a: Quality Evaluation

| Test ID | Test Name | Expected | Actual | Pass/Fail (pass=100% perfect, fail=anything else) | Timestamp | Error |
|---------|-----------|----------|--------|---------------------------------------------------|-----------|-------|
| P4-01 | Conv-1 standard-tiered quality | Sonnet rates GOOD or ACCEPTABLE, tiers populated | | pass=100% perfect, fail=anything else | | |
| P4-02 | Conv-1 knowledge-enriched quality | Sonnet rates GOOD or ACCEPTABLE, knowledge_graph_facts >0 | | pass=100% perfect, fail=anything else | | |
| P4-03 | Conv-2 standard-tiered quality | Sonnet rates GOOD or ACCEPTABLE, tiers populated | | pass=100% perfect, fail=anything else | | |
| P4-04 | Conv-2 knowledge-enriched quality | Sonnet rates GOOD or ACCEPTABLE, knowledge_graph_facts >0 | | pass=100% perfect, fail=anything else | | |

## Integration Phase 4b: Performance

| Test ID | Test Name | Expected | Actual | Pass/Fail (pass=100% perfect, fail=anything else) | Timestamp | Error |
|---------|-----------|----------|--------|---------------------------------------------------|-----------|-------|
| P5-01 | store_message avg latency | <200ms | | pass=100% perfect, fail=anything else | | |
| P5-02 | get_context avg latency | <1000ms | | pass=100% perfect, fail=anything else | | |
| P5-03 | Embedding batch performance | >10/sec | | pass=100% perfect, fail=anything else | | |
| P5-04 | Embedding coverage | 100% | | pass=100% perfect, fail=anything else | | |
| P5-05 | Extraction success rate | >0% (facts actually in Neo4j) | | pass=100% perfect, fail=anything else | | |

---

## Summary

| Category | Total | Pass | Fail |
|----------|-------|------|------|
| Unit tests | 1 | | |
| Component tests | 28 | | |
| Cross-provider | 3 | | |
| Phase 1: Bulk Load | 11 | | |
| Phase 2: Imperator | 10 | | |
| Phase 3: Tools | 9 | | |
| Phase 4a: Quality | 4 | | |
| Phase 4b: Performance | 5 | | |
| **TOTAL** | **71** | | |

**ALL 71 TESTS MUST PASS. 0 FAILURES ACCEPTABLE.**
