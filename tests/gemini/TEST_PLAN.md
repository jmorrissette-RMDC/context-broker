# Gemini Test Plan: Independent Coverage Audit & Remediation

## 1. Overview
This test plan addresses the critical gaps identified in the "Context Broker Test Coverage Report". The focus is on replacing **MOCK** tests with **REAL** infrastructure tests and providing coverage for areas currently marked as **NONE**.

## 2. Priority Coverage Areas

### Priority 1: The Alerter Sidecar (Status: NONE)
*   **Target:** `alerter/alerter.py`
*   **Goal:** Verify that the standalone sidecar service can receive webhooks, process them via LLM (simulated or real), and correctly format the output for dispatch.
*   **Strategy:** Implement a dedicated integration test that spawns the Alerter process or uses a test client to verify its message flow.

### Priority 2: Operational & Scheduling Tools (Status: NONE)
*   **Target:** `app/flows/contracts.py` (Operational/Scheduling implementations)
*   **Goal:** Verify that operational tools can successfully interact with PostgreSQL and Neo4j without mocking the DB layer.
*   **Strategy:** Use the existing `pytest` DB fixtures to perform actual writes and reads for domain knowledge and scheduling metadata.

### Priority 3: Prompt Loading & Filesystem (Status: NONE/MOCK)
*   **Target:** `app/prompt_loader.py`
*   **Goal:** Ensure prompts are correctly loaded from the `config/prompts/` directory, handling missing files or malformed content gracefully.
*   **Strategy:** Use `tmp_path` fixture in pytest to create a temporary prompt directory and verify loading logic.

### Priority 4: REAL Infrastructure for Heavily Mocked Areas (Status: MOCK)
*   **Target:** `app/token_budget.py`, `app/routes/mcp.py`
*   **Goal:** Replace mocks with actual calls where feasible, or use a "local-first" infrastructure approach (e.g., local LLM proxies or actual vector searches).
*   **Strategy:** Integration tests for the hybrid search flow and token budget scaling using real-world scenarios.

## 3. Test Directory Structure
*   `tests/gemini/unit/`: Logic-only tests for the newly covered areas.
*   `tests/gemini/integration/`: Tests requiring live DB/Neo4j/Alerter.
*   `tests/gemini/e2e/`: Full scenario-based tests that bridge multiple gaps.

## 4. Implementation Schedule
1.  **Phase 1:** Implement `unit/test_prompt_loader.py` and `unit/test_alerter_logic.py`.
2.  **Phase 2:** Implement `integration/test_operational_tools.py` and `integration/test_scheduling_tools.py`.
3.  **Phase 3:** Create `integration/test_alerter_service.py` (Full service integration).
4.  **Phase 4:** Final E2E validation.

## 5. Success Criteria
*   Zero **NONE** statuses for critical core services.
*   Critical tool paths (Operational/Scheduling) upgraded from **NONE** to **REAL**.
*   Alerter sidecar fully verified in an integration environment.
