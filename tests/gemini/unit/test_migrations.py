import pytest
import asyncpg
from unittest.mock import AsyncMock, patch, MagicMock

import app.migrations as migrations_module
from app.migrations import run_migrations, get_current_schema_version, _migration_008, _migration_009, _migration_012, _migration_013

@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock()
    conn.fetch = AsyncMock()
    
    # Create a proper async context manager for pool.acquire()
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__.return_value = conn
    pool.acquire.return_value = acquire_ctx
    
    # Create a proper async context manager for conn.transaction()
    tx_ctx = AsyncMock()
    tx_ctx.__aenter__.return_value = tx_ctx
    conn.transaction.return_value = tx_ctx
    
    return pool, conn

@pytest.mark.asyncio
async def test_get_current_schema_version(mock_pool):
    _, conn = mock_pool
    
    conn.fetchval.return_value = 5
    version = await get_current_schema_version(conn)
    assert version == 5
    
    conn.fetchval.return_value = None
    version = await get_current_schema_version(conn)
    assert version == 0
    
    conn.fetchval.side_effect = asyncpg.UndefinedTableError("table not found")
    version = await get_current_schema_version(conn)
    assert version == 0

@pytest.mark.asyncio
async def test_run_migrations_all_applied(mock_pool):
    pool, conn = mock_pool
    conn.fetchval.return_value = max(v for v, _, _ in migrations_module.MIGRATIONS)
    
    with patch("app.migrations.get_pg_pool", return_value=pool):
        await run_migrations()
        
    conn.execute.assert_any_call("SELECT pg_advisory_lock(1)")
    conn.execute.assert_any_call("SELECT pg_advisory_unlock(1)")
    # No transactions should be created because no migrations to run
    assert not conn.transaction.called

@pytest.mark.asyncio
async def test_run_migrations_applies_pending(mock_pool):
    pool, conn = mock_pool
    
    # Simulate current version being 2 less than max
    max_version = max(v for v, _, _ in migrations_module.MIGRATIONS)
    conn.fetchval.return_value = max_version - 2
    
    with patch("app.migrations.get_pg_pool", return_value=pool):
        await run_migrations()
        
    conn.execute.assert_any_call("SELECT pg_advisory_lock(1)")
    assert conn.transaction.call_count == 2 # 2 pending migrations
    conn.execute.assert_any_call("SELECT pg_advisory_unlock(1)")

@pytest.mark.asyncio
async def test_run_migrations_error(mock_pool):
    pool, conn = mock_pool
    conn.fetchval.return_value = 0 # All migrations pending
    
    # Make the first migration fail
    migrations_module.MIGRATIONS[0] = (1, "Test error", AsyncMock(side_effect=asyncpg.PostgresError("DB error")))
    
    with patch("app.migrations.get_pg_pool", return_value=pool):
        with pytest.raises(RuntimeError, match="Cannot start with incompatible schema"):
            await run_migrations()
            
    conn.execute.assert_any_call("SELECT pg_advisory_unlock(1)")

@pytest.mark.asyncio
async def test_migration_008(mock_pool):
    _, conn = mock_pool
    await _migration_008(conn)
    conn.execute.assert_called()

    # Test error handling inside the savepoint mock
    conn.execute.side_effect = asyncpg.UndefinedTableError("table not found")
    await _migration_008(conn) # Should not raise

@pytest.mark.asyncio
async def test_migration_009(mock_pool):
    _, conn = mock_pool
    conn.fetchval.return_value = 1536
    await _migration_009(conn)
    conn.execute.assert_called()
    
    conn.fetchval.return_value = None
    conn.execute.reset_mock()
    await _migration_009(conn)
    conn.execute.assert_not_called()

@pytest.mark.asyncio
async def test_migration_012(mock_pool):
    _, conn = mock_pool
    # Return different boolean values for fetchval to hit all branches
    conn.fetchval.side_effect = [
        True, True, # sender_id and sender exist
        True, False, # recipient_id exists, recipient does not
        "YES", # is_nullable
        True # index exists
    ]
    await _migration_012(conn)
    assert conn.execute.call_count > 5

@pytest.mark.asyncio
async def test_migration_013(mock_pool):
    _, conn = mock_pool
    await _migration_013(conn)
    assert conn.execute.call_count == 2
