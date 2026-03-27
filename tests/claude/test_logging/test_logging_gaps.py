"""Tests for app/logging_setup.py covering gap areas.

Covers: HealthCheckFilter, update_log_level.
"""

import logging
from unittest.mock import MagicMock

import pytest

from app.logging_setup import HealthCheckFilter, update_log_level


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def health_filter():
    """Return a fresh HealthCheckFilter instance."""
    return HealthCheckFilter()


def _make_record(message: str) -> logging.LogRecord:
    """Create a minimal LogRecord with the given message."""
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )


# ── HealthCheckFilter ─────────────────────────────────────────────


class TestHealthCheckFilter:
    """Tests for HealthCheckFilter."""

    def test_suppresses_health_path(self, health_filter):
        """Suppresses log records containing '/health'."""
        record = _make_record('GET /health HTTP/1.1 200')
        assert health_filter.filter(record) is False

    def test_suppresses_get_health(self, health_filter):
        """Suppresses log records containing 'GET /health'."""
        record = _make_record("GET /health")
        assert health_filter.filter(record) is False

    def test_passes_normal_messages(self, health_filter):
        """Passes through log records that do not mention /health."""
        record = _make_record("Processing request for /api/chat")
        assert health_filter.filter(record) is True

    def test_passes_unrelated_get_request(self, health_filter):
        """Passes through unrelated GET requests."""
        record = _make_record("GET /api/mcp/tools HTTP/1.1 200")
        assert health_filter.filter(record) is True

    def test_suppresses_bare_health(self, health_filter):
        """Suppresses a message that is just '/health'."""
        record = _make_record("/health")
        assert health_filter.filter(record) is False


# ── update_log_level ──────────────────────────────────────────────


class TestUpdateLogLevel:
    """Tests for update_log_level()."""

    def test_sets_valid_level(self):
        """Sets the root and context_broker logger to the requested level."""
        update_log_level("DEBUG")

        root_level = logging.getLogger().level
        cb_level = logging.getLogger("context_broker").level
        assert root_level == logging.DEBUG
        assert cb_level == logging.DEBUG

        # Reset to INFO to avoid affecting other tests
        update_log_level("INFO")

    def test_warns_on_invalid_level(self, caplog):
        """Warns and keeps current level when an invalid level is given."""
        # Set a known baseline
        update_log_level("INFO")

        with caplog.at_level(logging.WARNING, logger="context_broker"):
            update_log_level("BOGUS_LEVEL")

        assert any("Invalid log level" in msg for msg in caplog.messages)
        # Level should still be INFO
        assert logging.getLogger("context_broker").level == logging.INFO

    def test_case_insensitive(self):
        """Accepts lowercase level names."""
        update_log_level("warning")

        assert logging.getLogger().level == logging.WARNING
        assert logging.getLogger("context_broker").level == logging.WARNING

        # Reset
        update_log_level("INFO")
