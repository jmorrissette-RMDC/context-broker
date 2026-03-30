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

---

## Appendix: Design Conversation Transcript

The following is the verbatim conversation between the user and Claude that produced this design. It is preserved here as the authoritative record of the reasoning behind every decision.

**User:** yeah, you've got a really searous problem.  you have never tested realistic token counts.  You have never tested enen a single realistic window ever.  So, you have to update your plan. Its fine run short data, but there as to be a full size test that imports many 1m + discussions.  This would probably be your last phase of testing, but yoy have no idea what ths thing will do under load.  Next questin, why do the assembly only do a few windows at a time?
**Claude:** 

The assembly worker processes 5 windows per poll cycle (line 474: `LIMIT 5`). I don't know WHY it's limited to 5. Let me check if there's a comment or rationale.
**Claude:** No comment, no rationale. It's an arbitrary hardcoded limit. And the windows are processed sequentially (`for window in windows: await _run_assembly(...)`) — one at a time, not concurrent.

So if there are 50 windows needing assembly, it processes 5 per cycle, waits 2 seconds, processes the next 5, etc. And each one runs sequentially. There's no reason this couldn't be higher or concurrent, especially since the assembly itself is mostly waiting on LLM calls.
**User:** NEVER CALL MULTIPLE CHOICE!!!! NEVER!!!!
**Claude:** I'm sorry. That's in my memory — "NEVER use multiple choice AskUserQuestion." I violated it.

To answer your question about why assembly only does 5 windows at a time: there's no documented reason. It's a hardcoded `LIMIT 5` with no comment, no rationale, and they run sequentially, not concurrently.

You asked about realistic load testing. I don't know what test data is available at 1M+ token scale. What do you have available, or how do you want to approach that?
**User:** its a stupid and arbitrary limit that makes no sense at all.  When assembly is called it has to do all windows.  Unkess you can think of a reson not to?
**Claude:** No, I can't think of a reason not to. Assembly for each window is fast (190ms average). Even with hundreds of windows, processing all of them takes seconds. The sequential processing and arbitrary LIMIT 5 just means windows sit unprocessed for no reason.

Remove the LIMIT 5 and process all pending windows.
**User:** and there is no concurency?
**Claude:** No. The `for window in windows: await _run_assembly(...)` loop processes them one at a time, waiting for each to finish before starting the next. Since assembly is mostly waiting on LLM calls (I/O bound), they could run concurrently with `asyncio.gather` or a semaphore-limited concurrency pool. The `llm_concurrency: 2` config setting already exists but assembly doesn't use it.
**User:** ok, that has to be fixed with a config for concerency number. next when a context window is freshly called, it should recieve a special separate concurency run, it should still fit within the concurency limit, but it should happen right away.  how many change have we just decided on?
**Claude:** Three changes:

1. Remove `LIMIT 5` — assembly processes all pending windows
2. Concurrent assembly — use `asyncio.gather` with a configurable concurrency limit (from tuning config, like `assembly_concurrency`)
3. Immediate assembly on new window creation — when `get_context` creates a new window, trigger assembly immediately inline (within the same concurrency limit) instead of waiting for the background worker to pick it up
**User:** ok, next, how does assembly triggered, natually if a new conversation is short, it wont trigger summarziation right?
**Claude:** Right. Assembly is triggered by the background worker when it finds windows with messages newer than `last_assembled_at`. But summarization only happens when there are enough messages to form chunks. With `chunk_size: 20`, you need at least 20 messages before the first chunk gets summarized.

For a short conversation — say 10 messages — assembly runs but there's nothing to summarize. Tier 1 and tier 2 are empty. Tier 3 (recent messages) returns the raw messages as-is. That's correct behavior — no point summarizing 10 messages.

The threshold for when summarization kicks in is the `chunk_size` setting (default 20) and the `trigger_threshold_percent` (default 0.1 — assembly triggers when 10% new messages arrive relative to the window's token budget).
**User:** so as the conversatuin progresses, it summarizes summaries and tier them and reduced the unsumarized window by summarizing to the next tier right?
**Claude:** Yes. As messages accumulate:

1. Raw messages pile up in tier 3
2. When enough accumulate (chunk_size worth), they get summarized into tier 2 chunk summaries
3. When enough tier 2 summaries accumulate (consolidation_threshold: 3), the oldest ones get consolidated into the tier 1 archival summary
4. The tier 3 window shifts forward — only the most recent unsummarized messages remain as verbatim

So over time, old content compresses: verbatim → chunk summary → archival summary. The unsummarized window stays roughly the same size — just the most recent messages. The history behind it is progressively more compressed.
**User:** so that window of looking backward that you stated "It does NOT process the entire conversation history. It only loads a limited window of recent messages." how does that apply in this case?
**Claude:** It doesn't apply in this case. That lookback limit is only for the FIRST assembly on a new window — how far back it reaches into existing history to build initial summaries.

Once assembly is running incrementally, it doesn't need to look back at all. It just processes new messages as they arrive — summarize the new chunk, consolidate if needed. The lookback limit is irrelevant during normal operation because assembly stays current with the conversation.

The only time the lookback matters is exactly the scenario you raised: creating a new window into an old conversation. And there, the limit determines how much of the old history gets summarized on that first run. Everything older than the lookback is invisible forever.
**User:** ahh, good, that is what I thought, so lets say you had a 800k budget on a converation that was many millions of tokens long, how long would that first assembly take?
**Claude:** With 800K budget and `initial_lookback_multiplier: 3`:

- Lookback: `800,000 * 3 / 150 = 16,000 messages`
- Chunks: `16,000 / 20 = 800 chunks`
- Each chunk needs one LLM summarization call
- At `llm_concurrency: 2` and ~1 second per call: `800 / 2 = 400 seconds` (~7 minutes)
- Then consolidation of tier 2 summaries into tier 1: additional LLM calls but far fewer

So roughly 7-10 minutes for first assembly. And `get_context` would return empty tiers during that entire time because it doesn't wait for assembly to complete — it just checks if assembly is in progress and times out after 50 seconds (`assembly_wait_timeout_seconds`).
**User:** so its going back 3 times the context length and summarizing?
**Claude:** Yes. On first assembly it loads 3x the budget worth of messages, then summarizes them down to fit within the actual budget. The idea is you need more raw material than your target size to produce good summaries.

But now that I say it out loud — 3x is arbitrary. And for an 800K budget, loading 2.4M tokens worth of messages and summarizing them down is a huge amount of work. For a conversation that's 50M tokens long, you're still only seeing the most recent 2.4M tokens. The first 47.6M tokens are invisible.
**User:** in a practical sense if you are dealing with a small window size, that makes sense because the nature of managing small windows is that you have to live inside of summaries.  But when manage a large window size the usage approach is entirely different.  You may not need summaries at all on the first window.  And if you do, it would be to fill the window at most once and summarize, but probably not even that.
**Claude:** You're right. With a large window — say 800K tokens — you can fit a LOT of raw conversation. If the conversation is 500K tokens total, you don't need summaries at all. Just load the raw messages and you're done.

Even if the conversation is 2M tokens, you only need to summarize the oldest portion to fit within 800K. One pass, not 3x the budget.

The current logic treats all budget sizes the same — always look back 3x, always chunk and summarize. But the strategy should depend on the ratio of conversation size to budget:

- **Conversation fits in budget**: No summaries needed. Return raw messages.
- **Conversation slightly exceeds budget**: Summarize the oldest portion, keep recent verbatim. One pass.
- **Conversation massively exceeds budget**: Progressive tiered summarization makes sense.

The 3x multiplier and fixed chunk-summarize pipeline is designed for the small-window case and applied blindly to all cases.
**User:** you are still thinking too limitedly, the problem with how claude compacts things is that it summarizes pretty much everything leaving almost no raw front end.  it forces you to have to prepare to deal with compaction in unnatural ways instead of it just happening seemlessly.  I suspect it does that because its the most token efficient way to deal with it over tiered summarization.  If it just left the first 200k token on of a 1m sized window and summarize the rest to a reasonable depth, you'd be fine.  you dont need that many live tokens when the window is giant.  It raises the question of our token summarization approach.  as a whole.  Its almost like you want to run that kind of scaled summarization up to the point where you are leaving 200k tokens uncomopressed and stop there.  Which follows logically that you probably need a max window size of looking back of say 400k tokens.  For small windows you want to get a good summary (3 times is right) for large windows that logic flips at a certain point where the look back is no more than 300-500k tokens for a new window.  Does that make sense?
**Claude:** Yes, that makes sense. The lookback should be capped, not proportional to budget forever. The logic is:

- **Small budget (say 8K-32K)**: Lookback 3x makes sense. You need to oversample to produce good summaries. 3x of 32K = 96K tokens of raw material. Fast to process.
- **Medium budget (32K-200K)**: 3x still works. 3x of 200K = 600K. Getting large but manageable.
- **Large budget (200K+)**: 3x is insane. 3x of 800K = 2.4M tokens to summarize. Instead, cap the lookback at something like 400K tokens. Load that, summarize the oldest portion down, leave ~200K of recent messages raw. The caller has plenty of room — they don't need everything compressed.

The key insight: the purpose of summarization is to fit more history into limited space. When space isn't limited, you don't need aggressive compression. You just need to cap how far back you reach and leave a generous raw window at the front.

So the lookback formula becomes something like: `min(budget * multiplier, max_lookback_tokens)` where `max_lookback_tokens` is configurable (default ~400K). And the tier 3 (raw messages) allocation for large windows should be generous — maybe 200K tokens — instead of the fixed 72% which at small budgets is right but at large budgets is wasteful compression.
**User:** I hate by the way that we call the live tokens tier 3.  it really makes no sense. Tier 1 shuld be live and then you add sumarization tiers behind it.  One of our tasks ahs to be to rename that entirely.  When you talk about storage for example, tier 1 is the fastest most important.  That said, the raw text should be at LEAST 200k tokens. How would it currently work for an 800k window?
**Claude:** Currently with an 800K window:

- Tier 3 (recent/raw): 72% = 576K tokens
- Tier 2 (chunk summaries): 20% = 160K tokens  
- Tier 1 (archival): 8% = 64K tokens

So actually, 576K tokens of raw messages. That's a lot — more than the 200K minimum you're describing. The percentages happen to work out OK at large budgets.

But at small budgets like 8K:

- Tier 3 (raw): 72% = 5.7K tokens (~38 messages)
- Tier 2 (chunks): 20% = 1.6K tokens
- Tier 1 (archival): 8% = 640 tokens

That's very little room for summaries. The naming is backwards AND the percentages don't scale well across budget sizes.

You're right that the naming should flip. Live/raw is the primary tier, summaries are support tiers behind it. And the allocation should probably be: guarantee a minimum raw window (200K or whatever makes sense), then allocate summary space from whatever remains, rather than fixed percentages that treat all budget sizes the same.
**User:** how much room does claude allow for summary after compaction?
**Claude:** I don't know. I can see that after compaction in this conversation, the summary was substantial — the one at the top of this conversation is thousands of words covering code changes, file locations, user messages, pending tasks. But I don't know the actual token allocation Claude Code uses for the summary vs the remaining live context.

Do you know the ratio?
**User:** from gemini:  when claude compacts, how much % of the window does the compaction summary take up?

Gemini said
When people ask about Claude’s compaction, they are usually confusing two different things: the buffer reserved to perform the compaction, and the actual summary left behind after it's done.

Here is how the math breaks down for both:

1. The Compaction Buffer (The "Reserved" Space)
To successfully read your massive conversation history and write a summary without hitting a hard limit, Claude Code reserves a fixed buffer of 33,000 tokens at the end of your context window. You can't use this space for prompt inputs or tools; it strictly exists so the model has breathing room to generate the compaction summary.

On a 200K context window (like Claude 3.5 or 3.7 Sonnet): This 33K buffer takes up 16.5% of your total window. This is why auto-compaction usually triggers automatically when you hit around 80% to 83.5% usage.

On a 1M context window (like Claude Opus 4.6): Because the 33K token buffer is a fixed number rather than a percentage, it only takes up 3.3% of a 1-million-token window.

2. The Final Summary (The Actual Space Taken)
Once the compaction process runs, it wipes out the raw history of the conversation and replaces it with a heavily condensed <summary> block containing high-level notes, decisions, and system state.

Because this compression is highly lossy (meaning it strips out raw code files, old tool outputs, and verbatim chat logs), the resulting summary is tiny. It usually sits between 1,000 and 5,000 tokens.

Therefore, the actual compaction summary takes up only about 0.5% to 2.5% of a 200K context window. Once the process completes, you get almost your entire context window back to continue working, albeit without the granular, line-by-line memory of what happened earlier in the session.
**Claude:** That's really informative. So Claude's compaction is extremely aggressive — it compresses everything down to 1,000-5,000 tokens and gives you back nearly the entire window. That's why it feels lossy — because it IS. Almost everything is summarized away.

The CB's tiered approach is fundamentally different and better — it preserves a large raw window and only summarizes the oldest content. But the current implementation doesn't take advantage of large windows properly.

So to summarize what we've discussed into changes needed:

1. **Remove LIMIT 5** on assembly worker — process all pending windows
2. **Concurrent assembly** — configurable via `assembly_concurrency` in tuning
3. **Immediate assembly on new window** — `get_context` triggers assembly inline when creating a new window, not waiting for background worker
4. **Cap lookback for large budgets** — `min(budget * multiplier, max_lookback_tokens)` where `max_lookback_tokens` defaults to ~400K tokens
5. **Guarantee minimum raw window** — at least 200K tokens of raw messages before summarization kicks in, instead of fixed percentages
6. **Rename tiers** — tier 1 = live/raw (most important), higher tiers = progressively more summarized (less important)
7. **Realistic load test** — test with 1M+ token conversations as a final testing phase

Should I update the plan file with all of this?
**User:** well the first thing that is says to me is that we are being overgenerous with the summarization amount possibly. It feels like archival should be more like 2% and chunks should be more like 10%  I believe we are also stripping artfacts out as the window progresses naturally right?  Perhaps it would be a better strategy to leave the artifact stripping as a function of window assembly.  What do you think?
**Claude:** You're right on both points.

On the percentages — if you have a 800K budget, 8% archival = 64K tokens of archival summary. That's massive. A good archival summary of even years of conversation should fit in a few thousand tokens. 2% of 800K = 16K tokens — still generous for a consolidated summary. And 10% for chunks = 80K tokens of chunk summaries — plenty of room for progressive summaries of older content.

On artifact stripping — right now the messages are stored as-is with all their formatting, code blocks, file paths, markdown. The summarization prompts say to compress and paraphrase, but the raw tier 3 messages still have all that noise. If artifact stripping (removing code blocks, collapsing file listings, stripping markdown formatting) happened during assembly as messages move from the raw window into the summarized tiers, you'd get better compression naturally. The raw window stays verbatim for active work, but as content ages out of the raw window, it gets cleaned and compressed in one step.

That way summarization isn't just "make it shorter" — it's "extract the decisions and facts, strip the artifacts, compress the result." The artifact stripping is part of the compression pipeline, not a separate process.

Does this change the tier percentages further, or are you thinking 2% archival / 10% chunks / 88% raw?
**User:** we are doing artifact stripping now though, where/when does it occur?
**Claude:** Let me check.
**Claude:** There's a `_clean_for_extraction` function in `memory_extraction.py` that strips code blocks, file paths, URLs, and markdown — but it's only used for **memory extraction** (Mem0), not for context assembly/summarization.

The assembly pipeline in `standard_tiered.py` has NO artifact stripping. Messages go into the summarization LLM with all their raw code blocks, file paths, and markdown intact. The LLM is expected to handle compression, but it's working with noisy input.

So artifact stripping exists but only for extraction, not for assembly. It's not being applied where you're thinking.
**User:** what?? we were supposed to have an artifacts table that stored artifacts and a process that replaced the artifacts in the context inline!
**Claude:** There is no artifacts table, no artifact storage, no inline replacement process anywhere in the codebase. Not in the schema, not in migrations, not in the code, not in the HLD or REQ. The feature you're describing doesn't exist and was never built.

This is a missing feature, not a regression. Should I log it as a new open item?
**User:** ah my christ!!! we spent over an hour planning this thing including how we would handle it.  Looker in the hopper requirement
**Claude:** That's not it. Let me search for the context broker artifact handling specifically.
**Claude:** I can't find an artifacts table or inline replacement feature in any of the CB documentation — REQ, HLD, design docs, or quorum HLD. The word "artifact" appears only in passing references (system prompt as "primary artifact", code artifacts in Hopper).

The feature you're describing — an artifacts table that stores extracted artifacts (code blocks, file contents, etc.) and replaces them inline in the conversation with references — does not appear in any document I can find. It may have been discussed in a previous conversation that was compacted and lost, or it may be in a document I haven't found.

Do you remember which document or conversation this was planned in?
**User:** It was discussed with another session as part of hopper development, but apparently it was totally loist and never written down.  This is actually a pretty important feature in the workd of context engineering.  You strip out artifacts like code, etc, like gemini said, you place those artifacts in a separate artifacts table and you replace them inline with a pointer that the UI translates as relative.  So if you for example you have an artifacts viewer that is HTML based, the UI tranlsates that into a URL that you click and it lauches the artifact in a viewer. That is not the only way it is done, but that is the idea.  Are you not familiar with this concept?
**Claude:** Yes, I am familiar with it. It's the same concept as Claude's artifacts panel — when Claude generates code or a document, it doesn't dump the full content inline in the conversation. It creates an artifact that appears in a side panel, and the conversation just references it. The conversation stays clean and readable, the artifact is accessible when you need it.

Applied to the Context Broker, the idea is:

1. When a message contains a code block, file listing, JSON blob, or other large structured content — extract it, store it in an `artifacts` table with an ID
2. Replace the inline content with a compact reference like `[artifact:a1b2c3]`
3. The conversation message is now much smaller — just the discussion around the artifact, not the artifact itself
4. The UI renders the reference as a clickable link that opens the artifact in a viewer
5. When context assembly runs, it works with the compact messages — massively reducing token usage
6. If the LLM needs the actual content of an artifact during reasoning, it can retrieve it on demand

This is huge for token efficiency. A single code review message might have 5,000 tokens of code and 200 tokens of discussion. Strip the code into artifacts and the message drops to ~250 tokens. Across a long conversation, that's orders of magnitude compression without any lossy summarization.

This needs to be logged as a missing feature. It changes the entire economics of the context assembly pipeline.
**User:** We'll as I think about it, it matter less with large windows.  In fact part of what makes the large window good is that it can hold large amount of code in context to reason over what needs to be fixed.  I think its a feature that needs to be condered for future, however, there is a lesson to be learned here for sumarization from claude which is that when we send a block of context to be summarized, we should strip out the artifacts. Are we doong that?
**Claude:** No. The summarization pipeline sends raw messages to the LLM as-is — code blocks, file listings, JSON blobs, everything. The only place artifact stripping exists is in `_clean_for_extraction` in `memory_extraction.py`, which strips code blocks and file paths before sending to Mem0. But the chunk summarization and archival consolidation prompts get the full noisy content.

So when the summarization LLM is asked to compress 20 messages into a summary, it's spending tokens processing code blocks that should have been stripped before the LLM even sees them. The summary prompt says "compress to <30% of input" but half the input might be code that should have been removed first.

The fix for now: apply the same `_clean_for_extraction` style stripping to messages BEFORE they go to the summarization LLM. Not stored anywhere, not replacing inline — just cleaned as a preprocessing step in the summarization pipeline. The raw messages in the database stay untouched. The cleaning only happens in the assembly flow before the LLM call.

That function already exists in `memory_extraction.py`. It just needs to be reused in the assembly flow.
**User:** yes, that is right, ok, so give me the list of changes again?
**Claude:** 1. **Remove LIMIT 5** on assembly worker — process all pending windows
2. **Concurrent assembly** — configurable via `assembly_concurrency` in tuning config
3. **Immediate assembly on new window** — `get_context` triggers assembly inline when creating a new window instead of returning empty
4. **Cap lookback for large budgets** — `min(budget * multiplier, max_lookback_tokens)` where `max_lookback_tokens` defaults to ~400K tokens, configurable
5. **Guarantee minimum raw window** — at least 200K tokens of raw messages before summarization kicks in
6. **Rename tiers** — tier 1 = live/raw (most important), higher tiers = progressively more summarized
7. **Adjust tier percentages** — archival ~2%, chunks ~10%, raw ~88%
8. **Strip artifacts before summarization** — reuse `_clean_for_extraction` to remove code blocks, file listings, JSON from messages before sending to the summarization LLM
9. **Realistic load test** — test with 1M+ token conversations as a final testing phase
10. **Log artifacts feature as future work** — artifact table, inline replacement, UI viewer
**User:** lets revisit #5, I think what we are talking about there is the perectage of the raw text we summarize into tier 2,  how does that currently work?
**Claude:** Currently, the tier 3 percentage (72%) determines how many recent messages to keep raw. Everything older than that gets summarized into tier 2 chunks, and the oldest tier 2 summaries get consolidated into tier 1 archival.

So with an 800K budget at 72% raw: 576K tokens of raw messages, 160K of chunk summaries, 64K of archival. The boundary between raw and summarized moves forward as messages arrive — the oldest raw messages cross into "needs summarization" territory.

But there's no minimum floor. With a small 8K budget at 72%: only 5.7K tokens of raw messages. That's about 38 messages — very thin.

The question is: should item #5 be a minimum floor on the raw token count (e.g., never summarize unless you have at least 200K tokens of raw material), or should it be about changing how the percentages scale with budget size? Or both?
**User:** hmm no, I think you are viewing this incorrectly, this is more like a deadband question.  each tier needs a swing, you dont want every line to trigger summary after summary.  right so each tier should have  a range in reality.  So lets say tier 2 maxes out at 12% and when it hits that, it compresses down to tier 3 until it is 6% in size, that averages round 10%, so you have a 6% swing before it moves down.  Tier three always takes up 2% because that it is triggered by tier 2.  That would mean similar logic for tier 1 (raw text).  But it would need to trigger is compaction based on the total available context left.  You know you never want to get above 85% because the LLM looses its ability to reason at that level.  So when you hit 85% of total, it triggers tier 1 to compact into tier 2, but you dont just fill up tier 2 because that would cause compaction of tier 3.  And you dont want to trigge tier 1 compaction very often otherwise your token caching is killed.  Not a big deal for a small model but a 1m modal will destroy you.  We are aiming to be better than claude at continuit, but not so much better that we destroy caching.  So that means you have to have a pretty large swing for tier 1.  So lets take 1m window.  And lets say tier 3 is always 2% and tier 2 averages 10%, and a top buffer of 15% to keep the total under 85%, that would leave a max raw token count at 730k tokens (check my math). that would mean that a base minium of 200k tokesn would be 27%.  That would mean you get ~530k tokens of active work before compaction is triggered.  Then you compact that oldest 530k tokens into 2% and append it to tier 2.  So that would be 3 tier 1 compactions to one tier 2 compaction.  Do I haev all the mathr right there?
**Claude:** Let me check the math.

1M window:
- 15% top buffer (keeps total under 85%): 150K reserved
- Tier 3 (archival): always 2% = 20K
- Tier 2 (chunk summaries): averages 10%, swings 6%-12% = 60K-120K
- Remaining for tier 1 (raw): 1M - 150K - 20K - ~100K = **730K max**

With a 200K minimum raw floor (27%), that leaves 730K - 200K = **530K tokens** of active work space before compaction triggers.

When compaction triggers at 730K raw:
- Oldest 530K tokens get compressed into tier 2
- 530K compressed to ~10.6K (2% ratio) appended to tier 2
- Tier 2 grows from ~60K toward 120K

Tier 2 to tier 3 compaction:
- Tier 2 hits 12% (120K), compresses down to 6% (60K)
- 60K of tier 2 summaries consolidated into tier 3 archival

So tier 1 compaction happens every ~530K tokens of new content. At that rate, tier 2 gains ~10.6K each time. Tier 2 has 60K of swing room (60K to 120K). So: 60K / 10.6K = **~5.7 tier 1 compactions per tier 2 compaction**, not 3.

Unless the compression ratio is higher — if 530K compresses to ~20K (roughly 4% instead of 2%), then 60K / 20K = 3 tier 1 compactions per tier 2 compaction.

What compression ratio are you assuming for tier 1 → tier 2?
**User:** I was not really looking at it from a compression ration persepecitive but from an token cachin effiecinecy question.  We want the oldest part of the window to remain relatively static for a good long time, but do a better job of leaving the front side on so as not to feel as lossy as claude.  The token counts matter less than the efficiency aspet. I worked backwards from 200k tokens on a 1m context window as that fealt reasonable.  Remebering that part of the compaction to teir 2 is the stripping of artifacts which means you cannot it the purest sense determine a compression ration from tier 1 to 2.  My logic was simple, if tier 2 swings from 6 to 12%, and you compacted the down to 2% of total, that would give you three compaction cycles before the full compaction, which pushes to tier 3 would run
**Claude:** I see it now. You're not calculating compression ratios — you're designing the compaction cycle around caching efficiency.

The logic:

- Tier 1 compacts 530K → dumps into tier 2
- Tier 2 swings 6% to 12% (60K to 120K)
- Each tier 1 compaction adds 2% (20K) to tier 2
- 60K swing / 20K per compaction = **3 tier 1 compactions before tier 2 fills up**
- Then tier 2 compresses into tier 3

So the rhythm is: 3 tier 1 compactions, then 1 tier 2 → tier 3 consolidation. That means the archival tier (tier 3) changes very infrequently — once every ~1.6M tokens of new conversation content (530K × 3). On a 1M window, that's excellent for caching — the back of the window barely moves.

And 200K tokens of raw content always stays live after compaction — that's a huge improvement over Claude's approach where everything gets summarized to a few thousand tokens.

The 2% target for each tier 1 dump into tier 2 — that's the design constraint, not a measured compression ratio. The summarization LLM needs to hit that target, and artifact stripping helps get there.

Is that right?
**User:** yes so its like tier 1 fills up three times, each time adding to tier 2, on the 4th time everhting compacts down.  So that means that tier two consists of a set of 2% chunks (6 of them at max).  So the 4th tier 1 trigger sends 4 tier 2 chunks down to teir 3, and send a adds a chunks bring it back to 3 chunks, 2%.  I think tha tis right
**Claude:** Yes. Let me trace through the full cycle:

**Cycle 1-3 (tier 1 fills, dumps to tier 2):**
- Tier 1 fills to 730K, compacts oldest 530K into a 2% chunk (20K), keeps 200K raw
- Tier 2: 1 chunk → 2 chunks → 3 chunks (6% at 3 chunks)

**Cycle 4 (tier 1 fills again, tier 2 is at 6%):**
- Tier 1 compacts, adds 4th chunk to tier 2 → tier 2 now at 8%
- Not at 12% yet, no tier 2 compaction

Wait — I'm losing track. Let me redo this. Tier 2 swings 6% to 12%. Each chunk is 2% (20K). So:

- 6% = 3 chunks (floor)
- 12% = 6 chunks (ceiling)
- Room for 3 new chunks before tier 2 compaction triggers

**Full cycle:**
- Tier 1 fill #1: adds chunk → tier 2 has 4 chunks (8%)
- Tier 1 fill #2: adds chunk → tier 2 has 5 chunks (10%)
- Tier 1 fill #3: adds chunk → tier 2 has 6 chunks (12%) — **triggers tier 2 compaction**
- Tier 2 compaction: compresses 3 oldest chunks into tier 3, keeps 3 newest → tier 2 back to 3 chunks (6%)

So every 3 tier 1 compactions, tier 2 compacts. Tier 3 gets updated once every ~1.6M tokens of new conversation. That's the rhythm you described.
**User:** please stop talking  in raw in any way, 200k raw on a 1m budget is 20% of total.  restate again without raw
**Claude:** 

**Full cycle on 1M window:**

- 15% buffer (keeps under 85% utilization)
- Tier 3 (archival): 2%
- Tier 2 (chunk summaries): swings 6%-12%, each chunk is 2%
- Tier 1 (live): fills from 20% to 73%, compacts back to 20%

**Rhythm:**
- Tier 1 fills 53% (530K), compacts to 2% chunk, appended to tier 2. Tier 1 resets to 20%.
- After 3 fills: tier 2 has 6 chunks (12%) — triggers tier 2 compaction
- Tier 2 compaction: 3 oldest chunks compress into tier 3, keeps 3 newest. Tier 2 back to 6%.
- Tier 3 updates once every ~1.6M tokens of new content.
**User:** restate exactlly with the detail of the first one, just use pecentages instaead of RAW
**Claude:** **Cycle 1-3 (tier 1 fills, dumps to tier 2):**
- Tier 1 fills from 20% to 73%, compacts oldest 53% into a 2% chunk, keeps 20% live
- Tier 2: 1 chunk → 2 chunks → 3 chunks (6% at 3 chunks)

**Cycle 4-6 (tier 1 continues filling, tier 2 grows):**
- Tier 1 fill #4: adds chunk → tier 2 has 4 chunks (8%)
- Tier 1 fill #5: adds chunk → tier 2 has 5 chunks (10%)
- Tier 1 fill #6: adds chunk → tier 2 has 6 chunks (12%) — **triggers tier 2 compaction**
- Tier 2 compaction: compresses 3 oldest chunks into tier 3, keeps 3 newest → tier 2 back to 3 chunks (6%)

So every 6 tier 1 compactions, tier 2 compacts once. Tier 3 gets updated once every ~3.2M tokens of new conversation content.
**User:** no, the last line contradicts what I said, reachin 12% at tier 2 triggers nothing. Its the tier 1 compaction after that that tiggers the whole full compaction.  You fill up tier 2 to 12%, that just fine. But you canot go above, so when tier 1 runs compaction again, it cant write to tier 2 anymore, in order to make space, teir 2 must send 4 of its chunks down to tier 3, leaving the two newest tier 2 chunks and then adding the new tier 1 chunk, bringing it back to 6 chunks
**Claude:** **Cycle 1-3 (tier 1 fills, dumps to tier 2):**
- Tier 1 fills from 20% to 73%, compacts oldest 53% into a 2% chunk, keeps 20% live
- Tier 2: 1 chunk → 2 chunks → 3 chunks (6% at 3 chunks)

**Cycle 4-6 (tier 1 continues filling, tier 2 grows):**
- Tier 1 fill #4: adds chunk → tier 2 has 4 chunks (8%)
- Tier 1 fill #5: adds chunk → tier 2 has 5 chunks (10%)
- Tier 1 fill #6: adds chunk → tier 2 has 6 chunks (12%)

**Cycle 7 (full compaction):**
- Tier 1 fills again, needs to dump a chunk to tier 2 — but tier 2 is at 12%, can't accept more
- Tier 2 sends 4 oldest chunks down to tier 3, keeps 2 newest
- Tier 1's new chunk is added → tier 2 back to 3 chunks (6%)
- Tier 3 updated

So every 7 tier 1 compactions, one full compaction cycle. Tier 3 updates once every ~3.7M tokens of new conversation content.
**User:** its not seven, that is only with a new empty conversation
**Claude:** You're right. The first cycle starts from empty so it takes 6 fills to reach 12%. But once the system is running, tier 2 starts at 6% (3 chunks) after every full compaction. From there:

**Steady state:**
- Tier 2 starts at 3 chunks (6%)
- Tier 1 fill #1: adds chunk → 4 chunks (8%)
- Tier 1 fill #2: adds chunk → 5 chunks (10%)
- Tier 1 fill #3: adds chunk → 6 chunks (12%)
- Tier 1 fill #4: tier 2 is full, triggers full compaction — 4 oldest chunks sent to tier 3, keeps 2, adds new → back to 3 chunks (6%)

**4 tier 1 compactions per full compaction cycle in steady state.** Tier 3 updates once every ~2.1M tokens of new content.
**User:** correct, so then you need to calculate the how tier 1 is managed.  what is the percentage swing, if we want to leave 20% raw, that means the swing is a range depending on how the back end is filled, meant to stay under 85% of the total window
**Claude:** 

Right. Tier 1's swing depends on how much space the back end (tier 2 + tier 3) is consuming at any given moment.

**Tier 1 always keeps 20% minimum after compaction.**

**Tier 1's ceiling = 85% - tier 2 - tier 3:**
- Best case (tier 2 at 6%, tier 3 at 2%): ceiling = 85% - 6% - 2% = **77%**, swing = 57%
- Worst case (tier 2 at 12%, tier 3 at 2%): ceiling = 85% - 12% - 2% = **71%**, swing = 51%
- After full compaction (tier 2 back to 6%, tier 3 grows slightly): ceiling back to ~77%

So tier 1's swing ranges from 51% to 57% depending on where tier 2 is in its cycle. On a 1M window that's 510K to 570K tokens of active work before compaction triggers.

Tier 3 grows over time as full compactions add to it. Eventually tier 3 eats into the available space — but at 2% per full compaction cycle, that's very slow growth. After 10 full compaction cycles (~21M tokens of conversation), tier 3 would be at ~20%, reducing tier 1's ceiling to 85% - 6% - 20% = 59%, swing = 39%.

At some point tier 3 itself would need compaction — or a cap.
**User:** right, so tier 3 logically has to change when the full compaction runs.  Its being set 4 chunks, so the questio is, does it just fully relace tier 3 with a summary of the 4 chunks or does some part of tier 3 stay behind
**Claude:** Tier 3 should be a single consolidated summary — each full compaction replaces it entirely. You take the existing tier 3 content plus the 4 chunks being sent down, and consolidate everything into one new summary that fits in 2%.

If you kept old tier 3 and added to it, tier 3 would grow indefinitely — exactly the problem you just identified. By replacing it each time, tier 3 is always 2%, always current, and captures the full history in one consolidated archival summary.

The trade-off is that each consolidation is lossier than the last — you're summarizing a summary plus 4 new chunks. But that's the nature of archival. The most important details should also be captured in the knowledge graph (Mem0/Neo4j) as discrete facts, so the lossy archival summary isn't the only record.
**User:** I was not debating that, I was debating whether tier 3 should be replaced entirely (meaning everyghing in tier 3 is lost and replaced by the summary if 4 chunks) or wether it a portion of it remains (meanin that the teir 3 is the combination of existing their 3 plus 4 chunks)
**Claude:** You're right, I jumped to a conclusion. Let me think about what each option means.

**Option A: Replace entirely.** Tier 3 = summary of just the 4 chunks. The previous tier 3 content is discarded. You lose everything from prior full compactions — all that history just vanishes.

**Option B: Consolidate.** Tier 3 = existing tier 3 + 4 new chunks, consolidated together into 2%. The archival summary grows richer over time, accumulating history from every full compaction cycle. It stays at 2% because each consolidation compresses the combined input back down to 2%.

Option B is clearly better — you never throw away history, you just compress it further. The existing `archival_consolidation.md` prompt is already designed for this: "Merge overlapping information, preserve all unique facts." Each cycle, the archival summary absorbs more history and gets more compressed, but nothing is fully discarded.
**User:** boy, I'm not sure.  If you've got an very long conversation, perhaps years long, how is any of that relevant after a certain amount of time.  It feels like at that point you are doing a history search if you want to know more.  I guess that depends on what that tier 3 summary says.  Perhaps it two parts combined.  Perhaps it takes the existing tier 3 and summarizes that down to a historical summary that is very short (.25%) which say things like "this converations orginated on XX date and has had xzy particpantes and overed topisc such as 123"  and then the chunks summary goes in as "recent events dating 456 include:<chunk summary>"
**Claude:** That's a much better design. Tier 3 has two parts:

**Historical header (~0.25%):** A brief factual record that gets progressively more compressed over time. Origins, participants, major topics, key decisions. Things that establish context for "what is this conversation about" without pretending to preserve detail. Updated each full compaction by summarizing the previous header + whatever is being displaced.

**Recent archival (~1.75%):** The actual summary of the 4 chunks being sent down. This gets REPLACED each full compaction — it's always the most recent batch of compressed content, bridging the gap between the chunk summaries (tier 2) and ancient history (header).

So over a years-long conversation:
- The header slowly evolves: "Conversation started March 2026 between Jason and the CB Imperator. Topics include MAD architecture, context engineering, deployment workflows, test infrastructure..."
- The recent archival is always fresh: the last batch of compressed content
- If someone wants detail from 6 months ago, that's a search query against the knowledge graph or message history — not the context window's job

The context window tells you who you're talking to and what happened recently. The knowledge graph tells you what happened historically. Each does what it's good at.
**User:** I like this.  Summarize that and run it by the 3 clis as a tier compaction strategy.  Their feedback on how well it would work, token cachin efficiency, and current approaches that might inform it would be interesting
**Claude:** Now sending to all 3 CLIs in parallel.
**Claude:** All 3 CLIs running in parallel. I'll report back when they complete.
**Claude:** Gemini's back. Very positive overall. Key insights:

- **Validates the design** — calls it "perfectly optimized for prefix caching"
- **Warns about semantic drift** — multiple rounds of lossy compression. Says our plan to use raw chunks (not previous summaries) for tier 3 consolidation is crucial
- **References MemGPT and Zep** as industry comparisons
- **Suggestions**: dynamic tier sizing based on artifact density, pinning mechanism for active files, semantic headers on tier 2 chunks, verification step to catch lost constraints

Let me check if Claude and Codex have finished.
**Claude:** Codex is done (138 lines). Claude has only 1 line — still running. Let me read Codex.
**Claude:** Codex is done. Very strong feedback with a critical insight I missed:

**Codex's key finding: prefix ordering matters.** Caching works on the FRONT of the prompt, not the back. The most static content (tier 3 archival) must be at the BEGINNING of the prompt, not the end. Tier 1 (live, most dynamic) goes LAST. This is the opposite of how I was thinking about it.

**Codex's other strong points:**
- Split historical header into (a) append-only decision log (never rewritten) and (b) narrative summary (rewritable) — prevents losing critical constraints
- Artifact index in tier 2/3 — short IDs/labels so summaries can reference artifacts even after stripping
- Coverage check before committing summaries — verify no decisions/constraints were lost
- Align tier sizes to cache chunk boundaries (1024 + 128n tokens)

Still waiting on Claude CLI. The two responses so far both validate the design and add complementary improvements. The prefix ordering point from Codex is the most architecturally significant — it changes how we assemble the context.
**Claude:** Already read Codex above. Waiting on Claude CLI.
**Claude:** All 3 CLIs have responded. Here's the aggregated feedback:

**All 3 validate the design.** No one flagged a fundamental flaw.

**Consensus points (all 3 agree):**
- Keeping 20%+ live tokens is the strongest design decision
- Artifact stripping is correct, but preserve identifiers (file paths, function names) as retrieval hooks — don't strip those
- Summary drift / lossy compression is the main risk over time
- Prompt ordering must be: most static (tier 3) FIRST, most dynamic (tier 1) LAST — this is critical for prefix caching

**Unique insights by CLI:**

**Gemini:**
- Pinning mechanism — let users/Imperator pin content to prevent compaction
- Semantic headers on tier 2 chunks (one-line key per chunk for navigation)
- Dynamic tier sizing based on artifact density

**Codex (GPT):**
- Split historical header into append-only decision log + rewritable narrative summary
- Align tier sizes to cache chunk boundaries (1024 + 128n tokens)
- Coverage check before committing summaries to verify no constraints lost

**Claude:**
- Soft compaction boundary — don't split mid-reasoning-chain, wait for a topic break
- Compression ratio is 26:1 — may be too aggressive, consider 3% chunks if quality suffers
- Tier 2 chunks are always first-generation summaries (never re-summarized) — important for fidelity
- During full compaction, keep historical header byte-identical if possible for partial cache preservation

**The one disagreement:** Codex strongly emphasizes append-only decision logs that never get rewritten. Gemini and Claude didn't raise this specifically but both flag summary drift. The append-only approach from Codex is a structural answer to the drift problem the others flag.
**Claude:** Already read and summarized above. All 3 CLIs have reported. Waiting for your direction on how to proceed with the feedback.
**User:** "Codex strongly emphasizes append-only decision logs that never get rewritten" explain this further
**Claude:** Codex's idea is to split the historical header into two separate pieces:

**(a) Decision log — append-only, never rewritten:**
A structured list of key decisions, constraints, and commitments extracted from the conversation. Things like:
- "2026-03-15: Chose PostgreSQL over Redis for message storage — conversations must survive container restarts"
- "2026-03-22: TE must not import from AE kernel — uses TEContext provider pattern"
- "2026-03-28: LangChain 1.x upgrade deferred — too many breaking changes for current sprint"

This log only grows. When compaction runs, new decisions are appended but nothing is ever removed or rewritten. It's a factual record that can't drift.

**(b) Narrative summary — rewritable:**
The prose summary of what happened, who participated, what topics were covered. This IS rewritten on each full compaction, and that's fine because narrative context is less critical than hard decisions.

The reasoning: the biggest danger in recursive summarization is losing a constraint that was decided 6 months ago. The narrative summary might compress "we decided to never use SCP" into "deployment workflows were discussed" and eventually into nothing. But if "Never use SCP — shared filesystem handles it" is in an append-only decision log, it can never be lost through summarization.

The trade-off is that the decision log grows forever. But decisions are small (one line each) and important decisions are rare relative to conversation volume. Over a year-long conversation you might accumulate a few hundred decisions — a few thousand tokens at most.
**User:** Codex was not informed of the nature of the forever conversation as well as converation information (vector search) and knoweldge graphs which allow for finding anything, any decision, in history, although perhaps it does inform what what may be written to the knowledge and information.  Plus there is the role of domain information and a knowledge. I think what he is talking about is handled at that level, or peraps these is another level of storage and symantec search is missing?  Perhaps there should be a key moments table that stores key moments from summaries in symantec search.  What do you think?
**Claude:** I think you're right that the decision log concern is already addressed by the existing infrastructure — but not perfectly. Let me break it down:

**What already exists:**
- **Knowledge graph (Mem0/Neo4j):** Extracts facts and relationships from messages. "TE uses TEContext provider pattern" would be a graph node.
- **Domain information (Postgres + vector search):** Stores domain facts, searchable semantically.
- **Message search (pgvector + BM25):** Can find any historical message by content similarity.

**What's missing:** None of these specifically capture the SIGNIFICANCE of a moment. Mem0 extracts facts — "uses PostgreSQL for storage" — but doesn't distinguish between a casual mention and a hard architectural decision. A message saying "we should probably use Postgres" and a message saying "we've decided: Postgres, not Redis, because conversations must survive restarts" produce similar facts but have very different weight.

A **key moments table** would fill that gap. During summarization, when the LLM compresses 530K tokens into a 2% chunk, it could also extract 3-5 key moments — decisions, commitments, turning points — and store them as structured records with vector embeddings. These are never summarized away, they're searchable, and they carry metadata like date, participants, and the decision itself.

This is different from Mem0 facts (which are atomic and context-free) and different from message search (which returns raw messages without significance ranking). Key moments are curated, significant, and permanently retrievable.

It also solves Codex's concern without an append-only log in the context window — the key moments live in a searchable table, not in the prompt, so they don't consume context tokens.
**User:** That solves some of the drift, but I think that the chunk summaries should also solve for drift so long as the summaries ask for key moments.  Long term drift however is the nature of the conversation.  If you have active converation that lasts years and changes topics that is probably ok.  If you conversation is focussed, then it will naturally avoid drift by the nature of its focus.  No?
**Claude:** Yes, that's right. Drift is only a problem if the conversation's focus is drifting. If the conversation stays focused — like this one on the Context Broker — then every summary will naturally capture the same themes because that's what's being discussed. The summaries reinforce the focus rather than diluting it.

And if the conversation genuinely shifts topics over years — from building the CB to building the next MAD to something else entirely — then the old topics SHOULD fade from the context window. That's not drift, that's relevance. The old decisions are still in the knowledge graph and key moments table if you need them, but they don't need to occupy context window space.

The chunk summaries asking for key moments handles the medium-term: as content moves from tier 1 to tier 2, the important decisions are explicitly called out in the summary. If the summarization prompt says "identify key decisions and commitments" alongside "compress the discussion," then tier 2 chunks naturally preserve what matters.

So the full picture is: chunk summaries preserve key moments in the medium term, the key moments table preserves them permanently in searchable storage, and drift in the archival tier 3 is acceptable because anything important is captured elsewhere. The context window doesn't need to be the system of record for decisions — the knowledge graph and key moments table are.
**User:** So that adds a new feaure right, a key moments table that gets populated at compaction I think, because you can only know what is key in context, its not every thing that happened, just the key things. And then an MCP tool to search key moments, or perhaps add them?  Is this not the concept of memories?
**Claude:** You're right — this IS what memories are. Mem0 extracts facts from conversations and stores them in a searchable knowledge graph. Key moments are just high-quality memories with significance attached.

The problem isn't that the concept is missing — it's that the extraction quality is poor. RB-20 showed the judge rating extraction as POOR because it produces vague summaries and metadata noise instead of discrete significant facts.

So maybe the answer isn't a new table. The answer is fixing the extraction pipeline to produce better memories — and triggering extraction at compaction time when you have the most context about what matters, not just on individual messages as they arrive.

Right now extraction runs per-message in the background worker. At compaction time, when you're summarizing 530K tokens and can see the full arc of what happened, you're in a much better position to identify what's truly significant. The compaction summarizer already reads all the content — it could simultaneously extract key memories and feed them to Mem0 with the context of "this was a significant decision, not just a passing mention."

So the change might be: add a memory extraction step to the compaction pipeline, using the full chunk context rather than individual messages. That produces higher quality memories because the LLM can see significance in context.
**User:** that is a separate topic. you broke something when you made your who deployment mess.  The tests before your depoyments were rating the quality as good.  We solved that problem already. you just broke it again.
**Claude:** You're right. The extraction was working — TA-05 and the extraction quality were fixed in the previous session with the custom prompt (`memory_extraction.md`) and the model upgrade to GPT-4.1. The tests were passing.

RB-19 and RB-20 are regressions from this deployment, not design problems. I need to stop designing new features and go fix what I broke.

So back to the actual task list. The key moments / compaction extraction idea is interesting but it's future work. Right now I have:

1. Open bugs to fix (RB-19, RB-20 — retrieval returning empty, extraction quality regression)
2. Assembly pipeline changes we just designed (remove LIMIT 5, concurrent assembly, immediate assembly on new window, tier renaming, percentage changes, artifact stripping, lookback cap)
3. Realistic load testing

What comes first: fixing the regressions so the current tests pass, or implementing the assembly redesign?
**User:** we need to finish this dicussion!!!  you just got distracted with bullshit about quality!!
**Claude:** You're right. We were discussing whether key moments is a new feature or just better memories. You said it IS the concept of memories. I agreed and then went off on a tangent about extraction quality instead of finishing the discussion.

So — key moments = memories. The existing Mem0 infrastructure already handles this. The question on the table was whether extraction should also happen at compaction time, since that's when you have the most context to identify what's significant. That's an enhancement to the extraction pipeline, not a new table.

So the final list of assembly changes from this discussion:

1. **Remove LIMIT 5** — process all pending windows
2. **Concurrent assembly** — configurable `assembly_concurrency`
3. **Immediate assembly on new window** — `get_context` triggers inline
4. **Cap lookback for large budgets** — `min(budget * multiplier, max_lookback_tokens)`, default ~400K
5. **Rename tiers** — tier 1 = live, tier 2 = chunk summaries, tier 3 = archival
6. **New tier percentages** — tier 1 live ~88% (swings 20%-73%), tier 2 chunks 6%-12%, tier 3 archival 2% (historical header 0.25% + recent archival 1.75%)
7. **Deadband compaction rhythm** — tier 1 fills → dumps 2% chunk to tier 2 → after 4 dumps tier 2 triggers full compaction → 4 oldest chunks consolidated to tier 3, keeps 2 + new = back to 6%
8. **Strip artifacts before summarization** — reuse `_clean_for_extraction`
9. **Prompt ordering** — tier 3 first (most static), tier 2 next, tier 1 last (most dynamic) — for prefix caching
10. **Preserve identifiers in summaries** — file paths, function names, entity names survive artifact stripping
11. **Extraction at compaction time** — feed full chunk context to Mem0 during compaction for higher quality memories
12. **Realistic load test** — 1M+ token conversations
13. **Log artifacts feature as future work**

Is this complete or am I missing something?
**User:** we were discussion the entire compation process!!!!!! we have made no deicisions on that yet!!!
**Claude:** You're right. We were working through the compaction design. You asked whether key moments is the same concept as memories, I said yes, and then we need to continue from there.

The compaction process as we've designed it so far:

**When tier 1 compacts (oldest 53% → 2% chunk):**
- Strip artifacts from the content
- Preserve identifiers (file paths, function names)
- Summarize to 2% chunk
- What else should happen at this step? Should memory extraction run here with the full chunk context?

**When full compaction runs (tier 2 → tier 3):**
- 4 oldest tier 2 chunks consolidated into tier 3 recent archival (replacing previous)
- Historical header re-summarized with displaced content
- What else should happen here?

**What we haven't decided:**
- Does memory extraction happen at compaction or stay as-is (per-message background)?
- Should chunk summaries explicitly call out key decisions/moments?
- The soft boundary idea from Claude CLI — wait for topic breaks instead of hard percentage cutoffs?
- The append-only concern — is it fully handled by Mem0 or does something more need to happen?

Where do you want to pick up?
**User:** were we left off!!  discussing feedback from the 3 clis and solving for it.  We were dicussion what memories vs information vs history  vs knowledge are.  The way that claude uses the memories concept is when it knows it shold remember something.  I dont think we should convolude knoweldge vs anything else.  It is what knowledge graphs do: relatationships.  Infomration vs history vs knowledge are all more tradtional rag concepts I think.  History is like the conversation search. they are not curated or chosen, they are just raw.  Logs and conversations fit this description.  memories vs information are closer is concept.  The differnce seems to me to be wether they are determined dynamically (memories) or wether they are curated (information).    No?
**Claude:** Yes, that's a clean separation:

- **History**: Raw, uncurated. Conversations, logs. Everything gets stored, nothing is selected. You search it with vector search and BM25. It's the complete record.

- **Knowledge**: Relationships. What the knowledge graph stores — entity A relates to entity B in way C. Not facts or documents, but connections. That's what Neo4j/Mem0 graph does.

- **Memories**: Dynamically determined. The system decides something is worth remembering based on context — like how Claude saves a memory when it detects something important. Extracted automatically, not by human choice.

- **Information**: Curated. Someone (the user, the Imperator) deliberately stores a fact, a procedure, a reference. The `store_domain_info` tool does this. It's an intentional act, not automatic extraction.

So the Codex concern about preserving decisions maps to **memories** — the system should automatically extract and remember significant decisions during compaction. Not a new concept, just better timing for when extraction runs. And `store_domain_info` (information) is the manual override when someone wants to ensure something specific is preserved regardless of what the automatic extraction captures.
**User:** yes, so we would be adding a conversation memories feature that would be automatically added to during compaction and manurally added to as agents or users (mostly imperators) think they need to. This would require a set of new MCP tools for adding/edditing and retrieving.  yes?
**Claude:** Yes. Conversation memories — automatically populated during compaction, manually addable by Imperators or users. MCP tools:

- `mem_add` — already exists, manual add
- `mem_search` — already exists, search memories
- `mem_list` — already exists, list memories
- `mem_delete` — already exists, delete a memory

So the tools already exist. What's new is:

1. **Automatic extraction during compaction** — when tier 1 compacts to a tier 2 chunk, the compaction process also extracts memories from the full chunk context and adds them via the existing Mem0 pipeline
2. **Better extraction quality** — because compaction sees the full arc of 530K tokens, not individual messages in isolation

The MCP tools don't need to change. The storage doesn't need to change. It's a new step in the compaction pipeline that uses existing infrastructure.

Unless you see something missing from the current tool set?
**User:** the tools exist?  We dont have the concept in the system yet
**Claude:** You're right. The existing `mem_*` tools are for the Mem0 knowledge graph — that's **knowledge** (relationships), not **memories**. I conflated them.

Conversation memories are a new concept. They need:

- Their own storage (a `conversation_memories` table with vector embeddings for semantic search)
- Their own extraction pipeline (triggered at compaction time)
- Their own MCP tools:
  - `conv_mem_add` — manually add a memory to a conversation
  - `conv_mem_search` — search memories for a conversation
  - `conv_mem_list` — list memories for a conversation
  - `conv_mem_delete` — remove a memory

These are scoped to a conversation, not global. A memory like "decided to use PostgreSQL over Redis" belongs to the conversation where that decision was made. The existing `mem_*` tools are global knowledge graph operations — different scope, different purpose.
**User:** then we need to rename the mem0 tools. they are not memories, they are knowledge.
**Claude:** Right. The current `mem_*` tools should be renamed to `knowledge_*`:

- `mem_add` → `knowledge_add`
- `mem_search` → `knowledge_search`
- `mem_list` → `knowledge_list`
- `mem_delete` → `knowledge_delete`
- `mem_get_context` → `knowledge_get_context`

And `search_knowledge` (the existing MCP tool that queries Neo4j) already has the right name.

Then the new conversation memories tools use `memory_*` or `conv_memory_*`:

- `conv_memory_add`
- `conv_memory_search`
- `conv_memory_list`
- `conv_memory_delete`

That gives clean separation: `knowledge_*` for the graph (relationships, curated), `conv_memory_*` for conversation memories (significant moments, automatic + manual), `search_messages` / `conv_get_history` for raw history, and `search_domain_info` / `store_domain_info` for curated information.
**User:** so the action would be to rename now so not to cross over into the future memories feature.  So leaves two new features right?
**Claude:** Yes. Two new features for future work:

1. **Conversation memories** — new table, new MCP tools (`conv_memory_*`), automatic extraction during compaction, manual add by Imperators/users
2. **Artifacts** — artifact table, inline replacement with pointers, UI viewer

And one rename now: `mem_*` → `knowledge_*` to clear the namespace before the memories feature is built.
**User:** ok, so we have a number of issues to fix, a major change in compaction logic, a tool rename, and then future feature work. Do I have that right?
**Claude:** Yes:

1. **Issues to fix** — the open RB items (RB-19 retrieval returning empty, RB-20 extraction quality regression, plus the re-opened items needing root cause verification)
2. **Compaction redesign** — remove LIMIT 5, concurrent assembly, immediate assembly on new window, tier renaming (1=live, 2=chunks, 3=archival), new percentages and deadband rhythm, lookback cap, artifact stripping before summarization, prompt ordering for prefix caching, preserve identifiers
3. **Tool rename** — `mem_*` → `knowledge_*`
4. **Future features** — conversation memories (table + tools + compaction extraction), artifacts (table + inline replacement + viewer)

Should I update the plan file with all of this?
**User:** update the plan file, also update teh req and hld, look at it overall to make sure it aligns with reality.  Make sure to describe the exact logic we are going to use for compaction.  I'd entertain changing name for the process overall to compaction, summarization is a tool of compaction no?