"""
Thorough unit tests for Mem0 client initialization, configuration,
lifecycle, and error handling.

Tests every function in mem0_client.py with edge cases.
Does NOT call real Mem0/Neo4j — mocks external dependencies.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from context_broker_ae.memory.mem0_client import (
    _compute_config_hash,
    _get_embedding_dims,
    _neo4j_config,
    reset_mem0_client,
)


# ── _compute_config_hash ─────────────────────────────────────────────

class TestComputeConfigHash:
    def test_same_config_same_hash(self):
        config = {"llm": {"model": "gpt-4o"}, "embeddings": {"model": "text-embedding-3-small"}}
        assert _compute_config_hash(config) == _compute_config_hash(config)

    def test_different_llm_different_hash(self):
        c1 = {"llm": {"model": "gpt-4o"}}
        c2 = {"llm": {"model": "gpt-4o-mini"}}
        assert _compute_config_hash(c1) != _compute_config_hash(c2)

    def test_different_embeddings_different_hash(self):
        c1 = {"embeddings": {"model": "text-embedding-3-small"}}
        c2 = {"embeddings": {"model": "nomic-embed-text"}}
        assert _compute_config_hash(c1) != _compute_config_hash(c2)

    def test_graph_store_change_detected(self):
        c1 = {"graph_store": {"url": "bolt://host1:7687"}}
        c2 = {"graph_store": {"url": "bolt://host2:7687"}}
        assert _compute_config_hash(c1) != _compute_config_hash(c2)

    def test_vector_store_change_detected(self):
        c1 = {"vector_store": {"host": "pg1"}}
        c2 = {"vector_store": {"host": "pg2"}}
        assert _compute_config_hash(c1) != _compute_config_hash(c2)

    def test_irrelevant_keys_ignored(self):
        c1 = {"llm": {"model": "x"}, "workers": {"poll": 5}}
        c2 = {"llm": {"model": "x"}, "workers": {"poll": 999}}
        assert _compute_config_hash(c1) == _compute_config_hash(c2)

    def test_empty_config_returns_valid_hash(self):
        h = _compute_config_hash({})
        assert isinstance(h, str)
        assert len(h) == 64

    def test_nested_change_detected(self):
        c1 = {"llm": {"config": {"temperature": 0.5}}}
        c2 = {"llm": {"config": {"temperature": 0.9}}}
        assert _compute_config_hash(c1) != _compute_config_hash(c2)

    def test_key_order_irrelevant(self):
        c1 = {"llm": {"a": 1, "b": 2}}
        c2 = {"llm": {"b": 2, "a": 1}}
        assert _compute_config_hash(c1) == _compute_config_hash(c2)


# ── _neo4j_config ────────────────────────────────────────────────────

class TestNeo4jConfig:
    def test_with_real_password(self):
        cfg = _neo4j_config("secret123")
        assert cfg["username"] == "neo4j"
        assert cfg["password"] == "secret123"
        assert cfg["url"].startswith("bolt://")

    def test_empty_password_uses_dummy(self):
        """PG-44: Mem0 requires username+password even with NEO4J_AUTH=none."""
        cfg = _neo4j_config("")
        assert cfg["username"] == "neo4j"
        assert cfg["password"] == "neo4j"

    def test_none_password_uses_dummy(self):
        cfg = _neo4j_config(None)
        assert cfg["password"] == "neo4j"

    def test_always_has_exactly_three_keys(self):
        """Mem0 GraphStoreConfig requires exactly url, username, password."""
        for pwd in ["", None, "real-pass"]:
            cfg = _neo4j_config(pwd)
            assert set(cfg.keys()) == {"url", "username", "password"}

    def test_default_host_and_port(self):
        cfg = _neo4j_config("")
        assert "context-broker-neo4j" in cfg["url"]
        assert "7687" in cfg["url"]

    @patch.dict(os.environ, {"NEO4J_HOST": "custom-host", "NEO4J_PORT": "9999"})
    def test_env_overrides(self):
        cfg = _neo4j_config("")
        assert "custom-host" in cfg["url"]
        assert "9999" in cfg["url"]

    @patch.dict(os.environ, {"NEO4J_HOST": "host-with-special.chars", "NEO4J_PORT": "7687"})
    def test_host_with_dots(self):
        cfg = _neo4j_config("")
        assert "host-with-special.chars" in cfg["url"]


# ── _get_embedding_dims ──────────────────────────────────────────────

class TestGetEmbeddingDims:
    def test_explicit_config_wins(self):
        assert _get_embedding_dims({"embedding_dims": 256}, {"model": "text-embedding-3-small"}) == 256

    def test_explicit_config_zero(self):
        # 0 is falsy but not None — should still use it? Actually 0 dims makes no sense.
        # The code checks `is not None`, so 0 would be used.
        assert _get_embedding_dims({"embedding_dims": 0}, {}) == 0

    def test_text_embedding_3_small(self):
        assert _get_embedding_dims({}, {"model": "text-embedding-3-small"}) == 1536

    def test_text_embedding_3_large(self):
        assert _get_embedding_dims({}, {"model": "text-embedding-3-large"}) == 3072

    def test_text_embedding_ada_002(self):
        assert _get_embedding_dims({}, {"model": "text-embedding-ada-002"}) == 1536

    def test_nomic_embed_text(self):
        assert _get_embedding_dims({}, {"model": "nomic-embed-text"}) == 768

    def test_nomic_embed_text_latest(self):
        assert _get_embedding_dims({}, {"model": "nomic-embed-text:latest"}) == 768

    def test_unknown_model_defaults_1536(self):
        assert _get_embedding_dims({}, {"model": "totally-unknown-model"}) == 1536

    def test_no_model_at_all_defaults_1536(self):
        assert _get_embedding_dims({}, {}) == 1536

    def test_explicit_none_falls_through(self):
        assert _get_embedding_dims({"embedding_dims": None}, {"model": "nomic-embed-text"}) == 768

    def test_returns_int(self):
        result = _get_embedding_dims({"embedding_dims": "512"}, {})
        assert isinstance(result, int)
        assert result == 512


# ── reset_mem0_client ────────────────────────────────────────────────

class TestResetMem0Client:
    @pytest.fixture(autouse=True)
    def clean_state(self):
        """Ensure clean global state before and after each test."""
        import context_broker_ae.memory.mem0_client as mod
        mod._mem0_instance = None
        mod._mem0_config_hash = None
        yield
        mod._mem0_instance = None
        mod._mem0_config_hash = None

    def test_clears_instance(self):
        import context_broker_ae.memory.mem0_client as mod
        mod._mem0_instance = "fake"
        mod._mem0_config_hash = "fake-hash"
        reset_mem0_client()
        assert mod._mem0_instance is None
        assert mod._mem0_config_hash == ""

    def test_idempotent(self):
        reset_mem0_client()
        reset_mem0_client()
        reset_mem0_client()

    def test_allows_recreation_after_reset(self):
        """After reset, get_mem0_client should attempt to create a new instance."""
        import context_broker_ae.memory.mem0_client as mod
        mod._mem0_instance = "old-instance"
        mod._mem0_config_hash = "old-hash"
        reset_mem0_client()
        # Hash is empty string, so any config will trigger recreation
        assert mod._mem0_config_hash == ""
        assert mod._mem0_instance is None


# ── get_mem0_client ──────────────────────────────────────────────────

class TestGetMem0Client:
    @pytest.fixture(autouse=True)
    def clean_state(self):
        import context_broker_ae.memory.mem0_client as mod
        mod._mem0_instance = None
        mod._mem0_config_hash = None
        yield
        mod._mem0_instance = None
        mod._mem0_config_hash = None

    @pytest.mark.asyncio
    async def test_returns_none_on_init_failure(self):
        """Graceful degradation: returns None if Mem0 can't initialize."""
        import context_broker_ae.memory.mem0_client as mod
        reset_mem0_client()

        with patch.object(mod, "_build_mem0_instance", side_effect=ConnectionError("neo4j down")):
            result = await mod.get_mem0_client({"llm": {}, "embeddings": {}})
            assert result is None

    @pytest.mark.asyncio
    async def test_caches_instance(self):
        """Second call with same config returns cached instance."""
        import context_broker_ae.memory.mem0_client as mod
        reset_mem0_client()

        mock_instance = MagicMock()
        with patch.object(mod, "_build_mem0_instance", return_value=mock_instance) as mock_build:
            config = {"llm": {"model": "x"}, "embeddings": {"model": "y"}}
            result1 = await mod.get_mem0_client(config)
            result2 = await mod.get_mem0_client(config)
            assert result1 is result2
            assert mock_build.call_count == 1

    @pytest.mark.asyncio
    async def test_recreates_on_config_change(self):
        """Config change triggers recreation."""
        import context_broker_ae.memory.mem0_client as mod
        reset_mem0_client()

        mock_instance = MagicMock()
        with patch.object(mod, "_build_mem0_instance", return_value=mock_instance) as mock_build:
            await mod.get_mem0_client({"llm": {"model": "a"}, "embeddings": {}})
            await mod.get_mem0_client({"llm": {"model": "b"}, "embeddings": {}})
            assert mock_build.call_count == 2

    @pytest.mark.asyncio
    async def test_recreates_after_reset(self):
        """reset_mem0_client forces recreation on next call."""
        import context_broker_ae.memory.mem0_client as mod
        reset_mem0_client()

        mock_instance = MagicMock()
        with patch.object(mod, "_build_mem0_instance", return_value=mock_instance) as mock_build:
            config = {"llm": {"model": "x"}, "embeddings": {}}
            await mod.get_mem0_client(config)
            reset_mem0_client()
            await mod.get_mem0_client(config)
            assert mock_build.call_count == 2


# ── _build_mem0_instance ─────────────────────────────────────────────

class TestBuildMem0Instance:
    @pytest.fixture(autouse=True)
    def skip_patches(self):
        """Prevent _apply_mem0_patches from running during unit tests."""
        import context_broker_ae.memory.mem0_client as mod
        mod._patches_applied = True
        yield
        mod._patches_applied = False

    @patch.dict(os.environ, {
        "POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "test_db", "POSTGRES_USER": "test_user",
        "POSTGRES_PASSWORD": "test_pass", "NEO4J_PASSWORD": "",
    })
    @patch("mem0.Memory")
    def test_creates_memory_with_all_providers(self, mock_memory):
        from context_broker_ae.memory.mem0_client import _build_mem0_instance
        config = {
            "extraction": {"base_url": "http://localhost:11434/v1", "model": "qwen2.5:7b", "api_key_env": ""},
            "embeddings": {"base_url": "http://localhost:7997", "model": "nomic-embed-text", "api_key_env": ""},
        }
        _build_mem0_instance(config)
        mock_memory.assert_called_once()

    @patch.dict(os.environ, {
        "POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "test_db", "POSTGRES_USER": "test_user",
        "POSTGRES_PASSWORD": "test_pass", "NEO4J_PASSWORD": "real-pw",
    })
    @patch("mem0.Memory")
    def test_passes_real_neo4j_password(self, mock_memory):
        from context_broker_ae.memory.mem0_client import _build_mem0_instance
        config = {
            "extraction": {"model": "gpt-4o-mini", "api_key_env": ""},
            "embeddings": {"model": "text-embedding-3-small", "api_key_env": ""},
        }
        _build_mem0_instance(config)
        call_args = mock_memory.call_args
        mem_config = call_args.kwargs.get("config")
        if mem_config is None and call_args.args:
            mem_config = call_args.args[0]
        # MemoryConfig is a Pydantic model — access graph_store.config
        gs_config = mem_config.graph_store.config
        # GraphStoreConfig.config may be a dict or Pydantic model
        pwd = gs_config["password"] if isinstance(gs_config, dict) else gs_config.password
        assert pwd == "real-pw"

    @patch.dict(os.environ, {
        "POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "test_db", "POSTGRES_USER": "test_user",
        "POSTGRES_PASSWORD": "test_pass", "NEO4J_PASSWORD": "",
    })
    @patch("mem0.Memory")
    def test_dummy_neo4j_password_when_auth_none(self, mock_memory):
        """PG-44: Empty NEO4J_PASSWORD should pass dummy credentials."""
        from context_broker_ae.memory.mem0_client import _build_mem0_instance
        config = {
            "extraction": {"model": "gpt-4o-mini", "api_key_env": ""},
            "embeddings": {"model": "text-embedding-3-small", "api_key_env": ""},
        }
        _build_mem0_instance(config)
        call_args = mock_memory.call_args
        mem_config = call_args.kwargs.get("config")
        if mem_config is None and call_args.args:
            mem_config = call_args.args[0]
        gs_config = mem_config.graph_store.config
        pwd = gs_config["password"] if isinstance(gs_config, dict) else gs_config.password
        usr = gs_config["username"] if isinstance(gs_config, dict) else gs_config.username
        assert pwd == "neo4j"
        assert usr == "neo4j"

    @patch.dict(os.environ, {
        "POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "test_db", "POSTGRES_USER": "test_user",
        "POSTGRES_PASSWORD": "test_pass", "NEO4J_PASSWORD": "",
    })
    @patch("mem0.Memory")
    def test_uses_extraction_config_not_llm(self, mock_memory):
        """Extraction LLM config should come from 'extraction' key, not 'llm'."""
        from context_broker_ae.memory.mem0_client import _build_mem0_instance
        config = {
            "extraction": {"base_url": "http://extraction-host/v1", "model": "extraction-model", "api_key_env": ""},
            "llm": {"base_url": "http://wrong-host/v1", "model": "wrong-model", "api_key_env": ""},
            "embeddings": {"model": "text-embedding-3-small", "api_key_env": ""},
        }
        _build_mem0_instance(config)
        call_args = mock_memory.call_args
        mem_config = call_args.kwargs.get("config")
        if mem_config is None and call_args.args:
            mem_config = call_args.args[0]
        assert mem_config.llm.config["model"] == "extraction-model"
        assert "extraction-host" in mem_config.llm.config["openai_base_url"]

    @patch.dict(os.environ, {
        "POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "test_db", "POSTGRES_USER": "test_user",
        "POSTGRES_PASSWORD": "test_pass", "NEO4J_PASSWORD": "",
    })
    @patch("mem0.Memory")
    def test_falls_back_to_llm_if_no_extraction(self, mock_memory):
        """If no 'extraction' key, fall back to 'llm'."""
        from context_broker_ae.memory.mem0_client import _build_mem0_instance
        config = {
            "llm": {"base_url": "http://fallback/v1", "model": "fallback-model", "api_key_env": ""},
            "embeddings": {"model": "text-embedding-3-small", "api_key_env": ""},
        }
        _build_mem0_instance(config)
        call_args = mock_memory.call_args
        mem_config = call_args.kwargs.get("config")
        if mem_config is None and call_args.args:
            mem_config = call_args.args[0]
        assert mem_config.llm.config["model"] == "fallback-model"
