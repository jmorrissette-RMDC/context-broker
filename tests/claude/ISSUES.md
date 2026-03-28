# Test Issues Log

**Generated:** 2026-03-28T02:03:08.432309+00:00
**Total issues:** 21

| Severity | Category | Test | Description | Expected | Actual |
|----------|----------|------|-------------|----------|--------|
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation ui-ctx-fca382b3 (afccd556-a54d-4c9e-a322-f5c18fe879ae) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation ui-hist-9e8753a9 (d741ee41-3d38-470a-bf1a-6fe64ecf110c) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation ui-test-6b1a2858 (0007aa8e-4d56-4b0d-92a9-7cb9619e9529) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation roundtrip-test (e1a9d3fc-ef15-42d5-a83d-534483476912) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation None (b0f02415-6f39-4c10-b1a7-84631413fe61) has no summaries | >0 summaries | 0 |
| info | imperator | `test_tool_turn_0` | Turn 0: expected tool 'get_context' not detected in response | Tool 'get_context' used | Tools used: none detected |
| info | imperator | `test_tool_turn_1` | Turn 1: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_2` | Turn 2: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_3` | Turn 3: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_4` | Turn 4: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_6` | Turn 6: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_7` | Turn 7: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| warning | imperator-tool | `test_tool_file_list` | None of expected keywords ['config.yml', 'te.yml'] found in response | config.yml, te.yml | Here are the contents of the subdirectories:

**/config/credentials/**
```
/config/credentials
└── postgres_password.txt
```

**/config/prompts/**
```
/config/prompts
├── archival_consolidation.md
├── chunk_summarization.md
└── imperator_identity.md
``` |
| warning | imperator-tool | `test_tool_file_write` | None of expected keywords ['written', 'created', 'saved', 'success', 'wrote'] found in response | written, created, saved, success, wrote | I have sent a `system.misconfiguration` alert to the administrator detailing the issue. I apologize that I could not complete your request. Is there anything else I can help you with? |
| warning | imperator-tool | `test_tool_update_system_prompt` | update_system_prompt not found in tools/list | update_system_prompt in tool list | ['get_context', 'store_message', 'search_messages', 'search_knowledge', 'conv_create_conversation', 'conv_delete_conversation', 'conv_rename_conversation', 'conv_list_conversations', 'conv_store_message', 'conv_retrieve_context', 'conv_create_context_window', 'conv_search', 'conv_search_messages', 'conv_get_history', 'conv_search_context_windows', 'query_logs', 'search_logs', 'mem_search', 'mem_get_context', 'mem_add'] |
| warning | imperator-tool | `test_tool_add_alert_instruction` | None of expected keywords ['added', 'alert', 'instruction', 'created', 'test alerts'] found in response | added, alert, instruction, created, test alerts | I was unable to complete that request within the allowed number of steps. Please try again, or break the request into smaller parts. |
| warning | imperator-tool | `test_tool_list_alert_instructions` | None of expected keywords ['alert', 'instruction', 'test', 'no instruction', 'none'] found in response | alert, instruction, test, no instruction, none | I was unable to complete that request within the allowed number of steps. Please try again, or break the request into smaller parts. |
| warning | imperator-tool | `test_diagnostic_context_introspection` | Expected keyword 'context' not found in response | context | Of course. I can do that, but I'll need the conversation's unique ID (the `conversation_id`).

Could you please provide the ID for the conversation you'd like me to inspect? |
| warning | imperator-tool | `test_admin_verbose_toggle` | Expected keyword 'verbose' not found in response | verbose | I was unable to complete that request within the allowed number of steps. Please try again, or break the request into smaller parts. |
| warning | imperator-tool | `test_admin_verbose_toggle` | Expected keyword 'toggle' not found in response | toggle | I was unable to complete that request within the allowed number of steps. Please try again, or break the request into smaller parts. |
| warning | performance | `performance_report` | imperator_chat averages 6.0s per call | <5s | 6.0s |
