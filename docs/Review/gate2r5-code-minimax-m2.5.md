# Code Review: Context Broker

I've completed a thorough review of the codebase. Below are my findings organized by severity.

---

## Blocker Issues

### 1. SQL Injection Risk in Search Flow
**File:** `app/flows/search_flow.py`  
**Function:** `search_conversations_db` (lines 138-175)  
**Severity:** blocker

**Description:** The SQL query is built using f-strings with parameterized arguments, but the `extra_where` clause construction uses `.format()` with parameter indices. While the column names are hardcoded (safe), the pattern is fragile and could become unsafe if extended.

```python
# Line 157-158: Fragile pattern
clauses = ""
for i, f in enumerate(filters):
    clauses += " AND " + f.format(start_idx + i)
```

**Why it matters:** Any future modification adding user-controlled filter keys could introduce SQL injection.

---

### 2. Missing Error Propagation in Assembly Lock Release
**File:** `app/flows/build_types/standard_tiered.py`  
**Function:** `route_after_lock` (line 537)  
**Severity:** blocker

**Description:** When `lock_acquired` is False, the flow goes to END directly, but when there's an error AFTER acquiring the lock, it routes to `"release_assembly_lock"`. However, `release_assembly_lock` doesn't check for or log the error state:

```python
def route_after_lock(state: StandardTieredAssemblyState) -> str:
    if not state.get("lock_acquired"):
        return END
    return "load_window_config"

def route_after_load_config(state: StandardTieredAssemblyState) -> str:
    if state.get("error"):
        return "release_assembly_lock"  # Error is lost here
    return "load_messages"
```

**Why it matters:** Errors during assembly are silently swallowed when the lock release path is taken, making debugging impossible.

---

### 3. Race Condition in Config Hot Reload
**File:** `app/config.py`  
**Function:** `_apply_config` (lines 67-82)  
**Severity:** blocker

**Description:** The function updates global state (`_config_cache`, `_config_mtime`, `_config_content_hash`) without holding the lock during the entire update sequence:

```python
def _apply_config(config: dict, raw: str, current_mtime: float) -> dict:
    global _config_cache, _config_mtime, _config_content_hash
    new_hash = hashlib.sha256(raw.encode()).hexdigest()
    if new_hash != _config_content_hash and _config_content_hash != "":
        # Lock is acquired HERE but released after state updates
        with _cache_lock:
            _llm_cache.clear()
            _embeddings_cache.clear()
    # State updates happen OUTSIDE the lock
    _config_cache = config
    _config_mtime = current_mtime
    _config_content_hash = new_hash
```

**Why it matters:** A concurrent reader could see a cleared cache with stale config, or a half-updated state.

---

## Major Issues

### 4. Broad Exception Handling Masks Bugs
**File:** `app/flows/build_types/knowledge_enriched.py`  
**Function:** `ke_inject_knowledge_graph` (lines 183-217)  
**Severity:** major

**Description:** The function catches `Exception` broadly:
```python
except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:
```

**Why it matters:** This catches programming errors (TypeError, AttributeError) that should propagate, causing the system to silently degrade when there's a bug.

---

### 5. Unbounded Memory in SSE Session Management
**File:** `app/routes/mcp.py`  
**Function:** `mcp_sse_session` (lines 52-95)  
**Severity:** major

**Description:** Sessions are stored in an OrderedDict with a max size of 1000, but there's no bound on the total memory used by all sessions combined. Each session has a queue of maxsize 100, but the message content itself is unbounded.

**Why it matters:** A malicious or buggy client could cause memory exhaustion.

---

### 6. Incomplete Error Handling in Message Pipeline
**File:** `app/flows/message_pipeline.py`  
**Function:** `store_message` (lines 72-180)  
**Severity:** major

**Description:** When a message is collapsed (duplicate detection), the function returns early but doesn't include the `conversation_id` in the return dict when `was_collapsed` is True:

```python
if (
    prev_msg is not None
    and prev_msg["sender"] == state["sender"]
    and prev_msg["content"] == content
    ...
):
    return {
        "message_id": str(prev_msg["id"]),
        "conversation_id": conversation_id,  # This IS set
        "sequence_number": None,
        "was_duplicate": False,
        "was_collapsed": True,  # But caller may expect different behavior
        "queued_jobs": [],
    }
```

Actually looking more closely, this is handled correctly. However, there's still an issue: the `was_duplicate` flag is always False even when the message was collapsed (which is a form of deduplication).

---

### 7. Missing Validation in Tool Dispatch Initial State
**File:** `app/flows/tool_dispatch.py`  
**Function:** `dispatch_tool` (lines 167-195)  
**Severity:** major

**Description:** The initial state passed to retrieval graphs contains zero values for required fields:
```python
result = await retrieval_graph.ainvoke(
    {
        "context_window_id": str(validated.context_window_id),
        "config": config,
        "window": None,
        "build_type_config": None,
        "conversation_id": None,
        "max_token_budget": 0,  # Should be loaded from window
        ...
    }
)
```

**Why it matters:** The retrieval flow must load these values from the database anyway, but passing invalid defaults could cause confusion.

---

### 8. Potential KeyError in Memory Scoring
**File:** `app/flows/memory_scoring.py`  
**Function:** `score_memory` (lines 27-56)  
**Severity:** major

**Description:** The function accesses `memory.get("category")` and `memory.get("created_at")` without checking if the memory dict has the expected structure. Mem0 may return different structures:

```python
category = memory.get("category", "default")
half_life_days = half_lives.get(category, half_lives.get("default", 30))
# If memory is a string (some Mem0 responses), this will fail
```

**Why it matters:** Mem0 responses aren't strongly typed; different response formats could cause runtime crashes.

---

### 9. Inconsistent Error Handling in Worker
**File:** `app/workers/arq_worker.py`  
**Function:** `_handle_job_failure` (lines 175-200)  
**Severity:** major

**Description:** When Redis is unavailable during error handling, the exception is logged but the job is lost:
```python
try:
    await _handle_job_failure(...)
except (ConnectionError, OSError, redis.exceptions.RedisError) as redis_exc:
    _log.error("Redis failure during error handling...")
    # Job is lost here - no dead letter, no retry
```

**Why it matters:** Jobs can be permanently lost if Redis fails during error handling.

---

## Minor Issues

### 10. Inefficient N+1 Query Pattern
**File:** `app/flows/embed_pipeline.py`  
**Function:** `enqueue_context_assembly` (lines 112-180)  
**Severity:** minor

**Description:** The code batches Redis EXISTS checks (good) but then queries tokens for each window individually:
```python
for window in windows:
    ...
    if window["last_assembled_at"] is not None:
        tokens_since = await pool.fetchval(...)  # N queries
```

**Why it matters:** With many context windows, this becomes an N+1 query pattern.

---

### 11. Unsafe Default in Neo4j Health Check
**File:** `app/database.py`  
**Function:** `check_neo4j_health` (lines 108-130)  
**Severity:** minor

**Description:** The function sends Basic auth credentials even when `neo4j_password` is empty string (which is the default in docker-compose):
```python
if neo4j_password:
    credentials = base64.b64encode(f"neo4j:{neo4j_password}".encode()).decode()
    headers["Authorization"] = f"Basic {credentials}"
```

When password is empty, no auth header is sent, which works with `NEO4J_AUTH=none`. However, if someone sets a non-empty password in environment but not in config, the check will fail silently.

---

### 12. Missing null check in Chat Route
**File:** `app/routes/chat.py`  
**Function:** `chat_completions` (lines 55-75)  
**Severity:** minor

**Description:** The context_window_id fallback chain doesn't validate that `imperator_manager` is initialized:
```python
if not context_window_id and imperator_manager is not None:
    context_window_id = await imperator_manager.get_context_window_id()
```

If `imperator_manager` is None (Postgres unavailable at startup), this silently falls through and could pass None to the flow.

---

### 13. Hardcoded Magic Numbers
**File:** `app/flows/memory_extraction.py`  
**Function:** `build_extraction_text` (lines 115-145)  
**Severity:** minor

**Description:** The extraction text building uses hardcoded values that should be configurable:
```python
max_chars = get_tuning(state["config"], "extraction_max_chars", 90000)  # Good
# But inside the loop:
if total_chars + len(line) + 1 > max_chars:
    if not selected_ids:
        selected_ids.append(msg_id)
        selected_lines.append(line[:max_chars])  # Truncation happens here
```

The truncation logic could produce invalid state if a single message exceeds max_chars.

---

### 14. Potential Import Error
**File:** `app/flows/build_types/knowledge_enriched.py`  
**Function:** `ke_inject_knowledge_graph` (line 183)  
**Severity:** minor

**Description:** The function imports inside the try block:
```python
try:
    from app.memory.mem0_client import get_mem0_client
```

If the import fails, it will be caught by the broad exception handler and treated as a Mem0 failure rather than a code bug.

---

### 15. Inconsistent Lock Usage
**File:** `app/config.py`  
**Functions:** `get_chat_model`, `get_embeddings_model`  
**Severity:** minor

**Description:** These functions modify global caches (`_llm_cache`, `_embeddings_cache`) without any locking. While individual dict operations are atomic under CPython's GIL, the check-then-set pattern isn't atomic:
```python
if cache_key not in _llm_cache:  # Check
    if len(_llm_cache) >= _MAX_CACHE_ENTRIES:
        _llm_cache.clear()
    _llm_cache[cache_key] = ...   # Set - race condition possible
```

---

## Summary

| Severity | Count |
|----------|-------|
| Blocker  | 3     |
| Major    | 6     |
| Minor    | 6     |

**Top priorities to fix:**
1. Error propagation in assembly lock release path (blocker #2)
2. Race condition in config hot reload (blocker #3)  
3. Broad exception handling in knowledge graph injection (major #4)
4. Memory exhaustion risk in SSE sessions (major #5)
5. Error handling job loss in worker (major #9)