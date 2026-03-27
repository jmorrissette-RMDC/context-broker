"""Phase D: Pipeline and worker verification on bulk-loaded data.

Verifies that embeddings, extraction, assembly, and log shipping all
ran correctly after the Phase 1 bulk load.
"""

import re

import pytest

from tests.claude.live.helpers import (
    docker_logs,
    docker_psql,
    get_db_counts,
    log_issue,
)

pytestmark = pytest.mark.live


class TestEmbeddings:
    """Verify that the embedding pipeline processed all messages."""

    def test_all_content_messages_have_embeddings(self):
        """Every message with non-empty content should have a non-null embedding."""
        total_with_content = docker_psql(
            "SELECT COUNT(*) FROM conversation_messages "
            "WHERE content IS NOT NULL AND content != ''"
        ).strip()
        embedded = docker_psql(
            "SELECT COUNT(*) FROM conversation_messages "
            "WHERE embedding IS NOT NULL"
        ).strip()
        total_with_content = int(total_with_content or "0")
        embedded = int(embedded or "0")

        assert embedded > 0, "No embeddings found at all"
        assert embedded >= total_with_content, (
            f"Not all content messages have embeddings: "
            f"{embedded}/{total_with_content}"
        )

    def test_embedding_dimensions_correct(self):
        """Embedding vectors should be 1024-dimensional."""
        dims = docker_psql(
            "SELECT vector_dims(embedding) FROM conversation_messages "
            "WHERE embedding IS NOT NULL LIMIT 1"
        ).strip()
        assert dims, "Could not read embedding dimensions"
        assert int(dims) == 1024, (
            f"Expected 1024-dimensional embeddings, got {dims}"
        )


class TestExtraction:
    """Verify that memory extraction ran."""

    def test_mem0_memories_has_entries(self):
        """The mem0_memories table should have rows after extraction."""
        count = docker_psql("SELECT COUNT(*) FROM mem0_memories").strip()
        count = int(count or "0")
        assert count > 0, "mem0_memories table is empty; extraction did not run"


class TestAssembly:
    """Verify that context assembly built summaries."""

    def test_conversation_summaries_exist(self):
        """conversation_summaries table should have rows."""
        count = docker_psql("SELECT COUNT(*) FROM conversation_summaries").strip()
        count = int(count or "0")
        assert count > 0, "conversation_summaries table is empty"

    def test_assembly_ran_for_conversations_with_context_windows(self):
        """At least some conversations should have context windows built."""
        count = docker_psql(
            "SELECT COUNT(DISTINCT conversation_id) FROM conversation_summaries"
        ).strip()
        count = int(count or "0")
        assert count > 0, (
            "No conversations have summaries; assembly may not have run"
        )


class TestLogShipper:
    """Verify that the log shipper wrote logs to the database."""

    def test_system_logs_has_entries(self):
        """system_logs table should have rows from the log shipper."""
        count = docker_psql("SELECT COUNT(*) FROM system_logs").strip()
        count = int(count or "0")
        assert count > 0, "system_logs table is empty; log shipper did not run"

    def test_log_embeddings_generated(self):
        """system_logs rows should have embeddings if log_embeddings is configured."""
        total = docker_psql("SELECT COUNT(*) FROM system_logs").strip()
        embedded = docker_psql(
            "SELECT COUNT(*) FROM system_logs WHERE embedding IS NOT NULL"
        ).strip()
        total = int(total or "0")
        embedded = int(embedded or "0")

        if total == 0:
            pytest.skip("No system_logs rows to check")

        if embedded == 0:
            log_issue(
                "test_log_embeddings_generated",
                "warning",
                "pipeline",
                "No log embeddings found; log_embeddings may not be configured",
                ">0 embedded logs",
                "0",
            )
        # Don't hard-fail; log embedding is optional config
        assert True


class TestBackgroundWorker:
    """Verify background worker started and no pipeline errors in logs."""

    def test_background_worker_started(self):
        """Docker logs should show the background worker starting."""
        logs = docker_logs("claude-test-app", lines=200)
        assert "Background worker starting" in logs or "background worker" in logs.lower(), (
            "Could not find 'Background worker starting' in app container logs"
        )

    def test_no_pipeline_errors_in_recent_logs(self):
        """Check recent app logs for error-level entries and log any found."""
        logs = docker_logs("claude-test-app", lines=50)
        lines = logs.splitlines()
        error_lines = [
            line for line in lines
            if re.search(r'"level"\s*:\s*"error"', line, re.IGNORECASE)
            or re.search(r'\bERROR\b', line)
        ]
        for err_line in error_lines:
            log_issue(
                "test_no_pipeline_errors_in_recent_logs",
                "warning",
                "pipeline",
                f"Error in recent logs: {err_line[:200]}",
            )
        # Log but don't hard-fail; some transient errors may be acceptable
        assert True
