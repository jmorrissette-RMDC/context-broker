Please read these files:

- anchor_context/REQ-context-broker.md
- anchor_context/c1-the-context-broker.md
- anchor_context/rogers-code.md
- anchor_context/d4-agent-optimal-code-architecture.md
- anchor_context/d5-state-4-mad.md

# Task: Context Broker HLD

## Context

You are producing the High-Level Design (HLD) for the **Context Broker** — a standalone context engineering and conversational memory service.

The Context Broker is a **State 4 refactor** of an existing, working system called Rogers, which currently operates as a State 2 MAD within the Joshua26 agentic ecosystem. The goal is to produce an HLD for a new, standalone version that:

- Implements the same domain capabilities as the current Rogers (context assembly, knowledge extraction, semantic search, conversational memory)
- Replaces ecosystem-specific dependencies with configurable alternatives (see REQ)
- Uses **idiomatic LangChain/LangGraph patterns** where the current Rogers uses hand-rolled infrastructure
- Is **self-contained** — no references to Joshua26, Sutherland, Alexandria, joshua-net, or other ecosystem services in the output
- Can be deployed standalone on any Docker-capable host via `docker compose up`

**This is not a documentation exercise for the existing code.** The existing Rogers code is included so you understand what the system does in practice — the domain behavior, the data flows, the schema. The HLD you produce is for the **new implementation** built to the REQ specification.

## Anchor Package Contents

The files you have read contain:

1. **REQ-context-broker.md** — The requirements specification. This is the authoritative target. The HLD must satisfy these requirements.

2. **c1-the-context-broker.md** — The companion concept paper defining the domain model: infinite conversation, context windows, build types, three-tier progressive compression, episodic and semantic memory layers, per-participant windows, proactive assembly, and known failure modes.

3. **rogers-code.md** — Flattened source code of the current Rogers State 2 implementation. This shows what the system does today. Key areas:
   - `rogers-langgraph/flows/` — The StateGraph flows (message pipeline, embedding, context assembly, retrieval, memory extraction, imperator)
   - `rogers-langgraph/services/` — Database operations, queue worker, Mem0 setup, config, MCP client
   - `rogers-langgraph/server.py` — HTTP server and tool routing
   - `rogers-postgres/init.sql` — Database schema
   - `rogers/` — Node.js MCP gateway (being replaced by nginx in State 4)

4. **d4-agent-optimal-code-architecture.md** — Theoretical basis for StateGraph-as-application, AE/TE separation, intelligence-as-packages, and the State 0-3 evolution arc.

5. **d5-state-4-mad.md** — The State 4 MAD concept: fully configurable external dependencies, same code runs standalone or in ecosystem.

## Task

Produce an HLD for the standalone Context Broker that covers:

1. **System overview** — What the Context Broker is, what problem it solves, how it maps to c1
2. **Container architecture** — The five containers, their roles, network topology, volume mounts
3. **StateGraph flow designs** — Each flow (message pipeline, embed pipeline, context assembly, retrieval, memory extraction, imperator) described with nodes, edges, state schemas, and how they use standard LangChain/LangGraph components instead of hand-rolled equivalents
4. **Database schema** — PostgreSQL tables, pgvector indexes, Neo4j data model, Redis usage
5. **MCP tool interface** — How tools are exposed, input/output contracts
6. **OpenAI-compatible chat interface** — How `/v1/chat/completions` maps to the Imperator flow
7. **Configuration system** — How `config.yml` drives provider selection, build types, token budgets
8. **Build type and retrieval pipeline** — How `standard-tiered` and `knowledge-enriched` build types drive different retrieval strategies, including the vector search and knowledge graph paths that the current code does not yet implement
9. **Queue and async processing** — Job types, lifecycle, retry, eventual consistency model
10. **Imperator design** — Identity, purpose, tool belt, persistent conversation, admin capabilities

## Key Design Decisions to Address

- Where the current code uses raw `asyncpg` queries, what LangChain retrievers or stores should replace them?
- Where the current code calls Sutherland MCP tools for embeddings/LLM, how does the configurable provider interface work?
- Where the current code uses a custom Node.js gateway, how does the nginx + LangGraph direct MCP pattern work?
- Where the current code has a custom queue worker polling Redis lists, is there a more standard approach, or is the current pattern appropriate?
- How does the `knowledge-enriched` build type's retrieval pipeline work end-to-end (vector search + graph traversal + episodic tiers)?

## Output Format

Produce a single, comprehensive Markdown document. Save it to the `outputs/` directory with the filename `HLD-context-broker-YYYYMMDD-HHMMSS.md` using the current date and time (e.g., `outputs/HLD-context-broker-20260320-223000.md`).

Be thorough and detailed. Each section should provide enough technical depth that a developer can implement from it. Use clear section headers, architecture diagrams (ASCII or Mermaid), and specific technical detail. The audience is a developer who will implement from this HLD and the REQ. They should be able to build the system without referring back to the Rogers source code.

## What NOT to Include

- No references to Joshua26, Sutherland, Alexandria, Henson, joshua-net, registry.yml, or any ecosystem-specific service
- No documentation of the current Rogers implementation — this is a forward-looking design
- No speculative features beyond what the REQ requires
