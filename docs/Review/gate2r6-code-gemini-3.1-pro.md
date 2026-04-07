Here is the code review for the Context Broker based on the provided requirements and source code.

### 1. `asyncpg` JSONB Serialization Failure
- **File:** `app/flows/message_pipeline.py`
- **Function/Line:** `store_message` (around line 120)
- **Severity:** blocker
- **Description:** The `tool_calls` field (which is a Python `list[dict]`) is passed directly as parameter `$9` to the `INSERT` statement for a `JSONB` column. Because `asyncpg` does not automatically serialize Python objects to JSON without a registered type codec (and no codec is registered in `init_postgres`), this will raise an `asyncpg.exceptions.DataError` at runtime. Any attempt to store a message containing tool calls will crash the flow. 
**Fix:** Serialize the list to a JSON string using `json.dumps(state.get("tool_calls"))` and cast it in the query (`$9::jsonb`), or register a JSON codec on the `asyncpg` pool during initialization.

### 2. Non-Atomic Queue Operations Lead to Permanent Job Loss
- **File:** `app/workers/arq_worker.py`
- **Function/Line:** `_consume_queue` (around line 230) and `_sweep_delayed_queues` (around line 350)
- **Severity:** major
- **Description:** For sorted-set queues (like `memory_extraction_jobs`), `_consume_queue` uses `zpopmin` followed by `lpush` to move jobs to the processing queue. Similarly, `_sweep_delayed_queues` uses `zrem` followed by `lpush`/`zadd`. These operations are not atomic. If the worker crashes or is killed between the two Redis commands, the job is permanently lost because it has been removed from the source queue but not yet added to the destination queue. 
**Fix:** Use a Redis Lua script to atomically pop from the sorted set and push to the destination queue in a single transaction.

### 3. Memory/Counter Leak on MCP SSE Session Disconnect
- **File:** `app/routes/mcp.py`
- **Function/Line:** `mcp_sse_session` (around line 90, inside the `finally` block)
- **Severity:** major
- **Description:** When a client disconnects and the `finally` block executes, the session is removed from `_sessions`, but the global `_total_queued_messages` counter is not decremented by the remaining `message_queue.qsize()`. This causes a permanent leak in the global counter. Over time, this leaked count will artificially inflate `_total_queued_messages` until it exceeds `_MAX_TOTAL_QUEUED`, triggering aggressive and unwarranted eviction of active, healthy sessions.
**Fix:** In the `finally` block, decrement `_total_queued_messages` by the number of items remaining in the queue before discarding it.

### 4. `IndexError` in Archival Consolidation
- **File:** `app/flows/build_types/standard_tiered.py`
- **Function/Line:** `consolidate_archival_summary` (around line 340)
- **Severity:** major
- **Description:** The function calculates `to_consolidate = list(active_t2)[:-keep_recent]`. If the configured `consolidation_keep_recent` is greater than or equal to `len(active_t2)`, this slice results in an empty list. Later in the function, `to_consolidate[0]["summarizes_from_seq"]` is accessed, which will raise an `IndexError` and crash the assembly job. Because `consolidation_keep_recent` and `consolidation_threshold` are independently configurable tuning parameters, a user can easily trigger this crash via `config.yml`.
**Fix:** Add a guard clause to return early if `not to_consolidate` after the slice is performed.