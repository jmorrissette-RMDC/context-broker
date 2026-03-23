Here is a thorough code review of the Context Broker source code based on the provided requirements.

### 1. Uncaught Redis Exceptions Crash Application Startup
**Severity:** Blocker
**File:** `app/main.py`
**Function:** `lifespan` and `_redis_retry_loop` (lines 115, 148)
**Description:** 
When verifying Redis connectivity at startup, the code catches `(ConnectionError, OSError, RuntimeError)`. However, `redis-py` raises its own exception hierarchy (e.g., `redis.exceptions.ConnectionError`), which inherits from `Exception`, not Python's built-in `ConnectionError` or `OSError`. 
If Redis is unavailable at startup, `redis_client.ping()` will raise a `redis.exceptions.ConnectionError` that bypasses the `except` block, crashing the FastAPI lifespan and preventing the container from starting. This violates the independent container startup requirement (REQ-001 §7.2).
**Fix:** Import `redis.exceptions` and add `redis.exceptions.RedisError` to the `except` tuples in both `lifespan` and `_redis_retry_loop`.

### 2. Malformed Jobs Create Infinite Poison-Pill Loop
**Severity:** Major
**File:** `app/workers/arq_worker.py`
**Function:** `_consume_queue` (lines 277-285)
**Description:** 
If `json.loads(raw_job)` fails with a `JSONDecodeError`, the `job` variable remains `None`. In the exception handler, the cleanup logic is gated behind `if job is not None and raw_job is not None:`. Because `job` is `None`, the cleanup block is entirely skipped.
The malformed job is never removed from the `processing_queue` and never sent to the dead-letter queue. On worker restart, `_sweep_stranded_processing_jobs` will push it back to the main queue, where it will fail again, creating an infinite loop that permanently occupies a slot in the processing queue.
**Fix:** Change the exception handler to handle cases where `job` is `None`. If `raw_job` exists but `job` does not, remove `raw_job` from the processing queue and push it directly to `dead_letter_unparseable` (similar to how `_sweep_dead_letters` handles it).

### 3. Missing Default Recipient Causes Database Constraint Violation
**Severity:** Major
**File:** `app/flows/message_pipeline.py`
**Function:** `store_message` (lines 79-84, 128)
**Description:** 
The pipeline attempts to derive a default `recipient` if one is not provided, but it only handles `user` and `assistant` roles. If a message is stored with `role="system"` or `role="tool"` and no recipient is provided, the `recipient` variable remains `None`. 
The `INSERT` statement explicitly passes this `None` as `$4`, which attempts to insert `NULL` into the database. Because Migration 012 altered the `recipient` column to be `NOT NULL`, this will raise an `asyncpg.exceptions.NotNullViolationError` and crash the message ingestion pipeline.
**Fix:** Add a fallback so `recipient` defaults to `"unknown"` or `"system"` if it remains unresolved after the role checks.

### 4. Postgres Retry Loop Exits Prematurely on Imperator Init Failure
**Severity:** Minor
**File:** `app/main.py`
**Function:** `_postgres_retry_loop` (lines 43-54)
**Description:** 
The `_postgres_retry_loop` is designed to run in the background until Postgres is available and the Imperator is initialized. However, if the loop runs when `postgres_available` is already `True` (e.g., on the second iteration) and `imperator_manager.initialize()` fails (perhaps due to a transient DB blip), the exception is caught, logged, and the function unconditionally hits `return`. 
This exits the background task forever, leaving `imperator_initialized` permanently `False`. 
**Fix:** Replace the `return` statement at the end of the `if getattr(application.state, "postgres_available", False):` block with a `continue` (or only return if initialization succeeds), matching the logic in the lower branch.

### 5. Thread-Safety Discipline Violation in MCP Session Management
**Severity:** Minor
**File:** `app/routes/mcp.py`
**Function:** `mcp_tool_call` (lines 182-185)
**Description:** 
In `mcp_sse_session`, the `_sessions` dictionary and `_total_queued_messages` counter are strictly protected by `async with _session_lock:` (R6-M3). However, in `mcp_tool_call`, the code reads `_sessions` and modifies the global `_total_queued_messages` counter without acquiring `_session_lock`. 
While `asyncio`'s cooperative multitasking prevents OS-level race conditions here (because there are no `await` yields between the check and the update), this violates the explicit locking discipline established for these variables and creates a fragility where future refactoring (e.g., adding an `await` inside the block) will introduce silent race conditions.
**Fix:** Wrap the session lookup and queue insertion block in `mcp_tool_call` with `async with _session_lock:`.