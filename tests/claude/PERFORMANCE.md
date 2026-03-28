# Performance Report

**Generated:** 2026-03-28T02:03:08.412951+00:00
**Source:** Prometheus /metrics endpoint

---

## Database State

| Metric | Value |
|--------|-------|
| Total messages | 33,945 |
| Embedded messages | 33,945 |
| Summaries | 61 |
| Extracted messages | 8,615 |

## Queue Depths (current)

| Queue | Depth |
|-------|-------|
| embedding | **1** |
| assembly | empty |
| extraction | **25,320** |

## MCP Tool Latencies

| Tool | Calls | Errors | Avg (s) | p50 (s) | p90 (s) | p99 (s) | Total (s) |
|------|-------|--------|---------|---------|---------|---------|-----------|
| store_message | 34,866 | 4 | 0.026 | 0.05 | 0.05 | 0.50 | 922.1 |
| mem_add | 38 | 0 | 4.836 | 5.00 | 10.00 | 30.00 | 183.8 |
| imperator_chat | 7 | 0 | 6.027 | 5.00 | 30.00 | 30.00 | 42.2 |
| get_context | 265 | 0 | 0.101 | 0.10 | 0.50 | 0.50 | 26.7 |
| install_stategraph | 4 | 0 | 2.922 | 5.00 | 5.00 | 5.00 | 11.7 |
| mem_search | 6 | 0 | 1.090 | 5.00 | 5.00 | 5.00 | 6.5 |
| search_knowledge | 4 | 0 | 1.198 | 5.00 | 5.00 | 5.00 | 4.8 |
| mem_get_context | 4 | 0 | 1.193 | 5.00 | 5.00 | 5.00 | 4.8 |
| search_messages | 8 | 0 | 0.427 | 0.50 | 1.00 | 1.00 | 3.4 |
| conv_create_conversation | 87 | 0 | 0.032 | 0.05 | 0.10 | 0.50 | 2.8 |
| conv_search | 4 | 0 | 0.683 | 1.00 | 1.00 | 1.00 | 2.7 |
| conv_search_messages | 4 | 0 | 0.499 | 0.50 | 1.00 | 1.00 | 2.0 |
| mem_delete | 2 | 0 | 0.878 | 0.01 | 5.00 | 5.00 | 1.8 |
| search_logs | 4 | 0 | 0.302 | 0.50 | 0.50 | 0.50 | 1.2 |
| conv_store_message | 28 | 0 | 0.033 | 0.05 | 0.10 | 0.10 | 0.9 |
| conv_create_context_window | 16 | 0 | 0.033 | 0.05 | 0.10 | 0.50 | 0.5 |
| conv_list_conversations | 55 | 0 | 0.010 | 0.01 | 0.05 | 0.10 | 0.5 |
| mem_list | 20 | 0 | 0.023 | 0.01 | 0.05 | 0.50 | 0.5 |
| conv_retrieve_context | 8 | 0 | 0.037 | 0.05 | 0.10 | 0.10 | 0.3 |
| metrics_get | 20 | 0 | 0.013 | 0.01 | 0.05 | 0.05 | 0.3 |
| conv_delete_conversation | 8 | 0 | 0.030 | 0.05 | 0.05 | 0.05 | 0.2 |
| conv_get_history | 12 | 4 | 0.008 | 0.01 | 0.05 | 0.05 | 0.1 |
| conv_search_context_windows | 8 | 0 | 0.006 | 0.01 | 0.05 | 0.05 | 0.1 |
| query_logs | 8 | 0 | 0.002 | 0.01 | 0.01 | 0.01 | 0.0 |
| completely_nonexistent_tool_xyz | 4 | 4 | 0.000 | 0.01 | 0.01 | 0.01 | 0.0 |

## Chat (Imperator) Latencies

| Metric | Value |
|--------|-------|
| Total calls | 193 |
| Total time | 2133.2s |
| Average | 11.1s |
| p50 | 10.0s |
| p90 | 30.0s |
| p99 | 60.0s |

## Background Job Performance

| Job Type | Completed | Errors | Avg (s) | p50 (s) | p90 (s) | Total (s) |
|----------|-----------|--------|---------|---------|---------|-----------|
| embed_batch | 33,944 | 0 | 1.78 | 5.0 | 5.0 | 1666.8 |
| extract_memory | 131 | 11 | 37.73 | 60.0 | 120.0 | 4942.5 |
| assemble_context | 202 | 0 | 0.29 | 0.1 | 1.0 | 58.4 |

## Key Findings

- **SLOW**: `imperator_chat` averages 6.0s per call
- **QUEUE NOT DRAINED**: `embedding` queue has 1 pending items
- **QUEUE NOT DRAINED**: `extraction` queue has 25,320 pending items
- **SLOW CHAT**: Imperator averages 11.1s per response
