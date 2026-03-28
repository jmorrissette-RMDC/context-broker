# Gate 2 Code Review — Pass 2: Context Broker

**Date:** 2026-03-28
**Reviewer:** Gemini CLI
**Status:** Completed
**Focus:** Logic Bugs, Resource Management, Async Correctness, and Architectural Integrity.

---

## Executive Summary

This second-pass review identifies critical logic and resource management issues missed in previous iterations. While the core StateGraph architecture is robust, the implementation of supporting infrastructure (Alerter, DB Workers, and MCP transport) contains several "fail-soft" patterns that can lead to permanent data loss, connection leaks, or system crashes under specific conditions.

---

## NEW Findings

### A. Logic & Critical Bugs

| ID | File | Line | Severity | Description | Fix |
|:---|:---|:---:|:---:|:---|:---|
| **L-01** | `alerter/alerter.py` | 196-200 | **Blocker** | Broken SQL in `_fetch_log_context`. | The query refers to `level` and `timestamp` columns, but the `system_logs` table (defined in `init.sql`) uses `data->>'level'` and `log_timestamp`. Webhook processing will crash if log context is enabled. |
| **L-02** | `app/workers/db_worker.py` | 215-220 | **Major** | Extraction "success" marked before work. | `_extraction_worker` updates `memory_extracted = TRUE` *before* attempting extraction. If the worker crashes or the extraction fails, the message is never retried, leading to permanent knowledge graph gaps. |
| **L-03** | `app/database.py` | 25-35 | **Major** | Connection pool leak in retry loop. | `init_postgres` overwrites the global `_pg_pool` without closing the old one. If `_postgres_retry_loop` in `main.py` calls it multiple times (e.g., if migrations fail), previous pool handles are leaked. |
| **L-04** | `app/workers/db_worker.py` | 155, 310 | **Minor** | Scientific notation in vector strings. | Using `str(v)` for vector components can produce `1e-05` for small floats. `pgvector` parsing may fail or lose precision depending on the specific Postgres cast. Use `f"{v:f}"` for formatting. |
| **L-05** | `app/migrations.py` | 95-100 | **Minor** | Migration 009 fails on empty database. | The migration only types the `embedding` column if data is present. If the first message is stored after migration, the column remains untyped, and the HNSW index creation may fail. |

### B. Resource Management & Concurrency

| ID | File | Line | Severity | Description | Fix |
|:---|:---|:---:|:---:|:---|:---|
| **R-01** | `app/routes/mcp.py` | 55-150 | **Major** | Idle MCP sessions never evicted. | `_evict_stale_sessions` only runs when a *new* session is created. If the broker becomes idle, stale sessions and their message queues stay in memory indefinitely. |
| **R-02** | `app/stategraph_registry.py` | 125-135 | **Major** | Partial module eviction on reload. | `_evict_package_modules` assumes all package modules start with the `package_name` prefix. If a package uses sub-packages or dynamic modules with different names, they aren't evicted, leading to "Frankenstein" states after hot-reload. |

### C. Async & Architectural Correctness

| ID | File | Line | Severity | Description | Fix |
|:---|:---|:---:|:---:|:---|:---|
| **A-01** | `app/flows/install_stategraph.py` | 66 | **Major** | Blocking `scan()` freezes event loop. | `install_stategraph` is an async MCP tool, but it calls `scan()` (which performs blocking `entry_points` loading) directly. This blocks the entire ASGI server during runtime package installation. |
| **A-02** | `packages/.../message_pipeline.py` | 45-55 | **Major** | Tool error reclassification false positives. | `_detect_tool_error_role` reclassifies messages matching error patterns. A user message starting with "Error reading..." could be incorrectly flagged as a `tool` role and hidden from history. |

### D. Deployment & Security

| ID | File | Line | Severity | Description | Fix |
|:---|:---|:---:|:---:|:---|:---|
| **D-01** | `entrypoint.sh` | 134 | **Minor** | Silent directory creation failure. | `mkdir -p /data/downloads || true` fails silently if the host-mounted volume has incorrect permissions. The app will later crash when attempting to write downloads. Remove `|| true` and ensure `/data` is writable. |
| **D-02** | `log_shipper/Dockerfile` | 18 | **Minor** | Non-deterministic Docker group GID. | Adding `shipper` to the `docker` group inside the container fails to grant access if the GID doesn't match the host's `/var/run/docker.sock`. Use `groupmod -g <HOST_GID> docker` or run as root/privileged. |

---

## Conclusion

While the previous 24 fixes significantly improved the codebase, these new findings highlight critical gaps in the "glue" logic—particularly in how the system handles background work and resource cleanup. Addressing the **L-01 Blocker** and the **L-02/L-03 Major** logic bugs is mandatory before the system can be considered stable for production use.
