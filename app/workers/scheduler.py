"""Built-in scheduler — fires StateGraphs on cron/interval schedules.

Polls the `schedules` table for enabled schedules and fires them
when due. Execution results recorded in `schedule_history`.

Uses DB-based last_fired_at for coordination — safe with multiple
workers or container restarts.

Schedule types:
  - cron: standard cron expression (e.g., "*/10 * * * *" = every 10 minutes)
  - interval: seconds between runs (e.g., "600" = every 10 minutes)
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from app.config import async_load_config, get_tuning
from app.database import get_pg_pool

_log = logging.getLogger("context_broker.workers.scheduler")


def _cron_is_due(cron_expr: str, now: datetime) -> bool:
    """Check if a cron expression matches the current minute.

    Supports standard 5-field cron: minute hour day-of-month month day-of-week.
    Supports * and */N syntax. Does not support ranges or lists.
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False

    fields = [now.minute, now.hour, now.day, now.month, now.weekday()]
    # cron weekday: 0=Sunday, Python weekday: 0=Monday
    # Convert Python weekday to cron: (python_weekday + 1) % 7
    fields[4] = (fields[4] + 1) % 7

    for field_val, pattern in zip(fields, parts):
        if pattern == "*":
            continue
        if pattern.startswith("*/"):
            divisor = int(pattern[2:])
            if field_val % divisor != 0:
                return False
        else:
            if field_val != int(pattern):
                return False
    return True


async def _fire_schedule(schedule_id: str, message: str, target: str, config: dict):
    """Fire a schedule by dispatching the message to the target."""
    pool = get_pg_pool()

    # Record start
    history_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO schedule_history (id, schedule_id, status)
        VALUES ($1, $2, 'running')
        """,
        history_id,
        uuid.UUID(schedule_id),
    )

    try:
        if target == "imperator":
            from app.flows.tool_dispatch import dispatch_tool

            result = await dispatch_tool(
                "imperator_chat",
                {"message": message},
                config,
                None,
            )
            summary = result.get("response", "")[:500]
        else:
            summary = f"Unknown target: {target}"

        await pool.execute(
            """
            UPDATE schedule_history
            SET completed_at = NOW(), status = 'completed', summary = $1
            WHERE id = $2
            """,
            summary,
            history_id,
        )
        _log.info("Schedule %s fired successfully", schedule_id)

    except (ValueError, RuntimeError, OSError) as exc:
        await pool.execute(
            """
            UPDATE schedule_history
            SET completed_at = NOW(), status = 'error', error = $1
            WHERE id = $2
            """,
            str(exc)[:1000],
            history_id,
        )
        _log.error("Schedule %s failed: %s", schedule_id, exc)


async def scheduler_worker(config: dict) -> None:
    """Poll for due schedules and fire them.

    Uses DB-based last_fired_at column for coordination — prevents
    double-firing across container restarts or multiple workers.
    """
    _log.info("Scheduler worker started")

    while True:
        try:
            config = await async_load_config()
            poll_interval = get_tuning(config, "scheduler_poll_interval_seconds", 60)
            pool = get_pg_pool()

            now = datetime.now(timezone.utc)

            rows = await pool.fetch(
                "SELECT id, name, schedule_type, schedule_expr, last_fired_at "
                "FROM schedules WHERE enabled = TRUE"
            )

            for row in rows:
                schedule_id = str(row["id"])
                schedule_type = row["schedule_type"]
                schedule_expr = row["schedule_expr"]
                last_fired_at = row["last_fired_at"]

                is_due = False
                if schedule_type == "cron":
                    is_due = _cron_is_due(schedule_expr, now)
                    # Don't fire same cron schedule twice in the same minute
                    if is_due and last_fired_at:
                        elapsed = (now - last_fired_at).total_seconds()
                        if elapsed < 55:
                            is_due = False
                elif schedule_type == "interval":
                    interval_secs = int(schedule_expr)
                    if not last_fired_at:
                        is_due = True
                    else:
                        elapsed = (now - last_fired_at).total_seconds()
                        is_due = elapsed >= interval_secs

                if is_due:
                    # Atomic claim: UPDATE last_fired_at only if it hasn't
                    # changed since we read it. Prevents double-firing.
                    if last_fired_at:
                        claimed = await pool.execute(
                            "UPDATE schedules SET last_fired_at = $1 "
                            "WHERE id = $2 AND last_fired_at = $3",
                            now,
                            row["id"],
                            last_fired_at,
                        )
                    else:
                        claimed = await pool.execute(
                            "UPDATE schedules SET last_fired_at = $1 "
                            "WHERE id = $2 AND last_fired_at IS NULL",
                            now,
                            row["id"],
                        )

                    if claimed == "UPDATE 0":
                        # Another worker already claimed this firing
                        continue

                    _log.info("Firing schedule: %s (%s)", row["name"], schedule_id)

                    full_row = await pool.fetchrow(
                        "SELECT message, target FROM schedules WHERE id = $1",
                        row["id"],
                    )
                    if full_row:
                        asyncio.create_task(
                            _fire_schedule(
                                schedule_id,
                                full_row["message"],
                                full_row["target"],
                                config,
                            )
                        )

            await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            _log.info("Scheduler worker cancelled")
            raise
        except Exception as exc:
            _log.error("Scheduler worker error: %s: %s", type(exc).__name__, exc)
            await asyncio.sleep(30)
