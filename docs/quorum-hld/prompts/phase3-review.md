Please read these files:

- anchor_context/HLD-context-broker-synthesized.md
- anchor_context/REQ-context-broker.md
- anchor_context/c1-the-context-broker.md

# Task: Review the Context Broker HLD

## Context

You are reviewing a synthesized High-Level Design (HLD) for the Context Broker — a standalone context engineering and conversational memory service. The HLD was produced by synthesizing three independent drafts from different models. Your job is to review it, not rewrite it.

The REQ is the authoritative requirements specification. c1 is the companion concept paper defining the domain model. The HLD must be architecturally sound, consistent with both, and follow established best practices.

## Review Dimensions

Evaluate the HLD against these criteria:

1. **Architectural soundness** — Are the component boundaries clean? Do the data flows make sense? Are there circular dependencies or unnecessary coupling?

2. **REQ consistency** — Does the HLD satisfy all requirements in the REQ? Are there requirements the HLD ignores or contradicts?

3. **c1 consistency** — Does the HLD faithfully represent the domain model from c1? Build types, tiers, memory layers, proactive assembly, per-participant windows — are these correctly reflected in the architecture?

4. **Best practice** — Does the architecture follow established best practices for LangChain/LangGraph, Docker Compose, async Python, PostgreSQL/pgvector, Neo4j, and Redis? Where the HLD deviates from standard patterns, is the deviation justified?

5. **Completeness** — Does the HLD cover what an HLD should cover? (Major components, how they relate, key decisions with rationale, data flow, technology choices, interface contracts, deployment model.) Are there architectural decisions that should be made at HLD level but are missing?

6. **Clarity** — Is anything ambiguous, contradictory, or confusing? Could a developer pick this up and understand the architecture without asking clarifying questions?

## What NOT to Flag

- **Missing implementation detail.** This is an HLD, not an LLD. Do not flag the absence of class designs, function signatures, full database DDL, error handling logic, or code examples. The code itself serves as the LLD in this system.
- **Missing tool schemas.** Input/output schemas belong in the README, not the HLD.
- **Missing Dockerfile or nginx config.** These are implementation artifacts.

## Output Format

Produce an issue log. For each issue:

```
### Issue N
**Type:** Gap | Inconsistency | Technical Concern | Best Practice | Clarity
**Section:** Which HLD section this applies to
**Description:** What the issue is
**Recommendation:** What you suggest
```

At the end, provide a summary: total issues by type, and your overall assessment of the HLD's readiness (e.g., "ready with minor revisions", "needs significant rework in section X", etc.).

Do NOT rewrite the HLD. Do NOT produce a new version. Just raise issues.
