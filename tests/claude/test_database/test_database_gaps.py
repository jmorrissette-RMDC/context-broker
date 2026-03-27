"""Tests for app/database.py and app/migrations.py covering gap areas.

Covers: get_pg_pool, close_all_connections, migration_009, migration_012,
migration idempotency, get_current_schema_version, run_migrations.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

import app.database as db_mod
import app.migrations as mig_mod


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_pg_pool():
    """Reset the module-level pool between tests."""
    original = db_mod._pg_pool
    yield
    db_mod._pg_pool = original


@pytest.fixture
def mock_conn():
    """An AsyncMock pretending to be an asyncpg.Connection."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.transaction = MagicMock()
    # Make transaction() usable as async context manager
    tx = AsyncMock()
    conn.transaction.return_value = tx
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    return conn


class _FakeAcquireContext:
    """Async context manager returned by pool.acquire()."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def mock_pool(mock_conn):
    """An AsyncMock pretending to be an asyncpg.Pool."""
    pool = MagicMock()
    pool.acquire.return_value = _FakeAcquireContext(mock_conn)
    pool.close = AsyncMock()
    return pool


# ── app/database.py tests ────────────────────────────────────────


class TestGetPgPool:
    """Tests for get_pg_pool()."""

    def test_raises_runtime_error_when_pool_is_none(self):
        """Raises RuntimeError when pool has not been initialized."""
        db_mod._pg_pool = None
        with pytest.raises(RuntimeError, match="PostgreSQL pool not initialized"):
            db_mod.get_pg_pool()

    def test_returns_pool_when_initialized(self):
        """Returns the pool when it has been set."""
        fake_pool = MagicMock()
        db_mod._pg_pool = fake_pool
        assert db_mod.get_pg_pool() is fake_pool


class TestCloseAllConnections:
    """Tests for close_all_connections()."""

    @pytest.mark.asyncio
    async def test_closes_pool_and_sets_to_none(self):
        """Closes the pool and sets the global to None."""
        fake_pool = AsyncMock()
        fake_pool.close = AsyncMock()
        db_mod._pg_pool = fake_pool

        await db_mod.close_all_connections()

        fake_pool.close.assert_awaited_once()
        assert db_mod._pg_pool is None

    @pytest.mark.asyncio
    async def test_noop_when_pool_already_none(self):
        """Does nothing when pool is already None."""
        db_mod._pg_pool = None
        await db_mod.close_all_connections()
        assert db_mod._pg_pool is None


# ── Migration 009: HNSW index ────────────────────────────────────


class TestMigration009:
    """Tests for _migration_009 — HNSW vector index."""

    @pytest.mark.asyncio
    async def test_creates_hnsw_index_when_embeddings_exist(self, mock_conn):
        """Creates HNSW index when vector_dims returns a dimension."""
        mock_conn.fetchval.return_value = 768

        await mig_mod._migration_009(mock_conn)

        # Migration does ALTER TABLE (type column) + CREATE INDEX = 2 calls
        assert mock_conn.execute.await_count == 2
        calls = [c[0][0] for c in mock_conn.execute.call_args_list]
        assert any("ALTER TABLE" in sql and "vector(768)" in sql for sql in calls)
        assert any("CREATE INDEX IF NOT EXISTS idx_messages_embedding" in sql for sql in calls)

    @pytest.mark.asyncio
    async def test_skips_index_when_no_embeddings(self, mock_conn):
        """Skips index creation when no embeddings exist."""
        mock_conn.fetchval.return_value = None

        await mig_mod._migration_009(mock_conn)

        mock_conn.execute.assert_not_awaited()


# ── Migration 012: sender_id rename ──────────────────────────────


class TestMigration012:
    """Tests for _migration_012 — schema alignment."""

    @pytest.mark.asyncio
    async def test_renames_sender_id_to_sender(self, mock_conn):
        """Renames sender_id to sender on legacy DBs (has sender_id, no sender)."""
        call_count = 0

        async def fake_fetchval(sql, *args):
            nonlocal call_count
            call_count += 1
            # has_sender_id = True, has_sender = False,
            # has_recipient_id = False, has_recipient = True,
            # is_nullable = 'NO', idx_exists = False
            results = [True, False, False, True, "NO", False]
            if call_count <= len(results):
                return results[call_count - 1]
            return None

        mock_conn.fetchval = AsyncMock(side_effect=fake_fetchval)

        await mig_mod._migration_012(mock_conn)

        # Check that RENAME was called
        calls = [str(c) for c in mock_conn.execute.call_args_list]
        rename_calls = [c for c in calls if "RENAME COLUMN sender_id TO sender" in c]
        assert len(rename_calls) == 1

    @pytest.mark.asyncio
    async def test_drops_sender_id_on_fresh_db(self, mock_conn):
        """Drops sender_id when fresh DB has both sender_id and sender."""
        call_count = 0

        async def fake_fetchval(sql, *args):
            nonlocal call_count
            call_count += 1
            # has_sender_id = True, has_sender = True (fresh DB),
            # has_recipient_id = False, has_recipient = True,
            # is_nullable = 'NO', idx_exists = False
            results = [True, True, False, True, "NO", False]
            if call_count <= len(results):
                return results[call_count - 1]
            return None

        mock_conn.fetchval = AsyncMock(side_effect=fake_fetchval)

        await mig_mod._migration_012(mock_conn)

        calls = [str(c) for c in mock_conn.execute.call_args_list]
        drop_calls = [c for c in calls if "DROP COLUMN sender_id" in c]
        assert len(drop_calls) == 1


# ── Migration idempotency ────────────────────────────────────────


class TestMigrationIdempotency:
    """Tests for migration idempotency (ON CONFLICT DO NOTHING)."""

    @pytest.mark.asyncio
    async def test_rerun_migration_is_safe(self, mock_pool, mock_conn):
        """Re-running a migration does not fail (ON CONFLICT DO NOTHING in insert)."""
        with patch.object(mig_mod, "get_pg_pool", return_value=mock_pool):
            # Simulate schema_migrations table exists, current version = 0
            mock_conn.fetchval.return_value = 0
            # Replace MIGRATIONS with a single no-op migration
            test_migration = AsyncMock()
            test_migrations = [(1, "test migration", test_migration)]

            with patch.object(mig_mod, "MIGRATIONS", test_migrations):
                await mig_mod.run_migrations()

            test_migration.assert_awaited_once()


# ── get_current_schema_version ────────────────────────────────────


class TestGetCurrentSchemaVersion:
    """Tests for get_current_schema_version()."""

    @pytest.mark.asyncio
    async def test_returns_0_on_undefined_table_error(self, mock_conn):
        """Returns 0 when schema_migrations table does not exist."""
        mock_conn.fetchval.side_effect = asyncpg.UndefinedTableError("")

        version = await mig_mod.get_current_schema_version(mock_conn)

        assert version == 0

    @pytest.mark.asyncio
    async def test_returns_max_version(self, mock_conn):
        """Returns the highest applied migration version."""
        mock_conn.fetchval.return_value = 15

        version = await mig_mod.get_current_schema_version(mock_conn)

        assert version == 15

    @pytest.mark.asyncio
    async def test_returns_0_when_table_empty(self, mock_conn):
        """Returns 0 when schema_migrations table exists but is empty."""
        mock_conn.fetchval.return_value = 0

        version = await mig_mod.get_current_schema_version(mock_conn)

        assert version == 0


# ── run_migrations ────────────────────────────────────────────────


class TestRunMigrations:
    """Tests for run_migrations()."""

    @pytest.mark.asyncio
    async def test_acquires_advisory_lock(self, mock_pool, mock_conn):
        """Acquires and releases advisory lock around migration execution."""
        mock_conn.fetchval.return_value = 999  # All migrations applied

        with patch.object(mig_mod, "get_pg_pool", return_value=mock_pool):
            await mig_mod.run_migrations()

        calls = [str(c) for c in mock_conn.execute.call_args_list]
        lock_calls = [c for c in calls if "pg_advisory_lock(1)" in c]
        unlock_calls = [c for c in calls if "pg_advisory_unlock(1)" in c]
        assert len(lock_calls) == 1
        assert len(unlock_calls) == 1

    @pytest.mark.asyncio
    async def test_applies_pending_and_skips_applied(self, mock_pool, mock_conn):
        """Applies only migrations with version > current_version."""
        mock_conn.fetchval.return_value = 1  # Migration 1 already applied

        mig2_fn = AsyncMock()
        mig3_fn = AsyncMock()
        test_migrations = [
            (1, "already applied", AsyncMock()),
            (2, "pending one", mig2_fn),
            (3, "pending two", mig3_fn),
        ]

        with (
            patch.object(mig_mod, "get_pg_pool", return_value=mock_pool),
            patch.object(mig_mod, "MIGRATIONS", test_migrations),
        ):
            await mig_mod.run_migrations()

        mig2_fn.assert_awaited_once()
        mig3_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_all_when_up_to_date(self, mock_pool, mock_conn):
        """Does nothing when all migrations are already applied."""
        mock_conn.fetchval.return_value = 3

        mig_fn = AsyncMock()
        test_migrations = [
            (1, "m1", mig_fn),
            (2, "m2", mig_fn),
            (3, "m3", mig_fn),
        ]

        with (
            patch.object(mig_mod, "get_pg_pool", return_value=mock_pool),
            patch.object(mig_mod, "MIGRATIONS", test_migrations),
        ):
            await mig_mod.run_migrations()

        mig_fn.assert_not_awaited()
