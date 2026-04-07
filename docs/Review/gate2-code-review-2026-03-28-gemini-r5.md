# Code Review: Context Broker (Round 5)
**Date:** 2026-03-28
**Reviewer:** Gemini CLI
**Status:** 5 New Issues Identified

## 1. Executive Summary
This is the fifth round of code review for the State 4 MAD "Context Broker". While the core architecture is robust and leverages LangGraph effectively, 5 new issues were identified regarding state persistence in ReAct loops, async correctness in configuration loading, and container security/topology standards.

## 2. Identified Issues

| ID | File | Line | Severity | Description | Fix |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **G5-01** | `packages/context-broker-te/src/context_broker_te/imperator_flow.py` | 245-310 | **High** | **Context Loss in ReAct Loop:** The `agent_node` loads conversation history via `get_context` and prepends it to the local `messages` list for the LLM call, but it does not return these messages in the state update. Consequently, on subsequent ReAct iterations (e.g., after a tool call), the `else` block loads the existing state which lacks the history, causing the agent to lose context during multi-turn reasoning. | Update `agent_node` to return the `history_messages` in the first turn's state update, or re-load/prepend them in the `else` block to ensure they are present in every LLM call within the ReAct cycle. |
| **G5-02** | `app/config.py` | 130 | **Medium** | **Synchronous File I/O in Async Context:** `async_load_config` calls the synchronous `load_config()` function. If `load_config` encounters a cache miss (e.g., on first load or after a file change), it performs synchronous file reading (`open().read()`), which blocks the FastAPI/LangGraph event loop. This violates REQ-001 §5.1. | Refactor `load_config` to separate the I/O logic, and ensure `async_load_config` always performs file reads via `loop.run_in_executor` or an async I/O library. |
| **G5-03** | `alerter/alerter.py`, `log_shipper/shipper.py` | ~25 | **Medium** | **Non-Structured Logging in Sidecars:** Both the Alerter and Log Shipper sidecar containers use standard `logging.basicConfig` with string formatting. This violates REQ-001 §4.2, which mandates JSON-formatted structured logging for all components. | Implement a JSON formatter (similar to the one in `app/logging_setup.py`) and apply it to the root logger in both sidecar applications. |
| **G5-04** | `alerter/Dockerfile`, `log_shipper/Dockerfile`, `ui/Dockerfile` | Various | **Low** | **Dockerfile USER/COPY Violation:** These Dockerfiles perform `COPY` operations as `root` before the `USER` directive is declared, and the `USER` directive does not immediately follow user creation. This violates REQ-002 §1.1 and §1.3. | Move user creation to the top of the Dockerfile (after system package installation), follow it immediately with the `USER` directive, and use `COPY --chown` for all subsequent file copies. |
| **G5-05** | `docker-compose.yml` | 200 | **Low** | **Bypassing Gateway Boundary:** The `context-broker-ui` service publishes port 7860 directly to the host. REQ-002 §2.2 states that the gateway (Nginx) must be the "sole network boundary between external traffic and internal containers." | Remove the `ports` mapping from `context-broker-ui` in the compose file and instead route UI traffic through the Nginx gateway via a new `/ui` location block in `nginx/nginx.conf`. |

## 3. Compliance Scorecard

| Category | Status | Notes |
| :--- | :--- | :--- |
| **LangGraph Architecture** | Warning | Correct graph structure, but state flow bug (G5-01). |
| **Async Correctness** | Fail | Synchronous I/O in async config loader (G5-02). |
| **Structured Logging** | Fail | Missing JSON logging in sidecar containers (G5-03). |
| **Security & Topology** | Warning | Gateway boundary bypassed (G5-05); Dockerfile user order (G5-04). |
| **Fail-Fast Configuration** | Pass | Good validation of build types at startup in `app/main.py`. |
| **AE/TE Separation** | Pass | Clean separation via entry_points and independent config files. |
