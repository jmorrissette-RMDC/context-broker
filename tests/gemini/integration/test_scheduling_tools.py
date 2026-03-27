import pytest
import uuid
from unittest.mock import patch, AsyncMock, MagicMock
from context_broker_te.tools.scheduling import create_schedule, list_schedules, enable_schedule, disable_schedule

@pytest.fixture
def mock_pool():
    """Create a mock connection pool for testing scheduling logic."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    pool.execute = AsyncMock()
    return pool

@pytest.fixture(autouse=True)
def mock_get_pg_pool(mock_pool):
    """Patch get_pg_pool to return our mock pool."""
    with patch("context_broker_te.tools.scheduling.get_pg_pool", return_value=mock_pool):
        yield

@pytest.mark.asyncio
async def test_schedule_lifecycle_mocked(mock_pool):
    """Test logic for creating, listing, and toggling a schedule using mocks."""
    name = f"test-sched-{uuid.uuid4().hex[:8]}"
    
    # Setup mock return for create_schedule
    mock_pool.fetchrow.return_value = {"id": "123e4567-e89b-12d3-a456-426614174000"}
    
    # 1. Create
    res = await create_schedule.ainvoke({
        "name": name,
        "schedule_type": "interval",
        "schedule_expr": "600",
        "message": "test-message"
    })
    assert "Schedule created" in res
    sched_id = "123e4567-e89b-12d3-a456-426614174000"

    # Setup mock return for list_schedules
    mock_pool.fetch.return_value = [
        {"id": sched_id, "name": name, "schedule_type": "interval", "schedule_expr": "600",
         "message": "test-message", "target": "imperator", "enabled": True, 
         "created_at": None, "run_count": 0, "last_status": "never run"}
    ]

    # 2. List
    res_list = await list_schedules.ainvoke({})
    assert name in res_list
    assert "enabled" in res_list

    # 3. Disable
    mock_pool.execute.return_value = "UPDATE 1"
    res_dis = await disable_schedule.ainvoke({"schedule_id": sched_id})
    assert "disabled" in res_dis
