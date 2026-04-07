# Quorum HLD — Anchor Package Manifest

## Purpose

Document Quorum to produce the HLD for the standalone Context Broker (State 4 refactor of Rogers).

## Anchor Package Contents

| File | Source | Tokens (approx) | Purpose |
|------|--------|-----------------|---------|
| `REQ-context-broker.md` | `portfolio/ContextBroker/docs/REQ-context-broker.md` | ~5k | The authoritative requirements spec |
| `c1-the-context-broker.md` | `Joshua26/docs/concepts/c1-the-context-broker.md` | ~3k | Domain model and concept paper |
| `rogers-code.md` | Flattened `Joshua26/mads/rogers/` (excl docs, tests, scripts, packages) | ~51k | Current implementation — domain behavior reference |
| `d4-agent-optimal-code-architecture.md` | `Joshua26/docs/concepts/d4-agent-optimal-code-architecture.md` | ~4k | StateGraph-as-application theory |
| `d5-state-4-mad.md` | `Joshua26/docs/concepts/d5-state-4-mad.md` | ~3k | State 4 configurable dependency pattern |
| `prompt.md` | This directory | ~2k | Task instructions |

**Estimated total anchor size:** ~68k tokens

## Quorum Composition

**Type:** Document Quorum (strategy lead + prose lead + multi-model review)

**Participants:** TBD — recommend 3-4 models from: Claude Opus, Gemini 2.5 Pro, GPT-5, Codex, Grok

**Lead:** TBD

## To Run

1. Assemble anchor: concatenate all files in this directory into a single document (or load individually if the platform supports multi-file input)
2. Distribute anchor + prompt to all participants
3. Follow Document Quorum process per `b1-the-quorum-pattern.md`
