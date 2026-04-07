# Gate 2 Code Review — Round 6 (Codex)

Date: 2026-03-28
Scope: Python in app/, packages/, alerter/, log_shipper/
Requirements: REQ-001 (State 4 MAD), REQ-002 (State 4 pMAD)

## Findings

### 1) Blocking filesystem I/O inside async tool handlers (REQ-001 §5.1)
- `packages/context-broker-te/src/context_broker_te/tools/filesystem.py:43-221`
  - `file_read`, `file_list`, `file_search`, `file_write`, `read_system_prompt`, and `update_system_prompt` perform synchronous filesystem operations (`open`, `os.listdir`, `os.walk`, `os.path.getsize`, `os.makedirs`) directly inside async functions.
  - `read_system_prompt` and `update_system_prompt` also call `get_ctx().load_merged_config()` (sync, performs file I/O) inside async functions.
  - These block the event loop under load and violate the “no blocking I/O in async functions” requirement.

### 2) Imperator state file I/O executed synchronously in async lifecycle (REQ-001 §5.1)
- `app/imperator/state_manager.py:35-113`
  - `initialize()` and `get_conversation_id()` call `_read_state_file()` and `_write_state_file()` which use synchronous `open()` and filesystem operations.
  - These are invoked from async methods without executor offload, which can block the event loop during startup or runtime recovery.

### 3) Synchronous config reads inside async startup and retry loop (REQ-001 §5.1)
- `app/main.py:31-92`
  - `_postgres_retry_loop()` and `lifespan()` call `load_config()` directly (sync file reads + `os.stat`).
  - These run on the event loop during startup and retry cycles; config loads should use `async_load_config()` or be offloaded to an executor.

### 4) Synchronous config read in async install_stategraph flow (REQ-001 §5.1)
- `app/flows/install_stategraph.py:16-29`
  - `install_stategraph()` calls `load_config()` synchronously inside an async function.
  - Should switch to `async_load_config()` or use `run_in_executor` for config reads to prevent event-loop blocking.

### 5) Synchronous config read inside async notification tool (REQ-001 §5.1)
- `packages/context-broker-te/src/context_broker_te/tools/notify.py:45-51`
  - `send_notification()` calls `get_ctx().load_merged_config()` (sync file I/O) inside an async tool handler.
  - This blocks the event loop on every notification send.

## Notes
- No new blanket `except Exception` usage found.
- Logging remains structured JSON to stdout/stderr as required.

