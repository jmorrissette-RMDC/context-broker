# Performance Report

**Generated:** 2026-03-29T06:09:43.478075+00:00
**Source:** Prometheus /metrics endpoint

---

## Database State

| Metric | Value |
|--------|-------|
| Total messages | 10,419 |
| Embedded messages | 10,419 |
| Summaries | 54 |
| Extracted messages | 1,540 |

## Queue Depths (current)

| Queue | Depth |
|-------|-------|
| embedding | empty |
| assembly | empty |
| extraction | **8,923** |

## MCP Tool Latencies

| Tool | Calls | Errors | Avg (s) | p50 (s) | p90 (s) | p99 (s) | Total (s) |
|------|-------|--------|---------|---------|---------|---------|-----------|
| store_message | 10,601 | 2 | 0.023 | 0.05 | 0.05 | 0.10 | 241.5 |
| mem_add | 16 | 0 | 5.170 | 5.00 | 10.00 | 10.00 | 82.7 |
| imperator_chat | 5 | 0 | 11.183 | 30.00 | 30.00 | 30.00 | 55.9 |
| get_context | 179 | 0 | 0.140 | 0.10 | 0.50 | 5.00 | 25.1 |
| mem_search | 6 | 0 | 1.328 | 5.00 | 5.00 | 5.00 | 8.0 |
| search_knowledge | 4 | 0 | 1.048 | 1.00 | 5.00 | 5.00 | 4.2 |
| install_stategraph | 2 | 0 | 1.918 | 5.00 | 5.00 | 5.00 | 3.8 |
| mem_delete | 2 | 0 | 1.532 | 5.00 | 5.00 | 5.00 | 3.1 |
| mem_get_context | 2 | 0 | 1.488 | 1.00 | 5.00 | 5.00 | 3.0 |
| search_messages | 8 | 8 | 0.349 | 0.50 | 0.50 | 0.50 | 2.8 |
| conv_search | 2 | 0 | 0.673 | 1.00 | 1.00 | 1.00 | 1.4 |
| conv_create_conversation | 41 | 0 | 0.028 | 0.05 | 0.05 | 0.10 | 1.1 |
| search_logs | 2 | 0 | 0.390 | 0.50 | 0.50 | 0.50 | 0.8 |
| conv_search_messages | 2 | 2 | 0.259 | 0.50 | 0.50 | 0.50 | 0.5 |
| conv_store_message | 14 | 0 | 0.035 | 0.05 | 0.05 | 0.10 | 0.5 |
| conv_list_conversations | 27 | 0 | 0.011 | 0.01 | 0.05 | 0.10 | 0.3 |
| metrics_get | 8 | 0 | 0.030 | 0.01 | 0.50 | 0.50 | 0.2 |
| conv_create_context_window | 8 | 0 | 0.029 | 0.05 | 0.05 | 0.05 | 0.2 |
| conv_delete_conversation | 4 | 0 | 0.056 | 0.10 | 0.10 | 0.10 | 0.2 |
| mem_list | 10 | 0 | 0.014 | 0.01 | 0.05 | 0.05 | 0.1 |
| conv_retrieve_context | 4 | 0 | 0.033 | 0.05 | 0.10 | 0.10 | 0.1 |
| conv_get_history | 8 | 2 | 0.009 | 0.01 | 0.05 | 0.05 | 0.1 |
| conv_search_context_windows | 4 | 0 | 0.008 | 0.01 | 0.05 | 0.05 | 0.0 |
| query_logs | 4 | 0 | 0.003 | 0.01 | 0.01 | 0.01 | 0.0 |
| completely_nonexistent_tool_xyz | 2 | 2 | 0.000 | 0.01 | 0.01 | 0.01 | 0.0 |

## Chat (Imperator) Latencies

| Metric | Value |
|--------|-------|
| Total calls | 139 |
| Total time | 1482.5s |
| Average | 10.7s |
| p50 | 10.0s |
| p90 | 30.0s |
| p99 | 60.0s |

## Background Job Performance

| Job Type | Completed | Errors | Avg (s) | p50 (s) | p90 (s) | Total (s) |
|----------|-----------|--------|---------|---------|---------|-----------|
| embed_batch | 10,417 | 0 | 1.30 | 5.0 | 5.0 | 508.5 |
| extract_memory | 82 | 0 | 18.77 | 30.0 | 60.0 | 1539.2 |
| assemble_context | 125 | 0 | 0.33 | 0.1 | 1.0 | 41.1 |

## Key Findings

- **SLOW**: `imperator_chat` averages 11.2s per call
- **SLOW**: `mem_add` averages 5.2s per call
- **QUEUE NOT DRAINED**: `extraction` queue has 8,923 pending items
- **SLOW CHAT**: Imperator averages 10.7s per response
