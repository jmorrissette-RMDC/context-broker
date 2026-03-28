# Performance Report

**Generated:** 2026-03-28T02:50:28.520690+00:00
**Source:** Prometheus /metrics endpoint

---

## Database State

| Metric | Value |
|--------|-------|
| Total messages | 34,277 |
| Embedded messages | 34,277 |
| Summaries | 64 |
| Extracted messages | 9,613 |

## Queue Depths (current)

| Queue | Depth |
|-------|-------|
| embedding | empty |
| assembly | empty |
| extraction | **24,697** |

## MCP Tool Latencies

| Tool | Calls | Errors | Avg (s) | p50 (s) | p90 (s) | p99 (s) | Total (s) |
|------|-------|--------|---------|---------|---------|---------|-----------|
| store_message | 35,044 | 6 | 0.027 | 0.05 | 0.05 | 0.50 | 931.2 |
| mem_add | 55 | 0 | 4.811 | 5.00 | 10.00 | 30.00 | 264.6 |
| imperator_chat | 14 | 0 | 7.066 | 5.00 | 30.00 | 30.00 | 98.9 |
| get_context | 451 | 0 | 0.114 | 0.10 | 0.50 | 0.50 | 51.4 |
| install_stategraph | 6 | 0 | 2.903 | 5.00 | 5.00 | 5.00 | 17.4 |
| search_knowledge | 15 | 0 | 1.155 | 5.00 | 5.00 | 5.00 | 17.3 |
| mem_search | 13 | 0 | 1.183 | 5.00 | 5.00 | 5.00 | 15.4 |
| search_messages | 17 | 0 | 0.464 | 0.50 | 1.00 | 1.00 | 7.9 |
| mem_get_context | 6 | 0 | 1.291 | 5.00 | 5.00 | 5.00 | 7.8 |
| mem_delete | 4 | 0 | 1.373 | 5.00 | 5.00 | 5.00 | 5.5 |
| conv_create_conversation | 125 | 0 | 0.033 | 0.05 | 0.10 | 0.10 | 4.2 |
| conv_search | 6 | 0 | 0.670 | 1.00 | 1.00 | 1.00 | 4.0 |
| conv_search_messages | 6 | 0 | 0.514 | 1.00 | 1.00 | 1.00 | 3.1 |
| search_logs | 6 | 0 | 0.335 | 0.50 | 0.50 | 0.50 | 2.0 |
| conv_store_message | 42 | 0 | 0.039 | 0.05 | 0.10 | 0.10 | 1.6 |
| conv_create_context_window | 24 | 0 | 0.032 | 0.05 | 0.05 | 0.50 | 0.8 |
| conv_list_conversations | 88 | 0 | 0.008 | 0.01 | 0.05 | 0.10 | 0.7 |
| mem_list | 24 | 0 | 0.024 | 0.01 | 0.10 | 0.50 | 0.6 |
| metrics_get | 33 | 0 | 0.013 | 0.01 | 0.05 | 0.05 | 0.4 |
| conv_retrieve_context | 12 | 0 | 0.034 | 0.05 | 0.10 | 0.10 | 0.4 |
| conv_delete_conversation | 12 | 0 | 0.027 | 0.05 | 0.05 | 0.05 | 0.3 |
| conv_get_history | 20 | 6 | 0.007 | 0.01 | 0.05 | 0.05 | 0.1 |
| conv_search_context_windows | 12 | 0 | 0.005 | 0.01 | 0.01 | 0.05 | 0.1 |
| query_logs | 12 | 0 | 0.002 | 0.01 | 0.01 | 0.01 | 0.0 |
| completely_nonexistent_tool_xyz | 6 | 6 | 0.000 | 0.01 | 0.01 | 0.01 | 0.0 |

## Chat (Imperator) Latencies

| Metric | Value |
|--------|-------|
| Total calls | 339 |
| Total time | 3757.7s |
| Average | 11.1s |
| p50 | 10.0s |
| p90 | 30.0s |
| p99 | 60.0s |

## Background Job Performance

| Job Type | Completed | Errors | Avg (s) | p50 (s) | p90 (s) | Total (s) |
|----------|-----------|--------|---------|---------|---------|-----------|
| embed_batch | 34,278 | 0 | 1.53 | 5.0 | 5.0 | 1766.5 |
| extract_memory | 196 | 14 | 38.97 | 60.0 | 120.0 | 7638.8 |
| assemble_context | 364 | 0 | 0.19 | 0.1 | 0.1 | 70.6 |

## Key Findings

- **SLOW**: `imperator_chat` averages 7.1s per call
- **QUEUE NOT DRAINED**: `extraction` queue has 24,697 pending items
- **SLOW CHAT**: Imperator averages 11.1s per response
