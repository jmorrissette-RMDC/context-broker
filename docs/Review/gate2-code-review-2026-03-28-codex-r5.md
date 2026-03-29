# Gate 2 Code Review - 2026-03-28 - Codex R5

Scope: app/, packages/, alerter/, log_shipper/, Dockerfile, docker-compose.yml, entrypoint.sh, nginx/, deploy.sh, init.sql
Requirements: REQ-001 State 4 MAD, REQ-002 State 4 pMAD

Status: NEW issues found (6)

## Findings

1) alerter/alerter.py:31-36
Severity: Medium
Issue: Logging is plain-text via logging.basicConfig; does not emit structured JSON with required fields (timestamp, level, message, context) and uses WARNING instead of WARN.
Fix: Replace basicConfig with the shared JSON logger (or implement a JSON formatter) and map WARNING -> WARN if required by schema.

2) log_shipper/shipper.py:12-18
Severity: Medium
Issue: Log shipper logs are plain-text, not JSON structured logs as required for all containers.
Fix: Adopt the same JSON logging formatter used by the main app (stdout) and ensure required fields are present.

3) alerter/alerter.py:146-160
Severity: High
Issue: /health always returns 200 and status "healthy" even when Postgres is unavailable. REQ-001 requires 503 on unhealthy with per-dependency status.
Fix: Return 503 when _pool is None or query fails, and include per-dependency status fields (e.g., postgres: "down").

4) alerter/alerter.py (missing endpoint)
Severity: Medium
Issue: HTTP system lacks /metrics endpoint. REQ-001 requires Prometheus exposition on GET /metrics for any HTTP-exposed service.
Fix: Add a /metrics route exporting Prometheus metrics (can be minimal counters for requests, errors, durations).

5) alerter/alerter.py:253-314
Severity: High
Issue: Webhook processing is not idempotent. Retries will re-send notifications and re-insert alert_events/alert_deliveries, violating REQ-001 idempotency for retriable handlers.
Fix: Use CloudEvents id (or a hash of event payload) to dedupe; store a unique constraint and short-circuit if already processed.

6) alerter/alerter.py:265-270
Severity: Medium
Issue: External input validation is incomplete. If event["data"] is not an object (e.g., string/array), data.get raises AttributeError and returns 500.
Fix: Validate data is a dict before accessing; return 400 with a clear error when the shape is invalid.

## Notes
- If fewer than 5 new issues had been found, the codebase would be considered clean. This round found 6 new issues.
