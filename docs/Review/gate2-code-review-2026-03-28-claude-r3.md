The report is ready. Here's the consolidated third-pass review:

---

# Gate 2 Code Review — Round 3 (Claude Opus 4.6)

**35 new findings: 7 blockers, 18 majors, 10 minors**

## BLOCKERS (7)

| ID | File | Description |
|----|------|-------------|
| **B-01** | `tools/system.py:30-37,68` | **Command injection** via shell metacharacters in prefix-allowed commands. `create_subprocess_shell` + `startswith("curl -s ")` allows `curl -s http://x; cat /etc/shadow`. Fix: use `create_subprocess_exec` with `shlex.split()`. |
| **B-02** | `tools/system.py:24` | **`env` in allowlist leaks all secrets.** Dumps `POSTGRES_PASSWORD`, `NEO4J_PASSWORD`, all API keys. LLM can be prompt-injected to exfiltrate. Fix: remove `env` from `_ALLOWED_COMMANDS`. |
| **B-03** | `tools/web.py:44-96` | **SSRF** — `web_read` has zero URL validation. Can hit `http://169.254.169.254/`, internal Docker services, `file://` URIs. Fix: validate scheme, block RFC 1918/link-local/localhost. |
| **B-04** | `alerter/alerter.py:342-348` | **Alerter queries non-existent columns.** `SELECT level, timestamp FROM system_logs` but schema has `log_timestamp` and no `level` column. Every invocation fails — log-context enrichment is completely broken. |
| **B-05** | `log_shipper/shipper.py:220-235` | **Log shipper writes no `level` field.** Even if B-04 is fixed, the shipper stores `{"raw": line}` with no extracted level. Alerter severity filtering can never match. |
| **B-06** | `search_flow.py:346-373` | **SQL parameter index mismatch.** `min_content_length` literal filter inflates index counter. `date_from`/`date_to` bind params are off by 1 — silently wrong query results when all filters active. |
| **B-07** | `context_assembly.py:24` | **ImportError** — imports `_atomic_lock_release` from `standard_tiered` but the symbol doesn't exist (grep confirmed). Any import of the shim crashes. |

## MAJORS (18)

| ID | File | Description |
|----|------|-------------|
| **M-01** | `main.py:58-81,144-148` | Background worker never started after Postgres retry succeeds — embedding/extraction permanently missing. |
| **M-02** | `mcp.py:829-834`, `models.py:144` | `mem_get_context` MCP schema uses `user_prompt` but Pydantic model expects `query` — tool always fails. |
| **M-03** | `credentials/.env.local-backup:4` | Committed credential file with `POSTGRES_PASSWORD=contextbroker123`. Not in `.gitignore`. |
| **M-04** | `credentials/postgres_password.txt` | Committed placeholder credential. Not gitignored. |
| **M-05** | `migrations.py:336` | Migration 014 `DROP TABLE IF EXISTS system_logs` — destroys data on re-run. Others use `CREATE IF NOT EXISTS`. |
| **M-06** | `knowledge_enriched.py:156`, `standard_tiered.py:868` | Advisory lock leak if exception between `pg_try_advisory_lock` and `pg_advisory_unlock`. Blocks future assembly on pooled connection. |
| **M-07** | `conversation_ops_flow.py:575-601` | V2 user message insert skips `total_messages`/`estimated_token_count` counter update — counters drift. |
| **M-08** | `conversation_ops_flow.py:575-601` | V2 insert skips duplicate-collapse check — repeated calls accumulate duplicate messages. |
| **M-09** | `conversation_ops_flow.py:557` | NoneType crash: concurrent-create fallback accesses `row["id"]` without None check. |
| **M-10** | `scheduler.py:183-191` | Fire-and-forget tasks stored in local var — can be GC'd and silently cancelled. Exceptions swallowed. |
| **M-11** | `scheduler.py:27-54,198` | Bad cron expression (`*/abc`) crashes entire poll cycle — all schedules skipped, not just the bad one. |
| **M-12** | `database.py:22-40` | `init_postgres` leaks old pool on retry — orphaned connections never closed. |
| **M-13** | `deploy.sh:81` | `--down` uses `-v` flag, destroying all data volumes including Postgres. No confirmation. |
| **M-14** | `deploy.sh:85-91` | `--down` hardcodes `data-test` cleanup regardless of prefix — cleans wrong directory. |
| **M-15** | `admin.py:89` | `SET statement_timeout` is session-scoped, leaks into connection pool. Fix: `SET LOCAL`. |
| **M-16** | `web.py:29` | `DDGS().text()` is synchronous HTTP — blocks event loop. Needs `run_in_executor`. |
| **M-17** | `admin.py:124-160` | `config_write`/`change_inference` do blocking file I/O in async context (unlike `config_read` which uses executor). |
| **M-18** | `nginx.conf` (all locations) | `add_header` missing `always` — security headers silently dropped on 4xx/5xx responses. |

## MINORS (10)

| ID | File | Description |
|----|------|-------------|
| **m-01** | `mcp.py:59-91,130` | `_total_queued_messages` double-decremented by evictor and consumer — drifts to 0, defeats memory-pressure eviction. |
| **m-02** | `admin.py:29` | `_redact_config` regex uses `_token` not `token` — bare `token` keys leak unredacted. |
| **m-03** | `system.py:33` | `docker inspect` prefix leaks container env vars, volumes, network config of other containers. |
| **m-04** | `scheduling.py:57-101` | No cron expression validation in `create_schedule` — invalid expressions stored and crash scheduler (see M-11). |
| **m-05** | `mem0_client.py:21`, `domain_mem0.py:19` | Module-level `asyncio.Lock()` — breaks across event loops on Python <3.10 or multi-loop test setups. |
| **m-06** | `db_worker.py:236` | `_conv_failures` dict grows unbounded — permanently-failed conversations never pruned. |
| **m-07** | `health.py:21`, `metrics.py:20` | Flow singletons not invalidated on `install_stategraph` — updated flows require restart. |
| **m-08** | `init.sql:61` | `embedding vector` has no dimension constraint — allows mixed dimensions, corrupts similarity search. |
| **m-09** | `main.py:238-239` | Middleware exempt paths use exact match — `/health/` with trailing slash returns 503. |
| **m-10** | `seed_knowledge.py:182-186` | Concurrent startup can double-seed domain knowledge — no locking on empty check. |

---

The highest-priority cluster is **security** (B-01, B-02, B-03, M-03, M-04, m-03) — the system tools allow arbitrary command execution, secret exfiltration, and SSRF via the LLM. The second cluster is **alerter/log-shipper schema mismatch** (B-04, B-05) — the entire log-context feature is dead code. Third is the **search filter index bug** (B-06) which silently corrupts date-filtered search results.
