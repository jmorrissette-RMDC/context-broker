# Performance Report

**Generated:** 2026-03-27T21:40:41.492812+00:00
**Source:** Prometheus /metrics endpoint

---

## Database State

| Metric | Value |
|--------|-------|
| Total messages | 61,292 |
| Embedded messages | 61,291 |
| Summaries | 118 |
| Extracted messages | 54,911 |

## Queue Depths (current)

| Queue | Depth |
|-------|-------|
| embedding | empty |
| assembly | empty |
| extraction | **6,398** |

## MCP Tool Latencies

| Tool | Calls | Errors | Avg (s) | p50 (s) | p90 (s) | p99 (s) | Total (s) |
|------|-------|--------|---------|---------|---------|---------|-----------|
| store_message | 10,494 | 1 | 0.023 | 0.05 | 0.05 | 0.50 | 241.7 |
| mem_add | 7 | 0 | 4.021 | 5.00 | 10.00 | 10.00 | 28.1 |
| imperator_chat | 2 | 0 | 2.972 | 5.00 | 5.00 | 5.00 | 5.9 |
| get_context | 66 | 0 | 0.085 | 0.10 | 0.50 | 0.50 | 5.6 |
| mem_search | 3 | 0 | 1.274 | 5.00 | 5.00 | 5.00 | 3.8 |
| install_stategraph | 1 | 0 | 2.761 | 5.00 | 5.00 | 5.00 | 2.8 |
| mem_get_context | 1 | 0 | 1.094 | 5.00 | 5.00 | 5.00 | 1.1 |
| conv_search | 1 | 0 | 0.868 | 1.00 | 1.00 | 1.00 | 0.9 |
| search_knowledge | 1 | 0 | 0.779 | 1.00 | 1.00 | 1.00 | 0.8 |
| search_logs | 1 | 0 | 0.620 | 1.00 | 1.00 | 1.00 | 0.6 |
| search_messages | 2 | 0 | 0.278 | 0.50 | 0.50 | 0.50 | 0.6 |
| conv_create_conversation | 22 | 0 | 0.023 | 0.05 | 0.05 | 0.05 | 0.5 |
| conv_search_messages | 1 | 0 | 0.358 | 0.50 | 0.50 | 0.50 | 0.4 |
| conv_list_conversations | 13 | 0 | 0.018 | 0.05 | 0.05 | 0.10 | 0.2 |
| conv_store_message | 7 | 0 | 0.031 | 0.05 | 0.05 | 0.05 | 0.2 |
| mem_list | 8 | 0 | 0.027 | 0.01 | 0.50 | 0.50 | 0.2 |
| conv_create_context_window | 4 | 0 | 0.026 | 0.05 | 0.10 | 0.10 | 0.1 |
| conv_retrieve_context | 2 | 0 | 0.016 | 0.05 | 0.05 | 0.05 | 0.0 |
| conv_delete_conversation | 2 | 0 | 0.011 | 0.05 | 0.05 | 0.05 | 0.0 |
| metrics_get | 4 | 0 | 0.006 | 0.01 | 0.01 | 0.01 | 0.0 |
| conv_get_history | 3 | 1 | 0.003 | 0.01 | 0.01 | 0.01 | 0.0 |
| conv_search_context_windows | 2 | 0 | 0.003 | 0.01 | 0.01 | 0.01 | 0.0 |
| query_logs | 2 | 0 | 0.003 | 0.01 | 0.01 | 0.01 | 0.0 |
| completely_nonexistent_tool_xyz | 1 | 1 | 0.000 | 0.01 | 0.01 | 0.01 | 0.0 |

## Chat (Imperator) Latencies

| Metric | Value |
|--------|-------|
| Total calls | 48 |
| Total time | 445.4s |
| Average | 9.3s |
| p50 | 10.0s |
| p90 | 30.0s |
| p99 | 60.0s |

## Background Job Performance

| Job Type | Completed | Errors | Avg (s) | p50 (s) | p90 (s) | Total (s) |
|----------|-----------|--------|---------|---------|---------|-----------|
| embed_batch | 10,214 | 0 | 1.46 | 5.0 | 5.0 | 382.6 |
| extract_memory | 26 | 19 | 14.70 | 30.0 | 60.0 | 382.1 |
| assemble_context | 48 | 0 | 0.23 | 0.1 | 0.1 | 11.1 |

## Key Findings

- **HIGH ERROR RATE**: `extract_memory` has 42% error rate (19/45)
- **QUEUE NOT DRAINED**: `extraction` queue has 6,398 pending items
