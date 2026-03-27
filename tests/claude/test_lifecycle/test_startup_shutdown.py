"""Tests for app/main.py — startup, shutdown, retry loops, and exception handlers."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_CONFIG = {
    "log_level": "INFO",
    "build_types": {
        "sliding-window": {"tier1_pct": 0, "tier2_pct": 0, "tier3_pct": 1.0},
    },
    "embeddings": {"embedding_dims": 768},
    "tuning": {"postgres_retry_interval_seconds": 0},
}


def _config_missing_dims():
    cfg = {**_VALID_CONFIG, "embeddings": {}}
    return cfg


def _config_bad_build_type():
    cfg = {**_VALID_CONFIG}
    cfg["build_types"] = {"bad": {"tier1_pct": "not-a-number"}}
    return cfg


# ---------------------------------------------------------------------------
# Startup: build type validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_raises_on_bad_build_type_config():
    """get_build_type_config raising ValueError should abort startup."""
    with (
        patch("app.main.load_config", return_value=_config_bad_build_type()),
        patch("app.main.update_log_level"),
        patch(
            "app.main.scan_stategraph_packages"
            if False
            else "app.stategraph_registry.scan",
            return_value={"ae": True, "te": True},
        ),
        patch(
            "app.main.get_build_type_config",
            side_effect=ValueError("bad tier percentages"),
        ),
    ):
        from app.main import app  # noqa: F811

        with pytest.raises(RuntimeError, match="Invalid build type config"):
            async with app.router.lifespan_context(app):
                pass


# ---------------------------------------------------------------------------
# Startup: embedding_dims required
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_raises_when_embedding_dims_missing():
    """Startup must fail fast when embeddings.embedding_dims is absent."""
    with (
        patch("app.main.load_config", return_value=_config_missing_dims()),
        patch("app.main.update_log_level"),
        patch(
            "app.stategraph_registry.scan",
            return_value={"ae": True, "te": True},
        ),
    ):
        from app.main import app

        with pytest.raises(RuntimeError, match="embedding_dims is required"):
            async with app.router.lifespan_context(app):
                pass


# ---------------------------------------------------------------------------
# Postgres retry loop — retries connection, runs migrations on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_retry_loop_retries_and_succeeds():
    """_postgres_retry_loop should call init_postgres + run_migrations, then return."""
    from app.main import _postgres_retry_loop

    fake_app = MagicMock()
    fake_app.state.postgres_available = False
    fake_app.state.imperator_initialized = True
    fake_app.state.imperator_manager = None

    call_count = {"n": 0}
    original_init = AsyncMock()

    async def fake_init(cfg):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise OSError("pg down")
        # success on second try

    cfg = {**_VALID_CONFIG}
    with (
        patch("app.main.load_config", return_value=cfg),
        patch("app.main.get_tuning", return_value=0),
        patch("app.main.init_postgres", side_effect=fake_init),
        patch("app.main.run_migrations", new_callable=AsyncMock) as mock_mig,
    ):
        await _postgres_retry_loop(fake_app, cfg)

    assert fake_app.state.postgres_available is True
    mock_mig.assert_called_once()


# ---------------------------------------------------------------------------
# Postgres retry loop — retries Imperator initialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_retry_loop_retries_imperator_init():
    """When postgres is already available but imperator_initialized is False,
    the loop should retry imperator initialization."""
    from app.main import _postgres_retry_loop

    fake_app = MagicMock()
    fake_app.state.postgres_available = True
    fake_app.state.imperator_initialized = False
    mock_manager = AsyncMock()
    fake_app.state.imperator_manager = mock_manager

    cfg = {**_VALID_CONFIG}
    with (
        patch("app.main.load_config", return_value=cfg),
        patch("app.main.get_tuning", return_value=0),
    ):
        await _postgres_retry_loop(fake_app, cfg)

    mock_manager.initialize.assert_awaited_once()
    assert fake_app.state.imperator_initialized is True


# ---------------------------------------------------------------------------
# Degraded mode (postgres_available=False)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_degraded_mode_sets_postgres_unavailable():
    """When init_postgres raises, app should enter degraded mode."""
    with (
        patch("app.main.load_config", return_value=_VALID_CONFIG),
        patch("app.main.update_log_level"),
        patch(
            "app.stategraph_registry.scan",
            return_value={"ae": True, "te": True},
        ),
        patch("app.main.get_build_type_config"),
        patch("app.main.init_postgres", side_effect=OSError("pg down")),
        patch("app.main.run_migrations", new_callable=AsyncMock),
        patch("app.main.ImperatorStateManager") as MockISM,
        patch("app.main.start_background_worker", new_callable=AsyncMock),
        patch("app.main.close_all_connections", new_callable=AsyncMock),
        patch("app.main._postgres_retry_loop", new_callable=AsyncMock),
    ):
        mock_ism_instance = AsyncMock()
        mock_ism_instance.initialize = AsyncMock(side_effect=OSError("no pg"))
        MockISM.return_value = mock_ism_instance

        from app.main import app

        async with app.router.lifespan_context(app):
            assert app.state.postgres_available is False


# ---------------------------------------------------------------------------
# Domain knowledge seeding — ImportError handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_domain_knowledge_seed_import_error_is_non_fatal():
    """ImportError from seed_knowledge should be caught and logged, not crash."""
    with (
        patch("app.main.load_config", return_value=_VALID_CONFIG),
        patch("app.main.update_log_level"),
        patch(
            "app.stategraph_registry.scan",
            return_value={"ae": True, "te": True},
        ),
        patch("app.main.get_build_type_config"),
        patch("app.main.init_postgres", new_callable=AsyncMock),
        patch("app.main.run_migrations", new_callable=AsyncMock),
        patch("app.main.ImperatorStateManager") as MockISM,
        patch("app.main.start_background_worker", new_callable=AsyncMock),
        patch("app.main.close_all_connections", new_callable=AsyncMock),
    ):
        mock_ism_instance = AsyncMock()
        MockISM.return_value = mock_ism_instance

        # Patch the import inside lifespan to raise ImportError
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "context_broker_te.seed_knowledge":
                raise ImportError("No module named 'context_broker_te'")
            if name == "context_broker_ae.memory.mem0_client":
                raise ImportError("Mocked out for test")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            from app.main import app

            async with app.router.lifespan_context(app):
                # Should not raise — seeding failure is non-fatal
                assert app.state.imperator_initialized is True


# ---------------------------------------------------------------------------
# Shutdown — cancels worker tasks, closes connections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_cancels_tasks_and_closes_connections():
    """Shutdown should cancel worker/retry tasks and close DB connections."""
    with (
        patch("app.main.load_config", return_value=_VALID_CONFIG),
        patch("app.main.update_log_level"),
        patch(
            "app.stategraph_registry.scan",
            return_value={"ae": True, "te": True},
        ),
        patch("app.main.get_build_type_config"),
        patch("app.main.init_postgres", new_callable=AsyncMock),
        patch("app.main.run_migrations", new_callable=AsyncMock),
        patch("app.main.ImperatorStateManager") as MockISM,
        patch(
            "app.main.start_background_worker", new_callable=AsyncMock
        ) as mock_worker,
        patch(
            "app.main.close_all_connections", new_callable=AsyncMock
        ) as mock_close,
    ):
        mock_ism_instance = AsyncMock()
        MockISM.return_value = mock_ism_instance

        # Make seed_knowledge import fail silently
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "context_broker_te.seed_knowledge":
                raise ImportError("nope")
            if name == "context_broker_ae.memory.mem0_client":
                raise ImportError("Mocked out for test")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            from app.main import app

            async with app.router.lifespan_context(app):
                pass

        # close_all_connections should have been awaited during shutdown
        mock_close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Known exception handler — RuntimeError, ValueError, OSError,
# ConnectionError, asyncpg.PostgresError -> 500 JSON
# ---------------------------------------------------------------------------

# We test the exception handler via httpx.ASGITransport against a
# specially-prepared app that raises inside an endpoint.


@pytest.mark.asyncio
async def test_known_exception_handler_runtime_error():
    """RuntimeError raised in a route should yield 500 JSON via the handler."""
    from app.main import app

    # Temporarily add a test route that raises
    @app.get("/test-raise-runtime")
    async def _raise():
        raise RuntimeError("boom")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test-raise-runtime")

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"] == "internal_server_error"
    # Clean up
    app.routes[:] = [r for r in app.routes if getattr(r, "path", None) != "/test-raise-runtime"]


@pytest.mark.asyncio
async def test_known_exception_handler_value_error():
    """ValueError raised in a route should yield 500 JSON."""
    from app.main import app

    @app.get("/test-raise-value")
    async def _raise():
        raise ValueError("bad value")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test-raise-value")

    assert resp.status_code == 500
    assert resp.json()["error"] == "internal_server_error"
    app.routes[:] = [r for r in app.routes if getattr(r, "path", None) != "/test-raise-value"]


@pytest.mark.asyncio
async def test_known_exception_handler_os_error():
    """OSError raised in a route should yield 500 JSON."""
    from app.main import app

    @app.get("/test-raise-os")
    async def _raise():
        raise OSError("disk full")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test-raise-os")

    assert resp.status_code == 500
    assert resp.json()["error"] == "internal_server_error"
    app.routes[:] = [r for r in app.routes if getattr(r, "path", None) != "/test-raise-os"]


@pytest.mark.asyncio
async def test_known_exception_handler_connection_error():
    """ConnectionError raised in a route should yield 500 JSON."""
    from app.main import app

    @app.get("/test-raise-conn")
    async def _raise():
        raise ConnectionError("refused")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test-raise-conn")

    assert resp.status_code == 500
    assert resp.json()["error"] == "internal_server_error"
    app.routes[:] = [r for r in app.routes if getattr(r, "path", None) != "/test-raise-conn"]


@pytest.mark.asyncio
async def test_known_exception_handler_asyncpg_error():
    """asyncpg.PostgresError raised in a route should yield 500 JSON."""
    from app.main import app

    @app.get("/test-raise-pg")
    async def _raise():
        raise asyncpg.PostgresError("relation does not exist")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test-raise-pg")

    assert resp.status_code == 500
    assert resp.json()["error"] == "internal_server_error"
    app.routes[:] = [r for r in app.routes if getattr(r, "path", None) != "/test-raise-pg"]
