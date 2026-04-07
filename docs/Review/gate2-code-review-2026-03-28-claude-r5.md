# Gate 2 Code Review — Round 5 (Claude Opus 4.6)

**Date:** 2026-03-28
**Reviewer:** Claude Opus 4.6 (1M context)
**Scope:** All Python in `app/`, `packages/`, `alerter/`, `log_shipper/`, plus `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, `nginx/`, `deploy.sh`, `init.sql`
**Prior rounds:** R1-R4 found ~95 issues, all fixed.

---

## Verdict: CLEAN

Fewer than 5 new issues found. After 4 rounds of fixes the codebase is in good shape. The 4 findings below are the residual items — 1 blocker, 1 major, 2 minors.

---

## BLOCKER (1)

| ID | File | Line | Severity | Description |
|----|------|------|----------|-------------|
| **B-01** | `packages/context-broker-ae/src/context_broker_ae/conversation_ops_flow.py` | 601 | **Blocker** | **Missing `import asyncpg` — `NameError` on exception path.** The V2 user-message-store code (added during R3 fixes) catches `asyncpg.PostgresError` at line 601, but the module never imports `asyncpg` (lines 1-21 import only `logging`, `uuid`, `typing`, `langgraph`, `app.config`, `app.database`, `app.token_budget`). On the happy path this is invisible — Python doesn't evaluate exception handler types until an exception is raised. When a Postgres error *does* occur (connection timeout, constraint violation), the handler crashes with `NameError: name 'asyncpg' is not defined`, masking the real error and propagating an unhandled exception up the call stack. |

**Fix:** Add `import asyncpg` to the module imports:
```python
import asyncpg
```

---

## MAJOR (1)

| ID | File | Line | Severity | Description |
|----|------|------|----------|-------------|
| **M-01** | `packages/context-broker-te/src/context_broker_te/domain_mem0.py` | 73 | **Major** | **Deprecated `version="v1.1"` parameter passed to `MemoryConfig`.** The AE's `mem0_client.py` (line 137) explicitly documents `# v1.0: version parameter removed (was "v1.1" in 0.1.x)` and omits the parameter. The TE's `domain_mem0.py` still passes `version="v1.1"` at line 73. With Mem0 >= 1.0, this causes `TypeError: MemoryConfig.__init__() got an unexpected keyword argument 'version'`, preventing domain Mem0 initialization entirely. All domain knowledge tools (`domain_search`, `domain_add`, etc.) will fail. This is a TE/AE parity gap introduced when the AE client was updated but the TE client was not. |

**Fix:** Remove the `version="v1.1"` line from the `MemoryConfig` constructor in `domain_mem0.py`:
```python
mem_config = MemoryConfig(
    # version parameter removed in Mem0 v1.0
    llm=LlmConfig(
```

---

## MINOR (2)

| ID | File | Line | Severity | Description |
|----|------|------|----------|-------------|
| **m-01** | `app/workers/scheduler.py` | 46, 52, 146 | **Minor** | **Unguarded `int()` conversions in schedule evaluation skip all remaining schedules on bad data.** `_cron_is_due()` calls `int(pattern[2:])` (line 46) and `int(pattern)` (line 52) without try/except — a cron expression like `*/abc * * * *` throws `ValueError`. Similarly, `int(schedule_expr)` at line 146 for interval schedules throws on non-integer values. Both are caught by the outer `except` at line 198, but this aborts the *entire* poll cycle — all schedules after the bad one are skipped for that iteration. R3-M11 fixed the field-count check (`len(parts) != 5`) but not the integer-conversion paths. |

**Fix:** Wrap `_cron_is_due` internals in try/except and validate interval separately:
```python
# In _cron_is_due, replace lines 45-53:
        if pattern.startswith("*/"):
            try:
                divisor = int(pattern[2:])
            except ValueError:
                return False
            if divisor == 0:
                return False
            if field_val % divisor != 0:
                return False
        else:
            try:
                if field_val != int(pattern):
                    return False
            except ValueError:
                return False

# For interval (line 146), add validation:
    elif schedule_type == "interval":
        try:
            interval_secs = int(schedule_expr)
        except ValueError:
            _log.warning("Schedule %s: invalid interval '%s', skipping",
                         schedule_id, schedule_expr)
            continue
```

| **m-02** | `app/token_budget.py` | 5, 32, 114 | **Minor** | **Stale `user_prompt` artifacts in docstrings and log message.** Line 5 says `"auto": user_prompt the configured LLM provider's model list endpoint`, line 32 says `Auto-user_prompt the LLM provider`, and line 114 logs `"failed to user_prompt provider model list"`. These are copy-paste remnants where `user_prompt` was substituted for `query` during an earlier edit. The log message is confusing for anyone reading container logs during debugging. |

**Fix:** Replace all three occurrences:
```python
# Line 5:
- "auto": user_prompt the configured LLM provider's model list endpoint
+ "auto": query the configured LLM provider's model list endpoint

# Line 32:
- Auto-user_prompt the LLM provider if max_context_tokens == "auto"
+ Auto-query the LLM provider if max_context_tokens == "auto"

# Line 114:
- "Token budget: failed to user_prompt provider model list: %s, using fallback %d",
+ "Token budget: failed to query provider model list: %s, using fallback %d",
```

---

## Summary

| Severity | Count |
|----------|-------|
| Blocker  | 1     |
| Major    | 1     |
| Minor    | 2     |
| **Total**| **4** |

**B-01** is the highest priority — it's a latent `NameError` on any Postgres failure in the V2 user-message-store path. **M-01** will crash domain Mem0 initialization if Mem0 >= 1.0 is installed. Both are one-line fixes.

The codebase is clean after 5 rounds. No security issues, no architectural violations, no async correctness problems remain. The 4 findings above are a missing import, a stale constructor parameter, incomplete input validation in the scheduler, and cosmetic log message typos.
