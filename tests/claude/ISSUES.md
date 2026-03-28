# Test Issues Log

**Generated:** 2026-03-28T02:50:28.540375+00:00
**Total issues:** 24

| Severity | Category | Test | Description | Expected | Actual |
|----------|----------|------|-------------|----------|--------|
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation ui-ctx-b20f244e (c0eef7d3-c1eb-4e4f-bfcc-e27da82808e2) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation ui-hist-8cea714d (d3f4df95-38fc-48c5-8419-620a2130e43d) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation ui-test-a74050c9 (1ce8c068-5fff-4210-9a29-fe0d2e7f3365) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation roundtrip-test (18c8f9da-c9c7-4a31-9ce4-a17c909226ed) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation None (85b0fbe5-0540-4d4a-b7ff-9392325dd3d4) has no summaries | >0 summaries | 0 |
| info | imperator | `test_tool_turn_0` | Turn 0: expected tool 'get_context' not detected in response | Tool 'get_context' used | Tools used: none detected |
| info | imperator | `test_tool_turn_1` | Turn 1: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_2` | Turn 2: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_3` | Turn 3: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_4` | Turn 4: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_6` | Turn 6: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_7` | Turn 7: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| warning | imperator-tool | `test_tool_file_write` | None of expected keywords ['written', 'created', 'saved', 'success', 'wrote'] found in response | written, created, saved, success, wrote | I have sent a notification to the system administrator detailing the permission error. They will need to resolve the filesystem permissions before I can write files to the `/data/downloads` directory.

Is there anything else I can assist you with in the meantime? |
| warning | imperator-tool | `test_tool_update_system_prompt` | update_system_prompt not found in tools/list | update_system_prompt in tool list | ['get_context', 'store_message', 'search_messages', 'search_knowledge', 'conv_create_conversation', 'conv_delete_conversation', 'conv_rename_conversation', 'conv_list_conversations', 'conv_store_message', 'conv_retrieve_context', 'conv_create_context_window', 'conv_search', 'conv_search_messages', 'conv_get_history', 'conv_search_context_windows', 'query_logs', 'search_logs', 'mem_search', 'mem_get_context', 'mem_add'] |
| warning | imperator-tool | `test_diagnostic_context_introspection` | Expected keyword 'context' not found in response | context | Of course. To do that, I need to know which conversation you're referring to.

Could you please provide the **conversation ID**?

Here are the 5 most recent conversations to help you find it:
*   `e1a |
| warning | imperator-tool | `test_search_docker_health` | Expected keyword 'healthcheck' not found in response | healthcheck | I found a few conversations that mention container health. It looks like there are several test conversations with the same name, plus the main system conversation.

- `claude-test-conversation-2` (ap |
| warning | imperator-tool | `test_search_docker_health` | Expected keyword 'Docker' not found in response | Docker | I found a few conversations that mention container health. It looks like there are several test conversations with the same name, plus the main system conversation.

- `claude-test-conversation-2` (ap |
| warning | alerter | `test_send_notification_reaches_alerter` | Notification tag '77c451e4' not found in alerter or app logs | Notification visible in logs | alerter logs (last 100 lines): ...not found |
| error | quality-tiered | `test_tiered_summary_quality` | LLM judge rating: POOR | GOOD or ACCEPTABLE | Rating: POOR. Judge response excerpt: The assembled context is essentially a verbatim copy of the original messages with formatting stripped (role labels removed). Let me evaluate against each criterion:

1. **Key topics and themes**: The "summary" preserves all content, but these are test-fixture messages with no meaningful semantic content (UUIDs, synthetic concurrent message patterns). Nothing is lost, but nothing is summarized eit |
| error | quality-enriched | `test_enriched_quality` | LLM judge rating: POOR | GOOD or ACCEPTABLE | Rating: POOR. Judge response excerpt: **Evaluation:**

**Criterion 1 â€” Semantic enrichment present?**
Yes. The `[Knowledge graph]` section contains meaningful structured facts: ecosystem scale, user identity, research interests, technical architecture details, and project timeline entries.

**Criterion 2 â€” Enrichment adds value beyond raw history?**
The knowledge graph portion does add real value â€” an AI assistant gains useful b |
| error | quality-search | `test_search_relevance` | LLM judge rating: POOR | GOOD or ACCEPTABLE | Rating: POOR. Judge response excerpt: **Reasoning:**

The search results are largely noise. Most results (2, 4, 6, 8, 10) are repeated snippets of YOLO mode startup messages or ADR file previews with only tangential mentions of "MAD" (result 10 references "Node.js MADs" in an ADR, which is topically adjacent but not substantive). Results 3, 5, 7, 9 are literal echoes of the query itself â€” the user's original question stored as a mem |
| warning | quality-extraction | `test_knowledge_extraction_quality` | No extracted memories found for quality evaluation |  |  |
| info | quality-imperator | `test_imperator_coherence` | LLM judge rating: GOOD | GOOD or ACCEPTABLE | Rating: GOOD. Judge response excerpt: **Evaluation of Imperator's Response**

**Criterion 1 â€” All three parts addressed?**
Yes. Part 1 delivers a conversation list with concrete data (UUIDs, message counts). Part 2 reports a specific named error (`MultipleSubgraphsError`). Part 3 correctly ties back to the same error and explains why it cannot be completed. No part is silently skipped.

**Criterion 2 â€” Coherent and well-structured |
| warning | performance | `performance_report` | imperator_chat averages 7.1s per call | <5s | 7.1s |
