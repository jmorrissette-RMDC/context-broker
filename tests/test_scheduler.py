"""Tests for the scheduler (§4.20).

Verifies cron parsing, interval minimum enforcement.
"""

from datetime import datetime, timezone

import pytest

from app.workers.scheduler import _cron_is_due


class TestCronParsing:
    """T-20.1: Cron expression parsing."""

    def test_wildcard_always_matches(self):
        now = datetime(2026, 3, 25, 14, 30, tzinfo=timezone.utc)
        assert _cron_is_due("* * * * *", now) is True

    def test_every_10_minutes_matches(self):
        now = datetime(2026, 3, 25, 14, 30, tzinfo=timezone.utc)
        assert _cron_is_due("*/10 * * * *", now) is True

    def test_every_10_minutes_no_match(self):
        now = datetime(2026, 3, 25, 14, 33, tzinfo=timezone.utc)
        assert _cron_is_due("*/10 * * * *", now) is False

    def test_specific_minute(self):
        now = datetime(2026, 3, 25, 14, 15, tzinfo=timezone.utc)
        assert _cron_is_due("15 * * * *", now) is True

    def test_specific_minute_no_match(self):
        now = datetime(2026, 3, 25, 14, 16, tzinfo=timezone.utc)
        assert _cron_is_due("15 * * * *", now) is False

    def test_specific_hour_and_minute(self):
        now = datetime(2026, 3, 25, 9, 0, tzinfo=timezone.utc)
        assert _cron_is_due("0 9 * * *", now) is True

    def test_specific_hour_no_match(self):
        now = datetime(2026, 3, 25, 10, 0, tzinfo=timezone.utc)
        assert _cron_is_due("0 9 * * *", now) is False

    def test_every_5_minutes_at_zero(self):
        now = datetime(2026, 3, 25, 14, 0, tzinfo=timezone.utc)
        assert _cron_is_due("*/5 * * * *", now) is True

    def test_invalid_expression_returns_false(self):
        now = datetime(2026, 3, 25, 14, 30, tzinfo=timezone.utc)
        assert _cron_is_due("bad", now) is False

    def test_too_few_fields_returns_false(self):
        now = datetime(2026, 3, 25, 14, 30, tzinfo=timezone.utc)
        assert _cron_is_due("* *", now) is False

    def test_day_of_week_sunday(self):
        # 2026-03-29 is a Sunday (weekday=6 in Python, 0 in cron)
        now = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)
        assert _cron_is_due("0 12 * * 0", now) is True

    def test_day_of_week_monday(self):
        # 2026-03-30 is a Monday (weekday=0 in Python, 1 in cron)
        now = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)
        assert _cron_is_due("0 12 * * 1", now) is True

    def test_specific_day_of_month(self):
        now = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
        assert _cron_is_due("0 0 1 * *", now) is True


class TestIntervalMinimum:
    """T-20.5: Interval minimum enforcement."""

    @pytest.mark.asyncio
    async def test_rejects_short_interval(self):
        from context_broker_te.tools.scheduling import create_schedule

        result = await create_schedule.ainvoke(
            {
                "name": "too-fast",
                "schedule_type": "interval",
                "schedule_expr": "10",
                "message": "test",
            }
        )
        assert "at least 30 seconds" in result

    @pytest.mark.asyncio
    async def test_rejects_invalid_interval(self):
        from context_broker_te.tools.scheduling import create_schedule

        result = await create_schedule.ainvoke(
            {
                "name": "bad",
                "schedule_type": "interval",
                "schedule_expr": "abc",
                "message": "test",
            }
        )
        assert "Invalid interval" in result

    @pytest.mark.asyncio
    async def test_rejects_invalid_schedule_type(self):
        from context_broker_te.tools.scheduling import create_schedule

        result = await create_schedule.ainvoke(
            {
                "name": "bad",
                "schedule_type": "weekly",
                "schedule_expr": "* * * * *",
                "message": "test",
            }
        )
        assert "must be 'cron' or 'interval'" in result
