# Design: Context Compaction v2 — Deadband Tiered Compaction with Prefix Caching

**Date:** 2026-03-30
**Status:** Designed, validated by 3 CLIs (Claude, Gemini, Codex). Not yet implemented.
**Author:** Jason + Claude Opus 4.6

---

## Problem

The current context assembly pipeline has fundamental issues:

1. **Fixed percentages don't scale.** Tier allocations (8%/20%/72%) are the same for an 8K window and an 800K window. A 72% raw allocation on 8K is 5.7K tokens (38 messages). On 800K it's 576K — generous but wasteful compression of the remaining 28%.
2. **No immediate assembly on new windows.** `get_context` creates windows on demand but assembly runs in a background worker. The first call returns empty tiers.
3. **Sequential processing with arbitrary limits.** The assembly worker processes 5 windows per cycle, sequentially. No concurrency.
4. **Tier naming is backwards.** Tier 1 = archival (least important), Tier 3 = recent (most important). Should be the opposite — tier 1 is the primary, most important tier.
5. **No artifact stripping.** Summarization LLM receives raw code blocks, JSON blobs, file listings. Tokens wasted processing artifacts instead of summarizing discussion.
6. **Aggressive compaction destroys continuity.** Claude Code's compaction compresses everything to 1-5K tokens. We should preserve 200K+ tokens of live content for active reasoning.

---

## Design Principles

### 1. Token Caching Efficiency Drives Layout

LLM providers (Gemini, Claude, OpenAI) cache prompt prefixes. Any change to earlier content invalidates the cache for everything after it. Therefore:

- **Most static content goes FIRST** in the prompt (tier 3 archival — changes every ~2.1M tokens of new content)
- **Moderately stable content goes NEXT** (tier 2 chunks — grows monotonically between full compactions)
- **Most dynamic content goes LAST** (tier 1 live — changes every turn)

This maximizes cache hits. The vast majority of turns get cache hits on everything except the latest message. Full cache invalidation only occurs during full compaction (~every 2.1M tokens).

This principle was validated by all 3 CLIs. Codex specifically emphasized it and cited provider documentation for cache behavior.

### 2. Deadband Swing, Not Fixed Percentages

Each tier operates within a range (deadband), not at a fixed percentage. This prevents every message from triggering compaction and gives the system breathing room:

- **Tier 2** swings between 6% and 12%. Each compaction chunk is 2%. Room for 3 new chunks before full compaction triggers.
- **Tier 1** swings between 20% (floor after compaction) and ~73% (ceiling before compaction triggers). The ceiling depends on how full tier 2 is.

The deadband approach was designed by the user. The reasoning: you don't want every line to trigger summary after summary. Each tier needs a swing range before it triggers the next level of compaction. This is analogous to a thermostat — you don't heat and cool on every 0.1 degree change.

### 3. Generous Live Window

Claude Code's compaction compresses everything to 1-5K tokens and gives back the whole window. This is extremely lossy — almost everything is summarized away.

Our approach preserves a minimum of 20% of the window as live, unsummarized content. On a 1M window, that's 200K tokens always available for active reasoning. This was the user's key insight: with large windows, you don't need aggressive compression. You just need to cap how far back you reach and leave a generous raw window at the front.

The 200K minimum was chosen by working backwards: on a 1M context window, 200K of raw content felt reasonable for maintaining continuity without feeling as lossy as Claude's approach. The math: 85% utilization cap - 2% archival - ~10% average chunks = ~73% available for live, with 20% always preserved after compaction.

### 4. Never Return Empty on First Call

When `get_context` creates a new window into an existing conversation, it must return content immediately — not empty tiers waiting for a background worker. The assembly worker processes the initial content inline within the same request (subject to concurrency limits).

For large windows (800K budget) into old conversations, the lookback is capped at ~400K tokens to prevent minutes-long first-assembly times. The formula: `min(budget * initial_lookback_multiplier, max_lookback_tokens)`.

### 5. Compaction, Not Summarization

"Summarization" is one tool of compaction. The compaction process also includes:
- **Artifact stripping** — remove code blocks, JSON, file listings before the LLM sees the content
- **Identifier preservation** — keep file paths, function names, entity names as retrieval hooks even after stripping artifacts
- **Memory extraction** — feed the full chunk context to the knowledge extraction pipeline during compaction, when significance is most visible
- **Tier management** — the deadband swing logic, chunk sizing, tier transitions

The rename from "summarization" to "compaction" reflects this broader scope.

---

## Architecture

### Tier Layout (1M token window example)

```
[---- 15% buffer (keeps total under 85%) ----]
[-- Tier 3: 2% archival --][---- Tier 2: 6-12% chunks ----][----------- Tier 1: 20-73% live -----------]
                                                             ^                                          ^
                                                        floor (20%)                              ceiling (~73%)
```

**Tier 3 — Archival (2% of window)**

Split into two parts:
- **Historical header (~0.25%):** Brief factual record — origin date, participants, major topics, key decisions. Progressively more compressed over time. This is NOT a full summary — it's "what is this conversation about" context.
- **Recent archival (~1.75%):** Summary of the last batch of compacted tier 2 chunks. REPLACED each full compaction cycle — always the most recent batch.

The historical header + recent archival split was designed through discussion. The alternative — replacing tier 3 entirely each cycle — would lose all historical context. The alternative of keeping everything — tier 3 would grow indefinitely. The split preserves a brief history record while keeping the bulk of tier 3 as fresh, recent summaries.

For very long conversations (years), the historical header is the only in-window record of ancient history. Detailed historical lookup is delegated to semantic search and knowledge graph queries — not the context window's job.

**Tier 2 — Chunk Summaries (6%-12% of window)**

Each chunk is 2% of the window. Tier 2 swings between 3 chunks (6%) and 6 chunks (12%). Each chunk is a first-generation summary from raw tier 1 content — never re-summarized. This is important for fidelity (Claude CLI confirmed: tier 2 chunks should always be first-generation summaries).

**Tier 1 — Live (20%-73% of window)**

Raw, unsummarized conversation messages. The most recent content. Minimum 20% always preserved after compaction. Fills up to ~73% before compaction triggers (ceiling depends on tier 2 size).

### Compaction Rhythm (Steady State)

**Tier 1 → Tier 2 compaction:**
1. Tier 1 fills from 20% to ~73% (53% of new content accumulates)
2. Tier 1 compacts: strip artifacts from oldest 53%, summarize to 2% chunk, append to tier 2
3. Tier 1 resets to 20% (the most recent 20% stays live)

**Tier 2 → Tier 3 full compaction:**
After 4 tier 1 compactions, tier 2 has 6 chunks (12%) and can't accept more. The next tier 1 compaction triggers full compaction:
1. 4 oldest tier 2 chunks consolidated into tier 3 recent archival (replaces previous)
2. Existing tier 3 historical header re-summarized with displaced content
3. 2 newest tier 2 chunks remain + new tier 1 chunk = tier 2 back to 3 chunks (6%)

**Cycle frequency:**
- Tier 1 compaction: every ~530K tokens of new content (on 1M window)
- Full compaction: every 4 tier 1 compactions (~2.1M tokens)
- Tier 3 archival update: once per full compaction

### Tier 1 Swing Calculation

Tier 1 ceiling = 85% - tier 2 - tier 3:
- Best case (tier 2 at 6%, tier 3 at 2%): ceiling = 77%, swing = 57%
- Worst case (tier 2 at 12%, tier 3 at 2%): ceiling = 71%, swing = 51%

On a 1M window: 510K to 570K tokens of active work before compaction triggers.

### Compaction Preprocessing

Before sending content to the summarization LLM:
1. **Strip artifacts** — remove code blocks, file listings, JSON blobs, markdown formatting
2. **Preserve identifiers** — keep file paths, function names, entity names in the summary as retrieval hooks
3. **The LLM summarizes the discussion about artifacts, not the artifacts themselves**

The `_clean_for_extraction()` function in `memory_extraction.py` already does this for knowledge extraction. Reuse it for compaction preprocessing.

### Memory Extraction at Compaction Time

When tier 1 compacts, the full chunk context (530K tokens worth of conversation) is also fed to the knowledge extraction pipeline (Mem0). This produces higher quality memories than per-message extraction because the LLM can see significance in context — a decision that spans 50 messages is visible as a decision, not as 50 individual statements.

This was discussed in the context of Codex's suggestion for an append-only decision log. The user determined that "key moments" = memories, and the existing Mem0 infrastructure handles this. The improvement is timing — extract at compaction when context is richest, not per-message in isolation.

### Lookback Cap for Large Budgets

For new windows into old conversations:
- Small budget (8K-32K): lookback = budget * 3 (need to oversample for good summaries)
- Large budget (200K+): lookback capped at ~400K tokens (no need to process entire history)

Formula: `min(budget * initial_lookback_multiplier, max_lookback_tokens)`

The reasoning: with a large window, you can fit a lot of raw conversation. If the conversation is shorter than the budget, you don't need summaries at all. Even if it exceeds the budget, you only need to summarize the oldest portion. The 3x multiplier and fixed chunk-summarize pipeline designed for small windows is unnecessary for large windows.

---

## Assembly Worker Changes

1. **Remove LIMIT 5** — process ALL pending windows each cycle. Assembly per window averages 190ms. Even hundreds of windows takes seconds.
2. **Concurrent assembly** — `asyncio.gather` with configurable `assembly_concurrency` from tuning config. Assembly is I/O bound (LLM calls), so concurrency helps.
3. **Immediate assembly on new window** — when `get_context` creates a new window, trigger assembly inline (within concurrency limit) instead of returning empty and waiting for background worker.

---

## Data Model Clarification

During design, the user clarified four distinct data concepts:

- **History**: Raw, uncurated. Conversations, logs. Everything stored, nothing selected. Searched via vector+BM25. Not curated or chosen.
- **Knowledge**: Relationships. What the knowledge graph stores — entity A relates to entity B. Neo4j/Mem0 graph operations. The existing `mem_*` tools are knowledge operations and should be renamed to `knowledge_*`.
- **Memories**: Dynamically determined. The system decides something is worth remembering based on context. Extracted automatically at compaction time. Future feature: `conv_memory_*` MCP tools, `conversation_memories` table.
- **Information**: Curated. Someone deliberately stores a fact via `store_domain_info`. An intentional act, not automatic extraction.

This clarification led to:
- Renaming `mem_*` MCP tools to `knowledge_*` (Phase 3)
- Planning conversation memories as a future feature (not this session)
- Understanding that compaction-time memory extraction enhances the existing Mem0 pipeline, not a new system

---

## 3-CLI Validation Summary

All 3 CLIs (Claude, Gemini, Codex) validated the design. No fundamental flaws found.

### Consensus (all 3 agree)
- Keeping 20%+ live tokens is the strongest design decision
- Artifact stripping is correct — preserve identifiers as retrieval hooks
- Summary drift is the main risk over time
- Prompt ordering (static first, dynamic last) is critical for prefix caching

### Claude CLI Highlights
- Soft compaction boundary — avoid splitting mid-reasoning-chain, wait for topic breaks
- Compression ratio is 26:1 at 530K→20K — validate empirically, consider 3% chunks if quality suffers
- Tier 2 chunks must be first-generation summaries only (never re-summarized)
- Keep historical header byte-identical during full compaction for partial cache preservation

### Gemini CLI Highlights
- Pinning mechanism — allow user/Imperator to pin content to prevent compaction (future)
- Semantic headers on tier 2 chunks — one-line key per chunk for navigation
- Dynamic tier sizing based on artifact density
- Compared to MemGPT (paging metaphor) and Zep (message window + summary)

### Codex CLI Highlights
- Prefix ordering is critical — static content MUST be first in prompt (all providers)
- Split historical header into append-only decision log + rewritable narrative (user decided this = memories, handled by Mem0)
- Artifact index in tier 2/3 — short IDs/labels so summaries can reference stripped artifacts
- Coverage check before committing summaries — verify no constraints lost
- Align tier sizes to cache chunk boundaries (1024 + 128n tokens)

---

## Future Features (not part of this implementation)

### Conversation Memories
- `conversation_memories` table with vector embeddings
- Auto-populated during compaction, manually addable by Imperators/users
- MCP tools: `conv_memory_add`, `conv_memory_search`, `conv_memory_list`, `conv_memory_delete`
- Scoped per conversation, not global

### Artifacts
- Artifact table storing extracted code blocks, file contents, JSON blobs
- Inline replacement with pointers in conversation messages
- UI viewer for artifact browsing
- Reduces token usage for code-heavy conversations
- Less critical with large windows — the value is in summarization preprocessing, not token savings

---

## Related Documents

- `DESIGN-context-retrieval-v2.md` — V2 query-driven RAG with cache-friendly distillation
- `docs/quorum-hld/anchor_context/c1-the-context-broker.md` — Original concept paper defining three-tier assembly
- REQ-001 §2.1 — StateGraph mandate (all compaction logic must be StateGraphs)
- REQ-002 §5.1.1 — Zero-Script Deployment (compaction must work on fresh deploy)
