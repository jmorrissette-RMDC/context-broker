# Test Issues Log

**Generated:** 2026-03-27T17:31:20.722710+00:00
**Total issues:** 23

| Severity | Category | Test | Description | Expected | Actual |
|----------|----------|------|-------------|----------|--------|
| error | mem0 | `test_mem_add_embedding_persisted` | mem_add accepted call but stored 0 rows for user_id=live-test-embed-ed17a501 — Mem0 silently failed to persist (TA-04) | >=1 row | 0 rows |
| warning | mem0 | `test_mem_search_finds_added_memory` | mem_search returned no results for a just-added memory after retries; Mem0 may not be fully functional (table schema mismatch) |  |  |
| warning | mem0 | `test_mem_list_returns_memories` | Expected at least 1 memory from mem_list, got 0; Mem0 may not be fully functional (table schema mismatch) |  |  |
| warning | mem0 | `test_mem_delete_removes_memory` | Could not determine memory_id from add result: {'status': 'added', 'result': {'results': [], 'relations': [{'source': 'live-test-del-d87ffb08', 'relationship': 'has_property', 'target': 'temporary_memory_to_be_deleted'}]}}; Mem0 may not be fully functional (table schema mismatch) |  |  |
| warning | mem0 | `test_mem0_memories_has_entries` | mem0_memories table is empty; extraction may not have run or Mem0 table schema mismatch prevents storage | >0 rows | 0 |
| warning | pipeline | `test_no_pipeline_errors_in_recent_logs` | Error in recent logs: claude-test-langgraph  | {"timestamp": "2026-03-27T17:22:29.635340+00:00", "level": "ERROR", "message": "Extraction failed for 76667b9a-783e-4167-81c3-e70f464833a9 (attempt 3/3): Mem0 returned empty r |  |  |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation conversation-2 (4ed21aee-2d10-4a43-b32a-db60fdddfd3d) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation conversation-3 (0810fc79-2104-4b8c-834a-0f2bcac29217) has no summaries | >0 summaries | 0 |
| info | imperator | `test_tool_turn_0` | Turn 0: expected tool 'get_context' not detected in response | Tool 'get_context' used | Tools used: none detected |
| info | imperator | `test_tool_turn_1` | Turn 1: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_2` | Turn 2: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_3` | Turn 3: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_4` | Turn 4: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_6` | Turn 6: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_7` | Turn 7: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| warning | imperator-tool | `test_tool_file_write` | None of expected keywords ['written', 'created', 'saved', 'success', 'wrote'] found in response | written, created, saved, success, wrote | I'm sorry, I was unable to write the file. The system returned a "Permission denied" error.

It appears that the application, running inside its container, does not have the necessary write permissions for the `/data/downloads` directory on the host system. You will need to adjust the directory's pe |
| warning | imperator-tool | `test_tool_update_system_prompt` | update_system_prompt not found in tools/list | update_system_prompt in tool list | ['get_context', 'store_message', 'search_messages', 'search_knowledge', 'conv_create_conversation', 'conv_delete_conversation', 'conv_rename_conversation', 'conv_list_conversations', 'conv_store_message', 'conv_retrieve_context', 'conv_create_context_window', 'conv_search', 'conv_search_messages', 'conv_get_history', 'conv_search_context_windows', 'query_logs', 'search_logs', 'mem_search', 'mem_get_context', 'mem_add'] |
| warning | imperator-tool | `test_diagnostic_context_introspection` | Expected keyword 'context' not found in response | context | Of course. I can do that, but I will need the conversation ID.

Which conversation would you like me to inspect? If you're not sure of the ID, I can list your recent conversations for you. |
| warning | imperator-tool | `test_admin_verbose_toggle` | Expected keyword 'verbose' not found in response | verbose | I'm sorry, I was unable to change the setting. The system returned a "Permission denied" error, just as it did before.

This is a security and stability feature of the system. My role as the Tenant En |
| warning | imperator-tool | `test_admin_verbose_toggle` | Expected keyword 'toggle' not found in response | toggle | I'm sorry, I was unable to change the setting. The system returned a "Permission denied" error, just as it did before.

This is a security and stability feature of the system. My role as the Tenant En |
| warning | imperator | `test_verbose_logging_toggle` | Response does not mention verbose/toggle/logging | verbose or toggle or logging | i'm sorry, i was unable to change the setting. the system returned a "permission denied" error, just as it did before.

this is a security and stability feature of the system. my role as the tenant en |
| warning | performance | `performance_report` | conv_search_messages averages 8.6s per call | <5s | 8.6s |
| warning | performance | `performance_report` | extract_memory has 100% error rate | <10% | 100% |
