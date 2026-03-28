# Gate 2 Code Review — Claude Opus 4.6

**Date:** 2026-03-28
**Reviewer:** Claude Opus 4.6 (1M context)
**Scope:** All Python files in `app/`, `packages/`, `alerter/`, `log_shipper/` plus `docker-compose.yml`, `Dockerfile`, `entrypoint.sh`, `nginx/nginx.conf`, `deploy.sh`
**Requirements:** REQ-001 (MAD Engineering), REQ-002 (pMAD Engineering), DESIGN-context-retrieval-v2

---

## Summary

| Severity | Count |
|----------|-------|
| Blocker  | 5     |
| Major    | 35    |
| Minor    | 52    |

---

## Blockers

### BLK-01. `env` command in system tool allowlist exposes all secrets

| Field | Value |
|-------|-------|
| **File** | `packages/context-broker-te/src/context_broker_te/tools/system.py` |
| **Line** | 26 |
| **Category** | Security |

**Description:** `"env"` is in `_ALLOWED_COMMANDS`. The Imperator (or any user talking to it) can run `env` to dump ALL environment variables including `POSTGRES_PASSWORD`, API keys, and any other secrets injected via Docker environment. Combined with `docker inspect` (BLK-04), this provides full secret exfiltration.

**Fix:** Remove `"env"` from `_ALLOWED_COMMANDS`. If environment inspection is needed, add a filtered version that redacts variables matching secret patterns (like `_redact_config` does for config).

---

### BLK-02. `alerter/Dockerfile` runs as root (no USER directive)

| Field | Value |
|-------|-------|
| **File** | `alerter/Dockerfile` |
| **Lines** | 1–15 (entire file) |
| **Category** | REQ-002 §1.1, §1.2 |

**Description:** No user creation and no `USER` directive. The alerter process runs as root. REQ-002 requires root only for system package installation, then `USER` directive with non-root service account.

**Fix:** Add `RUN groupadd --gid 1001 alerter && useradd --uid 1001 --gid 1001 alerter` after system packages, then `USER alerter` before `CMD`.

---

### BLK-03. `log_shipper/Dockerfile` runs as root (no USER directive)

| Field | Value |
|-------|-------|
| **File** | `log_shipper/Dockerfile` |
| **Lines** | 1–22 (entire file) |
| **Category** | REQ-002 §1.1, §1.2 |

**Description:** Same as BLK-02. No user creation, no `USER` directive. Combined with Docker socket mount (`docker-compose.yml:175`), this gives root-equivalent access on the host.

**Fix:** Create a non-root user with docker group membership. Add `USER` directive.

---

### BLK-04. Command injection / SSRF via system tool allowlist

| Field | Value |
|-------|-------|
| **File** | `packages/context-broker-te/src/context_broker_te/tools/system.py` |
| **Lines** | 33–35 |
| **Category** | Security |

**Description:** `"curl -s "` is an allowed prefix, enabling SSRF to cloud metadata endpoints (`169.254.169.254`), internal services, and localhost-only ports. `"docker inspect "` can reveal environment variables (secrets) of any container on the host. These are LLM-invocable tools — a prompt injection or adversarial user message could trigger them.

**Fix:** Remove `curl -s` from allowed prefixes or restrict to a domain allowlist. For `docker inspect`, filter output to exclude `.Config.Env`, or remove entirely.

---

### BLK-05. No validation on `package_name` in `install_stategraph` — arbitrary pip install

| Field | Value |
|-------|-------|
| **File** | `app/flows/install_stategraph.py` |
| **Lines** | 33, 45 |
| **Category** | Security |

**Description:** `package_name` is passed directly to pip. While `subprocess.run` with a list avoids shell injection, pip interprets specifiers like `git+https://evil.com/repo.git` or local paths like `/tmp/malicious_package`. No validation that the name is a legitimate PyPI package.

**Fix:** Validate against `^[a-zA-Z0-9_-]+$`. Validate version against PEP 440. Consider a package allowlist.

---

## Majors

### MAJ-01. Blanket `except Exception` in 10+ locations (REQ-001 §4.5)

| Field | Value |
|-------|-------|
| **Files** | `packages/context-broker-ae/src/context_broker_ae/memory_admin_flow.py:55,120,177`, `memory_extraction.py:350`, `memory_search_flow.py:78,168`, `conversation_ops_flow.py:601`, `build_types/knowledge_enriched.py:435,652`, `app/workers/db_worker.py:215,363,497,629`, `app/workers/scheduler.py:194`, `packages/context-broker-te/src/context_broker_te/seed_knowledge.py:187,217,228`, `tools/alerting.py:73,107,181,200`, `tools/web.py:77` |
| **Category** | REQ-001 §4.5 compliance |

**Description:** REQ-001 prohibits blanket `except Exception:`. Many locations list specific exceptions *then* add `Exception` at the end (making the specifics redundant). Workers use bare `except Exception` in their main loops.

**Fix:** Remove `Exception` from all tuples. Add the specific exceptions that each call can actually raise (e.g., `asyncpg.PostgresError`, `neo4j.exceptions.ServiceUnavailable`, `httpx.HTTPError`). For worker loops, use a tuple of known infrastructure exceptions.

---

### MAJ-02. Blocking I/O in async — TE filesystem tools

| Field | Value |
|-------|-------|
| **File** | `packages/context-broker-te/src/context_broker_te/tools/filesystem.py` |
| **Lines** | 57–58 (`file_read`), 78–86 (`file_list`), 115–129 (`file_search`), 159–161 (`file_write`), 179–180 (`read_system_prompt`), 211–215 (`update_system_prompt`) |
| **Category** | REQ-001 §5.1 |

**Description:** All filesystem tools perform synchronous `open()`, `os.listdir()`, `os.walk()` directly in async functions without `run_in_executor`. The `config_read` tool in `admin.py` correctly uses `run_in_executor` for the same pattern, proving the codebase knows how to do this.

**Fix:** Wrap all blocking file I/O in `await loop.run_in_executor(None, sync_fn)`.

---

### MAJ-03. Blocking I/O in async — TE admin tools (config_write, change_inference, migrate_embeddings)

| Field | Value |
|-------|-------|
| **File** | `packages/context-broker-te/src/context_broker_te/tools/admin.py` |
| **Lines** | 124–152 (`config_write`), 327–338 (`change_inference`), 407–412 (`migrate_embeddings`) |
| **Category** | REQ-001 §5.1 |

**Description:** Synchronous `open()` + `yaml.safe_load()` + `yaml.dump()` in async functions without `run_in_executor`.

**Fix:** Wrap in `run_in_executor`, consistent with `config_read`.

---

### MAJ-04. Blocking I/O in async — web_search tool

| Field | Value |
|-------|-------|
| **File** | `packages/context-broker-te/src/context_broker_te/tools/web.py` |
| **Line** | 29 |
| **Category** | REQ-001 §5.1 |

**Description:** `DDGS().text(query, max_results=max_results)` is synchronous, blocking the event loop during the HTTP round-trip to DuckDuckGo.

**Fix:** Wrap in `await asyncio.get_running_loop().run_in_executor(None, lambda: DDGS().text(query, max_results=max_results))`.

---

### MAJ-05. Blocking I/O in async — `async_load_config()` on cache miss

| Field | Value |
|-------|-------|
| **File** | `app/config.py` |
| **Lines** | 145–149 |
| **Category** | REQ-001 §5.1 |

**Description:** `async_load_config()` calls synchronous `load_config()` which performs `open()` + `f.read()` + `yaml.safe_load()` on cache miss. The mtime fast-path mitigates this in the common case, but a config file change triggers a full blocking read on the event loop.

**Fix:** Offload `load_config()` to `run_in_executor` on cache miss, matching the TE config pattern.

---

### MAJ-06. Blocking I/O in async — Imperator state manager

| Field | Value |
|-------|-------|
| **File** | `app/imperator/state_manager.py` |
| **Lines** | 90–114 |
| **Category** | REQ-001 §5.1 |

**Description:** `_read_state_file` and `_write_state_file` perform synchronous file I/O, called from `async def initialize()` and `async def get_conversation_id()`.

**Fix:** Use `aiofiles` or `await asyncio.to_thread(self._read_state_file)`.

---

### MAJ-07. Blocking `scan()` in async `install_stategraph`

| Field | Value |
|-------|-------|
| **File** | `app/flows/install_stategraph.py` |
| **Line** | 66 |
| **Category** | REQ-001 §5.1 |

**Description:** `scan()` from `stategraph_registry` is called synchronously inside an async function. It scans `entry_points` and does importlib operations.

**Fix:** `await loop.run_in_executor(None, scan)`.

---

### MAJ-08. Blocking DNS in `resolve_caller`

| Field | Value |
|-------|-------|
| **File** | `app/routes/caller_identity.py` |
| **Line** | 42 |
| **Category** | REQ-001 §5.1 |

**Description:** `socket.gethostbyaddr()` is a blocking DNS call used from async route handlers. The DNS cache mitigates repeat calls, but the first call per IP blocks the event loop.

**Fix:** Use `asyncio.get_event_loop().run_in_executor(None, socket.gethostbyaddr, client_ip)` and make `resolve_caller` async.

---

### MAJ-09. Blocking `_get_tool_list()` in MCP handler

| Field | Value |
|-------|-------|
| **File** | `app/routes/mcp.py` |
| **Lines** | 377–387 |
| **Category** | REQ-001 §5.1 |

**Description:** `_get_tool_list()` calls `load_te_config()` and `load_config()` synchronously (not async variants) on every `tools/list` request.

**Fix:** Cache the tool list or use async config loaders.

---

### MAJ-10. TE imports from AE private implementation (REQ-001 §9/§12.3)

| Field | Value |
|-------|-------|
| **Files** | `packages/context-broker-te/src/context_broker_te/domain_mem0.py:28`, `tools/admin.py:450` |
| **Category** | REQ-001 §12.3 |

**Description:** `from context_broker_ae.memory.mem0_client import _apply_mem0_patches` and `from context_broker_ae.memory.mem0_client import reset_mem0_client` directly import private symbols from the AE. REQ-001 requires the TE must not import from or depend on a specific AE implementation.

**Fix:** Move shared utilities to a common package, or have the TE call through the AE's public API/registry.

---

### MAJ-11. Shallow config merge loses nested keys

| Field | Value |
|-------|-------|
| **File** | `app/config.py` |
| **Lines** | 133–135, 154, 159 |
| **Category** | Correctness |

**Description:** `{**ae_config, **te_config}` is a shallow merge. If both configs have a top-level key like `tuning` with different sub-keys, the TE version entirely replaces the AE version, silently losing AE tuning keys.

**Fix:** Implement a recursive deep merge utility.

---

### MAJ-12. Migration 014 uses `DROP TABLE IF EXISTS` (data loss risk)

| Field | Value |
|-------|-------|
| **File** | `app/migrations.py` |
| **Lines** | 334–335 |
| **Category** | Data safety |

**Description:** `_migration_014` drops and recreates `system_logs`. If run against a database with existing log data (e.g., migration tracker reset), all log data is destroyed. Every other migration uses safe `IF NOT EXISTS` guards.

**Fix:** Replace with `CREATE TABLE IF NOT EXISTS`. If schema changes are needed, use `ALTER TABLE`.

---

### MAJ-13. `tool_dispatch.py` is a 750-line if/elif chain with inline SQL

| Field | Value |
|-------|-------|
| **File** | `app/flows/tool_dispatch.py` |
| **Lines** | 105–853 |
| **Category** | REQ-001 §2.1 architecture |

**Description:** `_dispatch_tool_inner` is ~750 lines of procedural if/elif dispatch with embedded SQL queries. This violates the stated architecture that all tool logic lives in StateGraph flows. It also makes the function very difficult to test.

**Fix:** Extract each inline-SQL branch into its own StateGraph flow. Replace if/elif with a dispatch table.

---

### MAJ-14. Unescaped LIKE wildcards in tool_dispatch SQL

| Field | Value |
|-------|-------|
| **File** | `app/flows/tool_dispatch.py` |
| **Lines** | 579, 595, 598–610, 668–681 |
| **Category** | Security |

**Description:** User input containing LIKE wildcards (`%`, `_`) is wrapped in `%...%` without escaping, allowing query performance attacks via patterns like `%_%_%_%`.

**Fix:** Escape LIKE special characters before wrapping.

---

### MAJ-15. Global mutable `_imperator_flow` without lock

| Field | Value |
|-------|-------|
| **File** | `app/flows/imperator_wrapper.py` |
| **Lines** | 20–21, 30–31 |
| **Category** | Concurrency |

**Description:** `_get_flow()` reads/writes two module-level globals with no lock. Concurrent async tasks on cache-empty can invoke the builder twice, silently discarding one result.

**Fix:** Add a `threading.Lock` or `asyncio.Lock` to guard lazy initialization.

---

### MAJ-16. Global mutable `_prebound_llm` in Imperator flow

| Field | Value |
|-------|-------|
| **File** | `packages/context-broker-te/src/context_broker_te/imperator_flow.py` |
| **Lines** | 214, 681–683 |
| **Category** | State immutability |

**Description:** `_prebound_llm` is a module-level global set during `build_imperator_flow()` and read during `agent_node()`. Multiple builds with different configs silently overwrite, affecting all previously compiled graphs.

**Fix:** Pass the pre-bound LLM via closure or graph config, not a module-level global.

---

### MAJ-17. Builder compilation inside global lock (thread convoy)

| Field | Value |
|-------|-------|
| **File** | `app/flows/build_type_registry.py` |
| **Lines** | 55–69, 77–92 |
| **Category** | Performance |

**Description:** `builder()` is called while holding `_lock`. If compilation is slow, every thread waiting for any build type is blocked.

**Fix:** Double-checked locking: check under lock, compile outside, store under lock with re-check.

---

### MAJ-18. N+1 embedding storage — 50 individual UPDATEs per batch

| Field | Value |
|-------|-------|
| **File** | `app/workers/db_worker.py` |
| **Lines** | 154–159 |
| **Category** | Performance |

**Description:** After batch-embedding, vectors are stored one row at a time in a loop, issuing up to 50 individual `UPDATE` statements per batch.

**Fix:** Use `pool.executemany()` or build a single `UPDATE ... FROM (VALUES ...)` statement.

---

### MAJ-19. Log embedding worker deletes logs on persistent failure

| Field | Value |
|-------|-------|
| **File** | `app/workers/db_worker.py` |
| **Lines** | 601–606 |
| **Category** | Data safety |

**Description:** When log embedding fails 5 times, the worker DELETEs the log entries. A transient embedding model issue causes permanent log data loss. The conversation embedding worker correctly uses a zero-vector fallback.

**Fix:** Use the same zero-vector sentinel approach, or set an `embedding_failed=TRUE` flag column.

---

### MAJ-20. `asyncio.create_task` without reference retention in scheduler

| Field | Value |
|-------|-------|
| **File** | `app/workers/scheduler.py` |
| **Line** | 179 |
| **Category** | Correctness |

**Description:** `asyncio.create_task(_fire_schedule(...))` creates a fire-and-forget task. Python's event loop only holds a weak reference — the task may be garbage-collected before completion.

**Fix:** Store the task: `_running_tasks.add(task); task.add_done_callback(_running_tasks.discard)`.

---

### MAJ-21. Health flow invocation has no exception handling

| Field | Value |
|-------|-------|
| **File** | `app/routes/health.py` |
| **Lines** | 56–70 |
| **Category** | Resilience |

**Description:** `_get_health_flow().ainvoke(...)` is not wrapped in try/except. If the flow raises (e.g., AE package not loaded), the health endpoint returns an unstructured 500 instead of a degraded 503.

**Fix:** Wrap in try/except returning 503 with structured degraded response.

---

### MAJ-22. Metrics endpoint has no exception handling for flow invocation

| Field | Value |
|-------|-------|
| **File** | `app/routes/metrics.py` |
| **Line** | 44 |
| **Category** | Resilience |

**Description:** Same as MAJ-21 but for the metrics endpoint. Prometheus scrapers will log scrape failures.

**Fix:** Wrap in try/except returning Prometheus-compatible error comment with 500 status.

---

### MAJ-23. Hardcoded fallback passwords in 3 locations

| Field | Value |
|-------|-------|
| **Files** | `alerter/alerter.py:46–48`, `log_shipper/shipper.py:21–24`, `docker-compose.yml:177,210` |
| **Category** | Security / REQ-001 §3.1 |

**Description:** Default DSN strings contain plaintext passwords (`context_broker:context_broker@...`, `${POSTGRES_PASSWORD:-contextbroker123}`). These defaults are visible in committed files.

**Fix:** Remove default passwords. Require `POSTGRES_PASSWORD` via env_file and fail on absence.

---

### MAJ-24. `alerter/Dockerfile` and `log_shipper/Dockerfile` — COPY without `--chown`

| Field | Value |
|-------|-------|
| **Files** | `alerter/Dockerfile:5–8`, `log_shipper/Dockerfile:11` |
| **Category** | REQ-002 §1.3 |

**Description:** `COPY` commands lack `--chown`. Files are copied as root. REQ-002 mandates `COPY --chown`.

**Fix:** Add `--chown=<user>:<group>` to all COPY directives (after creating the non-root user per BLK-02/BLK-03).

---

### MAJ-25. Missing healthchecks in docker-compose.yml for UI, log-shipper, alerter

| Field | Value |
|-------|-------|
| **File** | `docker-compose.yml` |
| **Lines** | 166–179 (log-shipper), 184–195 (UI), 201–215 (alerter) |
| **Category** | REQ-002 §5.2 |

**Description:** Three services lack `healthcheck` directives in the compose file. REQ-002 requires Docker HEALTHCHECK per container.

**Fix:** Add `healthcheck` to each service definition.

---

### MAJ-26. Docker socket + root in log-shipper = host privilege escalation

| Field | Value |
|-------|-------|
| **File** | `docker-compose.yml` |
| **Line** | 175 |
| **Category** | Security |

**Description:** `/var/run/docker.sock:/var/run/docker.sock:ro` mounted into a root-running container. This gives full Docker API access, equivalent to root on the host.

**Fix:** Run as non-root with docker group (BLK-03). Consider a Docker API proxy like `tecnativa/docker-socket-proxy` that restricts to read-only log access.

---

### MAJ-27. O(n²) `list.insert(0, ...)` in message assembly loops

| Field | Value |
|-------|-------|
| **Files** | `build_types/passthrough.py:280`, `standard_tiered.py:264,998`, `knowledge_enriched.py:281,532` |
| **Category** | Performance |

**Description:** Multiple functions iterate messages in reverse and call `list.insert(0, item)`. Each insert is O(n), making the loop O(n²). With `max_messages_to_load` of 1000, this is measurably slow.

**Fix:** Append items then reverse, or use `collections.deque.appendleft()`.

---

### MAJ-28. `get_mem0_client` exception handling too narrow for initialization

| Field | Value |
|-------|-------|
| **File** | `packages/context-broker-ae/src/context_broker_ae/memory/mem0_client.py` |
| **Line** | 93 |
| **Category** | Resilience |

**Description:** Catches only `(ImportError, ConnectionError, ValueError)` but `_build_mem0_instance` can raise Neo4j driver errors, psycopg2 errors, `OSError`, `RuntimeError`. An uncaught exception crashes the caller instead of gracefully degrading.

**Fix:** Widen to include `(OSError, RuntimeError)` at minimum, or catch `Exception` at this specific initialization boundary with logging.

---

### MAJ-29. Unbounded `get_history` without hard cap

| Field | Value |
|-------|-------|
| **File** | `packages/context-broker-ae/src/context_broker_ae/conversation_ops_flow.py` |
| **Lines** | 262–273 |
| **Category** | Performance |

**Description:** When `limit` is not provided, the query fetches ALL messages with no upper bound. A conversation with millions of messages would cause OOM.

**Fix:** Apply a hard cap (e.g., `LIMIT 10000`) when no user limit is provided.

---

### MAJ-30. Advisory lock leak risk in memory extraction

| Field | Value |
|-------|-------|
| **File** | `packages/context-broker-ae/src/context_broker_ae/memory_extraction.py` |
| **Lines** | 407–417 |
| **Category** | Resource management |

**Description:** Session-level advisory locks (`pg_try_advisory_lock`) persist until the session disconnects. If release fails (broken connection), with connection pooling the lock could persist indefinitely.

**Fix:** Consider transaction-level locks or a TTL-based lock table. Add try/finally guarantee on release.

---

### MAJ-31. Stale Redis references throughout standard_tiered and knowledge_enriched

| Field | Value |
|-------|-------|
| **Files** | `build_types/passthrough.py:52,122`, `standard_tiered.py:87,107,110,124,549,855,859,878`, `knowledge_enriched.py` (multiple) |
| **Category** | Code quality |

**Description:** Implementation uses Postgres advisory locks, but docstrings, comments, log messages, and variable names all still reference "Redis". Misleading for maintainers and produces confusing logs.

**Fix:** Search-and-replace all Redis references with accurate Postgres advisory lock terminology.

---

### MAJ-32. Dead code: lock TTL renewal block in standard_tiered

| Field | Value |
|-------|-------|
| **File** | `packages/context-broker-ae/src/context_broker_ae/build_types/standard_tiered.py` |
| **Lines** | 549–556 |
| **Category** | Dead code |

**Description:** Block claims to "Renew lock TTL" but the try body is `pass` with a comment "Advisory locks: no TTL renewal needed". Dead code that adds confusion.

**Fix:** Remove the entire block.

---

### MAJ-33. `ke_wait_for_assembly` missing error handling (inconsistency with standard_tiered)

| Field | Value |
|-------|-------|
| **File** | `packages/context-broker-ae/src/context_broker_ae/build_types/knowledge_enriched.py` |
| **Lines** | 145–189 |
| **Category** | Resilience |

**Description:** The equivalent `ret_wait_for_assembly` in `standard_tiered.py` wraps `get_pg_pool()` in try/except for RuntimeError. The knowledge-enriched version does not, and will crash if the pool is unavailable.

**Fix:** Add the same error guards as the standard_tiered counterpart.

---

### MAJ-34. Duplicate `get_pg_pool()` call in summarize_message_chunks

| Field | Value |
|-------|-------|
| **File** | `packages/context-broker-ae/src/context_broker_ae/build_types/standard_tiered.py` |
| **Lines** | 333, 343 |
| **Category** | Dead code |

**Description:** `get_pg_pool()` called twice in the same function. Line 343 is a leftover from the Redis-to-Postgres migration.

**Fix:** Remove line 343 and its comment.

---

### MAJ-35. `_total_queued_messages` counter not atomic in MCP

| Field | Value |
|-------|-------|
| **File** | `app/routes/mcp.py` |
| **Line** | 129–130 |
| **Category** | Concurrency |

**Description:** `_total_queued_messages` decremented inside `event_stream` async generator without `_session_lock`, inconsistent with all other mutation sites which hold the lock.

**Fix:** Acquire `_session_lock` around the decrement.

---

## Minors

### m-01. Unbounded DNS cache in `caller_identity.py`
- **File:** `app/routes/caller_identity.py:19,44,48`
- **Description:** `_dns_cache` grows without bound. Use `functools.lru_cache` with maxsize.

### m-02. Unbounded prompt cache in `prompt_loader.py`
- **File:** `app/prompt_loader.py:18,60,89`
- **Description:** `_prompt_cache` has no size limit. Acceptable if prompt names are static; document assumption.

### m-03. `ConnectionError` handler redundant with `OSError` in `main.py`
- **File:** `app/main.py:297–298`
- **Description:** `ConnectionError` is a subclass of `OSError`. Separate handler is redundant.

### m-04. Missing type annotations on migration `conn` parameters
- **File:** `app/migrations.py:19,29,39,51,...`
- **Description:** All migration functions accept `conn` without type annotation. Should be `conn: asyncpg.Connection`.

### m-05. "user_prompt" find-replace artifact in docstrings
- **File:** `app/token_budget.py:6,113–114,330`
- **Description:** Docstrings contain `"user_prompt"` where `"query"` was intended. Stale find-replace.

### m-06. `check_neo4j_health` creates new httpx client on every call
- **File:** `app/database.py:89–95`
- **Description:** Creates a new `httpx.AsyncClient` per health check invocation. Reuse a shared client.

### m-07. `MetricsGetInput` is an empty Pydantic model
- **File:** `app/models.py:258–261`
- **Description:** Empty model with only `pass`. Dead code or interface placeholder.

### m-08. `list_build_types` reads `_registry` without lock
- **File:** `app/flows/build_type_registry.py:95–97`
- **Description:** Inconsistent with the explicit threading discipline in the same module.

### m-09. DRY violation: `get_assembly_graph` and `get_retrieval_graph` near-identical
- **File:** `app/flows/build_type_registry.py:49–92`
- **Description:** Could extract a shared `_get_graph(name, kind)` helper.

### m-10. Blocking `os.stat()` in `imperator_wrapper.py`
- **File:** `app/flows/imperator_wrapper.py:37–39`
- **Description:** Blocking filesystem call in async path for TE config mtime check.

### m-11. Unused `config` parameter in imperator wrapper
- **File:** `app/flows/imperator_wrapper.py:66,92`
- **Description:** Both `invoke_with_metrics` and `astream_events_with_metrics` accept `config` but never use it.

### m-12. Duplicate Imperator flow caches (wrapper vs. tool_dispatch)
- **File:** `app/flows/imperator_wrapper.py` + `app/flows/tool_dispatch.py`
- **Description:** Two independent flow caches with inconsistent invalidation.

### m-13. Too-narrow exception catch in `install_stategraph` DB recording
- **File:** `app/flows/install_stategraph.py:116–117`
- **Description:** Best-effort DB write catches only `(OSError, RuntimeError)`, missing `asyncpg.PostgresError`.

### m-14. Scattered inline imports in `tool_dispatch.py`
- **File:** `app/flows/tool_dispatch.py` (multiple lines)
- **Description:** 11+ inline imports with inconsistent aliases (`_get_pg_pool`, `_get_pool`, `_get_pool2`).

### m-15. `_flow_cache` not thread-safe in `tool_dispatch.py`
- **File:** `app/flows/tool_dispatch.py:47–58`
- **Description:** Module-level dict mutated without lock.

### m-16. Unused `app_state` parameter in `dispatch_tool`
- **File:** `app/flows/tool_dispatch.py:84`
- **Description:** Parameter accepted but never referenced.

### m-17. Hardcoded `/data/imperator_state.json` path
- **File:** `app/imperator/state_manager.py:25`
- **Description:** REQ-001 §8 requires configurable externals. Should come from config or env var.

### m-18. DB query on every `get_conversation_id()` call
- **File:** `app/imperator/state_manager.py:59–77`
- **Description:** Verifies conversation exists on every call. Cache with TTL instead.

### m-19. Thin gateway violation: role mapping logic in chat route
- **File:** `app/routes/chat.py:110–127`
- **Description:** LangChain message conversion is app logic that should live in a flow layer.

### m-20. Hardcoded `-1` for usage tokens in chat response
- **File:** `app/routes/chat.py:280–283`
- **Description:** Not standard OpenAI API. Consider omitting `usage` or returning `0`.

### m-21. Stream exception handling may miss LangChain/httpx exceptions
- **File:** `app/routes/chat.py:244`
- **Description:** Catches only `(RuntimeError, ConnectionError, OSError)`. Other exceptions drop the SSE stream silently.

### m-22. MCP error message leaks internal exception details
- **File:** `app/routes/mcp.py:249`
- **Description:** `f"Configuration unavailable: {exc}"` leaks internals to client.

### m-23. TOCTOU race — redundant session check outside lock in MCP
- **File:** `app/routes/mcp.py:288–301`
- **Description:** First session check is outside the lock; second check inside the lock is the one that matters.

### m-24. Tool list is a 544-line function in MCP route
- **File:** `app/routes/mcp.py:368–912`
- **Description:** Giant list literal. Move to a separate module or declarative file.

### m-25. Import inside loop in `db_worker.py`
- **File:** `app/workers/db_worker.py:107`
- **Description:** `from app.config import get_embeddings_model` inside `while True` loop body.

### m-26. Dead code: `_check_assembly_needed` in `db_worker.py`
- **File:** `app/workers/db_worker.py:374–447`
- **Description:** Function defined but never called.

### m-27. No timeout on assembly in `_run_assembly`
- **File:** `app/workers/db_worker.py:502–531`
- **Description:** `assembly_graph.ainvoke()` not wrapped in `asyncio.wait_for()`. The dead `_check_assembly_needed` had a timeout.

### m-28. SQL injection via f-string DDL (low exploitability)
- **File:** `app/workers/db_worker.py:195–196`
- **Description:** `dim` from embedding model injected into DDL via f-string. Validate is positive int.

### m-29. Cron parser doesn't handle ranges or lists
- **File:** `app/workers/scheduler.py:26–50`
- **Description:** Only supports `*`, `*/N`, and literals. `int("9-17")` would raise `ValueError`. Document limitations or use `croniter`.

### m-30. `int(schedule_expr)` unvalidated in scheduler
- **File:** `app/workers/scheduler.py:142`
- **Description:** Non-numeric string raises `ValueError`, preventing processing of all remaining schedules.

### m-31. Extra DB round-trip for schedule `message`/`target`
- **File:** `app/workers/scheduler.py:122–125,174–177`
- **Description:** Initial query omits columns needed at fire time; second query fetches them.

### m-32. `_rerank_via_api` creates new httpx client per call
- **File:** `packages/context-broker-ae/src/context_broker_ae/search_flow.py:41`
- **Description:** New TCP connection per rerank. Use persistent client.

### m-33. `embed_pipeline.py` fetches `SELECT *` instead of needed columns
- **File:** `packages/context-broker-ae/src/context_broker_ae/embed_pipeline.py:53–56`
- **Description:** Fetches entire row including large embedding vector when only `content`, `sequence_number`, `conversation_id` are needed.

### m-34. Token count estimation `len(content) // 4` is very rough
- **File:** `packages/context-broker-ae/src/context_broker_ae/message_pipeline.py:138`, `conversation_ops_flow.py:599`
- **Description:** For non-English or code-heavy content, error margin is 2–3x. Consider `tiktoken` for primary pipeline.

### m-35. `route_after_store` dead code in message pipeline
- **File:** `packages/context-broker-ae/src/context_broker_ae/message_pipeline.py:289–293`
- **Description:** Always returns `END`, never used. Remove.

### m-36. `sender` field access without `.get()` safety in `ke_assemble_context`
- **File:** `packages/context-broker-ae/src/context_broker_ae/build_types/knowledge_enriched.py:478,553,558,568`
- **Description:** `m['sender']` without `.get()` — will raise `KeyError` if missing. Use `.get("sender", "")`.

### m-37. Unused `lock_token` UUID in standard_tiered assembly
- **File:** `packages/context-broker-ae/src/context_broker_ae/build_types/standard_tiered.py:108,551`
- **Description:** Redis-era artifact. Set to `"pg_advisory"` (matching passthrough) or remove.

### m-38. Tier scaling inconsistent rounding precision (4 vs 6 decimal places)
- **File:** `packages/context-broker-ae/src/context_broker_ae/build_types/tier_scaling.py:73–75 vs 116–118`
- **Description:** Cosmetic inconsistency in rounding precision.

### m-39. Trivial constant routers could be simple edges
- **Files:** `passthrough.py:148–150`, `knowledge_enriched.py:674–675`, `standard_tiered.py:1090–1091`
- **Description:** `pt_route_after_finalize`, `ke_route_after_wait`, `ret_route_after_wait` always return the same value. Use `add_edge` instead of `add_conditional_edges`.

### m-40. Delayed imports of `EFFECTIVE_UTILIZATION_DEFAULT` in build types
- **Files:** `passthrough.py:239`, `standard_tiered.py:165,837`, `knowledge_enriched.py:129`
- **Description:** Imported inside functions. Move to top-level or document circular import reason.

### m-41. MemorySaver contradicts docstring "no checkpointer"
- **File:** `packages/context-broker-te/src/context_broker_te/imperator_flow.py:6,710–712`
- **Description:** Docstring says "ARCH-06: No MemorySaver" but line 712 compiles with `MemorySaver()`. Update docstring.

### m-42. Dead conditional in notify `get_tools`
- **File:** `packages/context-broker-te/src/context_broker_te/tools/notify.py:133–138`
- **Description:** Checks config but returns `[send_notification]` in both branches.

### m-43. Module-level singletons without locks in imperator_flow
- **File:** `packages/context-broker-te/src/context_broker_te/imperator_flow.py:77–101,216–231`
- **Description:** Three singleton patterns without locking. `domain_mem0.py` correctly uses `asyncio.Lock()`.

### m-44. `calculate` tool uses `eval()` with brittle sandbox
- **File:** `packages/context-broker-te/src/context_broker_te/tools/system.py:120`
- **Description:** `eval()` with empty `__builtins__` has known bypass vectors. Use `ast.literal_eval` or `simpleeval` library.

### m-45. Path traversal edge case in filesystem `_is_safe_read_path`
- **File:** `packages/context-broker-te/src/context_broker_te/tools/filesystem.py:26–29`
- **Description:** `str.startswith("/app")` would match `/appdata/`. Ensure roots end with `/`.

### m-46. Unbounded `os.walk` in `file_search`
- **File:** `packages/context-broker-te/src/context_broker_te/tools/filesystem.py:115–129`
- **Description:** Traverses entire directory tree with no depth/count limit. Only output is capped.

### m-47. Embeddings model instantiated per seed article
- **File:** `packages/context-broker-te/src/context_broker_te/seed_knowledge.py:201–202`
- **Description:** `get_embeddings_model(config)` called inside loop. Move before loop.

### m-48. Multiple sequential DB queries in `pipeline_status`
- **File:** `packages/context-broker-te/src/context_broker_te/tools/diagnostic.py:157–180`
- **Description:** Six sequential `fetchval()` calls. Combine into one query with CTEs.

### m-49. Alerter uses deprecated FastAPI lifecycle hooks
- **File:** `alerter/alerter.py:68,88`
- **Description:** `@app.on_event("startup")` is deprecated. Use `lifespan` context manager.

### m-50. Log shipper uses f-string logging
- **File:** `log_shipper/shipper.py:49,69,84,...`
- **Description:** `logger.info(f"...")` should be `logger.info("...", value)` for deferred formatting.

### m-51. Nginx config has no rate limiting or `client_max_body_size`
- **File:** `nginx/nginx.conf`
- **Description:** No request size limits or rate limiting. Add `client_max_body_size 10m;` and consider `limit_req_zone`.

### m-52. `deploy.sh` references `docker-compose.claude-test.yml` not main compose file
- **File:** `deploy.sh:38`
- **Description:** Hardcodes test-specific compose file. Make configurable or document as test-only.

---

## Positive Observations

1. **StateGraph architecture** is properly followed throughout. All flows use `StateGraph` with nodes, edges, and conditional routing. Nodes return new dicts (state immutability). The Imperator correctly implements the ReAct pattern (agent → conditional edge → tool node → edge back).
2. **No actual secrets committed** — credentials use env_file indirection and environment variable references. API keys use `api_key_env` pattern.
3. **Input validation** via Pydantic models with field constraints (max_length, ge/le bounds, regex patterns) on all MCP tool inputs.
4. **Prometheus metrics** have bounded cardinality with documented memory impact.
5. **Migration system** uses advisory locks for concurrency safety and transactions for atomicity.
6. **Config hot-reload** with mtime + SHA-256 content hash is well-designed.
7. **Prior issue log** shows thorough iterative review — 200+ findings across 7 rounds, nearly all resolved.

---

*Review conducted by Claude Opus 4.6 (1M context) on 2026-03-28.*
