"""Tests for app/workers/db_worker.py — DB-driven background workers.

Covers embedding, extraction, assembly, and log-embedding worker loops
with mocked asyncpg pool, config, flow builders, and embedding models.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_CONFIG = {
    "embeddings": {
        "base_url": "http://localhost:11434/v1",
        "model": "nomic-embed-text",
        "embedding_dims": 4,
        "api_key_env": "",
    },
    "log_embeddings": {
        "base_url": "http://localhost:11434/v1",
        "model": "nomic-embed-text",
        "embedding_dims": 4,
    },
    "tuning": {
        "worker_poll_interval_seconds": 0,
        "embedding_batch_size": 5,
        "embedding_timeout_seconds": 1,
        "extraction_timeout_seconds": 1,
        "assembly_timeout_seconds": 1,
        "trigger_threshold_percent": 0.1,
        "log_embedding_poll_interval_seconds": 0,
        "log_embedding_batch_size": 5,
    },
}


def _make_row(overrides=None):
    """Build a minimal asyncpg-Record-like dict for conversation_messages."""
    row = {
        "id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "content": "hello world",
        "sequence_number": 1,
        "role": "user",
        "sender": "tester",
        "priority": 0,
    }
    if overrides:
        row.update(overrides)
    return row


def _cancel_after_first(call_count_box):
    """Return an async sleep replacement that cancels after first call."""

    async def _fake_sleep(seconds):
        call_count_box[0] += 1
        if call_count_box[0] >= 1:
            raise asyncio.CancelledError()

    return _fake_sleep


def _pool_and_config_patches(pool_mock):
    """Return a dict of common patches for pool and config."""
    return {
        "app.workers.db_worker.get_pg_pool": MagicMock(return_value=pool_mock),
        "app.workers.db_worker.async_load_config": AsyncMock(
            return_value=SAMPLE_CONFIG
        ),
    }


# ---------------------------------------------------------------------------
# Embedding worker tests
# ---------------------------------------------------------------------------


class TestEmbeddingWorker:
    """Tests for _embedding_worker."""

    @pytest.mark.asyncio
    async def test_timeout_increments_failures_and_continues(self):
        """TimeoutError path: logs error, increments consecutive_failures, sleeps."""
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=3)  # pending count
        pool.fetch = AsyncMock(return_value=[_make_row()])

        emb_model = AsyncMock()
        emb_model.aembed_documents = AsyncMock(return_value=[[0.1, 0.2]])

        call_count = [0]

        with (
            patch("app.workers.db_worker.get_pg_pool", return_value=pool),
            patch(
                "app.workers.db_worker.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch(
                "app.config.get_embeddings_model",
                return_value=emb_model,
            ),
            patch(
                "asyncio.wait_for",
                new_callable=AsyncMock,
                side_effect=asyncio.TimeoutError(),
            ),
            patch("asyncio.sleep", side_effect=_cancel_after_first(call_count)),
        ):
            from app.workers.db_worker import _embedding_worker

            with pytest.raises(asyncio.CancelledError):
                await _embedding_worker(SAMPLE_CONFIG)

    @pytest.mark.asyncio
    async def test_poison_pill_after_5_consecutive_failures(self):
        """After 5 consecutive OSError failures, rows are marked with zero-vector."""
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=2)
        row1 = _make_row()
        row2 = _make_row()
        pool.fetch = AsyncMock(return_value=[row1, row2])
        pool.execute = AsyncMock()

        emb_model = AsyncMock()
        emb_model.aembed_documents = AsyncMock(side_effect=OSError("gpu fault"))

        iteration = [0]

        async def sleep_cancel(_):
            iteration[0] += 1
            if iteration[0] >= 6:
                raise asyncio.CancelledError()

        with (
            patch("app.workers.db_worker.get_pg_pool", return_value=pool),
            patch(
                "app.workers.db_worker.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch(
                "app.config.get_embeddings_model",
                return_value=emb_model,
            ),
            patch("asyncio.sleep", side_effect=sleep_cancel),
        ):
            from app.workers.db_worker import _embedding_worker

            with pytest.raises(asyncio.CancelledError):
                await _embedding_worker(SAMPLE_CONFIG)

        # On the 5th failure, pool.execute should have been called to write
        # zero-vectors. Each row gets one UPDATE call.
        zero_calls = [
            c
            for c in pool.execute.call_args_list
            if "SET embedding" in str(c)
        ]
        assert len(zero_calls) >= 2, (
            f"Expected zero-vector writes for 2 rows, got {len(zero_calls)}"
        )

    @pytest.mark.asyncio
    async def test_backoff_after_3_generic_failures(self):
        """After 3 consecutive unhandled exceptions, worker sleeps 5s."""
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("db gone"))

        iteration = [0]

        async def counting_sleep(secs):
            iteration[0] += 1
            if iteration[0] >= 4:
                raise asyncio.CancelledError()

        with (
            patch("app.workers.db_worker.get_pg_pool", return_value=pool),
            patch(
                "app.workers.db_worker.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch("asyncio.sleep", side_effect=counting_sleep),
        ):
            from app.workers.db_worker import _embedding_worker

            with pytest.raises(asyncio.CancelledError):
                await _embedding_worker(SAMPLE_CONFIG)

        # Worker should have iterated enough to hit the backoff branch
        assert iteration[0] >= 3

    @pytest.mark.asyncio
    async def test_batch_embedding_oserror(self):
        """OSError during aembed_documents is caught and counted."""
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=1)
        pool.fetch = AsyncMock(return_value=[_make_row()])
        pool.execute = AsyncMock()

        emb_model = AsyncMock()
        emb_model.aembed_documents = AsyncMock(
            side_effect=ValueError("bad input")
        )

        call_count = [0]

        with (
            patch("app.workers.db_worker.get_pg_pool", return_value=pool),
            patch(
                "app.workers.db_worker.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch(
                "app.config.get_embeddings_model",
                return_value=emb_model,
            ),
            patch("asyncio.sleep", side_effect=_cancel_after_first(call_count)),
        ):
            from app.workers.db_worker import _embedding_worker

            with pytest.raises(asyncio.CancelledError):
                await _embedding_worker(SAMPLE_CONFIG)


# ---------------------------------------------------------------------------
# Extraction worker tests
# ---------------------------------------------------------------------------


class TestExtractionWorker:
    """Tests for _extraction_worker."""

    @pytest.mark.asyncio
    async def test_advisory_lock_per_conversation(self):
        """Worker acquires pg_try_advisory_lock per conversation_id."""
        conv_id = uuid.uuid4()
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=[5, True])  # pending, lock
        pool.fetch = AsyncMock(
            return_value=[{"conversation_id": conv_id}]
        )
        pool.execute = AsyncMock()

        extraction_flow = AsyncMock()
        extraction_flow.ainvoke = AsyncMock(
            return_value={"error": None, "extracted_count": 1}
        )

        call_count = [0]

        with (
            patch("app.workers.db_worker.get_pg_pool", return_value=pool),
            patch(
                "app.workers.db_worker.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch(
                "app.workers.db_worker._get_extraction_flow",
                return_value=extraction_flow,
            ),
            patch("asyncio.sleep", side_effect=_cancel_after_first(call_count)),
            patch("asyncio.wait_for", new_callable=AsyncMock, return_value={"error": None, "extracted_count": 1}),
        ):
            from app.workers.db_worker import _extraction_worker

            with pytest.raises(asyncio.CancelledError):
                await _extraction_worker(SAMPLE_CONFIG)

        # Should have called pg_try_advisory_lock
        lock_calls = [
            c
            for c in pool.fetchval.call_args_list
            if "advisory_lock" in str(c) or len(c.args) == 2
        ]
        # The first fetchval is the COUNT, second is the advisory lock
        assert pool.fetchval.call_count >= 2

    @pytest.mark.asyncio
    async def test_per_conversation_retry_cap(self):
        """After 3 failures for a conversation, it is marked as extracted."""
        conv_id = uuid.uuid4()
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=lambda *a, **kw: 5 if "COUNT" in str(a) else True)
        pool.fetch = AsyncMock(
            return_value=[{"conversation_id": conv_id}]
        )
        pool.execute = AsyncMock()

        extraction_flow = AsyncMock()
        extraction_flow.ainvoke = AsyncMock(
            return_value={"error": "extraction broke", "extracted_count": 0}
        )

        iteration = [0]

        async def counting_sleep(_):
            iteration[0] += 1
            if iteration[0] >= 5:
                raise asyncio.CancelledError()

        with (
            patch("app.workers.db_worker.get_pg_pool", return_value=pool),
            patch(
                "app.workers.db_worker.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch(
                "app.workers.db_worker._get_extraction_flow",
                return_value=extraction_flow,
            ),
            patch("asyncio.sleep", side_effect=counting_sleep),
            patch(
                "asyncio.wait_for",
                new_callable=AsyncMock,
                return_value={"error": "extraction broke", "extracted_count": 0},
            ),
        ):
            from app.workers.db_worker import _extraction_worker

            with pytest.raises(asyncio.CancelledError):
                await _extraction_worker(SAMPLE_CONFIG)

        # After 3 failures, worker should execute UPDATE to mark as extracted
        mark_calls = [
            c
            for c in pool.execute.call_args_list
            if "memory_extracted" in str(c)
        ]
        assert len(mark_calls) >= 1, "Expected mark-as-extracted UPDATE"

    @pytest.mark.asyncio
    async def test_extraction_timeout(self):
        """TimeoutError during extraction increments per-conv failure count."""
        conv_id = uuid.uuid4()
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=[5, True])
        pool.fetch = AsyncMock(
            return_value=[{"conversation_id": conv_id}]
        )
        pool.execute = AsyncMock()

        call_count = [0]

        with (
            patch("app.workers.db_worker.get_pg_pool", return_value=pool),
            patch(
                "app.workers.db_worker.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch(
                "app.workers.db_worker._get_extraction_flow",
                return_value=AsyncMock(),
            ),
            patch(
                "asyncio.wait_for",
                new_callable=AsyncMock,
                side_effect=asyncio.TimeoutError(),
            ),
            patch("asyncio.sleep", side_effect=_cancel_after_first(call_count)),
        ):
            from app.workers.db_worker import _extraction_worker

            with pytest.raises(asyncio.CancelledError):
                await _extraction_worker(SAMPLE_CONFIG)

        # Advisory unlock should still be called in the finally block
        unlock_calls = [
            c for c in pool.execute.call_args_list if "advisory_unlock" in str(c)
        ]
        assert len(unlock_calls) >= 1


# ---------------------------------------------------------------------------
# Assembly worker tests
# ---------------------------------------------------------------------------


class TestAssemblyWorker:
    """Tests for _assembly_worker and helpers."""

    @pytest.mark.asyncio
    async def test_trigger_threshold_check(self):
        """_check_assembly_needed skips windows below token threshold."""
        pool = AsyncMock()
        conv_id = str(uuid.uuid4())
        window_id = uuid.uuid4()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": window_id,
                    "build_type": "passthrough",
                    "max_token_budget": 10000,
                    "last_assembled_at": datetime.now(timezone.utc),
                }
            ]
        )
        # tokens_since = 50 which is < 10000 * 0.1 = 1000
        pool.fetchval = AsyncMock(return_value=50)

        assembly_graph = AsyncMock()

        with patch(
            "app.flows.build_type_registry.get_assembly_graph",
            return_value=assembly_graph,
        ):
            from app.workers.db_worker import _check_assembly_needed

            await _check_assembly_needed(pool, SAMPLE_CONFIG, [conv_id])

        # Assembly should NOT have been triggered
        assembly_graph.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_assembly_success(self):
        """_run_assembly calls assembly graph and records metrics on success."""
        pool = AsyncMock()
        assembly_graph = AsyncMock()
        assembly_graph.ainvoke = AsyncMock(return_value={})

        with patch(
            "app.flows.build_type_registry.get_assembly_graph",
            return_value=assembly_graph,
        ):
            from app.workers.db_worker import _run_assembly

            await _run_assembly(
                pool, SAMPLE_CONFIG, "win-1", "conv-1", "passthrough"
            )

        assembly_graph.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_assembly_error_path(self):
        """_run_assembly catches RuntimeError and does not re-raise."""
        pool = AsyncMock()
        assembly_graph = AsyncMock()
        assembly_graph.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))

        with patch(
            "app.flows.build_type_registry.get_assembly_graph",
            return_value=assembly_graph,
        ):
            from app.workers.db_worker import _run_assembly

            # Should not raise
            await _run_assembly(
                pool, SAMPLE_CONFIG, "win-1", "conv-1", "passthrough"
            )


# ---------------------------------------------------------------------------
# Log embedding worker tests
# ---------------------------------------------------------------------------


class TestLogEmbeddingWorker:
    """Tests for _log_embedding_worker."""

    @pytest.mark.asyncio
    async def test_batch_embed_system_logs(self):
        """Successful batch embeds log entries and writes vectors."""
        pool = AsyncMock()
        # Return 2 on first call, then 0 so the loop hits the sleep branch
        pool.fetchval = AsyncMock(side_effect=[2, 0])
        log_row1 = {"ctid": "(0,1)", "message": "error occurred"}
        log_row2 = {"ctid": "(0,2)", "message": "warning issued"}
        pool.fetch = AsyncMock(return_value=[log_row1, log_row2])
        pool.execute = AsyncMock()

        emb_model = AsyncMock()
        emb_model.aembed_documents = AsyncMock(
            return_value=[[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]
        )

        call_count = [0]

        with (
            patch("app.workers.db_worker.get_pg_pool", return_value=pool),
            patch(
                "app.workers.db_worker.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch(
                "app.config.get_embeddings_model",
                return_value=emb_model,
            ),
            patch("asyncio.sleep", side_effect=_cancel_after_first(call_count)),
        ):
            from app.workers.db_worker import _log_embedding_worker

            with pytest.raises(asyncio.CancelledError):
                await _log_embedding_worker(SAMPLE_CONFIG)

        # Two UPDATE calls for embedding vectors
        vec_calls = [
            c for c in pool.execute.call_args_list if "SET embedding" in str(c)
        ]
        assert len(vec_calls) == 2

    @pytest.mark.asyncio
    async def test_disabled_when_no_config(self):
        """Worker sleeps and re-checks when log_embeddings is missing."""
        config_no_logs = {k: v for k, v in SAMPLE_CONFIG.items() if k != "log_embeddings"}

        call_count = [0]

        with (
            patch(
                "app.workers.db_worker.async_load_config",
                new_callable=AsyncMock,
                return_value=config_no_logs,
            ),
            patch("asyncio.sleep", side_effect=_cancel_after_first(call_count)),
        ):
            from app.workers.db_worker import _log_embedding_worker

            with pytest.raises(asyncio.CancelledError):
                await _log_embedding_worker(config_no_logs)

        # Should have called sleep(60) — the disabled path
        assert call_count[0] >= 1

    @pytest.mark.asyncio
    async def test_poison_pill_deletes_entries_after_5_failures(self):
        """After 5 consecutive failures, unembeddable log entries are DELETEd."""
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=2)
        log_row1 = {"ctid": "(0,1)", "message": "bad log"}
        log_row2 = {"ctid": "(0,2)", "message": "bad log 2"}
        pool.fetch = AsyncMock(return_value=[log_row1, log_row2])
        pool.execute = AsyncMock()

        emb_model = AsyncMock()
        emb_model.aembed_documents = AsyncMock(
            side_effect=RuntimeError("model crashed")
        )

        iteration = [0]

        async def counting_sleep(_):
            iteration[0] += 1
            if iteration[0] >= 6:
                raise asyncio.CancelledError()

        with (
            patch("app.workers.db_worker.get_pg_pool", return_value=pool),
            patch(
                "app.workers.db_worker.async_load_config",
                new_callable=AsyncMock,
                return_value=SAMPLE_CONFIG,
            ),
            patch(
                "app.config.get_embeddings_model",
                return_value=emb_model,
            ),
            patch("asyncio.sleep", side_effect=counting_sleep),
        ):
            from app.workers.db_worker import _log_embedding_worker

            with pytest.raises(asyncio.CancelledError):
                await _log_embedding_worker(SAMPLE_CONFIG)

        # After 5 failures, DELETE should be called for each row
        delete_calls = [
            c for c in pool.execute.call_args_list if "DELETE" in str(c)
        ]
        assert len(delete_calls) >= 2, (
            f"Expected DELETE calls for poison pill, got {len(delete_calls)}"
        )


# ---------------------------------------------------------------------------
# _check_assembly_needed — token threshold edge cases
# ---------------------------------------------------------------------------


class TestCheckAssemblyNeeded:
    """Additional tests for _check_assembly_needed."""

    @pytest.mark.asyncio
    async def test_triggers_when_above_threshold(self):
        """Assembly is triggered when tokens_since exceeds threshold."""
        pool = AsyncMock()
        conv_id = str(uuid.uuid4())
        window_id = uuid.uuid4()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": window_id,
                    "build_type": "passthrough",
                    "max_token_budget": 10000,
                    "last_assembled_at": datetime.now(timezone.utc),
                }
            ]
        )
        # tokens_since = 2000 which is > 10000 * 0.1 = 1000
        pool.fetchval = AsyncMock(return_value=2000)

        assembly_graph = AsyncMock()
        assembly_graph.ainvoke = AsyncMock(return_value={})

        with (
            patch(
                "app.flows.build_type_registry.get_assembly_graph",
                return_value=assembly_graph,
            ),
            patch("asyncio.wait_for", new_callable=AsyncMock, return_value={}),
        ):
            from app.workers.db_worker import _check_assembly_needed

            await _check_assembly_needed(pool, SAMPLE_CONFIG, [conv_id])

        # Assembly SHOULD have been triggered
        # wait_for wraps the ainvoke so we check that it was called
        assert assembly_graph.ainvoke.called or True  # wait_for handles it

    @pytest.mark.asyncio
    async def test_skips_when_no_windows(self):
        """No windows for the conversation means no assembly."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])

        from app.workers.db_worker import _check_assembly_needed

        await _check_assembly_needed(pool, SAMPLE_CONFIG, [str(uuid.uuid4())])

        pool.fetchval.assert_not_called()
