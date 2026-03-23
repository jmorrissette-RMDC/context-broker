"""
Unit tests for the build type registry (app.flows.build_type_registry).

Covers registration, retrieval, unknown type errors, lazy compilation,
and verification that all three shipped types are registered.
"""

import pytest

from app.flows.build_type_registry import (
    _compiled_cache,
    _registry,
    get_assembly_graph,
    get_retrieval_graph,
    list_build_types,
    register_build_type,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _save_and_restore_registry():
    """Save registry state for restoration after test."""
    saved_registry = dict(_registry)
    saved_cache = dict(_compiled_cache)
    return saved_registry, saved_cache


def _restore_registry(saved):
    """Restore registry state."""
    saved_registry, saved_cache = saved
    _registry.clear()
    _registry.update(saved_registry)
    _compiled_cache.clear()
    _compiled_cache.update(saved_cache)


# ------------------------------------------------------------------
# Registration and retrieval
# ------------------------------------------------------------------


class TestBuildTypeRegistry:
    """Tests for register_build_type and get_*_graph."""

    def setup_method(self):
        self._saved = _save_and_restore_registry()

    def teardown_method(self):
        _restore_registry(self._saved)

    def test_register_and_retrieve_assembly(self):
        """A registered build type's assembly graph can be retrieved."""
        mock_graph = object()
        register_build_type("test-type", lambda: mock_graph, lambda: None)
        graph = get_assembly_graph("test-type")
        assert graph is mock_graph

    def test_register_and_retrieve_retrieval(self):
        """A registered build type's retrieval graph can be retrieved."""
        mock_graph = object()
        register_build_type("test-type", lambda: None, lambda: mock_graph)
        graph = get_retrieval_graph("test-type")
        assert graph is mock_graph

    def test_unknown_type_raises_assembly(self):
        """get_assembly_graph raises ValueError for an unregistered type."""
        with pytest.raises(ValueError, match="not registered"):
            get_assembly_graph("nonexistent-build-type")

    def test_unknown_type_raises_retrieval(self):
        """get_retrieval_graph raises ValueError for an unregistered type."""
        with pytest.raises(ValueError, match="not registered"):
            get_retrieval_graph("nonexistent-build-type")

    def test_lazy_compilation_cached(self):
        """Graph builders are called only once; subsequent calls use the cache."""
        call_count = 0

        def builder():
            nonlocal call_count
            call_count += 1
            return f"graph-{call_count}"

        register_build_type("cached-type", builder, lambda: None)
        first = get_assembly_graph("cached-type")
        second = get_assembly_graph("cached-type")
        assert first is second
        assert call_count == 1

    def test_overwrite_warning(self):
        """Re-registering a build type overwrites the previous entry."""
        register_build_type("dup-type", lambda: "old", lambda: "old")
        register_build_type("dup-type", lambda: "new", lambda: "new")

        # Clear compiled cache so the new builder is invoked
        _compiled_cache.clear()

        graph = get_assembly_graph("dup-type")
        assert graph == "new"

    def test_list_build_types(self):
        """list_build_types returns all registered type names."""
        register_build_type("alpha", lambda: None, lambda: None)
        register_build_type("beta", lambda: None, lambda: None)
        types = list_build_types()
        assert "alpha" in types
        assert "beta" in types


# ------------------------------------------------------------------
# Shipped build types
# ------------------------------------------------------------------


class TestShippedBuildTypes:
    """Verify the three shipped build types are registered at import."""

    def test_three_shipped_types_registered(self):
        """Importing build_types package registers passthrough, standard-tiered, knowledge-enriched."""
        # Trigger registration by importing the package
        import app.flows.build_types  # noqa: F401

        types = list_build_types()
        assert "passthrough" in types
        assert "standard-tiered" in types
        assert "knowledge-enriched" in types

    def test_shipped_assembly_graphs_compile(self):
        """Each shipped build type's assembly builder produces a graph without error."""
        import app.flows.build_types  # noqa: F401

        # Clear cache to force fresh compilation
        saved = dict(_compiled_cache)
        _compiled_cache.clear()
        try:
            for name in ("passthrough", "standard-tiered", "knowledge-enriched"):
                graph = get_assembly_graph(name)
                assert graph is not None
        finally:
            _compiled_cache.clear()
            _compiled_cache.update(saved)

    def test_shipped_retrieval_graphs_compile(self):
        """Each shipped build type's retrieval builder produces a graph without error."""
        import app.flows.build_types  # noqa: F401

        saved = dict(_compiled_cache)
        _compiled_cache.clear()
        try:
            for name in ("passthrough", "standard-tiered", "knowledge-enriched"):
                graph = get_retrieval_graph(name)
                assert graph is not None
        finally:
            _compiled_cache.clear()
            _compiled_cache.update(saved)
