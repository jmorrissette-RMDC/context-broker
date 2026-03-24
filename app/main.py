"""
Context Broker — ASGI application entry point.

FastAPI application that wires together all routes, middleware,
and lifecycle events. This file is transport only — all logic
lives in StateGraph flows.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import asyncpg
import redis.exceptions
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import load_config, get_build_type_config, get_tuning
from app.database import init_postgres, init_redis, close_all_connections
from app.logging_setup import setup_logging, update_log_level
from app.migrations import run_migrations
from app.routes import chat, health, mcp, metrics
from app.workers.arq_worker import start_background_worker
from app.imperator.state_manager import ImperatorStateManager

setup_logging()
_log = logging.getLogger("context_broker.main")


async def _postgres_retry_loop(application: FastAPI, config: dict) -> None:
    """Background task that retries PostgreSQL connection if it failed at startup."""
    while True:
        # R6-M15: Reload config each iteration so hot-reloaded corrections take effect
        config = load_config()
        retry_interval = get_tuning(config, "postgres_retry_interval_seconds", 10)
        await asyncio.sleep(retry_interval)
        if getattr(application.state, "postgres_available", False):
            # Postgres came back — also retry Imperator init if it was skipped
            if not getattr(application.state, "imperator_initialized", False):
                try:
                    imperator_manager = getattr(
                        application.state, "imperator_manager", None
                    )
                    if imperator_manager is not None:
                        await imperator_manager.initialize()
                        application.state.imperator_initialized = True
                        _log.info(
                            "Imperator initialization succeeded on Postgres retry"
                        )
                except (OSError, RuntimeError, asyncpg.PostgresError) as exc:
                    _log.warning("Imperator initialization retry failed: %s", exc)
            return
        try:
            _log.info("Retrying PostgreSQL connection...")
            await init_postgres(config)
            await run_migrations()
            application.state.postgres_available = True
            _log.info("PostgreSQL connection established on retry")

            # Retry Imperator init now that Postgres is available
            if not getattr(application.state, "imperator_initialized", False):
                try:
                    imperator_manager = getattr(
                        application.state, "imperator_manager", None
                    )
                    if imperator_manager is not None:
                        await imperator_manager.initialize()
                        application.state.imperator_initialized = True
                        _log.info(
                            "Imperator initialization succeeded on Postgres retry"
                        )
                except (OSError, RuntimeError, asyncpg.PostgresError) as exc:
                    _log.warning(
                        "Imperator initialization retry failed (will retry next loop): %s",
                        exc,
                    )
                    # Don't return — keep retrying Imperator on next loop iteration
                    continue

            return
        except (OSError, RuntimeError, asyncpg.PostgresError) as exc:
            _log.warning("PostgreSQL retry failed: %s", exc)


async def _redis_retry_loop(application: FastAPI, config: dict) -> None:
    """Background task that retries Redis connection and starts the worker once available."""
    while True:
        # R6-M15: Reload config each iteration so hot-reloaded corrections take effect
        config = load_config()
        retry_interval = get_tuning(config, "redis_retry_interval_seconds", 10)
        await asyncio.sleep(retry_interval)
        if getattr(application.state, "redis_available", False):
            return
        try:
            from app.database import get_redis

            # R5-M23: Recreate the Redis client if it doesn't exist or
            # if the previous init_redis() failed (client is None)
            try:
                redis_client = get_redis()
            except (RuntimeError, AttributeError):
                _log.info("Redis client not available, reinitializing...")
                await init_redis(config)
                redis_client = get_redis()
            await redis_client.ping()
            application.state.redis_available = True
            _log.info("Redis connection verified on retry — starting background worker")
            application.state.worker_task = asyncio.create_task(
                start_background_worker(config)
            )
            return
        except (
            ConnectionError,
            OSError,
            RuntimeError,
            redis.exceptions.RedisError,
        ) as exc:
            _log.warning("Redis retry failed: %s", exc)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage application lifecycle: startup and shutdown."""
    _log.info("Context Broker starting up")

    config = load_config()

    # Apply configured log level now that config is available
    configured_level = config.get("log_level", "INFO")
    update_log_level(configured_level)

    # REQ-001 §10.2: Scan for StateGraph packages via entry_points
    from app.stategraph_registry import scan as scan_stategraph_packages

    discovered = scan_stategraph_packages()
    _log.info("StateGraph packages discovered: %s", discovered)
    if not discovered.get("ae"):
        _log.warning(
            "No AE packages found. Infrastructure flows will not be available "
            "until an AE package is installed via install_stategraph."
        )
    if not discovered.get("te"):
        _log.warning(
            "No TE packages found. The Imperator will not be available "
            "until a TE package is installed via install_stategraph."
        )

    # REQ-001 §7.4 Fail Fast: Validate build type configs at startup
    for bt_name in config.get("build_types", {}):
        try:
            get_build_type_config(config, bt_name)
        except (ValueError, KeyError) as exc:
            _log.error("Invalid build type config '%s': %s", bt_name, exc)
            raise RuntimeError(
                f"Invalid build type config '{bt_name}': {exc}"
            ) from exc

    # Initialize database connections — Postgres failure is non-fatal
    pg_retry_task = None
    try:
        await init_postgres(config)
        await run_migrations()
        application.state.postgres_available = True
    except (OSError, RuntimeError, asyncpg.PostgresError) as exc:
        _log.warning(
            "PostgreSQL unavailable at startup — starting in degraded mode: %s", exc
        )
        application.state.postgres_available = False
        pg_retry_task = asyncio.create_task(_postgres_retry_loop(application, config))

    await init_redis(config)

    # Verify Redis is actually usable before starting the worker (G5-32)
    redis_retry_task = None
    worker_task = None
    try:
        from app.database import get_redis

        redis_client = get_redis()
        await redis_client.ping()
        application.state.redis_available = True
        worker_task = asyncio.create_task(start_background_worker(config))
    except (ConnectionError, OSError, RuntimeError, redis.exceptions.RedisError) as exc:
        _log.warning(
            "Redis unavailable at startup — worker deferred until Redis connects: %s",
            exc,
        )
        application.state.redis_available = False
        redis_retry_task = asyncio.create_task(_redis_retry_loop(application, config))

    # Initialize Imperator persistent state
    imperator_manager = ImperatorStateManager(config)
    application.state.imperator_manager = imperator_manager
    application.state.startup_config = config

    try:
        await imperator_manager.initialize()
        application.state.imperator_initialized = True
    except (OSError, RuntimeError, asyncpg.PostgresError) as exc:
        _log.warning(
            "Imperator initialization failed (Postgres may be unavailable) — "
            "will retry when Postgres connects: %s",
            exc,
        )
        application.state.imperator_initialized = False
        # Ensure retry loop is running if not already started
        if pg_retry_task is None:
            pg_retry_task = asyncio.create_task(
                _postgres_retry_loop(application, config)
            )

    _log.info("Context Broker startup complete")

    yield

    # Shutdown
    _log.info("Context Broker shutting down")

    # Worker may have been started later by the Redis retry loop
    active_worker = worker_task or getattr(application.state, "worker_task", None)
    tasks_to_cancel = [
        t for t in [active_worker, pg_retry_task, redis_retry_task] if t is not None
    ]
    for t in tasks_to_cancel:
        t.cancel()
    for t in tasks_to_cancel:
        try:
            await t
        except asyncio.CancelledError:
            pass
    await close_all_connections()
    _log.info("Context Broker shutdown complete")


app = FastAPI(
    title="Context Broker",
    description="Context engineering and conversational memory service",
    version="1.0.0",
    lifespan=lifespan,
)

# Register routers
app.include_router(health.router)
app.include_router(metrics.router)
app.include_router(mcp.router)
app.include_router(chat.router)


@app.middleware("http")
async def check_postgres_middleware(request: Request, call_next):
    """Return 503 for routes that need Postgres when it is unavailable.

    Health and metrics endpoints are exempt so monitoring stays functional.
    """
    exempt_paths = {"/health", "/metrics"}
    if request.url.path not in exempt_paths:
        if not getattr(request.app.state, "postgres_available", False):
            return JSONResponse(
                status_code=503,
                content={
                    "error": "service_unavailable",
                    "message": "PostgreSQL is not available. The service is starting in degraded mode.",
                },
            )
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Return structured JSON for HTTP exceptions instead of Starlette's default."""
    _log.warning(
        "HTTP exception: %s %s — %s (status %d)",
        request.method,
        request.url.path,
        exc.detail,
        exc.status_code,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "http_error",
            "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return structured JSON for request validation failures."""
    _log.warning(
        "Validation error: %s %s — %s",
        request.method,
        request.url.path,
        exc.errors(),
    )
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "Request validation failed.",
            "details": exc.errors(),
        },
    )


# Last-resort handler for known exception families that could bubble out of
# our application code. Covers runtime failures, OS/network errors, DB errors,
# and stdlib value errors. This is NOT a blanket Exception catch — each type
# is explicitly listed. (CB-R3-01 / G5-22)
# R7-M7: Added asyncpg.PostgresError to catch unhandled DB errors
@app.exception_handler(RuntimeError)
@app.exception_handler(ValueError)
@app.exception_handler(OSError)
@app.exception_handler(ConnectionError)
@app.exception_handler(asyncpg.PostgresError)
async def known_exception_handler(request: Request, exc):
    """Return structured error for known unhandled exception families."""
    _log.error(
        "Unhandled %s: %s %s — %s",
        type(exc).__name__,
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred. Check server logs for details.",
        },
    )
