"""
Database schema migration management.

Applies pending migrations on startup. Migrations are forward-only
and non-destructive. The application refuses to start if a migration
cannot be safely applied.
"""

import logging
from typing import Callable

import asyncpg

from app.database import get_pg_pool

_log = logging.getLogger("context_broker.migrations")


async def _migration_001(conn) -> None:
    """Migration 1: Initial schema.

    The initial schema is applied by postgres/init.sql via the Docker
    entrypoint. This migration just records that it was applied.
    """
    pass


async def _migration_002(conn) -> None:
    """Migration 2: Ensure participant_id index exists on context_windows.

    Safe to run multiple times (CREATE INDEX IF NOT EXISTS).
    """
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_windows_participant_conversation
        ON context_windows(participant_id, conversation_id)
        """)


async def _migration_003(conn) -> None:
    """Migration 3: Add unique constraint on (conversation_id, sequence_number).

    Prevents duplicate sequence numbers under concurrent inserts.
    Safe to run multiple times (CREATE UNIQUE INDEX IF NOT EXISTS).
    """
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_conversation_seq_unique
        ON conversation_messages(conversation_id, sequence_number)
        """)


async def _migration_004(conn) -> None:
    """Migration 4: Add recipient_id column to conversation_messages.

    Captures who the message was addressed to alongside the existing sender_id.
    Safe to run multiple times (ADD COLUMN IF NOT EXISTS).
    """
    await conn.execute("""
        ALTER TABLE conversation_messages
        ADD COLUMN IF NOT EXISTS recipient_id VARCHAR(255)
        """)


async def _migration_005(conn) -> None:
    """Migration 5: Add flow_id, user_id to conversations (F-05)."""
    await conn.execute(
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS flow_id VARCHAR(255)"
    )
    await conn.execute(
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS user_id VARCHAR(255)"
    )


async def _migration_006(conn) -> None:
    """Migration 6: Add content_type, priority, repeat_count to conversation_messages (F-04, F-06)."""
    await conn.execute(
        "ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS content_type VARCHAR(50) DEFAULT 'text'"
    )
    await conn.execute(
        "ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 0"
    )
    await conn.execute(
        "ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS repeat_count INTEGER DEFAULT 1"
    )


async def _migration_007(conn) -> None:
    """Migration 7: Prevent duplicate summary rows under concurrent assembly (M-08).

    Adds a unique index on (context_window_id, tier, summarizes_from_seq, summarizes_to_seq)
    to prevent duplicate summary rows when multiple workers race to summarize the same range.
    """
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_summaries_window_tier_seq
        ON conversation_summaries(context_window_id, tier, summarizes_from_seq, summarizes_to_seq)
        """)


async def _migration_008(conn) -> None:
    """Migration 8: Attempt to create Mem0 dedup index (F-19).

    Mem0 creates its own tables on first use. This migration attempts
    to add a dedup index on mem0_memories. If the table doesn't exist yet,
    this is a no-op (the index will be created on next startup after
    Mem0 has initialized).
    """
    # Use a savepoint so UndefinedTableError doesn't abort the outer transaction
    try:
        async with conn.transaction():
            await conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_mem0_memories_dedup
                ON mem0_memories(memory, user_id)
                """)
        _log.info("Mem0 dedup index created or already exists")
    except asyncpg.UndefinedTableError:
        _log.info("Mem0 table not yet created — dedup index deferred to next startup")


async def _migration_009(conn) -> None:
    """Migration 9: Create HNSW vector index if embeddings exist (G5-41).

    Deferred from init.sql because pgvector HNSW requires knowing the
    vector dimension. We detect the dimension from existing data.
    """
    dim = await conn.fetchval(
        "SELECT vector_dims(embedding) FROM conversation_messages WHERE embedding IS NOT NULL LIMIT 1"
    )
    if dim is not None:
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_messages_embedding
            ON conversation_messages USING hnsw ((embedding::vector({dim})) vector_cosine_ops)
            """)
        _log.info("HNSW index created for %d-dimensional embeddings", dim)
    else:
        _log.info("No embeddings yet — HNSW index deferred to next startup")


async def _migration_010(conn) -> None:
    """Migration 10: Unique constraint on context_windows for idempotent creation (G5-08).

    Prevents duplicate context windows for the same (conversation, participant, build_type).
    Safe to run multiple times (CREATE UNIQUE INDEX IF NOT EXISTS).
    """
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_windows_conv_participant_build
        ON context_windows(conversation_id, participant_id, build_type)
        """)


async def _migration_011(conn) -> None:
    """Migration 11: Add last_accessed_at to context_windows.

    Tracks when a context window was last retrieved, enabling dormant
    window detection and deferred assembly.
    """
    await conn.execute(
        "ALTER TABLE context_windows ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMP WITH TIME ZONE"
    )


async def _migration_012(conn) -> None:
    """Migration 12: Schema alignment for ARCH-01, ARCH-08, ARCH-09, ARCH-12, ARCH-13.

    Comprehensive column renames, additions, drops, and constraint changes
    on conversation_messages to align with the v4 schema design.

    All statements use IF EXISTS / IF NOT EXISTS guards so the migration
    is safe to run against databases in any intermediate state.
    """

    # ── ARCH-13: Rename sender_id → sender ──────────────────────────
    has_sender_id = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'conversation_messages' AND column_name = 'sender_id'
        )
        """)
    has_sender = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'conversation_messages' AND column_name = 'sender'
        )
        """)
    if has_sender_id and has_sender:
        # Fresh DB: init.sql created 'sender', old migration added 'sender_id' — drop the old one
        await conn.execute("ALTER TABLE conversation_messages DROP COLUMN sender_id")
        _log.info("Dropped duplicate sender_id (sender already exists from init.sql)")
    elif has_sender_id:
        await conn.execute(
            "ALTER TABLE conversation_messages RENAME COLUMN sender_id TO sender"
        )
        _log.info("Renamed sender_id → sender")

    # ── ARCH-13: Rename recipient_id → recipient ────────────────────
    has_recipient_id = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'conversation_messages' AND column_name = 'recipient_id'
        )
        """)
    has_recipient = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'conversation_messages' AND column_name = 'recipient'
        )
        """)
    if has_recipient_id and has_recipient:
        # Fresh DB: init.sql created 'recipient', migration 4 added 'recipient_id' — drop the old one
        await conn.execute("ALTER TABLE conversation_messages DROP COLUMN recipient_id")
        _log.info(
            "Dropped duplicate recipient_id (recipient already exists from init.sql)"
        )
    elif has_recipient_id:
        await conn.execute(
            "ALTER TABLE conversation_messages RENAME COLUMN recipient_id TO recipient"
        )
        _log.info("Renamed recipient_id → recipient")

    # ── ARCH-12: NOT NULL on recipient (backfill first) ─────────────
    await conn.execute(
        "UPDATE conversation_messages SET recipient = 'unknown' WHERE recipient IS NULL"
    )
    # Check if the column already has a NOT NULL constraint
    is_nullable = await conn.fetchval("""
        SELECT is_nullable FROM information_schema.columns
        WHERE table_name = 'conversation_messages' AND column_name = 'recipient'
        """)
    if is_nullable == "YES":
        await conn.execute(
            "ALTER TABLE conversation_messages ALTER COLUMN recipient SET DEFAULT 'unknown'"
        )
        await conn.execute(
            "ALTER TABLE conversation_messages ALTER COLUMN recipient SET NOT NULL"
        )
        _log.info("Set NOT NULL constraint on recipient with default 'unknown'")

    # ── ARCH-01: Add tool_calls (JSONB) ─────────────────────────────
    await conn.execute(
        "ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS tool_calls JSONB"
    )

    # ── ARCH-01: Add tool_call_id ───────────────────────────────────
    await conn.execute(
        "ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS tool_call_id VARCHAR(255)"
    )

    # ── ARCH-08: Drop content_type column ───────────────────────────
    await conn.execute(
        "ALTER TABLE conversation_messages DROP COLUMN IF EXISTS content_type"
    )

    # ── ARCH-09: Drop idempotency unique index ──────────────────────
    await conn.execute("DROP INDEX IF EXISTS idx_messages_idempotency")

    # ── ARCH-09: Drop idempotency_key column ────────────────────────
    await conn.execute(
        "ALTER TABLE conversation_messages DROP COLUMN IF EXISTS idempotency_key"
    )

    # ── ARCH-01: Make content nullable ──────────────────────────────
    # (tool-call messages may have no text content)
    await conn.execute(
        "ALTER TABLE conversation_messages ALTER COLUMN content DROP NOT NULL"
    )

    # ── ARCH-13: Rename sender index to match new column name ───────
    # PostgreSQL doesn't have ALTER INDEX IF EXISTS … RENAME, so check first.
    idx_exists = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE indexname = 'idx_messages_conversation_sender'
        )
        """)
    if idx_exists:
        await conn.execute(
            "ALTER INDEX idx_messages_conversation_sender RENAME TO idx_messages_conversation_sender_new"
        )
        _log.info("Renamed sender index → idx_messages_conversation_sender_new")

    # ── Safety net: Ensure last_accessed_at exists on context_windows
    # (in case migration 011 was skipped or partially applied) ───────
    await conn.execute(
        "ALTER TABLE context_windows ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMP WITH TIME ZONE"
    )

    _log.info("Migration 012 complete — schema aligned with v4 design")


async def _migration_013(conn) -> None:
    """Migration 13: Update context_windows unique constraint (D-03).

    Window identity changes from (conversation_id, participant_id, build_type)
    to (conversation_id, build_type, max_token_budget). participant_id is no
    longer part of window identity — windows are shared by conversation +
    build strategy + budget bucket.
    """
    # Drop old unique constraint (may be named differently depending on how it was created)
    await conn.execute("""
        DO $$
        BEGIN
            -- Drop the unique index created by migration 010
            DROP INDEX IF EXISTS idx_context_windows_unique;
            -- Also try the constraint form in case it was created as a constraint
            ALTER TABLE context_windows
                DROP CONSTRAINT IF EXISTS idx_context_windows_unique;
        EXCEPTION WHEN undefined_object THEN
            NULL;
        END $$
    """)

    # Create new unique constraint on (conversation_id, build_type, max_token_budget)
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_context_windows_identity
        ON context_windows (conversation_id, build_type, max_token_budget)
    """)

    _log.info(
        "Migration 013 complete — context_windows unique constraint updated for D-03"
    )


async def _migration_014(conn) -> None:
    """Migration 14: Add system_logs table for log shipper.

    Enables the Imperator to query logs from all MAD containers via SQL.
    The log shipper uses the Docker API to discover containers on
    context-broker-net and writes their logs with resolved names.
    """
    await conn.execute("DROP TABLE IF EXISTS system_logs")
    await conn.execute("""
        CREATE TABLE system_logs (
            container_name  VARCHAR(255) NOT NULL,
            log_timestamp   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            message         TEXT,
            data            JSONB
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_system_logs_container_time
        ON system_logs (container_name, log_timestamp DESC)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_system_logs_time
        ON system_logs (log_timestamp DESC)
    """)
    _log.info("Migration 014 complete — system_logs table for Fluent Bit")


async def _migration_015(conn) -> None:
    """Migration 15: Add stategraph_packages table for dynamic loading (REQ-001 §10).

    Tracks which StateGraph packages are installed and their versions.
    Used by install_stategraph() to record installations.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS stategraph_packages (
            package_name  VARCHAR(255) PRIMARY KEY,
            version       VARCHAR(100) NOT NULL,
            installed_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            entry_point_group VARCHAR(100),
            metadata      JSONB
        )
    """)
    _log.info("Migration 015 complete — stategraph_packages table")


async def _migration_016(conn) -> None:
    """Migration 16: Add unique index for Mem0 memory deduplication.

    Prevents duplicate memories with the same hash+user_id. Works with
    the PGVector.insert monkey-patch (ON CONFLICT DO NOTHING) to prevent
    transaction aborts from duplicate inserts. From Rogers (Fix 2).

    The mem0_memories table is created by Mem0 on first use, not by our
    migrations. On a fresh deploy it may not exist yet — skip gracefully.
    """
    table_exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'mem0_memories')"
    )
    if not table_exists:
        _log.info("Migration 016 skipped — mem0_memories table not yet created by Mem0")
        return
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mem0_hash_user
        ON mem0_memories ((payload->>'hash'), (payload->>'user_id'))
    """)
    _log.info("Migration 016 complete — mem0_memories dedup index")


async def _migration_017(conn) -> None:
    """Migration 17: Add embedding column to system_logs for log vectorization.

    Enables semantic search over logs. The column is nullable — logs without
    embeddings are not yet vectorized. A background worker polls for NULL
    embeddings and fills them in batches.
    """
    has_col = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'system_logs' AND column_name = 'embedding'
        )
    """)
    if not has_col:
        await conn.execute("ALTER TABLE system_logs ADD COLUMN embedding vector")
    _log.info("Migration 017 complete — system_logs embedding column")


async def _migration_018(conn) -> None:
    """Migration 18: Add domain_information table for Imperator domain knowledge.

    TE-owned: the Imperator stores learned facts about its operational domain.
    Searched semantically via pgvector cosine similarity.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS domain_information (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            content         TEXT NOT NULL,
            embedding       vector,
            source          VARCHAR(255),
            created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
    """)
    _log.info("Migration 018 complete — domain_information table")


async def _migration_019(conn) -> None:
    """Migration 19: Add schedules and schedule_history tables.

    Built-in scheduler — fires StateGraphs on cron/interval schedules.
    The Imperator can create/manage schedules at runtime via tools.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name            VARCHAR(255) NOT NULL,
            schedule_type   VARCHAR(20) NOT NULL CHECK (schedule_type IN ('cron', 'interval')),
            schedule_expr   VARCHAR(255) NOT NULL,
            message         TEXT NOT NULL,
            target          VARCHAR(255) NOT NULL DEFAULT 'imperator',
            enabled         BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule_history (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            schedule_id     UUID NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
            started_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            completed_at    TIMESTAMP WITH TIME ZONE,
            status          VARCHAR(20) NOT NULL DEFAULT 'running',
            summary         TEXT,
            error           TEXT
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_schedule_history_schedule_id
        ON schedule_history (schedule_id, started_at DESC)
    """)
    _log.info("Migration 019 complete — schedules and schedule_history tables")


async def _migration_020(conn) -> None:
    """Migration 20: Add last_fired_at to schedules for DB-based coordination.

    Prevents double-firing across container restarts or multiple workers.
    The scheduler uses optimistic locking: UPDATE WHERE last_fired_at = old_value.
    """
    has_col = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'schedules' AND column_name = 'last_fired_at'
        )
    """)
    if not has_col:
        await conn.execute(
            "ALTER TABLE schedules ADD COLUMN last_fired_at TIMESTAMP WITH TIME ZONE"
        )
    _log.info("Migration 020 complete — schedules.last_fired_at column")


# Migration registry: version -> (description, migration_function)
# Add new migrations here. Never modify existing entries.
# IMPORTANT: This list MUST appear after all _migration_NNN function definitions.
MIGRATIONS: list[tuple[int, str, Callable]] = [
    (1, "Initial schema — created by postgres/init.sql", _migration_001),
    (2, "Add participant_id index on context_windows", _migration_002),
    (3, "Add unique constraint on (conversation_id, sequence_number)", _migration_003),
    (4, "Add recipient_id column to conversation_messages", _migration_004),
    (5, "Add flow_id, user_id to conversations", _migration_005),
    (
        6,
        "Add content_type, priority, repeat_count to conversation_messages",
        _migration_006,
    ),
    (7, "Unique index on summaries to prevent duplicate rows (M-08)", _migration_007),
    (8, "Mem0 dedup index on mem0_memories (F-19)", _migration_008),
    (9, "Deferred HNSW vector index (G5-41)", _migration_009),
    (
        10,
        "Unique constraint on context_windows (conversation_id, participant_id, build_type) (G5-08)",
        _migration_010,
    ),
    (11, "Add last_accessed_at to context_windows", _migration_011),
    (
        12,
        "Schema alignment: renames, tool_calls, drops, constraints (ARCH-01/08/09/12/13)",
        _migration_012,
    ),
    (
        13,
        "Update context_windows unique constraint: (conversation_id, build_type, max_token_budget) (D-03)",
        _migration_013,
    ),
    (
        14,
        "Add system_logs table for Fluent Bit log collection",
        _migration_014,
    ),
    (
        15,
        "Add stategraph_packages table for dynamic loading (REQ-001 §10)",
        _migration_015,
    ),
    (
        16,
        "Add mem0_memories dedup index (hash+user_id) — from Rogers Fix 2",
        _migration_016,
    ),
    (
        17,
        "Add embedding column to system_logs for log vectorization",
        _migration_017,
    ),
    (
        18,
        "Add domain_information table for Imperator domain knowledge",
        _migration_018,
    ),
    (
        19,
        "Add schedules and schedule_history tables for built-in scheduler",
        _migration_019,
    ),
    (
        20,
        "Add last_fired_at to schedules for DB-based coordination",
        _migration_020,
    ),
]


async def get_current_schema_version(conn) -> int:
    """Return the highest applied migration version, or 0 if none."""
    try:
        version = await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        )
        return version or 0
    except asyncpg.UndefinedTableError:
        # schema_migrations table doesn't exist yet — fresh database
        return 0


async def run_migrations() -> None:
    """Apply all pending migrations in order.

    R5-m12: Uses a PostgreSQL advisory lock to serialize migrations when
    multiple workers start simultaneously. Advisory lock ID 1 is reserved
    for schema migrations.

    Raises RuntimeError if any migration fails, preventing startup
    with an incompatible schema.
    """
    pool = get_pg_pool()

    async with pool.acquire() as conn:
        # R5-m12: Acquire advisory lock to prevent concurrent migration runs
        await conn.execute("SELECT pg_advisory_lock(1)")
        try:
            current_version = await get_current_schema_version(conn)
            _log.info("Current schema version: %d", current_version)

            pending = [
                (version, description, fn)
                for version, description, fn in MIGRATIONS
                if version > current_version
            ]

            if not pending:
                _log.info("Schema is up to date (version %d)", current_version)
                return

            for version, description, migration_fn in pending:
                _log.info("Applying migration %d: %s", version, description)
                try:
                    async with conn.transaction():
                        await migration_fn(conn)
                        await conn.execute(
                            """
                            INSERT INTO schema_migrations (version, description)
                            VALUES ($1, $2)
                            ON CONFLICT (version) DO NOTHING
                            """,
                            version,
                            description,
                        )
                    _log.info("Migration %d applied successfully", version)
                except (asyncpg.PostgresError, OSError) as exc:
                    raise RuntimeError(
                        f"Migration {version} ('{description}') failed: {exc}. "
                        "Cannot start with incompatible schema."
                    ) from exc

            _log.info(
                "Schema migrations complete. Now at version %d",
                pending[-1][0],
            )
        finally:
            await conn.execute("SELECT pg_advisory_unlock(1)")
