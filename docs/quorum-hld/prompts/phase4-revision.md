Please read these files:

- anchor_context/HLD-context-broker-synthesized.md
- anchor_context/REQ-context-broker.md
- anchor_context/c1-the-context-broker.md
- anchor_context/review-gemini.md
- anchor_context/review-claude.md
- anchor_context/review-codex.md

# Task: Lead Resolution and HLD Revision

## Context

You are the Lead for this document. You produced the synthesized HLD. Three independent reviewers (Gemini, Claude, Codex) have each submitted an issue log after reviewing the HLD against the REQ and c1.

Your job is to:

1. **Read all three issue logs.**
2. **Address every issue** with one of:
   - **Fixed** — you agree and will apply the change
   - **Denied** — you disagree, with rationale
   - **Need Information** — you cannot resolve without clarification
3. **Produce the issue resolution log** showing your disposition of every item.
4. **Produce the revised HLD** with all accepted fixes applied.

## Guidelines

- **Convergence is signal.** Issues raised by multiple reviewers independently are strong signals. Give them serious weight.
- **This is an HLD, not an LLD.** The HLD describes architecture: components, data flow, technology choices, interface contracts, deployment model. It does not include class designs, function signatures, database DDL, Dockerfile specifics, error handling logic, or code examples. Deny issues that ask for implementation-level detail.
- **Preserve the document's level of abstraction.** Where you accept a fix, keep additions brief — a sentence or short paragraph. Do not let the document drift toward LLD.
- **No ecosystem references.** The output must remain fully self-contained with no references to Joshua26, Sutherland, Alexandria, or other ecosystem services.

## Output

Produce two files:

1. **`outputs/issue-resolution-log.md`** — Your disposition of every review issue. Format:
```
### [Reviewer] Issue N
**Status:** Fixed | Denied | Need Information
**Rationale:** Why
```

2. **`outputs/HLD-context-broker-final.md`** — The revised HLD with all accepted fixes applied.
