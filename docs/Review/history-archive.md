# Context Broker — Progress Log

Historical record of all development actions from initial extraction through deployment and testing.

| Date | Step | Action | Outcome |
|------|------|--------|---------|
| 2026-03-22 | 1 | Extracted 52 files from Sonnet output | 37 app + 4 config/infra + 11 test files to portfolio/ContextBroker/ |
| 2026-03-22 | 2 | Fixed docker-compose.yml | Removed depends_on, added Ollama container with GPU passthrough |
| 2026-03-22 | 3 | Updated config.example.yml | Defaults to local Ollama (qwen2.5:7b + nomic-embed-text) |
| 2026-03-22 | 4 | Internal code review | 11 finding categories: raw libs, hardcoded prompts/config, route logic, blanket exceptions, idempotency, state immutability, blocking I/O, fail-fast, Neo4j auth |
| 2026-03-22 | 6 | Fixed all internal review findings | Neo4j auth disabled, 7 placeholder API keys removed, Mem0 init retry fixed, state_manager fail-fast, 23+ blanket exceptions→specific, 3 prompts externalized, 19 tuning params to config.yml, 6 state immutability fixes, 5 asyncio.get_running_loop fixes, 3 idempotency fixes (atomic seq#, summary dedup, job dedup), migrations.py forward ref fixed, duplicate httpx removed |
| 2026-03-22 | 5 | Gate 2 complete — 7 reviewers, 3 passes | 21 review files in docs/Review/. Pass 1: 7/7 code reviews. Pass 2: 7/7 compliance reviews. Pass 3: 4/4 functional reviews (frontier + Opus). Aggregated: 9 blockers, 26 majors, 12 minors, 10 functional gaps, 6 compliance failures |
| 2026-03-22 | 5 | Findings presented to Jason | All findings approved for fixing — nothing deferred. Also adding recipient_id field to message store |
| 2026-03-22 | 5 | Reviewer performance evaluated | Rankings: GPT-5.4 > Opus > Grok-4.2 > MiniMax > Gemini > Qwen > DeepSeek. Saved to memory. |
| 2026-03-22 | 6 | Fixed all Gate 2 findings | 9 blockers, 26 majors, 12 minors, 10 functional gaps, 6 compliance gaps fixed. Added recipient_id field. Source grew from 49K to 59K tokens. |
| 2026-03-22 | 5-R2 | Gate 2 Round 2 complete | 7 reviewers × 3 passes. Results: 5 blockers, 25 majors, 18 minors (down from 9/26/12 in R1). Opus compliance: 71 PASS / 3 PARTIAL / 1 FAIL (README only) |
| 2026-03-22 | 6-R2 | Fixed all R2 findings (48 total) | Advisory locks for seq#, delayed retry queue, Imperator persistence to Postgres, lock safety tokens, search filters in SQL, budget clamping, cache invalidation, Mem0 dims, admin tool hardening, and 18 minors |
| 2026-03-22 | 5-R2 | R2 reviewer performance evaluated | GPT-5.4 > Opus > Gemini > Grok > MiniMax > Qwen > DeepSeek. Dropped DeepSeek-R1 and Qwen3-Coder for R3 (no unique value in either round). Gemini dropped from functional pass (whiffed on feature parity). |
| 2026-03-22 | 5-R3 | Gate 2 Round 3 launched | 5 reviewers (GPT-5.4, Grok-4.2, Gemini-3.1-Pro, MiniMax-M2.5, Opus). Functional: 3 (GPT-5.4, Grok-4.2, Opus — MiniMax context too tight, Gemini unreliable). |
| 2026-03-22 | 5-R3 | R3 results revealed R2 fix failure | Parallel agents overwrote each other's changes on shared files. Many R2 fixes didn't land. R3 was wasted. Must audit all code, build verified fix list, fix sequentially, verify each. |
| 2026-03-22 | 6-R3 | Built issue log, fixed all 58 OPEN items | Issue log at docs/Review/issue-log.md. 72 total findings catalogued. Fixes partitioned by file (no overlapping agents). Every fix verified against actual code via grep/read. Source now 72K tokens. |
| 2026-03-22 | 5-R4 | Gate 2 Round 4 complete | 5 reviewers × 3 passes. 0 blockers, 23 majors, 20 minors. Opus: 0 blocker, 0 major code, 48/0/3 compliance. |
| 2026-03-22 | 5-R4 | R4 findings + WONTFIX review with Jason | All R4 findings recorded. 10 WONTFIX items reviewed — 2 kept (F-03 parallel extraction, R2-F18 no assembly on collapse), 8 reopened. Major architectural decisions made: ARCH-01 through ARCH-20. EX-CB-001 approved (Mem0 broad exception). REQ-001 §2.1 hardened. Exception registry created. |
| 2026-03-22 | 6-R4 | Implemented all ARCH changes + R4 fixes + reopened WONTFIX | ~20 ARCH items + ~30 R4 findings + 5 reopened WONTFIX. Schema changes, build type registry, Imperator ReAct graph, MemorySaver removed, messages array assembly, memory scoring. |
| 2026-03-22 | 5-R5 | Gate 2 Round 5 complete | 5 reviewers. Pre-fix code reviews found null-content issues (already fixed mid-round). Opus compliance: 74/3/2. Opus functional: 0 blockers, "no accidental regressions." |
| 2026-03-22 | 6-R5 | Fixed ALL R5 findings (27 items) | 16 majors + 11 minors. All 27 verified. |
| 2026-03-22 | 5-R6 | Gate 2 Round 6 | 0 blockers (4 fixed mid-round), 21 majors, 19 minors. Opus compliance 70/4/0. |
| 2026-03-22 | 6-R6 | Fixed all R6 findings (41 items) | Updated REQ/HLD for ARCH changes. Updated review prompts with accepted items. |
| 2026-03-23 | 5-R7 | Gate 2 Round 7 | 2 blockers (1/5), 21 majors, 27 minors. **Opus compliance: 80/80 PASS.** "Codebase is mature." |
| 2026-03-23 | 6-R7 | Fixed high-consensus R7 items only | B1 (Redis exceptions), M1 (admin_tools restart-required), M3 (session lock), M18 (recipient defaults). B2 WONTFIX (local mode). Remaining 45 deferred. Gate review complete. |
| 2026-03-23 | 7 | Deployed to irina | Committed 178 files, pushed, pulled on irina. Fixed: dependency versions, FastAPI response type, token_budget None, blmove API, redis shadow, Ollama network, migration 8 savepoint, migration 12 duplicate columns. |
| 2026-03-23 | 8+9 | Health verified, basic functionality confirmed | /health 200 (all deps ok). conv_create_conversation, conv_create_context_window, conv_store_message, conv_retrieve_context all working. Messages stored with embedding/extraction jobs queued. Context retrieved as structured messages array. |
| 2026-03-23 | 10 | Test code written (REQ-105) | 173 tests: 135 unit + 38 static analysis. |
| 2026-03-23 | 11 | Full test suite | 268 tests. 267 passed, 1 failed (embedding). Fixed: network, GPU spread, healthcheck, pipeline SSH tests, static checks. Deployment issues found by e2e: tiktoken, VRAM, migration abort, embedding API params. |
| 2026-03-23 | 11 | Infrastructure: Infinity container | Replaced local cross-encoder with Infinity container for embeddings + reranking. Removed sentence-transformers. Reranker uses /v1/rerank API. ONNX crashes on models — use --engine torch with michaelf34/infinity:latest (NOT latest-cpu). mxbai-rerank-xsmall-v1 for reranker. |
| 2026-03-23 | 11 | Session resumed after compaction | Read all Session Resume docs. Completed 12 pending doc updates. |
| 2026-03-23 | 11 | Infinity verified and tested | Infinity healthy. Embedding (768 dims), reranking (correct ordering), LLM (Ollama) all working. Fixed: Infinity URL (no /v1 prefix), rerank URL (/rerank not /v1/rerank), embeddings config base_url. |
| 2026-03-23 | 11 | Verified graceful degradation | All inference paths handle provider unavailability. httpx wraps as ConnectError (subclass of HTTPError). LangChain wraps as APIConnectionError (subclass of APIError). |
| 2026-03-23 | 11 | Full test suite: 268/268 passing | Fixed: Decimal serialization in MCP search (Postgres ts_rank returns Decimal), embedding test column name ('vector' → 'embedding'). |
| 2026-03-23 | 11 | Fixed healthcheck false negatives | Ollama: bash TCP trick unreliable, changed to `ollama list`. Gateway: Alpine wget resolves localhost to IPv6, changed to 127.0.0.1. All 7 containers healthy. |
| 2026-03-23 | 11 | Phase 1 cross-provider direct API checks | 8 passed (5 LLM + 3 embedding). Together rerank requires dedicated endpoint (skipped). |
| 2026-03-23 | 11 | REQ-001/002 gap analysis | Found 4 missing requirements in REQ-001 (AE/TE separation, dynamic loading, Imperator, TE package structure) + base contract. REQ-002 missing TE config separation. Updated all requirements docs + Context Broker REQ/HLD. |
