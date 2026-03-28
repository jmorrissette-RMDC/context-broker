The review is complete. Here's the summary:

## Gate 2 Code Review — Pass 2: 22 New Findings (3B / 9M / 10m)

### Blockers (3)

| ID | File | Line | Issue |
|----|------|------|-------|
| **B-01** | `app/workers/scheduler.py` | 193 | Missing `import asyncpg` — `NameError` crashes the scheduler on any DB error |
| **B-02** | `packages/.../seed_knowledge.py` | 187 | Same missing import — `NameError` on first startup with unavailable DB |
| **B-03** | `postgres/init.sql` | 166-171 | `system_logs` has no primary key — unbounded growth, no dedup, no retention |

### Majors (9)

| ID | File | Line | Issue |
|----|------|------|-------|
| **M-01** | `app/workers/scheduler.py` | 44-45 | Cron `*/0` causes `ZeroDivisionError` |
| **M-02** | `app/workers/scheduler.py` | 179 | Fire-and-forget `create_task` — exceptions silently lost, history rows stuck in `running` |
| **M-03** | `log_shipper/shipper.py` | 345-347 | Task cancelled without await on container disconnect |
| **M-04** | `log_shipper/shipper.py` | 381-393 | Shutdown cancels writer before final flush can complete |
| **M-05** | `nginx/nginx.conf` | 12-71 | No security headers, no `server_tokens off`, no rate limiting |
| **M-06** | `Dockerfile` | 13-17 | `build-essential` (~150MB) left in final image |
| **M-07** | `deploy.sh` | 38 | Hardcoded to test compose file — can't deploy production |
| **M-08** | `entrypoint.sh` | 99-111 | `|| echo` swallows pip failures — violates REQ-001 §7.4 fail-fast |
| **M-09** | `app/migrations.py` | 130-132 | f-string SQL with unvalidated `dim` value |

### Minors (10)

| ID | File | Issue |
|----|------|-------|
| **m-01** | `config/alerter.yml:29-30` | Personal email addresses in committed config |
| **m-02** | `log_shipper/Dockerfile:8` | `pip install` runs as root |
| **m-03** | `app/routes/mcp.py:288` | Double-check pattern needs explanatory comment |
| **m-04** | `app/flows/tool_dispatch.py:595` | ILIKE wildcard chars (`%`, `_`) not escaped in keyword |
| **m-05** | `alerter/alerter.py:231` | Same ILIKE escaping gap in alerter |
| **m-06** | `postgres/init.sql:129` | `conversation_summaries.conversation_id` FK missing index |
| **m-07** | `postgres/init.sql` | Missing index on `data->>'level'` for log queries |
| **m-08** | `search_flow.py:341` | Hardcoded `min_content_length = 50` (REQ-001 §8.2) |
| **m-09** | `docker-compose.claude-test.yml:157,170` | Fallback password `contextbroker123` in DSN |
| **m-10** | `docker-compose.yml` | No `deploy.resources.limits` on any container |

The review document is ready to write to `docs/Review/gate2-code-review-2026-03-28-claude-r2.md` when you approve the write permission.
