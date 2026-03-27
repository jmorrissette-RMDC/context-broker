# Test Issues Log

**Generated:** 2026-03-27T20:21:28.868431+00:00
**Total issues:** 24

| Severity | Category | Test | Description | Expected | Actual |
|----------|----------|------|-------------|----------|--------|
| error | mem0 | `test_mem_add_embedding_persisted` | mem_add accepted call but stored 0 rows for user_id=live-test-embed-57764648 — Mem0 silently failed to persist (TA-04) | >=1 row | 0 rows |
| warning | mem0 | `test_mem_search_finds_added_memory` | mem_search returned no results for a just-added memory after retries; Mem0 may not be fully functional (table schema mismatch) |  |  |
| warning | mem0 | `test_mem_list_returns_memories` | Expected at least 1 memory from mem_list, got 0; Mem0 may not be fully functional (table schema mismatch) |  |  |
| warning | mem0 | `test_mem_delete_removes_memory` | Could not determine memory_id from add result: {'status': 'added', 'result': {'results': [], 'relations': []}}; Mem0 may not be fully functional (table schema mismatch) |  |  |
| warning | mem0 | `test_mem0_memories_has_entries` | mem0_memories table is empty; extraction may not have run or Mem0 table schema mismatch prevents storage | >0 rows | 0 |
| warning | pipeline | `test_no_pipeline_errors_in_recent_logs` | Error in recent logs: claude-test-langgraph  | {"timestamp": "2026-03-27T20:11:45.971021+00:00", "level": "ERROR", "message": "Extraction failed for 2dc38a8c-83f1-4cd3-9f16-54f488beb228 (attempt 3/3): Mem0 returned empty r |  |  |
| warning | pipeline | `test_no_pipeline_errors_in_recent_logs` | Error in recent logs: claude-test-langgraph  | {"timestamp": "2026-03-27T20:11:50.783396+00:00", "level": "ERROR", "message": "Extraction failed for 62da7169-c19e-4dce-8461-6a5ebe7763c3 (attempt 3/3): Mem0 returned empty r |  |  |
| warning | pipeline | `test_no_pipeline_errors_in_recent_logs` | Error in recent logs: claude-test-langgraph  | {"timestamp": "2026-03-27T20:11:56.861978+00:00", "level": "ERROR", "message": "Extraction failed for a8122e0c-a182-4ef8-b1e3-12d1db2d8711 (attempt 1/3): Mem0 returned empty r |  |  |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation conversation-2 (a11275d6-ee07-4506-bf39-6f035c0cd21d) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation conversation-3 (abc892fa-44ff-4a8b-83d3-a5e3b760258c) has no summaries | >0 summaries | 0 |
| info | imperator | `test_tool_turn_0` | Turn 0: expected tool 'get_context' not detected in response | Tool 'get_context' used | Tools used: none detected |
| info | imperator | `test_tool_turn_1` | Turn 1: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_2` | Turn 2: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_3` | Turn 3: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_4` | Turn 4: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_6` | Turn 6: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_7` | Turn 7: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| warning | imperator-tool | `test_tool_file_write` | None of expected keywords ['written', 'created', 'saved', 'success', 'wrote'] found in response | written, created, saved, success, wrote | I've sent an alert to the system administrator detailing the permission issue and recommending a fix. They will need to adjust the directory ownership or permissions on the host machine.

For now, I am unable to write files to `/data/downloads` until this is resolved. Is there anything else I can he |
| warning | imperator-tool | `test_tool_update_system_prompt` | update_system_prompt not found in tools/list | update_system_prompt in tool list | ['get_context', 'store_message', 'search_messages', 'search_knowledge', 'conv_create_conversation', 'conv_delete_conversation', 'conv_rename_conversation', 'conv_list_conversations', 'conv_store_message', 'conv_retrieve_context', 'conv_create_context_window', 'conv_search', 'conv_search_messages', 'conv_get_history', 'conv_search_context_windows', 'query_logs', 'search_logs', 'mem_search', 'mem_get_context', 'mem_add'] |
| warning | imperator-tool | `test_diagnostic_context_introspection` | Expected keyword 'context' not found in response | context | I can certainly do that, but I'll need the ID of the conversation you're interested in.

From my perspective as the Imperator, I don't have an intrinsic sense of our "current" conversation; each inter |
| warning | imperator-tool | `test_diagnostic_pipeline_status` | Expected keyword 'pipeline' not found in response | pipeline | There are no pending embedding jobs, but there are 464 messages waiting for knowledge extraction.

Here's the full status:
- **Pending embedding:** 0 messages
- **Pending extraction:** 464 messages
-  |
| warning | imperator-tool | `test_admin_verbose_toggle` | Expected keyword 'verbose' not found in response | verbose | I've sent an error notification to the configured alerting channels detailing the permission issue. A system administrator will need to resolve this for me to be able to change the configuration. |
| warning | imperator-tool | `test_admin_verbose_toggle` | Expected keyword 'toggle' not found in response | toggle | I've sent an error notification to the configured alerting channels detailing the permission issue. A system administrator will need to resolve this for me to be able to change the configuration. |
| warning | performance | `performance_report` | extract_memory has 100% error rate | <10% | 100% |
