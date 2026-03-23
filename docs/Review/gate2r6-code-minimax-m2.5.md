# Code Review: Context Broker

I'll now provide a comprehensive code review identifying critical issues, bugs, security concerns, and architectural problems.

---

## Critical Issues (Blocker)

### 1. SQL Injection Risk in Admin Tool
**File:** `app/flows/imperator_flow.py`  
**Function:** `_db_query_tool` (lines ~590-620)

**Description:** The `_db_query_tool` function executes raw SQL directly from user input without any sanitization. While `SET TRANSACTION READ ONLY` prevents data modification, the query can still:
- Expose sensitive data via SELECT
- Cause denial of service via expensive queries
- Be leveraged for information gathering in combination with other vulnerabilities

```python
# The sql parameter is used directly in fetch()
rows = await conn.fetch(sql)  # sql comes directly from tool input
```

**Severity:** blocker - This is a direct SQL injection vector, albeit constrained by READ ONLY mode.

---

### 2. Race Condition in Session Management
**File:** `app/routes/mcp.py`  
**Function:** `_evict_stale_sessions` (lines ~60-90)

**Description:** The session eviction logic reads global state (`_sessions`, `_total_queued_messages`) without atomic operations. Between checking conditions and modifying state, another coroutine could modify the same state, leading to:
- Negative queue counts
- Sessions being tracked multiple times
- Memory leaks from orphaned sessions

```python
# Non-atomic read-modify-write
while len(_sessions) > _MAX_SESSIONS:
    evicted_id, info = _sessions.popitem(last=False)
    _total_queued_messages -= info["queue"].qsize()  # Race here
```

**Severity:** blocker - Can cause corrupted state and memory leaks under load.

---

### 3. Infinite Retry Loop in Postgres Recovery
**File:** `app/main.py`  
**Function:** `_postgres_retry_loop` (lines ~60-100)

**Description:** If Postgres becomes available but Imperator initialization keeps failing (e.g., due to schema issues), the loop continues indefinitely. There's no maximum retry count or circuit breaker.

```python
while True:
    # ... if imperator_initialized fails, loop continues forever
    if not getattr(application.state, "imperator_initialized", False):
        # Keeps retrying without limit
        continue
```

**Severity:** blocker - Can cause infinite loop consuming CPU under certain failure conditions.

---

## Major Issues

### 4. Inefficient Double-Reverse of Messages
**File:** `app/flows/build_types/standard_tiered.py`  
**Function:** `load_messages` (lines ~280-310)

**Description:** Messages are loaded in DESC order, reversed to a list, then reversed again. This is O(2n) when it could be O(n).

```python
rows = await pool.fetch(..., ORDER BY sequence_number DESC, ...)
rows = list(reversed(rows))  # First reversal
messages = [dict(r) for r in rows]  # Already in correct order!
# Later in calculate_tier_boundaries:
for msg in reversed(messages):  # Second reversal needed because of first reversal
```

**Severity:** major - Performance issue with large message counts.

---

### 5. Collapsed Messages Not Actually Stored
**File:** `app/flows/message_pipeline.py`  
**Function:** `store_message` (lines ~130-160)

**Description:** When a message is collapsed (duplicate), the function returns success but never stores the message. Callers expecting persistence may assume the message is stored.

```python
if (...duplicate...):
    # Just updates repeat_count, doesn't insert new row
    await conn.execute("UPDATE ...")
    return {
        "message_id": str(prev_msg["id"]),
        "was_collapsed": True,  # Caller might not handle this
    }
```

**Severity:** major - Semantic mismatch; collapsed messages appear successful but aren't persisted.

---

### 6. Config Redaction Regex Too Broad
**File:** `app/flows/imperator_flow.py`  
**Function:** `_redact_config` (lines ~170-195)

**Description:** The regex `(api_key|secret|token|password)` matches any key containing these substrings, potentially redacting non-secret values like `api_key_env` → `***REDACTED***` (correct) but also `participant_id` → `***REDACTED***` (incorrect - contains "id").

```python
_secret_key_re = re.compile(r"(api_key|secret|token|password)", re.IGNORECASE)
# Matches: participant_id, secret_key, token_count, password_hash, etc.
```

**Severity:** major - Can corrupt configuration output with false positives.

---

### 7. Empty Batch Query Execution
**File:** `app/flows/embed_pipeline.py`  
**Function:** `enqueue_context_assembly` (lines ~180-210)

**Description:** If `windows_needing_token_check` is empty, the query still executes with empty arrays, which PostgreSQL may reject or handle unexpectedly.

```python
token_rows = await pool.fetch(
    """
    SELECT w.id AS window_id, COALESCE(SUM(m.token_count), 0) AS tokens_since
    FROM (SELECT unnest($1::uuid[]) AS id, ...) w
    ...
    """,
    [w["id"] for w in windows_needing_token_check],  # Empty list!
    [w["last_assembled_at"] for w in windows_needing_token_check],  # Empty list!
    ...
)
```

**Severity:** major - Can cause query failures when no windows need token checking.

---

## Minor Issues

### 8. Timezone Handling Inconsistency
**File:** `app/flows/search_flow.py`  
**Function:** `search_conversations_db` (lines ~130-145)

**Description:** Dates are parsed with `fromisoformat` which accepts naive datetimes, then UTC is assumed. This works but is implicit and could cause subtle bugs with timezone-aware inputs.

```python
if parsed_date_from.tzinfo is None:
    parsed_date_from = parsed_date_from.replace(tzinfo=timezone.utc)
# But created_at from DB is timezone-aware, comparison may be inconsistent
```

**Severity:** minor - Works but fragile; explicit timezone handling would be clearer.

---

### 9. Lock TTL Renewal Race Condition
**File:** `app/flows/build_types/standard_tiered.py`  
**Function:** `_summarize_chunk` (lines ~380-395)

**Description:** The lock TTL is renewed before each chunk, but there's a window where the lock could expire between the check and the renewal.

```python
current_val = await redis.get(lock_key)
if current_val and current_val.decode() == lock_token:
    await redis.expire(lock_key, lock_ttl)  # Race: lock could expire between lines
```

**Severity:** minor - Low probability but possible under high load.

---

### 10. Missing Error Handling for Missing Prompt
**File:** `app/flows/imperator_flow.py`  
**Function:** `agent_node` (lines ~290-310)

**Description:** If the imperator_identity prompt file is missing, the error is caught but the function returns a generic error message without propagating the failure properly for retry logic.

```python
except RuntimeError as exc:
    return {
        "messages": [AIMessage(content="I encountered a configuration error.")],
        "response_text": "I encountered a configuration error.",
        "error": f"Prompt loading failed: {exc}",
    }
# Caller checks for "error" but continues processing
```

**Severity:** minor - Error is swallowed; caller may not handle gracefully.

---

### 11. Potential KeyError in Context Window Lookup
**File:** `app/flows/build_types/knowledge_enriched.py`  
**Function:** `ke_load_window` (lines ~70-95)

**Description:** After fetching the window, the code accesses `window_dict["build_type"]` without checking if the key exists. If the schema changes or migration is incomplete, this will raise KeyError.

```python
build_type_config = get_build_type_config(state["config"], window_dict["build_type"])
# If build_type column is missing, KeyError bubbles up
```

**Severity:** minor - Would fail loudly but with unclear error message.

---

### 12. Hardcoded Fallback Values
**File:** `app/flows/memory_scoring.py`  
**Function:** `score_memory` (lines ~30-35)

**Description:** The half-life values have a fallback but if the config returns 0 for all categories, the `max(1, ...)` guard prevents division by zero, but results in very long decay times (effectively immortal memories).

```python
half_life_days = max(1, half_lives.get(category, half_lives.get("default", 30)))
# If config returns {"default": 0}, this becomes 1 day, not 30
```

**Severity:** minor - Misconfiguration could cause unexpected behavior.

---

## Summary Table

| # | File | Function/Line | Severity | Issue |
|---|------|---------------|----------|-------|
| 1 | `app/flows/imperator_flow.py` | `_db_query_tool` ~590 | blocker | SQL injection risk |
| 2 | `app/routes/mcp.py` | `_evict_stale_sessions` ~60 | blocker | Race condition in session management |
| 3 | `app/main.py` | `_postgres_retry_loop` ~60 | blocker | Infinite retry loop |
| 4 | `app/flows/build_types/standard_tiered.py` | `load_messages` ~280 | major | Double-reverse inefficiency |
| 5 | `app/flows/message_pipeline.py` | `store_message` ~130 | major | Collapsed messages not stored |
| 6 | `app/flows/imperator_flow.py` | `_redact_config` ~170 | major | Over-broad regex redaction |
| 7 | `app/flows/embed_pipeline.py` | `enqueue_context_assembly` ~180 | major | Empty batch query |
| 8 | `app/flows/search_flow.py` | `search_conversations_db` ~130 | minor | Timezone handling inconsistency |
| 9 | `app/flows/build_types/standard_tiered.py` | `_summarize_chunk` ~380 | minor | Lock TTL renewal race |
| 10 | `app/flows/imperator_flow.py` | `agent_node` ~290 | minor | Swallowed prompt loading error |
| 11 | `app/flows/build_types/knowledge_enriched.py` | `ke_load_window` ~70 | minor | Missing key check |
| 12 | `app/flows/memory_scoring.py` | `score_memory` ~30 | minor | Hardcoded fallback values |