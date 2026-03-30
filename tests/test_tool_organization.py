"""Tests for Imperator tool organization (§4.13).

Verifies tool discovery, gating, and module exports.
"""



class TestToolDiscovery:
    """T-13.1: Tool discovery from modules."""

    def test_collect_tools_without_admin(self):
        from context_broker_te.imperator_flow import _collect_tools

        tools = _collect_tools({"admin_tools": False})
        names = {t.name for t in tools}

        # Core tools always present
        assert "conv_search" in names
        assert "mem_search" in names

        # Diagnostic tools always present
        assert "log_query" in names
        assert "context_introspection" in names
        assert "pipeline_status" in names

        # Admin tools NOT present
        assert "config_read" not in names
        assert "db_query" not in names
        assert "migrate_embeddings" not in names
        assert "change_inference" not in names

    def test_collect_tools_with_admin(self):
        from context_broker_te.imperator_flow import _collect_tools

        tools = _collect_tools({"admin_tools": True})
        names = {t.name for t in tools}

        # Admin tools present
        assert "config_read" in names
        assert "db_query" in names
        assert "config_write" in names
        assert "verbose_toggle" in names
        assert "migrate_embeddings" in names
        assert "change_inference" in names

        # Core + diagnostic still present
        assert "conv_search" in names
        assert "log_query" in names


class TestOperationalToolGating:
    """T-13.2: Operational tool gating by config."""

    def test_domain_info_enabled(self):
        from context_broker_te.tools.operational import get_tools

        tools = get_tools({"domain_information": {"enabled": True}})
        names = {t.name for t in tools}
        assert "store_domain_info" in names
        assert "search_domain_info" in names

    def test_domain_info_disabled(self):
        from context_broker_te.tools.operational import get_tools

        tools = get_tools({"domain_information": {"enabled": False}})
        names = {t.name for t in tools}
        assert "store_domain_info" not in names
        assert "search_domain_info" not in names

    def test_domain_knowledge_enabled(self):
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

    def test_domain_knowledge_disabled_by_default(self):
        from context_broker_te.tools.operational import get_tools

        tools = get_tools({"domain_information": {"enabled": True}})
        names = {t.name for t in tools}
        assert "extract_domain_knowledge" not in names
        assert "search_domain_knowledge" not in names

    def test_empty_dict_config_returns_empty(self):
        from context_broker_te.tools.operational import get_tools

        # Empty dict is falsy in Python — no tools returned
        tools = get_tools({})
        assert tools == []

    def test_none_config_returns_empty(self):
        from context_broker_te.tools.operational import get_tools

        tools = get_tools(None)
        assert tools == []

    def test_default_enabled_when_section_present(self):
        from context_broker_te.tools.operational import get_tools

        # domain_information defaults to enabled when section exists but no explicit flag
        tools = get_tools({"domain_information": {}})
        names = {t.name for t in tools}
        assert "store_domain_info" in names


class TestToolModuleExports:
    """T-13.3: Each tool module exports valid tools."""

    def test_diagnostic_module(self):
        from context_broker_te.tools.diagnostic import get_tools

        tools = get_tools()
        assert len(tools) == 3
        for t in tools:
            assert hasattr(t, "name")
            assert hasattr(t, "ainvoke")

    def test_admin_module(self):
        from context_broker_te.tools.admin import get_tools

        tools = get_tools()
        assert len(tools) == 6
        for t in tools:
            assert hasattr(t, "name")

    def test_operational_module_with_all_enabled(self):
        from context_broker_te.tools.operational import get_tools

        tools = get_tools(
            {
                "domain_information": {"enabled": True},
                "domain_knowledge": {"enabled": True},
            }
        )
        assert len(tools) == 4
        for t in tools:
            assert hasattr(t, "name")
