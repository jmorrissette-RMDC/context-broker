# Performance Report

**Generated:** 2026-03-27T17:31:20.691614+00:00
**Source:** Prometheus /metrics endpoint

---

## Database State

| Metric | Value |
|--------|-------|
| Total messages | 30,647 |
| Embedded messages | 30,647 |
| Summaries | 62 |
| Extracted messages | 25,450 |

## Queue Depths (current)

| Queue | Depth |
|-------|-------|
| embedding | empty |
| assembly | empty |
| extraction | **5,195** |

## MCP Tool Latencies

| Tool | Calls | Errors | Avg (s) | p50 (s) | p90 (s) | p99 (s) | Total (s) |
|------|-------|--------|---------|---------|---------|---------|-----------|
| store_message | 31,628 | 3 | 0.025 | 0.05 | 0.05 | 0.10 | 804.2 |
| mem_add | 21 | 0 | 4.019 | 5.00 | 5.00 | 10.00 | 84.4 |
| search_messages | 6 | 0 | 4.624 | 0.50 | 10.00 | 10.00 | 27.8 |
| conv_search_messages | 3 | 0 | 8.550 | 10.00 | 10.00 | 10.00 | 25.6 |
| mem_search | 9 | 0 | 1.783 | 5.00 | 5.00 | 5.00 | 16.1 |
| get_context | 199 | 0 | 0.065 | 0.10 | 0.50 | 0.50 | 12.9 |
| install_stategraph | 3 | 0 | 2.848 | 5.00 | 5.00 | 5.00 | 8.5 |
| imperator_chat | 3 | 0 | 2.575 | 5.00 | 5.00 | 5.00 | 7.7 |
| search_knowledge | 3 | 0 | 1.121 | 5.00 | 5.00 | 5.00 | 3.4 |
| mem_get_context | 3 | 0 | 0.906 | 1.00 | 5.00 | 5.00 | 2.7 |
| conv_create_conversation | 67 | 0 | 0.030 | 0.05 | 0.10 | 0.10 | 2.0 |
| conv_search | 3 | 0 | 0.360 | 0.50 | 0.50 | 0.50 | 1.1 |
| search_logs | 3 | 0 | 0.241 | 0.50 | 0.50 | 0.50 | 0.7 |
| conv_store_message | 21 | 0 | 0.026 | 0.05 | 0.05 | 0.05 | 0.5 |
| mem_list | 24 | 0 | 0.018 | 0.05 | 0.05 | 0.10 | 0.4 |
| conv_list_conversations | 39 | 0 | 0.010 | 0.01 | 0.05 | 0.10 | 0.4 |
| conv_create_context_window | 12 | 0 | 0.027 | 0.05 | 0.05 | 0.05 | 0.3 |
| metrics_get | 12 | 0 | 0.022 | 0.01 | 0.05 | 0.50 | 0.3 |
| conv_delete_conversation | 6 | 0 | 0.031 | 0.05 | 0.05 | 0.05 | 0.2 |
| conv_retrieve_context | 6 | 0 | 0.026 | 0.05 | 0.05 | 0.05 | 0.2 |
| query_logs | 6 | 0 | 0.017 | 0.01 | 0.10 | 0.10 | 0.1 |
| conv_get_history | 9 | 3 | 0.009 | 0.01 | 0.05 | 0.05 | 0.1 |
| conv_search_context_windows | 6 | 0 | 0.008 | 0.01 | 0.05 | 0.05 | 0.1 |
| completely_nonexistent_tool_xyz | 3 | 3 | 0.000 | 0.01 | 0.01 | 0.01 | 0.0 |

## Chat (Imperator) Latencies

| Metric | Value |
|--------|-------|
| Total calls | 145 |
| Total time | 1369.8s |
| Average | 9.4s |
| p50 | 10.0s |
| p90 | 30.0s |
| p99 | 30.0s |

## Background Job Performance

| Job Type | Completed | Errors | Avg (s) | p50 (s) | p90 (s) | Total (s) |
|----------|-----------|--------|---------|---------|---------|-----------|
| embed_batch | 30,645 | 0 | 1.54 | 5.0 | 5.0 | 1199.1 |
| extract_memory | 0 | 97 | 0.00 | inf | inf | 0.0 |
| assemble_context | 137 | 0 | 0.39 | 0.1 | 1.0 | 52.7 |

## Context Assembly by Build Type

| Build Type | Assemblies | Avg (s) | p50 (s) | p90 (s) | Total (s) |
|------------|-----------|---------|---------|---------|-----------|
| sliding-window | 3 | 0.043 | 0.5 | 0.5 | 0.1 |
| tiered-summary | 134 | 0.383 | 0.5 | 1.0 | 51.3 |

## Key Findings

- **SLOW**: `conv_search_messages` averages 8.6s per call
- **HIGH ERROR RATE**: `extract_memory` has 100% error rate (97/97)
- **QUEUE NOT DRAINED**: `extraction` queue has 5,195 pending items
