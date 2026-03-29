I've completed a thorough review of all code. Here's my round 4 report.

---

# Gate 2 Code Review — Round 4 (Claude Opus 4.6)

**5 new findings: 1 blocker, 2 majors, 2 minors**

After 3 prior rounds (81 issues, all fixed), the codebase is in strong shape. These 5 findings are the remaining issues.

## BLOCKER (1)

| ID | File | Line | Severity | Description |
|----|------|------|----------|-------------|
| **B-01** | `packages/.../tools/system.py` | 15-30 | **Blocker** | **`python` and `pip` in `_ALLOWED_BINARIES` grant arbitrary code execution.** The R3 B-01 fix switched to `create_subprocess_exec` + `shlex.split()`, preventing shell metacharacter injection. But the binary allowlist still includes `python` and `pip`. An LLM (or prompt-injected message) can run `python -c "import os; print(open('/proc/self/environ').read())"` to dump every secret, or `pip install <malicious-package>` to execute arbitrary code. Only `args[0]` is checked — no argument validation. This completely undermines all other security measures. **Fix:** Remove `python` and `pip` from `_ALLOWED_BINARIES`. If Python version inspection is needed, add a dedicated tool that runs `python --version` only. |

## MAJORS (2)

| ID | File | Line | Severity | Description |
|----|------|------|----------|-------------|
| **M-01** | `packages/.../tools/system.py` | 20 | **Major** | **`cat` in allowlist enables secret exfiltration via `/proc/self/environ`.** The R1 fix removed `env` from the allowlist, but `cat /proc/self/environ` achieves the identical result — dumping `POSTGRES_PASSWORD`, all API keys, etc. Also `cat /etc/shadow` (if readable), `cat /config/*.yml` (raw config with un-redacted secrets). **Fix:** Remove `cat` from `_ALLOWED_BINARIES`. If file reading is needed, add a tool that restricts paths to a safe allowlist. |
| **M-02** | `memory_admin_flow.py` (55-62, 120-127, 177-184), `memory_search_flow.py` (78-85, 168-175), `knowledge_enriched.py` (435-442) | multiple | **Major** | **Blanket `except (..., Exception)` violates REQ-001 §4.5.** Six locations catch a tuple of specific exceptions *plus* bare `Exception`, making the specific types redundant. This masks programming errors (e.g., `TypeError`, `KeyError`, `AttributeError`) as Mem0 degradation. Tagged `EX-CB-001` / `G5-18` but the requirement says "No blanket except Exception." **Fix:** Remove `Exception` from each tuple. The five specific types (`ConnectionError`, `RuntimeError`, `ValueError`, `ImportError`, `OSError`) already cover Mem0/Neo4j failure modes. If a new failure type surfaces, add it explicitly. |

## MINORS (2)

| ID | File | Line | Severity | Description |
|----|------|------|----------|-------------|
| **m-01** | `log_shipper/shipper.py` | 31 | **Minor** | **Unbounded `asyncio.Queue()` — no `maxsize`.** If Postgres write falls behind log production (e.g., during a DB outage), the queue grows without limit, eventually exhausting container memory and crashing the shipper. The writer already drops failed batches (line 238-241), so applying backpressure is consistent with the design. **Fix:** `self.log_queue = asyncio.Queue(maxsize=10000)` and handle `QueueFull` in the producer by dropping the log entry with a warning counter. |
| **m-02** | `packages/.../tools/admin.py` | 124-153, 327-338 | **Minor** | **TOCTOU race in `config_write` and `change_inference`.** Both functions read YAML from disk, modify in memory, and write back with no file lock. Two concurrent tool invocations (e.g., Imperator changes two config keys in parallel) can lose one update. `config_read` correctly uses `run_in_executor` for I/O but these functions don't. **Fix:** Use `fcntl.flock` (or an `asyncio.Lock` at minimum) around the read-modify-write cycle, and wrap the file I/O in `run_in_executor` per R3 M-17. |

---

## Verdict

5 genuine new findings remain — the codebase is close to clean. **B-01 is the highest priority** since `python` in the binary allowlist is a full code-execution primitive that trivially bypasses every other security control. Once B-01 and M-01 are fixed (remove `python`, `pip`, and `cat` from the allowlist), the remaining 3 findings are correctness and robustness improvements rather than security risks.
ered `admin.py` tools specifically; these are different functions in `filesystem.py`.)
- **Fix:** Wrap all file operations in `await loop.run_in_executor(None, ...)`.

**R4-M08: `domain_mem0` singleton never invalidated on config change**
- **File:** `packages/context-broker-te/src/context_broker_te/domain_mem0.py`, lines 103-118
- **Severity:** Major
- **Description:** The AE's `get_mem0_client()` checks a config hash and recreates the client when config changes (supporting hot-reload per REQ-001 §8.3). The TE's `get_domain_mem0()` creates the instance once and never checks for config changes. If `change_inference` updates the LLM or embedding provider, the domain Mem0 client keeps using stale settings.
- **Fix:** Add config-hash checking similar to `_compute_config_hash` in `mem0_client.py`.

### MINOR

**R4-m01: `web_read` private IP blocking is incomplete**
- **File:** `packages/context-broker-te/src/context_broker_te/tools/web.py`, line 66
- **Severity:** Minor
- **Description:** `hostname.startswith("172.")` blocks the entire 172.x.x.x range, but only 172.16.0.0/12 is private. Missing: IPv6 loopback (`::1`), link-local (`fe80::`), carrier-grade NAT (`100.64.0.0/10`). DNS rebinding can also bypass hostname-based checks. (R3 B-03 fixed the initial SSRF; this is about the fix being incomplete.)
- **Fix:** Use `ipaddress.ip_address(resolved_ip).is_private` after DNS resolution instead of string prefix matching.

**R4-m02: `config/imperator.yml` is stale/orphaned with wrong schema**
- **File:** `config/imperator.yml`, lines 1-80
- **Severity:** Minor
- **Description:** Uses a completely different schema (`inference:` block, `imperator_max_iterations`, flat structure) than the current `te.yml`. Labeled "Run 2" — leftover from an earlier architecture. Could cause misconfiguration if someone copies it.
- **Fix:** Delete or move to `config/archive/`.

**R4-m03: `context_introspection` hardcodes "85%" in label**
- **File:** `packages/context-broker-te/src/context_broker_te/tools/diagnostic.py`, line 135
- **Severity:** Minor
- **Description:** The string `"Effective Budget (85%)"` is hardcoded, but the actual utilization value comes from `EFFECTIVE_UTILIZATION_DEFAULT` which could change. The label would then be wrong.
- **Fix:** `f"Effective Budget ({int(EFFECTIVE_UTILIZATION_DEFAULT * 100)}%)"`.

**R4-m04: `POSTGRES_DSN` accessed at module import with bare `KeyError`**
- **File:** `alerter/alerter.py`, line 45; `log_shipper/shipper.py`, line 22
- **Severity:** Minor
- **Description:** `os.environ["POSTGRES_DSN"]` at module level crashes with an unhelpful `KeyError` traceback if the env var is missing, before any logging initializes. (REQ-001 §7.4 says fail fast with a *clear* error.)
- **Fix:** `os.environ.get("POSTGRES_DSN") or sys.exit("POSTGRES_DSN environment variable is required")`.

**R4-m05: `.env.example` is incomplete**
- **File:** `config/credentials/.env.example`
- **Severity:** Minor
- **Description:** Only lists `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `POSTGRES_PASSWORD`. Missing: `GOOGLE_API_KEY` (used by live config and `inference-models.yml`), `XAI_API_KEY`, `TOGETHER_API_KEY`, `SENDGRID_API_KEY` (used by `alerter.yml`).
- **Fix:** Add commented-out entries for all API keys referenced in `inference-models.yml` and `alerter.yml`.

---

## Summary

| Severity | Count |
|----------|-------|
| Major    | 8     |
| Minor    | 5     |
| **Total**| **13**|

**Key themes this round:**
1. **TE/AE parity gaps** (M01, M02, M08) — The domain Mem0 client in the TE package diverged from the AE's Mem0 client during R1-R3 fixes. Missing `embedding_dims` (M01) is the most impactful — it will cause runtime failures.
2. **Sync I/O in async** (M06, M07) — New locations not covered by R3 M-17 (`config.py` AE path and `filesystem.py` tools).
3. **Dependency pinning** (M04, M05) — `pyproject.toml` and `log_shipper/Dockerfile` use loose version specs.
4. **State immutability** (M03) — `_rerank_via_api` mutates graph state candidates in-place.

No blockers found. The codebase has improved substantially across three rounds. The remaining issues are real but none are security-critical.
