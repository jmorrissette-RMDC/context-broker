-- Context Broker — PostgreSQL schema
-- Loaded automatically by postgres entrypoint on first run.
-- Requires: pgvector extension (pre-installed in pgvector/pgvector:pg16 image)

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- Schema versioning
-- Applied and checked at application startup.
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT NOW(),
    description TEXT
);

INSERT INTO schema_migrations (version, description)
VALUES (1, 'Initial schema')
ON CONFLICT (version) DO NOTHING;

-- ============================================================
-- conversations
-- Top-level conversation entity.
-- ============================================================

CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(500),
    flow_id VARCHAR(255),
    user_id VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    total_messages INTEGER DEFAULT 0,
    estimated_token_count INTEGER DEFAULT 0
);

CREATE INDEX idx_conversations_created ON conversations(created_at DESC);
CREATE INDEX idx_conversations_updated ON conversations(updated_at DESC);

-- ============================================================
-- conversation_messages
-- One row per message. Includes vector embedding and tsvector
-- for hybrid search (vector ANN + BM25 via RRF).
-- ============================================================

CREATE TABLE conversation_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    role VARCHAR(50) NOT NULL,
    sender VARCHAR(255) NOT NULL,
    recipient VARCHAR(255) NOT NULL,
    content TEXT,
    tool_calls JSONB,
    tool_call_id VARCHAR(255),
    token_count INTEGER,
    model_name VARCHAR(255),
    priority INTEGER DEFAULT 0,
    repeat_count INTEGER DEFAULT 1,
    embedding vector,
    sequence_number INTEGER NOT NULL,
    memory_extracted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    content_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(content, ''))
    ) STORED
);

CREATE UNIQUE INDEX idx_messages_conversation_seq_unique
    ON conversation_messages(conversation_id, sequence_number);

CREATE INDEX idx_messages_conversation
    ON conversation_messages(conversation_id, sequence_number ASC);

CREATE INDEX idx_messages_conversation_sender
    ON conversation_messages(conversation_id, sender, sequence_number DESC);

CREATE INDEX idx_messages_created ON conversation_messages(created_at DESC);

-- Vector similarity index is created by the application after the first
-- embedding is stored and the dimension is known. See migrations.py.
-- pgvector HNSW requires a typed vector column for index creation.

CREATE INDEX idx_messages_tsv
    ON conversation_messages
    USING GIN(content_tsv);

-- ============================================================
-- context_windows
-- Per-participant context window instances.
-- Scoped to a participant-conversation pair.
-- ============================================================

CREATE TABLE context_windows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    participant_id VARCHAR(255) NOT NULL,
    build_type VARCHAR(100) NOT NULL,
    max_token_budget INTEGER NOT NULL,
    last_assembled_at TIMESTAMP WITH TIME ZONE,
    last_accessed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_windows_conversation
    ON context_windows(conversation_id);

CREATE INDEX idx_windows_participant
    ON context_windows(participant_id);

CREATE INDEX idx_windows_build_type
    ON context_windows(build_type);

-- G5-08: Unique constraint for idempotent context window creation.
-- Prevents duplicate windows for the same (conversation, participant, build_type).
CREATE UNIQUE INDEX IF NOT EXISTS idx_windows_conv_participant_build
    ON context_windows(conversation_id, participant_id, build_type);

-- ============================================================
-- conversation_summaries
-- Tiered summaries keyed to context windows.
-- Tier 1 = archival (oldest, most compressed)
-- Tier 2 = chunk summaries (intermediate)
-- ============================================================

CREATE TABLE conversation_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    context_window_id UUID NOT NULL REFERENCES context_windows(id),
    summary_text TEXT NOT NULL,
    tier INTEGER NOT NULL CHECK (tier IN (1, 2)),
    summarizes_from_seq INTEGER NOT NULL,
    summarizes_to_seq INTEGER NOT NULL,
    message_count INTEGER,
    original_token_count INTEGER,
    summary_token_count INTEGER,
    summarized_by_model VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_summaries_window_active
    ON conversation_summaries(context_window_id, is_active, tier ASC, summarizes_from_seq ASC);

-- Prevent duplicate summary rows for the same window/tier/sequence range (M-08)
CREATE UNIQUE INDEX IF NOT EXISTS idx_summaries_window_tier_seq
    ON conversation_summaries(context_window_id, tier, summarizes_from_seq, summarizes_to_seq);

-- ============================================================
-- Mem0 deduplication index
-- Applied after Mem0 creates its own tables on first init.
-- The application attempts to create this index at startup.
-- ============================================================

-- Note: mem0_memories table is created by Mem0 on first use.
-- The application creates this index after Mem0 initializes.

-- ============================================================
-- System logs (Fluent Bit writes here)
-- Enables Imperator to query logs from all MAD containers.
-- ============================================================

CREATE TABLE IF NOT EXISTS system_logs (
    id              BIGSERIAL PRIMARY KEY,
    container_name  VARCHAR(255) NOT NULL,
    log_timestamp   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    level           VARCHAR(10),
    message         TEXT,
    raw_json        JSONB
);

CREATE INDEX IF NOT EXISTS idx_system_logs_container_time
    ON system_logs (container_name, log_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_system_logs_level
    ON system_logs (level, log_timestamp DESC);
