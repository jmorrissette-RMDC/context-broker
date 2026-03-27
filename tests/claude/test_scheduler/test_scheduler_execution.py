"""Tests for app/workers/scheduler.py — schedule firing and poll loop.

Covers _fire_schedule dispatch, error recording, scheduler_worker poll loop,
atomic claiming with optimistic locking, double-fire prevention, and
interval schedule elapsed checks.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_CONFIG = {
    "tuning": {
        "scheduler_poll_interval_seconds": 0,
    },
}


def _cancel_after_first(call_count_box):
    """Return an async sleep replacement that cancels after first call."""

    async def _fake_sleep(seconds):
        call_count_box[0] += 1
        if call_count_box[0] >= 1:
            raise asyncio.CancelledError()

    return _fake_sleep


def _make_schedule_row(
    schedule_type="cron",
    schedule_expr="*/5 * * * *",
    last_fired_at=None,
    name="test-schedule",
):
    """Build a minimal schedule row dict."""
    return {
        "id": uuid.uuid4(),
        "name": name,
        "schedule_type": schedule_type,
        "schedule_expr": schedule_expr,
        "last_fired_at": last_fired_at,
    }


# ---------------------------------------------------------------------------
# _fire_schedule tests
# ---------------------------------------------------------------------------


class TestFireSchedule:
    """Tests for _fire_schedule."""

    @pytest.mark.asyncio
    async def test_dispatch_to_imperator_target(self):
        """imperator target calls dispatch_tool('imperator_chat', ...)."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        schedule_id = str(uuid.uuid4())

        dispatch_mock = AsyncMock(
            return_value={"response": "task completed successfully"}
        )

        with (
            patch("app.workers.scheduler.get_pg_pool", return_value=pool),
            patch(
                "app.flows.tool_dispatch.dispatch_tool",
                dispatch_mock,
            ),
        ):
            from app.workers.scheduler import _fire_schedule

            await _fire_schedule(
                schedule_id, "run maintenance", "imperator", SAMPLE_CONFIG
            )

        # Should INSERT running, then UPDATE completed
        assert pool.execute.call_count >= 2
        insert_call = pool.execute.call_args_list[0]
        assert "running" in str(insert_call)
        update_call = pool.execute.call_args_list[1]
        assert "completed" in str(update_call)

        dispatch_mock.assert_called_once_with(
            "imperator_chat",
            {"message": "run maintenance"},
            SAMPLE_CONFIG,
            None,
        )

    @pytest.mark.asyncio
    async def test_unknown_target(self):
        """Unknown target records 'Unknown target: ...' as summary."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        schedule_id = str(uuid.uuid4())

        with patch("app.workers.scheduler.get_pg_pool", return_value=pool):
            from app.workers.scheduler import _fire_schedule

            await _fire_schedule(
                schedule_id, "do stuff", "nonexistent", SAMPLE_CONFIG
            )

        # Should still INSERT + UPDATE completed (with unknown target summary)
        assert pool.execute.call_count >= 2
        update_call = pool.execute.call_args_list[1]
        assert "Unknown target" in str(update_call)

    @pytest.mark.asyncio
    async def test_error_recording_in_schedule_history(self):
        """When dispatch_tool raises, error is recorded in schedule_history."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        schedule_id = str(uuid.uuid4())

        dispatch_mock = AsyncMock(side_effect=RuntimeError("service unavailable"))

        with (
            patch("app.workers.scheduler.get_pg_pool", return_value=pool),
            patch(
                "app.flows.tool_dispatch.dispatch_tool",
                dispatch_mock,
            ),
        ):
            from app.workers.scheduler import _fire_schedule

            await _fire_schedule(
                schedule_id, "run task", "imperator", SAMPLE_CONFIG
            )

        # Should INSERT 'running', then UPDATE 'error'
        assert pool.execute.call_count >= 2
        error_call = pool.execute.call_args_list[1]
        assert "error" in str(error_call)
        assert "service unavailable" in str(error_call)


# ---------------------------------------------------------------------------
# scheduler_worker tests
# ---------------------------------------------------------------------------


class TestSchedulerWorker:
    """Tests for scheduler_worker poll loop."""

    @pytest.mark.asyncio
    async def test_poll_loop_finds_due_schedules(self):
        """Worker fetches enabled schedules and checks if they are due."""
        pool = AsyncMock()
        # A cron schedule that matches the current minute
        now = datetime.now(timezone.utc)
        cron_expr = f"{now.minute} * * * *"
        row = _make_schedule_row(
            schedule_type="cron",
            schedule_expr=cron_expr,
            last_fired_at=None,
        )
        pool.fetch = AsyncMock(return_value=[row])
        # Atomic claim returns "UPDATE 1"
        pool.execute = AsyncMock(return_value="UPDATE 1")
        pool.fetchrow = AsyncMock(
            return_value={"message": "hello", "target": "imperator"}
        )

        call_count = [0]

        with (
            patch("app.workers.scheduler.get_pg_pool", return_value=pool),
            patch(
                "app.workers.scheduler.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch("asyncio.sleep", side_effect=_cancel_after_first(call_count)),
            patch("asyncio.create_task") as mock_create_task,
        ):
            from app.workers.scheduler import scheduler_worker

            with pytest.raises(asyncio.CancelledError):
                await scheduler_worker(SAMPLE_CONFIG)

        # Should have created a task for _fire_schedule
        assert mock_create_task.called

    @pytest.mark.asyncio
    async def test_atomic_claim_optimistic_locking(self):
        """UPDATE WHERE last_fired_at = old prevents double-fire."""
        pool = AsyncMock()
        old_fired = datetime.now(timezone.utc) - timedelta(minutes=10)
        now = datetime.now(timezone.utc)
        cron_expr = f"{now.minute} * * * *"
        row = _make_schedule_row(
            schedule_type="cron",
            schedule_expr=cron_expr,
            last_fired_at=old_fired,
        )
        pool.fetch = AsyncMock(return_value=[row])
        # Simulate another worker already claimed: UPDATE 0
        pool.execute = AsyncMock(return_value="UPDATE 0")
        pool.fetchrow = AsyncMock(
            return_value={"message": "hello", "target": "imperator"}
        )

        call_count = [0]

        with (
            patch("app.workers.scheduler.get_pg_pool", return_value=pool),
            patch(
                "app.workers.scheduler.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch("asyncio.sleep", side_effect=_cancel_after_first(call_count)),
            patch("asyncio.create_task") as mock_create_task,
        ):
            from app.workers.scheduler import scheduler_worker

            with pytest.raises(asyncio.CancelledError):
                await scheduler_worker(SAMPLE_CONFIG)

        # Should NOT have created a task because claim returned UPDATE 0
        mock_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_double_fire_prevention_same_cron_minute(self):
        """If last_fired_at is <55s ago, cron schedule is not fired again."""
        pool = AsyncMock()
        now = datetime.now(timezone.utc)
        # Fired 30 seconds ago — should NOT fire again
        recent = now - timedelta(seconds=30)
        cron_expr = f"{now.minute} * * * *"
        row = _make_schedule_row(
            schedule_type="cron",
            schedule_expr=cron_expr,
            last_fired_at=recent,
        )
        pool.fetch = AsyncMock(return_value=[row])
        pool.execute = AsyncMock(return_value="UPDATE 1")

        call_count = [0]

        with (
            patch("app.workers.scheduler.get_pg_pool", return_value=pool),
            patch(
                "app.workers.scheduler.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch("asyncio.sleep", side_effect=_cancel_after_first(call_count)),
            patch("asyncio.create_task") as mock_create_task,
        ):
            from app.workers.scheduler import scheduler_worker

            with pytest.raises(asyncio.CancelledError):
                await scheduler_worker(SAMPLE_CONFIG)

        # elapsed < 55s means is_due should be False, no task created
        mock_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_interval_schedule_elapsed_check(self):
        """Interval schedule fires when elapsed >= interval_secs."""
        pool = AsyncMock()
        now = datetime.now(timezone.utc)
        # Last fired 700 seconds ago, interval is 600 — should fire
        old = now - timedelta(seconds=700)
        row = _make_schedule_row(
            schedule_type="interval",
            schedule_expr="600",
            last_fired_at=old,
        )
        pool.fetch = AsyncMock(return_value=[row])
        pool.execute = AsyncMock(return_value="UPDATE 1")
        pool.fetchrow = AsyncMock(
            return_value={"message": "interval task", "target": "imperator"}
        )

        call_count = [0]

        with (
            patch("app.workers.scheduler.get_pg_pool", return_value=pool),
            patch(
                "app.workers.scheduler.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch("asyncio.sleep", side_effect=_cancel_after_first(call_count)),
            patch("asyncio.create_task") as mock_create_task,
        ):
            from app.workers.scheduler import scheduler_worker

            with pytest.raises(asyncio.CancelledError):
                await scheduler_worker(SAMPLE_CONFIG)

        assert mock_create_task.called

    @pytest.mark.asyncio
    async def test_interval_not_elapsed_skips(self):
        """Interval schedule does NOT fire when elapsed < interval_secs."""
        pool = AsyncMock()
        now = datetime.now(timezone.utc)
        # Last fired 100 seconds ago, interval is 600 — should NOT fire
        recent = now - timedelta(seconds=100)
        row = _make_schedule_row(
            schedule_type="interval",
            schedule_expr="600",
            last_fired_at=recent,
        )
        pool.fetch = AsyncMock(return_value=[row])
        pool.execute = AsyncMock(return_value="UPDATE 1")

        call_count = [0]

        with (
            patch("app.workers.scheduler.get_pg_pool", return_value=pool),
            patch(
                "app.workers.scheduler.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch("asyncio.sleep", side_effect=_cancel_after_first(call_count)),
            patch("asyncio.create_task") as mock_create_task,
        ):
            from app.workers.scheduler import scheduler_worker

            with pytest.raises(asyncio.CancelledError):
                await scheduler_worker(SAMPLE_CONFIG)

        mock_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Schedule history recording
# ---------------------------------------------------------------------------


class TestScheduleHistory:
    """Tests for schedule_history INSERT/UPDATE patterns."""

    @pytest.mark.asyncio
    async def test_history_insert_running_then_completed(self):
        """Successful fire: INSERT running, UPDATE completed with summary."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        schedule_id = str(uuid.uuid4())

        dispatch_mock = AsyncMock(return_value={"response": "done"})

        with (
            patch("app.workers.scheduler.get_pg_pool", return_value=pool),
            patch(
                "app.flows.tool_dispatch.dispatch_tool",
                dispatch_mock,
            ),
        ):
            from app.workers.scheduler import _fire_schedule

            await _fire_schedule(
                schedule_id, "run it", "imperator", SAMPLE_CONFIG
            )

        calls = pool.execute.call_args_list
        assert len(calls) == 2
        # First call: INSERT ... 'running'
        assert "running" in str(calls[0])
        # Second call: UPDATE ... 'completed'
        assert "completed" in str(calls[1])

    @pytest.mark.asyncio
    async def test_history_insert_running_then_error(self):
        """Failed fire: INSERT running, UPDATE error with message."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        schedule_id = str(uuid.uuid4())

        dispatch_mock = AsyncMock(
            side_effect=ValueError("invalid schedule config")
        )

        with (
            patch("app.workers.scheduler.get_pg_pool", return_value=pool),
            patch(
                "app.flows.tool_dispatch.dispatch_tool",
                dispatch_mock,
            ),
        ):
            from app.workers.scheduler import _fire_schedule

            await _fire_schedule(
                schedule_id, "run it", "imperator", SAMPLE_CONFIG
            )

        calls = pool.execute.call_args_list
        assert len(calls) == 2
        assert "running" in str(calls[0])
        assert "error" in str(calls[1])
        assert "invalid schedule config" in str(calls[1])
