# Gate 2 Code Review — Pass 4: Context Broker

**Date:** 2026-03-28
**Reviewer:** Gemini CLI
**Status:** Action Required
**Focus:** Data Integrity, Async Efficiency, and State Persistence.

---

## Executive Summary

Round 4 has identified critical regressions and architectural gaps that survived the previous "all fixed" status of 81 issues. While significant progress was made in modularizing the AE logic into a package, the core mandate of AE/TE separation remains violated. Furthermore, a new data integrity issue was discovered in the tiered summarization logic that would lead to incorrect conversation history tracking. The system also suffers from severe async inefficiencies in the alerting layer and unstable database identifiers in background workers.

---

## NEW Findings (Pass 4)

### 1. Data Integrity: Truncated Metadata in Archival Consolidation
- **File:** `packages/context-broker-ae/src/context_broker_ae/build_types/standard_tiered.py`
- **Line:** 678-685
- **Severity:** Major
- **Description:** When consolidating Tier 2 summaries into a new Tier 1 archival summary, the code includes the *content* of the old Tier 1 summary but fails to include its *metadata* in the new database record. Specifically, `summarizes_from_seq` and `message_count` are derived only from the new Tier 2 chunks, resulting in a new Tier 1 record that incorrectly reports a truncated sequence range and an undercounted message total.
- **Fix:** Update the metadata calculation to include `existing_t1["summarizes_from_seq"]` and `existing_t1["message_count"]` when an existing archival summary is merged.

### 2. Async Inefficiency: Serial Webhook Fan-out
- **File:** `alerter/alerter.py`
- **Line:** 299-305
- **Severity:** Major
- **Description:** The `deliver_alert` function iterates through notification channels (Slack, Discord, webhooks) and awaits each delivery sequentially. Because the Imperator calls the Alerter via a tool-dispatch, a single slow webhook (e.g., 10s timeout) blocks the Imperator's entire turn, wasting expensive inference resources and delaying user responses.
- **Fix:** Use `asyncio.gather` with `return_exceptions=True` to fan out notifications to all channels in parallel.

### 3. Database Stability: Unstable CTID Usage in Log Worker
- **File:** `app/workers/db_worker.py`
- **Line:** 346, 360, 375
- **Severity:** Major
- **Description:** The `_log_embedding_worker` uses PostgreSQL's internal `ctid` (physical row location) to identify logs for update and deletion. `ctid` is unstable and can change during vacuuming or concurrent updates, potentially causing the worker to update the wrong row or fail silently. Additionally, the worker deletes logs on embedding failure, causing permanent data loss for non-vectorized logs.
- **Fix:** Use the `id` UUID primary key (added in Migration 014) for all updates/deletes. Replace `DELETE` with a fallback zero-vector update to preserve logs.

### 4. Persistence: Ephemeral StateGraph Installations
- **File:** `Dockerfile`, `docker-compose.yml`, `app/flows/install_stategraph.py`
- **Severity:** Major
- **Description:** REQ-001 §10.1 mandates runtime StateGraph installation. The current implementation uses `pip install --user`, which writes to `/home/context-broker/.local`. However, this directory is not a volume in `docker-compose.yml` nor is it persisted in the `Dockerfile`. Any package installed via the `install_stategraph` tool is lost upon container restart/recreation.
- **Fix:** Mount a named volume to `/home/context-broker/.local` in `docker-compose.yml` to ensure runtime-installed packages persist.

### 5. Error Handling: Missing asyncpg Exceptions in Lock Logic
- **File:** `packages/context-broker-ae/src/context_broker_ae/build_types/standard_tiered.py`
- **Line:** 90-95
- **Severity:** Minor
- **Description:** The `acquire_assembly_lock` function catches `RuntimeError`, `OSError`, and `ConnectionError` but fails to catch `asyncpg.PostgresError`. If a database connection fails during lock acquisition, the flow will raise an unhandled exception instead of returning a graceful error state. The log message also erroneously mentions "Redis" despite the switch to advisory locks.
- **Fix:** Add `asyncpg.PostgresError` to the exception list and update the log message to "Database unavailable".

---

## UNRESOLVED Issues (Missed Fixes from r3)

### S-01. Improper AE/TE Separation (Critical)
- **File:** `packages/context-broker-te/src/context_broker_te/imperator_flow.py`
- **Description:** Still contains 7 direct imports from `app.config` and `app.database`. REQ-001 §12.3 and §13.2 mandate that TE packages must not import from a specific AE implementation.
- **Status:** **NOT FIXED**. Previous claim of "all fixed" is incorrect for this file.

### S-04. Blocking Graph Compilation (Major)
- **File:** `app/flows/build_type_registry.py`
- **Description:** Graph compilation still occurs inside a `threading.Lock` within `get_assembly_graph`. This blocks the event loop for all concurrent requests during the compilation of complex graphs.
- **Status:** **NOT FIXED**.
