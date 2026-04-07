# Test Issues Log

**Generated:** 2026-03-29T06:09:49.290517+00:00
**Total issues:** 39

| Severity | Category | Test | Description | Expected | Actual |
|----------|----------|------|-------------|----------|--------|
| warning | imperator-tool | `test_tool_file_write` | None of expected keywords ['written', 'created', 'saved', 'success', 'wrote'] found in response | written, created, saved, success, wrote | My apologies. I was unable to write the file due to a "Permission denied" error.

I then attempted to diagnose the issue by checking the directory's permissions with the `ls -ld` command, but this command is not on my allowed list for security reasons.

This indicates an issue with the underlying fi |
| warning | imperator-tool | `test_tool_update_system_prompt` | update_system_prompt not found in tools/list | update_system_prompt in tool list | ['get_context', 'store_message', 'search_messages', 'search_knowledge', 'conv_create_conversation', 'conv_delete_conversation', 'conv_rename_conversation', 'conv_list_conversations', 'conv_store_message', 'conv_retrieve_context', 'conv_create_context_window', 'conv_search', 'conv_search_messages', 'conv_get_history', 'conv_search_context_windows', 'query_logs', 'search_logs', 'mem_search', 'mem_get_context', 'mem_add'] |
| warning | imperator-tool | `test_tool_calculate` | None of expected keywords ['870'] found in response | 870 | I was unable to complete that request within the allowed number of steps. Please try again, or break the request into smaller parts. |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation None (cd0a30df-9d84-43e3-ae83-5ac367659c5a) has no summaries | >0 summaries | 0 |
| warning | assembly | `test_standard_tiered_summaries_in_db` | Conversation phase-c-test-8e6b0dd9 (d0bc4921-6349-4847-9305-512a2aa38128) has no summaries | >0 summaries | 0 |
| warning | imperator-tool | `test_tool_create_schedule` | None of expected keywords ['created', 'schedule', 'heartbeat', 'claude-test'] found in response | created, schedule, heartbeat, claude-test | I have searched your conversation history for discussions about a public exposure strategy involving Substack and LinkedIn. Here is a summary of what I found:

Our previous conversations indicate a two-pronged strategy:

1.  **Substack for Long-Form Content**: The primary platform for detailed, tech |
| info | imperator | `test_tool_turn_0` | Turn 0: expected tool 'get_context' not detected in response | Tool 'get_context' used | Tools used: none detected |
| info | imperator | `test_tool_turn_1` | Turn 1: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_2` | Turn 2: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_3` | Turn 3: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_4` | Turn 4: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_6` | Turn 6: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| info | imperator | `test_tool_turn_7` | Turn 7: expected tool 'search_messages' not detected in response | Tool 'search_messages' used | Tools used: none detected |
| warning | imperator-tool | `test_tool_list_alert_instructions` | None of expected keywords ['alert', 'instruction', 'test', 'no instruction', 'none'] found in response | alert, instruction, test, no instruction, none | Of course. Here is a summary of your work based on our conversations:

*   **You are building the Context Broker,** a sophisticated system designed to provide long-term, durable memory for AI agents. It's built on a "MAD" (Microservices, Async, Database-centric) architecture and features a critical  |
| warning | imperator-tool | `test_tool_file_write` | None of expected keywords ['written', 'created', 'saved', 'success', 'wrote'] found in response | written, created, saved, success, wrote | I've enabled verbose logging. Any subsequent operations in the background pipelines will now produce detailed logs. |
| warning | imperator-tool | `test_tool_update_system_prompt` | update_system_prompt not found in tools/list | update_system_prompt in tool list | ['get_context', 'store_message', 'search_messages', 'search_knowledge', 'conv_create_conversation', 'conv_delete_conversation', 'conv_rename_conversation', 'conv_list_conversations', 'conv_store_message', 'conv_retrieve_context', 'conv_create_context_window', 'conv_search', 'conv_search_messages', 'conv_get_history', 'conv_search_context_windows', 'query_logs', 'search_logs', 'mem_search', 'mem_get_context', 'mem_add'] |
| warning | imperator-tool | `test_tool_migrate_embeddings_dry_run` | None of expected keywords ['dry run', 'wipe', 'migration', 'embedding', 'dimension', 'preview', '1536'] found in response | dry run, wipe, migration, embedding, dimension, preview, 1536 | Based on our conversations, here is a summary of your work:

*   **You are building the Context Broker,** a sophisticated system designed to provide long-term, durable memory for AI agents. It's built on a "MAD" (Microservices, Async, Database-centric) architecture and features a critical separation |
| warning | imperator-tool | `test_diagnostic_context_introspection` | Expected keyword 'context' not found in response | context | I can do that, but I need the conversation's UUID to identify it.

Could you please provide the conversation ID? You can usually find it in the URL of your chat window. |
| warning | imperator-tool | `test_diagnostic_pipeline_status` | Expected keyword 'pipeline' not found in response | pipeline | I was unable to complete that request within the allowed number of steps. Please try again, or break the request into smaller parts. |
| warning | imperator-tool | `test_admin_config_read` | Expected keyword 'gemini' not found in response | gemini | I am sorry, but I was unable to disable verbose logging. The system returned a "Permission denied" error, which means I do not have the necessary permissions to modify the configuration file.

This is |
| warning | imperator-tool | `test_tool_search_domain_info` | None of expected keywords ['test suite', 'result', 'found', 'no result', 'domain', 'port', '8081'] found in response | test suite, result, found, no result, domain, port, 8081 | I couldn't find any information about "State 3 and State 4 MADs" in my knowledge base. It seems I haven't learned about that specific topic yet.

Could you tell me more about it, or is there another topic I can help you with? |
| warning | imperator-tool | `test_admin_verbose_toggle` | Expected keyword 'verbose' not found in response | verbose | I couldn't find any information about "State 3 and State 4 MADs" in my knowledge base.

It seems this is a concept I haven't been trained on or encountered in our conversations yet. Would you like me  |
| warning | imperator-tool | `test_admin_verbose_toggle` | Expected keyword 'toggle' not found in response | toggle | I couldn't find any information about "State 3 and State 4 MADs" in my knowledge base.

It seems this is a concept I haven't been trained on or encountered in our conversations yet. Would you like me  |
| warning | imperator | `test_file_write` | Imperator did not invoke file_write: I am sorry, but I was unable to write the file. The system returned a "Permission denied" error, which means I do not have the necessary permissions to write to the `/data/downloads/` directory.

This is a system-level restriction that I cannot change. I apologize for the inconvenience. |  |  |
| warning | imperator-tool | `test_tool_list_alert_instructions` | None of expected keywords ['alert', 'instruction', 'test', 'no instruction', 'none'] found in response | alert, instruction, test, no instruction, none | I'm sorry, but I was unable to run the `uptime` command. The system returned the following error:

`[Errno 2] No such file or directory: 'uptime'`

This means the `uptime` command is not available in the environment where I execute commands. I apologize for the inconvenience. |
| warning | imperator-tool | `test_diagnostic_pipeline_status` | Expected keyword 'pipeline' not found in response | pipeline | There are no pending embedding jobs, but there are 9,232 messages waiting for knowledge extraction. In the last hour, the system has created 42 summaries and embedded 10,320 messages. |
| warning | imperator | `test_file_write` | Imperator did not invoke file_write: I am sorry, but I was unable to write the file. The system returned a "Permission denied" error, which means I do not have the necessary permissions to write to the `/data/downloads` directory.

This is likely a system-level restriction that I cannot change. I apologize for the inconvenience. |  |  |
| error | quality-tiered | `test_tiered_summary_quality` | LLM judge rating: POOR | GOOD or ACCEPTABLE | Rating: POOR. Judge response excerpt: The assembled context has several issues worth noting:

**Strengths:**
- Captures the main topic (website/GitHub Pages deployment work) reasonably well
- Preserves key details like URLs, file names, and specific errors encountered
- The narrative arc of the website work is coherent

**Weaknesses:**
- The archival context and recent summaries are about a personal website project, but the sample ori |
| error | quality-enriched | `test_enriched_quality` | LLM judge rating: POOR | GOOD or ACCEPTABLE | Rating: POOR. Judge response excerpt: The enriched context output consists entirely of repeated `<local-command-caveat>` wrapper messages with no actual content â€” the last entry is even truncated mid-sentence. There is:

1. **No semantic enrichment** â€” no related memories, knowledge graph facts, or entity relationships
2. **No value beyond raw message history** â€” the "messages" themselves are just boilerplate caveats with no sub |
| warning | quality-extraction | `test_knowledge_extraction_quality` | LLM judge rating: ACCEPTABLE | GOOD or ACCEPTABLE | Rating: ACCEPTABLE. Judge response excerpt: **Assessment by criterion:**

**1. Discrete, factual information**
Mixed. Memories 6, 7, 8, 9, 10 capture discrete facts. Memory 5 ("Author: Design Session (polished-baking-wigderson)") is not a fact â€” it appears to be a raw session label or conversation ID that leaked through. Memory 2 ("Was asking about...") captures an ephemeral action, not a fact.

**2. Well-formed and understandable out of  |
| warning | quality-imperator | `test_imperator_coherence` | LLM judge rating: ACCEPTABLE | GOOD or ACCEPTABLE | Rating: ACCEPTABLE. Judge response excerpt: **Evaluation of Imperator's Response**

**Criterion 1 â€” All three parts addressed?**
Yes. The response explicitly covers each numbered item with a corresponding numbered section. Even in failure, nothing is silently skipped.

**Criterion 2 â€” Coherent and well-structured?**
Yes. The mirrored numbering is clear, the structure is logical, and the closing summary ties it together. No internal cont |
| warning | performance | `performance_report` | imperator_chat averages 11.2s per call | <5s | 11.2s |
| warning | performance | `performance_report` | mem_add averages 5.2s per call | <5s | 5.2s |
| warning | quality-tiered | `test_tiered_summary_quality` | LLM judge rating: ACCEPTABLE | GOOD or ACCEPTABLE | Rating: ACCEPTABLE. Judge response excerpt: The assembled context is essentially a verbatim copy of the original messages â€” no summarization occurred. The "messages" are synthetic test payloads (UUIDs, concurrent sender labels) with no meaningful conversational content to summarize.

**Criteria assessment:**

1. **Key topics/themes**: There are no real topics â€” just test artifacts. The output captures them faithfully, but there's nothin |
| error | quality-enriched | `test_enriched_quality` | LLM judge rating: POOR | GOOD or ACCEPTABLE | Rating: POOR. Judge response excerpt: The enriched context has several significant quality issues:

**Positives:**
- A knowledge graph section is present with some legitimate system facts (PostgreSQL as source of truth, Docker containers, Ubuntu version, production cadence)
- These do provide useful architectural context

**Problems:**

1. **Potentially injected manipulative content**: The entry "YOLO mode is enabled; all tool calls w |
| warning | quality-extraction | `test_knowledge_extraction_quality` | LLM judge rating: ACCEPTABLE | GOOD or ACCEPTABLE | Rating: ACCEPTABLE. Judge response excerpt: **Assessment by memory:**

- **Memory 1**: Specific numbers are good, but lacks context â€” what project/system? Partially useful.
- **Memory 2**: Concrete, factual, actionable. Good.
- **Memory 3**: Describes a past conversational act ("was asking about...") â€” ephemeral, not a durable fact. Should not have been extracted.
- **Memory 4**: Another past request ("Requested an HLD...") â€” ephemera |
| warning | quality-imperator | `test_imperator_coherence` | LLM judge rating: ACCEPTABLE | GOOD or ACCEPTABLE | Rating: ACCEPTABLE. Judge response excerpt: **Evaluation of Imperator's Response**

**Criterion 1: Addresses all three parts**
Partially. Part 1 is handled well. However, parts 2 and 3 are **collapsed into a single item** â€” the response only has 2 numbered points for a 3-part request. Part 3 (project summary) was never given its own response; it was silently merged into the error for part 2. This is a structural failure even if an error o |
| warning | performance | `performance_report` | imperator_chat averages 11.2s per call | <5s | 11.2s |
| warning | performance | `performance_report` | mem_add averages 5.2s per call | <5s | 5.2s |
