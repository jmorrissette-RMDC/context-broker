## Code Review Findings

### High Severity Issues

**File:** `app/flows/context_assembly.py`  
**Function/Line:** `calculate_tier_boundaries` (lines 189-230)  
**Severity:** Major  
**Description:** Race condition in incremental assembly logic. The function reads existing tier 2 summaries to determine what's already been processed, but there's no transaction isolation between the read and subsequent processing. If two assembly jobs run concurrently for the same window, they may both process the same unsummarized messages, leading to duplicate summaries. This violates the idempotency requirement (REQ-001 §7.3).

---

**File:** `app/flows/memory_extraction.py`  
**Function/Line:** `build_extraction_text` (lines 117-152)  
**Severity:** Major  
**Description:** Potential data loss in message selection. The function builds extraction text by taking messages newest-first up to a character limit, then reverses them to restore chronological order. However, if the character budget is exceeded mid-message, that message is truncated but still included. This can result in incomplete or malformed input to Mem0, potentially causing extraction failures or incorrect knowledge graph entries.

---

**File:** `app/flows/retrieval_flow.py`  
**Function/Line:** `load_recent_messages` (lines 205-245)  
**Severity:** Major  
**Description:** Incorrect token budget calculation. The function calculates `remaining_budget` as the minimum of `tier3_budget` and `max_budget - summary_tokens`, but this can result in loading fewer recent messages than intended when summary tokens exceed the tier3 allocation. This breaks the contract that tier3 should get `tier3_pct` of the total budget.

---

### Medium Severity Issues

**File:** `app/database.py`  
**Function/Line:** `check_neo4j_health` (lines 174-190)  
**Severity:** Major  
**Description:** Insecure health check. The Neo4j health check uses HTTP without authentication to the Neo4j browser endpoint. This could be exploited if Neo4j has a vulnerable browser interface. The check should use the Bolt protocol with proper credentials, or at least check a more secure endpoint.

---

**File:** `app/flows/search_flow.py`  
**Function/Line:** `hybrid_search_messages` (lines 180-310)  
**Severity:** Major  
**Description:** SQL injection vulnerability in dynamic query construction. The function builds SQL queries using string formatting with user-provided `conversation_id` values. Although `conversation_id` is validated as a UUID, the dynamic SQL construction with `candidate_limit` (from config) could be exploited if the config is compromised. All dynamic values should be parameterized.

---

**File:** `app/workers/arq_worker.py`  
**Function/Line:** `_handle_job_failure` (lines 210-232)  
**Severity:** Major  
**Description:** Ineffective retry backoff. The retry backoff uses `5 ** (attempt - 1)`, which grows extremely quickly (1, 5, 25, 125 seconds). This can cause long delays for transient failures. A more reasonable exponential backoff (e.g., 2^attempt or attempt * 10) would be more appropriate.

---

### Low Severity Issues

**File:** `app/config.py`  
**Function/Line:** `get_api_key` (lines 57-67)  
**Severity:** Minor  
**Description:** Misleading log level. When an API key environment variable is not set, the function logs a warning. However, for local providers like Ollama that don't require API keys, this is expected and should not generate warnings. The log level should be DEBUG or the check should be conditional on whether the provider requires a key.

---

**File:** `app/flows/embed_pipeline.py`  
**Function/Line:** `generate_embedding` (lines 80-110)  
**Severity:** Minor  
**Description:** Inefficient context building. The function fetches prior messages to build contextual embeddings, but it does so for every message individually. This results in N+1 query pattern where N is the number of messages. For conversations with many messages, this can become a performance bottleneck. Consider batching these queries or caching recent context windows.

---

**File:** `app/routes/chat.py`  
**Function/Line:** `_stream_imperator_response` (lines 154-196)  
**Severity:** Minor  
**Description:** Poor streaming granularity. The streaming response splits the full response text by spaces and streams word-by-word. This can result in unnatural pauses and a poor user experience, especially with punctuation. A better approach would be to stream by sentences or use a more sophisticated tokenization method that considers natural language boundaries.

---

**File:** `app/token_budget.py`  
**Function/Line:** `_query_provider_context_length` (lines 105-150)  
**Severity:** Minor  
**Description:** Fragile model parsing. The function parses the provider's model list response assuming a specific structure (`data` array with `id` and `context_length` fields). This is fragile and may break with different provider implementations. The function should be more robust in handling variations in the response format, including missing or unexpected fields.