# Concept: The Context Broker

**Status:** Concept
**Date:** 2026-03-06
**Maturity:** Partially Implemented

---

## Author

**J. Morrissette**

---

## What This Is

The Context Broker is the service that makes the infinite conversation practically usable. **Context engineering** is the discipline of assembling the right information for an agent to reason well — selecting what from the accumulated history matters for this task, this agent, this moment. The Context Broker is the tool that performs that assembly, proactively and at scale.

The two terms are related but distinct: context engineering is the what (the practice of assembling purposeful context); the Context Broker is the how (the service that does it). This paper covers both.

---

## The Infinite Conversation

Every agent in the ecosystem participates in conversations. Those conversations are stored completely — every message, every turn, every participant, indefinitely. There is no expiry, no session boundary, no loss of history. The conversation accumulates without limit.

This infinite conversation is the primary substrate for memory and cognition in the ecosystem. It is not a log or an audit trail. It is the medium through which agents think, accumulate knowledge, build relationships with collaborators, and carry context across time. A conversation begun months ago carries the full weight of everything decided, explored, and learned within it. That weight is available to any reasoning step that needs it.

The challenge this creates is not storage — storage is solved. The challenge is that agents reason within finite context windows. No model holds an unbounded conversation. The infinite conversation must be made usable within real constraints, differently for each agent and each purpose.

---

## Context as a Curated View

A context window is not the conversation truncated to fit. It is a **purpose-built view of the conversation**, constructed according to a specific strategy for a specific participant with a specific token budget.

The same conversation can produce radically different context windows depending on who is asking and what they need. Two agents in the same conversation may receive entirely different context — not because they have access to different information, but because what each agent needs from that information is different. A reasoning agent working through a complex architectural problem needs different cognitive fuel than a persona agent maintaining continuity with a user.

This is the core function of the Context Broker: to engineer the right informational environment for each agent at each reasoning step, assembled proactively so it is ready when the agent reaches for it.

---

## The Assembly Strategy

Every context window is built according to a **build type** — a named strategy that defines how the conversation history is processed, filtered, weighted, and assembled into the context delivered to the agent. The build type is the recipe. The conversation is the raw material. The assembled context is what gets served.

Build types are not a fixed taxonomy. They are open-ended. Any agent that needs a particular view into the conversation can have a build type designed for it. The number of possible strategies is unlimited. A few illustrative examples of what a build type can prescribe:

**Three-tier progressive compression** — the standard strategy for general-purpose context. The conversation history is organised into three layers: a stable archival summary covering the full history, rolling chunk summaries of messages that have aged out of verbatim, and the most recent messages in full. Older content is progressively compressed; recent content is preserved verbatim. The proportions scale with window size — larger windows hold more verbatim history. This is the default strategy for most agents.

**Sliding window** — no summarization. The most recent N tokens of conversation, verbatim. Appropriate when only the immediate exchange matters and historical depth is not required.

**Knowledge-dominant** — the context is composed primarily of extracted knowledge graph facts and entity relationships, with minimal raw conversation. Appropriate when an agent needs to reason about accumulated facts, preferences, or relationships rather than the conversational record itself.

**Document injection** — bypasses conversation summarization entirely. Injects a predefined bundle of reference material — architectural specifications, policy documents, standards — followed by recent conversation turns. Appropriate when the agent needs authoritative reference context more than conversational history.

**Domain-specific hybrids** — any combination of the above, weighted and proportioned for a specific agent's reasoning requirements. An agent managing personal relationships needs different depth of preference memory and emotional context than one performing infrastructure analysis.

---

## Per-Participant Windows

Within a single conversation, different participants may have different context windows. Each LLM participant creates its own context window with its own build type and its own token budget. Two models reasoning about the same conversation can be receiving entirely different assembled views of it — one with deep summarization history, one with heavy knowledge graph injection — because they need different fuel for different reasoning tasks.

This means context is not a property of the conversation. It is a property of the participant-conversation relationship, configurable per participant, per purpose, per token constraint.

---

## Two Layers of Memory

The Context Broker manages two distinct memory layers, which can be combined in any proportion by a build type:

**Episodic memory** — the conversation record itself. What was said, by whom, in what order. Stored as messages, compressed progressively into chunk summaries and archival summaries as conversations grow. The three-tier strategy is the primary mechanism for making episodic memory usable within finite windows.

**Semantic memory** — extracted knowledge: facts, entity relationships, preferences, decisions. Extracted from conversations by a background process and stored in a knowledge graph. Queryable by meaning and by graph traversal. Persistent independently of raw conversation. This layer answers "what do I know about X?" rather than "what was said about X?"

A build type can draw on either layer, both, or weight them in any proportion. An agent that benefits from rich factual memory but does not need deep conversational history can be given a context dominated by semantic retrieval. An agent that needs the conversational thread verbatim can receive it without knowledge graph injection.

---

## Proactive Assembly

Context is not assembled on demand. It is assembled proactively — triggered in the background after each message is stored, during the idle time between reasoning steps. When an agent calls for context, the assembled view is already waiting. The agent does not wait for summarization. Summarization has already happened.

This makes context retrieval fast and deterministic from the agent's perspective. The complexity of progressive summarization, chunk consolidation, archival compression, and knowledge graph retrieval is entirely hidden. The agent asks for context and receives it.

---

## Purpose-Driven Context Design

Context is not generic. It is designed for a specific agent serving a specific purpose. The build type is the formalisation of that design — it encodes what kind of context best serves what the agent is trying to do.

An agent whose purpose is to be the voice of a system needs raw architectural documents injected at the start of every conversation. An agent whose purpose is personal continuity with a user needs deep personal memory, emotional anchors, and accumulated facts about that person weighted heavily over recent conversational history. An agent whose purpose is engineering work needs a different balance entirely — prior architectural decisions, code patterns, past failures, project history.

Without knowing an agent's purpose, a context strategy cannot be designed. The build type answers the question: given what this agent is supposed to achieve, what does it need to know to reason well right now? That question has no answer without a purpose.

The same applies to memory design. What an engineering agent extracts from its conversations and stores in its knowledge graph — architectural decisions, what worked, what failed, precedents — is fundamentally different from what a security analyst would extract. The extraction priorities, the schema of structured facts, what gets retained and what is discarded — all are shaped by what the agent is trying to do. A memory system designed without a clear purpose will accumulate indiscriminately, and indiscriminate accumulation produces noise, not expertise.

This connection between purpose and context runs through both layers of memory:

**Episodic memory** (conversation history) — what portions of the history are most relevant to this agent's reasoning depends on what the agent is trying to accomplish. The build type encodes this weighting.

**Semantic memory** (knowledge graph) — what facts and relationships are worth extracting and retaining depends entirely on what the agent needs to know to serve its purpose well. The extraction configuration is a purpose specification.

Neither layer can be designed well without first knowing what the agent is for.

---

## What This Enables

The Context Broker is what makes infinite conversation practically usable. Without it, agents either receive a truncated recent history (losing the depth of accumulated work) or are overwhelmed by undifferentiated volume (losing the signal in the noise). With it, every agent receives exactly the cognitive fuel its reasoning requires — compressed where compression serves, verbatim where fidelity matters, knowledge-rich where facts are what the agent needs — all within the token budget available.

Conversation becomes the vehicle for long-term memory, deep context, accumulated expertise, and persistent relationships — not despite agents having finite context windows, but in full awareness of that constraint, engineered around it. And because that engineering is anchored to purpose, the context that accumulates over time is not just more — it is better. More precisely targeted. More useful. More capable of supporting the agent in doing what it is designed to do.

---

## Known Failure Modes

**Memory contamination.** The infinite conversation accumulates everything. If an agent produces consistently bad reasoning over a period — due to a misconfigured system prompt, a bad tool, or simply an edge case it handles poorly — that reasoning enters the conversation and becomes available as context for future invocations. Bad context produces bad reasoning, which produces more bad context. The contamination is self-reinforcing. Mitigation requires observation of output quality and mechanisms to identify and quarantine degraded periods of the conversational record.

**Progressive summarization decay.** The three-tier compression strategy compresses older content into summaries, and those summaries into archival summaries. Each compression step loses fidelity — detail that was present in the original exchange may not survive into the archival summary. Over very long timescales, the archival record converges toward high-level generalities, losing the specific decisions, constraints, and context that mattered at the time. This is a known limitation of summarization-based memory. Mitigation: explicit preservation of high-value decisions and artifacts through structured knowledge graph extraction, rather than relying solely on summarization.

**Scale and assembly cost.** The proactive assembly model assembles context after every message. As conversation volume grows — many agents, many concurrent conversations — the assembly workload grows proportionally. At sufficient scale, context assembly becomes a compute bottleneck. This is an engineering challenge that will require attention as the ecosystem matures; it is not a solved problem.

**Error propagation.** The Context Broker feeds agents; agents produce conversation; conversation feeds the Context Broker. If context assembly produces a systematically misleading view of the conversation — over-emphasising certain periods, under-emphasising others — the agents reasoning from that context will make systematically skewed decisions. This is not a theoretical risk; it is an inherent property of any summarization-based memory system. The mitigation is careful build type design and ongoing evaluation of context quality against agent output quality.

---

## Relationship to Other Concepts

- **MAD Pattern** (`a5-the-mad-pattern.md`) — conversation is the medium through which MADs think and accumulate domain knowledge; the Context Broker is what makes that accumulation usable at reasoning time
- **Agent Purpose and Identity** (`a4-agent-purpose-and-identity.md`) — purpose is the foundation of context design; without a clear purpose, neither the assembly strategy nor the memory schema can be designed well
- **System 3 ReAct** (`d1-system-3-react.md`) — the Conversation Engine at both ends of the reasoning loop depends on the Context Broker to supply the assembled context that makes the Imperator's reasoning grounded and continuous
- **The Conversation Broker** (`c3-the-conversation-broker.md`) — agent-to-agent messages flow through the same conversation substrate and are available to context assembly like any other conversation content
