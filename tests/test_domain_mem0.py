"""Tests for domain Mem0 client (§4.19).

Verifies separate collection name and config isolation.
"""



class TestDomainMem0Config:
    """T-19.3: Domain Mem0 uses separate collection."""

    def test_uses_domain_memories_collection(self):
        """Verify the domain Mem0 builder uses 'domain_memories' collection."""
        from context_broker_te.domain_mem0 import _build_domain_mem0
        import os
        from unittest.mock import patch

        env = {
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "test",
            "POSTGRES_USER": "test",
            "POSTGRES_PASSWORD": "test",
            "NEO4J_PASSWORD": "",
        }
        config = {
            "extraction": {"model": "test", "api_key_env": "", "base_url": "http://x"},
            "embeddings": {
                "model": "test",
                "api_key_env": "",
                "base_url": "http://x",
                "embedding_dims": 768,
            },
        }

        with patch.dict(os.environ, env), patch("mem0.Memory") as mock_mem:
            _build_domain_mem0(config)
            call_args = mock_mem.call_args
            mem_config = call_args.kwargs.get("config")
            if mem_config is None and call_args.args:
                mem_config = call_args.args[0]
            # Check the vector store collection name
            vs_config = mem_config.vector_store.config
            collection = (
                vs_config["collection_name"]
                if isinstance(vs_config, dict)
                else vs_config.collection_name
            )
            assert collection == "domain_memories"

    def test_reset_clears_singleton(self):
        from context_broker_te.domain_mem0 import (
            reset_domain_mem0,
        )

        import context_broker_te.domain_mem0 as mod

        mod._domain_mem0_instance = "fake"
        reset_domain_mem0()
        assert mod._domain_mem0_instance is None


class TestDomainKnowledgeToolGating:
    """T-19.4: Domain knowledge tools gated by config."""

    def test_disabled_by_default(self):
        from context_broker_te.tools.operational import get_tools

        tools = get_tools({"domain_information": {"enabled": True}})
        names = {t.name for t in tools}
        assert "extract_domain_knowledge" not in names
        assert "search_domain_knowledge" not in names

    def test_enabled_when_configured(self):
        from context_broker_te.tools.operational import get_tools

        tools = get_tools(
            {
                "domain_information": {"enabled": True},
                "domain_knowledge": {"enabled": True},
            }
        )
        names = {t.name for t in tools}
        assert "extract_domain_knowledge" in names
        assert "search_domain_knowledge" in names
