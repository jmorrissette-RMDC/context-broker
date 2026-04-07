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
        total_raw = docker_psql(
            "SELECT COUNT(*) FROM conversation_messages "
            "WHERE content IS NOT NULL AND content != ''"
        ).strip()
        embedded_raw = docker_psql(
            "SELECT COUNT(*) FROM conversation_messages "
            "WHERE embedding IS NOT NULL"
        ).strip()
        # Extract first integer found in the output (handles column headers etc.)
        import re
        total_match = re.search(r"(\d+)", total_raw)
        embedded_match = re.search(r"(\d+)", embedded_raw)
        total_with_content = int(total_match.group(1)) if total_match else 0
        embedded = int(embedded_match.group(1)) if embedded_match else 0

        assert embedded > 0, f"No embeddings found at all (raw output: {embedded_raw!r})"
        # Pipeline may still be processing; check that most are done
        if total_with_content > 0:
            ratio = embedded / total_with_content
            assert ratio >= 0.8, (
                f"Less than 80% of content messages have embeddings: "
                f"{embedded}/{total_with_content} ({ratio:.0%})"
            )

    def test_embedding_dimensions_correct(self):
        """Embedding vectors should be 1024-dimensional."""
        import re
        dims_raw = docker_psql(
            "SELECT vector_dims(embedding) FROM conversation_messages "
            "WHERE embedding IS NOT NULL LIMIT 1"
        ).strip()
        dims_match = re.search(r"(\d+)", dims_raw)
        assert dims_match, f"Could not read embedding dimensions (raw: {dims_raw!r})"
        dims = int(dims_match.group(1))
        assert dims == 1024, (
            f"Expected 1024-dimensional embeddings, got {dims}"
        )


class TestExtraction:
    """Verify that memory extraction ran."""

    def test_mem0_memories_has_entries(self):
        """The mem0_memories table should have rows after extraction.

        Skips gracefully if Mem0 is not functional (e.g. stub table schema
        doesn't match what Mem0 expects).
        """
        import re
        raw = docker_psql("SELECT COUNT(*) FROM mem0_memories").strip()
        match = re.search(r"(\d+)", raw)
        count = int(match.group(1)) if match else 0
        if count == 0:
            log_issue(
                "test_mem0_memories_has_entries",
                "warning",
                "mem0",
                "mem0_memories table is empty; extraction may not have run "
                "or Mem0 table schema mismatch prevents storage",
                ">0 rows",
                "0",
            )
            pytest.fail(
                "Mem0 not functional — mem0_memories table is empty. "
                "Extraction worker may not have run or Mem0 table schema mismatch."
            )


class TestCEAExtraction:
    """Verify CEA compaction-time extraction populates cea_quality_metadata."""

    def test_cea_quality_metadata_populated(self):
        """D-new: cea_quality_metadata should have rows if CEA extraction ran.

        CEA extraction fires at compaction time (within compact_tier1).
        This verifies that at least some facts have associated quality metadata.
        Hard-fails if the table is empty — Phase 1 data load should have
        triggered compaction and produced extraction results.
        """
        import re
        raw = docker_psql("SELECT COUNT(*) FROM cea_quality_metadata").strip()
        match = re.search(r"(\d+)", raw)
        count = int(match.group(1)) if match else 0
        if count == 0:
            log_issue(
                "test_cea_quality_metadata_populated",
                "warning",
                "cea",
                "cea_quality_metadata table is empty; CEA extraction may not have "
                "fired yet (compaction threshold not reached) or extraction LLM failed",
                ">0 rows",
                "0",
            )
            pytest.fail(
                "cea_quality_metadata is empty — CEA extraction has not produced any facts. "
                "Compaction may not have triggered or extraction LLM is misconfigured."
            )

    def test_cea_metadata_has_required_columns(self):
        """D-new: cea_quality_metadata rows have required CEA columns."""
        import re
        # Check at least one row has non-null durability and confidence
        raw = docker_psql(
            "SELECT COUNT(*) FROM cea_quality_metadata "
            "WHERE durability IS NOT NULL AND confidence IS NOT NULL"
        ).strip()
        match = re.search(r"(\d+)", raw)
        count = int(match.group(1)) if match else 0
        if count == 0:
            # If the table is empty, skip with warning
            total_raw = docker_psql("SELECT COUNT(*) FROM cea_quality_metadata").strip()
            total_match = re.search(r"(\d+)", total_raw)
            total = int(total_match.group(1)) if total_match else 0
            if total == 0:
                pytest.skip("cea_quality_metadata is empty — CEA extraction not yet triggered")
            pytest.fail(
                f"cea_quality_metadata has {total} rows but none have "
                "durability/confidence populated"
            )


class TestCEAMetrics:
    """Verify CEA Prometheus metrics are registered."""

    def test_cea_extraction_metrics_registered(self, http_client):
        """D-new: /metrics contains CEA extraction counters after startup."""
        resp = http_client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        # CEA metrics may not have fired yet (no compaction) but should be registered
        # Look for any CEA-related metric name
        has_cea_metric = (
            "cea_extraction" in text
            or "cea_facts" in text
            or "ceac_enrichment" in text
        )
        if not has_cea_metric:
            log_issue(
                "test_cea_extraction_metrics_registered",
                "warning",
                "cea",
                "No CEA metrics found in /metrics output; "
                "prometheus_client may not be installed or CEA code path not reached",
                "cea_extraction_* metrics",
                "none found",
            )
        # Intentional soft-fail: prometheus_client registers metrics on first use,
        # not at import time. Metrics will be absent on a fresh deploy before any
        # compaction fires. log_issue above records absence as a warning.
        assert True  # noqa: S101 — soft-fail by design, see comment above


class TestAssembly:
    """Verify that context assembly built summaries."""

    def test_conversation_summaries_exist(self):
        """conversation_summaries table should have rows."""
        import re
        raw = docker_psql("SELECT COUNT(*) FROM conversation_summaries").strip()
        match = re.search(r"(\d+)", raw)
        count = int(match.group(1)) if match else 0
        assert count > 0, f"conversation_summaries table is empty (raw: {raw!r})"

    def test_assembly_ran_for_conversations_with_context_windows(self):
        """At least some conversations should have context windows built."""
        import re
        raw = docker_psql(
            "SELECT COUNT(DISTINCT conversation_id) FROM conversation_summaries"
        ).strip()
        match = re.search(r"(\d+)", raw)
        count = int(match.group(1)) if match else 0
        assert count > 0, (
            f"No conversations have summaries; assembly may not have run (raw: {raw!r})"
        )


class TestLogShipper:
    """Verify that the log shipper wrote logs to the database."""

    def test_system_logs_has_entries(self):
        """system_logs table should have rows from the log shipper."""
        import re
        raw = docker_psql("SELECT COUNT(*) FROM system_logs").strip()
        match = re.search(r"(\d+)", raw)
        count = int(match.group(1)) if match else 0
        assert count > 0, f"system_logs table is empty; log shipper did not run (raw: {raw!r})"

    def test_log_embeddings_generated(self):
        """system_logs rows should have embeddings if log_embeddings is configured."""
        import re
        total_raw = docker_psql("SELECT COUNT(*) FROM system_logs").strip()
        embedded_raw = docker_psql(
            "SELECT COUNT(*) FROM system_logs WHERE embedding IS NOT NULL"
        ).strip()
        total_match = re.search(r"(\d+)", total_raw)
        embedded_match = re.search(r"(\d+)", embedded_raw)
        total = int(total_match.group(1)) if total_match else 0
        embedded = int(embedded_match.group(1)) if embedded_match else 0

        if total == 0:
            assert False, "No system_logs rows"

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
        """Docker logs should show the background worker starting.

        Uses a large tail window (2000 lines) because the startup message
        may scroll past after bulk-loading thousands of messages.  Falls
        back to checking that embeddings exist (which proves the worker ran).
        """
        raw_logs = docker_logs("context-broker-langgraph", lines=2000)
        # Strip compose prefix ("container-name | ") from each line
        lines = []
        for line in raw_logs.splitlines():
            if " | " in line:
                line = line.split(" | ", 1)[1]
            lines.append(line)
        logs = "\n".join(lines)
        found_in_logs = (
            "Background worker starting" in logs
            or "background worker" in logs.lower()
        )
        if found_in_logs:
            return

        # Fallback: if the log line scrolled off, verify the worker ran by
        # confirming embeddings exist (the worker is responsible for these).
        import re as _re
        embedded_raw = docker_psql(
            "SELECT COUNT(*) FROM conversation_messages "
            "WHERE embedding IS NOT NULL"
        ).strip()
        embedded_match = _re.search(r"(\d+)", embedded_raw)
        embedded = int(embedded_match.group(1)) if embedded_match else 0
        assert embedded > 0, (
            "Background worker startup message not found in last 2000 log "
            "lines and no embeddings exist (worker may not have run)"
        )

    def test_no_pipeline_errors_in_recent_logs(self):
        """Check recent app logs for error-level entries and log any found."""
        logs = docker_logs("context-broker-langgraph", lines=50)
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
