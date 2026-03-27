"""Tests for scheduling tools — create, enable, disable schedules."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def mock_pool():
    """Return an AsyncMock that behaves like an asyncpg pool."""
    pool = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# create_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_schedule_cron(mock_pool):
    """create_schedule() inserts a cron schedule into the DB."""
    sched_id = uuid.uuid4()
    mock_pool.fetchrow = AsyncMock(return_value={"id": sched_id})

    with patch(
        "context_broker_te.tools.scheduling.get_pg_pool",
        return_value=mock_pool,
    ):
        from context_broker_te.tools.scheduling import create_schedule

        result = await create_schedule.ainvoke(
            {
                "name": "nightly-check",
                "schedule_type": "cron",
                "schedule_expr": "0 2 * * *",
                "message": "Run nightly health check",
            }
        )

    assert "created" in result.lower()
    assert str(sched_id) in result
    mock_pool.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_schedule_interval(mock_pool):
    """create_schedule() inserts an interval schedule into the DB."""
    sched_id = uuid.uuid4()
    mock_pool.fetchrow = AsyncMock(return_value={"id": sched_id})

    with patch(
        "context_broker_te.tools.scheduling.get_pg_pool",
        return_value=mock_pool,
    ):
        from context_broker_te.tools.scheduling import create_schedule

        result = await create_schedule.ainvoke(
            {
                "name": "periodic-ping",
                "schedule_type": "interval",
                "schedule_expr": "600",
                "message": "Ping the world",
            }
        )

    assert "created" in result.lower()


@pytest.mark.asyncio
async def test_create_schedule_rejects_short_interval(mock_pool):
    """create_schedule() rejects intervals shorter than 30 seconds."""
    with patch(
        "context_broker_te.tools.scheduling.get_pg_pool",
        return_value=mock_pool,
    ):
        from context_broker_te.tools.scheduling import create_schedule

        result = await create_schedule.ainvoke(
            {
                "name": "too-fast",
                "schedule_type": "interval",
                "schedule_expr": "10",
                "message": "This should fail",
            }
        )

    assert "at least 30 seconds" in result.lower()
    mock_pool.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_schedule_rejects_invalid_type(mock_pool):
    """create_schedule() rejects an invalid schedule_type."""
    with patch(
        "context_broker_te.tools.scheduling.get_pg_pool",
        return_value=mock_pool,
    ):
        from context_broker_te.tools.scheduling import create_schedule

        result = await create_schedule.ainvoke(
            {
                "name": "bad-type",
                "schedule_type": "weekly",
                "schedule_expr": "* * * * 1",
                "message": "nope",
            }
        )

    assert "must be" in result.lower()


# ---------------------------------------------------------------------------
# enable_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_schedule_updates_enabled_true(mock_pool):
    """enable_schedule() updates enabled=TRUE for the given schedule."""
    sched_id = str(uuid.uuid4())
    mock_pool.execute = AsyncMock(return_value="UPDATE 1")

    with patch(
        "context_broker_te.tools.scheduling.get_pg_pool",
        return_value=mock_pool,
    ):
        from context_broker_te.tools.scheduling import enable_schedule

        result = await enable_schedule.ainvoke({"schedule_id": sched_id})

    assert "enabled" in result.lower()
    mock_pool.execute.assert_awaited_once()
    call_args = mock_pool.execute.call_args
    assert "TRUE" in call_args[0][0]


@pytest.mark.asyncio
async def test_enable_schedule_not_found(mock_pool):
    """enable_schedule() reports not found when no row is updated."""
    sched_id = str(uuid.uuid4())
    mock_pool.execute = AsyncMock(return_value="UPDATE 0")

    with patch(
        "context_broker_te.tools.scheduling.get_pg_pool",
        return_value=mock_pool,
    ):
        from context_broker_te.tools.scheduling import enable_schedule

        result = await enable_schedule.ainvoke({"schedule_id": sched_id})

    assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# disable_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_schedule_updates_enabled_false(mock_pool):
    """disable_schedule() updates enabled=FALSE for the given schedule."""
    sched_id = str(uuid.uuid4())
    mock_pool.execute = AsyncMock(return_value="UPDATE 1")

    with patch(
        "context_broker_te.tools.scheduling.get_pg_pool",
        return_value=mock_pool,
    ):
        from context_broker_te.tools.scheduling import disable_schedule

        result = await disable_schedule.ainvoke({"schedule_id": sched_id})

    assert "disabled" in result.lower()
    mock_pool.execute.assert_awaited_once()
    call_args = mock_pool.execute.call_args
    assert "FALSE" in call_args[0][0]


@pytest.mark.asyncio
async def test_disable_schedule_not_found(mock_pool):
    """disable_schedule() reports not found when no row is updated."""
    sched_id = str(uuid.uuid4())
    mock_pool.execute = AsyncMock(return_value="UPDATE 0")

    with patch(
        "context_broker_te.tools.scheduling.get_pg_pool",
        return_value=mock_pool,
    ):
        from context_broker_te.tools.scheduling import disable_schedule

        result = await disable_schedule.ainvoke({"schedule_id": sched_id})

    assert "not found" in result.lower()
