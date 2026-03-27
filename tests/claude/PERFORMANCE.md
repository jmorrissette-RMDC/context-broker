# Performance Report

**Generated:** 2026-03-27T14:52:26.638175+00:00
**Source:** Prometheus /metrics endpoint

---

## Database State

| Metric | Value |
|--------|-------|
| Total messages | 30,529 |
| Embedded messages | 30,529 |
| Summaries | 37 |
| Extracted messages | 19,085 |

## Queue Depths (current)

| Queue | Depth |
|-------|-------|
| embedding | empty |
| assembly | empty |
| extraction | **9,362** |

## MCP Tool Latencies

| Tool | Calls | Errors | Avg (s) | p50 (s) | p90 (s) | p99 (s) | Total (s) |
|------|-------|--------|---------|---------|---------|---------|-----------|
| store_message | 31,514 | 2 | 0.024 | 0.05 | 0.05 | 0.10 | 753.5 |
| mem_add | 12 | 0 | 4.787 | 5.00 | 10.00 | 10.00 | 57.4 |
| search_messages | 4 | 0 | 6.321 | 0.50 | 30.00 | 30.00 | 25.3 |
| conv_search_messages | 2 | 0 | 10.868 | 10.00 | 30.00 | 30.00 | 21.7 |
| imperator_chat | 2 | 0 | 4.755 | 5.00 | 5.00 | 5.00 | 9.5 |
| get_context | 132 | 0 | 0.070 | 0.10 | 0.50 | 0.50 | 9.3 |
| mem_search | 6 | 0 | 1.506 | 5.00 | 5.00 | 5.00 | 9.0 |
| install_stategraph | 2 | 0 | 2.790 | 5.00 | 5.00 | 5.00 | 5.6 |
| search_knowledge | 2 | 0 | 1.507 | 5.00 | 5.00 | 5.00 | 3.0 |
| mem_get_context | 2 | 0 | 1.181 | 5.00 | 5.00 | 5.00 | 2.4 |
| conv_create_conversation | 47 | 0 | 0.030 | 0.05 | 0.10 | 0.10 | 1.4 |
| conv_search | 2 | 0 | 0.335 | 0.50 | 0.50 | 0.50 | 0.7 |
| search_logs | 2 | 0 | 0.278 | 0.50 | 0.50 | 0.50 | 0.6 |
| conv_store_message | 14 | 0 | 0.033 | 0.05 | 0.05 | 0.10 | 0.5 |
| mem_list | 16 | 0 | 0.028 | 0.01 | 0.10 | 0.50 | 0.5 |
| conv_create_context_window | 8 | 0 | 0.030 | 0.05 | 0.10 | 0.10 | 0.2 |
| conv_list_conversations | 26 | 0 | 0.009 | 0.01 | 0.05 | 0.05 | 0.2 |
| conv_retrieve_context | 4 | 0 | 0.039 | 0.05 | 0.10 | 0.10 | 0.2 |
| conv_delete_conversation | 4 | 0 | 0.037 | 0.05 | 0.10 | 0.10 | 0.1 |
| metrics_get | 7 | 0 | 0.018 | 0.05 | 0.05 | 0.05 | 0.1 |
| conv_get_history | 6 | 2 | 0.007 | 0.01 | 0.05 | 0.05 | 0.0 |
| conv_search_context_windows | 4 | 0 | 0.008 | 0.01 | 0.05 | 0.05 | 0.0 |
| query_logs | 4 | 0 | 0.003 | 0.01 | 0.01 | 0.01 | 0.0 |
| completely_nonexistent_tool_xyz | 2 | 2 | 0.000 | 0.01 | 0.01 | 0.01 | 0.0 |

## Chat (Imperator) Latencies

| Metric | Value |
|--------|-------|
| Total calls | 96 |
| Total time | 1015.5s |
| Average | 10.6s |
| p50 | 10.0s |
| p90 | 30.0s |
| p99 | 60.0s |

## Background Job Performance

| Job Type | Completed | Errors | Avg (s) | p50 (s) | p90 (s) | Total (s) |
|----------|-----------|--------|---------|---------|---------|-----------|
| embed_batch | 30,529 | 0 | 1.43 | 5.0 | 5.0 | 1021.5 |
| extract_memory | 72 | 52 | 22.72 | 30.0 | 60.0 | 1635.9 |
| assemble_context | 97 | 0 | 0.28 | 0.1 | 0.5 | 27.2 |

## Context Assembly by Build Type

| Build Type | Assemblies | Avg (s) | p50 (s) | p90 (s) | Total (s) |
|------------|-----------|---------|---------|---------|-----------|
| passthrough | 2 | 0.068 | 0.5 | 0.5 | 0.1 |
| standard-tiered | 95 | 0.273 | 0.5 | 0.5 | 25.9 |

## Key Findings

- **SLOW**: `conv_search_messages` averages 10.9s per call
- **SLOW**: `search_messages` averages 6.3s per call
- **HIGH ERROR RATE**: `extract_memory` has 42% error rate (52/124)
- **QUEUE NOT DRAINED**: `extraction` queue has 9,362 pending items
- **SLOW CHAT**: Imperator averages 10.6s per response
