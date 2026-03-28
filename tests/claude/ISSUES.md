# Test Issues Log

**Generated:** 2026-03-27T21:40:41.515713+00:00
**Total issues:** 21

| Severity | Category | Test | Description | Expected | Actual |
|----------|----------|------|-------------|----------|--------|
| error | mem0 | `test_mem_add_embedding_persisted` | mem_add accepted call but stored 0 rows for user_id=live-test-embed-5ce8bd7c — Mem0 silently failed to persist (TA-04) | >=1 row | 0 rows |
| warning | mem0 | `test_mem_search_finds_added_memory` | mem_search returned no results for a just-added memory after retries; Mem0 may not be fully functional (table schema mismatch) |  |  |
| warning | mem0 | `test_mem_list_returns_memories` | Expected at least 1 memory from mem_list, got 0; Mem0 may not be fully functional (table schema mismatch) |  |  |
| warning | mem0 | `test_mem_delete_removes_memory` | Could not determine memory_id from add result: {'status': 'added', 'result': {'results': [], 'relations': [{'source': 'live-test-del-ff15015d', 'relationship': 'has_memory_status', 'target': 'temporary_memory_to_be_deleted'}]}}; Mem0 may not be fully functional (table schema mismatch) |  |  |
| warning | mem0 | `test_mem0_memories_has_entries` | mem0_memories table is empty; extraction may not have run or Mem0 table schema mismatch prevents storage | >0 rows | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation conversation-2 (083d7f6c-b3f9-48c9-ba94-fba733f32716) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation conversation-3 (1cd7021e-d4e1-4e9f-aba7-b2826d425dc2) has no summaries | >0 summaries | 0 |
| info | imperator | `test_tool_turn_0` | Turn 0: expected tool 'get_context' not detected in response | Tool 'get_context' used | Tools used: none detected |
| info | imperator | `test_tool_turn_1` | Turn 1: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_2` | Turn 2: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_3` | Turn 3: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_4` | Turn 4: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_6` | Turn 6: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_7` | Turn 7: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| warning | imperator-tool | `test_tool_file_list` | None of expected keywords ['config.yml', 'te.yml'] found in response | config.yml, te.yml | Of course. Here are the contents of those directories:

**`/config/credentials/`**
*   `postgres_password.txt`

**`/config/prompts/`**
*   `archival_consolidation.md`
*   `chunk_summarization.md`
*   `imperator_identity.md`

Let me know if you'd like to examine any of these files further. |
| warning | imperator-tool | `test_tool_file_write` | None of expected keywords ['written', 'created', 'saved', 'success', 'wrote'] found in response | written, created, saved, success, wrote | I have sent a notification to the system administrator detailing the permission error. I am unable to write the file as requested until this issue is resolved. I apologize for the inconvenience. |
| warning | imperator-tool | `test_tool_update_system_prompt` | update_system_prompt not found in tools/list | update_system_prompt in tool list | ['get_context', 'store_message', 'search_messages', 'search_knowledge', 'conv_create_conversation', 'conv_delete_conversation', 'conv_rename_conversation', 'conv_list_conversations', 'conv_store_message', 'conv_retrieve_context', 'conv_create_context_window', 'conv_search', 'conv_search_messages', 'conv_get_history', 'conv_search_context_windows', 'query_logs', 'search_logs', 'mem_search', 'mem_get_context', 'mem_add'] |
| warning | imperator-tool | `test_tool_add_alert_instruction` | None of expected keywords ['added', 'alert', 'instruction', 'created', 'test alerts'] found in response | added, alert, instruction, created, test alerts | I was unable to complete that request within the allowed number of steps. Please try again, or break the request into smaller parts. |
| warning | imperator-tool | `test_diagnostic_context_introspection` | Expected keyword 'context' not found in response | context | Of course. I can do that, but I'll need the conversation ID to proceed. Please provide the ID of the conversation you'd like me to inspect. |
| warning | imperator-tool | `test_admin_config_read` | Expected keyword 'gemini' not found in response | gemini | I am using `claude-3-haiku-20240307` for summarization tasks. |
| warning | performance | `performance_report` | extract_memory has 42% error rate | <10% | 42% |
