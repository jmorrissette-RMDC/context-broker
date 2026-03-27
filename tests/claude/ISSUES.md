# Test Issues Log

**Generated:** 2026-03-27T19:59:58.115659+00:00
**Total issues:** 26

| Severity | Category | Test | Description | Expected | Actual |
|----------|----------|------|-------------|----------|--------|
| error | mem0 | `test_mem_add_embedding_persisted` | mem_add accepted call but stored 0 rows for user_id=live-test-embed-ecc18009 — Mem0 silently failed to persist (TA-04) | >=1 row | 0 rows |
| warning | mem0 | `test_mem_search_finds_added_memory` | mem_search returned no results for a just-added memory after retries; Mem0 may not be fully functional (table schema mismatch) |  |  |
| warning | mem0 | `test_mem_list_returns_memories` | Expected at least 1 memory from mem_list, got 0; Mem0 may not be fully functional (table schema mismatch) |  |  |
| warning | mem0 | `test_mem_delete_removes_memory` | Could not determine memory_id from add result: {'status': 'added', 'result': {'results': [], 'relations': [{'source': 'live-test-del-8513e356', 'relationship': 'has_temporary_memory', 'target': 'temporary_memory'}]}}; Mem0 may not be fully functional (table schema mismatch) |  |  |
| warning | mem0 | `test_mem0_memories_has_entries` | mem0_memories table is empty; extraction may not have run or Mem0 table schema mismatch prevents storage | >0 rows | 0 |
| warning | pipeline | `test_no_pipeline_errors_in_recent_logs` | Error in recent logs: claude-test-langgraph  | {"timestamp": "2026-03-27T19:50:59.578207+00:00", "level": "ERROR", "message": "Extraction failed for 25d6537b-e409-4db1-8c5d-80f4df0f83fc (attempt 2/3): Mem0 returned empty r |  |  |
| warning | pipeline | `test_no_pipeline_errors_in_recent_logs` | Error in recent logs: claude-test-langgraph  | {"timestamp": "2026-03-27T19:51:06.640590+00:00", "level": "ERROR", "message": "Extraction failed for 05135193-6ea7-41fe-82c9-2bbc5bccd6df (attempt 3/3): Mem0 returned empty r |  |  |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation conversation-2 (739c08bc-9c3a-483d-ad58-e149e4dd6afe) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation conversation-3 (317f2dee-5ed3-4050-8c60-06b7f2b1f434) has no summaries | >0 summaries | 0 |
| info | imperator | `test_tool_turn_0` | Turn 0: expected tool 'get_context' not detected in response | Tool 'get_context' used | Tools used: none detected |
| info | imperator | `test_tool_turn_1` | Turn 1: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_2` | Turn 2: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_3` | Turn 3: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_4` | Turn 4: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_6` | Turn 6: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_7` | Turn 7: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| warning | imperator-tool | `test_tool_file_write` | None of expected keywords ['written', 'created', 'saved', 'success', 'wrote'] found in response | written, created, saved, success, wrote | I have sent a notification to the system administrator detailing the permission issue. They will need to correct the directory permissions on the host system.

I am unable to complete your request to write the file until this infrastructure problem is resolved. Is there anything else I can help you  |
| warning | imperator-tool | `test_tool_update_system_prompt` | update_system_prompt not found in tools/list | update_system_prompt in tool list | ['get_context', 'store_message', 'search_messages', 'search_knowledge', 'conv_create_conversation', 'conv_delete_conversation', 'conv_rename_conversation', 'conv_list_conversations', 'conv_store_message', 'conv_retrieve_context', 'conv_create_context_window', 'conv_search', 'conv_search_messages', 'conv_get_history', 'conv_search_context_windows', 'query_logs', 'search_logs', 'mem_search', 'mem_get_context', 'mem_add'] |
| warning | imperator-tool | `test_tool_add_alert_instruction` | None of expected keywords ['added', 'alert', 'instruction', 'created', 'test alerts'] found in response | added, alert, instruction, created, test alerts | I was unable to complete that request within the allowed number of steps. Please try again, or break the request into smaller parts. |
| warning | imperator-tool | `test_tool_list_alert_instructions` | None of expected keywords ['alert', 'instruction', 'test', 'no instruction', 'none'] found in response | alert, instruction, test, no instruction, none | I was unable to complete that request within the allowed number of steps. Please try again, or break the request into smaller parts. |
| warning | imperator-tool | `test_diagnostic_context_introspection` | Expected keyword 'context' not found in response | context | Of course. What is the ID of the conversation you would like me to inspect? |
| warning | imperator-tool | `test_search_imperator_design` | Expected keyword 'Imperator' not found in response | Imperator | I was unable to complete that request within the allowed number of steps. Please try again, or break the request into smaller parts. |
| warning | imperator-tool | `test_admin_verbose_toggle` | Expected keyword 'verbose' not found in response | verbose | I have sent a notification detailing the permission error. An administrator will need to adjust the file permissions on the host system for me to be able to modify the configuration. |
| warning | imperator-tool | `test_admin_verbose_toggle` | Expected keyword 'toggle' not found in response | toggle | I have sent a notification detailing the permission error. An administrator will need to adjust the file permissions on the host system for me to be able to modify the configuration. |
| warning | imperator | `test_verbose_logging_toggle` | Response does not mention verbose/toggle/logging | verbose or toggle or logging | i've confirmed that i'm running as user `context-broker` (uid 1001). the attempt to write to `/config/config.yml` failed with this user, indicating a file permission issue.

i have sent a notification |
| warning | performance | `performance_report` | extract_memory has 100% error rate | <10% | 100% |
