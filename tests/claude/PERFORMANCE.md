# Performance Report

**Generated:** 2026-03-27T20:21:28.847230+00:00
**Source:** Prometheus /metrics endpoint

---

## Database State

| Metric | Value |
|--------|-------|
| Total messages | 51,077 |
| Embedded messages | 51,076 |
| Summaries | 101 |
| Extracted messages | 50,587 |

## Queue Depths (current)

| Queue | Depth |
|-------|-------|
| embedding | empty |
| assembly | **1** |
| extraction | **489** |

## MCP Tool Latencies

| Tool | Calls | Errors | Avg (s) | p50 (s) | p90 (s) | p99 (s) | Total (s) |
|------|-------|--------|---------|---------|---------|---------|-----------|
| store_message | 10,494 | 1 | 0.022 | 0.05 | 0.05 | 0.10 | 235.3 |
| mem_add | 7 | 0 | 3.753 | 5.00 | 5.00 | 5.00 | 26.3 |
| get_context | 66 | 0 | 0.087 | 0.10 | 0.50 | 0.50 | 5.8 |
| imperator_chat | 1 | 0 | 4.665 | 5.00 | 5.00 | 5.00 | 4.7 |
| mem_search | 3 | 0 | 1.465 | 5.00 | 5.00 | 5.00 | 4.4 |
| install_stategraph | 1 | 0 | 2.779 | 5.00 | 5.00 | 5.00 | 2.8 |
| mem_get_context | 1 | 0 | 0.915 | 1.00 | 1.00 | 1.00 | 0.9 |
| search_knowledge | 1 | 0 | 0.902 | 1.00 | 1.00 | 1.00 | 0.9 |
| conv_create_conversation | 22 | 0 | 0.032 | 0.05 | 0.05 | 0.10 | 0.7 |
| conv_search | 1 | 0 | 0.692 | 1.00 | 1.00 | 1.00 | 0.7 |
| search_messages | 2 | 0 | 0.298 | 0.50 | 0.50 | 0.50 | 0.6 |
| search_logs | 1 | 0 | 0.586 | 1.00 | 1.00 | 1.00 | 0.6 |
| conv_list_conversations | 13 | 0 | 0.023 | 0.01 | 0.10 | 0.10 | 0.3 |
| conv_store_message | 7 | 0 | 0.043 | 0.05 | 0.10 | 0.10 | 0.3 |
| conv_search_messages | 1 | 0 | 0.226 | 0.50 | 0.50 | 0.50 | 0.2 |
| conv_create_context_window | 4 | 0 | 0.034 | 0.05 | 0.05 | 0.05 | 0.1 |
| conv_retrieve_context | 2 | 0 | 0.050 | 0.05 | 0.10 | 0.10 | 0.1 |
| conv_delete_conversation | 2 | 0 | 0.033 | 0.05 | 0.05 | 0.05 | 0.1 |
| mem_list | 8 | 0 | 0.008 | 0.01 | 0.05 | 0.05 | 0.1 |
| conv_get_history | 3 | 1 | 0.009 | 0.01 | 0.05 | 0.05 | 0.0 |
| metrics_get | 4 | 0 | 0.008 | 0.01 | 0.05 | 0.05 | 0.0 |
| conv_search_context_windows | 2 | 0 | 0.008 | 0.01 | 0.05 | 0.05 | 0.0 |
| completely_nonexistent_tool_xyz | 1 | 1 | 0.000 | 0.01 | 0.01 | 0.01 | 0.0 |
| query_logs | 2 | 0 | 0.002 | 0.01 | 0.01 | 0.01 | 0.0 |

## Chat (Imperator) Latencies

| Metric | Value |
|--------|-------|
| Total calls | 48 |
| Total time | 551.0s |
| Average | 11.5s |
| p50 | 10.0s |
| p90 | 30.0s |
| p99 | 60.0s |

## Background Job Performance

| Job Type | Completed | Errors | Avg (s) | p50 (s) | p90 (s) | Total (s) |
|----------|-----------|--------|---------|---------|---------|-----------|
| embed_batch | 10,214 | 0 | 1.38 | 5.0 | 5.0 | 359.6 |
| extract_memory | 0 | 41 | 0.00 | inf | inf | 0.0 |
| assemble_context | 48 | 0 | 0.31 | 0.1 | 1.0 | 14.9 |

## Key Findings

- **HIGH ERROR RATE**: `extract_memory` has 100% error rate (41/41)
- **QUEUE NOT DRAINED**: `assembly` queue has 1 pending items
- **QUEUE NOT DRAINED**: `extraction` queue has 489 pending items
- **SLOW CHAT**: Imperator averages 11.5s per response
