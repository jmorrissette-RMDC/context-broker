# Performance Report

**Generated:** 2026-03-27T19:59:58.094796+00:00
**Source:** Prometheus /metrics endpoint

---

## Database State

| Metric | Value |
|--------|-------|
| Total messages | 40,862 |
| Embedded messages | 40,862 |
| Summaries | 79 |
| Extracted messages | 40,769 |

## Queue Depths (current)

| Queue | Depth |
|-------|-------|
| embedding | empty |
| assembly | empty |
| extraction | **90** |

## MCP Tool Latencies

| Tool | Calls | Errors | Avg (s) | p50 (s) | p90 (s) | p99 (s) | Total (s) |
|------|-------|--------|---------|---------|---------|---------|-----------|
| store_message | 10,542 | 1 | 0.023 | 0.05 | 0.05 | 0.10 | 243.5 |
| mem_add | 7 | 0 | 4.565 | 5.00 | 10.00 | 10.00 | 31.9 |
| get_context | 66 | 0 | 0.070 | 0.10 | 0.50 | 0.50 | 4.6 |
| mem_search | 3 | 0 | 1.371 | 5.00 | 5.00 | 5.00 | 4.1 |
| install_stategraph | 1 | 0 | 2.808 | 5.00 | 5.00 | 5.00 | 2.8 |
| imperator_chat | 1 | 0 | 2.437 | 5.00 | 5.00 | 5.00 | 2.4 |
| mem_get_context | 1 | 0 | 0.883 | 1.00 | 1.00 | 1.00 | 0.9 |
| search_logs | 1 | 0 | 0.818 | 1.00 | 1.00 | 1.00 | 0.8 |
| search_knowledge | 1 | 0 | 0.776 | 1.00 | 1.00 | 1.00 | 0.8 |
| conv_search | 1 | 0 | 0.667 | 1.00 | 1.00 | 1.00 | 0.7 |
| conv_create_conversation | 22 | 0 | 0.029 | 0.05 | 0.05 | 0.50 | 0.7 |
| search_messages | 2 | 0 | 0.231 | 0.50 | 0.50 | 0.50 | 0.5 |
| conv_search_messages | 1 | 0 | 0.432 | 0.50 | 0.50 | 0.50 | 0.4 |
| conv_list_conversations | 13 | 0 | 0.020 | 0.01 | 0.10 | 0.50 | 0.3 |
| conv_store_message | 7 | 0 | 0.023 | 0.05 | 0.05 | 0.05 | 0.2 |
| mem_list | 8 | 0 | 0.014 | 0.05 | 0.05 | 0.05 | 0.1 |
| conv_create_context_window | 4 | 0 | 0.024 | 0.05 | 0.05 | 0.05 | 0.1 |
| conv_retrieve_context | 2 | 0 | 0.032 | 0.05 | 0.05 | 0.05 | 0.1 |
| conv_delete_conversation | 2 | 0 | 0.026 | 0.05 | 0.05 | 0.05 | 0.1 |
| metrics_get | 4 | 0 | 0.013 | 0.05 | 0.05 | 0.05 | 0.1 |
| conv_get_history | 3 | 1 | 0.009 | 0.01 | 0.05 | 0.05 | 0.0 |
| conv_search_context_windows | 2 | 0 | 0.003 | 0.01 | 0.01 | 0.01 | 0.0 |
| completely_nonexistent_tool_xyz | 1 | 1 | 0.000 | 0.01 | 0.01 | 0.01 | 0.0 |
| query_logs | 2 | 0 | 0.001 | 0.01 | 0.01 | 0.01 | 0.0 |

## Chat (Imperator) Latencies

| Metric | Value |
|--------|-------|
| Total calls | 48 |
| Total time | 510.6s |
| Average | 10.6s |
| p50 | 10.0s |
| p90 | 30.0s |
| p99 | 60.0s |

## Background Job Performance

| Job Type | Completed | Errors | Avg (s) | p50 (s) | p90 (s) | Total (s) |
|----------|-----------|--------|---------|---------|---------|-----------|
| embed_batch | 10,213 | 0 | 1.44 | 5.0 | 5.0 | 367.6 |
| extract_memory | 0 | 65 | 0.00 | inf | inf | 0.0 |
| assemble_context | 47 | 0 | 0.27 | 0.1 | 0.1 | 12.8 |

## Key Findings

- **HIGH ERROR RATE**: `extract_memory` has 100% error rate (65/65)
- **QUEUE NOT DRAINED**: `extraction` queue has 90 pending items
- **SLOW CHAT**: Imperator averages 10.6s per response
