"""
Database connection management for the Context Broker.

Manages asyncpg (PostgreSQL) and redis.asyncio (Redis) connection pools.
Connections are initialized at startup and closed at shutdown.
"""

import base64
import logging
import os
from typing import Optional

import asyncpg
import httpx
import redis.asyncio as aioredis
import redis.exceptions

_log = logging.getLogger("context_broker.database")

_pg_pool: Optional[asyncpg.Pool] = None
_redis_client: Optional[aioredis.Redis] = None


async def init_postgres(config: dict) -> asyncpg.Pool:
    """Create the asyncpg connection pool using config and environment variables.

    Database credentials come from environment (loaded via env_file in compose).
    Connection parameters come from environment variables set in compose.
    """
    global _pg_pool

    db_config = config.get("database", {})
    password = os.environ.get("POSTGRES_PASSWORD", "")

    _pg_pool = await asyncpg.create_pool(
        host=os.environ.get("POSTGRES_HOST", "context-broker-postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ.get("POSTGRES_DB", "context_broker"),
        user=os.environ.get("POSTGRES_USER", "context_broker"),
        password=password,
        min_size=db_config.get("pool_min_size", 2),
        max_size=db_config.get("pool_max_size", 10),
        command_timeout=30,
    )
    _log.info("PostgreSQL connection pool initialized")
    return _pg_pool


async def init_redis(config: dict) -> aioredis.Redis:
    """Create the async Redis client and verify connectivity."""
    global _redis_client

    _redis_client = aioredis.Redis(
        host=os.environ.get("REDIS_HOST", "context-broker-redis"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        decode_responses=True,
    )

    try:
        await _redis_client.ping()
        _log.info("Redis client initialized (ping OK)")
    except (redis.exceptions.RedisError, ConnectionError, OSError) as exc:
        _log.warning(
            "Redis client created but ping failed — starting in degraded mode: %s", exc
        )

    return _redis_client


def get_pg_pool() -> asyncpg.Pool:
    """Return the initialized asyncpg pool. Raises if not initialized."""
    if _pg_pool is None:
        raise RuntimeError(
            "PostgreSQL pool not initialized — call init_postgres() first"
        )
    return _pg_pool


def get_redis() -> aioredis.Redis:
    """Return the initialized Redis client. Raises if not initialized."""
    if _redis_client is None:
        raise RuntimeError(
            "Redis client not initialized — call init_redis() first"
        )
    return _redis_client


async def close_all_connections() -> None:
    """Gracefully close all database connections."""
    global _pg_pool, _redis_client

    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None
        _log.info("PostgreSQL pool closed")

    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        _log.info("Redis client closed")


async def check_postgres_health() -> bool:
    """Check PostgreSQL connectivity for health endpoint."""
    try:
        pool = get_pg_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except (asyncpg.PostgresError, OSError, RuntimeError) as exc:
        _log.warning("PostgreSQL health check failed: %s", exc)
        return False


async def check_redis_health() -> bool:
    """Check Redis connectivity for health endpoint."""
    try:
        client = get_redis()
        await client.ping()
        return True
    except (redis.exceptions.RedisError, ConnectionError, OSError, RuntimeError) as exc:
        _log.warning("Redis health check failed: %s", exc)
        return False


async def check_neo4j_health(config: dict | None = None) -> bool:
    """Check Neo4j connectivity for health endpoint.

    G5-13: This intentionally probes the HTTP endpoint (port 7474) rather than
    Bolt (port 7687). The HTTP endpoint is Neo4j's built-in health/discovery
    endpoint, suitable for container health checks. Bolt is used for data
    operations by the Mem0 client and Neo4j driver, not for health probes.

    R5-m4: The ``config`` parameter is accepted for interface consistency with
    callers (health_flow.py) but connection details are read from environment
    variables, matching the pattern used by init_postgres/init_redis.
    """
    neo4j_host = os.environ.get("NEO4J_HOST", "context-broker-neo4j")
    neo4j_http_port = os.environ.get("NEO4J_HTTP_PORT", "7474")
    url = f"http://{neo4j_host}:{neo4j_http_port}/"

    headers = {}
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "")
    if neo4j_password:
        credentials = base64.b64encode(f"neo4j:{neo4j_password}".encode()).decode()
        headers["Authorization"] = f"Basic {credentials}"

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url, headers=headers)
            return response.status_code == 200
    except (httpx.HTTPError, OSError) as exc:
        _log.warning("Neo4j health check failed: %s", exc)
        return False
