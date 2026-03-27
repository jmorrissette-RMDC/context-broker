"""Scheduling tools — always available to the Imperator.

The Imperator can create, list, enable, and disable scheduled tasks.
Schedules fire messages to the Imperator (or other targets) on
cron expressions or fixed intervals.
"""

import logging

import asyncpg
from langchain_core.tools import tool

from app.database import get_pg_pool

_log = logging.getLogger("context_broker.tools.scheduling")


@tool
async def list_schedules() -> str:
    """List all configured schedules and their status.

    Shows schedule name, type (cron/interval), expression, target,
    enabled status, and recent execution history.
    """
    try:
        pool = get_pg_pool()
        rows = await pool.fetch("""
            SELECT s.id, s.name, s.schedule_type, s.schedule_expr,
                   s.message, s.target, s.enabled, s.created_at,
                   (SELECT COUNT(*) FROM schedule_history sh
                    WHERE sh.schedule_id = s.id) AS run_count,
                   (SELECT status FROM schedule_history sh
                    WHERE sh.schedule_id = s.id
                    ORDER BY started_at DESC LIMIT 1) AS last_status
            FROM schedules s
            ORDER BY s.created_at
            """)
        if not rows:
            return "No schedules configured."

        lines = [f"Schedules ({len(rows)}):"]
        for row in rows:
            status = "enabled" if row["enabled"] else "disabled"
            last = row["last_status"] or "never run"
            lines.append(
                f"- [{status}] {row['name']} ({row['schedule_type']}: {row['schedule_expr']}) "
                f"→ {row['target']} | runs: {row['run_count']}, last: {last}"
            )
            lines.append(f"  message: {row['message'][:100]}")
            lines.append(f"  id: {row['id']}")
        return "\n".join(lines)
    except (asyncpg.PostgresError, OSError) as exc:
        return f"Error listing schedules: {exc}"


@tool
async def create_schedule(
    name: str,
    schedule_type: str,
    schedule_expr: str,
    message: str,
    target: str = "imperator",
) -> str:
    """Create a new scheduled task.

    Args:
        name: Human-readable name for the schedule.
        schedule_type: "cron" or "interval".
        schedule_expr: Cron expression (e.g., "*/10 * * * *") or interval
                       in seconds (e.g., "600" for every 10 minutes).
        message: The message to send to the target when fired.
        target: Target to receive the message (default: "imperator").
    """
    if schedule_type not in ("cron", "interval"):
        return "schedule_type must be 'cron' or 'interval'."

    if schedule_type == "interval":
        try:
            secs = int(schedule_expr)
            if secs < 30:
                return "Interval must be at least 30 seconds."
        except ValueError:
            return f"Invalid interval: {schedule_expr}. Must be a number of seconds."

    try:
        pool = get_pg_pool()
        row = await pool.fetchrow(
            """
            INSERT INTO schedules (name, schedule_type, schedule_expr, message, target)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            name,
            schedule_type,
            schedule_expr,
            message,
            target,
        )
        return f"Schedule created: {name} (id: {row['id']})"
    except (asyncpg.PostgresError, OSError) as exc:
        return f"Error creating schedule: {exc}"


@tool
async def enable_schedule(schedule_id: str) -> str:
    """Enable a schedule by ID.

    Args:
        schedule_id: The UUID of the schedule to enable.
    """
    import uuid

    try:
        pool = get_pg_pool()
        result = await pool.execute(
            "UPDATE schedules SET enabled = TRUE, updated_at = NOW() WHERE id = $1",
            uuid.UUID(schedule_id),
        )
        if result == "UPDATE 0":
            return f"Schedule {schedule_id} not found."
        return f"Schedule {schedule_id} enabled."
    except (asyncpg.PostgresError, OSError, ValueError) as exc:
        return f"Error enabling schedule: {exc}"


@tool
async def disable_schedule(schedule_id: str) -> str:
    """Disable a schedule by ID. The schedule is not deleted, just paused.

    Args:
        schedule_id: The UUID of the schedule to disable.
    """
    import uuid

    try:
        pool = get_pg_pool()
        result = await pool.execute(
            "UPDATE schedules SET enabled = FALSE, updated_at = NOW() WHERE id = $1",
            uuid.UUID(schedule_id),
        )
        if result == "UPDATE 0":
            return f"Schedule {schedule_id} not found."
        return f"Schedule {schedule_id} disabled."
    except (asyncpg.PostgresError, OSError, ValueError) as exc:
        return f"Error disabling schedule: {exc}"


def get_tools() -> list:
    """Return all scheduling tools."""
    return [list_schedules, create_schedule, enable_schedule, disable_schedule]
