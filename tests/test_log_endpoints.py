"""Tests for log MCP endpoints and vectorization config (§4.16, §4.17).

Unit tests for input validation and config gating.
"""

import pytest
from pydantic import ValidationError

from app.models import QueryLogsInput, SearchLogsInput


class TestQueryLogsInput:
    """T-16.x: query_logs input validation."""

    def test_valid_no_filters(self):
        inp = QueryLogsInput()
        assert inp.limit == 50
        assert inp.container_name is None

    def test_valid_all_filters(self):
        inp = QueryLogsInput(
            container_name="langgraph",
            level="ERROR",
            since="2026-03-25T00:00:00Z",
            until="2026-03-25T23:59:59Z",
            keyword="failed",
            limit=100,
        )
        assert inp.level == "ERROR"
        assert inp.limit == 100

    def test_invalid_level(self):
        with pytest.raises(ValidationError):
            QueryLogsInput(level="TRACE")

    def test_limit_bounds(self):
        with pytest.raises(ValidationError):
            QueryLogsInput(limit=0)
        with pytest.raises(ValidationError):
            QueryLogsInput(limit=501)


class TestSearchLogsInput:
    """T-16.5 partial: search_logs input validation."""

    def test_requires_query(self):
        with pytest.raises(ValidationError):
            SearchLogsInput()

    def test_valid_minimal(self):
        inp = SearchLogsInput(query="embedding failed")
        assert inp.limit == 20

    def test_limit_bounds(self):
        with pytest.raises(ValidationError):
            SearchLogsInput(query="test", limit=101)


class TestLogEmbeddingsConfig:
    """T-17.3: Log embeddings use separate config key."""

    def test_get_embeddings_model_accepts_config_key(self):
        """Verify the config_key parameter is accepted."""
        from app.config import get_embeddings_model
        import inspect

        sig = inspect.signature(get_embeddings_model)
        assert "config_key" in sig.parameters
        assert sig.parameters["config_key"].default == "embeddings"
