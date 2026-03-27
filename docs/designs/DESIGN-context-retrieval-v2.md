# Design: Context Retrieval v2 — Query-Driven RAG with Cache-Friendly Distillation

**Date:** 2026-03-27
**Status:** Proposed
**Author:** Jason + Claude Opus 4.6
**Relates to:** TA-02, TA-03, TA-04, prompt caching, domain RAG

---

## Problem

The current `get_context` API takes `(conversation_id, build_type, budget)`. The retrieval graph uses recent conversation messages as the search query for semantic retrieval and knowledge graph lookups. This has several problems:

1. **Wrong query source** — semantic search and KG retrieval should be driven by the user's current prompt, not the tail of the conversation history.
2. **No distillation** — raw search results are dumped into context without summarization, consuming excessive tokens.
3. **Domain knowledge is TE-side** — the Imperator searches domain info via tools, but these results are ephemeral (lost next turn) or break prompt caching if injected into the system message.
4. **Cache-hostile** — conversation history is embedded in the SystemMessage text, which changes every turn and breaks prompt caching across all LLM providers (estimated 900% cost increase vs cached).
5. **External MADs excluded** — when another MAD's Imperator calls the CB, the CB has no access to that MAD's domain knowledge.

## Proposed API

```
get_context(
    conversation_id: str,
    build_type: str,
    budget: int,
    query: str,                    # NEW: user's current prompt — drives all retrieval
    model: dict,                   # NEW: caller's LLM config for distillation (cache reuse)
    domain_context: str = "",      # NEW: caller's domain RAG results (optional)
)
```

### Parameters

- **`query`** — the user's actual message. Used by the retrieval graph to drive semantic vector search, KG fact retrieval, and (if local) domain info search. Replaces the current pattern of using recent messages as the search query.

- **`model`** — the calling agent's LLM configuration `{base_url, model, api_key_env}`. The AE uses this model for the distillation call so it hits the same prompt cache as the caller. This is critical for cost — the distillation call sees the same system prompt + history prefix, so it's mostly a cache hit.

- **`domain_context`** — optional domain-specific knowledge the caller wants folded into the context. The local CB Imperator passes its own `search_domain_info` results. External MADs pass their own domain context. MADs with no domain knowledge omit it. The CB treats it as additional retrieval input for distillation.

## Architecture

### Call Flow

```
1. User sends message to MAD's Imperator
2. Imperator does local domain RAG (search_domain_info)
3. Imperator calls get_context(
       conversation_id, build_type, budget,
       query=user_message,
       model=imperator_llm_config,
       domain_context=domain_rag_results
   )
4. AE retrieval graph runs:
   a. Load prebuilt tiers (tier1 archival, tier2 chunks, tier3 recent) — fast, precomputed
   b. Semantic search driven by query (pgvector HNSW) — fast with index
   c. KG retrieval driven by query (Mem0/Neo4j) — structured facts
   d. Combine: tiers + semantic results + KG facts + domain_context
   e. Distillation call to caller's model:
      "Given these retrieval results, summarize what is relevant to: {query}"
      Uses caller's model → cache hit on system prompt + history prefix
      Returns concise summary
   f. Budget-fit: trim to requested token budget
   g. Return: distilled context as message array
5. Imperator receives clean, summarized context
6. Imperator makes response call (cache hit on prefix + distilled context)
7. Store: user message (role=user), distilled context (role=assistant), response (role=assistant)
```

### Message Structure for LLM Calls (Cache-Friendly)

```
[SystemMessage]     — static identity prompt (CACHED, never changes)
[HumanMessage]      — turn 1 user (CACHED, prefix grows)
[AIMessage]         — turn 1 response (CACHED)
[AIMessage]         — turn 2 distilled context summary (CACHED — was stored as assistant)
[HumanMessage]      — turn 2 user (CACHED)
[AIMessage]         — turn 2 response (CACHED)
[AIMessage]         — turn 3 distilled context summary (CACHED)
[HumanMessage]      — turn 3 user message (NEW — only uncached tokens)
```

The distilled context summaries persist as assistant messages in conversation history. On future turns, they're part of the cached prefix. The Imperator sees its own prior summaries naturally.

### What Changes

| Component | Current | Proposed |
|-----------|---------|----------|
| `get_context` API | `(conv_id, build_type, budget)` | Add `query`, `model`, `domain_context` |
| Semantic search query | Recent messages tail | User's current prompt |
| KG retrieval query | Recent messages tail | User's current prompt |
| Domain info search | TE-side tool call (ephemeral) | Caller passes results via `domain_context` |
| Distillation | None — raw results in context | New node: LLM summarization using caller's model |
| SystemMessage | Dynamic (identity + history) | Static (identity only) |
| Conversation history | Embedded in SystemMessage text | Separate HumanMessage/AIMessage pairs |
| RAG results | Ephemeral (role=rag, deleted after 7d) | Stored as assistant message (persistent) |
| Prompt caching | Broken every turn | Prefix cached, only new user message uncached |

### Build Type Impact

- **passthrough** — no retrieval, no distillation. Returns raw recent messages. `query`/`model`/`domain_context` ignored.
- **standard-tiered** — no semantic/KG retrieval. Distillation optional (only if `domain_context` provided). Tiers are prebuilt.
- **knowledge-enriched** — full pipeline: tiers + semantic (query-driven) + KG (query-driven) + domain_context + distillation.

### Cost Analysis

Per turn with distillation (knowledge-enriched, 100K context):
- **Distillation call**: ~100K cached tokens (system + history) + ~2K new tokens (search results) = ~$0.003 at Gemini 2.5 Pro cached rate
- **Response call**: ~100K cached tokens + ~500 new tokens (distilled summary + user message) = ~$0.003
- **Total**: ~$0.006 per turn

Without distillation (current, history in SystemMessage, no caching):
- **Response call**: ~100K uncached tokens = ~$0.05 per turn
- **Savings with caching**: ~88%

### Dependencies

- Requires typed vector columns for HNSW index (TA-03, FIXED)
- Requires Mem0 initialization at startup (TA-02, PARTIALLY FIXED)
- Requires extraction validation (TA-04, FIXED)

### Migration Path

1. Add `query`, `model`, `domain_context` as optional parameters to `get_context` (backward compatible)
2. When `query` is provided, use it for semantic/KG search instead of recent messages
3. When `model` is provided, add distillation node to retrieval graph
4. When `domain_context` is provided, include in distillation input
5. Update Imperator flow to pass all three parameters
6. Remove history-in-SystemMessage pattern
7. Remove ephemeral RAG role and cleanup worker (no longer needed)

---

## Open Questions

1. **Distillation prompt** — what instructions produce the best summaries? Need to test different prompts for quality.
2. **Distillation token budget** — how much of the total budget should the distilled summary consume? Currently KG gets 15%, semantic gets 15%.
3. **External MAD protocol** — should `domain_context` be a string or structured (array of articles with metadata)?
4. **Fallback** — if distillation fails (LLM error), return raw results or empty context?
