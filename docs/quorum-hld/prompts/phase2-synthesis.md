Please read these files:

- anchor_context/REQ-context-broker.md
- anchor_context/c1-the-context-broker.md
- anchor_context/HLD-context-broker-gemini.md
- anchor_context/HLD-context-broker-claude.md
- anchor_context/HLD-context-broker-codex.md

# Task: Synthesize Context Broker HLD

## Context

Three independent models (Gemini, Claude, Codex) have each produced a High-Level Design for the same system — the standalone Context Broker — from the same requirements spec and concept paper. You are the Lead. Your job is to synthesize these three drafts into a single, authoritative HLD.

The REQ and c1 are the source of truth. The three HLD drafts are inputs to your synthesis — they inform but do not bind. Where the drafts agree, that convergence is a strong signal. Where they diverge, use your judgment to pick the better architectural decision, informed by the REQ and c1.

## What an HLD Is

An HLD describes the architecture at a level above implementation detail. It covers:

- What the major components are and how they relate
- Key architectural decisions and their rationale
- Data flow between components
- Technology choices and why
- Interface contracts at a high level
- Deployment model
- Assumptions and constraints

An HLD does **not** include: detailed class designs, function signatures, full database DDL, node-by-node implementation specifics, error handling logic, or code examples. That level of detail is Low-Level Design (LLD) territory — and in this system, the code itself serves as the LLD. The StateGraph structure is self-documenting; an LLM implementing from this HLD will go directly to code.

If any of the three drafts went too deep into implementation specifics, pull it back to the architectural level. If any draft stayed too shallow and missed important architectural decisions, fill the gap.

## Synthesis Guidelines

1. **Convergence is signal.** When all three drafts make the same architectural choice, keep it. That's independent validation.

2. **Divergence requires judgment.** When drafts disagree on a technology choice, pattern, or approach — evaluate each against the REQ and c1, pick the one that best serves the requirements, and briefly note why.

3. **Preserve the best ideas from each.** One draft may have a better architecture diagram. Another may have identified an important design decision the others missed. A third may have the clearest description of data flow. Take the best of each.

4. **Stay at HLD level.** The output should give an implementer enough architectural direction to start building. It should not prescribe implementation details they can figure out themselves.

5. **No ecosystem references.** The output must be fully self-contained — no references to Joshua26, Sutherland, Alexandria, Henson, joshua-net, registry.yml, or any ecosystem-specific service.

## Output Format

Produce a single Markdown document. Save it to `outputs/HLD-context-broker-synthesized.md`.

The document should cover (at minimum):

1. System overview — what the Context Broker is, what problem it solves
2. Container architecture — components, roles, network topology, volumes
3. Data flow — how a message moves through the system end-to-end
4. StateGraph architecture — the major flows, their purpose, how they relate, key technology choices (LangChain/LangGraph components)
5. Storage design — PostgreSQL, Neo4j, Redis roles and data model at a high level
6. Interface design — MCP tools, OpenAI-compatible chat, health endpoint
7. Configuration system — how config.yml drives the system
8. Build types and retrieval — how different assembly strategies work
9. Async processing model — queue architecture, eventual consistency
10. Imperator — the built-in conversational agent, its role as reference consumer

Use clear section headers and architecture diagrams (ASCII or Mermaid) where they clarify the design.
