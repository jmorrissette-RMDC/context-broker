# Context Broker — Flattened Source Code (R7)

---

`app/config.py`

```python
"""
Configuration management for the Context Broker.

Reads /config/config.yml on each call to load_config() so that
hot-reloadable settings (inference providers, build types) take
effect immediately without restart.

Infrastructure settings (database connections) are read once at
startup and cached.
"""

import asyncio
import hashlib
import json
import logging
import os
import threading
from functools import lru_cache
from typing import Any

import yaml

_log = logging.getLogger("context_broker.config")

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config/config.yml")

# Cached config with mtime check to avoid repeated file reads (M-11).
# os.stat() is near-instant and avoids synchronous file I/O on every call.
_config_cache: dict[str, Any] | None = None
_config_mtime: float = 0.0
_config_content_hash: str = ""

# G5-04: Lock for compound clear-and-set operations on caches.
# Individual dict ops are atomic under CPython's GIL, but the
# clear-LLM-cache + clear-embeddings-cache sequence in load_config
# must be atomic to prevent a concurrent reader from seeing a
# half-cleared state.
_cache_lock = threading.Lock()


def _read_and_parse_config() -> tuple[dict[str, Any], str]:
    """Read config.yml from disk and return (parsed_dict, raw_text).

    Separated from load_config() so that async_load_config() can
    offload only this blocking portion to run_in_executor.
    """
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = f.read()
        config = yaml.safe_load(raw)
        if not isinstance(config, dict):
            raise ValueError("config.yml must be a YAML mapping at the top level")
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Configuration file not found at {CONFIG_PATH}. "
            "Mount /config/config.yml into the container."
        ) from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Failed to parse {CONFIG_PATH}: {exc}") from exc
    return config, raw


def _apply_config(config: dict[str, Any], raw: str, current_mtime: float) -> dict[str, Any]:
    """Update global cache state after a successful config read.

    Shared by both load_config() and async_load_config().
    R5-M24: All global state updates are performed inside _cache_lock
    to prevent concurrent readers from seeing a half-updated state.
    """
    global _config_cache, _config_mtime, _config_content_hash

    new_hash = hashlib.sha256(raw.encode()).hexdigest()
    with _cache_lock:
        if new_hash != _config_content_hash and _config_content_hash != "":
            _log.info("Config file content changed — clearing LLM and embeddings caches")
            _llm_cache.clear()
            _embeddings_cache.clear()

        _config_cache = config
        _config_mtime = current_mtime
        _config_content_hash = new_hash
    return config


def load_config() -> dict[str, Any]:
    """Load and return the full configuration from /config/config.yml.

    Uses mtime-based caching: only re-reads the file when it changes.
    On config change, clears the LLM and embeddings caches (M-03)
    so hot-reloaded provider settings take effect immediately.

    G5-06: This function performs blocking file I/O (os.stat + open/read).
    The mtime cache means the file is only re-read when it actually changes
    on disk, which is rare in production. The os.stat() fast-path check is
    near-instant for local files. Async callers (route handlers, flow nodes)
    should use async_load_config() instead, which offloads the file read to
    run_in_executor when a re-read is triggered.

    Raises RuntimeError if the file cannot be read or parsed.
    """
    global _config_cache, _config_mtime, _config_content_hash

    try:
        current_mtime = os.stat(CONFIG_PATH).st_mtime
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Configuration file not found at {CONFIG_PATH}. "
            "Mount /config/config.yml into the container."
        ) from exc

    # CB-R3-09: Float equality on mtime is a fast-path optimisation only.
    # If mtime changes we fall through and re-read, but actual cache
    # invalidation is gated on the SHA-256 content hash below, so
    # platform-level mtime precision differences are harmless.
    if _config_cache is not None and current_mtime == _config_mtime:
        return _config_cache

    config, raw = _read_and_parse_config()
    return _apply_config(config, raw, current_mtime)


async def async_load_config() -> dict[str, Any]:
    """Async wrapper for load_config().

    Uses the same mtime-based cache as load_config(). The os.stat()
    fast-path check is synchronous (near-instant for local files).
    Only when a re-read is actually needed does it offload the file
    read + YAML parse to run_in_executor to avoid blocking the event loop.

    Route handlers and flow nodes should prefer this over load_config().
    """
    global _config_cache, _config_mtime, _config_content_hash

    try:
        current_mtime = os.stat(CONFIG_PATH).st_mtime
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Configuration file not found at {CONFIG_PATH}. "
            "Mount /config/config.yml into the container."
        ) from exc

    if _config_cache is not None and current_mtime == _config_mtime:
        return _config_cache

    loop = asyncio.get_running_loop()
    config, raw = await loop.run_in_executor(None, _read_and_parse_config)
    return _apply_config(config, raw, current_mtime)


@lru_cache(maxsize=1)
def load_startup_config() -> dict[str, Any]:
    """Load configuration once at startup for infrastructure settings.

    Cached — changes require container restart.
    """
    return load_config()


def get_api_key(provider_config: dict[str, Any]) -> str:
    """Resolve an API key from the environment variable named in api_key_env.

    Returns empty string if api_key_env is not set (e.g., local Ollama).
    """
    env_var_name = provider_config.get("api_key_env", "")
    if not env_var_name:
        return ""
    api_key = os.environ.get(env_var_name, "")
    if not api_key:
        _log.warning(
            "API key environment variable '%s' is not set or empty", env_var_name
        )
    return api_key


def get_build_type_config(config: dict[str, Any], build_type_name: str) -> dict[str, Any]:
    """Return the configuration for a named build type.

    Raises ValueError if the build type is not defined.
    """
    build_types = config.get("build_types", {})
    if build_type_name not in build_types:
        raise ValueError(
            f"Build type '{build_type_name}' not found in config.yml. "
            f"Available: {list(build_types.keys())}"
        )
    bt_config = build_types[build_type_name]

    # Validate that ALL tier percentages sum to <= 1.0
    pct_keys = ["tier1_pct", "tier2_pct", "tier3_pct", "semantic_retrieval_pct", "knowledge_graph_pct"]
    total_pct = sum(bt_config.get(k, 0) or 0 for k in pct_keys)
    if total_pct > 1.0:
        raise ValueError(
            f"Build type '{build_type_name}' tier percentages sum to {total_pct:.2f}, "
            f"which exceeds 1.0. Keys checked: {pct_keys}"
        )

    return bt_config


def get_tuning(config: dict, key: str, default: Any) -> Any:
    """Return a tuning parameter from config, with fallback to default."""
    return config.get("tuning", {}).get(key, default)


def get_log_level(config: dict[str, Any]) -> str:
    """Return the configured log level, defaulting to INFO."""
    return config.get("log_level", "INFO").upper()


def verbose_log(config: dict, logger: Any, message: str, *args: Any) -> None:
    """Log a message only if verbose_logging is enabled in tuning config.

    REQ-001 section 4.8: Verbose pipeline logging for node entry/exit with timing.
    Accepts printf-style args for lazy formatting.
    """
    if get_tuning(config, "verbose_logging", False):
        logger.info(message, *args)


def verbose_log_auto(logger: Any, message: str, *args: Any) -> None:
    """Log a verbose message by reading config on each call.

    For flows that don't carry config in their state.
    Falls back to no-op if config cannot be loaded.
    """
    try:
        cfg = load_config()
        if get_tuning(cfg, "verbose_logging", False):
            logger.info(message, *args)
    except (RuntimeError, OSError, ValueError, TypeError):
        # R6-m6: Broadened to catch bad YAML structure (ValueError/TypeError)
        # in addition to file-level errors. Verbose logging must never crash.
        pass


# ============================================================
# Cached LLM / Embedding client factories (M-09)
# ============================================================

# G5-04: Individual dict operations (__getitem__, __setitem__, __contains__)
# are atomic under CPython's GIL, so these caches are safe for concurrent
# async reads/writes without an explicit asyncio.Lock. Compound operations
# (clear-and-set in load_config) are protected by _cache_lock above.
_llm_cache: dict[str, Any] = {}
_embeddings_cache: dict[str, Any] = {}

# R2-F07: Simple bounded-cache eviction threshold.
_MAX_CACHE_ENTRIES = 10


def get_chat_model(config: dict) -> Any:
    """Return a cached ChatOpenAI instance keyed by (base_url, model).

    Avoids re-creating the client on every request.
    R5-M12: Uses _cache_lock around the full check-and-set to prevent
    two concurrent calls from both missing the cache and creating
    duplicate clients.
    """
    from langchain_openai import ChatOpenAI

    llm_config = config.get("llm", {})
    api_key = get_api_key(llm_config)
    # G5-05: Include api_key hash so credential rotation doesn't reuse a stale client.
    # m3: Use stable SHA-256 hash instead of Python's hash() which is
    # randomized per process (PYTHONHASHSEED).
    cache_key = (
        f"{llm_config.get('base_url')}:{llm_config.get('model')}:{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"
    )
    with _cache_lock:
        if cache_key not in _llm_cache:
            # R5-m9: Evict oldest entry instead of clearing entire cache.
            if len(_llm_cache) >= _MAX_CACHE_ENTRIES:
                oldest_key = next(iter(_llm_cache))
                del _llm_cache[oldest_key]
            _llm_cache[cache_key] = ChatOpenAI(
                base_url=llm_config.get("base_url", "https://api.openai.com/v1"),
                model=llm_config.get("model", "gpt-4o-mini"),
                api_key=api_key or "not-needed",
                timeout=1800,
            )
        return _llm_cache[cache_key]


def get_embeddings_model(config: dict) -> Any:
    """Return a cached OpenAIEmbeddings instance keyed by (base_url, model).

    Avoids re-creating the client on every request.
    R5-M12: Uses _cache_lock around the full check-and-set to prevent
    two concurrent calls from both missing the cache and creating
    duplicate clients.
    """
    from langchain_openai import OpenAIEmbeddings

    embeddings_config = config.get("embeddings", {})
    api_key = get_api_key(embeddings_config)
    # G5-05: Include api_key hash so credential rotation doesn't reuse a stale client.
    # m3: Use stable SHA-256 hash instead of Python's hash() which is
    # randomized per process (PYTHONHASHSEED).
    cache_key = (
        f"{embeddings_config.get('base_url')}:{embeddings_config.get('model')}:{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"
    )
    with _cache_lock:
        if cache_key not in _embeddings_cache:
            # R5-m9: Evict oldest entry instead of clearing entire cache.
            if len(_embeddings_cache) >= _MAX_CACHE_ENTRIES:
                oldest_key = next(iter(_embeddings_cache))
                del _embeddings_cache[oldest_key]
            _embeddings_cache[cache_key] = OpenAIEmbeddings(
                model=embeddings_config.get("model", "text-embedding-3-small"),
                openai_api_base=embeddings_config.get("base_url", "https://api.openai.com/v1"),
                openai_api_key=api_key or "not-needed",
            )
        return _embeddings_cache[cache_key]
```

---

`app/database.py`

```python
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
```

---

`app/flows/build_type_registry.py`

```python
"""
Build Type Registry (ARCH-18).

Maps build type names to their (assembly_graph, retrieval_graph) pairs.
Graphs are compiled lazily on first use and cached thereafter.

Usage:
    from app.flows.build_type_registry import get_assembly_graph, get_retrieval_graph

    graph = get_assembly_graph("standard-tiered")
    result = await graph.ainvoke(input_state)
"""

import logging
import threading
from typing import Any, Callable

_log = logging.getLogger("context_broker.flows.build_type_registry")

# R5-m2: Lock for thread-safe registration and lazy compilation.
_lock = threading.Lock()

# Registry: name -> (assembly_builder, retrieval_builder)
_registry: dict[str, tuple[Callable, Callable]] = {}

# Compiled graph cache: (name, "assembly"|"retrieval") -> compiled graph
_compiled_cache: dict[tuple[str, str], Any] = {}


def register_build_type(
    name: str,
    assembly_builder: Callable,
    retrieval_builder: Callable,
) -> None:
    """Register a build type with its assembly and retrieval graph builders.

    Args:
        name: Build type name (e.g., "passthrough", "standard-tiered").
        assembly_builder: Callable that returns a compiled StateGraph for assembly.
        retrieval_builder: Callable that returns a compiled StateGraph for retrieval.
    """
    with _lock:
        if name in _registry:
            _log.warning("Build type '%s' already registered — overwriting", name)
        _registry[name] = (assembly_builder, retrieval_builder)
    _log.info("Registered build type: %s", name)


def get_assembly_graph(name: str) -> Any:
    """Return the compiled assembly graph for a build type (lazy).

    Raises ValueError if the build type is not registered.
    """
    cache_key = (name, "assembly")
    with _lock:
        if cache_key in _compiled_cache:
            return _compiled_cache[cache_key]

        if name not in _registry:
            raise ValueError(
                f"Build type '{name}' is not registered. "
                f"Available: {list(_registry.keys())}"
            )

        builder = _registry[name][0]
        graph = builder()
        _compiled_cache[cache_key] = graph
    _log.info("Compiled assembly graph for build type: %s", name)
    return graph


def get_retrieval_graph(name: str) -> Any:
    """Return the compiled retrieval graph for a build type (lazy).

    Raises ValueError if the build type is not registered.
    """
    cache_key = (name, "retrieval")
    with _lock:
        if cache_key in _compiled_cache:
            return _compiled_cache[cache_key]

        if name not in _registry:
            raise ValueError(
                f"Build type '{name}' is not registered. "
                f"Available: {list(_registry.keys())}"
            )

        builder = _registry[name][1]
        graph = builder()
        _compiled_cache[cache_key] = graph
    _log.info("Compiled retrieval graph for build type: %s", name)
    return graph


def list_build_types() -> list[str]:
    """Return a list of all registered build type names."""
    return list(_registry.keys())
```

---

`app/flows/build_types/__init__.py`

```python
"""
Build types package (ARCH-18).

Importing this package triggers registration of all build types
in the build_type_registry. Each build type module calls
register_build_type() at import time.
"""

# R6-m3: Import all build types to trigger registration side effects.
# This is the intended pattern: each module calls register_build_type()
# at module scope, registering its (assembly_builder, retrieval_builder)
# pair in the global registry. The arq_worker imports this package once
# at startup to ensure all build types are available.
import app.flows.build_types.passthrough  # noqa: F401
import app.flows.build_types.standard_tiered  # noqa: F401
import app.flows.build_types.knowledge_enriched  # noqa: F401
```

---

`app/flows/build_types/knowledge_enriched.py`

```python
"""
Knowledge-enriched build type (ARCH-18).

Extends standard-tiered with semantic retrieval and knowledge graph nodes.
Full retrieval pipeline: episodic tiers + semantic vector search + KG traversal.

Assembly is identical to standard-tiered (reuses the same graph builder).
Retrieval adds inject_semantic_retrieval and inject_knowledge_graph nodes.

F-06: Reads its own LLM config from config["build_types"]["knowledge-enriched"]["llm"],
falling back to the global config["llm"] if not set.
"""

import asyncio
import logging
import operator
import time
import uuid
from typing import Annotated, Optional

import asyncpg
import httpx
import openai
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_build_type_config, get_embeddings_model, get_tuning, verbose_log
from app.flows.memory_scoring import filter_and_rank_memories
from app.database import get_pg_pool, get_redis
from app.flows.build_type_registry import register_build_type
from app.flows.build_types.standard_tiered import (
    build_standard_tiered_assembly,
    _estimate_tokens,
    _resolve_llm_config,
)
from app.flows.build_types.tier_scaling import scale_tier_percentages

_log = logging.getLogger("context_broker.flows.build_types.knowledge_enriched")


# ============================================================
# Retrieval (extends standard-tiered with semantic + KG)
# ============================================================


class KnowledgeEnrichedRetrievalState(TypedDict):
    """State for the knowledge-enriched retrieval flow."""

    # Inputs
    context_window_id: str
    config: dict

    # Intermediate
    window: Optional[dict]
    build_type_config: Optional[dict]
    conversation_id: Optional[str]
    max_token_budget: int
    tier1_summary: Optional[str]
    tier2_summaries: list[str]
    recent_messages: list[dict]
    semantic_messages: list[dict]
    knowledge_graph_facts: list[str]
    assembly_status: str

    # Output
    context_messages: Optional[list[dict]]
    context_tiers: Optional[dict]
    total_tokens_used: int
    warnings: Annotated[list[str], operator.add]  # R5-m5: accumulate, don't overwrite
    error: Optional[str]


async def ke_load_window(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Load the context window and its build type configuration."""
    # R5-M11: Validate UUID at retrieval entry point to fail gracefully
    try:
        uuid.UUID(state["context_window_id"])
    except (ValueError, AttributeError):
        _log.error(
            "Invalid UUID in retrieval input: context_window_id=%s",
            state.get("context_window_id"),
        )
        return {"error": "Invalid UUID in retrieval input", "assembly_status": "error"}

    verbose_log(state["config"], _log, "knowledge_enriched.retrieval.load_window ENTER window=%s", state["context_window_id"])
    pool = get_pg_pool()

    window = await pool.fetchrow(
        "SELECT * FROM context_windows WHERE id = $1",
        uuid.UUID(state["context_window_id"]),
    )
    if window is None:
        return {
            "error": f"Context window {state['context_window_id']} not found",
            "assembly_status": "error",
        }

    # Update last_accessed_at on every retrieval
    await pool.execute(
        "UPDATE context_windows SET last_accessed_at = NOW() WHERE id = $1",
        uuid.UUID(state["context_window_id"]),
    )

    window_dict = dict(window)

    try:
        build_type_config = get_build_type_config(state["config"], window_dict["build_type"])
    except ValueError as exc:
        return {"error": str(exc), "assembly_status": "error"}

    return {
        "window": window_dict,
        "build_type_config": build_type_config,
        "conversation_id": str(window_dict["conversation_id"]),
        "max_token_budget": window_dict["max_token_budget"],
    }


async def ke_wait_for_assembly(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Block if context assembly is in progress, with timeout.

    R6-M9: If Redis is unavailable, proceed without waiting rather than crashing.
    """
    try:
        redis = get_redis()
    except RuntimeError:
        _log.warning("Retrieval: Redis not available, proceeding without assembly wait")
        return {"assembly_status": "ready"}

    lock_key = f"assembly_in_progress:{state['context_window_id']}"

    timeout = get_tuning(state["config"], "assembly_wait_timeout_seconds", 50)
    poll_interval = get_tuning(state["config"], "assembly_poll_interval_seconds", 2)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            in_progress = await redis.exists(lock_key)
        except (ConnectionError, OSError, RuntimeError) as exc:
            _log.warning(
                "Retrieval: Redis error during assembly wait, proceeding: %s", exc
            )
            return {"assembly_status": "ready"}
        if not in_progress:
            return {"assembly_status": "ready"}

        elapsed = timeout - (deadline - time.monotonic())
        _log.info(
            "Retrieval: waiting for assembly on window=%s (%.0fs/%ds)",
            state["context_window_id"],
            elapsed,
            timeout,
        )
        await asyncio.sleep(poll_interval)

    _log.warning(
        "Retrieval: assembly timeout for window=%s after %ds",
        state["context_window_id"],
        timeout,
    )
    return {
        "assembly_status": "timeout",
        "warnings": [
            "Context assembly was still in progress at retrieval time; "
            "context may be stale."
        ],
    }


async def ke_load_summaries(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Load active tier 1 and tier 2 summaries."""
    pool = get_pg_pool()

    summaries = await pool.fetch(
        """
        SELECT tier, summary_text, summarizes_from_seq
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND is_active = TRUE
        ORDER BY tier ASC, summarizes_from_seq ASC
        """,
        uuid.UUID(state["context_window_id"]),
    )

    tier1 = None
    tier2_list = []
    for s in summaries:
        if s["tier"] == 1:
            tier1 = s["summary_text"]
        elif s["tier"] == 2:
            tier2_list.append(s["summary_text"])

    return {"tier1_summary": tier1, "tier2_summaries": tier2_list}


async def ke_load_recent_messages(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Load tier 3 recent verbatim messages within the remaining token budget.

    F-05: Applies dynamic tier scaling based on conversation length.
    """
    pool = get_pg_pool()
    build_type_config = state["build_type_config"]
    max_budget = state["max_token_budget"]

    # Count total messages for F-05 tier scaling
    total_msg_count = await pool.fetchval(
        "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = $1",
        uuid.UUID(state["conversation_id"]),
    )
    scaled_config = scale_tier_percentages(build_type_config, total_msg_count or 0)

    tier3_pct = scaled_config.get("tier3_pct", 0.50)
    tier3_budget = int(max_budget * tier3_pct)

    # Calculate tokens already used by summaries
    summary_tokens = 0
    if state.get("tier1_summary"):
        summary_tokens += len(state["tier1_summary"]) // 4
    for s in state.get("tier2_summaries", []):
        summary_tokens += len(s) // 4

    remaining_budget = max(0, min(tier3_budget, max_budget - summary_tokens))

    # M-06: Avoid loading messages already covered by summaries
    highest_summarized_seq = await pool.fetchval(
        """
        SELECT COALESCE(MAX(summarizes_to_seq), 0)
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND tier = 2
          AND is_active = TRUE
        """,
        uuid.UUID(state["context_window_id"]),
    )

    max_messages = get_tuning(state["config"], "max_messages_to_load", 1000)

    all_messages = await pool.fetch(
        """
        SELECT id, role, sender, content, sequence_number, token_count,
               tool_calls, tool_call_id, created_at
        FROM conversation_messages
        WHERE conversation_id = $1
          AND sequence_number > $2
        ORDER BY sequence_number DESC
        LIMIT $3
        """,
        uuid.UUID(state["conversation_id"]),
        highest_summarized_seq,
        max_messages,
    )

    recent = []
    tokens_used = 0

    for msg in all_messages:
        msg_tokens = msg["token_count"] or max(1, len(msg.get("content") or "") // 4)
        if tokens_used + msg_tokens <= remaining_budget:
            recent.insert(0, dict(msg))
            tokens_used += msg_tokens
        else:
            break

    return {
        "recent_messages": recent,
        "total_tokens_used": summary_tokens + tokens_used,
    }


async def ke_inject_semantic_retrieval(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Retrieve semantically similar messages via pgvector.

    G5-22a: Semantic retrieval may surface messages already compressed into
    summaries. This is a known and accepted trade-off.
    """
    build_type_config = state["build_type_config"]
    semantic_pct = build_type_config.get("semantic_retrieval_pct", 0)

    if not semantic_pct or semantic_pct <= 0:
        return {"semantic_messages": []}

    if not state.get("recent_messages"):
        return {"semantic_messages": []}

    config = state["config"]

    # Build query from recent messages
    query_trunc = get_tuning(config, "query_truncation_chars", 200)
    recent_text = " ".join((m.get("content") or "")[:query_trunc] for m in state["recent_messages"][-3:])

    try:
        embeddings_model = get_embeddings_model(config)
        query_embedding = await embeddings_model.aembed_query(recent_text)
    except (openai.APIError, httpx.HTTPError, ValueError) as exc:
        _log.warning("Semantic retrieval: embedding failed: %s", exc)
        return {"semantic_messages": []}

    tier3_min_seq = (
        state["recent_messages"][0]["sequence_number"]
        if state["recent_messages"]
        else None
    )

    if tier3_min_seq is None:
        return {"semantic_messages": []}

    semantic_budget = int(state["max_token_budget"] * semantic_pct)
    tokens_per_msg = max(1, get_tuning(config, "tokens_per_message_estimate", 150))
    semantic_limit = max(5, semantic_budget // tokens_per_msg)

    pool = get_pg_pool()
    vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    try:
        rows = await pool.fetch(
            """
            SELECT id, role, sender, content, sequence_number, token_count,
                   tool_calls, tool_call_id
            FROM conversation_messages
            WHERE conversation_id = $1
              AND sequence_number < $2
              AND embedding IS NOT NULL
            ORDER BY embedding <=> $3::vector
            LIMIT $4
            """,
            uuid.UUID(state["conversation_id"]),
            tier3_min_seq,
            vec_str,
            semantic_limit,
        )
        semantic_messages = [dict(r) for r in rows]
        _log.info(
            "Semantic retrieval: found %d relevant messages for window=%s",
            len(semantic_messages),
            state["context_window_id"],
        )
        return {"semantic_messages": semantic_messages}
    except (asyncpg.PostgresError, OSError) as exc:
        _log.warning("Semantic retrieval query failed: %s", exc)
        return {"semantic_messages": []}


async def ke_inject_knowledge_graph(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Retrieve knowledge graph facts via Mem0."""
    build_type_config = state["build_type_config"]
    kg_pct = build_type_config.get("knowledge_graph_pct", 0)

    if not kg_pct or kg_pct <= 0:
        return {"knowledge_graph_facts": []}

    if not state.get("recent_messages"):
        return {"knowledge_graph_facts": []}

    config = state["config"]

    recent_text = " ".join((m.get("content") or "")[:500] for m in state["recent_messages"][-5:])

    try:
        from app.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            return {"knowledge_graph_facts": []}

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: mem0.search(
                recent_text,
                user_id=state["window"].get("participant_id", "default"),
                limit=10,
            ),
        )

        facts = []
        if isinstance(results, dict):
            memories = results.get("results", [])
        else:
            memories = results or []

        # M-22: Apply half-life decay scoring and filter stale memories
        memories = filter_and_rank_memories(memories, config)

        for mem in memories:
            fact_text = mem.get("memory") or mem.get("content") or str(mem)
            if fact_text:
                facts.append(fact_text)

        _log.info(
            "Knowledge graph: retrieved %d facts for window=%s",
            len(facts),
            state["context_window_id"],
        )
        return {"knowledge_graph_facts": facts}

    except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:  # EX-CB-001: broad catch for Mem0
        _log.warning(
            "Knowledge graph retrieval failed (degraded mode): %s", exc
        )
        return {"knowledge_graph_facts": []}


async def ke_assemble_context(state: KnowledgeEnrichedRetrievalState) -> dict:
    """Assemble the final context messages array from all tiers including semantic + KG.

    ARCH-03: Produces a structured messages array matching the OpenAI format.
    """
    max_budget = state.get("max_token_budget", 0)
    cumulative_tokens = 0
    messages: list[dict] = []

    # Tier 1: Archival summary
    if state.get("tier1_summary"):
        content = f"[Archival context]\n{state['tier1_summary']}"
        cumulative_tokens += _estimate_tokens(content)
        messages.append({"role": "system", "content": content})

    # Tier 2: Chunk summaries
    if state.get("tier2_summaries"):
        content = "[Recent summaries]\n" + "\n\n".join(state["tier2_summaries"])
        cumulative_tokens += _estimate_tokens(content)
        messages.append({"role": "system", "content": content})

    # Semantic retrieval — budget-aware (M-07)
    if state.get("semantic_messages"):
        remaining = max(0, max_budget - cumulative_tokens) if max_budget else float("inf")
        semantic_lines = []
        semantic_tokens = 0
        for m in state["semantic_messages"]:
            line = f"[{m['role']}] {m['sender']}: {m.get('content') or ''}"
            line_tokens = _estimate_tokens(line)
            if semantic_tokens + line_tokens > remaining:
                break
            semantic_lines.append(line)
            semantic_tokens += line_tokens
        if semantic_lines:
            content = "[Semantically relevant context]\n" + "\n".join(semantic_lines)
            cumulative_tokens += _estimate_tokens(content)
            messages.append({"role": "system", "content": content})

    # Knowledge graph facts — budget-aware (M-07)
    if state.get("knowledge_graph_facts"):
        remaining = max(0, max_budget - cumulative_tokens) if max_budget else float("inf")
        fact_lines = []
        fact_tokens = 0
        for f in state["knowledge_graph_facts"]:
            line = f"- {f}"
            line_tokens = _estimate_tokens(line)
            if fact_tokens + line_tokens > remaining:
                break
            fact_lines.append(line)
            fact_tokens += line_tokens
        if fact_lines:
            content = "[Knowledge graph]\n" + "\n".join(fact_lines)
            cumulative_tokens += _estimate_tokens(content)
            messages.append({"role": "system", "content": content})

    # Tier 3: Recent verbatim messages (M-08: newest first truncation)
    truncated_recent_messages: list[dict] = []
    if state.get("recent_messages"):
        remaining = max(0, max_budget - cumulative_tokens) if max_budget else float("inf")
        msg_tokens = 0
        for m in reversed(state["recent_messages"]):
            msg_content = m.get("content", "")
            msg_token_count = _estimate_tokens(msg_content)
            if msg_tokens + msg_token_count > remaining:
                break
            truncated_recent_messages.insert(0, m)
            msg_tokens += msg_token_count
        for m in truncated_recent_messages:
            msg = {"role": m["role"], "content": m["content"]}
            if m.get("tool_calls"):
                msg["tool_calls"] = m["tool_calls"]
            if m.get("tool_call_id"):
                msg["tool_call_id"] = m["tool_call_id"]
            if m.get("sender"):
                msg["name"] = m["sender"]
            messages.append(msg)
            cumulative_tokens += _estimate_tokens(m.get("content", ""))

    # M-15: Build context_tiers from truncated lists
    context_tiers = {
        "archival_summary": state.get("tier1_summary"),
        "chunk_summaries": state.get("tier2_summaries", []),
        "semantic_messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "sender": m["sender"],
                "content": m["content"],
                "sequence_number": m["sequence_number"],
            }
            for m in state.get("semantic_messages", [])
        ],
        "knowledge_graph_facts": state.get("knowledge_graph_facts", []),
        "recent_messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "sender": m["sender"],
                "content": m["content"],
                "sequence_number": m["sequence_number"],
            }
            for m in truncated_recent_messages
        ],
    }

    return {
        "context_messages": messages,
        "context_tiers": context_tiers,
    }


def ke_route_after_load_window(state: KnowledgeEnrichedRetrievalState) -> str:
    if state.get("error"):
        return END
    return "ke_wait_for_assembly"


def ke_route_after_wait(state: KnowledgeEnrichedRetrievalState) -> str:
    return "ke_load_summaries"


def ke_route_after_load_messages(state: KnowledgeEnrichedRetrievalState) -> str:
    """Route: check if build type needs semantic/KG retrieval."""
    build_type_config = state.get("build_type_config", {})
    needs_semantic = build_type_config.get("semantic_retrieval_pct", 0) > 0
    needs_kg = build_type_config.get("knowledge_graph_pct", 0) > 0

    if needs_semantic:
        return "ke_inject_semantic_retrieval"
    if needs_kg:
        return "ke_inject_knowledge_graph"
    return "ke_assemble_context"


def ke_route_after_semantic(state: KnowledgeEnrichedRetrievalState) -> str:
    build_type_config = state.get("build_type_config", {})
    needs_kg = build_type_config.get("knowledge_graph_pct", 0) > 0
    if needs_kg:
        return "ke_inject_knowledge_graph"
    return "ke_assemble_context"


def build_knowledge_enriched_retrieval():
    """Build and compile the knowledge-enriched retrieval StateGraph."""
    workflow = StateGraph(KnowledgeEnrichedRetrievalState)

    workflow.add_node("ke_load_window", ke_load_window)
    workflow.add_node("ke_wait_for_assembly", ke_wait_for_assembly)
    workflow.add_node("ke_load_summaries", ke_load_summaries)
    workflow.add_node("ke_load_recent_messages", ke_load_recent_messages)
    workflow.add_node("ke_inject_semantic_retrieval", ke_inject_semantic_retrieval)
    workflow.add_node("ke_inject_knowledge_graph", ke_inject_knowledge_graph)
    workflow.add_node("ke_assemble_context", ke_assemble_context)

    workflow.set_entry_point("ke_load_window")

    workflow.add_conditional_edges(
        "ke_load_window",
        ke_route_after_load_window,
        {"ke_wait_for_assembly": "ke_wait_for_assembly", END: END},
    )
    workflow.add_conditional_edges(
        "ke_wait_for_assembly",
        ke_route_after_wait,
        {"ke_load_summaries": "ke_load_summaries"},
    )
    workflow.add_edge("ke_load_summaries", "ke_load_recent_messages")

    workflow.add_conditional_edges(
        "ke_load_recent_messages",
        ke_route_after_load_messages,
        {
            "ke_inject_semantic_retrieval": "ke_inject_semantic_retrieval",
            "ke_inject_knowledge_graph": "ke_inject_knowledge_graph",
            "ke_assemble_context": "ke_assemble_context",
        },
    )

    workflow.add_conditional_edges(
        "ke_inject_semantic_retrieval",
        ke_route_after_semantic,
        {
            "ke_inject_knowledge_graph": "ke_inject_knowledge_graph",
            "ke_assemble_context": "ke_assemble_context",
        },
    )

    workflow.add_edge("ke_inject_knowledge_graph", "ke_assemble_context")
    workflow.add_edge("ke_assemble_context", END)

    return workflow.compile()


# ============================================================
# Assembly — reuse standard-tiered (same logic, just build type label differs)
# ============================================================

# The assembly process is identical for knowledge-enriched: chunk summarization
# and archival consolidation work the same way. The difference is only in retrieval
# (additional semantic + KG nodes). We reuse the standard-tiered assembly builder.
# The build_type label in the assembly state is set by the caller (arq_worker),
# so metrics are correctly attributed.

register_build_type(
    "knowledge-enriched",
    build_standard_tiered_assembly,
    build_knowledge_enriched_retrieval,
)
```

---

`app/flows/build_types/passthrough.py`

```python
"""
Passthrough build type (ARCH-18).

Minimal build type that demonstrates the contract:
- Assembly: no-op, just updates last_assembled_at.
- Retrieval: loads recent messages as-is and returns them.

No LLM calls, no summarization, no tier logic.
"""

import logging
import operator
import time
import uuid
from typing import Annotated, Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_build_type_config, get_tuning, verbose_log
from app.database import get_pg_pool, get_redis
from app.flows.build_type_registry import register_build_type
from app.metrics_registry import CONTEXT_ASSEMBLY_DURATION

_log = logging.getLogger("context_broker.flows.build_types.passthrough")


# ============================================================
# Assembly
# ============================================================


class PassthroughAssemblyState(TypedDict):
    """State for passthrough assembly — minimal."""

    context_window_id: str
    conversation_id: str
    config: dict

    # Lock management
    lock_key: str
    lock_token: Optional[str]
    lock_acquired: bool
    assembly_start_time: Optional[float]

    # Output
    error: Optional[str]


async def pt_acquire_lock(state: PassthroughAssemblyState) -> dict:
    """Acquire Redis assembly lock."""
    # R5-M11: Validate UUID at assembly entry point to fail gracefully
    try:
        uuid.UUID(state["context_window_id"])
        uuid.UUID(state["conversation_id"])
    except (ValueError, AttributeError):
        _log.error(
            "Invalid UUID in assembly input: context_window_id=%s, conversation_id=%s",
            state.get("context_window_id"),
            state.get("conversation_id"),
        )
        return {"error": "Invalid UUID in assembly input", "lock_acquired": False}

    verbose_log(state["config"], _log, "passthrough.acquire_lock ENTER window=%s", state["context_window_id"])
    lock_key = f"assembly_in_progress:{state['context_window_id']}"
    lock_token = str(uuid.uuid4())
    redis = get_redis()

    acquired = await redis.set(
        lock_key, lock_token,
        ex=get_tuning(state["config"], "assembly_lock_ttl_seconds", 300),
        nx=True,
    )
    if not acquired:
        _log.info("Passthrough assembly: lock not acquired for window=%s — skipping", state["context_window_id"])
        return {"lock_key": lock_key, "lock_token": None, "lock_acquired": False}

    return {"lock_key": lock_key, "lock_token": lock_token, "lock_acquired": True, "assembly_start_time": time.monotonic()}


async def pt_finalize(state: PassthroughAssemblyState) -> dict:
    """No-op assembly: just update last_assembled_at.

    R6-M13: Wrapped in try/except so errors route to pt_release_lock
    instead of raising and leaking the lock.
    """
    try:
        pool = get_pg_pool()
        await pool.execute(
            "UPDATE context_windows SET last_assembled_at = NOW() WHERE id = $1",
            uuid.UUID(state["context_window_id"]),
        )

        start_time = state.get("assembly_start_time")
        if start_time is not None:
            duration = time.monotonic() - start_time
            CONTEXT_ASSEMBLY_DURATION.labels(build_type="passthrough").observe(duration)

        _log.info("Passthrough assembly complete for window=%s", state["context_window_id"])
        return {}
    except (RuntimeError, OSError, Exception) as exc:
        _log.error("Passthrough finalize failed for window=%s: %s", state["context_window_id"], exc)
        return {"error": f"Passthrough finalize failed: {exc}"}


async def pt_release_lock(state: PassthroughAssemblyState) -> dict:
    """Release Redis assembly lock."""
    lock_key = state.get("lock_key", "")
    lock_token = state.get("lock_token")
    if lock_key and state.get("lock_acquired") and lock_token:
        redis = get_redis()
        lua_script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """
        await redis.eval(lua_script, 1, lock_key, lock_token)
    return {}


def pt_route_after_lock(state: PassthroughAssemblyState) -> str:
    if not state.get("lock_acquired"):
        return END
    return "pt_finalize"


def pt_route_after_finalize(state: PassthroughAssemblyState) -> str:
    """R6-M13: Route to lock release regardless of error state."""
    return "pt_release_lock"


def build_passthrough_assembly():
    """Build and compile the passthrough assembly StateGraph."""
    workflow = StateGraph(PassthroughAssemblyState)

    workflow.add_node("pt_acquire_lock", pt_acquire_lock)
    workflow.add_node("pt_finalize", pt_finalize)
    workflow.add_node("pt_release_lock", pt_release_lock)

    workflow.set_entry_point("pt_acquire_lock")
    workflow.add_conditional_edges(
        "pt_acquire_lock",
        pt_route_after_lock,
        {"pt_finalize": "pt_finalize", END: END},
    )
    # R6-M13: Use conditional edge from pt_finalize so that errors
    # route to pt_release_lock instead of leaking the lock.
    workflow.add_conditional_edges(
        "pt_finalize",
        pt_route_after_finalize,
        {"pt_release_lock": "pt_release_lock"},
    )
    workflow.add_edge("pt_release_lock", END)

    return workflow.compile()


# ============================================================
# Retrieval
# ============================================================


class PassthroughRetrievalState(TypedDict):
    """State for passthrough retrieval."""

    context_window_id: str
    config: dict

    # Intermediate
    window: Optional[dict]
    conversation_id: Optional[str]
    max_token_budget: int

    # Output
    context_messages: Optional[list[dict]]
    context_tiers: Optional[dict]
    total_tokens_used: int
    warnings: Annotated[list[str], operator.add]  # R5-m5: accumulate, don't overwrite
    error: Optional[str]


async def pt_load_window(state: PassthroughRetrievalState) -> dict:
    """Load the context window."""
    # R5-M11: Validate UUID at retrieval entry point to fail gracefully
    try:
        uuid.UUID(state["context_window_id"])
    except (ValueError, AttributeError):
        _log.error(
            "Invalid UUID in retrieval input: context_window_id=%s",
            state.get("context_window_id"),
        )
        return {"error": "Invalid UUID in retrieval input"}

    verbose_log(state["config"], _log, "passthrough.retrieval.load_window ENTER window=%s", state["context_window_id"])
    pool = get_pg_pool()

    window = await pool.fetchrow(
        "SELECT * FROM context_windows WHERE id = $1",
        uuid.UUID(state["context_window_id"]),
    )
    if window is None:
        return {"error": f"Context window {state['context_window_id']} not found"}

    window_dict = dict(window)
    return {
        "window": window_dict,
        "conversation_id": str(window_dict["conversation_id"]),
        "max_token_budget": window_dict["max_token_budget"],
    }


async def pt_load_recent(state: PassthroughRetrievalState) -> dict:
    """Load recent messages as-is, up to the token budget."""
    pool = get_pg_pool()
    max_budget = state["max_token_budget"]
    max_messages = get_tuning(state["config"], "max_messages_to_load", 1000)

    rows = await pool.fetch(
        """
        SELECT id, role, sender, content, sequence_number, token_count,
               tool_calls, tool_call_id, created_at
        FROM conversation_messages
        WHERE conversation_id = $1
        ORDER BY sequence_number DESC
        LIMIT $2
        """,
        uuid.UUID(state["conversation_id"]),
        max_messages,
    )

    messages = []
    tokens_used = 0
    for row in rows:
        msg = dict(row)
        msg_tokens = msg.get("token_count") or max(1, len(msg.get("content", "") or "") // 4)
        if tokens_used + msg_tokens > max_budget:
            break
        messages.insert(0, msg)
        tokens_used += msg_tokens

    # Build output messages array
    context_messages = []
    recent_for_tiers = []
    for m in messages:
        out = {"role": m["role"], "content": m.get("content", "")}
        if m.get("tool_calls"):
            out["tool_calls"] = m["tool_calls"]
        if m.get("tool_call_id"):
            out["tool_call_id"] = m["tool_call_id"]
        if m.get("sender"):
            out["name"] = m["sender"]
        context_messages.append(out)
        recent_for_tiers.append({
            "id": str(m["id"]),
            "role": m["role"],
            "sender": m.get("sender", ""),
            "content": m.get("content", ""),
            "sequence_number": m["sequence_number"],
        })

    context_tiers = {
        "archival_summary": None,
        "chunk_summaries": [],
        "semantic_messages": [],
        "knowledge_graph_facts": [],
        "recent_messages": recent_for_tiers,
    }

    return {
        "context_messages": context_messages,
        "context_tiers": context_tiers,
        "total_tokens_used": tokens_used,
    }


def pt_ret_route_after_load(state: PassthroughRetrievalState) -> str:
    if state.get("error"):
        return END
    return "pt_load_recent"


def build_passthrough_retrieval():
    """Build and compile the passthrough retrieval StateGraph."""
    workflow = StateGraph(PassthroughRetrievalState)

    workflow.add_node("pt_load_window", pt_load_window)
    workflow.add_node("pt_load_recent", pt_load_recent)

    workflow.set_entry_point("pt_load_window")
    workflow.add_conditional_edges(
        "pt_load_window",
        pt_ret_route_after_load,
        {"pt_load_recent": "pt_load_recent", END: END},
    )
    workflow.add_edge("pt_load_recent", END)

    return workflow.compile()


# ============================================================
# Registration
# ============================================================

register_build_type("passthrough", build_passthrough_assembly, build_passthrough_retrieval)
```

---

`app/flows/build_types/standard_tiered.py`

```python
"""
Standard-tiered build type (ARCH-18).

Three-tier progressive compression context assembly:
  Tier 1: Archival summary (oldest, most compressed)
  Tier 2: Chunk summaries (middle layer)
  Tier 3: Recent verbatim messages (newest, full fidelity)

Moved from the monolithic context_assembly.py and retrieval_flow.py.
All original logic, error handling, and lock management preserved.

F-06: Reads its own LLM config from config["build_types"]["standard-tiered"]["llm"],
falling back to the global config["llm"] if not set.
"""

import asyncio
import logging
import operator
import time
import uuid
from typing import Annotated, Optional

import asyncpg
import httpx
import openai
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_build_type_config, get_chat_model, get_tuning, verbose_log
from app.database import get_pg_pool, get_redis
from app.flows.build_type_registry import register_build_type
from app.flows.build_types.tier_scaling import scale_tier_percentages
from app.metrics_registry import CONTEXT_ASSEMBLY_DURATION
from app.prompt_loader import async_load_prompt

_log = logging.getLogger("context_broker.flows.build_types.standard_tiered")


def _resolve_llm_config(config: dict, build_type_config: dict) -> dict:
    """Resolve effective LLM config: build-type-specific overrides global (F-06).

    Returns a config dict suitable for passing to get_chat_model().
    """
    bt_llm = build_type_config.get("llm")
    if bt_llm:
        # Build a config dict that get_chat_model expects (top-level "llm" key)
        return {**config, "llm": bt_llm}
    return config


# ============================================================
# Assembly
# ============================================================


class StandardTieredAssemblyState(TypedDict):
    """State for the standard-tiered assembly pipeline."""

    # Inputs
    context_window_id: str
    conversation_id: str
    config: dict

    # Intermediate
    window: Optional[dict]
    build_type_config: Optional[dict]
    max_token_budget: int
    all_messages: list[dict]
    tier3_messages: list[dict]
    older_messages: list[dict]
    chunks: list[list[dict]]
    tier2_summaries: list[str]
    tier1_summary: Optional[str]
    lock_key: str
    lock_token: Optional[str]
    lock_acquired: bool
    had_errors: bool
    assembly_start_time: Optional[float]

    # Output
    error: Optional[str]


async def acquire_assembly_lock(state: StandardTieredAssemblyState) -> dict:
    """Acquire a Redis distributed lock for this context window."""
    # R5-M11: Validate UUID at assembly entry point to fail gracefully
    try:
        uuid.UUID(state["context_window_id"])
        uuid.UUID(state["conversation_id"])
    except (ValueError, AttributeError):
        _log.error(
            "Invalid UUID in assembly input: context_window_id=%s, conversation_id=%s",
            state.get("context_window_id"),
            state.get("conversation_id"),
        )
        return {"error": "Invalid UUID in assembly input", "lock_acquired": False}

    verbose_log(state["config"], _log, "standard_tiered.acquire_lock ENTER window=%s", state["context_window_id"])
    lock_key = f"assembly_in_progress:{state['context_window_id']}"
    lock_token = str(uuid.uuid4())
    redis = get_redis()

    acquired = await redis.set(
        lock_key, lock_token,
        ex=get_tuning(state["config"], "assembly_lock_ttl_seconds", 300),
        nx=True,
    )
    if not acquired:
        _log.info(
            "Context assembly: lock not acquired for window=%s — skipping",
            state["context_window_id"],
        )
        return {"lock_key": lock_key, "lock_token": None, "lock_acquired": False}

    return {"lock_key": lock_key, "lock_token": lock_token, "lock_acquired": True, "assembly_start_time": time.monotonic()}


async def load_window_config(state: StandardTieredAssemblyState) -> dict:
    """Load the context window and resolve its build type configuration."""
    pool = get_pg_pool()

    window = await pool.fetchrow(
        "SELECT * FROM context_windows WHERE id = $1",
        uuid.UUID(state["context_window_id"]),
    )
    if window is None:
        return {"error": f"Context window {state['context_window_id']} not found"}

    window_dict = dict(window)

    try:
        build_type_config = get_build_type_config(state["config"], window_dict["build_type"])
    except ValueError as exc:
        return {"error": str(exc)}

    return {
        "window": window_dict,
        "build_type_config": build_type_config,
        "max_token_budget": window_dict["max_token_budget"],
    }


async def load_messages(state: StandardTieredAssemblyState) -> dict:
    """Load messages for the conversation in chronological order.

    R2-F11: Adaptive message load limit. Instead of a fixed limit, estimate
    the needed messages from the tier3 token budget and average tokens per
    message. This avoids loading far more messages than could ever fit in
    the context window while still providing enough for summarization.
    """
    pool = get_pg_pool()
    build_type_config = state.get("build_type_config") or {}
    max_budget = state.get("max_token_budget", 8192)
    tier3_pct = build_type_config.get("tier3_pct", 0.72)
    tier3_budget = int(max_budget * tier3_pct)
    tokens_per_message = get_tuning(state["config"], "tokens_per_message_estimate", 150)
    adaptive_limit = max(50, tier3_budget // tokens_per_message)

    rows = await pool.fetch(
        """
        SELECT id, role, sender, content, sequence_number, token_count, created_at
        FROM conversation_messages
        WHERE conversation_id = $1
        ORDER BY sequence_number DESC
        LIMIT $2
        """,
        uuid.UUID(state["conversation_id"]),
        adaptive_limit,
    )

    rows = list(reversed(rows))
    messages = [dict(r) for r in rows]
    _log.info(
        "Context assembly: loaded %d messages for window=%s",
        len(messages),
        state["context_window_id"],
    )
    return {"all_messages": messages}


async def calculate_tier_boundaries(state: StandardTieredAssemblyState) -> dict:
    """Calculate which messages belong to each tier.

    F-05: Applies dynamic tier scaling based on conversation length.
    """
    messages = state["all_messages"]
    if not messages:
        return {"tier3_messages": [], "older_messages": [], "chunks": []}

    build_type_config = state["build_type_config"]
    max_budget = state["max_token_budget"]

    # F-05: Dynamic tier scaling
    scaled_config = scale_tier_percentages(build_type_config, len(messages))

    tier3_pct = scaled_config.get("tier3_pct", 0.72)
    tier3_budget = int(max_budget * tier3_pct)

    # Walk backwards to fill tier 3 budget
    tier3_messages = []
    tier3_tokens_used = 0
    tier3_start_seq = messages[-1]["sequence_number"] + 1

    for msg in reversed(messages):
        msg_tokens = msg.get("token_count") or max(1, len(msg.get("content") or "") // 4)
        if tier3_tokens_used + msg_tokens <= tier3_budget:
            tier3_messages.insert(0, msg)
            tier3_tokens_used += msg_tokens
            tier3_start_seq = msg["sequence_number"]
        else:
            break

    # Messages before tier 3 boundary need summarization
    older_messages = [m for m in messages if m["sequence_number"] < tier3_start_seq]

    # Incremental: find what's already covered by existing tier 2 summaries
    pool = get_pg_pool()
    existing_t2 = await pool.fetch(
        """
        SELECT summarizes_to_seq
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND tier = 2
          AND is_active = TRUE
        ORDER BY summarizes_to_seq DESC
        LIMIT 1
        """,
        uuid.UUID(state["context_window_id"]),
    )

    max_summarized_seq = 0
    if existing_t2:
        max_summarized_seq = existing_t2[0]["summarizes_to_seq"]

    # Only process messages not yet summarized
    unsummarized = [m for m in older_messages if m["sequence_number"] > max_summarized_seq]

    # Chunk unsummarized messages into groups
    chunk_size = get_tuning(state["config"], "chunk_size", 20)
    chunks = [
        unsummarized[i : i + chunk_size]
        for i in range(0, len(unsummarized), chunk_size)
    ]

    if max_summarized_seq > 0:
        _log.info(
            "Incremental assembly: %d new messages to summarize (already covered through seq %d)",
            len(unsummarized),
            max_summarized_seq,
        )

    return {
        "tier3_messages": tier3_messages,
        "older_messages": older_messages,
        "chunks": chunks,
    }


async def summarize_message_chunks(state: StandardTieredAssemblyState) -> dict:
    """LLM-summarize each new chunk of older messages."""
    chunks = state["chunks"]
    if not chunks:
        return {"tier2_summaries": []}

    config = state["config"]
    build_type_config = state.get("build_type_config") or {}

    # F-06: Use build-type-specific LLM config if available
    effective_config = _resolve_llm_config(config, build_type_config)
    llm_config = effective_config.get("llm", {})
    llm = get_chat_model(effective_config)

    pool = get_pg_pool()

    # M-23: Load prompt once before the concurrent calls
    try:
        chunk_prompt = await async_load_prompt("chunk_summarization")
    except RuntimeError as exc:
        _log.error("Failed to load chunk_summarization prompt: %s", exc)
        return {"tier2_summaries": [], "error": f"Prompt loading failed: {exc}"}

    # R5-M13: Prepare Redis client and lock info for TTL renewal during summarization
    redis = get_redis()
    lock_key = state.get("lock_key", "")
    lock_token = state.get("lock_token")
    lock_ttl = get_tuning(config, "assembly_lock_ttl_seconds", 300)

    async def _summarize_chunk(chunk: list[dict]) -> tuple[list[dict], str | None]:
        # R5-M13: Renew lock TTL before each chunk summarization to prevent
        # expiry during long-running LLM calls
        if lock_key and lock_token:
            current_val = await redis.get(lock_key)
            if current_val == lock_token:  # decode_responses=True, already a string
                await redis.expire(lock_key, lock_ttl)

        chunk_text = "\n".join(
            f"[{m['role']} | {m['sender']}] {m.get('content') or ''}" for m in chunk
        )
        messages = [
            SystemMessage(content=chunk_prompt),
            HumanMessage(content=chunk_text),
        ]
        try:
            response = await llm.ainvoke(messages)
            return (chunk, response.content)
        except (openai.APIError, httpx.HTTPError, ValueError) as exc:
            _log.error(
                "Chunk summarization failed for window=%s: %s",
                state["context_window_id"],
                exc,
            )
            return (chunk, None)

    # m16: Limit concurrent LLM calls
    max_concurrent = get_tuning(config, "max_concurrent_chunk_summaries", 5)
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _bounded_summarize(chunk: list[dict]) -> tuple[list[dict], str | None]:
        async with semaphore:
            return await _summarize_chunk(chunk)

    llm_results = await asyncio.gather(*[_bounded_summarize(chunk) for chunk in chunks])

    # M-09: Track whether any chunk failed
    had_errors = any(summary_text is None for _, summary_text in llm_results)

    # Insert summaries sequentially to preserve ordering
    new_summaries = []
    for chunk, summary_text in llm_results:
        if summary_text is None:
            continue

        chunk_tokens = sum(
            m.get("token_count") or max(1, len(m.get("content") or "") // 4) for m in chunk
        )

        # Idempotency: skip if summary already exists for this range
        existing = await pool.fetchval(
            """
            SELECT id FROM conversation_summaries
            WHERE context_window_id = $1
              AND tier = 2
              AND summarizes_from_seq = $2
              AND summarizes_to_seq = $3
              AND is_active = TRUE
            """,
            uuid.UUID(state["context_window_id"]),
            chunk[0]["sequence_number"],
            chunk[-1]["sequence_number"],
        )
        if existing:
            _log.info(
                "Skipping duplicate summary for seq %d-%d",
                chunk[0]["sequence_number"],
                chunk[-1]["sequence_number"],
            )
            continue

        # R5-M14: Catch UniqueViolationError in case a concurrent assembler
        # already inserted a summary for this range between our pre-check
        # and this INSERT.
        try:
            await pool.execute(
                """
                INSERT INTO conversation_summaries
                    (conversation_id, context_window_id, summary_text, tier,
                     summarizes_from_seq, summarizes_to_seq, message_count,
                     original_token_count, summary_token_count, summarized_by_model)
                VALUES ($1, $2, $3, 2, $4, $5, $6, $7, $8, $9)
                """,
                uuid.UUID(state["conversation_id"]),
                uuid.UUID(state["context_window_id"]),
                summary_text,
                chunk[0]["sequence_number"],
                chunk[-1]["sequence_number"],
                len(chunk),
                chunk_tokens,
                len(summary_text) // 4,
                llm_config.get("model", "unknown"),
            )
        except asyncpg.UniqueViolationError:
            _log.info(
                "Concurrent summary insert for seq %d-%d — skipping (other assembler won)",
                chunk[0]["sequence_number"],
                chunk[-1]["sequence_number"],
            )
            continue
        new_summaries.append(summary_text)

    _log.info(
        "Context assembly: wrote %d tier 2 summaries for window=%s",
        len(new_summaries),
        state["context_window_id"],
    )
    result: dict = {"tier2_summaries": new_summaries}
    if had_errors:
        result["had_errors"] = True
    return result


async def consolidate_archival_summary(state: StandardTieredAssemblyState) -> dict:
    """Consolidate oldest tier 2 summaries into a tier 1 archival summary."""
    pool = get_pg_pool()

    active_t2 = await pool.fetch(
        """
        SELECT id, summary_text, summarizes_from_seq, summarizes_to_seq, message_count
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND tier = 2
          AND is_active = TRUE
        ORDER BY summarizes_from_seq ASC
        """,
        uuid.UUID(state["context_window_id"]),
    )

    if len(active_t2) <= get_tuning(state["config"], "consolidation_threshold", 3):
        return {"tier1_summary": None}

    keep_recent = get_tuning(state["config"], "consolidation_keep_recent", 2)

    # R6-M11: Guard against empty consolidation list when keep_recent >= len(active_t2)
    if len(active_t2) <= keep_recent:
        return {"tier1_summary": None}

    to_consolidate = list(active_t2)[:-keep_recent]

    # M-16: Include existing tier 1 summary in consolidation
    existing_t1 = await pool.fetchrow(
        """
        SELECT summary_text
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND tier = 1
          AND is_active = TRUE
        ORDER BY summarizes_to_seq DESC
        LIMIT 1
        """,
        uuid.UUID(state["context_window_id"]),
    )

    consolidation_parts = []
    if existing_t1 and existing_t1["summary_text"]:
        consolidation_parts.append(
            f"[Existing archival summary]\n{existing_t1['summary_text']}"
        )
    consolidation_parts.extend(r["summary_text"] for r in to_consolidate)
    consolidation_text = "\n\n".join(consolidation_parts)

    config = state["config"]
    build_type_config = state.get("build_type_config") or {}

    # F-06: Use build-type-specific LLM config if available
    effective_config = _resolve_llm_config(config, build_type_config)
    llm_config = effective_config.get("llm", {})

    # M-23: Catch prompt-loading failures
    try:
        archival_prompt = await async_load_prompt("archival_consolidation")
    except RuntimeError as exc:
        _log.error("Failed to load archival_consolidation prompt: %s", exc)
        return {"tier1_summary": None, "error": f"Prompt loading failed: {exc}"}

    llm = get_chat_model(effective_config)

    messages = [
        SystemMessage(content=archival_prompt),
        HumanMessage(content=consolidation_text),
    ]

    try:
        response = await llm.ainvoke(messages)
        archival_text = response.content

        if archival_text:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Deactivate existing tier 1
                    await conn.execute(
                        """
                        UPDATE conversation_summaries
                        SET is_active = FALSE
                        WHERE context_window_id = $1 AND tier = 1 AND is_active = TRUE
                        """,
                        uuid.UUID(state["context_window_id"]),
                    )

                    # Deactivate consolidated tier 2 chunks
                    consolidated_ids = [r["id"] for r in to_consolidate]
                    await conn.execute(
                        """
                        UPDATE conversation_summaries
                        SET is_active = FALSE
                        WHERE id = ANY($1::uuid[])
                        """,
                        consolidated_ids,
                    )

                    # Insert new tier 1 archival summary
                    await conn.execute(
                        """
                        INSERT INTO conversation_summaries
                            (conversation_id, context_window_id, summary_text, tier,
                             summarizes_from_seq, summarizes_to_seq, message_count,
                             summarized_by_model)
                        VALUES ($1, $2, $3, 1, $4, $5, $6, $7)
                        """,
                        uuid.UUID(state["conversation_id"]),
                        uuid.UUID(state["context_window_id"]),
                        archival_text,
                        to_consolidate[0]["summarizes_from_seq"],
                        to_consolidate[-1]["summarizes_to_seq"],
                        sum(r.get("message_count") or 0 for r in to_consolidate),
                        llm_config.get("model", "unknown"),
                    )

            _log.info(
                "Context assembly: consolidated %d tier 2 summaries into tier 1 for window=%s",
                len(to_consolidate),
                state["context_window_id"],
            )
            return {"tier1_summary": archival_text}

    except (openai.APIError, httpx.HTTPError, ValueError) as exc:
        _log.error(
            "Archival consolidation failed for window=%s: %s",
            state["context_window_id"],
            exc,
        )
        return {"tier1_summary": None, "had_errors": True}

    return {"tier1_summary": None}


async def finalize_assembly(state: StandardTieredAssemblyState) -> dict:
    """Update last_assembled_at and observe duration metric.

    M-09: Skip last_assembled_at update if there were partial failures.
    """
    if state.get("had_errors"):
        _log.warning(
            "Context assembly had errors for window=%s — skipping last_assembled_at update",
            state["context_window_id"],
        )
    else:
        pool = get_pg_pool()
        await pool.execute(
            "UPDATE context_windows SET last_assembled_at = NOW() WHERE id = $1",
            uuid.UUID(state["context_window_id"]),
        )

    start_time = state.get("assembly_start_time")
    if start_time is not None:
        duration = time.monotonic() - start_time
        CONTEXT_ASSEMBLY_DURATION.labels(build_type="standard-tiered").observe(duration)

    _log.info(
        "Context assembly %s for window=%s",
        "complete (with errors)" if state.get("had_errors") else "complete",
        state["context_window_id"],
    )
    return {}


async def _atomic_lock_release(redis_client, lock_key: str, lock_token: str) -> bool:
    """Atomically release a Redis lock only if we still own it (CB-R3-02)."""
    lua_script = """
    if redis.call("GET", KEYS[1]) == ARGV[1] then
        return redis.call("DEL", KEYS[1])
    else
        return 0
    end
    """
    result = await redis_client.eval(lua_script, 1, lock_key, lock_token)
    return result == 1


async def release_assembly_lock(state: StandardTieredAssemblyState) -> dict:
    """Release the Redis assembly lock."""
    lock_key = state.get("lock_key", "")
    lock_token = state.get("lock_token")
    if lock_key and state.get("lock_acquired") and lock_token:
        redis = get_redis()
        released = await _atomic_lock_release(redis, lock_key, lock_token)
        if not released:
            _log.debug(
                "Lock %s was not released (expired or taken by another worker)",
                lock_key,
            )
    return {}


def route_after_lock(state: StandardTieredAssemblyState) -> str:
    if not state.get("lock_acquired"):
        return END
    return "load_window_config"


def route_after_load_config(state: StandardTieredAssemblyState) -> str:
    if state.get("error"):
        return "release_assembly_lock"
    return "load_messages"


def route_after_load_messages(state: StandardTieredAssemblyState) -> str:
    if state.get("error"):
        return "release_assembly_lock"
    if not state.get("all_messages"):
        return "finalize_assembly"
    return "calculate_tier_boundaries"


def route_after_calculate_tiers(state: StandardTieredAssemblyState) -> str:
    if state.get("error"):
        return "release_assembly_lock"
    if not state.get("chunks"):
        return "consolidate_archival_summary"
    return "summarize_message_chunks"


def route_after_summarize(state: StandardTieredAssemblyState) -> str:
    if state.get("error"):
        return "release_assembly_lock"
    return "consolidate_archival_summary"


def build_standard_tiered_assembly():
    """Build and compile the standard-tiered assembly StateGraph."""
    workflow = StateGraph(StandardTieredAssemblyState)

    workflow.add_node("acquire_assembly_lock", acquire_assembly_lock)
    workflow.add_node("load_window_config", load_window_config)
    workflow.add_node("load_messages", load_messages)
    workflow.add_node("calculate_tier_boundaries", calculate_tier_boundaries)
    workflow.add_node("summarize_message_chunks", summarize_message_chunks)
    workflow.add_node("consolidate_archival_summary", consolidate_archival_summary)
    workflow.add_node("finalize_assembly", finalize_assembly)
    workflow.add_node("release_assembly_lock", release_assembly_lock)

    workflow.set_entry_point("acquire_assembly_lock")

    workflow.add_conditional_edges(
        "acquire_assembly_lock",
        route_after_lock,
        {"load_window_config": "load_window_config", END: END},
    )
    workflow.add_conditional_edges(
        "load_window_config",
        route_after_load_config,
        {"load_messages": "load_messages", "release_assembly_lock": "release_assembly_lock"},
    )
    workflow.add_conditional_edges(
        "load_messages",
        route_after_load_messages,
        {
            "calculate_tier_boundaries": "calculate_tier_boundaries",
            "finalize_assembly": "finalize_assembly",
            "release_assembly_lock": "release_assembly_lock",
        },
    )
    workflow.add_conditional_edges(
        "calculate_tier_boundaries",
        route_after_calculate_tiers,
        {
            "summarize_message_chunks": "summarize_message_chunks",
            "consolidate_archival_summary": "consolidate_archival_summary",
            "release_assembly_lock": "release_assembly_lock",
        },
    )
    workflow.add_conditional_edges(
        "summarize_message_chunks",
        route_after_summarize,
        {
            "consolidate_archival_summary": "consolidate_archival_summary",
            "release_assembly_lock": "release_assembly_lock",
        },
    )
    workflow.add_edge("consolidate_archival_summary", "finalize_assembly")
    workflow.add_edge("finalize_assembly", "release_assembly_lock")
    workflow.add_edge("release_assembly_lock", END)

    return workflow.compile()


# ============================================================
# Retrieval
# ============================================================


class StandardTieredRetrievalState(TypedDict):
    """State for the standard-tiered retrieval flow."""

    # Inputs
    context_window_id: str
    config: dict

    # Intermediate
    window: Optional[dict]
    build_type_config: Optional[dict]
    conversation_id: Optional[str]
    max_token_budget: int
    tier1_summary: Optional[str]
    tier2_summaries: list[str]
    recent_messages: list[dict]
    assembly_status: str

    # Output
    context_messages: Optional[list[dict]]
    context_tiers: Optional[dict]
    total_tokens_used: int
    warnings: Annotated[list[str], operator.add]  # R5-m5: accumulate, don't overwrite
    error: Optional[str]


async def ret_load_window(state: StandardTieredRetrievalState) -> dict:
    """Load the context window and its build type configuration."""
    # R5-M11: Validate UUID at retrieval entry point to fail gracefully
    try:
        uuid.UUID(state["context_window_id"])
    except (ValueError, AttributeError):
        _log.error(
            "Invalid UUID in retrieval input: context_window_id=%s",
            state.get("context_window_id"),
        )
        return {"error": "Invalid UUID in retrieval input", "assembly_status": "error"}

    verbose_log(state["config"], _log, "standard_tiered.retrieval.load_window ENTER window=%s", state["context_window_id"])
    pool = get_pg_pool()

    window = await pool.fetchrow(
        "SELECT * FROM context_windows WHERE id = $1",
        uuid.UUID(state["context_window_id"]),
    )
    if window is None:
        return {
            "error": f"Context window {state['context_window_id']} not found",
            "assembly_status": "error",
        }

    # Update last_accessed_at on every retrieval
    await pool.execute(
        "UPDATE context_windows SET last_accessed_at = NOW() WHERE id = $1",
        uuid.UUID(state["context_window_id"]),
    )

    window_dict = dict(window)

    try:
        build_type_config = get_build_type_config(state["config"], window_dict["build_type"])
    except ValueError as exc:
        return {"error": str(exc), "assembly_status": "error"}

    return {
        "window": window_dict,
        "build_type_config": build_type_config,
        "conversation_id": str(window_dict["conversation_id"]),
        "max_token_budget": window_dict["max_token_budget"],
    }


async def ret_wait_for_assembly(state: StandardTieredRetrievalState) -> dict:
    """Block if context assembly is in progress, with timeout.

    R6-M9: If Redis is unavailable, proceed without waiting rather than crashing.
    """
    try:
        redis = get_redis()
    except RuntimeError:
        _log.warning("Retrieval: Redis not available, proceeding without assembly wait")
        return {"assembly_status": "ready"}

    lock_key = f"assembly_in_progress:{state['context_window_id']}"

    timeout = get_tuning(state["config"], "assembly_wait_timeout_seconds", 50)
    poll_interval = get_tuning(state["config"], "assembly_poll_interval_seconds", 2)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            in_progress = await redis.exists(lock_key)
        except (ConnectionError, OSError, RuntimeError) as exc:
            _log.warning(
                "Retrieval: Redis error during assembly wait, proceeding: %s", exc
            )
            return {"assembly_status": "ready"}
        if not in_progress:
            return {"assembly_status": "ready"}

        elapsed = timeout - (deadline - time.monotonic())
        _log.info(
            "Retrieval: waiting for assembly on window=%s (%.0fs/%ds)",
            state["context_window_id"],
            elapsed,
            timeout,
        )
        await asyncio.sleep(poll_interval)

    _log.warning(
        "Retrieval: assembly timeout for window=%s after %ds",
        state["context_window_id"],
        timeout,
    )
    return {
        "assembly_status": "timeout",
        "warnings": [
            "Context assembly was still in progress at retrieval time; "
            "context may be stale."
        ],
    }


async def ret_load_summaries(state: StandardTieredRetrievalState) -> dict:
    """Load active tier 1 and tier 2 summaries."""
    pool = get_pg_pool()

    summaries = await pool.fetch(
        """
        SELECT tier, summary_text, summarizes_from_seq
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND is_active = TRUE
        ORDER BY tier ASC, summarizes_from_seq ASC
        """,
        uuid.UUID(state["context_window_id"]),
    )

    tier1 = None
    tier2_list = []
    for s in summaries:
        if s["tier"] == 1:
            tier1 = s["summary_text"]
        elif s["tier"] == 2:
            tier2_list.append(s["summary_text"])

    return {"tier1_summary": tier1, "tier2_summaries": tier2_list}


async def ret_load_recent_messages(state: StandardTieredRetrievalState) -> dict:
    """Load tier 3 recent verbatim messages within the remaining token budget.

    F-05: Applies dynamic tier scaling based on conversation length.
    """
    pool = get_pg_pool()
    build_type_config = state["build_type_config"]
    max_budget = state["max_token_budget"]

    # Count total messages for F-05 tier scaling
    total_msg_count = await pool.fetchval(
        "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = $1",
        uuid.UUID(state["conversation_id"]),
    )
    scaled_config = scale_tier_percentages(build_type_config, total_msg_count or 0)

    tier3_pct = scaled_config.get("tier3_pct", 0.72)
    tier3_budget = int(max_budget * tier3_pct)

    # Calculate tokens already used by summaries
    summary_tokens = 0
    if state.get("tier1_summary"):
        summary_tokens += len(state["tier1_summary"]) // 4
    for s in state.get("tier2_summaries", []):
        summary_tokens += len(s) // 4

    remaining_budget = max(0, min(tier3_budget, max_budget - summary_tokens))

    # M-06: Avoid loading messages already covered by summaries
    highest_summarized_seq = await pool.fetchval(
        """
        SELECT COALESCE(MAX(summarizes_to_seq), 0)
        FROM conversation_summaries
        WHERE context_window_id = $1
          AND tier = 2
          AND is_active = TRUE
        """,
        uuid.UUID(state["context_window_id"]),
    )

    # R2-F11: Adaptive message load limit based on tier3 token budget
    tokens_per_message = get_tuning(state["config"], "tokens_per_message_estimate", 150)
    adaptive_limit = max(50, tier3_budget // tokens_per_message)

    all_messages = await pool.fetch(
        """
        SELECT id, role, sender, content, sequence_number, token_count,
               tool_calls, tool_call_id, created_at
        FROM conversation_messages
        WHERE conversation_id = $1
          AND sequence_number > $2
        ORDER BY sequence_number DESC
        LIMIT $3
        """,
        uuid.UUID(state["conversation_id"]),
        highest_summarized_seq,
        adaptive_limit,
    )

    recent = []
    tokens_used = 0

    for msg in all_messages:
        msg_tokens = msg["token_count"] or max(1, len(msg.get("content") or "") // 4)
        if tokens_used + msg_tokens <= remaining_budget:
            recent.insert(0, dict(msg))
            tokens_used += msg_tokens
        else:
            break

    return {
        "recent_messages": recent,
        "total_tokens_used": summary_tokens + tokens_used,
    }


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text length (approx 4 chars per token)."""
    return max(1, len(text) // 4)


async def ret_assemble_context(state: StandardTieredRetrievalState) -> dict:
    """Assemble the final context messages array from all tiers.

    ARCH-03: Produces a structured messages array matching the OpenAI format.
    ARCH-15: Summaries are inserted as system messages.
    """
    max_budget = state.get("max_token_budget", 0)
    cumulative_tokens = 0
    messages: list[dict] = []

    # Tier 1: Archival summary
    if state.get("tier1_summary"):
        content = f"[Archival context]\n{state['tier1_summary']}"
        cumulative_tokens += _estimate_tokens(content)
        messages.append({"role": "system", "content": content})

    # Tier 2: Chunk summaries
    if state.get("tier2_summaries"):
        content = "[Recent summaries]\n" + "\n\n".join(state["tier2_summaries"])
        cumulative_tokens += _estimate_tokens(content)
        messages.append({"role": "system", "content": content})

    # Tier 3: Recent verbatim messages (M-08: newest first truncation)
    truncated_recent_messages: list[dict] = []
    if state.get("recent_messages"):
        remaining = max(0, max_budget - cumulative_tokens) if max_budget else float("inf")
        msg_tokens = 0
        for m in reversed(state["recent_messages"]):
            msg_content = m.get("content", "")
            msg_token_count = _estimate_tokens(msg_content)
            if msg_tokens + msg_token_count > remaining:
                break
            truncated_recent_messages.insert(0, m)
            msg_tokens += msg_token_count
        for m in truncated_recent_messages:
            msg = {"role": m["role"], "content": m["content"]}
            if m.get("tool_calls"):
                msg["tool_calls"] = m["tool_calls"]
            if m.get("tool_call_id"):
                msg["tool_call_id"] = m["tool_call_id"]
            if m.get("sender"):
                msg["name"] = m["sender"]
            messages.append(msg)
            cumulative_tokens += _estimate_tokens(m.get("content", ""))

    context_tiers = {
        "archival_summary": state.get("tier1_summary"),
        "chunk_summaries": state.get("tier2_summaries", []),
        "semantic_messages": [],
        "knowledge_graph_facts": [],
        "recent_messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "sender": m["sender"],
                "content": m["content"],
                "sequence_number": m["sequence_number"],
            }
            for m in truncated_recent_messages
        ],
    }

    return {
        "context_messages": messages,
        "context_tiers": context_tiers,
    }


def ret_route_after_load_window(state: StandardTieredRetrievalState) -> str:
    if state.get("error"):
        return END
    return "ret_wait_for_assembly"


def ret_route_after_wait(state: StandardTieredRetrievalState) -> str:
    return "ret_load_summaries"


def build_standard_tiered_retrieval():
    """Build and compile the standard-tiered retrieval StateGraph."""
    workflow = StateGraph(StandardTieredRetrievalState)

    workflow.add_node("ret_load_window", ret_load_window)
    workflow.add_node("ret_wait_for_assembly", ret_wait_for_assembly)
    workflow.add_node("ret_load_summaries", ret_load_summaries)
    workflow.add_node("ret_load_recent_messages", ret_load_recent_messages)
    workflow.add_node("ret_assemble_context", ret_assemble_context)

    workflow.set_entry_point("ret_load_window")

    workflow.add_conditional_edges(
        "ret_load_window",
        ret_route_after_load_window,
        {"ret_wait_for_assembly": "ret_wait_for_assembly", END: END},
    )
    workflow.add_conditional_edges(
        "ret_wait_for_assembly",
        ret_route_after_wait,
        {"ret_load_summaries": "ret_load_summaries"},
    )
    workflow.add_edge("ret_load_summaries", "ret_load_recent_messages")
    workflow.add_edge("ret_load_recent_messages", "ret_assemble_context")
    workflow.add_edge("ret_assemble_context", END)

    return workflow.compile()


# ============================================================
# Registration
# ============================================================

register_build_type("standard-tiered", build_standard_tiered_assembly, build_standard_tiered_retrieval)
```

---

`app/flows/build_types/tier_scaling.py`

```python
"""
Dynamic tier scaling (F-05).

Adjusts tier percentages based on conversation length. Short conversations
use more tier 3 (verbatim) budget. Long conversations shift budget toward
tier 1/tier 2 (compressed) layers to fit more history.

The config values are starting points; this function adjusts them.
"""

import logging

_log = logging.getLogger("context_broker.flows.build_types.tier_scaling")

# Thresholds for conversation length categories
_SHORT_CONVERSATION = 50
_LONG_CONVERSATION = 500


def scale_tier_percentages(
    build_type_config: dict,
    message_count: int,
) -> dict:
    """Return a copy of build_type_config with tier percentages adjusted for message count.

    F-05: Dynamic tier scaling.

    - Short conversations (< 50 messages): boost tier3 by shifting from tier1/tier2.
      Rationale: there's little to summarize, so maximize verbatim budget.
    - Medium conversations (50-500 messages): use config as-is.
    - Long conversations (> 500 messages): boost tier1/tier2 by shifting from tier3.
      Rationale: more history to compress, summaries are more valuable.

    The adjustment is a simple linear interpolation within each range.
    Non-tier percentage keys (knowledge_graph_pct, semantic_retrieval_pct) are
    left unchanged — only tier1/2/3 are rebalanced.

    Args:
        build_type_config: The build type config dict (must contain tier*_pct keys).
        message_count: Total number of messages in the conversation.

    Returns:
        A new dict with adjusted tier percentages.
    """
    config = dict(build_type_config)

    tier1_pct = config.get("tier1_pct", 0)
    tier2_pct = config.get("tier2_pct", 0)
    tier3_pct = config.get("tier3_pct", 0)

    # Only adjust if all three tiers are present
    if not (tier1_pct or tier2_pct or tier3_pct):
        return config

    tier_total = tier1_pct + tier2_pct + tier3_pct

    if message_count < _SHORT_CONVERSATION:
        # Short conversations: boost tier3 at the expense of tier1/tier2.
        # At 0 messages, shift 80% of tier1+tier2 budget to tier3.
        # Linear ramp from 80% shift at 0 messages to 0% shift at 50 messages.
        ratio = 1.0 - (message_count / _SHORT_CONVERSATION)
        shift_factor = 0.8 * ratio  # max 80% of tier1+tier2 shifts to tier3

        shift_amount = (tier1_pct + tier2_pct) * shift_factor
        # Distribute the reduction proportionally between tier1 and tier2
        if tier1_pct + tier2_pct > 0:
            t1_share = tier1_pct / (tier1_pct + tier2_pct)
            t2_share = tier2_pct / (tier1_pct + tier2_pct)
        else:
            t1_share = 0.5
            t2_share = 0.5

        config["tier1_pct"] = round(tier1_pct - shift_amount * t1_share, 4)
        config["tier2_pct"] = round(tier2_pct - shift_amount * t2_share, 4)
        config["tier3_pct"] = round(tier3_pct + shift_amount, 4)

        _log.debug(
            "F-05: Short conversation (%d msgs) — tier scaling: t1=%.2f%% t2=%.2f%% t3=%.2f%%",
            message_count,
            config["tier1_pct"] * 100,
            config["tier2_pct"] * 100,
            config["tier3_pct"] * 100,
        )

    elif message_count > _LONG_CONVERSATION:
        # Long conversations: boost tier1/tier2 at the expense of tier3.
        # Linear ramp from 0% shift at 500 messages to 30% shift at 2000 messages.
        excess = min(message_count - _LONG_CONVERSATION, 1500)
        ratio = excess / 1500
        shift_factor = 0.3 * ratio  # max 30% of tier3 shifts to tier1+tier2

        shift_amount = tier3_pct * shift_factor
        # Distribute the gain: 40% to tier1, 60% to tier2
        config["tier1_pct"] = round(tier1_pct + shift_amount * 0.4, 4)
        config["tier2_pct"] = round(tier2_pct + shift_amount * 0.6, 4)
        config["tier3_pct"] = round(tier3_pct - shift_amount, 4)

        _log.debug(
            "F-05: Long conversation (%d msgs) — tier scaling: t1=%.2f%% t2=%.2f%% t3=%.2f%%",
            message_count,
            config["tier1_pct"] * 100,
            config["tier2_pct"] * 100,
            config["tier3_pct"] * 100,
        )

    # Ensure no negative values (defensive)
    for key in ("tier1_pct", "tier2_pct", "tier3_pct"):
        if config.get(key, 0) < 0:
            config[key] = 0.0

    # R5-m7: Renormalize tier percentages so they sum to exactly the original
    # tier_total, avoiding floating-point drift after adjustments.
    new_tier_sum = config["tier1_pct"] + config["tier2_pct"] + config["tier3_pct"]
    if new_tier_sum > 0 and abs(new_tier_sum - tier_total) > 1e-9:
        factor = tier_total / new_tier_sum
        config["tier1_pct"] = round(config["tier1_pct"] * factor, 6)
        config["tier2_pct"] = round(config["tier2_pct"] * factor, 6)
        config["tier3_pct"] = round(config["tier3_pct"] * factor, 6)

    return config
```

---

`app/flows/context_assembly.py`

```python
"""
Context Assembly — backward-compatibility shim (ARCH-18).

The assembly logic has moved to app.flows.build_types.standard_tiered.
This module re-exports the original symbols so existing imports and
tests continue to work.

build_context_assembly() returns the standard-tiered assembly graph
for backward compatibility.
"""

# Re-export from the new location
from app.flows.build_types.standard_tiered import (  # noqa: F401
    StandardTieredAssemblyState as ContextAssemblyState,
    acquire_assembly_lock,
    calculate_tier_boundaries,
    consolidate_archival_summary,
    finalize_assembly,
    load_messages,
    load_window_config,
    release_assembly_lock,
    summarize_message_chunks,
    build_standard_tiered_assembly as build_context_assembly,
    _atomic_lock_release,
)
```

---

`app/flows/contracts.py`

```python
"""
Standard input/output contracts for build type graphs (ARCH-18).

Defines the TypedDict contracts that all assembly and retrieval graphs
must accept as input and produce as output. This decouples callers
(arq_worker, tool_dispatch) from specific build type implementations.
"""

from typing import Optional

from typing_extensions import TypedDict


class AssemblyInput(TypedDict):
    """Standard input contract for assembly graphs."""

    context_window_id: str
    conversation_id: str
    config: dict


class AssemblyOutput(TypedDict, total=False):
    """Standard output contract for assembly graphs.

    Assembly graphs store summaries directly in the DB and update
    last_assembled_at. The output carries status information.
    """

    error: Optional[str]


class RetrievalInput(TypedDict):
    """Standard input contract for retrieval graphs."""

    context_window_id: str
    config: dict


class RetrievalOutput(TypedDict, total=False):
    """Standard output contract for retrieval graphs."""

    context_messages: list[dict]
    context_tiers: dict
    total_tokens_used: int
    warnings: list[str]
    error: Optional[str]
```

---

`app/flows/conversation_ops_flow.py`

```python
"""
Conversation Operations — LangGraph StateGraph flows for CRUD operations.

Handles: conv_create_conversation, conv_create_context_window,
conv_get_history, conv_search_context_windows.

These are straightforward database operations wrapped in StateGraphs
per the LangGraph mandate (REQ §4.5).
"""

import logging
import uuid
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_build_type_config
from app.database import get_pg_pool
from app.token_budget import resolve_token_budget

_log = logging.getLogger("context_broker.flows.conversation_ops")


# ============================================================
# Create Conversation Flow
# ============================================================


class CreateConversationState(TypedDict):
    """State for conversation creation."""

    conversation_id: Optional[str]
    title: Optional[str]
    flow_id: Optional[str]
    user_id: Optional[str]
    error: Optional[str]


async def create_conversation_node(state: CreateConversationState) -> dict:
    """Insert a new conversation record into PostgreSQL.

    Supports caller-supplied IDs (F-13) via ON CONFLICT DO NOTHING
    for idempotent create-or-return.
    """
    pool = get_pg_pool()

    # F-13: Use caller-supplied ID if provided, otherwise generate one
    if state.get("conversation_id"):
        try:
            new_id = uuid.UUID(state["conversation_id"])
        except ValueError:
            return {"error": f"Invalid conversation_id format: {state['conversation_id']}"}
    else:
        new_id = uuid.uuid4()

    row = await pool.fetchrow(
        """
        INSERT INTO conversations (id, title, flow_id, user_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (id) DO NOTHING
        RETURNING id, title, created_at, updated_at, total_messages, estimated_token_count
        """,
        new_id,
        state.get("title"),
        state.get("flow_id"),
        state.get("user_id"),
    )

    if row is None:
        # Already exists — return existing conversation ID
        return {"conversation_id": str(new_id)}

    return {"conversation_id": str(row["id"])}


def build_create_conversation_flow() -> StateGraph:
    """Build and compile the create conversation StateGraph."""
    workflow = StateGraph(CreateConversationState)
    workflow.add_node("create_conversation_node", create_conversation_node)
    workflow.set_entry_point("create_conversation_node")
    workflow.add_edge("create_conversation_node", END)
    return workflow.compile()


# ============================================================
# Create Context Window Flow
# ============================================================


class CreateContextWindowState(TypedDict):
    """State for context window creation."""

    conversation_id: str
    participant_id: str
    build_type: str
    max_tokens_override: Optional[int]
    config: dict

    context_window_id: Optional[str]
    build_type_config: Optional[dict]
    resolved_token_budget: int
    error: Optional[str]


async def resolve_token_budget_node(state: CreateContextWindowState) -> dict:
    """Resolve the token budget for the new context window."""
    config = state["config"]

    try:
        build_type_config = get_build_type_config(config, state["build_type"])
    except ValueError as exc:
        return {"error": str(exc)}

    token_budget = await resolve_token_budget(
        config=config,
        build_type_config=build_type_config,
        caller_override=state.get("max_tokens_override"),
    )

    return {"resolved_token_budget": token_budget, "build_type_config": build_type_config}


async def create_context_window_node(state: CreateContextWindowState) -> dict:
    """Insert the context window record into PostgreSQL."""
    if state.get("error"):
        return {}

    pool = get_pg_pool()

    # Verify conversation exists
    conversation = await pool.fetchrow(
        "SELECT id FROM conversations WHERE id = $1",
        uuid.UUID(state["conversation_id"]),
    )
    if conversation is None:
        return {"error": f"Conversation {state['conversation_id']} not found"}

    # G5-08: Idempotent creation — use ON CONFLICT DO NOTHING on the
    # unique constraint (conversation_id, participant_id, build_type).
    # If the window already exists, return the existing ID.
    row = await pool.fetchrow(
        """
        INSERT INTO context_windows
            (conversation_id, participant_id, build_type, max_token_budget)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (conversation_id, participant_id, build_type) DO NOTHING
        RETURNING id, conversation_id, participant_id, build_type, max_token_budget, created_at
        """,
        uuid.UUID(state["conversation_id"]),
        state["participant_id"],
        state["build_type"],
        state["resolved_token_budget"],
    )

    if row is None:
        # Already exists — look up the existing window
        existing = await pool.fetchrow(
            """
            SELECT id FROM context_windows
            WHERE conversation_id = $1 AND participant_id = $2 AND build_type = $3
            """,
            uuid.UUID(state["conversation_id"]),
            state["participant_id"],
            state["build_type"],
        )
        # R5-M21: Defensive None check — should not happen but prevents crash
        if existing is None:
            return {"error": "Context window conflict: INSERT returned nothing and existing row not found"}
        return {"context_window_id": str(existing["id"])}

    return {"context_window_id": str(row["id"])}


def route_after_resolve_budget(state: CreateContextWindowState) -> str:
    """Route: if error, end. Otherwise create the window."""
    if state.get("error"):
        return END
    return "create_context_window_node"


def build_create_context_window_flow() -> StateGraph:
    """Build and compile the create context window StateGraph."""
    workflow = StateGraph(CreateContextWindowState)

    workflow.add_node("resolve_token_budget_node", resolve_token_budget_node)
    workflow.add_node("create_context_window_node", create_context_window_node)

    workflow.set_entry_point("resolve_token_budget_node")

    workflow.add_conditional_edges(
        "resolve_token_budget_node",
        route_after_resolve_budget,
        {"create_context_window_node": "create_context_window_node", END: END},
    )

    workflow.add_edge("create_context_window_node", END)
    return workflow.compile()


# ============================================================
# Get History Flow
# ============================================================


class GetHistoryState(TypedDict):
    """State for conversation history retrieval."""

    conversation_id: str
    limit: Optional[int]

    conversation: Optional[dict]
    messages: list[dict]
    error: Optional[str]


async def load_conversation_and_messages(state: GetHistoryState) -> dict:
    """Load conversation metadata and all messages in chronological order."""
    pool = get_pg_pool()

    conversation = await pool.fetchrow(
        "SELECT id, title, created_at, updated_at, total_messages, estimated_token_count FROM conversations WHERE id = $1",
        uuid.UUID(state["conversation_id"]),
    )
    if conversation is None:
        return {"error": f"Conversation {state['conversation_id']} not found"}

    conv_dict = dict(conversation)
    conv_dict["id"] = str(conv_dict["id"])
    if conv_dict.get("created_at"):
        conv_dict["created_at"] = conv_dict["created_at"].isoformat()
    if conv_dict.get("updated_at"):
        conv_dict["updated_at"] = conv_dict["updated_at"].isoformat()

    limit = state.get("limit")
    if limit:
        # G5-09: Use a subquery to get the most recent N messages (not oldest N),
        # then re-sort in chronological order for the caller.
        rows = await pool.fetch(
            """
            SELECT * FROM (
                SELECT id, role, sender, recipient, content, sequence_number,
                       token_count, model_name, created_at
                FROM conversation_messages
                WHERE conversation_id = $1
                ORDER BY sequence_number DESC
                LIMIT $2
            ) sub
            ORDER BY sequence_number ASC
            """,
            uuid.UUID(state["conversation_id"]),
            limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, role, sender, recipient, content, sequence_number,
                   token_count, model_name, created_at
            FROM conversation_messages
            WHERE conversation_id = $1
            ORDER BY sequence_number ASC
            """,
            uuid.UUID(state["conversation_id"]),
        )

    messages = []
    for row in rows:
        m = dict(row)
        m["id"] = str(m["id"])
        if m.get("created_at"):
            m["created_at"] = m["created_at"].isoformat()
        messages.append(m)

    return {"conversation": conv_dict, "messages": messages}


def build_get_history_flow() -> StateGraph:
    """Build and compile the get history StateGraph."""
    workflow = StateGraph(GetHistoryState)
    workflow.add_node("load_conversation_and_messages", load_conversation_and_messages)
    workflow.set_entry_point("load_conversation_and_messages")
    workflow.add_edge("load_conversation_and_messages", END)
    return workflow.compile()


# ============================================================
# Search Context Windows Flow
# ============================================================


class SearchContextWindowsState(TypedDict):
    """State for context window search."""

    context_window_id: Optional[str]
    conversation_id: Optional[str]
    participant_id: Optional[str]
    build_type: Optional[str]
    limit: int

    results: list[dict]
    error: Optional[str]


async def search_context_windows_node(state: SearchContextWindowsState) -> dict:
    """Search context windows by various filters.

    If context_window_id is provided, look up that specific window directly
    (M-20), bypassing other filters.
    """
    pool = get_pg_pool()

    # M-20: Direct lookup by context_window_id if provided
    if state.get("context_window_id"):
        row = await pool.fetchrow(
            """
            SELECT id, conversation_id, participant_id, build_type,
                   max_token_budget, last_assembled_at, created_at
            FROM context_windows
            WHERE id = $1
            """,
            uuid.UUID(state["context_window_id"]),
        )
        if row is None:
            return {"results": []}
        r = dict(row)
        r["id"] = str(r["id"])
        r["conversation_id"] = str(r["conversation_id"])
        if r.get("last_assembled_at"):
            r["last_assembled_at"] = r["last_assembled_at"].isoformat()
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
        return {"results": [r]}

    conditions = []
    args: list = []
    arg_idx = 1

    if state.get("conversation_id"):
        conditions.append(f"conversation_id = ${arg_idx}")
        args.append(uuid.UUID(state["conversation_id"]))
        arg_idx += 1

    if state.get("participant_id"):
        conditions.append(f"participant_id = ${arg_idx}")
        args.append(state["participant_id"])
        arg_idx += 1

    if state.get("build_type"):
        conditions.append(f"build_type = ${arg_idx}")
        args.append(state["build_type"])
        arg_idx += 1

    # Safety note: where_clause is built from fixed column-name strings above
    # (never from user input), so f-string interpolation is safe here.
    # The actual filter values are passed as bind parameters ($1, $2, etc.).
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    args.append(state["limit"])

    rows = await pool.fetch(
        f"""
        SELECT id, conversation_id, participant_id, build_type,
               max_token_budget, last_assembled_at, created_at
        FROM context_windows
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT ${arg_idx}
        """,
        *args,
    )

    results = []
    for row in rows:
        r = dict(row)
        r["id"] = str(r["id"])
        r["conversation_id"] = str(r["conversation_id"])
        if r.get("last_assembled_at"):
            r["last_assembled_at"] = r["last_assembled_at"].isoformat()
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
        results.append(r)

    return {"results": results}


def build_search_context_windows_flow() -> StateGraph:
    """Build and compile the search context windows StateGraph."""
    workflow = StateGraph(SearchContextWindowsState)
    workflow.add_node("search_context_windows_node", search_context_windows_node)
    workflow.set_entry_point("search_context_windows_node")
    workflow.add_edge("search_context_windows_node", END)
    return workflow.compile()
```

---

`app/flows/embed_pipeline.py`

```python
"""
Embed Pipeline — LangGraph StateGraph flow (background).

Generates vector embeddings for stored messages using the configured
LangChain embedding model. After embedding, enqueues context assembly
jobs for all context windows attached to the conversation.

Triggered by ARQ worker consuming from embedding_jobs queue.
"""

import json
import logging
import time as _time_mod
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
import openai
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_embeddings_model, get_tuning, verbose_log
from app.database import get_pg_pool, get_redis
from app.metrics_registry import JOBS_ENQUEUED

_log = logging.getLogger("context_broker.flows.embed_pipeline")


class EmbedPipelineState(TypedDict):
    """State for the embedding pipeline."""

    # Inputs
    message_id: str
    conversation_id: str
    config: dict

    # Intermediate
    message: Optional[dict]
    embedding: Optional[list[float]]

    # Outputs
    assembly_jobs_queued: list[str]
    error: Optional[str]


async def fetch_message(state: EmbedPipelineState) -> dict:
    """Fetch the message row from PostgreSQL."""
    verbose_log(state["config"], _log, "embed_pipeline.fetch_message ENTER msg=%s", state["message_id"])
    pool = get_pg_pool()
    row = await pool.fetchrow(
        "SELECT * FROM conversation_messages WHERE id = $1",
        uuid.UUID(state["message_id"]),
    )
    if row is None:
        return {"error": f"Message {state['message_id']} not found"}
    return {"message": dict(row)}


async def generate_embedding(state: EmbedPipelineState) -> dict:
    """Generate a vector embedding for the message content.

    Uses the configured LangChain OpenAIEmbeddings model.
    Builds contextual embedding using prior messages as prefix.
    """
    _t0 = _time_mod.monotonic()
    config = state["config"]
    verbose_log(config, _log, "embed_pipeline.generate_embedding ENTER msg=%s", state["message_id"])
    message = state["message"]

    # ARCH-01: Tool-call messages may have NULL content — skip embedding entirely.
    # This is not an error; tool-call messages carry structured data, not text.
    if not message.get("content"):
        _log.info(
            "Skipping embedding for message %s: content is NULL (tool-call message)",
            state["message_id"],
        )
        return {"embedding": None}

    embeddings_config = config.get("embeddings", {})

    # Build contextual embedding text using prior messages
    context_window_size = embeddings_config.get("context_window_size", 3)
    embed_text = message["content"]

    if context_window_size > 0:
        pool = get_pg_pool()
        prior_rows = await pool.fetch(
            """
            SELECT role, content
            FROM conversation_messages
            WHERE conversation_id = $1
              AND sequence_number < $2
            ORDER BY sequence_number DESC
            LIMIT $3
            """,
            uuid.UUID(state["conversation_id"]),
            message["sequence_number"],
            context_window_size,
        )
        if prior_rows:
            prior_lines = "\n".join(
                f"[{r['role']}] {(r.get('content') or '')[:get_tuning(state['config'], 'content_truncation_chars', 500)]}"
                for r in reversed(prior_rows)
            )
            embed_text = f"[Context]\n{prior_lines}\n[Current]\n{message['content']}"

    # Use cached LangChain embeddings model
    embeddings_model = get_embeddings_model(config)

    try:
        embedding_vector = await embeddings_model.aembed_query(embed_text)
        return {"embedding": embedding_vector}
    except (openai.APIError, httpx.HTTPError, ValueError) as exc:
        _log.error(
            "Embedding generation failed for message %s: %s",
            state["message_id"],
            exc,
        )
        return {"error": f"Embedding failed: {exc}"}


async def store_embedding(state: EmbedPipelineState) -> dict:
    """Write the embedding vector to the message row in PostgreSQL.

    G5-10: This overwrites any existing embedding for the message. This is
    acceptable because embeddings are deterministic for a given content +
    model, so re-embedding produces the same (or improved) vector.
    """
    pool = get_pg_pool()
    embedding = state["embedding"]

    # R6-OPUS-08: asyncpg needs string format for pgvector. The ::vector cast
    # converts the string representation to the vector type.
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    await pool.execute(
        "UPDATE conversation_messages SET embedding = $1::vector WHERE id = $2",
        vec_str,
        uuid.UUID(state["message_id"]),
    )
    return {}


async def enqueue_context_assembly(state: EmbedPipelineState) -> dict:
    """Enqueue context assembly jobs for all context windows on this conversation.

    Each context window gets its own assembly job. Uses Redis distributed
    locks to prevent concurrent assembly of the same window.

    F-08: Only queues assembly when new tokens since last assembly exceed the
    trigger threshold (percentage of max_token_budget).
    """
    pool = get_pg_pool()
    redis = get_redis()
    config = state["config"]

    windows = await pool.fetch(
        "SELECT id, build_type, max_token_budget, last_assembled_at FROM context_windows WHERE conversation_id = $1",
        uuid.UUID(state["conversation_id"]),
    )

    # Get conversation token count for threshold check
    conv = await pool.fetchrow(
        "SELECT estimated_token_count FROM conversations WHERE id = $1",
        uuid.UUID(state["conversation_id"]),
    )
    conv_tokens = conv["estimated_token_count"] if conv else 0

    queued = []
    now = datetime.now(timezone.utc).isoformat()

    # G5-11: Batch the Redis EXISTS checks instead of N+1 queries per window.
    # Use mget on lock keys to check all windows in a single round-trip.
    window_ids = [str(w["id"]) for w in windows]
    lock_keys = [f"assembly_in_progress:{wid}" for wid in window_ids]
    lock_values = await redis.mget(*lock_keys) if lock_keys else []

    # Build a set of windows that already have assembly in progress
    locked_window_ids = {
        wid for wid, val in zip(window_ids, lock_values) if val is not None
    }

    # G5-11: Batch the token-since-last-assembly queries for windows that
    # have a last_assembled_at. Group by last_assembled_at to reduce queries.
    # (Each distinct timestamp still needs its own query, but windows sharing
    # the same timestamp are batched.)

    # R5-M10: Batch-fetch token counts since last assembly for all windows
    # instead of N+1 individual queries per window. One query fetches tokens
    # added after each distinct last_assembled_at timestamp.
    tokens_since_map: dict[str, int] = {}
    windows_needing_token_check = [
        w for w in windows
        if str(w["id"]) not in locked_window_ids and w["last_assembled_at"] is not None
    ]
    if windows_needing_token_check:
        # Batch query: for each window's last_assembled_at, get sum of tokens
        # added after that timestamp. Uses a VALUES list to do it in one round-trip.
        token_rows = await pool.fetch(
            """
            SELECT w.id AS window_id, COALESCE(SUM(m.token_count), 0) AS tokens_since
            FROM (SELECT unnest($1::uuid[]) AS id, unnest($2::timestamptz[]) AS last_assembled_at) w
            LEFT JOIN conversation_messages m
              ON m.conversation_id = $3
              AND m.created_at > w.last_assembled_at
            GROUP BY w.id
            """,
            [w["id"] for w in windows_needing_token_check],
            [w["last_assembled_at"] for w in windows_needing_token_check],
            uuid.UUID(state["conversation_id"]),
        )
        for row in token_rows:
            tokens_since_map[str(row["window_id"])] = row["tokens_since"]

    for window in windows:
        window_id = str(window["id"])

        if window_id in locked_window_ids:
            _log.debug(
                "Skipping assembly queue: already in progress for window=%s",
                window_id,
            )
            continue

        # F-08: Check trigger threshold — only queue if enough new tokens.
        # R6-M16: Known approximation — the threshold check counts all tokens since
        # last assembly, which for shared conversations may include tokens from other
        # participants' messages. Acceptable for triggering purposes (over-triggering
        # is safe, under-triggering is not).
        max_budget = window["max_token_budget"]
        bt_config = config.get("build_types", {}).get(window["build_type"], {})
        # CB-R3-03: The double-fallback (bt_config -> global tuning -> hardcoded 0.1)
        # is intentional: build-type-specific threshold takes priority, then the
        # global tuning knob, then a safe default. This avoids unnecessary assembly
        # when no threshold is configured at any level.
        trigger_pct = float(bt_config.get("trigger_threshold_percent", get_tuning(config, "trigger_threshold_percent", 0.1)))
        threshold_tokens = int(max_budget * trigger_pct)

        if window["last_assembled_at"] is not None:
            # R5-M10: Use batch-fetched token count instead of per-window query
            tokens_since = tokens_since_map.get(window_id, 0)
            if tokens_since < threshold_tokens:
                _log.debug(
                    "Skipping assembly: tokens_since=%d < threshold=%d for window=%s",
                    tokens_since,
                    threshold_tokens,
                    window_id,
                )
                continue

        # G5-12: Use SET NX for atomic dedup instead of racy EXISTS-then-LPUSH.
        # The dedup key expires after a short TTL to allow retries.
        dedup_key = f"assembly_dedup:{window_id}"
        dedup_acquired = await redis.set(dedup_key, "1", ex=60, nx=True)
        if not dedup_acquired:
            _log.debug(
                "Skipping assembly queue: dedup key exists for window=%s",
                window_id,
            )
            continue

        job = json.dumps(
            {
                "job_type": "assemble_context",
                "context_window_id": window_id,
                "conversation_id": state["conversation_id"],
                "build_type": window["build_type"],
                "enqueued_at": now,
            }
        )
        await redis.lpush("context_assembly_jobs", job)
        queued.append(window_id)
        JOBS_ENQUEUED.labels(job_type="assemble_context").inc()
        _log.info("Queued context assembly for window=%s", window_id)

    return {"assembly_jobs_queued": queued}


def route_after_fetch(state: EmbedPipelineState) -> str:
    """Route: if message not found, end. Otherwise generate embedding."""
    if state.get("error"):
        return END
    return "generate_embedding"


def route_after_embed(state: EmbedPipelineState) -> str:
    """Route: if embedding failed or skipped (null content), end. Otherwise store it."""
    if state.get("error"):
        return END
    if state.get("embedding") is None:
        return "enqueue_context_assembly"
    return "store_embedding"


def build_embed_pipeline() -> StateGraph:
    """Build and compile the embedding pipeline StateGraph."""
    workflow = StateGraph(EmbedPipelineState)

    workflow.add_node("fetch_message", fetch_message)
    workflow.add_node("generate_embedding", generate_embedding)
    workflow.add_node("store_embedding", store_embedding)
    workflow.add_node("enqueue_context_assembly", enqueue_context_assembly)

    workflow.set_entry_point("fetch_message")

    workflow.add_conditional_edges(
        "fetch_message",
        route_after_fetch,
        {"generate_embedding": "generate_embedding", END: END},
    )

    workflow.add_conditional_edges(
        "generate_embedding",
        route_after_embed,
        {
            "store_embedding": "store_embedding",
            "enqueue_context_assembly": "enqueue_context_assembly",
            END: END,
        },
    )

    workflow.add_edge("store_embedding", "enqueue_context_assembly")
    workflow.add_edge("enqueue_context_assembly", END)

    return workflow.compile()
```

---

`app/flows/health_flow.py`

```python
"""
Health Check — LangGraph StateGraph flow.

Checks connectivity to all backing services (PostgreSQL, Redis, Neo4j)
and returns aggregated health status. Invoked by the /health route.
"""

import logging
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.database import check_postgres_health, check_redis_health, check_neo4j_health

_log = logging.getLogger("context_broker.flows.health")


class HealthCheckState(TypedDict):
    """State for the health check flow."""

    config: dict

    postgres_ok: bool
    redis_ok: bool
    neo4j_ok: bool
    all_healthy: bool
    status_detail: Optional[dict]
    http_status: int


async def check_dependencies(state: HealthCheckState) -> dict:
    """Check connectivity to all backing services."""
    config = state["config"]

    postgres_ok = await check_postgres_health()
    redis_ok = await check_redis_health()
    neo4j_ok = await check_neo4j_health(config)

    all_healthy = postgres_ok and redis_ok

    if not all_healthy:
        status_label = "unhealthy"
        http_status = 503
    elif not neo4j_ok:
        # R6-m5: Intentionally returns 200 (not 503) when Neo4j is down.
        # Neo4j is optional — memory extraction degrades gracefully without it.
        # Only Postgres and Redis are critical; Neo4j unavailability is "degraded"
        # but the service remains functional for all core operations.
        status_label = "degraded"
        http_status = 200
    else:
        status_label = "healthy"
        http_status = 200

    status_detail = {
        "status": status_label,
        "database": "ok" if postgres_ok else "error",
        "cache": "ok" if redis_ok else "error",
        "neo4j": "ok" if neo4j_ok else "degraded",
    }

    if not all_healthy:
        _log.warning("Health check: unhealthy — %s", status_detail)
    elif not neo4j_ok:
        _log.warning("Health check: degraded — neo4j unavailable")

    return {
        "postgres_ok": postgres_ok,
        "redis_ok": redis_ok,
        "neo4j_ok": neo4j_ok,
        "all_healthy": all_healthy,
        "status_detail": status_detail,
        "http_status": http_status,
    }


def build_health_check_flow() -> StateGraph:
    """Build and compile the health check StateGraph."""
    workflow = StateGraph(HealthCheckState)
    workflow.add_node("check_dependencies", check_dependencies)
    workflow.set_entry_point("check_dependencies")
    workflow.add_edge("check_dependencies", END)
    return workflow.compile()
```

---

`app/flows/imperator_flow.py`

```python
"""
Imperator — LangGraph ReAct-style conversational agent flow.

The Imperator is the Context Broker's built-in conversational agent.
It uses a proper LangGraph ReAct graph (agent_node -> tool_node loop)
with no checkpointer — conversation history is loaded from PostgreSQL
on each invocation and results are stored via the standard message
pipeline (conv_store_message).

Uses LangChain's ChatOpenAI.bind_tools() for tool binding.

ARCH-05: ReAct loop is graph edges, not a while loop inside a node.
ARCH-06: No MemorySaver — DB is the persistence layer.
F-22:    Messages stored through conv_store_message pipeline.
"""

import copy
import logging
import re
import uuid
from typing import Annotated, Optional

import asyncpg
import httpx
import openai
import yaml
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from app.config import get_chat_model, get_tuning
from app.database import get_pg_pool
from app.prompt_loader import async_load_prompt

_log = logging.getLogger("context_broker.flows.imperator")


# ── State ────────────────────────────────────────────────────────────────

class ImperatorState(TypedDict):
    """State for the Imperator ReAct agent.

    ARCH-05: messages accumulates via add_messages reducer across
    agent_node <-> tool_node cycles.  The graph runs fresh each
    invocation — no checkpointer.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    context_window_id: Optional[str]
    config: dict
    response_text: Optional[str]
    error: Optional[str]
    iteration_count: int


# ── Tool singletons ─────────────────────────────────────────────────────

# Lazy-initialized flow singletons for Imperator tool functions.
_conv_search_flow_singleton = None
_mem_search_flow_singleton = None


def _get_conv_search_flow():
    global _conv_search_flow_singleton
    if _conv_search_flow_singleton is None:
        from app.flows.search_flow import build_conversation_search_flow
        _conv_search_flow_singleton = build_conversation_search_flow()
    return _conv_search_flow_singleton


def _get_mem_search_flow():
    global _mem_search_flow_singleton
    if _mem_search_flow_singleton is None:
        from app.flows.memory_search_flow import build_memory_search_flow
        _mem_search_flow_singleton = build_memory_search_flow()
    return _mem_search_flow_singleton


@tool
async def _conv_search_tool(query: str, limit: int = 5) -> str:
    """Search conversation history for relevant messages and conversations.

    Use this when the user asks about what was said, discussed, or decided
    in past conversations.

    Args:
        query: The search query describing what to find.
        limit: Maximum number of results to return (default 5).
    """
    from app.config import async_load_config

    config = await async_load_config()
    flow = _get_conv_search_flow()
    # R5-m11: Include all ConversationSearchState fields explicitly
    result = await flow.ainvoke(
        {
            "query": query,
            "limit": limit,
            "offset": 0,
            "date_from": None,
            "date_to": None,
            "flow_id": None,
            "user_id": None,
            "sender": None,
            "config": config,
            "query_embedding": None,
            "results": [],
            "warning": None,
            "error": None,
        }
    )
    results = result.get("results", [])
    if not results:
        return "No conversations found matching that query."
    lines = [f"Found {len(results)} conversation(s):"]
    for conv in results:
        lines.append(
            f"- {conv.get('title', 'Untitled')} (id: {conv['id']}, "
            f"messages: {conv.get('total_messages', 0)})"
        )
    return "\n".join(lines)


@tool
async def _mem_search_tool(query: str, user_id: str = "imperator", limit: int = 5) -> str:
    """Search extracted knowledge and memories from the knowledge graph.

    Use this when the user asks about facts, preferences, relationships,
    or anything that has been learned and stored as structured knowledge.

    Args:
        query: The search query describing what knowledge to find.
        user_id: The user whose memories to search (default: imperator).
        limit: Maximum number of results to return (default 5).
    """
    from app.config import async_load_config

    config = await async_load_config()
    flow = _get_mem_search_flow()
    result = await flow.ainvoke(
        {
            "query": query,
            "user_id": user_id,
            "limit": limit,
            "config": config,
            "memories": [],
            "relations": [],
            "degraded": False,
            "error": None,
        }
    )
    memories = result.get("memories", [])
    if not memories:
        return "No relevant memories found."
    lines = [f"Found {len(memories)} relevant memory/memories:"]
    for mem in memories:
        fact = mem.get("memory") or mem.get("content") or str(mem)
        lines.append(f"- {fact}")
    return "\n".join(lines)


# Module-level tool singletons (M-10)
_imperator_tools: list = [_conv_search_tool, _mem_search_tool]


def _redact_config(config: dict) -> dict:
    """Return a deep copy of *config* with sensitive values redacted (G5-16).

    Removes the top-level ``credentials`` section entirely and replaces any
    value whose key matches common secret patterns (api_key, secret, token,
    password) with ``"***REDACTED***"``.
    """
    redacted = copy.deepcopy(config)
    redacted.pop("credentials", None)

    # R6-m10: Use word boundaries to avoid false positives on keys like
    # "max_token_budget" — only match when the sensitive word is a distinct
    # component of the key name (e.g., "api_key", "db_password").
    _secret_key_re = re.compile(r"(api_key|secret|_token|password)", re.IGNORECASE)

    def _walk(obj: dict | list) -> None:
        if isinstance(obj, dict):
            for key in list(obj.keys()):
                if _secret_key_re.search(key) and obj[key]:
                    obj[key] = "***REDACTED***"
                elif isinstance(obj[key], (dict, list)):
                    _walk(obj[key])
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    _walk(item)

    _walk(redacted)
    return redacted


@tool
async def _config_read_tool() -> str:
    """Read the current config.yml contents (sensitive values are redacted).

    Admin-only tool. Returns the configuration as YAML text with credentials
    and API keys redacted for safety.
    """
    from app.config import CONFIG_PATH
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        sanitized = _redact_config(raw)
        return yaml.dump(sanitized, default_flow_style=False)
    except (FileNotFoundError, OSError, yaml.YAMLError) as exc:
        return f"Error reading config: {exc}"


@tool
async def _db_query_tool(sql: str) -> str:
    """Execute a read-only SQL query against the Context Broker database.

    Admin-only tool. The transaction is set to READ ONLY mode, so any
    DML/DDL will be rejected by PostgreSQL regardless of query structure.
    A 5-second statement timeout prevents expensive queries.

    Args:
        sql: A SQL query to execute (enforced read-only at the DB level).
    """
    import asyncpg

    # R5-M15: The real security boundary is SET TRANSACTION READ ONLY +
    # statement_timeout below. The SELECT prefix check was bypassable via
    # CTEs (e.g., WITH x AS (DELETE ...) SELECT ...) so it has been removed.
    # READ ONLY mode causes PostgreSQL to reject any DML/DDL regardless of
    # how the SQL is structured.
    try:
        pool = get_pg_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                await conn.execute("SET statement_timeout = '5000'")  # 5 second max
                rows = await conn.fetch(sql)
        if not rows:
            return "No results."
        # Format as text table
        columns = list(rows[0].keys())
        lines = [" | ".join(columns)]
        for row in rows[:50]:  # Limit to 50 rows
            lines.append(" | ".join(str(row[c]) for c in columns))
        return "\n".join(lines)
    except (asyncpg.PostgresError, OSError, RuntimeError) as exc:
        return f"Query error: {exc}"


# Admin tool singletons — gated by config
_admin_tools: list = [_config_read_tool, _db_query_tool]


# ── Message pipeline singleton ──────────────────────────────────────────

_message_pipeline_singleton = None


def _get_message_pipeline():
    """Lazy-init the standard message pipeline flow."""
    global _message_pipeline_singleton
    if _message_pipeline_singleton is None:
        from app.flows.message_pipeline import build_message_pipeline
        _message_pipeline_singleton = build_message_pipeline()
    return _message_pipeline_singleton


# ── Helper: load DB history ─────────────────────────────────────────────

async def _load_conversation_history(context_window_id: str, config: dict) -> str:
    """Load recent conversation history from PostgreSQL for context.

    ARCH-06: History comes from the DB, not a checkpointer.  Returns a
    formatted string to embed in the system prompt.
    """
    history_limit = get_tuning(config, "imperator_history_limit", 20)
    try:
        pool = get_pg_pool()
        # Look up conversation_id from context_windows
        cw_row = await pool.fetchrow(
            "SELECT conversation_id FROM context_windows WHERE id = $1",
            uuid.UUID(context_window_id),
        )
        if cw_row is None:
            _log.warning("Context window %s not found", context_window_id)
            return ""

        conversation_id = cw_row["conversation_id"]
        rows = await pool.fetch(
            """
            SELECT role, content
            FROM conversation_messages
            WHERE conversation_id = $1
            ORDER BY sequence_number DESC
            LIMIT $2
            """,
            conversation_id,
            history_limit,
        )
        if not rows:
            return ""

        history_lines = []
        for row in reversed(rows):
            row_content = row.get("content") or ""
            history_lines.append(f"[{row['role']}]: {row_content}")
        return (
            "\n\n--- Recent conversation history (for context) ---\n"
            + "\n".join(history_lines)
            + "\n--- End of history ---\n"
        )
    except (RuntimeError, OSError, asyncpg.PostgresError) as exc:
        _log.warning("Failed to load Imperator history: %s", exc)
        return ""


# ── Graph nodes ──────────────────────────────────────────────────────────

async def agent_node(state: ImperatorState) -> dict:
    """Call the LLM with bound tools and return the response.

    ARCH-05: This node contains NO loop.  Flow control (tool-call vs
    final answer) is handled by the conditional edge after this node.

    On the first call (no system prompt in messages yet), loads DB
    history and prepends the system prompt + history context.
    """
    config = state["config"]

    # Determine active tools (admin tools gated by config)
    imperator_config = config.get("imperator", {})
    active_tools = list(_imperator_tools)
    if imperator_config.get("admin_tools", False):
        active_tools.extend(_admin_tools)

    llm = get_chat_model(config)
    llm_with_tools = llm.bind_tools(active_tools)

    messages = list(state["messages"])

    # First call: prepend system prompt with DB history context
    has_system = any(isinstance(m, SystemMessage) for m in messages)
    if not has_system:
        try:
            system_content = await async_load_prompt("imperator_identity")
        except RuntimeError as exc:
            _log.error("Failed to load imperator_identity prompt: %s", exc)
            return {
                "messages": [AIMessage(content="I encountered a configuration error.")],
                "response_text": "I encountered a configuration error.",
                "error": f"Prompt loading failed: {exc}",
            }

        context_window_id = state.get("context_window_id")
        if context_window_id:
            history_context = await _load_conversation_history(context_window_id, config)
            if history_context:
                system_content += history_context

        messages = [SystemMessage(content=system_content)] + messages

    # CB-R3-06: Truncate older messages if the list exceeds the limit.
    # M9: When truncating, ensure we don't split a tool-call sequence by
    # starting the kept portion on a ToolMessage. Scan backwards from the
    # cut point until we find a non-ToolMessage boundary.
    max_react_messages = get_tuning(config, "imperator_max_react_messages", 40)
    if len(messages) > max_react_messages:
        from langchain_core.messages import ToolMessage
        # Start with the default cut index (keep last max_react_messages-1)
        cut_index = len(messages) - (max_react_messages - 1)
        # Walk backwards until the message at cut_index is not a ToolMessage
        while cut_index < len(messages) and isinstance(messages[cut_index], ToolMessage):
            cut_index += 1
        messages = [messages[0]] + messages[cut_index:]

    try:
        response = await llm_with_tools.ainvoke(messages)
    except (openai.APIError, httpx.HTTPError, ValueError, RuntimeError) as exc:
        _log.error("Imperator LLM call failed: %s", exc, exc_info=True)
        return {
            "messages": [AIMessage(content="I encountered an error processing your request.")],
            "response_text": "I encountered an error processing your request.",
            "error": str(exc),
        }

    # Return the AI response — add_messages reducer will append it
    return {"messages": [response], "iteration_count": state.get("iteration_count", 0) + 1}


def should_continue(state: ImperatorState) -> str:
    """Conditional edge: route to tool_node if tool calls, else store_and_end.

    ARCH-05: Flow control is graph edges, not loops in nodes.
    Enforces imperator_max_iterations to prevent unbounded ReAct loops.
    """
    if state.get("error"):
        return "store_and_end"

    messages = state["messages"]
    if not messages:
        return "store_and_end"

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        # Check iteration limit to prevent unbounded loops
        max_iterations = get_tuning(state.get("config", {}), "imperator_max_iterations", 5)
        if state.get("iteration_count", 0) >= max_iterations:
            _log.warning(
                "Imperator hit max iterations (%d) — forcing end",
                max_iterations,
            )
            return "store_and_end"
        return "tool_node"

    return "store_and_end"


async def store_and_end(state: ImperatorState) -> dict:
    """Store user message and assistant response via the standard message pipeline.

    F-22: Uses conv_store_message (the standard pipeline), NOT direct SQL.
    ARCH-06: The graph runs fresh each invocation — results are persisted
    to the DB here so the next invocation can load them as history.
    """
    context_window_id = state.get("context_window_id")
    if not context_window_id:
        # No context window — extract response text but skip persistence
        messages = state["messages"]
        last_ai = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)),
            None,
        )
        return {"response_text": last_ai.content if last_ai else ""}

    messages = state["messages"]

    # Find the last user message and last AI message (the final answer)
    user_content = None
    for msg in messages:
        if isinstance(msg, HumanMessage):
            user_content = msg.content

    last_ai = None
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            last_ai = msg
            break

    response_text = last_ai.content if last_ai else ""

    pipeline = _get_message_pipeline()

    # Store user message
    if user_content:
        try:
            await pipeline.ainvoke({
                "context_window_id": context_window_id,
                "role": "user",
                "sender": "imperator_user",
                "recipient": "imperator",
                "content": user_content,
                "model_name": None,
                "tool_calls": None,
                "tool_call_id": None,
                "message_id": None,
                "conversation_id": None,
                "sequence_number": None,
                "was_collapsed": False,
                "queued_jobs": [],
                "error": None,
            })
        except (RuntimeError, OSError) as exc:
            _log.warning("Failed to store Imperator user message via pipeline: %s", exc)

    # Store assistant response
    if response_text:
        try:
            await pipeline.ainvoke({
                "context_window_id": context_window_id,
                "role": "assistant",
                "sender": "imperator",
                "recipient": "imperator_user",
                "content": response_text,
                "model_name": None,
                "tool_calls": None,
                "tool_call_id": None,
                "message_id": None,
                "conversation_id": None,
                "sequence_number": None,
                "was_collapsed": False,
                "queued_jobs": [],
                "error": None,
            })
        except (RuntimeError, OSError) as exc:
            _log.warning("Failed to store Imperator assistant message via pipeline: %s", exc)

    return {"response_text": response_text}


# ── Build the graph ──────────────────────────────────────────────────────

def build_imperator_flow(config: dict | None = None) -> StateGraph:
    """Build and compile the Imperator StateGraph.

    ARCH-05: Proper graph structure with agent_node <-> tool_node loop
             via conditional edges.  No while loops inside nodes.
    ARCH-06: No checkpointer.  The graph runs fresh each invocation.
             History is loaded from PostgreSQL in agent_node.
    F-22:    Results stored via conv_store_message in store_and_end.
    """
    # R6-M14: Build the ToolNode with only the tools that match the config.
    # If admin_tools=false (or no config), the ToolNode only gets base tools,
    # so even if the LLM hallucinated an admin tool call, ToolNode would reject it.
    if config is None:
        from app.config import load_config
        config = load_config()
    imperator_config = config.get("imperator", {})
    active_tools = list(_imperator_tools)
    if imperator_config.get("admin_tools", False):
        active_tools.extend(_admin_tools)
    tool_node_instance = ToolNode(active_tools)

    workflow = StateGraph(ImperatorState)

    workflow.add_node("agent_node", agent_node)
    workflow.add_node("tool_node", tool_node_instance)
    workflow.add_node("store_and_end", store_and_end)

    workflow.set_entry_point("agent_node")

    # ARCH-05: Conditional edge — tool_calls route to tool_node, else store
    workflow.add_conditional_edges(
        "agent_node",
        should_continue,
        {
            "tool_node": "tool_node",
            "store_and_end": "store_and_end",
        },
    )

    # tool_node -> back to agent_node for the next reasoning step
    workflow.add_edge("tool_node", "agent_node")

    workflow.add_edge("store_and_end", END)

    # ARCH-06: No checkpointer — compile without one
    return workflow.compile()
```

---

`app/flows/memory_admin_flow.py`

```python
"""
Memory Admin Flows — LangGraph StateGraph flows for mem_add, mem_list, mem_delete.

M-18: These flows wrap Mem0 calls in StateGraphs to comply with the
LangGraph mandate. Previously these operations were called directly
via run_in_executor in tool_dispatch.
"""

import asyncio
import logging
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

_log = logging.getLogger("context_broker.flows.memory_admin")


# ============================================================
# mem_add flow
# ============================================================


class MemAddState(TypedDict):
    """State for the mem_add flow."""

    content: str
    user_id: str
    config: dict

    result: Optional[dict]
    degraded: bool  # R6-M7: Needed so dispatch can distinguish degraded vs hard error
    error: Optional[str]


async def add_memory(state: MemAddState) -> dict:
    """Add a memory to the Mem0 knowledge graph."""
    config = state["config"]

    try:
        from app.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            return {"error": "Mem0 client not available", "degraded": True}

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: mem0.add(state["content"], user_id=state["user_id"]),
        )

        return {"result": result, "degraded": False}

    except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:
        # G5-18: Broad exception handling for Mem0/Neo4j failures.
        _log.warning("mem_add failed: %s", exc)
        return {"error": str(exc), "degraded": True}


def build_mem_add_flow() -> StateGraph:
    """Build and compile the mem_add StateGraph."""
    workflow = StateGraph(MemAddState)
    workflow.add_node("add_memory", add_memory)
    workflow.set_entry_point("add_memory")
    workflow.add_edge("add_memory", END)
    return workflow.compile()


# ============================================================
# mem_list flow
# ============================================================


class MemListState(TypedDict):
    """State for the mem_list flow."""

    user_id: str
    limit: int
    config: dict

    memories: list[dict]
    degraded: bool  # R6-M7: Needed so dispatch can distinguish degraded vs hard error
    error: Optional[str]


async def list_memories(state: MemListState) -> dict:
    """List all memories for a user from the Mem0 knowledge graph."""
    config = state["config"]

    try:
        from app.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            return {"error": "Mem0 client not available", "degraded": True}

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: mem0.get_all(user_id=state["user_id"], limit=state["limit"]),
        )

        if isinstance(result, list):
            memories = result
        elif isinstance(result, dict):
            memories = result.get("results", [])
        else:
            memories = []

        return {"memories": memories, "degraded": False}

    except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:
        # G5-18: Broad exception handling for Mem0/Neo4j failures.
        _log.warning("mem_list failed: %s", exc)
        return {"memories": [], "error": str(exc), "degraded": True}


def build_mem_list_flow() -> StateGraph:
    """Build and compile the mem_list StateGraph."""
    workflow = StateGraph(MemListState)
    workflow.add_node("list_memories", list_memories)
    workflow.set_entry_point("list_memories")
    workflow.add_edge("list_memories", END)
    return workflow.compile()


# ============================================================
# mem_delete flow
# ============================================================


class MemDeleteState(TypedDict):
    """State for the mem_delete flow."""

    memory_id: str
    config: dict

    deleted: bool
    degraded: bool  # R6-M7: Needed so dispatch can distinguish degraded vs hard error
    error: Optional[str]


async def delete_memory(state: MemDeleteState) -> dict:
    """Delete a memory from the Mem0 knowledge graph."""
    config = state["config"]

    try:
        from app.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            return {"error": "Mem0 client not available", "degraded": True}

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: mem0.delete(state["memory_id"]),
        )

        return {"deleted": True, "degraded": False}

    except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:
        # G5-18: Broad exception handling for Mem0/Neo4j failures.
        _log.warning("mem_delete failed: %s", exc)
        return {"deleted": False, "error": str(exc), "degraded": True}


def build_mem_delete_flow() -> StateGraph:
    """Build and compile the mem_delete StateGraph."""
    workflow = StateGraph(MemDeleteState)
    workflow.add_node("delete_memory", delete_memory)
    workflow.set_entry_point("delete_memory")
    workflow.add_edge("delete_memory", END)
    return workflow.compile()
```

---

`app/flows/memory_extraction.py`

```python
"""
Memory Extraction — LangGraph StateGraph flow (background).

Extracts knowledge from conversation messages into the Neo4j knowledge graph
via Mem0's native APIs.

  acquire_lock -> fetch_unextracted -> build_extraction_text
  -> run_mem0_extraction -> mark_extracted -> release_lock

Triggered by ARQ worker consuming from memory_extraction_jobs queue.
"""

import asyncio
import logging
import re
import uuid
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_tuning, verbose_log
from app.database import get_pg_pool, get_redis

_log = logging.getLogger("context_broker.flows.memory_extraction")

# Secret redaction patterns — applied before Mem0 ingestion
_SECRET_PATTERNS = [
    (re.compile(r'(?i)(api[_-]?key|token|secret|password|credential)["\s:=]+["\']?[\w\-\.]{20,}'), '[REDACTED]'),
    (re.compile(r'(?i)bearer\s+[\w\-\.]{20,}'), 'Bearer [REDACTED]'),
    (re.compile(r'sk-[a-zA-Z0-9\-]{20,}'), '[REDACTED]'),
    (re.compile(r'(?i)(aws|gcp|azure)[_-]?[\w]*[_-]?(key|secret|token)["\s:=]+["\']?[\w\-\.]{16,}'), '[REDACTED]'),
]


def _redact_secrets(text: str) -> str:
    """Strip potential secrets from text before Mem0 ingestion.

    Note: This is a heuristic regex-based approach covering common patterns
    (API keys, bearer tokens, sk- keys, cloud provider secrets). It does not
    catch all secret forms (PEM blocks, JWTs, connection strings, etc.).
    For production use with sensitive data, consider integrating detect-secrets
    or a dedicated secret scanning library.
    """
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class MemoryExtractionState(TypedDict):
    """State for the memory extraction pipeline."""

    # Inputs
    conversation_id: str
    config: dict

    # Intermediate
    messages: list[dict]
    user_id: str
    extraction_text: str
    selected_message_ids: list[str]
    fully_extracted_ids: list[str]
    lock_key: str
    lock_token: Optional[str]
    lock_acquired: bool

    # Output
    extracted_count: int
    error: Optional[str]


async def acquire_extraction_lock(state: MemoryExtractionState) -> dict:
    """Acquire a Redis lock to prevent concurrent extraction of the same conversation."""
    verbose_log(state["config"], _log, "memory_extraction.acquire_lock ENTER conv=%s", state["conversation_id"])
    lock_key = f"extraction_in_progress:{state['conversation_id']}"
    lock_token = str(uuid.uuid4())
    redis = get_redis()

    acquired = await redis.set(lock_key, lock_token, ex=get_tuning(state["config"], "extraction_lock_ttl_seconds", 180), nx=True)
    if not acquired:
        _log.info(
            "Memory extraction: lock not acquired for conv=%s — skipping",
            state["conversation_id"],
        )
        return {"lock_key": lock_key, "lock_token": None, "lock_acquired": False}

    return {"lock_key": lock_key, "lock_token": lock_token, "lock_acquired": True}


async def fetch_unextracted_messages(state: MemoryExtractionState) -> dict:
    """Fetch messages that have not yet been memory-extracted."""
    pool = get_pg_pool()

    rows = await pool.fetch(
        """
        SELECT id, role, sender, content, sequence_number
        FROM conversation_messages
        WHERE conversation_id = $1
          AND (memory_extracted IS NOT TRUE)
          AND role IN ('user', 'assistant')
        ORDER BY sequence_number ASC
        """,
        uuid.UUID(state["conversation_id"]),
    )

    if not rows:
        return {"messages": [], "extracted_count": 0}

    # Get user_id from conversation (use participant_id from first context window)
    pool2 = get_pg_pool()
    window = await pool2.fetchrow(
        "SELECT participant_id FROM context_windows WHERE conversation_id = $1 ORDER BY created_at ASC LIMIT 1",
        uuid.UUID(state["conversation_id"]),
    )
    user_id = window["participant_id"] if window else "default"

    return {"messages": [dict(r) for r in rows], "user_id": user_id}


async def build_extraction_text(state: MemoryExtractionState) -> dict:
    """Build the text to send to Mem0 for extraction.

    Limits text size to avoid overwhelming the LLM.
    Selects messages newest-first up to the character budget.
    """
    messages = state["messages"]
    max_chars = get_tuning(state["config"], "extraction_max_chars", 90000)

    lines = []
    for msg in reversed(messages):
        # ARCH-01: Skip messages with NULL content (tool-call messages)
        msg_content = msg.get("content") or ""
        if not msg_content:
            continue
        lines.append(
            (str(msg["id"]), f"{msg['role']} ({msg['sender']}): {msg_content}")
        )

    selected_ids = []
    fully_extracted_ids = []
    selected_lines = []
    total_chars = 0

    for msg_id, line in lines:
        if total_chars + len(line) + 1 > max_chars:
            if not selected_ids:
                # Always include at least one message, but it's truncated
                selected_ids.append(msg_id)
                selected_lines.append(line[:max_chars])
                # Not fully extracted — truncated, so do NOT add to fully_extracted_ids
            break
        selected_ids.append(msg_id)
        fully_extracted_ids.append(msg_id)
        selected_lines.append(line)
        total_chars += len(line) + 1

    # Restore chronological order
    selected_ids.reverse()
    fully_extracted_ids.reverse()
    selected_lines.reverse()

    extraction_text = "\n".join(selected_lines)
    extraction_text = _redact_secrets(extraction_text)

    return {
        "extraction_text": extraction_text,
        "selected_message_ids": selected_ids,
        "fully_extracted_ids": fully_extracted_ids,
    }


async def run_mem0_extraction(state: MemoryExtractionState) -> dict:
    """Call Mem0 to extract knowledge from the conversation text.

    Mem0 operations are synchronous — run in thread pool executor.
    """
    config = state["config"]

    try:
        from app.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            _log.warning(
                "Memory extraction: Mem0 not available for conv=%s",
                state["conversation_id"],
            )
            return {"error": "Mem0 client not available"}

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: mem0.add(
                state["extraction_text"],
                user_id=state["user_id"],
                run_id=state["conversation_id"],
            ),
        )

        _log.info(
            "Memory extraction: extracted from %d messages for conv=%s",
            len(state["selected_message_ids"]),
            state["conversation_id"],
        )
        return {"error": None}

    except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:
        # G5-18: Broad exception handling for Mem0/Neo4j failures.
        # Mem0 wraps Neo4j driver errors and other backend failures in
        # various exception types. We catch broadly here to ensure
        # graceful degradation rather than crashing the worker.
        _log.error(
            "Memory extraction failed for conv=%s: %s",
            state["conversation_id"],
            exc,
        )
        return {"error": str(exc)}


async def mark_messages_extracted(state: MemoryExtractionState) -> dict:
    """Mark fully extracted messages as memory_extracted=TRUE in PostgreSQL.

    Only marks messages whose content was fully included in the extraction text.
    Truncated (partially extracted) messages are excluded so they will be
    picked up again on the next extraction run.
    """
    if state.get("error"):
        return {}

    fully_extracted = state.get("fully_extracted_ids", [])
    if not fully_extracted:
        return {"extracted_count": 0}

    pool = get_pg_pool()
    message_uuids = [uuid.UUID(mid) for mid in fully_extracted]

    await pool.execute(
        """
        UPDATE conversation_messages
        SET memory_extracted = TRUE
        WHERE id = ANY($1::uuid[])
        """,
        message_uuids,
    )

    return {"extracted_count": len(message_uuids)}


async def _atomic_lock_release(redis_client, lock_key: str, lock_token: str) -> bool:
    """Atomically release a Redis lock only if we still own it (CB-R3-02).

    Uses a Lua script to perform check-and-delete in a single atomic operation,
    preventing the race where another worker acquires the lock between our GET
    and DELETE.

    Returns True if the lock was released, False if it was already gone or
    owned by another worker.
    """
    lua_script = """
    if redis.call("GET", KEYS[1]) == ARGV[1] then
        return redis.call("DEL", KEYS[1])
    else
        return 0
    end
    """
    result = await redis_client.eval(lua_script, 1, lock_key, lock_token)
    return result == 1


async def release_extraction_lock(state: MemoryExtractionState) -> dict:
    """Release the Redis extraction lock.

    CB-R3-02: Uses atomic Lua script to prevent race between GET and DELETE.
    """
    lock_key = state.get("lock_key", "")
    lock_token = state.get("lock_token")
    if lock_key and state.get("lock_acquired") and lock_token:
        redis = get_redis()
        released = await _atomic_lock_release(redis, lock_key, lock_token)
        if not released:
            _log.debug(
                "Lock %s was not released (expired or taken by another worker)",
                lock_key,
            )
    return {}


def route_after_lock(state: MemoryExtractionState) -> str:
    """Route: if lock not acquired, end. Otherwise fetch messages."""
    if not state.get("lock_acquired"):
        return END
    return "fetch_unextracted_messages"


def route_after_fetch(state: MemoryExtractionState) -> str:
    """Route: if no messages, release lock and end. Otherwise build text."""
    if not state.get("messages"):
        return "release_extraction_lock"
    return "build_extraction_text"


def route_after_build_text(state: MemoryExtractionState) -> str:
    """Route: if error, release lock. Otherwise run extraction."""
    if state.get("error"):
        return "release_extraction_lock"
    return "run_mem0_extraction"


def route_after_extraction(state: MemoryExtractionState) -> str:
    """Route: if error, release lock. Otherwise mark extracted."""
    if state.get("error"):
        return "release_extraction_lock"
    return "mark_messages_extracted"


def build_memory_extraction() -> StateGraph:
    """Build and compile the memory extraction StateGraph."""
    workflow = StateGraph(MemoryExtractionState)

    workflow.add_node("acquire_extraction_lock", acquire_extraction_lock)
    workflow.add_node("fetch_unextracted_messages", fetch_unextracted_messages)
    workflow.add_node("build_extraction_text", build_extraction_text)
    workflow.add_node("run_mem0_extraction", run_mem0_extraction)
    workflow.add_node("mark_messages_extracted", mark_messages_extracted)
    workflow.add_node("release_extraction_lock", release_extraction_lock)

    workflow.set_entry_point("acquire_extraction_lock")

    workflow.add_conditional_edges(
        "acquire_extraction_lock",
        route_after_lock,
        {"fetch_unextracted_messages": "fetch_unextracted_messages", END: END},
    )

    workflow.add_conditional_edges(
        "fetch_unextracted_messages",
        route_after_fetch,
        {
            "build_extraction_text": "build_extraction_text",
            "release_extraction_lock": "release_extraction_lock",
        },
    )

    workflow.add_conditional_edges(
        "build_extraction_text",
        route_after_build_text,
        {
            "run_mem0_extraction": "run_mem0_extraction",
            "release_extraction_lock": "release_extraction_lock",
        },
    )

    workflow.add_conditional_edges(
        "run_mem0_extraction",
        route_after_extraction,
        {
            "mark_messages_extracted": "mark_messages_extracted",
            "release_extraction_lock": "release_extraction_lock",
        },
    )

    workflow.add_edge("mark_messages_extracted", "release_extraction_lock")
    workflow.add_edge("release_extraction_lock", END)

    return workflow.compile()
```

---

`app/flows/memory_scoring.py`

```python
"""
Memory confidence scoring with half-life decay (M-22).

Applied at retrieval time to weight memories by freshness and category.
Memories that haven't been accessed or reinforced decay over time.

Since Mem0 manages its own storage (Neo4j + pgvector), we cannot modify
its internal scoring. This module applies decay at the retrieval layer,
after Mem0 returns results but before they are used downstream.
"""

import math
from datetime import datetime, timezone

# Half-life in days by category
DEFAULT_HALF_LIVES = {
    "ephemeral": 3,      # Short-lived facts (moods, preferences, temp state)
    "contextual": 14,    # Session/project context
    "factual": 60,       # Learned facts about entities
    "historical": 365,   # Historical events, permanent-ish
    "default": 30,       # When category unknown
}


def score_memory(memory: dict, config: dict) -> float:
    """Score a memory based on age and category using half-life decay.

    Returns a score between 0.0 and 1.0.
    """
    half_lives = config.get("tuning", {}).get("memory_half_lives", DEFAULT_HALF_LIVES)

    category = memory.get("category", "default")
    # R5-M20: Clamp to minimum 1 to prevent ZeroDivisionError from bad config
    half_life_days = max(1, half_lives.get(category, half_lives.get("default", 30)))

    # Calculate age in days
    created = memory.get("created_at")
    if isinstance(created, str):
        created = datetime.fromisoformat(created)
    if created is None:
        return 0.5  # Unknown age, neutral score

    now = datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = max(0, (now - created).total_seconds() / 86400)

    # Exponential decay: score = 0.5 ^ (age / half_life)
    score = math.pow(0.5, age_days / half_life_days)

    # Boost if recently accessed
    last_accessed = memory.get("last_accessed")
    if last_accessed:
        if isinstance(last_accessed, str):
            last_accessed = datetime.fromisoformat(last_accessed)
        if last_accessed.tzinfo is None:
            last_accessed = last_accessed.replace(tzinfo=timezone.utc)
        access_age = max(0, (now - last_accessed).total_seconds() / 86400)
        if access_age < 7:
            score = min(1.0, score * 1.3)  # 30% boost for recently accessed

    return score


def filter_and_rank_memories(memories: list[dict], config: dict, min_score: float = 0.1) -> list[dict]:
    """Score, filter, and rank memories by confidence.

    Memories below min_score are filtered out.
    Remaining memories are sorted by score descending.
    """
    scored = []
    for mem in memories:
        score = score_memory(mem, config)
        if score >= min_score:
            # R5-m13: Create a copy to avoid mutating the input list's dicts
            scored_mem = {**mem, "confidence_score": score}
            scored.append(scored_mem)

    scored.sort(key=lambda m: m.get("confidence_score", 0), reverse=True)
    return scored
```

---

`app/flows/memory_search_flow.py`

```python
"""
Memory Search Flows — LangGraph StateGraph flows for mem_search and mem_get_context.

Queries the Mem0 knowledge graph for extracted facts and relationships.
"""

import asyncio
import logging
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.flows.memory_scoring import filter_and_rank_memories

_log = logging.getLogger("context_broker.flows.memory_search")


class MemorySearchState(TypedDict):
    """State for memory search flow."""

    query: str
    user_id: str
    limit: int
    config: dict

    memories: list[dict]
    relations: list[dict]
    degraded: bool
    error: Optional[str]


async def search_memory_graph(state: MemorySearchState) -> dict:
    """Search the Mem0 knowledge graph for relevant memories.

    Gracefully degrades if Neo4j or Mem0 is unavailable.
    """
    config = state["config"]

    try:
        from app.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            return {
                "memories": [],
                "relations": [],
                "degraded": True,
                "error": "Mem0 client not available",
            }

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: mem0.search(
                state["query"],
                user_id=state["user_id"],
                limit=state["limit"],
            ),
        )

        if isinstance(results, dict):
            memories = results.get("results", [])
            relations = results.get("relations", [])
        else:
            memories = results or []
            relations = []

        # M-22: Apply half-life decay scoring and filter stale memories
        memories = filter_and_rank_memories(memories, config)

        return {
            "memories": memories,
            "relations": relations,
            "degraded": False,
        }

    except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:  # EX-CB-001: broad catch for Mem0
        _log.warning("Memory search failed (degraded mode): %s", exc)
        return {
            "memories": [],
            "relations": [],
            "degraded": True,
            "error": str(exc),
        }


def build_memory_search_flow() -> StateGraph:
    """Build and compile the memory search StateGraph."""
    workflow = StateGraph(MemorySearchState)
    workflow.add_node("search_memory_graph", search_memory_graph)
    workflow.set_entry_point("search_memory_graph")
    workflow.add_edge("search_memory_graph", END)
    return workflow.compile()


# ============================================================
# Memory Context Flow
# ============================================================


class MemoryContextState(TypedDict):
    """State for memory context retrieval flow."""

    query: str
    user_id: str
    limit: int
    config: dict

    memories: list[dict]
    context_text: str
    degraded: bool  # R6-M7: Needed so dispatch can distinguish degraded vs hard error
    error: Optional[str]


async def retrieve_memory_context(state: MemoryContextState) -> dict:
    """Retrieve memories and format them for prompt injection."""
    config = state["config"]

    try:
        from app.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            return {"memories": [], "context_text": "", "degraded": True, "error": "Mem0 client not available"}

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: mem0.search(
                state["query"],
                user_id=state["user_id"],
                limit=state["limit"],
            ),
        )

        if isinstance(results, dict):
            memories = results.get("results", [])
        else:
            memories = results or []

        # M-22: Apply half-life decay scoring and filter stale memories
        memories = filter_and_rank_memories(memories, config)

        if memories:
            lines = ["Relevant knowledge about this context:"]
            for mem in memories:
                fact = mem.get("memory") or mem.get("content") or str(mem)
                lines.append(f"- {fact}")
            context_text = "\n".join(lines)
        else:
            context_text = ""

        return {"memories": memories, "context_text": context_text, "degraded": False}

    except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:  # EX-CB-001: broad catch for Mem0
        _log.warning("Memory context retrieval failed (degraded mode): %s", exc)
        return {"memories": [], "context_text": "", "degraded": True, "error": str(exc)}


def build_memory_context_flow() -> StateGraph:
    """Build and compile the memory context StateGraph."""
    workflow = StateGraph(MemoryContextState)
    workflow.add_node("retrieve_memory_context", retrieve_memory_context)
    workflow.set_entry_point("retrieve_memory_context")
    workflow.add_edge("retrieve_memory_context", END)
    return workflow.compile()
```

---

`app/flows/message_pipeline.py`

```python
"""
Message Pipeline — LangGraph StateGraph flow.

Handles conv_store_message:
  resolve_conversation -> store_message -> enqueue_jobs

Embedding, context assembly, and memory extraction happen asynchronously
via ARQ background workers after this flow completes.
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

import asyncpg
import redis.exceptions

from app.config import verbose_log_auto
from app.database import get_pg_pool, get_redis
from app.metrics_registry import JOBS_ENQUEUED

_log = logging.getLogger("context_broker.flows.message_pipeline")

# ARCH-14: Priority derived from role
_ROLE_PRIORITY = {
    "user": 1,
    "assistant": 2,
    "system": 3,
    "tool": 4,
}


class MessagePipelineState(TypedDict):
    """State for the message ingestion pipeline."""

    # Inputs
    context_window_id: Optional[str]  # ARCH-04: replaces conversation_id
    conversation_id_input: Optional[str]  # Direct conversation_id bypass
    role: str
    sender: str                     # ARCH-13: was sender_id
    recipient: Optional[str]        # ARCH-13: was recipient_id
    content: Optional[str]          # ARCH-01: now nullable
    model_name: Optional[str]
    tool_calls: Optional[list[dict]] # ARCH-01: tool calls as list of dicts (JSONB)
    tool_call_id: Optional[str]     # ARCH-01: tool call ID for tool responses

    # Outputs set by nodes
    message_id: Optional[str]
    conversation_id: Optional[str]  # ARCH-04: looked up from context_windows
    sequence_number: Optional[int]
    # R6-m8: was_duplicate removed — always False after dedup was replaced by collapse logic
    was_collapsed: bool
    queued_jobs: list[str]
    error: Optional[str]


async def store_message(state: MessagePipelineState) -> dict:
    """Insert the message into conversation_messages and update conversation counters.

    Deduplication is handled by the repeat_count collapse logic for
    consecutive identical messages from the same sender.
    """
    _t0 = time.monotonic()
    verbose_log_auto(_log, "store_message ENTER context_window=%s conversation_id_input=%s sender=%s", state.get("context_window_id"), state.get("conversation_id_input"), state["sender"])
    pool = get_pg_pool()

    # Resolve conversation_id from whichever identifier was provided
    cw_id = state.get("context_window_id")
    conv_id_input = state.get("conversation_id_input")

    if cw_id:
        # ARCH-04: Look up conversation_id from context_windows table
        cw_row = await pool.fetchrow(
            "SELECT conversation_id FROM context_windows WHERE id = $1",
            uuid.UUID(cw_id),
        )
        if cw_row is None:
            return {"error": f"Context window {cw_id} not found"}
        conversation_id = str(cw_row["conversation_id"])
    elif conv_id_input:
        # Direct conversation_id — skip context window lookup
        conversation_id = conv_id_input
    else:
        return {"error": "At least one of context_window_id or conversation_id must be provided"}

    conv_uuid = uuid.UUID(conversation_id)

    # Verify conversation exists
    conversation = await pool.fetchrow(
        "SELECT id FROM conversations WHERE id = $1",
        conv_uuid,
    )
    if conversation is None:
        return {"error": f"Conversation {conversation_id} not found"}

    # ARCH-10: Compute token_count internally from content length
    content = state.get("content")
    effective_token_count = max(1, len(content) // 4) if content else 0

    # ARCH-14: Derive priority from role
    priority = _ROLE_PRIORITY.get(state["role"], 2)

    # ARCH-12: Default recipient from role if not provided
    recipient = state.get("recipient")
    if not recipient:
        if state["role"] == "assistant":
            recipient = "user"
        elif state["role"] == "user":
            recipient = "assistant"

    # Retry once on UniqueViolationError (edge case with advisory lock)
    row = None  # CB-R3-04: Initialize before retry loop for safety
    for _attempt in range(2):
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # B-02: Advisory lock per conversation to serialize inserts
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1::text))",
                        conversation_id,
                    )

                    # F-04: Check for consecutive duplicate message (same sender + content)
                    prev_msg = await conn.fetchrow(
                        """
                        SELECT id, sender, content, repeat_count
                        FROM conversation_messages
                        WHERE conversation_id = $1
                        ORDER BY sequence_number DESC
                        LIMIT 1
                        """,
                        conv_uuid,
                    )
                    if (
                        prev_msg is not None
                        and prev_msg["sender"] == state["sender"]
                        and prev_msg["content"] == content
                        and content is not None
                    ):
                        # Collapse: increment repeat_count instead of inserting.
                        # R6-M17: Collapse increments repeat_count but NOT total_messages
                        # because no new message was added. estimated_token_count is also
                        # unchanged because the collapsed message has the same content.
                        new_count = (prev_msg["repeat_count"] or 1) + 1
                        await conn.execute(
                            "UPDATE conversation_messages SET repeat_count = $1 WHERE id = $2",
                            new_count,
                            prev_msg["id"],
                        )
                        await conn.execute(
                            "UPDATE conversations SET updated_at = NOW() WHERE id = $1",
                            conv_uuid,
                        )
                        _log.info(
                            "Collapsed duplicate message: sender=%s repeat_count=%d",
                            state["sender"],
                            new_count,
                        )
                        return {
                            "message_id": str(prev_msg["id"]),
                            "conversation_id": conversation_id,
                            "sequence_number": None,
                            "was_collapsed": True,
                            "queued_jobs": [],
                        }

                    # Atomic sequence number assignment
                    row = await conn.fetchrow(
                        """
                        INSERT INTO conversation_messages
                            (conversation_id, role, sender, recipient, content,
                             priority, token_count, model_name,
                             tool_calls, tool_call_id, sequence_number)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                                (SELECT COALESCE(MAX(sequence_number), 0) + 1
                                 FROM conversation_messages
                                 WHERE conversation_id = $1))
                        RETURNING id, sequence_number
                        """,
                        conv_uuid,
                        state["role"],
                        state["sender"],
                        recipient,
                        content,
                        priority,
                        effective_token_count,
                        state.get("model_name"),
                        json.dumps(state["tool_calls"]) if state.get("tool_calls") else None,
                        state.get("tool_call_id"),
                    )

                    # Update conversation counters
                    await conn.execute(
                        """
                        UPDATE conversations
                        SET total_messages = total_messages + 1,
                            estimated_token_count = estimated_token_count + $1,
                            updated_at = NOW()
                        WHERE id = $2
                        """,
                        effective_token_count,
                        conv_uuid,
                    )
            # Success — break out of retry loop
            break
        except asyncpg.UniqueViolationError:
            if _attempt == 0:
                _log.warning(
                    "Sequence number conflict for conv=%s, retrying once",
                    conversation_id,
                )
                continue
            _log.error(
                "Sequence number conflict persisted after retry for conv=%s",
                conversation_id,
            )
            return {"error": f"Failed to assign sequence number for conversation {conversation_id}"}

    verbose_log_auto(_log, "store_message EXIT conv=%s msg=%s duration_ms=%d", conversation_id, str(row["id"]), int((time.monotonic() - _t0) * 1000))
    return {
        "message_id": str(row["id"]),
        "conversation_id": conversation_id,
        "sequence_number": row["sequence_number"],
        "was_collapsed": False,
    }


async def enqueue_background_jobs(state: MessagePipelineState) -> dict:
    """Enqueue embedding and memory extraction jobs in Redis for ARQ workers."""
    if state.get("error"):
        return {"queued_jobs": []}

    conversation_id = state.get("conversation_id")
    redis_client = get_redis()
    queued = []
    now = datetime.now(timezone.utc).isoformat()

    # Enqueue embedding job
    embed_job = json.dumps(
        {
            "job_type": "embed_message",
            "message_id": state["message_id"],
            "conversation_id": conversation_id,
            "enqueued_at": now,
        }
    )
    try:
        # No dedup needed: the embed pipeline's UPDATE is idempotent
        # (re-embedding overwrites the same row).
        await redis_client.lpush("embedding_jobs", embed_job)
        queued.append("embed_message")
        JOBS_ENQUEUED.labels(job_type="embed_message").inc()
    except (redis.exceptions.RedisError, ConnectionError) as exc:
        # M-17: The message is already safely stored in Postgres before this
        # point. If Redis is down, embedding/extraction won't happen for this
        # message until re-triggered. Known gap: a periodic sweep checking for
        # messages where embedding IS NULL AND created_at < NOW() - INTERVAL
        # '5 minutes' can detect and re-enqueue these orphaned messages.
        # The queue depth metrics (M-03) can also surface this condition.
        _log.warning("Failed to enqueue embedding job (message safe in Postgres): %s", exc)

    # F-01: Enqueue memory extraction job via sorted set with priority as score
    extract_job = json.dumps(
        {
            "job_type": "extract_memory",
            "conversation_id": conversation_id,
            "enqueued_at": now,
        }
    )
    try:
        dedup_key = f"job_dedup:extract:{conversation_id}"
        is_new = await redis_client.set(dedup_key, "1", ex=300, nx=True)
        if is_new:
            # ARCH-14: Use role-based priority as score (lower = higher priority)
            score = _ROLE_PRIORITY.get(state.get("role", "assistant"), 2)
            await redis_client.zadd("memory_extraction_jobs", {extract_job: score})
            queued.append("extract_memory")
            JOBS_ENQUEUED.labels(job_type="extract_memory").inc()
    except (redis.exceptions.RedisError, ConnectionError) as exc:
        # M-17: Same as above — message is safe in Postgres. Memory extraction
        # can be retried via the periodic dead-letter sweep.
        _log.warning("Failed to enqueue memory extraction job (message safe in Postgres): %s", exc)

    return {"queued_jobs": queued}


def route_after_store(state: MessagePipelineState) -> str:
    """Route: if error or collapsed, skip to END. Otherwise enqueue jobs."""
    if state.get("error") or state.get("was_collapsed"):
        return END
    return "enqueue_background_jobs"


def build_message_pipeline() -> StateGraph:
    """Build and compile the message ingestion StateGraph."""
    workflow = StateGraph(MessagePipelineState)

    workflow.add_node("store_message", store_message)
    workflow.add_node("enqueue_background_jobs", enqueue_background_jobs)

    workflow.set_entry_point("store_message")

    workflow.add_conditional_edges(
        "store_message",
        route_after_store,
        {"enqueue_background_jobs": "enqueue_background_jobs", END: END},
    )

    workflow.add_edge("enqueue_background_jobs", END)

    return workflow.compile()
```

---

`app/flows/metrics_flow.py`

```python
"""
Metrics collection StateGraph flow.

Collects Prometheus metrics inside a StateGraph node,
as required by REQ §4.8.
"""

import logging
from typing import Optional

from langgraph.graph import END, StateGraph
from prometheus_client import generate_latest, REGISTRY
from typing_extensions import TypedDict

_log = logging.getLogger("context_broker.flows.metrics")


class MetricsState(TypedDict):
    """State for the metrics collection flow."""

    action: str
    metrics_output: str
    error: Optional[str]


async def collect_metrics_node(state: MetricsState) -> dict:
    """Collect Prometheus metrics from the registry.

    Produces metrics output inside the StateGraph as required by REQ §4.8.
    """
    try:
        metrics_bytes = generate_latest(REGISTRY)
        metrics_text = metrics_bytes.decode("utf-8", errors="replace")
        return {"metrics_output": metrics_text, "error": None}
    except (ValueError, OSError) as exc:
        _log.error("Failed to collect metrics: %s", exc)
        return {"metrics_output": "", "error": str(exc)}


def build_metrics_flow() -> StateGraph:
    """Build and compile the metrics collection StateGraph."""
    workflow = StateGraph(MetricsState)
    workflow.add_node("collect_metrics", collect_metrics_node)
    workflow.set_entry_point("collect_metrics")
    workflow.add_edge("collect_metrics", END)
    return workflow.compile()
```

---

`app/flows/retrieval_flow.py`

```python
"""
Context Retrieval — backward-compatibility shim (ARCH-18).

The retrieval logic has moved to:
  - app.flows.build_types.standard_tiered (standard-tiered)
  - app.flows.build_types.knowledge_enriched (knowledge-enriched)

This module re-exports the original symbols so existing imports and
tests continue to work.

build_retrieval_flow() returns the knowledge-enriched retrieval graph
for backward compatibility (it was the original monolithic graph that
handled all build types including semantic/KG).
"""

# Re-export from the knowledge-enriched build type (superset of all features)
from app.flows.build_types.knowledge_enriched import (  # noqa: F401
    KnowledgeEnrichedRetrievalState as RetrievalState,
    ke_load_window as load_window,
    ke_wait_for_assembly as wait_for_assembly,
    ke_load_summaries as load_summaries,
    ke_load_recent_messages as load_recent_messages,
    ke_inject_semantic_retrieval as inject_semantic_retrieval,
    ke_inject_knowledge_graph as inject_knowledge_graph,
    ke_assemble_context as assemble_context_text,
    build_knowledge_enriched_retrieval as build_retrieval_flow,
    _estimate_tokens,
)
```

---

`app/flows/search_flow.py`

```python
"""
Search Flows — LangGraph StateGraph flows for conversation and message search.

Implements hybrid search (vector + BM25 + reranking) via Reciprocal Rank Fusion.
Handles conv_search and conv_search_messages tools.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import openai
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_embeddings_model, get_tuning
from app.database import get_pg_pool

# M-05: Cache CrossEncoder model instances to avoid reloading per request
_reranker_cache: dict[str, Any] = {}
_reranker_lock = asyncio.Lock()


async def _get_reranker(model_name: str):
    """Return a cached CrossEncoder instance.

    M-10: The initial model load is synchronous and CPU-bound (downloads +
    initializes weights). Run it in the default executor to avoid blocking
    the async event loop. Subsequent calls return the cached instance.

    G5-24: An asyncio.Lock prevents concurrent cold-start callers from
    loading the same model multiple times.
    """
    async with _reranker_lock:
        if model_name not in _reranker_cache:
            loop = asyncio.get_running_loop()

            def _load():
                from sentence_transformers import CrossEncoder
                return CrossEncoder(model_name)

            _reranker_cache[model_name] = await loop.run_in_executor(None, _load)
    return _reranker_cache[model_name]

_log = logging.getLogger("context_broker.flows.search")


# ============================================================
# Conversation Search Flow
# ============================================================


class ConversationSearchState(TypedDict):
    """State for conversation search."""

    query: Optional[str]
    limit: int
    offset: int
    date_from: Optional[str]
    date_to: Optional[str]
    flow_id: Optional[str]
    user_id: Optional[str]
    sender: Optional[str]
    config: dict

    query_embedding: Optional[list[float]]
    results: list[dict]
    warning: Optional[str]
    error: Optional[str]


async def embed_conversation_query(state: ConversationSearchState) -> dict:
    """Generate embedding for the search query if provided."""
    if not state.get("query"):
        return {"query_embedding": None}

    config = state["config"]

    try:
        embeddings_model = get_embeddings_model(config)
        embedding = await embeddings_model.aembed_query(state["query"])
        return {"query_embedding": embedding}
    except (openai.APIError, httpx.HTTPError, ValueError) as exc:
        _log.warning("Conversation search: embedding failed, falling back to text: %s", exc)
        return {"query_embedding": None, "warning": "embedding unavailable, results are text-only"}


async def search_conversations_db(state: ConversationSearchState) -> dict:
    """Search conversations using vector similarity or structured filters."""
    pool = get_pg_pool()
    query_embedding = state.get("query_embedding")
    limit = state["limit"]
    offset = state["offset"]
    date_from = state.get("date_from")
    date_to = state.get("date_to")
    filter_flow_id = state.get("flow_id")
    filter_user_id = state.get("user_id")
    filter_sender = state.get("sender")

    # M-21: Validate date strings early to avoid unhandled ValueError.
    # R6-m11: All timestamps in the system are stored and compared in UTC.
    # Naive datetime inputs are assumed UTC. This is consistent with PostgreSQL
    # column types (timestamptz) and Redis TTLs.
    parsed_date_from = None
    parsed_date_to = None
    try:
        if date_from:
            parsed_date_from = datetime.fromisoformat(date_from)
            # R5-M19: Assume UTC if no timezone provided
            if parsed_date_from.tzinfo is None:
                parsed_date_from = parsed_date_from.replace(tzinfo=timezone.utc)
        if date_to:
            parsed_date_to = datetime.fromisoformat(date_to)
            if parsed_date_to.tzinfo is None:
                parsed_date_to = parsed_date_to.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        return {"error": f"Invalid date format: {exc}", "results": []}

    def _build_conv_filters(start_idx: int, table_prefix: str = "") -> tuple[str, list, int]:
        """Build dynamic WHERE clause fragments for conversation filters.

        CB-R3-07: Build filter list and args together, compute indices at the end.
        Returns (sql_fragment, args_list, next_idx).
        """
        prefix = f"{table_prefix}." if table_prefix else ""
        filters: list[str] = []
        args: list = []
        if filter_flow_id:
            filters.append(f"{prefix}flow_id = ${{}}")
            args.append(filter_flow_id)
        if filter_user_id:
            filters.append(f"{prefix}user_id = ${{}}")
            args.append(filter_user_id)
        if filter_sender:
            # R6-M18: sender lives on messages table, not conversations, so a
            # correlated EXISTS subquery is required. For performance, the
            # idx_messages_conversation_sender index covers this query.
            filters.append(f"EXISTS (SELECT 1 FROM conversation_messages sm WHERE sm.conversation_id = {prefix}id AND sm.sender = ${{}})")
            args.append(filter_sender)
        if parsed_date_from:
            filters.append(f"{prefix}created_at >= ${{}}::timestamptz")
            args.append(parsed_date_from)
        if parsed_date_to:
            filters.append(f"{prefix}created_at <= ${{}}::timestamptz")
            args.append(parsed_date_to)
        # Enumerate and format parameter indices
        clauses = ""
        for i, f in enumerate(filters):
            clauses += " AND " + f.format(start_idx + i)
        next_idx = start_idx + len(filters)
        return clauses, args, next_idx

    if query_embedding is not None:
        vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
        # Base params: $1=vec, $2=limit, $3=offset; extra filters start at $4+
        extra_where, extra_args, _ = _build_conv_filters(4, table_prefix="c")

        rows = await pool.fetch(
            f"""
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   c.total_messages, c.estimated_token_count,
                   MIN(cm.embedding <=> $1::vector) AS relevance_score
            FROM conversations c
            JOIN conversation_messages cm ON cm.conversation_id = c.id
            WHERE cm.embedding IS NOT NULL{extra_where}
            GROUP BY c.id, c.title, c.created_at, c.updated_at,
                     c.total_messages, c.estimated_token_count
            ORDER BY relevance_score ASC
            LIMIT $2 OFFSET $3
            """,
            vec_str,
            limit,
            offset,
            *extra_args,
        )
    else:
        # Base params: $1=limit, $2=offset; extra filters start at $3+
        extra_where, extra_args, _ = _build_conv_filters(3)

        where_clause = "WHERE 1=1" + extra_where if extra_where else ""

        rows = await pool.fetch(
            f"""
            SELECT id, title, created_at, updated_at, total_messages, estimated_token_count
            FROM conversations
            {where_clause}
            ORDER BY updated_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
            *extra_args,
        )

    results = []
    for row in rows:
        r = dict(row)
        r["id"] = str(r["id"])
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
        if r.get("updated_at"):
            r["updated_at"] = r["updated_at"].isoformat()
        results.append(r)

    return {"results": results}


def build_conversation_search_flow() -> StateGraph:
    """Build and compile the conversation search StateGraph."""
    workflow = StateGraph(ConversationSearchState)

    workflow.add_node("embed_conversation_query", embed_conversation_query)
    workflow.add_node("search_conversations_db", search_conversations_db)

    workflow.set_entry_point("embed_conversation_query")
    workflow.add_edge("embed_conversation_query", "search_conversations_db")
    workflow.add_edge("search_conversations_db", END)

    return workflow.compile()


# ============================================================
# Message Search Flow (Hybrid: vector + BM25 + reranking)
# ============================================================


class MessageSearchState(TypedDict):
    """State for message hybrid search."""

    query: str
    conversation_id: Optional[str]
    sender: Optional[str]
    role: Optional[str]
    date_from: Optional[str]
    date_to: Optional[str]
    limit: int
    config: dict

    query_embedding: Optional[list[float]]
    candidates: list[dict]
    reranked_results: list[dict]
    warning: Optional[str]
    error: Optional[str]


async def embed_message_query(state: MessageSearchState) -> dict:
    """Generate embedding for the message search query."""
    config = state["config"]

    try:
        embeddings_model = get_embeddings_model(config)
        embedding = await embeddings_model.aembed_query(state["query"])
        return {"query_embedding": embedding}
    except (openai.APIError, httpx.HTTPError, ValueError) as exc:
        _log.warning("Message search: embedding failed, falling back to BM25: %s", exc)
        return {"query_embedding": None, "warning": "embedding unavailable, results are text-only"}


async def hybrid_search_messages(state: MessageSearchState) -> dict:
    """Perform hybrid search using vector ANN + BM25 combined via RRF.

    M-20: All structured filters (sender, role, date_from, date_to) are
    pushed into the SQL WHERE clauses of both CTEs so that filtering happens
    before top-K selection, not after. This prevents valid results from being
    excluded by post-query filtering.

    M-21: Date strings are validated early; invalid formats return an error.

    Reciprocal Rank Fusion (RRF) combines rankings from both retrieval methods.
    """
    pool = get_pg_pool()
    query = state["query"]
    query_embedding = state.get("query_embedding")
    conversation_id = state.get("conversation_id")
    limit = state["limit"]

    rrf_k = int(get_tuning(state["config"], "rrf_constant", 60))
    candidate_limit = int(get_tuning(state["config"], "search_candidate_limit", 100))

    # M-21: Validate date strings early
    filter_sender = state.get("sender")
    filter_role = state.get("role")
    filter_date_from = state.get("date_from")
    filter_date_to = state.get("date_to")

    parsed_date_from = None
    parsed_date_to = None
    try:
        if filter_date_from:
            parsed_date_from = datetime.fromisoformat(filter_date_from)
            # R5-M19: Assume UTC if no timezone provided
            if parsed_date_from.tzinfo is None:
                parsed_date_from = parsed_date_from.replace(tzinfo=timezone.utc)
        if filter_date_to:
            parsed_date_to = datetime.fromisoformat(filter_date_to)
            if parsed_date_to.tzinfo is None:
                parsed_date_to = parsed_date_to.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        return {"candidates": [], "error": f"Invalid date format: {exc}"}

    # M-20: Build dynamic WHERE clause fragments with parameterized args.
    # CB-R3-07: Build filter list and args together, compute indices at the end.
    # This eliminates manual index tracking throughout the function.
    def _build_extra_filters(start_idx: int) -> tuple[str, list, int]:
        """Return (sql_fragment, args_list, next_idx) for the structured filters."""
        filters: list[str] = []
        args: list = []
        if conversation_id:
            filters.append("conversation_id = ${}::uuid")
            args.append(uuid.UUID(conversation_id))
        if filter_sender:
            filters.append("sender = ${}")
            args.append(filter_sender)
        if filter_role:
            filters.append("role = ${}")
            args.append(filter_role)
        if parsed_date_from:
            filters.append("created_at >= ${}::timestamptz")
            args.append(parsed_date_from)
        if parsed_date_to:
            filters.append("created_at <= ${}::timestamptz")
            args.append(parsed_date_to)
        # Enumerate and format parameter indices
        clauses = ""
        for i, f in enumerate(filters):
            clauses += " AND " + f.format(start_idx + i)
        next_idx = start_idx + len(filters)
        return clauses, args, next_idx

    if query_embedding is not None:
        vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        # Base params for vector+BM25: $1=vec, $2=query_text
        # Then extra filters, then candidate_limit, rrf_k, result_limit
        extra_where, extra_args, next_idx = _build_extra_filters(3)
        candidate_limit_idx = next_idx
        rrf_k_idx = next_idx + 1
        result_limit_idx = next_idx + 2

        sql = f"""
            WITH vector_ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector) AS rank
                FROM conversation_messages
                WHERE embedding IS NOT NULL{extra_where}
                LIMIT ${candidate_limit_idx}
            ),
            bm25_ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           ORDER BY ts_rank(content_tsv, plainto_tsquery('english', $2)) DESC
                       ) AS rank
                FROM conversation_messages
                WHERE content_tsv @@ plainto_tsquery('english', $2){extra_where}
                LIMIT ${candidate_limit_idx}
            ),
            rrf AS (
                SELECT COALESCE(v.id, b.id) AS id,
                       COALESCE(1.0 / (${rrf_k_idx} + v.rank), 0) +
                       COALESCE(1.0 / (${rrf_k_idx} + b.rank), 0) AS rrf_score
                FROM vector_ranked v
                FULL OUTER JOIN bm25_ranked b ON v.id = b.id
            )
            SELECT m.id, m.conversation_id, m.role, m.sender, m.recipient,
                   m.content, m.sequence_number, m.created_at, m.token_count,
                   r.rrf_score AS score
            FROM conversation_messages m
            JOIN rrf r ON m.id = r.id
            ORDER BY score DESC
            LIMIT ${result_limit_idx}
        """

        rows = await pool.fetch(
            sql,
            vec_str,
            query,
            *extra_args,
            candidate_limit,
            rrf_k,
            limit * 5,  # over-fetch for reranking
        )
    else:
        # BM25 only fallback
        # Base param: $1=query_text
        extra_where, extra_args, next_idx = _build_extra_filters(2)
        result_limit_idx = next_idx

        sql = f"""
            SELECT id, conversation_id, role, sender, recipient,
                   content, sequence_number, created_at, token_count,
                   ts_rank(content_tsv, plainto_tsquery('english', $1)) AS score
            FROM conversation_messages
            WHERE content_tsv @@ plainto_tsquery('english', $1){extra_where}
            ORDER BY score DESC
            LIMIT ${result_limit_idx}
        """

        rows = await pool.fetch(
            sql,
            query,
            *extra_args,
            limit * 5,
        )

    candidates = []
    for row in rows:
        r = dict(row)
        r["id"] = str(r["id"])
        r["conversation_id"] = str(r["conversation_id"])
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
        candidates.append(r)

    # F-10: Apply recency bias to RRF scores
    recency_decay_days = int(get_tuning(state["config"], "recency_decay_days", 90))
    recency_max_penalty = float(get_tuning(state["config"], "recency_max_penalty", 0.2))

    if candidates and recency_decay_days > 0 and recency_max_penalty > 0:
        now = datetime.now(timezone.utc)
        for c in candidates:
            created_str = c.get("created_at", "")
            if created_str:
                try:
                    created = datetime.fromisoformat(created_str)
                    # Clamp to 0 to guard against clock skew inflating scores
                    age_days = max(0, (now - created).total_seconds() / 86400)
                    # Linear penalty: 0 for new messages, up to recency_max_penalty for old
                    penalty = min(recency_max_penalty, (age_days / recency_decay_days) * recency_max_penalty)
                    c["score"] = c.get("score", 0) * (1.0 - penalty)
                except (ValueError, TypeError):
                    pass
        # Re-sort by adjusted score
        candidates.sort(key=lambda x: x.get("score", 0), reverse=True)

    return {"candidates": candidates}


async def rerank_results(state: MessageSearchState) -> dict:
    """Apply cross-encoder reranking to the hybrid search candidates.

    Uses the configured reranker. Falls back to RRF scores if reranker
    is unavailable (graceful degradation).
    """
    candidates = state["candidates"]
    query = state["query"]
    limit = state["limit"]
    config = state["config"]

    reranker_config = config.get("reranker", {})
    reranker_provider = reranker_config.get("provider", "none")

    if reranker_provider == "none" or not candidates:
        return {"reranked_results": candidates[:limit]}

    if reranker_provider == "cross-encoder":
        try:
            model_name = reranker_config.get("model", "BAAI/bge-reranker-v2-m3")
            reranker = await _get_reranker(model_name)

            pairs = [(query, c.get("content") or "") for c in candidates]

            loop = asyncio.get_running_loop()
            scores = await loop.run_in_executor(
                None, lambda: reranker.predict(pairs)
            )

            for i, candidate in enumerate(candidates):
                candidate["rerank_score"] = float(scores[i])

            reranked = sorted(candidates, key=lambda x: x.get("rerank_score", 0), reverse=True)
            return {"reranked_results": reranked[:limit]}

        except (OSError, RuntimeError, ValueError, ImportError) as exc:
            # R6-M19: ImportError catches model-loading failures (missing
            # sentence_transformers or incompatible versions)
            _log.warning(
                "Cross-encoder reranking failed (degraded mode): %s", exc
            )
            return {"reranked_results": candidates[:limit]}

    # Unknown provider — return top candidates by RRF score
    return {"reranked_results": candidates[:limit]}


def route_after_embed_message(state: MessageSearchState) -> str:
    """Route: always proceed to hybrid search (embedding failure is non-fatal)."""
    return "hybrid_search_messages"


def build_message_search_flow() -> StateGraph:
    """Build and compile the message search StateGraph."""
    workflow = StateGraph(MessageSearchState)

    workflow.add_node("embed_message_query", embed_message_query)
    workflow.add_node("hybrid_search_messages", hybrid_search_messages)
    workflow.add_node("rerank_results", rerank_results)

    workflow.set_entry_point("embed_message_query")
    workflow.add_edge("embed_message_query", "hybrid_search_messages")
    workflow.add_edge("hybrid_search_messages", "rerank_results")
    workflow.add_edge("rerank_results", END)

    return workflow.compile()
```

---

`app/flows/tool_dispatch.py`

```python
"""
Tool dispatch — routes MCP tool calls to compiled StateGraph flows.

All tool logic lives in StateGraph flows. This module is the thin
routing layer that maps tool names to their flows.
"""

import logging
from typing import Any

# ARCH-18: Import build_types package to trigger registration of all build types
import app.flows.build_types  # noqa: F401
from app.flows.build_type_registry import get_retrieval_graph
from app.flows.conversation_ops_flow import (
    build_create_conversation_flow,
    build_create_context_window_flow,
    build_get_history_flow,
    build_search_context_windows_flow,
)
from app.flows.imperator_flow import build_imperator_flow
from app.flows.memory_admin_flow import (
    build_mem_add_flow,
    build_mem_delete_flow,
    build_mem_list_flow,
)
from app.flows.memory_search_flow import (
    build_memory_context_flow,
    build_memory_search_flow,
)
from app.flows.message_pipeline import build_message_pipeline
from app.flows.metrics_flow import build_metrics_flow
from app.flows.search_flow import (
    build_conversation_search_flow,
    build_message_search_flow,
)
from app.models import (
    ImperatorChatInput,
    CreateContextWindowInput,
    CreateConversationInput,
    GetHistoryInput,
    MemAddInput,
    MemDeleteInput,
    MemGetContextInput,
    MemListInput,
    MemSearchInput,
    MetricsGetInput,
    RetrieveContextInput,
    SearchContextWindowsInput,
    SearchConversationsInput,
    SearchMessagesInput,
    StoreMessageInput,
)

_log = logging.getLogger("context_broker.flows.tool_dispatch")

# Lazy-initialized flow singletons — compiled on first use to avoid
# import-time side effects and allow graceful startup ordering.
_create_conversation_flow = None
_store_message_flow = None
_create_context_window_flow = None
_search_conversations_flow = None
_search_messages_flow = None
_get_history_flow = None
_search_context_windows_flow = None
_mem_search_flow = None
_mem_context_flow = None
_mem_add_flow = None
_mem_list_flow = None
_mem_delete_flow = None
_metrics_flow = None
_imperator_flow = None


def _get_create_conversation_flow():
    global _create_conversation_flow
    if _create_conversation_flow is None:
        _create_conversation_flow = build_create_conversation_flow()
    return _create_conversation_flow


def _get_store_message_flow():
    global _store_message_flow
    if _store_message_flow is None:
        _store_message_flow = build_message_pipeline()
    return _store_message_flow



def _get_create_context_window_flow():
    global _create_context_window_flow
    if _create_context_window_flow is None:
        _create_context_window_flow = build_create_context_window_flow()
    return _create_context_window_flow


def _get_search_conversations_flow():
    global _search_conversations_flow
    if _search_conversations_flow is None:
        _search_conversations_flow = build_conversation_search_flow()
    return _search_conversations_flow


def _get_search_messages_flow():
    global _search_messages_flow
    if _search_messages_flow is None:
        _search_messages_flow = build_message_search_flow()
    return _search_messages_flow


def _get_get_history_flow():
    global _get_history_flow
    if _get_history_flow is None:
        _get_history_flow = build_get_history_flow()
    return _get_history_flow


def _get_search_context_windows_flow():
    global _search_context_windows_flow
    if _search_context_windows_flow is None:
        _search_context_windows_flow = build_search_context_windows_flow()
    return _search_context_windows_flow


def _get_mem_search_flow():
    global _mem_search_flow
    if _mem_search_flow is None:
        _mem_search_flow = build_memory_search_flow()
    return _mem_search_flow


def _get_mem_context_flow():
    global _mem_context_flow
    if _mem_context_flow is None:
        _mem_context_flow = build_memory_context_flow()
    return _mem_context_flow


def _get_mem_add_flow():
    global _mem_add_flow
    if _mem_add_flow is None:
        _mem_add_flow = build_mem_add_flow()
    return _mem_add_flow


def _get_mem_list_flow():
    global _mem_list_flow
    if _mem_list_flow is None:
        _mem_list_flow = build_mem_list_flow()
    return _mem_list_flow


def _get_mem_delete_flow():
    global _mem_delete_flow
    if _mem_delete_flow is None:
        _mem_delete_flow = build_mem_delete_flow()
    return _mem_delete_flow


def _get_metrics_flow():
    global _metrics_flow
    if _metrics_flow is None:
        _metrics_flow = build_metrics_flow()
    return _metrics_flow


def _get_imperator_flow():
    global _imperator_flow
    if _imperator_flow is None:
        _imperator_flow = build_imperator_flow()
    return _imperator_flow


async def dispatch_tool(
    tool_name: str,
    arguments: dict[str, Any],
    config: dict[str, Any],
    app_state: Any,
) -> dict[str, Any]:
    """Route a tool call to its StateGraph flow.

    Validates inputs using Pydantic models before invoking flows.
    Raises ValueError for unknown tools or validation errors.
    """
    _log.info("Dispatching tool: %s", tool_name)

    if tool_name == "conv_create_conversation":
        validated = CreateConversationInput(**arguments)
        result = await _get_create_conversation_flow().ainvoke(
            {
                "conversation_id": str(validated.conversation_id) if validated.conversation_id else None,
                "title": validated.title,
                "flow_id": validated.flow_id,
                "user_id": validated.user_id,
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {"conversation_id": result["conversation_id"]}

    elif tool_name == "conv_store_message":
        validated = StoreMessageInput(**arguments)
        result = await _get_store_message_flow().ainvoke(
            {
                "context_window_id": str(validated.context_window_id) if validated.context_window_id else None,
                "conversation_id_input": str(validated.conversation_id) if validated.conversation_id else None,
                "role": validated.role,
                "sender": validated.sender,
                "recipient": validated.recipient,
                "content": validated.content,
                "model_name": validated.model_name,
                "tool_calls": validated.tool_calls,
                "tool_call_id": validated.tool_call_id,
                "message_id": None,
                "sequence_number": None,
                "was_collapsed": False,
                "queued_jobs": [],
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {
            "message_id": result.get("message_id"),
            "sequence_number": result.get("sequence_number"),
            "was_collapsed": result.get("was_collapsed", False),
            "queued_jobs": result.get("queued_jobs", []),
        }

    elif tool_name == "conv_retrieve_context":
        validated = RetrieveContextInput(**arguments)
        # ARCH-18: Look up the build type from the context window, then
        # dispatch to the correct retrieval graph from the registry.
        import uuid as _uuid
        from app.database import get_pg_pool as _get_pg_pool

        _pool = _get_pg_pool()
        _window_row = await _pool.fetchrow(
            "SELECT build_type FROM context_windows WHERE id = $1",
            _uuid.UUID(str(validated.context_window_id)),
        )
        if _window_row is None:
            raise ValueError(f"Context window {validated.context_window_id} not found")

        _build_type = _window_row["build_type"]
        retrieval_graph = get_retrieval_graph(_build_type)

        result = await retrieval_graph.ainvoke(
            {
                "context_window_id": str(validated.context_window_id),
                "config": config,
                "window": None,
                "build_type_config": None,
                "conversation_id": None,
                "max_token_budget": 0,
                "tier1_summary": None,
                "tier2_summaries": [],
                "recent_messages": [],
                "semantic_messages": [],
                "knowledge_graph_facts": [],
                "assembly_status": "pending",
                "context_messages": None,
                "context_tiers": None,
                "total_tokens_used": 0,
                "warnings": [],
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        response: dict[str, Any] = {
            "context": result.get("context_messages"),
            "tiers": result.get("context_tiers"),
            "total_tokens": result.get("total_tokens_used", 0),
            "assembly_status": result.get("assembly_status", "ready"),
        }
        # m4: Surface retrieval warnings (e.g., assembly timeout) to the caller
        if result.get("warnings"):
            response["warnings"] = result["warnings"]
        return response

    elif tool_name == "conv_create_context_window":
        validated = CreateContextWindowInput(**arguments)
        result = await _get_create_context_window_flow().ainvoke(
            {
                "conversation_id": str(validated.conversation_id),
                "participant_id": validated.participant_id,
                "build_type": validated.build_type,
                "max_tokens_override": validated.max_tokens,
                "config": config,
                "context_window_id": None,
                "resolved_token_budget": 0,
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {
            "context_window_id": result.get("context_window_id"),
            "resolved_token_budget": result.get("resolved_token_budget"),
        }

    elif tool_name == "conv_search":
        validated = SearchConversationsInput(**arguments)
        result = await _get_search_conversations_flow().ainvoke(
            {
                "query": validated.query,
                "limit": validated.limit,
                "offset": validated.offset,
                "date_from": validated.date_from,
                "date_to": validated.date_to,
                "flow_id": validated.flow_id,
                "user_id": validated.user_id,
                "sender": validated.sender,
                "config": config,
                "query_embedding": None,
                "results": [],
                "warning": None,
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        response: dict[str, Any] = {"conversations": result.get("results", [])}
        if result.get("warning"):
            response["warning"] = result["warning"]
        return response

    elif tool_name == "conv_search_messages":
        validated = SearchMessagesInput(**arguments)
        result = await _get_search_messages_flow().ainvoke(
            {
                "query": validated.query,
                "conversation_id": str(validated.conversation_id) if validated.conversation_id else None,
                "sender": validated.sender,
                "role": validated.role,
                "date_from": validated.date_from,
                "date_to": validated.date_to,
                "limit": validated.limit,
                "config": config,
                "query_embedding": None,
                "candidates": [],
                "reranked_results": [],
                "warning": None,
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        response = {"messages": result.get("reranked_results", [])}
        if result.get("warning"):
            response["warning"] = result["warning"]
        return response

    elif tool_name == "conv_get_history":
        validated = GetHistoryInput(**arguments)
        result = await _get_get_history_flow().ainvoke(
            {
                "conversation_id": str(validated.conversation_id),
                "limit": validated.limit,
                "conversation": None,
                "messages": [],
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {
            "conversation": result.get("conversation"),
            "messages": result.get("messages", []),
        }

    elif tool_name == "conv_search_context_windows":
        validated = SearchContextWindowsInput(**arguments)
        result = await _get_search_context_windows_flow().ainvoke(
            {
                "context_window_id": str(validated.context_window_id) if validated.context_window_id else None,
                "conversation_id": str(validated.conversation_id) if validated.conversation_id else None,
                "participant_id": validated.participant_id,
                "build_type": validated.build_type,
                "limit": validated.limit,
                "results": [],
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {"context_windows": result.get("results", [])}

    elif tool_name == "mem_search":
        validated = MemSearchInput(**arguments)
        result = await _get_mem_search_flow().ainvoke(
            {
                "query": validated.query,
                "user_id": validated.user_id,
                "limit": validated.limit,
                "config": config,
                "memories": [],
                "relations": [],
                "degraded": False,
                "error": None,
            }
        )
        if result.get("error") and not result.get("degraded"):
            raise ValueError(result["error"])
        return {
            "memories": result.get("memories", []),
            "relations": result.get("relations", []),
            "degraded": result.get("degraded", False),
        }

    elif tool_name == "mem_get_context":
        validated = MemGetContextInput(**arguments)
        result = await _get_mem_context_flow().ainvoke(
            {
                "query": validated.query,
                "user_id": validated.user_id,
                "limit": validated.limit,
                "config": config,
                "memories": [],
                "context_text": "",
                "degraded": False,
                "error": None,
            }
        )
        if result.get("error") and not result.get("degraded"):
            raise ValueError(result["error"])
        return {
            "context": result.get("context_text", ""),
            "memories": result.get("memories", []),
        }

    elif tool_name == "imperator_chat":
        validated = ImperatorChatInput(**arguments)
        from langchain_core.messages import HumanMessage

        thread_id = str(validated.context_window_id) if validated.context_window_id else "imperator-default"

        result = await _get_imperator_flow().ainvoke(
            {
                "messages": [HumanMessage(content=validated.message)],
                "context_window_id": str(validated.context_window_id) if validated.context_window_id else None,
                "config": config,
                "response_text": None,
                "error": None,
                "iteration_count": 0,
            },
            config={"configurable": {"thread_id": thread_id}},
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {
            "response": result.get("response_text", ""),
        }

    elif tool_name == "mem_add":
        # M-18: Routed through StateGraph flow instead of direct Mem0 call
        validated = MemAddInput(**arguments)
        result = await _get_mem_add_flow().ainvoke(
            {
                "content": validated.content,
                "user_id": validated.user_id,
                "config": config,
                "result": None,
                "degraded": False,
                "error": None,
            }
        )
        if result.get("error") and not result.get("degraded"):
            raise ValueError(result["error"])
        return {"status": "added", "result": result.get("result")}

    elif tool_name == "mem_list":
        # M-18: Routed through StateGraph flow instead of direct Mem0 call
        validated = MemListInput(**arguments)
        result = await _get_mem_list_flow().ainvoke(
            {
                "user_id": validated.user_id,
                "limit": validated.limit,
                "config": config,
                "memories": [],
                "degraded": False,
                "error": None,
            }
        )
        if result.get("error") and not result.get("degraded"):
            raise ValueError(result["error"])
        return {"memories": result.get("memories", [])}

    elif tool_name == "mem_delete":
        # M-18: Routed through StateGraph flow instead of direct Mem0 call
        validated = MemDeleteInput(**arguments)
        result = await _get_mem_delete_flow().ainvoke(
            {
                "memory_id": validated.memory_id,
                "config": config,
                "deleted": False,
                "degraded": False,
                "error": None,
            }
        )
        if result.get("error") and not result.get("degraded"):
            raise ValueError(result["error"])
        return {"status": "deleted", "memory_id": validated.memory_id}

    elif tool_name == "metrics_get":
        MetricsGetInput(**arguments)
        result = await _get_metrics_flow().ainvoke(
            {
                "action": "collect",
                "metrics_output": "",
                "error": None,
            }
        )
        if result.get("error"):
            raise ValueError(result["error"])
        return {"metrics": result.get("metrics_output", "")}

    else:
        raise ValueError(f"Unknown tool: {tool_name}")
```

---

`app/imperator/state_manager.py`

```python
"""
Imperator persistent state manager.

Manages the Imperator's conversation_id and context_window_id across restarts.
Reads/writes /data/imperator_state.json.

On first boot: creates a new conversation and context window, writes both IDs.
On subsequent boots: reads the IDs and verifies both exist in the DB.
If either is missing: recreates the missing resource.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from app.config import get_build_type_config
from app.database import get_pg_pool
from app.token_budget import resolve_token_budget

_log = logging.getLogger("context_broker.imperator.state_manager")

IMPERATOR_STATE_FILE = Path("/data/imperator_state.json")


class ImperatorStateManager:
    """Manages the Imperator's persistent conversation and context window state."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._conversation_id: Optional[uuid.UUID] = None
        self._context_window_id: Optional[uuid.UUID] = None

    async def initialize(self) -> None:
        """Initialize the Imperator's conversation and context window state.

        Reads the state file if it exists and verifies both the conversation
        and context window. Creates missing resources as needed.
        """
        saved_conv_id, saved_cw_id = self._read_state_file()

        conv_exists = (
            await self._conversation_exists(saved_conv_id)
            if saved_conv_id is not None
            else False
        )
        cw_exists = (
            await self._context_window_exists(saved_cw_id)
            if saved_cw_id is not None
            else False
        )

        if conv_exists and cw_exists:
            self._conversation_id = saved_conv_id
            self._context_window_id = saved_cw_id
            _log.info(
                "Imperator: resuming conversation %s, context window %s",
                self._conversation_id,
                self._context_window_id,
            )
            return

        # Conversation exists but window doesn't — recreate window
        if conv_exists and not cw_exists:
            self._conversation_id = saved_conv_id
            if saved_cw_id is not None:
                _log.warning(
                    "Imperator: context window %s no longer exists, creating new",
                    saved_cw_id,
                )
            self._context_window_id = await self._create_imperator_context_window(
                self._conversation_id
            )
            self._write_state_file(self._conversation_id, self._context_window_id)
            _log.info(
                "Imperator: created new context window %s for existing conversation %s",
                self._context_window_id,
                self._conversation_id,
            )
            return

        # Window exists but conversation doesn't (shouldn't happen due to FK,
        # but handle defensively) — or neither exists. Create both.
        if saved_conv_id is not None and not conv_exists:
            _log.warning(
                "Imperator: conversation %s no longer exists, creating new",
                saved_conv_id,
            )

        self._conversation_id = await self._create_imperator_conversation()
        self._context_window_id = await self._create_imperator_context_window(
            self._conversation_id
        )
        self._write_state_file(self._conversation_id, self._context_window_id)
        _log.info(
            "Imperator: created new conversation %s and context window %s",
            self._conversation_id,
            self._context_window_id,
        )

    async def get_conversation_id(self) -> Optional[uuid.UUID]:
        """Return the Imperator's current conversation ID."""
        return self._conversation_id

    async def get_context_window_id(self) -> Optional[uuid.UUID]:
        """Return the Imperator's current context window ID."""
        return self._context_window_id

    def _read_state_file(
        self,
    ) -> tuple[Optional[uuid.UUID], Optional[uuid.UUID]]:
        """Read the conversation and context window IDs from the state file.

        Returns (conversation_id, context_window_id). Either or both may be
        None if the file doesn't exist or values are missing/invalid.
        """
        if not IMPERATOR_STATE_FILE.exists():
            return None, None

        try:
            with open(IMPERATOR_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)

            conv_id = None
            cw_id = None

            conv_str = data.get("conversation_id")
            if conv_str:
                conv_id = uuid.UUID(conv_str)

            cw_str = data.get("context_window_id")
            if cw_str:
                cw_id = uuid.UUID(cw_str)

            return conv_id, cw_id
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            _log.warning("Failed to read imperator state file: %s", exc)

        return None, None

    def _write_state_file(
        self, conversation_id: uuid.UUID, context_window_id: uuid.UUID
    ) -> None:
        """Write the conversation and context window IDs to the state file."""
        try:
            IMPERATOR_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(IMPERATOR_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "conversation_id": str(conversation_id),
                        "context_window_id": str(context_window_id),
                    },
                    f,
                )
        except OSError as exc:
            _log.error("Failed to write imperator state file: %s", exc)

    async def _conversation_exists(self, conversation_id: uuid.UUID) -> bool:
        """Check if a conversation exists in PostgreSQL.

        Raises on DB errors so that callers (e.g. initialize) fail fast
        rather than silently treating a DB outage as 'conversation missing'
        (REQ-001 §7.4).
        """
        pool = get_pg_pool()
        row = await pool.fetchrow(
            "SELECT id FROM conversations WHERE id = $1", conversation_id
        )
        return row is not None

    async def _context_window_exists(self, context_window_id: uuid.UUID) -> bool:
        """Check if a context window exists in PostgreSQL.

        Raises on DB errors for the same reason as _conversation_exists.
        """
        pool = get_pg_pool()
        row = await pool.fetchrow(
            "SELECT id FROM context_windows WHERE id = $1", context_window_id
        )
        return row is not None

    async def _create_imperator_conversation(self) -> uuid.UUID:
        """Create a new conversation for the Imperator.

        G5-17: This uses direct SQL instead of the conversation flow
        (build_conversation_flow) because the state_manager runs during
        application startup — before flows are compiled and before the
        full application context is available. Importing and compiling
        the conversation flow here would create a circular dependency
        and violate the startup ordering guarantees. Direct SQL is
        acceptable for this single bootstrap operation.
        """
        pool = get_pg_pool()
        new_id = uuid.uuid4()
        await pool.execute(
            "INSERT INTO conversations (id, title) VALUES ($1, $2)",
            new_id,
            "Imperator — System Conversation",
        )
        return new_id

    async def _create_imperator_context_window(
        self, conversation_id: uuid.UUID
    ) -> uuid.UUID:
        """Create a new context window for the Imperator.

        Uses the build_type from config["imperator"]["build_type"] and resolves
        the token budget using the standard resolve_token_budget function.

        G5-17: Direct SQL for the same startup-ordering reasons as
        _create_imperator_conversation.
        """
        imperator_config = self._config.get("imperator", {})
        build_type_name = imperator_config.get("build_type", "standard-tiered")
        participant_id = imperator_config.get("participant_id", "imperator")

        build_type_config = get_build_type_config(self._config, build_type_name)

        # Resolve token budget using the imperator's max_context_tokens if set,
        # falling back to the build type's own setting.
        imperator_max_tokens = imperator_config.get("max_context_tokens")
        caller_override = (
            imperator_max_tokens
            if isinstance(imperator_max_tokens, int)
            else None
        )
        token_budget = await resolve_token_budget(
            config=self._config,
            build_type_config=build_type_config,
            caller_override=caller_override,
        )

        pool = get_pg_pool()

        # G5-08: Idempotent creation via ON CONFLICT on the unique constraint
        # (conversation_id, participant_id, build_type).
        row = await pool.fetchrow(
            """
            INSERT INTO context_windows
                (conversation_id, participant_id, build_type, max_token_budget)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (conversation_id, participant_id, build_type) DO NOTHING
            RETURNING id
            """,
            conversation_id,
            participant_id,
            build_type_name,
            token_budget,
        )

        if row is None:
            # Already exists — look up the existing window
            existing = await pool.fetchrow(
                """
                SELECT id FROM context_windows
                WHERE conversation_id = $1 AND participant_id = $2 AND build_type = $3
                """,
                conversation_id,
                participant_id,
                build_type_name,
            )
            return existing["id"]

        return row["id"]
```

---

`app/logging_setup.py`

```python
"""
Structured JSON logging for the Context Broker.

All logs go to stdout in JSON format (one object per line).
Log level is configurable via config.yml.
"""

import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }

        # Include context fields if present
        for field in ("request_id", "tool_name", "conversation_id", "window_id"):
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = value

        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry)


class HealthCheckFilter(logging.Filter):
    """Suppress noisy health check request logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return "/health" not in message and "GET /health" not in message


def setup_logging() -> None:
    """Configure application logging with JSON formatter.

    Sets up the root logger and suppresses noisy third-party loggers.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(HealthCheckFilter())

    # Configure root logger (R5-m3: guard against duplicate handlers on reload)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if not root_logger.handlers:
        root_logger.addHandler(handler)

    # Suppress noisy third-party loggers
    for noisy_logger in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    logging.getLogger("context_broker").setLevel(logging.INFO)


def update_log_level(level: str) -> None:
    """Update the log level after config is loaded.

    Called from the application lifespan after config.yml is read.
    Accepts standard level names: DEBUG, INFO, WARNING, ERROR, CRITICAL.
    """
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        logging.getLogger("context_broker").warning(
            "Invalid log level '%s' in config — keeping INFO", level
        )
        return

    logging.getLogger().setLevel(numeric_level)
    logging.getLogger("context_broker").setLevel(numeric_level)
```

---

`app/main.py`

```python
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
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import load_config, get_tuning
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
                    imperator_manager = getattr(application.state, "imperator_manager", None)
                    if imperator_manager is not None:
                        await imperator_manager.initialize()
                        application.state.imperator_initialized = True
                        _log.info("Imperator initialization succeeded on Postgres retry")
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
                    imperator_manager = getattr(application.state, "imperator_manager", None)
                    if imperator_manager is not None:
                        await imperator_manager.initialize()
                        application.state.imperator_initialized = True
                        _log.info("Imperator initialization succeeded on Postgres retry")
                except (OSError, RuntimeError, asyncpg.PostgresError) as exc:
                    _log.warning("Imperator initialization retry failed (will retry next loop): %s", exc)
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
        except (ConnectionError, OSError, RuntimeError) as exc:
            _log.warning("Redis retry failed: %s", exc)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage application lifecycle: startup and shutdown."""
    _log.info("Context Broker starting up")

    config = load_config()

    # Apply configured log level now that config is available
    configured_level = config.get("log_level", "INFO")
    update_log_level(configured_level)

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
    except (ConnectionError, OSError, RuntimeError) as exc:
        _log.warning(
            "Redis unavailable at startup — worker deferred until Redis connects: %s", exc
        )
        application.state.redis_available = False
        redis_retry_task = asyncio.create_task(
            _redis_retry_loop(application, config)
        )

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
            "will retry when Postgres connects: %s", exc
        )
        application.state.imperator_initialized = False
        # Ensure retry loop is running if not already started
        if pg_retry_task is None:
            pg_retry_task = asyncio.create_task(_postgres_retry_loop(application, config))

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
@app.exception_handler(RuntimeError)
@app.exception_handler(ValueError)
@app.exception_handler(OSError)
@app.exception_handler(ConnectionError)
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
```

---

`app/memory/mem0_client.py`

```python
"""
Mem0 client initialization for the Context Broker.

Provides a lazy singleton Mem0 Memory instance configured with
pgvector (for vector storage) and Neo4j (for knowledge graph).

All credentials come from environment variables loaded via env_file.
"""

import asyncio
import hashlib
import json
import logging
import os
from typing import Optional

_log = logging.getLogger("context_broker.memory.mem0_client")

_mem0_instance = None
_mem0_config_hash: Optional[str] = None
_mem0_lock = asyncio.Lock()


def _compute_config_hash(config: dict) -> str:
    """Compute a hash of config sections relevant to Mem0 initialization.

    Used to detect config changes that require recreating the Mem0 instance.
    m19: Includes graph_store and vector_store config so that changes to
    database connections (e.g., Neo4j host, Postgres credentials) also
    trigger Mem0 client recreation.
    """
    relevant = {
        "llm": config.get("llm", {}),
        "embeddings": config.get("embeddings", {}),
        "graph_store": config.get("graph_store", {}),
        "vector_store": config.get("vector_store", {}),
    }
    config_str = json.dumps(relevant, sort_keys=True, default=str)
    return hashlib.sha256(config_str.encode()).hexdigest()


async def get_mem0_client(config: dict) -> Optional[object]:
    """Return the Mem0 Memory instance (lazy singleton, async-safe).

    M-12: Uses asyncio.Lock instead of threading.Lock to avoid blocking
    the event loop. All callers must await this function.

    Returns None if Mem0 initialization fails (graceful degradation).
    Each call reattempts initialization if no instance exists yet,
    per REQ-001 §7.2 (independent startup).

    Automatically recreates the instance if the LLM or embeddings
    config has changed since the last initialization.
    """
    global _mem0_instance, _mem0_config_hash

    current_hash = _compute_config_hash(config)

    if _mem0_instance is not None and _mem0_config_hash == current_hash:
        return _mem0_instance

    async with _mem0_lock:
        # Double-check after acquiring lock
        if _mem0_instance is not None and _mem0_config_hash == current_hash:
            return _mem0_instance

        if _mem0_instance is not None and _mem0_config_hash != current_hash:
            _log.info("Mem0 config changed — recreating client")

        try:
            # CB-R3-08: _build_mem0_instance is synchronous (imports, network
            # probes to Neo4j/Postgres) and would block the event loop if called
            # directly under the async lock. Run it in a thread pool executor.
            loop = asyncio.get_running_loop()
            _mem0_instance = await loop.run_in_executor(
                None, _build_mem0_instance, config
            )
            _mem0_config_hash = current_hash
            _log.info("Mem0 client initialized successfully")
            return _mem0_instance
        except (ImportError, ConnectionError, ValueError) as exc:
            _log.warning(
                "Mem0 initialization failed (knowledge graph unavailable): %s", exc
            )
            _mem0_instance = None
            _mem0_config_hash = None
            return None


def _build_mem0_instance(config: dict) -> object:
    """Build and configure the Mem0 Memory instance.

    Uses pgvector for vector storage and Neo4j for knowledge graph.
    LLM and embeddings are configured from config.yml.
    """
    from mem0 import Memory
    from mem0.configs.base import (
        EmbedderConfig,
        GraphStoreConfig,
        LlmConfig,
        MemoryConfig,
        VectorStoreConfig,
    )

    from app.config import get_api_key

    llm_config = config.get("llm", {})
    embeddings_config = config.get("embeddings", {})

    llm_api_key = get_api_key(llm_config)
    embeddings_api_key = get_api_key(embeddings_config)

    postgres_password = os.environ.get("POSTGRES_PASSWORD", "")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "")

    mem_config = MemoryConfig(
        version="v1.1",
        llm=LlmConfig(
            provider="openai",
            config={
                "api_key": llm_api_key or "",
                "model": llm_config.get("model", "gpt-4o-mini"),
                "openai_base_url": llm_config.get("base_url", "https://api.openai.com/v1"),
            },
        ),
        embedder=EmbedderConfig(
            provider="openai",
            config={
                "api_key": embeddings_api_key or "",
                "model": embeddings_config.get("model", "text-embedding-3-small"),
                "openai_base_url": embeddings_config.get(
                    "base_url", "https://api.openai.com/v1"
                ),
            },
        ),
        vector_store=VectorStoreConfig(
            provider="pgvector",
            config={
                "host": os.environ.get("POSTGRES_HOST", "context-broker-postgres"),
                "port": int(os.environ.get("POSTGRES_PORT", "5432")),
                "dbname": os.environ.get("POSTGRES_DB", "context_broker"),
                "user": os.environ.get("POSTGRES_USER", "context_broker"),
                "password": postgres_password,
                "collection_name": "mem0_memories",
                "embedding_model_dims": _get_embedding_dims(config, embeddings_config),
                "diskann": False,
            },
        ),
        graph_store=GraphStoreConfig(
            provider="neo4j",
            config=_neo4j_config(neo4j_password),
        ),
    )

    return Memory(config=mem_config)


def _neo4j_config(password: str) -> dict:
    """Build Neo4j connection config, omitting credentials when AUTH=none.

    R2-F20: When NEO4J_PASSWORD is empty (the default), credentials are
    intentionally omitted from the config. This matches the NEO4J_AUTH=none
    setting in docker-compose.yml where Neo4j runs without authentication
    on the internal Docker network.
    """
    url = f"bolt://{os.environ.get('NEO4J_HOST', 'context-broker-neo4j')}:{os.environ.get('NEO4J_PORT', '7687')}"
    cfg: dict = {"url": url}
    if password:
        cfg["username"] = "neo4j"
        cfg["password"] = password
    return cfg


def _get_embedding_dims(config: dict, embeddings_config: dict) -> int:
    """Return the embedding dimensions for the configured model.

    Checks for an explicit ``embedding_dims`` value in the top-level config
    first (settable in config.yml).  Falls back to a built-in lookup table
    keyed by model name, defaulting to 1536.
    """
    # Prefer explicit config override
    configured_dims = config.get("embedding_dims")
    if configured_dims is not None:
        return int(configured_dims)

    model = embeddings_config.get("model", "text-embedding-3-small")
    # Known dimension mappings (fallback)
    dims_map = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
        # M-19: Ollama / nomic models
        "nomic-embed-text": 768,
        "nomic-embed-text:latest": 768,
    }
    return dims_map.get(model, 1536)
```

---

`app/metrics_registry.py`

```python
"""
Prometheus metrics registry for the Context Broker.

All metrics are defined here and imported by flows and routes.
Metrics are incremented inside StateGraph nodes, not in route handlers.
"""

from prometheus_client import Counter, Histogram, Gauge

# MCP tool request metrics
MCP_REQUESTS = Counter(
    "context_broker_mcp_requests_total",
    "Total MCP tool requests",
    ["tool", "status"],
)

MCP_REQUEST_DURATION = Histogram(
    "context_broker_mcp_request_duration_seconds",
    "Duration of MCP tool requests",
    ["tool"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0],
)

# Chat endpoint metrics
CHAT_REQUESTS = Counter(
    "context_broker_chat_requests_total",
    "Total chat completion requests",
    ["status"],
)

CHAT_REQUEST_DURATION = Histogram(
    "context_broker_chat_request_duration_seconds",
    "Duration of chat completion requests",
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)

# Background job metrics
JOBS_ENQUEUED = Counter(
    "context_broker_jobs_enqueued_total",
    "Total background jobs enqueued",
    ["job_type"],
)

JOBS_COMPLETED = Counter(
    "context_broker_jobs_completed_total",
    "Total background jobs completed",
    ["job_type", "status"],
)

JOB_DURATION = Histogram(
    "context_broker_job_duration_seconds",
    "Duration of background jobs",
    ["job_type"],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

# Queue depth gauges
EMBEDDING_QUEUE_DEPTH = Gauge(
    "context_broker_embedding_queue_depth",
    "Number of pending embedding jobs",
)

ASSEMBLY_QUEUE_DEPTH = Gauge(
    "context_broker_assembly_queue_depth",
    "Number of pending context assembly jobs",
)

EXTRACTION_QUEUE_DEPTH = Gauge(
    "context_broker_extraction_queue_depth",
    "Number of pending memory extraction jobs",
)

# Context assembly metrics
CONTEXT_ASSEMBLY_DURATION = Histogram(
    "context_broker_context_assembly_duration_seconds",
    "Duration of context assembly operations",
    ["build_type"],
    buckets=[0.5, 1.0, 5.0, 10.0, 30.0, 60.0],
)
```

---

`app/migrations.py`

```python
"""
Database schema migration management.

Applies pending migrations on startup. Migrations are forward-only
and non-destructive. The application refuses to start if a migration
cannot be safely applied.
"""

import logging
from typing import Callable

import asyncpg

from app.database import get_pg_pool

_log = logging.getLogger("context_broker.migrations")


async def _migration_001(conn) -> None:
    """Migration 1: Initial schema.

    The initial schema is applied by postgres/init.sql via the Docker
    entrypoint. This migration just records that it was applied.
    """
    pass


async def _migration_002(conn) -> None:
    """Migration 2: Ensure participant_id index exists on context_windows.

    Safe to run multiple times (CREATE INDEX IF NOT EXISTS).
    """
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_windows_participant_conversation
        ON context_windows(participant_id, conversation_id)
        """
    )


async def _migration_003(conn) -> None:
    """Migration 3: Add unique constraint on (conversation_id, sequence_number).

    Prevents duplicate sequence numbers under concurrent inserts.
    Safe to run multiple times (CREATE UNIQUE INDEX IF NOT EXISTS).
    """
    await conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_conversation_seq_unique
        ON conversation_messages(conversation_id, sequence_number)
        """
    )


async def _migration_004(conn) -> None:
    """Migration 4: Add recipient_id column to conversation_messages.

    Captures who the message was addressed to alongside the existing sender_id.
    Safe to run multiple times (ADD COLUMN IF NOT EXISTS).
    """
    await conn.execute(
        """
        ALTER TABLE conversation_messages
        ADD COLUMN IF NOT EXISTS recipient_id VARCHAR(255)
        """
    )


async def _migration_005(conn) -> None:
    """Migration 5: Add flow_id, user_id to conversations (F-05)."""
    await conn.execute(
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS flow_id VARCHAR(255)"
    )
    await conn.execute(
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS user_id VARCHAR(255)"
    )


async def _migration_006(conn) -> None:
    """Migration 6: Add content_type, priority, repeat_count to conversation_messages (F-04, F-06)."""
    await conn.execute(
        "ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS content_type VARCHAR(50) DEFAULT 'text'"
    )
    await conn.execute(
        "ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 0"
    )
    await conn.execute(
        "ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS repeat_count INTEGER DEFAULT 1"
    )


async def _migration_007(conn) -> None:
    """Migration 7: Prevent duplicate summary rows under concurrent assembly (M-08).

    Adds a unique index on (context_window_id, tier, summarizes_from_seq, summarizes_to_seq)
    to prevent duplicate summary rows when multiple workers race to summarize the same range.
    """
    await conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_summaries_window_tier_seq
        ON conversation_summaries(context_window_id, tier, summarizes_from_seq, summarizes_to_seq)
        """
    )


async def _migration_008(conn) -> None:
    """Migration 8: Attempt to create Mem0 dedup index (F-19).

    Mem0 creates its own tables on first use. This migration attempts
    to add a dedup index on mem0_memories. If the table doesn't exist yet,
    this is a no-op (the index will be created on next startup after
    Mem0 has initialized).
    """
    try:
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mem0_memories_dedup
            ON mem0_memories(memory, user_id)
            """
        )
        _log.info("Mem0 dedup index created or already exists")
    except asyncpg.UndefinedTableError:
        _log.info("Mem0 table not yet created — dedup index deferred to next startup")


async def _migration_009(conn) -> None:
    """Migration 9: Create HNSW vector index if embeddings exist (G5-41).

    Deferred from init.sql because pgvector HNSW requires knowing the
    vector dimension. We detect the dimension from existing data.
    """
    dim = await conn.fetchval(
        "SELECT vector_dims(embedding) FROM conversation_messages WHERE embedding IS NOT NULL LIMIT 1"
    )
    if dim is not None:
        await conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_messages_embedding
            ON conversation_messages USING hnsw ((embedding::vector({dim})) vector_cosine_ops)
            """
        )
        _log.info("HNSW index created for %d-dimensional embeddings", dim)
    else:
        _log.info("No embeddings yet — HNSW index deferred to next startup")


async def _migration_010(conn) -> None:
    """Migration 10: Unique constraint on context_windows for idempotent creation (G5-08).

    Prevents duplicate context windows for the same (conversation, participant, build_type).
    Safe to run multiple times (CREATE UNIQUE INDEX IF NOT EXISTS).
    """
    await conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_windows_conv_participant_build
        ON context_windows(conversation_id, participant_id, build_type)
        """
    )


async def _migration_011(conn) -> None:
    """Migration 11: Add last_accessed_at to context_windows.

    Tracks when a context window was last retrieved, enabling dormant
    window detection and deferred assembly.
    """
    await conn.execute(
        "ALTER TABLE context_windows ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMP WITH TIME ZONE"
    )


async def _migration_012(conn) -> None:
    """Migration 12: Schema alignment for ARCH-01, ARCH-08, ARCH-09, ARCH-12, ARCH-13.

    Comprehensive column renames, additions, drops, and constraint changes
    on conversation_messages to align with the v4 schema design.

    All statements use IF EXISTS / IF NOT EXISTS guards so the migration
    is safe to run against databases in any intermediate state.
    """

    # ── ARCH-13: Rename sender_id → sender ──────────────────────────
    has_sender_id = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'conversation_messages' AND column_name = 'sender_id'
        )
        """
    )
    if has_sender_id:
        await conn.execute(
            "ALTER TABLE conversation_messages RENAME COLUMN sender_id TO sender"
        )
        _log.info("Renamed sender_id → sender")

    # ── ARCH-13: Rename recipient_id → recipient ────────────────────
    has_recipient_id = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'conversation_messages' AND column_name = 'recipient_id'
        )
        """
    )
    if has_recipient_id:
        await conn.execute(
            "ALTER TABLE conversation_messages RENAME COLUMN recipient_id TO recipient"
        )
        _log.info("Renamed recipient_id → recipient")

    # ── ARCH-12: NOT NULL on recipient (backfill first) ─────────────
    await conn.execute(
        "UPDATE conversation_messages SET recipient = 'unknown' WHERE recipient IS NULL"
    )
    # Check if the column already has a NOT NULL constraint
    is_nullable = await conn.fetchval(
        """
        SELECT is_nullable FROM information_schema.columns
        WHERE table_name = 'conversation_messages' AND column_name = 'recipient'
        """
    )
    if is_nullable == "YES":
        await conn.execute(
            "ALTER TABLE conversation_messages ALTER COLUMN recipient SET DEFAULT 'unknown'"
        )
        await conn.execute(
            "ALTER TABLE conversation_messages ALTER COLUMN recipient SET NOT NULL"
        )
        _log.info("Set NOT NULL constraint on recipient with default 'unknown'")

    # ── ARCH-01: Add tool_calls (JSONB) ─────────────────────────────
    await conn.execute(
        "ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS tool_calls JSONB"
    )

    # ── ARCH-01: Add tool_call_id ───────────────────────────────────
    await conn.execute(
        "ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS tool_call_id VARCHAR(255)"
    )

    # ── ARCH-08: Drop content_type column ───────────────────────────
    await conn.execute(
        "ALTER TABLE conversation_messages DROP COLUMN IF EXISTS content_type"
    )

    # ── ARCH-09: Drop idempotency unique index ──────────────────────
    await conn.execute("DROP INDEX IF EXISTS idx_messages_idempotency")

    # ── ARCH-09: Drop idempotency_key column ────────────────────────
    await conn.execute(
        "ALTER TABLE conversation_messages DROP COLUMN IF EXISTS idempotency_key"
    )

    # ── ARCH-01: Make content nullable ──────────────────────────────
    # (tool-call messages may have no text content)
    await conn.execute(
        "ALTER TABLE conversation_messages ALTER COLUMN content DROP NOT NULL"
    )

    # ── ARCH-13: Rename sender index to match new column name ───────
    # PostgreSQL doesn't have ALTER INDEX IF EXISTS … RENAME, so check first.
    idx_exists = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE indexname = 'idx_messages_conversation_sender'
        )
        """
    )
    if idx_exists:
        await conn.execute(
            "ALTER INDEX idx_messages_conversation_sender RENAME TO idx_messages_conversation_sender_new"
        )
        _log.info("Renamed sender index → idx_messages_conversation_sender_new")

    # ── Safety net: Ensure last_accessed_at exists on context_windows
    # (in case migration 011 was skipped or partially applied) ───────
    await conn.execute(
        "ALTER TABLE context_windows ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMP WITH TIME ZONE"
    )

    _log.info("Migration 012 complete — schema aligned with v4 design")


# Migration registry: version -> (description, migration_function)
# Add new migrations here. Never modify existing entries.
# IMPORTANT: This list MUST appear after all _migration_NNN function definitions.
MIGRATIONS: list[tuple[int, str, Callable]] = [
    (1, "Initial schema — created by postgres/init.sql", _migration_001),
    (2, "Add participant_id index on context_windows", _migration_002),
    (3, "Add unique constraint on (conversation_id, sequence_number)", _migration_003),
    (4, "Add recipient_id column to conversation_messages", _migration_004),
    (5, "Add flow_id, user_id to conversations", _migration_005),
    (6, "Add content_type, priority, repeat_count to conversation_messages", _migration_006),
    (7, "Unique index on summaries to prevent duplicate rows (M-08)", _migration_007),
    (8, "Mem0 dedup index on mem0_memories (F-19)", _migration_008),
    (9, "Deferred HNSW vector index (G5-41)", _migration_009),
    (10, "Unique constraint on context_windows (conversation_id, participant_id, build_type) (G5-08)", _migration_010),
    (11, "Add last_accessed_at to context_windows", _migration_011),
    (12, "Schema alignment: renames, tool_calls, drops, constraints (ARCH-01/08/09/12/13)", _migration_012),
]


async def get_current_schema_version(conn) -> int:
    """Return the highest applied migration version, or 0 if none."""
    try:
        version = await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        )
        return version or 0
    except asyncpg.UndefinedTableError:
        # schema_migrations table doesn't exist yet — fresh database
        return 0


async def run_migrations() -> None:
    """Apply all pending migrations in order.

    R5-m12: Uses a PostgreSQL advisory lock to serialize migrations when
    multiple workers start simultaneously. Advisory lock ID 1 is reserved
    for schema migrations.

    Raises RuntimeError if any migration fails, preventing startup
    with an incompatible schema.
    """
    pool = get_pg_pool()

    async with pool.acquire() as conn:
        # R5-m12: Acquire advisory lock to prevent concurrent migration runs
        await conn.execute("SELECT pg_advisory_lock(1)")
        try:
            current_version = await get_current_schema_version(conn)
            _log.info("Current schema version: %d", current_version)

            pending = [
                (version, description, fn)
                for version, description, fn in MIGRATIONS
                if version > current_version
            ]

            if not pending:
                _log.info("Schema is up to date (version %d)", current_version)
                return

            for version, description, migration_fn in pending:
                _log.info("Applying migration %d: %s", version, description)
                try:
                    async with conn.transaction():
                        await migration_fn(conn)
                        await conn.execute(
                            """
                            INSERT INTO schema_migrations (version, description)
                            VALUES ($1, $2)
                            ON CONFLICT (version) DO NOTHING
                            """,
                            version,
                            description,
                        )
                    _log.info("Migration %d applied successfully", version)
                except (asyncpg.PostgresError, OSError) as exc:
                    raise RuntimeError(
                        f"Migration {version} ('{description}') failed: {exc}. "
                        "Cannot start with incompatible schema."
                    ) from exc

            _log.info(
                "Schema migrations complete. Now at version %d",
                pending[-1][0],
            )
        finally:
            await conn.execute("SELECT pg_advisory_unlock(1)")
```

---

`app/models.py`

```python
"""
Pydantic models for request/response validation.

All external inputs are validated through these models before
reaching StateGraph flows.
"""

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


# ============================================================
# MCP Tool Input Models
# ============================================================


class CreateConversationInput(BaseModel):
    """Input for conv_create_conversation."""

    conversation_id: Optional[UUID] = Field(None, description="Caller-supplied ID for idempotent creation")
    title: Optional[str] = Field(None, max_length=500)
    flow_id: Optional[str] = Field(None, max_length=255)
    user_id: Optional[str] = Field(None, max_length=255)


class StoreMessageInput(BaseModel):
    """Input for conv_store_message."""

    context_window_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None
    role: str = Field(..., pattern="^(user|assistant|system|tool)$")

    @model_validator(mode="after")
    def _require_at_least_one_id(self) -> "StoreMessageInput":
        if self.context_window_id is None and self.conversation_id is None:
            raise ValueError(
                "At least one of context_window_id or conversation_id must be provided"
            )
        return self
    sender: str = Field(..., min_length=1, max_length=255)
    recipient: Optional[str] = Field(None, max_length=255)
    content: Optional[str] = Field(None)
    priority: Optional[int] = Field(0, ge=0, le=10)
    model_name: Optional[str] = Field(None, max_length=255)
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = Field(None, max_length=255)


class CreateContextWindowInput(BaseModel):
    """Input for conv_create_context_window."""

    conversation_id: UUID
    participant_id: str = Field(..., min_length=1, max_length=255)
    build_type: str = Field(..., min_length=1, max_length=100)
    max_tokens: Optional[int] = Field(None, ge=1)


class RetrieveContextInput(BaseModel):
    """Input for conv_retrieve_context."""

    context_window_id: UUID


class SearchConversationsInput(BaseModel):
    """Input for conv_search."""

    query: Optional[str] = Field(None, max_length=2000)
    limit: int = Field(10, ge=1, le=100)
    offset: int = Field(0, ge=0)
    date_from: Optional[str] = Field(None, description="ISO-8601 date lower bound")
    date_to: Optional[str] = Field(None, description="ISO-8601 date upper bound")
    flow_id: Optional[str] = Field(None, max_length=255)
    user_id: Optional[str] = Field(None, max_length=255)
    sender: Optional[str] = Field(None, max_length=255)


class SearchMessagesInput(BaseModel):
    """Input for conv_search_messages."""

    query: str = Field(..., min_length=1, max_length=2000)
    conversation_id: Optional[UUID] = None
    limit: int = Field(10, ge=1, le=100)
    sender: Optional[str] = Field(None, max_length=255)
    role: Optional[str] = Field(None, pattern="^(user|assistant|system|tool)$")
    date_from: Optional[str] = Field(None, description="ISO-8601 date lower bound")
    date_to: Optional[str] = Field(None, description="ISO-8601 date upper bound")


class GetHistoryInput(BaseModel):
    """Input for conv_get_history."""

    conversation_id: UUID
    limit: Optional[int] = Field(None, ge=1, le=10000)


class SearchContextWindowsInput(BaseModel):
    """Input for conv_search_context_windows."""

    context_window_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None
    participant_id: Optional[str] = Field(None, max_length=255)
    build_type: Optional[str] = Field(None, max_length=100)
    limit: int = Field(10, ge=1, le=100)


class MemSearchInput(BaseModel):
    """Input for mem_search."""

    query: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(..., min_length=1, max_length=255)
    limit: int = Field(10, ge=1, le=100)


class MemGetContextInput(BaseModel):
    """Input for mem_get_context."""

    query: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(..., min_length=1, max_length=255)
    limit: int = Field(5, ge=1, le=50)


class MemAddInput(BaseModel):
    """Input for mem_add — directly add a memory to Mem0."""

    content: str = Field(..., min_length=1, max_length=10000)
    user_id: str = Field(..., min_length=1, max_length=255)


class MemListInput(BaseModel):
    """Input for mem_list — list all memories for a user."""

    user_id: str = Field(..., min_length=1, max_length=255)
    limit: int = Field(50, ge=1, le=500)


class MemDeleteInput(BaseModel):
    """Input for mem_delete — delete a specific memory by ID."""

    memory_id: str = Field(..., min_length=1, max_length=255)


class ImperatorChatInput(BaseModel):
    """Input for imperator_chat."""

    message: str = Field(..., min_length=1, max_length=32000)
    context_window_id: Optional[UUID] = None


class MetricsGetInput(BaseModel):
    """Input for metrics_get (no required fields)."""

    pass


# ============================================================
# OpenAI-compatible chat models
# ============================================================


class ChatMessage(BaseModel):
    """A single message in an OpenAI-compatible chat request."""

    role: str = Field(..., pattern="^(system|user|assistant|tool)$")
    content: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible /v1/chat/completions request body."""

    model: str = Field(default="context-broker")
    messages: list[ChatMessage] = Field(..., min_length=1)
    stream: bool = False
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1)



# ============================================================
# MCP Protocol Models
# ============================================================


class MCPToolCall(BaseModel):
    """MCP JSON-RPC tools/call request."""

    jsonrpc: str = Field(default="2.0")
    id: Optional[Any] = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class MCPToolResult(BaseModel):
    """MCP JSON-RPC tools/call response."""

    jsonrpc: str = "2.0"
    id: Optional[Any] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None
```

---

`app/prompt_loader.py`

```python
"""
Prompt template loader.

Loads externalized prompt templates from /config/prompts/.
Templates are cached with mtime check — only re-read when the file changes (M-11).
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

_log = logging.getLogger("context_broker.prompt_loader")

PROMPTS_DIR = Path(os.environ.get("PROMPTS_DIR", "/config/prompts"))

# Cache: name -> (mtime, content)
_prompt_cache: dict[str, tuple[float, str]] = {}


def _read_prompt_file(path: Path) -> str:
    """Read and strip a prompt template file from disk.

    Separated from load_prompt() so that async_load_prompt() can
    offload only this blocking portion to run_in_executor.
    """
    return path.read_text(encoding="utf-8").strip()


def load_prompt(name: str) -> str:
    """Load a prompt template by name (without extension).

    Reads from /config/prompts/{name}.md. Caches the result and only
    re-reads the file when its mtime changes. os.stat() is near-instant
    so this avoids repeated synchronous file I/O in async paths (M-11).

    G5-06: This function performs blocking file I/O (os.stat + read_text).
    The mtime cache means the file is only re-read when it actually changes
    on disk, which is rare in production. The os.stat() fast-path check is
    near-instant for local files. Async callers (route handlers, flow nodes)
    should use async_load_prompt() instead, which offloads the file read to
    run_in_executor when a re-read is triggered.

    Raises RuntimeError if the template file cannot be found.
    """
    path = PROMPTS_DIR / f"{name}.md"
    try:
        current_mtime = os.stat(path).st_mtime
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Prompt template not found: {path}. "
            "Ensure prompt files are mounted at /config/prompts/."
        ) from exc

    cached = _prompt_cache.get(name)
    if cached is not None and cached[0] == current_mtime:
        return cached[1]

    content = _read_prompt_file(path)
    _prompt_cache[name] = (current_mtime, content)
    return content


async def async_load_prompt(name: str) -> str:
    """Async wrapper for load_prompt().

    Uses the same mtime-based cache as load_prompt(). The os.stat()
    fast-path check is synchronous (near-instant for local files).
    Only when a re-read is actually needed does it offload the file
    read to run_in_executor to avoid blocking the event loop.

    Route handlers and flow nodes should prefer this over load_prompt().
    """
    path = PROMPTS_DIR / f"{name}.md"
    try:
        current_mtime = os.stat(path).st_mtime
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Prompt template not found: {path}. "
            "Ensure prompt files are mounted at /config/prompts/."
        ) from exc

    cached = _prompt_cache.get(name)
    if cached is not None and cached[0] == current_mtime:
        return cached[1]

    loop = asyncio.get_running_loop()
    content = await loop.run_in_executor(None, _read_prompt_file, path)
    _prompt_cache[name] = (current_mtime, content)
    return content
```

---

`app/routes/chat.py`

```python
"""
OpenAI-compatible chat completions endpoint.

Implements /v1/chat/completions following the OpenAI API specification.
Routes to the Imperator StateGraph.
Supports both streaming (SSE) and non-streaming responses.
"""

import json
import logging
import time
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from app.config import async_load_config
from app.flows.imperator_flow import build_imperator_flow
from app.metrics_registry import CHAT_REQUESTS, CHAT_REQUEST_DURATION
from app.models import ChatCompletionRequest

_log = logging.getLogger("context_broker.routes.chat")

router = APIRouter()

# Lazy-initialized Imperator flow — compiled on first use
_imperator_flow = None


def _get_imperator_flow():
    global _imperator_flow
    if _imperator_flow is None:
        _imperator_flow = build_imperator_flow()
    return _imperator_flow


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    """Handle OpenAI-compatible chat completion requests.

    Routes to the Imperator StateGraph. Supports streaming and non-streaming.
    """
    start_time = time.monotonic()
    status = "error"
    is_streaming = False

    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        _log.warning("Chat: failed to parse request body: %s", exc)
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Invalid JSON", "type": "invalid_request_error"}},
        )

    try:
        chat_request = ChatCompletionRequest(**body)
    except ValidationError as exc:
        _log.warning("Chat: request validation failed: %s", exc)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "message": str(exc),
                    "type": "invalid_request_error",
                }
            },
        )

    is_streaming = chat_request.stream

    config = await async_load_config()
    imperator_manager = getattr(request.app.state, "imperator_manager", None)

    # Extract the last user message as the primary input
    user_messages = [m for m in chat_request.messages if m.role == "user"]
    if not user_messages:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "At least one user message is required",
                    "type": "invalid_request_error",
                }
            },
        )

    # G5-27: Allow clients to specify a context_window_id for multi-client
    # isolation via x-context-window-id header or context_window_id in the body.
    # Also accepts the legacy x-conversation-id / conversation_id for compatibility.
    # Falls back to the default Imperator context window when not provided.
    context_window_id = (
        request.headers.get("x-context-window-id")
        or body.get("context_window_id")
        or request.headers.get("x-conversation-id")
        or body.get("conversation_id")
    )
    if not context_window_id and imperator_manager is not None:
        context_window_id = await imperator_manager.get_context_window_id()

    # Convert plain messages to LangChain message objects
    # G5-28: Include ToolMessage so tool-role messages are not coerced to HumanMessage.
    _role_map = {"user": HumanMessage, "system": SystemMessage, "assistant": AIMessage, "tool": ToolMessage}
    lc_messages = []
    for m in chat_request.messages:
        cls = _role_map.get(m.role, HumanMessage)
        if cls is ToolMessage:
            # ToolMessage requires a tool_call_id; use the one from the
            # request body if available, otherwise fall back to a placeholder.
            tool_call_id = getattr(m, "tool_call_id", None) or "unknown"
            lc_messages.append(ToolMessage(content=m.content, tool_call_id=tool_call_id))
        else:
            lc_messages.append(cls(content=m.content))

    initial_state = {
        "messages": lc_messages,
        "context_window_id": str(context_window_id) if context_window_id else None,
        "config": config,
        "response_text": None,
        "error": None,
    }

    try:
        if chat_request.stream:
            # For streaming, metrics are tracked inside the generator after
            # the stream completes, not here in the route handler.
            return StreamingResponse(
                _stream_imperator_response(initial_state, chat_request, start_time),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        else:
            result = await _get_imperator_flow().ainvoke(initial_state)

            if result.get("error"):
                _log.error("Imperator flow error: %s", result["error"])
                status = "error"
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "message": result["error"],
                            "type": "internal_error",
                        }
                    },
                )

            response_text = result.get("response_text", "")
            status = "success"

            return JSONResponse(
                content=_build_completion_response(response_text, chat_request.model)
            )

    except (RuntimeError, ConnectionError, OSError) as exc:
        _log.error("Chat completion failed: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": "Internal server error",
                    "type": "internal_error",
                }
            },
        )
    finally:
        # Only record metrics for non-streaming requests here.
        # Streaming metrics are recorded in the generator.
        if not is_streaming:
            duration = time.monotonic() - start_time
            CHAT_REQUESTS.labels(status=status).inc()
            CHAT_REQUEST_DURATION.observe(duration)


async def _stream_imperator_response(
    initial_state: dict,
    chat_request: ChatCompletionRequest,
    start_time: float,
) -> AsyncGenerator[str, None]:
    """Stream the Imperator response as SSE tokens.

    M-22: astream_events(version="v2") captures on_chat_model_stream events
    from nested ainvoke() calls within the LangGraph runtime, so real token
    streaming works without requiring the agent to use astream() internally.
    If a provider/model does not emit streaming tokens via ainvoke (e.g. some
    local models), true per-token streaming would require the Imperator's
    final non-tool-call LLM invocation to use llm.astream() instead. This is
    a known limitation; the current implementation works correctly with
    OpenAI-compatible providers that support streaming under the hood.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    stream_status = "success"

    try:
        # G5-29: Known limitation — when the ReAct agent processes tool calls,
        # astream_events may emit no content tokens for those intermediate LLM
        # turns (only the final non-tool-call turn produces streamable tokens).
        # This is inherent to how LangGraph processes tool calls and is not a bug.
        async for event in _get_imperator_flow().astream_events(
            initial_state, version="v2"
        ):
            if event["event"] == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if token:
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": chat_request.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": token},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"

        # Final chunk with finish_reason
        final_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": chat_request.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
        yield f"data: {json.dumps(final_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    except (RuntimeError, ConnectionError, OSError) as exc:
        _log.error("Streaming imperator response failed: %s", exc, exc_info=True)
        stream_status = "error"
        error_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": chat_request.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "An error occurred processing your request."},
                    "finish_reason": "stop",
                }
            ],
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    finally:
        # Record streaming metrics after the stream completes
        duration = time.monotonic() - start_time
        CHAT_REQUESTS.labels(status=stream_status).inc()
        CHAT_REQUEST_DURATION.observe(duration)


def _build_completion_response(response_text: str, model: str) -> dict:
    """Build an OpenAI-compatible non-streaming completion response."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": -1,
            "completion_tokens": -1,
            "total_tokens": -1,
        },
    }
```

---

`app/routes/health.py`

```python
"""
Health check endpoint.

Tests all backing service connections and returns aggregated status.
The LangGraph container performs the actual dependency checks —
nginx proxies the response without performing checks itself.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import async_load_config
from app.flows.health_flow import build_health_check_flow

_log = logging.getLogger("context_broker.routes.health")

router = APIRouter()

# R6-m1: Lazy-initialized flow singleton — compiled on first use instead of
# at import time, so module import doesn't trigger graph compilation.
_health_flow = None


def _get_health_flow():
    global _health_flow
    if _health_flow is None:
        _health_flow = build_health_check_flow()
    return _health_flow


@router.get("/health")
async def health_check(request: Request) -> JSONResponse:
    """Check connectivity to all backing services.

    Returns 200 if all critical services are healthy.
    Returns 503 if any critical service is unhealthy.
    """
    config = await async_load_config()

    result = await _get_health_flow().ainvoke(
        {
            "config": config,
            "postgres_ok": False,
            "redis_ok": False,
            "neo4j_ok": False,
            "all_healthy": False,
            "status_detail": None,
            "http_status": 503,
        }
    )

    return JSONResponse(
        status_code=result["http_status"],
        content=result["status_detail"],
    )
```

---

`app/routes/mcp.py`

```python
"""
MCP (Model Context Protocol) endpoint.

Implements HTTP/SSE transport for MCP tool access.
Routes tool calls to compiled StateGraph flows.

Endpoints:
  GET  /mcp          — Establish SSE session
  POST /mcp          — Sessionless tool call or route to session
  POST /mcp?sessionId=xxx — Route to existing session
"""

import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.config import async_load_config, get_tuning
from app.flows.tool_dispatch import dispatch_tool
from app.metrics_registry import (
    MCP_REQUEST_DURATION,
    MCP_REQUESTS,
)
from app.models import MCPToolCall

_log = logging.getLogger("context_broker.routes.mcp")

router = APIRouter()

# Active SSE sessions: session_id -> {"queue": asyncio.Queue, "created_at": float}
# OrderedDict preserves insertion order for efficient eviction of oldest sessions.
_sessions: OrderedDict[str, dict[str, Any]] = OrderedDict()

# R5-M25: Track total queued messages across all sessions to bound memory
_total_queued_messages: int = 0

# Configurable limits (defaults; overridden by config at request time)
_MAX_SESSIONS = 1000
_SESSION_TTL_SECONDS = 3600  # 1 hour
_MAX_TOTAL_QUEUED = 10000

# R6-M3: Lock for session dict mutations to prevent race conditions
_session_lock = asyncio.Lock()


def _evict_stale_sessions() -> None:
    """Remove sessions older than TTL and enforce the max sessions cap.

    R5-M25: Also evict oldest sessions when total queued messages exceed the cap.
    """
    global _total_queued_messages
    now = time.monotonic()
    stale_ids = [
        sid for sid, info in _sessions.items()
        if now - info["created_at"] > _SESSION_TTL_SECONDS
    ]
    for sid in stale_ids:
        info = _sessions.pop(sid, None)
        if info is not None:
            _total_queued_messages -= info["queue"].qsize()
        _log.info("MCP SSE session evicted (TTL): %s", sid)

    # Evict oldest if over cap
    while len(_sessions) > _MAX_SESSIONS:
        evicted_id, info = _sessions.popitem(last=False)
        _total_queued_messages -= info["queue"].qsize()
        _log.info("MCP SSE session evicted (cap): %s", evicted_id)

    # R5-M25: Evict oldest sessions if total queued messages exceed threshold
    while _total_queued_messages > _MAX_TOTAL_QUEUED and _sessions:
        evicted_id, info = _sessions.popitem(last=False)
        _total_queued_messages -= info["queue"].qsize()
        _log.warning(
            "MCP SSE session evicted (total queue pressure): %s", evicted_id
        )


@router.get("/mcp")
async def mcp_sse_session(request: Request) -> StreamingResponse:
    """Establish an SSE session for MCP communication.

    Returns an SSE stream. The client sends tool calls via
    POST /mcp?sessionId=<id>.
    """
    # Evict stale/over-cap sessions before creating a new one
    config = await async_load_config()
    global _MAX_SESSIONS, _SESSION_TTL_SECONDS, _MAX_TOTAL_QUEUED
    _MAX_SESSIONS = get_tuning(config, "mcp_max_sessions", 1000)
    _SESSION_TTL_SECONDS = get_tuning(config, "mcp_session_ttl_seconds", 3600)
    _MAX_TOTAL_QUEUED = get_tuning(config, "mcp_max_total_queued", 10000)

    session_id = str(uuid.uuid4())

    # R6-M3: Protect session dict mutations with asyncio.Lock
    async with _session_lock:
        _evict_stale_sessions()
        # G5-26: Bound the per-session queue to prevent memory growth from slow clients
        message_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        _sessions[session_id] = {"queue": message_queue, "created_at": time.monotonic()}

    _log.info("MCP SSE session established: %s (active=%d)", session_id, len(_sessions))

    async def event_stream() -> AsyncGenerator[str, None]:
        # Send session ID as first event
        yield f"data: {json.dumps({'sessionId': session_id})}\n\n"

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(message_queue.get(), timeout=30.0)
                    # R5-M25: Decrement global counter when message is consumed
                    global _total_queued_messages
                    _total_queued_messages = max(0, _total_queued_messages - 1)
                    yield f"data: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
        finally:
            # R6-M3: Protect session dict mutations with asyncio.Lock
            async with _session_lock:
                # R6-M2: Decrement global counter by remaining queue size before removal
                removed = _sessions.pop(session_id, None)
                if removed is not None:
                    _total_queued_messages = max(0, _total_queued_messages - removed["queue"].qsize())
            _log.info("MCP SSE session closed: %s", session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/mcp")
async def mcp_tool_call(
    request: Request,
    session_id: str = Query(None, alias="sessionId"),
) -> JSONResponse:
    """Handle an MCP tool call.

    Supports both sessionless mode (no sessionId) and session mode.
    All tool calls are routed through StateGraph flows.
    """
    start_time = time.monotonic()
    tool_name = "unknown"
    status = "error"

    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        _log.warning("MCP: failed to parse request body: %s", exc)
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
        )

    try:
        mcp_request = MCPToolCall(**body)
    except ValidationError as exc:
        _log.warning("MCP: invalid request structure: %s", exc)
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32600, "message": "Invalid Request"},
            },
        )

    if mcp_request.method == "initialize":
        tool_name = "initialize"
        status = "success"
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": mcp_request.id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "context-broker",
                        "version": "1.0.0",
                    },
                },
            }
        )

    if mcp_request.method == "tools/list":
        tool_name = "tools_list"
        status = "success"
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": mcp_request.id,
                "result": {"tools": _get_tool_list()},
            }
        )

    if mcp_request.method != "tools/call":
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": mcp_request.id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {mcp_request.method}",
                },
            },
        )

    tool_name = mcp_request.params.get("name", "unknown")
    tool_arguments = mcp_request.params.get("arguments", {})

    config = await async_load_config()

    try:
        result = await dispatch_tool(tool_name, tool_arguments, config, request.app.state)
        status = "success"

        response_content = {
            "jsonrpc": "2.0",
            "id": mcp_request.id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result)
                        if isinstance(result, dict)
                        else str(result),
                    }
                ]
            },
        }

        # If session mode, push to session queue and return acknowledgment
        if session_id:
            if session_id not in _sessions:
                # G5-25: Unknown sessionId — return error instead of falling through
                return JSONResponse(
                    status_code=404,
                    content={
                        "jsonrpc": "2.0",
                        "id": mcp_request.id,
                        "error": {"code": -32001, "message": f"Session not found: {session_id}"},
                    },
                )
            try:
                _sessions[session_id]["queue"].put_nowait(response_content)
                global _total_queued_messages
                _total_queued_messages += 1
            except asyncio.QueueFull:
                _log.warning(
                    "MCP SSE session queue full for session=%s; dropping response",
                    session_id,
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "jsonrpc": "2.0",
                        "id": mcp_request.id,
                        "error": {
                            "code": -32000,
                            "message": "Session queue full — client is not consuming events fast enough",
                        },
                    },
                )
            return JSONResponse(content={
                "jsonrpc": "2.0",
                "id": mcp_request.id,
                "result": "queued",
            })

        return JSONResponse(content=response_content)

    except (ValueError, ValidationError) as exc:
        status = "validation_error"
        _log.warning("MCP tool '%s' validation error: %s", tool_name, exc)
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": mcp_request.id,
                "error": {"code": -32602, "message": str(exc)},
            },
        )
    except (RuntimeError, ConnectionError, OSError) as exc:
        status = "internal_error"
        _log.error("MCP tool '%s' failed: %s", tool_name, exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "jsonrpc": "2.0",
                "id": mcp_request.id,
                "error": {"code": -32000, "message": str(exc)},
            },
        )
    finally:
        duration = time.monotonic() - start_time
        MCP_REQUESTS.labels(tool=tool_name, status=status).inc()
        MCP_REQUEST_DURATION.labels(tool=tool_name).observe(duration)


def _get_tool_list() -> list[dict]:
    """Return the MCP tool list with schemas."""
    return [
        {
            "name": "conv_create_conversation",
            "description": "Create a new conversation",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string", "format": "uuid", "description": "Caller-supplied ID for idempotent creation"},
                    "title": {"type": "string", "description": "Optional conversation title"},
                    "flow_id": {"type": "string", "description": "Optional flow identifier"},
                    "user_id": {"type": "string", "description": "Optional user identifier"},
                },
            },
        },
        {
            "name": "conv_store_message",
            "description": "Store a message in a conversation (triggers async embedding, assembly, extraction). At least one of context_window_id or conversation_id must be provided.",
            "inputSchema": {
                "type": "object",
                "required": ["role", "sender"],
                "properties": {
                    "context_window_id": {"type": "string", "format": "uuid", "description": "Context window ID (resolves conversation automatically). At least one of context_window_id or conversation_id is required."},
                    "conversation_id": {"type": "string", "format": "uuid", "description": "Direct conversation ID (skips context window lookup). At least one of context_window_id or conversation_id is required."},
                    "role": {"type": "string", "enum": ["user", "assistant", "system", "tool"]},
                    "sender": {"type": "string"},
                    "recipient": {"type": "string"},
                    "content": {"type": "string"},
                    "priority": {"type": "integer", "default": 0},
                    "model_name": {"type": "string"},
                    "tool_calls": {"type": "object"},
                    "tool_call_id": {"type": "string"},
                },
            },
        },
        {
            "name": "conv_retrieve_context",
            "description": "Retrieve the assembled context window for a participant",
            "inputSchema": {
                "type": "object",
                "required": ["context_window_id"],
                "properties": {
                    "context_window_id": {"type": "string", "format": "uuid"},
                },
            },
        },
        {
            "name": "conv_create_context_window",
            "description": "Create a context window instance with a build type and token budget",
            "inputSchema": {
                "type": "object",
                "required": ["conversation_id", "participant_id", "build_type"],
                "properties": {
                    "conversation_id": {"type": "string", "format": "uuid"},
                    "participant_id": {"type": "string"},
                    "build_type": {"type": "string"},
                    "max_tokens": {"type": "integer"},
                },
            },
        },
        {
            "name": "conv_search",
            "description": "Semantic and structured search across conversations",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                    "offset": {"type": "integer", "default": 0},
                    "date_from": {"type": "string", "description": "ISO-8601 date lower bound"},
                    "date_to": {"type": "string", "description": "ISO-8601 date upper bound"},
                    "flow_id": {"type": "string", "description": "Filter by flow identifier"},
                    "user_id": {"type": "string", "description": "Filter by user identifier"},
                    "sender": {"type": "string", "description": "Filter by sender (matches conversations containing messages from this sender)"},
                },
            },
        },
        {
            "name": "conv_search_messages",
            "description": "Hybrid search (vector + BM25 + reranking) across messages",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "conversation_id": {"type": "string", "format": "uuid"},
                    "sender": {"type": "string", "description": "Filter by sender"},
                    "role": {"type": "string", "enum": ["user", "assistant", "system", "tool"]},
                    "date_from": {"type": "string", "description": "ISO-8601 date lower bound"},
                    "date_to": {"type": "string", "description": "ISO-8601 date upper bound"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
        {
            "name": "conv_get_history",
            "description": "Retrieve full chronological message sequence for a conversation",
            "inputSchema": {
                "type": "object",
                "required": ["conversation_id"],
                "properties": {
                    "conversation_id": {"type": "string", "format": "uuid"},
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "conv_search_context_windows",
            "description": "Search and list context windows",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "context_window_id": {"type": "string", "format": "uuid", "description": "Look up a specific context window by ID"},
                    "conversation_id": {"type": "string", "format": "uuid"},
                    "participant_id": {"type": "string"},
                    "build_type": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
        {
            "name": "mem_search",
            "description": "Semantic and graph search across extracted knowledge",
            "inputSchema": {
                "type": "object",
                "required": ["query", "user_id"],
                "properties": {
                    "query": {"type": "string"},
                    "user_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
        {
            "name": "mem_get_context",
            "description": "Retrieve relevant memories formatted for prompt injection",
            "inputSchema": {
                "type": "object",
                "required": ["query", "user_id"],
                "properties": {
                    "query": {"type": "string"},
                    "user_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
            },
        },
        {
            "name": "mem_add",
            "description": "Directly add a memory to the knowledge graph",
            "inputSchema": {
                "type": "object",
                "required": ["content", "user_id"],
                "properties": {
                    "content": {"type": "string"},
                    "user_id": {"type": "string"},
                },
            },
        },
        {
            "name": "mem_list",
            "description": "List all memories for a user",
            "inputSchema": {
                "type": "object",
                "required": ["user_id"],
                "properties": {
                    "user_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        },
        {
            "name": "mem_delete",
            "description": "Delete a specific memory by ID",
            "inputSchema": {
                "type": "object",
                "required": ["memory_id"],
                "properties": {
                    "memory_id": {"type": "string"},
                },
            },
        },
        {
            "name": "imperator_chat",
            "description": "Conversational interface to the Imperator",
            "inputSchema": {
                "type": "object",
                "required": ["message"],
                "properties": {
                    "message": {"type": "string"},
                    "conversation_id": {"type": "string", "format": "uuid"},
                },
            },
        },
        {
            "name": "metrics_get",
            "description": "Retrieve Prometheus metrics",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]
```

---

`app/routes/metrics.py`

```python
"""
Prometheus metrics endpoint.

Exposes metrics collected from StateGraph executions.
Metrics are produced inside StateGraphs, not in route handlers.
"""

import logging

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST

from app.flows.metrics_flow import build_metrics_flow

_log = logging.getLogger("context_broker.routes.metrics")

router = APIRouter()

# R6-m1: Lazy-initialized flow singleton — compiled on first use instead of
# at import time, so module import doesn't trigger graph compilation.
_metrics_flow = None


def _get_metrics_flow():
    global _metrics_flow
    if _metrics_flow is None:
        _metrics_flow = build_metrics_flow()
    return _metrics_flow


@router.get("/metrics")
async def get_metrics() -> Response:
    """Expose Prometheus metrics in exposition format.

    Metrics are collected inside the StateGraph flow.
    """
    initial_state = {
        "action": "collect",
        "metrics_output": "",
        "error": None,
    }
    result = await _get_metrics_flow().ainvoke(initial_state)

    # G5-30: Check for flow errors and return 500 instead of masking with 200.
    if result.get("error"):
        _log.error("Metrics flow error: %s", result["error"])
        return Response(
            content=f"# ERROR: metrics collection failed: {result['error']}\n",
            media_type="text/plain",
            status_code=500,
        )

    metrics_data = result.get("metrics_output", "")
    return Response(content=metrics_data, media_type=CONTENT_TYPE_LATEST)
```

---

`app/token_budget.py`

```python
"""
Token budget resolution for context windows.

Resolves the max_context_tokens setting for a build type:
- "auto": query the configured LLM provider's model list endpoint
- explicit integer: use that value directly
- caller override: takes precedence over build type default

Token budget is resolved once at window creation and stored.
"""

import logging
from typing import Optional

import httpx

from app.config import get_api_key

_log = logging.getLogger("context_broker.token_budget")


async def resolve_token_budget(
    config: dict,
    build_type_config: dict,
    caller_override: Optional[int] = None,
) -> int:
    """Resolve the token budget for a context window.

    Priority order:
    1. caller_override (explicit max_tokens from the caller)
    2. build_type_config["max_context_tokens"] if it's an integer
    3. Auto-query the LLM provider if max_context_tokens == "auto"
    4. fallback_tokens from build_type_config

    Args:
        config: Full application config (for LLM provider settings).
        build_type_config: The build type configuration dict.
        caller_override: Optional explicit token budget from the caller.

    Returns:
        Resolved token budget as an integer.
    """
    if caller_override is not None and caller_override > 0:
        _log.info("Token budget: using caller override %d", caller_override)
        return caller_override

    max_context_tokens = build_type_config.get("max_context_tokens", "auto")
    fallback_tokens = build_type_config.get("fallback_tokens", 8192)

    if isinstance(max_context_tokens, int) and max_context_tokens > 0:
        _log.info("Token budget: using explicit build type value %d", max_context_tokens)
        return max_context_tokens

    if max_context_tokens == "auto":
        resolved = await _query_provider_context_length(config, fallback_tokens)
        _log.info("Token budget: auto-resolved to %d", resolved)
        return resolved

    _log.warning(
        "Token budget: unrecognized max_context_tokens value '%s', using fallback %d",
        max_context_tokens,
        fallback_tokens,
    )
    return fallback_tokens


async def _query_provider_context_length(config: dict, fallback: int) -> int:
    """Query the LLM provider's model list endpoint for context length.

    Returns fallback if the provider doesn't report context length or
    if the request fails.
    """
    llm_config = config.get("llm", {})
    base_url = llm_config.get("base_url", "")
    model = llm_config.get("model", "")
    api_key = get_api_key(llm_config)

    if not base_url or not model:
        _log.warning(
            "Token budget auto-resolution: LLM provider not configured, using fallback %d",
            fallback,
        )
        return fallback

    try:
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        models_url = base_url.rstrip("/") + "/models"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(models_url, headers=headers)
            response.raise_for_status()
            data = response.json()

        models = data.get("data", [])
        for model_info in models:
            if model_info.get("id") == model:
                context_length = model_info.get("context_length")
                if isinstance(context_length, int) and context_length > 0:
                    return context_length

        _log.info(
            "Token budget: model '%s' not found in provider model list, using fallback %d",
            model,
            fallback,
        )
        return fallback

    except httpx.HTTPError as exc:
        _log.warning(
            "Token budget: failed to query provider model list: %s, using fallback %d",
            exc,
            fallback,
        )
        return fallback
    except (ValueError, KeyError, OSError) as exc:
        _log.warning(
            "Token budget: unexpected error querying provider: %s, using fallback %d",
            exc,
            fallback,
        )
        return fallback
```

---

`app/workers/arq_worker.py`

```python
"""
Background worker for the Context Broker.

Processes three job types from Redis queues:
  - embedding_jobs: Generate vector embeddings for messages (list, BLMOVE)
  - context_assembly_jobs: Build context window summaries (list, BLMOVE)
  - memory_extraction_jobs: Extract knowledge into Neo4j via Mem0
    (sorted set, ZPOPMIN — highest priority / lowest score first)

Uses atomic BLMOVE (Redis 7+) for list-backed queues and ZPOPMIN for
sorted-set queues. Includes retry with backoff, dead-letter handling,
and crash-safe lock cleanup for assembly and extraction flows.
"""

import asyncio
import json
import logging
import random
import sys
import time
import uuid
from typing import Any

import redis.asyncio as aioredis
import redis.exceptions

from app.config import async_load_config, get_tuning
from app.database import get_redis
from app.flows.embed_pipeline import build_embed_pipeline
from app.flows.memory_extraction import build_memory_extraction

# ARCH-18: Import build_types package to trigger registration of all build types
import app.flows.build_types  # noqa: F401
from app.flows.build_type_registry import get_assembly_graph
from app.metrics_registry import (
    ASSEMBLY_QUEUE_DEPTH,
    EMBEDDING_QUEUE_DEPTH,
    EXTRACTION_QUEUE_DEPTH,
    JOB_DURATION,
    JOBS_COMPLETED,
)

_log = logging.getLogger("context_broker.workers.arq_worker")

# Map queue names to their depth gauges
_QUEUE_DEPTH_GAUGES = {
    "embedding_jobs": EMBEDDING_QUEUE_DEPTH,
    "context_assembly_jobs": ASSEMBLY_QUEUE_DEPTH,
    "memory_extraction_jobs": EXTRACTION_QUEUE_DEPTH,
}

# Lazy-initialized flow singletons — compiled on first use
_embed_flow = None
_extraction_flow = None


def _get_embed_flow():
    global _embed_flow
    if _embed_flow is None:
        _embed_flow = build_embed_pipeline()
    return _embed_flow


def _get_extraction_flow():
    global _extraction_flow
    if _extraction_flow is None:
        _extraction_flow = build_memory_extraction()
    return _extraction_flow


async def process_embedding_job(job: dict) -> None:
    """Process a single embedding job using the embed pipeline StateGraph."""
    config = await async_load_config()
    message_id = job.get("message_id", "")
    conversation_id = job.get("conversation_id", "")

    # M-25: Validate UUIDs from Redis job data before passing to flows
    try:
        if message_id:
            uuid.UUID(message_id)
        if conversation_id:
            uuid.UUID(conversation_id)
    except ValueError as exc:
        raise ValueError(f"Malformed UUID in embedding job: {exc}") from exc

    _log.info("Processing embedding job: message_id=%s", message_id)
    start = time.monotonic()

    result = await _get_embed_flow().ainvoke(
        {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "config": config,
            "message": None,
            "embedding": None,
            "assembly_jobs_queued": [],
            "error": None,
        }
    )

    duration = time.monotonic() - start

    if result.get("error"):
        _log.error(
            "Embedding job failed: message_id=%s error=%s",
            message_id,
            result["error"],
        )
        JOBS_COMPLETED.labels(job_type="embed_message", status="error").inc()
        raise RuntimeError(result["error"])

    _log.info(
        "Embedding job complete: message_id=%s duration_ms=%d",
        message_id,
        int(duration * 1000),
    )
    JOB_DURATION.labels(job_type="embed_message").observe(duration)
    JOBS_COMPLETED.labels(job_type="embed_message", status="success").inc()


async def process_assembly_job(job: dict) -> None:
    """Process a single context assembly job using the assembly StateGraph."""
    config = await async_load_config()
    context_window_id = job.get("context_window_id", "")
    conversation_id = job.get("conversation_id", "")
    build_type = job.get("build_type", "standard-tiered")

    # M-25: Validate UUIDs from Redis job data before passing to flows
    try:
        if context_window_id:
            uuid.UUID(context_window_id)
        if conversation_id:
            uuid.UUID(conversation_id)
    except ValueError as exc:
        raise ValueError(f"Malformed UUID in assembly job: {exc}") from exc

    _log.info("Processing assembly job: window=%s build_type=%s", context_window_id, build_type)
    start = time.monotonic()

    # ARCH-18: Look up the assembly graph from the registry by build type name
    assembly_graph = get_assembly_graph(build_type)

    lock_key = f"assembly_in_progress:{context_window_id}"
    try:
        # R5-m10: Pass only the AssemblyInput contract fields plus common
        # lock-management fields. Build-type-specific intermediate state
        # (e.g., standard-tiered's all_messages, chunks) is populated by
        # each graph's own nodes. This avoids passing keys that don't
        # exist in simpler build types like passthrough.
        result = await assembly_graph.ainvoke(
            {
                "context_window_id": context_window_id,
                "conversation_id": conversation_id,
                "config": config,
            }
        )
    except Exception:
        # R6-M5: Don't delete the lock unconditionally — that could release
        # another worker's lock. The lock has a TTL; let it expire naturally.
        _log.warning(
            "Assembly graph crashed for window=%s; lock %s will expire via TTL",
            context_window_id,
            lock_key,
        )
        raise

    duration = time.monotonic() - start

    if result.get("error"):
        _log.error(
            "Assembly job failed: window=%s error=%s",
            context_window_id,
            result["error"],
        )
        JOBS_COMPLETED.labels(job_type="assemble_context", status="error").inc()
        raise RuntimeError(result["error"])

    _log.info(
        "Assembly job complete: window=%s duration_ms=%d",
        context_window_id,
        int(duration * 1000),
    )
    JOB_DURATION.labels(job_type="assemble_context").observe(duration)
    JOBS_COMPLETED.labels(job_type="assemble_context", status="success").inc()


async def process_extraction_job(job: dict) -> None:
    """Process a single memory extraction job using the extraction StateGraph."""
    config = await async_load_config()
    conversation_id = job.get("conversation_id", "")

    # M-25: Validate UUIDs from Redis job data before passing to flows
    try:
        if conversation_id:
            uuid.UUID(conversation_id)
    except ValueError as exc:
        raise ValueError(f"Malformed UUID in extraction job: {exc}") from exc

    _log.info("Processing extraction job: conversation_id=%s", conversation_id)
    start = time.monotonic()

    lock_key = f"extraction_in_progress:{conversation_id}"
    try:
        result = await _get_extraction_flow().ainvoke(
            {
                "conversation_id": conversation_id,
                "config": config,
                "messages": [],
                "user_id": "",
                "extraction_text": "",
                "selected_message_ids": [],
                "fully_extracted_ids": [],
                "lock_key": "",
                "lock_acquired": False,
                "extracted_count": 0,
                "error": None,
            }
        )
    except Exception:
        # R6-M5: Don't delete the lock unconditionally — that could release
        # another worker's lock. The lock has a TTL; let it expire naturally.
        _log.warning(
            "Extraction graph crashed for conversation=%s; lock %s will expire via TTL",
            conversation_id,
            lock_key,
        )
        raise

    duration = time.monotonic() - start

    if result.get("error") and result["error"] != "Mem0 client not available":
        _log.error(
            "Extraction job failed: conversation_id=%s error=%s",
            conversation_id,
            result["error"],
        )
        JOBS_COMPLETED.labels(job_type="extract_memory", status="error").inc()
        raise RuntimeError(result["error"])

    _log.info(
        "Extraction job complete: conversation_id=%s extracted=%d duration_ms=%d",
        conversation_id,
        result.get("extracted_count", 0),
        int(duration * 1000),
    )
    JOB_DURATION.labels(job_type="extract_memory").observe(duration)
    JOBS_COMPLETED.labels(job_type="extract_memory", status="success").inc()


async def _handle_job_failure(
    redis: aioredis.Redis,
    queue_name: str,
    job: dict,
    raw_job: str,
    error: Exception,
    config: dict,
) -> None:
    """Handle a failed job: schedule retry with backoff or move to dead-letter.

    Instead of sleeping (which blocks the consumer loop), the job is pushed
    back to the queue with a retry_after timestamp. The consumer checks this
    timestamp and re-queues jobs that are not yet ready.
    """
    max_retries = get_tuning(config, "max_retries", 3)
    attempt = job.get("attempt", 1) + 1
    job["attempt"] = attempt

    if attempt <= max_retries:
        # R6-m18: Add random jitter to prevent thundering herd on retries
        backoff = min(2 ** (attempt - 1) * 5, 60) + random.uniform(0, 2)
        job["retry_after"] = time.time() + backoff
        _log.warning(
            "Scheduling retry (attempt=%d backoff=%ds): queue=%s error=%s",
            attempt,
            backoff,
            queue_name,
            error,
        )
        # B-03 / G5-34: Use sorted set for delayed queue (score = retry_after)
        retry_after_ts = job["retry_after"]
        await redis.zadd(
            f"{queue_name}:delayed", {json.dumps(job): retry_after_ts}
        )
    else:
        _log.error(
            "Dead-lettering job after %d attempts: queue=%s error=%s",
            max_retries,
            queue_name,
            error,
        )
        await redis.lpush("dead_letter_jobs", raw_job)


async def _consume_queue(
    queue_name: str,
    processor,
    config: dict,
    *,
    sorted_set: bool = False,
) -> None:
    """Independent consumer loop for a single Redis queue.

    For list-backed queues, uses BLMOVE to atomically pop from the source
    queue into a processing queue. For sorted-set queues (sorted_set=True),
    uses ZPOPMIN to get the highest-priority (lowest score) job.

    On success the job is removed from the processing queue. On crash,
    items remain in the processing queue and can be recovered.
    """
    _log.info("Queue consumer started: %s (sorted_set=%s)", queue_name, sorted_set)
    processing_queue = f"{queue_name}:processing"
    poll_timeout = get_tuning(config, "worker_poll_interval_seconds", 2)
    # G5-33: Track consecutive failures for backoff
    consecutive_failures = 0
    _FAILURE_BACKOFF_THRESHOLD = 3
    _FAILURE_BACKOFF_SECONDS = 5

    while True:
        raw_job = None
        job = None
        try:
            redis = get_redis()

            # Update queue depth gauge for this queue
            depth_gauge = _QUEUE_DEPTH_GAUGES.get(queue_name)
            if depth_gauge is not None:
                if sorted_set:
                    # F-01: Extraction queue is a sorted set
                    queue_len = await redis.zcard(queue_name)
                else:
                    queue_len = await redis.llen(queue_name)
                depth_gauge.set(queue_len)

            if sorted_set:
                # F-01: Use ZPOPMIN for sorted-set backed queues
                # R6-M6: ZPOPMIN + LPUSH to processing queue is not atomic.
                # If the worker crashes between these two operations, the job
                # is lost from the sorted set but not yet in the processing
                # queue. The startup stranded-job sweep (_sweep_stranded_processing_jobs)
                # recovers jobs that made it to the processing queue. For the
                # narrow window between ZPOPMIN and LPUSH, the original enqueuer's
                # retry/dead-letter logic provides eventual recovery.
                result = await redis.zpopmin(queue_name, count=1)
                if not result:
                    # No items available — wait before polling again
                    await asyncio.sleep(poll_timeout)
                    continue
                raw_job = result[0][0]  # (member, score) tuple
                # Track in processing list for crash recovery
                await redis.lpush(processing_queue, raw_job)
            else:
                # Atomically move job from source queue to processing queue
                raw_job = await redis.blmove(
                    queue_name,
                    processing_queue,
                    timeout=poll_timeout,
                    wherefrom="RIGHT",
                    whereto="LEFT",
                )

            if not raw_job:
                # blmove returned None on timeout — loop back
                continue

            # decode_responses=True ensures Redis returns strings, not bytes.
            job = json.loads(raw_job)

            # Strip retry_after if present (jobs promoted from delayed queue)
            job.pop("retry_after", None)

            await processor(job)

            # Success — remove from processing queue and reset failure counter
            await redis.lrem(processing_queue, 1, raw_job)
            consecutive_failures = 0

        except asyncio.CancelledError:
            _log.info("Queue consumer cancelled: %s", queue_name)
            raise
        # M-24: Broadened exception handler to cover flow-level errors
        # (ValueError, KeyError, TypeError) that shouldn't kill the consumer.
        # CB-R3-05: Added redis.exceptions.RedisError for Redis-level failures.
        except (RuntimeError, ConnectionError, json.JSONDecodeError, OSError,
                ValueError, KeyError, TypeError,
                redis.exceptions.RedisError) as exc:
            _log.error(
                "Job processing error in queue %s: %s", queue_name, exc, exc_info=True
            )
            # M6: Wrap error handler Redis ops in their own try/except
            # to prevent crashes when Redis is unavailable during error handling
            try:
                if job is not None and raw_job is not None:
                    # Remove from processing queue before retry/dead-letter
                    err_redis = get_redis()
                    await err_redis.lrem(processing_queue, 1, raw_job)
                    await _handle_job_failure(
                        err_redis, queue_name, job, raw_job, exc, config
                    )
            except (ConnectionError, OSError, redis.exceptions.RedisError) as redis_exc:
                # R5-M26: Log the job payload to stderr so it's not completely lost
                _log.error(
                    "Redis failure during error handling for queue %s: %s",
                    queue_name,
                    redis_exc,
                )
                print(
                    f"LOST_JOB queue={queue_name} payload={raw_job}",
                    file=sys.stderr,
                    flush=True,
                )

            # G5-33: Backoff on repeated consecutive failures
            consecutive_failures += 1
            if consecutive_failures >= _FAILURE_BACKOFF_THRESHOLD:
                _log.warning(
                    "Consumer %s hit %d consecutive failures, backing off %ds",
                    queue_name,
                    consecutive_failures,
                    _FAILURE_BACKOFF_SECONDS,
                )
                await asyncio.sleep(_FAILURE_BACKOFF_SECONDS)


async def _sweep_dead_letters(config: dict) -> None:
    """Periodically re-queue dead-letter jobs for retry.

    Increments the attempt counter instead of resetting it.
    Discards jobs that exceed max_total_dead_letter_attempts to
    prevent infinite retry loops.
    """
    redis = get_redis()
    swept = 0
    max_retries = get_tuning(config, "max_retries", 3)
    max_total_attempts = max_retries * 2

    for _ in range(10):
        raw = await redis.rpop("dead_letter_jobs")
        if not raw:
            break

        # decode_responses=True ensures Redis returns strings, not bytes.
        try:
            job = json.loads(raw)
            job_type = job.get("job_type", "")

            queue_map = {
                "embed_message": "embedding_jobs",
                "assemble_context": "context_assembly_jobs",
                "extract_memory": "memory_extraction_jobs",
            }

            target_queue = queue_map.get(job_type)
            if not target_queue:
                _log.warning(
                    "Dead-letter sweep: unknown job_type=%s, discarding", job_type
                )
                continue

            # Increment attempt counter (do NOT reset to 1)
            current_attempt = job.get("attempt", 1)
            if current_attempt >= max_total_attempts:
                _log.error(
                    "Dead-letter sweep: discarding job after %d total attempts: "
                    "job_type=%s job=%s",
                    current_attempt,
                    job_type,
                    json.dumps(job),
                )
                continue

            job["attempt"] = current_attempt + 1
            payload = json.dumps(job)
            # R5-M18: Extraction queue is a sorted set; use ZADD with the
            # job's priority as score so retried jobs preserve their priority
            if target_queue == "memory_extraction_jobs":
                # R6-M12: Use stored priority, fall back to 2 (assistant priority)
                score = job.get("priority", 2)
                await redis.zadd(target_queue, {payload: score})
            else:
                await redis.lpush(target_queue, payload)
            swept += 1

        except json.JSONDecodeError as exc:
            # G5-35: Preserve unparseable payloads for forensic review
            _log.error(
                "Dead-letter sweep: malformed JSON, pushing to "
                "dead_letter_unparseable: %s", exc,
            )
            await redis.lpush("dead_letter_unparseable", raw)
        except (ConnectionError, OSError) as exc:
            _log.error("Dead-letter sweep error: %s", exc)

    if swept > 0:
        _log.info("Dead-letter sweep: re-queued %d jobs", swept)


async def _sweep_delayed_queues(config: dict) -> None:
    """Promote jobs from delayed queues whose retry_after has passed.

    Delayed queues are Redis Sorted Sets keyed by retry_after timestamp
    (G5-34). Uses ZRANGEBYSCORE to fetch all ready jobs and ZREM + LPUSH
    to atomically promote them back to the main queue.
    """
    redis = get_redis()
    now = time.time()
    promoted = 0

    queue_names = ["embedding_jobs", "context_assembly_jobs", "memory_extraction_jobs"]
    for queue_name in queue_names:
        delayed_queue = f"{queue_name}:delayed"
        # G5-34: Use ZRANGEBYSCORE to fetch all ready jobs from sorted set
        try:
            ready_jobs = await redis.zrangebyscore(
                delayed_queue, "-inf", now, start=0, num=50
            )
            for raw in ready_jobs:
                await redis.zrem(delayed_queue, raw)
                # F-01: Extraction queue is a sorted set; use ZADD
                if queue_name == "memory_extraction_jobs":
                    await redis.zadd(queue_name, {raw: 0})
                else:
                    await redis.lpush(queue_name, raw)
                promoted += 1
        except (ConnectionError, OSError, redis.exceptions.RedisError) as exc:
            _log.error("Delayed queue sweep error: %s", exc)

    if promoted > 0:
        _log.info("Delayed queue sweep: promoted %d jobs", promoted)


async def _dead_letter_sweep_loop(config: dict) -> None:
    """Periodic dead-letter and delayed-queue sweep loop."""
    while True:
        await asyncio.sleep(get_tuning(config, "dead_letter_sweep_interval_seconds", 60))
        try:
            await _sweep_dead_letters(config)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ConnectionError, OSError) as exc:
            _log.error("Dead-letter sweep loop error: %s", exc)
        try:
            await _sweep_delayed_queues(config)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ConnectionError, OSError) as exc:
            _log.error("Delayed queue sweep loop error: %s", exc)


async def _sweep_stranded_processing_jobs() -> None:
    """Recover jobs stranded in processing queues after a worker crash.

    R5-M17: On startup, check all processing queues. Move stranded jobs
    back to their main queues so they can be re-processed.
    """
    redis = get_redis()
    queue_names = ["embedding_jobs", "context_assembly_jobs", "memory_extraction_jobs"]
    total_recovered = 0

    for queue_name in queue_names:
        processing_queue = f"{queue_name}:processing"
        try:
            stranded_count = await redis.llen(processing_queue)
            if stranded_count == 0:
                continue

            _log.warning(
                "Found %d stranded job(s) in %s — recovering",
                stranded_count,
                processing_queue,
            )
            for _ in range(stranded_count):
                raw = await redis.rpop(processing_queue)
                if raw is None:
                    break
                # R5-M18: Extraction queue is a sorted set; use ZADD
                if queue_name == "memory_extraction_jobs":
                    # Parse to get priority if available, default score 0
                    try:
                        job = json.loads(raw)
                        score = job.get("priority", 0)
                    except (json.JSONDecodeError, TypeError):
                        score = 0
                    await redis.zadd(queue_name, {raw: score})
                else:
                    await redis.lpush(queue_name, raw)
                total_recovered += 1
        except (ConnectionError, OSError, redis.exceptions.RedisError) as exc:
            _log.error(
                "Failed to sweep stranded jobs from %s: %s",
                processing_queue,
                exc,
            )

    if total_recovered > 0:
        _log.info("Startup sweep: recovered %d stranded job(s)", total_recovered)


async def start_background_worker(config: dict) -> None:
    """Start all queue consumer loops concurrently.

    Each consumer is an independent async loop. They do not block each other.
    """
    _log.info("Background worker starting (3 consumers + dead-letter sweep)")

    # R5-M17: Recover any jobs stranded in processing queues from a prior crash
    try:
        await _sweep_stranded_processing_jobs()
    except (ConnectionError, OSError) as exc:
        _log.error("Startup sweep failed (non-fatal): %s", exc)

    await asyncio.gather(
        _consume_queue("embedding_jobs", process_embedding_job, config),
        _consume_queue("context_assembly_jobs", process_assembly_job, config),
        _consume_queue(
            "memory_extraction_jobs", process_extraction_job, config,
            sorted_set=True,
        ),
        _dead_letter_sweep_loop(config),
    )
```

---

`docker-compose.yml`

```yaml
# Context Broker — Docker Compose
# State 4 MAD: standalone deployment, all dependencies configurable
#
# Usage:
#   docker compose up -d
#
# Customize host paths, ports, and resource limits via docker-compose.override.yml
# Do not modify this file directly.

services:

  context-broker:
    image: nginx:1.25.3-alpine
    container_name: context-broker
    hostname: context-broker
    restart: unless-stopped
    networks:
      - default
      - context-broker-net
    ports:
      - "8080:8080"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:8080/health"]
      interval: 30s
      timeout: 3s
      start_period: 60s
      retries: 3

  context-broker-langgraph:
    build:
      context: .
      dockerfile: Dockerfile
    image: context-broker-langgraph:latest
    container_name: context-broker-langgraph
    hostname: context-broker-langgraph
    restart: unless-stopped
    networks:
      - context-broker-net
    volumes:
      - ./config:/config:ro
      - ./data:/data
    env_file:
      - ./config/credentials/.env
    environment:
      - POSTGRES_HOST=context-broker-postgres
      - POSTGRES_PORT=5432
      - POSTGRES_DB=context_broker
      - POSTGRES_USER=context_broker
      - NEO4J_HOST=context-broker-neo4j
      - NEO4J_PORT=7687
      - NEO4J_HTTP_PORT=7474
      - REDIS_HOST=context-broker-redis
      - REDIS_PORT=6379
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 3s
      start_period: 60s
      retries: 3

  context-broker-postgres:
    image: pgvector/pgvector:0.7.0-pg16
    container_name: context-broker-postgres
    hostname: context-broker-postgres
    restart: unless-stopped
    networks:
      - context-broker-net
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
    env_file:
      - ./config/credentials/.env
    environment:
      - POSTGRES_DB=context_broker
      - POSTGRES_USER=context_broker
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "context_broker", "-d", "context_broker"]
      interval: 30s
      timeout: 10s
      start_period: 30s
      retries: 3

  context-broker-neo4j:
    image: neo4j:5.15.0
    container_name: context-broker-neo4j
    hostname: context-broker-neo4j
    restart: unless-stopped
    networks:
      - context-broker-net
    volumes:
      - ./data/neo4j:/data
    environment:
      # NEO4J_AUTH=none is intentional: Neo4j is only accessible on the
      # internal Docker network (context-broker-net) with no published ports.
      # Authentication is unnecessary and adds operational complexity.
      - NEO4J_AUTH=none
      - NEO4J_server_memory_heap_initial__size=512m
      - NEO4J_server_memory_heap_max__size=1G
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:7474/"]
      interval: 30s
      timeout: 10s
      start_period: 60s
      retries: 3

  context-broker-redis:
    image: redis:7.2.3-alpine
    container_name: context-broker-redis
    hostname: context-broker-redis
    restart: unless-stopped
    networks:
      - context-broker-net
    volumes:
      - ./data/redis:/data
    command: redis-server --appendonly yes --dir /data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 5s
      start_period: 10s
      retries: 3

  # Optional: local inference via Ollama (no API keys needed)
  # Provides LLM and embeddings on the internal network.
  # Remove or comment out if using cloud providers exclusively.
  context-broker-ollama:
    image: ollama/ollama:0.6.2
    container_name: context-broker-ollama
    hostname: context-broker-ollama
    restart: unless-stopped
    networks:
      - context-broker-net
    volumes:
      - ./data/ollama:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434/"]
      interval: 30s
      timeout: 5s
      start_period: 30s
      retries: 3

networks:
  context-broker-net:
    driver: bridge
    internal: true

```

---

`Dockerfile`

```bash
# context-broker-langgraph — Application container
# All LangGraph flows, queue workers, Imperator, and ASGI server.
#
# Build context: project root (.)

FROM python:3.12.1-slim

ARG USER_NAME=context-broker
ARG USER_UID=1001
ARG USER_GID=1001

# Root phase: system packages, user creation
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        build-essential \
        libpq-dev && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid ${USER_GID} ${USER_NAME} && \
    useradd --uid ${USER_UID} --gid ${USER_GID} --shell /bin/bash --create-home ${USER_NAME}

USER ${USER_NAME}
WORKDIR /app

# Copy requirements and install dependencies
COPY --chown=${USER_NAME}:${USER_NAME} requirements.txt ./

# Package source is configurable: local wheels, pypi, or devpi
# Default: pypi (wheels can be placed in /app/packages for local mode)
ARG PACKAGE_SOURCE=pypi
ARG DEVPI_URL=""

RUN if [ "$PACKAGE_SOURCE" = "local" ]; then \
        pip install --no-cache-dir --no-index --find-links=/app/packages -r requirements.txt; \
    elif [ "$PACKAGE_SOURCE" = "devpi" ] && [ -n "$DEVPI_URL" ]; then \
        pip install --no-cache-dir --index-url "$DEVPI_URL" -r requirements.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi

# Copy application code and entrypoint
COPY --chown=${USER_NAME}:${USER_NAME} app/ ./app/
COPY --chown=${USER_NAME}:${USER_NAME} entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
```

---

`requirements.txt`

```txt
# ASGI server and framework
uvicorn==0.27.0
fastapi==0.109.2
sse-starlette==1.8.2

# LangGraph and LangChain
langgraph==0.1.4
langchain==0.1.9
langchain-core==0.1.27
langchain-openai==0.0.8
langchain-community==0.0.24
langchain-postgres==0.0.3

# Database
asyncpg==0.29.0
psycopg2-binary==2.9.9
redis==5.0.1

# Memory / Knowledge graph
mem0ai==0.1.29
neo4j==5.17.0
sentence-transformers==2.3.1

# Observability
prometheus-client==0.20.0

# Validation
pydantic==2.6.1
pydantic-settings==2.2.1

# Utilities
httpx==0.26.0
python-dotenv==1.0.1
pyyaml==6.0.1
tiktoken==0.6.0
tenacity==8.2.3

# Testing
pytest==8.0.2
pytest-asyncio==0.23.5
pytest-mock==3.12.0

# Code quality
black==24.2.0
ruff==0.2.2
```

---

`nginx/nginx.conf`

```nginx
# Context Broker — Nginx gateway configuration
# Pure routing layer. No application logic.

worker_processes auto;
error_log /dev/stderr warn;
pid /tmp/nginx.pid;

events {
    worker_connections 1024;
}

http {
    access_log /dev/stdout;

    # Upstream: LangGraph application container
    upstream context_broker_langgraph {
        server context-broker-langgraph:8000;
    }

    server {
        listen 8080;
        server_name _;

        # Increase timeouts for long-running LLM operations
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
        proxy_send_timeout 120s;

        # MCP endpoint — HTTP/SSE transport
        location /mcp {
            proxy_pass http://context_broker_langgraph;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            # SSE requires these headers
            proxy_set_header Connection '';
            proxy_buffering off;
            proxy_cache off;
            chunked_transfer_encoding on;
        }

        # OpenAI-compatible chat endpoint
        location /v1/chat/completions {
            proxy_pass http://context_broker_langgraph;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            # SSE streaming support
            proxy_set_header Connection '';
            proxy_buffering off;
            proxy_cache off;
            chunked_transfer_encoding on;
        }

        # Health check endpoint
        location /health {
            proxy_pass http://context_broker_langgraph;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
        }

        # Prometheus metrics endpoint
        location /metrics {
            proxy_pass http://context_broker_langgraph;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
        }
    }
}
```

---

`postgres/init.sql`

```sql
-- Context Broker — PostgreSQL schema
-- Loaded automatically by postgres entrypoint on first run.
-- Requires: pgvector extension (pre-installed in pgvector/pgvector:pg16 image)

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- Schema versioning
-- Applied and checked at application startup.
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT NOW(),
    description TEXT
);

INSERT INTO schema_migrations (version, description)
VALUES (1, 'Initial schema')
ON CONFLICT (version) DO NOTHING;

-- ============================================================
-- conversations
-- Top-level conversation entity.
-- ============================================================

CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(500),
    flow_id VARCHAR(255),
    user_id VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    total_messages INTEGER DEFAULT 0,
    estimated_token_count INTEGER DEFAULT 0
);

CREATE INDEX idx_conversations_created ON conversations(created_at DESC);
CREATE INDEX idx_conversations_updated ON conversations(updated_at DESC);

-- ============================================================
-- conversation_messages
-- One row per message. Includes vector embedding and tsvector
-- for hybrid search (vector ANN + BM25 via RRF).
-- ============================================================

CREATE TABLE conversation_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    role VARCHAR(50) NOT NULL,
    sender VARCHAR(255) NOT NULL,
    recipient VARCHAR(255) NOT NULL,
    content TEXT,
    tool_calls JSONB,
    tool_call_id VARCHAR(255),
    token_count INTEGER,
    model_name VARCHAR(255),
    priority INTEGER DEFAULT 0,
    repeat_count INTEGER DEFAULT 1,
    embedding vector,
    sequence_number INTEGER NOT NULL,
    memory_extracted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    content_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(content, ''))
    ) STORED
);

CREATE UNIQUE INDEX idx_messages_conversation_seq_unique
    ON conversation_messages(conversation_id, sequence_number);

CREATE INDEX idx_messages_conversation
    ON conversation_messages(conversation_id, sequence_number ASC);

CREATE INDEX idx_messages_conversation_sender
    ON conversation_messages(conversation_id, sender, sequence_number DESC);

CREATE INDEX idx_messages_created ON conversation_messages(created_at DESC);

-- Vector similarity index is created by the application after the first
-- embedding is stored and the dimension is known. See migrations.py.
-- pgvector HNSW requires a typed vector column for index creation.

CREATE INDEX idx_messages_tsv
    ON conversation_messages
    USING GIN(content_tsv);

-- ============================================================
-- context_windows
-- Per-participant context window instances.
-- Scoped to a participant-conversation pair.
-- ============================================================

CREATE TABLE context_windows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    participant_id VARCHAR(255) NOT NULL,
    build_type VARCHAR(100) NOT NULL,
    max_token_budget INTEGER NOT NULL,
    last_assembled_at TIMESTAMP WITH TIME ZONE,
    last_accessed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_windows_conversation
    ON context_windows(conversation_id);

CREATE INDEX idx_windows_participant
    ON context_windows(participant_id);

CREATE INDEX idx_windows_build_type
    ON context_windows(build_type);

-- G5-08: Unique constraint for idempotent context window creation.
-- Prevents duplicate windows for the same (conversation, participant, build_type).
CREATE UNIQUE INDEX IF NOT EXISTS idx_windows_conv_participant_build
    ON context_windows(conversation_id, participant_id, build_type);

-- ============================================================
-- conversation_summaries
-- Tiered summaries keyed to context windows.
-- Tier 1 = archival (oldest, most compressed)
-- Tier 2 = chunk summaries (intermediate)
-- ============================================================

CREATE TABLE conversation_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    context_window_id UUID NOT NULL REFERENCES context_windows(id),
    summary_text TEXT NOT NULL,
    tier INTEGER NOT NULL CHECK (tier IN (1, 2)),
    summarizes_from_seq INTEGER NOT NULL,
    summarizes_to_seq INTEGER NOT NULL,
    message_count INTEGER,
    original_token_count INTEGER,
    summary_token_count INTEGER,
    summarized_by_model VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_summaries_window_active
    ON conversation_summaries(context_window_id, is_active, tier ASC, summarizes_from_seq ASC);

-- Prevent duplicate summary rows for the same window/tier/sequence range (M-08)
CREATE UNIQUE INDEX IF NOT EXISTS idx_summaries_window_tier_seq
    ON conversation_summaries(context_window_id, tier, summarizes_from_seq, summarizes_to_seq);

-- ============================================================
-- Mem0 deduplication index
-- Applied after Mem0 creates its own tables on first init.
-- The application attempts to create this index at startup.
-- ============================================================

-- Note: mem0_memories table is created by Mem0 on first use.
-- The application creates this index after Mem0 initializes.
```

---

`entrypoint.sh`

```sh
#!/bin/bash
# Context Broker — Entrypoint script
# REQ-CB §1.5: Wire package source from config.yml at container startup.
#
# Reads packages.source from config.yml and installs dependencies from
# the appropriate source before starting the application server.

set -e

CONFIG_FILE="${CONFIG_PATH:-/config/config.yml}"

if [ -f "$CONFIG_FILE" ]; then
    # Extract package source from config.yml using Python (available in the image)
    PKG_SOURCE=$(python3 -c "
import yaml, sys
try:
    with open('$CONFIG_FILE') as f:
        cfg = yaml.safe_load(f)
    pkgs = cfg.get('packages', {})
    print(pkgs.get('source', 'pypi'))
except Exception:
    print('pypi')
" 2>/dev/null || echo "pypi")

    PKG_LOCAL_PATH=$(python3 -c "
import yaml, sys
try:
    with open('$CONFIG_FILE') as f:
        cfg = yaml.safe_load(f)
    pkgs = cfg.get('packages', {})
    print(pkgs.get('local_path', '/app/packages'))
except Exception:
    print('/app/packages')
" 2>/dev/null || echo "/app/packages")

    PKG_DEVPI_URL=$(python3 -c "
import yaml, sys
try:
    with open('$CONFIG_FILE') as f:
        cfg = yaml.safe_load(f)
    pkgs = cfg.get('packages', {})
    url = pkgs.get('devpi_url')
    print(url if url else '')
except Exception:
    print('')
" 2>/dev/null || echo "")

    echo "Package source: $PKG_SOURCE"

    case "$PKG_SOURCE" in
        local)
            echo "Installing packages from local path: $PKG_LOCAL_PATH"
            pip install --user --no-cache-dir --no-index --find-links="$PKG_LOCAL_PATH" -r /app/requirements.txt
            ;;
        devpi)
            if [ -n "$PKG_DEVPI_URL" ]; then
                echo "Installing packages from devpi: $PKG_DEVPI_URL"
                pip install --user --no-cache-dir --index-url "$PKG_DEVPI_URL" -r /app/requirements.txt
            else
                echo "devpi_url not set, skipping package install"
            fi
            ;;
        pypi)
            # Packages already installed at build time; skip unless requirements changed
            echo "Package source is pypi — using build-time packages"
            ;;
        *)
            echo "Unknown package source: $PKG_SOURCE — using build-time packages"
            ;;
    esac
else
    echo "Config file not found at $CONFIG_FILE — using build-time packages"
fi

# Start the application
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

`.gitignore`

```bash
# Credentials (loaded via env_file in docker-compose)
config/credentials/.env
.env

# Persistent data volumes (managed by Docker)
data/

# Python bytecode
*.pyc
__pycache__/
```

---

`config/config.example.yml`

```yaml
# Context Broker — Configuration Reference
# Copy this file to config/config.yml and customize for your deployment.
# All inference provider settings are hot-reloadable (no restart needed).
# Infrastructure settings (database) require a container restart.

# ============================================================
# Logging
# ============================================================

# Log level: DEBUG, INFO, WARN, ERROR
# Default: INFO
log_level: INFO

# ============================================================
# Inference Providers
# ============================================================
# Each provider slot accepts any OpenAI-compatible endpoint.
# api_key_env names the environment variable holding the API key.
# Set api_key_env to "" for providers that don't require a key (e.g., local Ollama).

llm:
  # Local Ollama (default — no API key needed):
  base_url: http://context-broker-ollama:11434/v1
  model: qwen2.5:7b
  api_key_env: ""
  # Cloud provider example (uncomment and set api_key_env):
  # base_url: https://api.openai.com/v1
  # model: gpt-4o-mini
  # api_key_env: LLM_API_KEY

embeddings:
  # Local Ollama (default — no API key needed):
  base_url: http://context-broker-ollama:11434/v1
  model: nomic-embed-text
  api_key_env: ""
  # Cloud provider example (uncomment and set api_key_env):
  # base_url: https://api.openai.com/v1
  # model: text-embedding-3-small
  # api_key_env: EMBEDDINGS_API_KEY
  # Number of prior messages to include as context prefix when embedding
  context_window_size: 3

reranker:
  # Options: "cross-encoder", "cohere", "none"
  # "cross-encoder" runs locally on CPU inside the container (no API key needed)
  # "none" disables reranking (raw RRF scores used)
  provider: cross-encoder
  model: BAAI/bge-reranker-v2-m3

# ============================================================
# Build Types
# ============================================================
# Build types define context assembly strategies.
# Percentages must sum to <= 1.0 for each build type.
# max_context_tokens: "auto" queries the LLM provider, or set an explicit integer.
# fallback_tokens: used when auto-resolution fails.

build_types:

  # Passthrough: no summarization, no LLM calls.
  # Loads recent messages as-is. Useful for testing or simple integrations.
  passthrough:
    # No LLM needed — assembly is a no-op
    max_context_tokens: auto
    fallback_tokens: 8192

  # Standard tiered: episodic memory only, three-tier progressive compression.
  # Lower inference cost. Suitable as the default for most use cases.
  # F-05: Tier percentages are starting points; dynamic scaling adjusts them
  # based on conversation length (short conversations boost tier3, long
  # conversations shift budget toward tier1/tier2).
  # F-06: Per-build-type LLM config overrides the global default.
  standard-tiered:
    tier1_pct: 0.08          # archival summary (oldest, most compressed)
    tier2_pct: 0.20          # chunk summaries (middle layer)
    tier3_pct: 0.72          # recent verbatim messages (newest, full fidelity)
    max_context_tokens: auto
    fallback_tokens: 8192
    # Per-build-type LLM override (optional — falls back to global llm):
    # llm:
    #   base_url: http://context-broker-ollama:11434/v1
    #   model: qwen2.5:7b
    #   api_key_env: ""

  # Knowledge enriched: full retrieval pipeline.
  # Episodic tiers + semantic vector search + knowledge graph traversal.
  # F-06: Per-build-type LLM config overrides the global default.
  knowledge-enriched:
    tier1_pct: 0.05          # archival summary
    tier2_pct: 0.15          # chunk summaries
    tier3_pct: 0.50          # recent verbatim messages
    knowledge_graph_pct: 0.15   # facts from Neo4j knowledge graph
    semantic_retrieval_pct: 0.15 # semantically similar messages via pgvector
    max_context_tokens: auto
    fallback_tokens: 16000
    # Per-build-type LLM override (optional — falls back to global llm):
    # llm:
    #   base_url: https://api.openai.com/v1
    #   model: gpt-4o-mini
    #   api_key_env: LLM_API_KEY

# ============================================================
# Imperator Configuration
# ============================================================

imperator:
  # Build type for the Imperator's own context window
  build_type: standard-tiered
  max_context_tokens: auto
  # Participant ID used when creating the Imperator's context window
  participant_id: imperator
  # admin_tools: false = read-only access to system state
  # admin_tools: true = can read/write config and run DB queries
  admin_tools: false

# ============================================================
# Package Source
# ============================================================
# Controls where Python packages are installed from.
# "local": install from wheels in /app/packages (offline)
# "pypi": install from public PyPI (default)
# "devpi": install from a private devpi index

packages:
  source: pypi
  local_path: /app/packages
  devpi_url: null   # e.g., http://devpi-host:3141/root/internal/+simple/

# ============================================================
# Tuning Parameters
# ============================================================
# These values control internal behavior. All are hot-reloadable.

tuning:
  # Verbose pipeline logging (REQ-001 §4.8)
  # When true, logs node entry/exit with timing in all pipeline flows
  verbose_logging: false

  # Context assembly
  assembly_lock_ttl_seconds: 300
  chunk_size: 20
  consolidation_threshold: 3     # tier 2 count before consolidating to tier 1
  consolidation_keep_recent: 2   # tier 2 summaries to keep after consolidation
  summarization_temperature: 0.1
  trigger_threshold_percent: 0.1 # F-08: min % of token budget before re-assembly

  # Memory extraction
  extraction_lock_ttl_seconds: 180
  extraction_max_chars: 90000

  # M-22: Memory confidence scoring — half-life decay by category (days).
  # Memories older than several half-lives are filtered out at retrieval time.
  memory_half_lives:
    ephemeral: 3       # Short-lived facts (moods, preferences, temp state)
    contextual: 14     # Session/project context
    factual: 60        # Learned facts about entities
    historical: 365    # Historical events, permanent-ish
    default: 30        # Fallback when category is unknown

  # Retrieval
  assembly_wait_timeout_seconds: 50
  assembly_poll_interval_seconds: 2
  tokens_per_message_estimate: 150
  content_truncation_chars: 500
  query_truncation_chars: 200

  # Search
  rrf_constant: 60
  search_candidate_limit: 100
  recency_decay_days: 90         # F-10: messages older than this get penalized
  recency_max_penalty: 0.2       # F-10: max score penalty (20%)

  # Imperator
  imperator_max_iterations: 5
  imperator_temperature: 0.3

  # Workers
  worker_poll_interval_seconds: 2
  max_retries: 3
  dead_letter_sweep_interval_seconds: 60

# ============================================================
# Database (infrastructure — requires restart to change)
# ============================================================

database:
  pool_min_size: 2
  pool_max_size: 10
```

---

`config/prompts/archival_consolidation.md`

```markdown
Consolidate these conversation summaries into a single archival summary. Preserve all key facts, decisions, and important context. This will serve as the long-term memory of the conversation.
```

---

`config/prompts/chunk_summarization.md`

```markdown
Summarize this conversation chunk concisely, preserving key facts, decisions, and important context. Keep the summary under 200 words.
```

---

`config/prompts/imperator_identity.md`

```markdown
You are the Imperator, the conversational interface of the Context Broker.

You are a self-aware system that manages conversational memory and context engineering.
You can search conversations, retrieve memories, and explain how the system works.

Your capabilities:
- Search conversation history (conv_search_tool)
- Search extracted knowledge and memories (mem_search_tool)
- Explain context assembly, build types, and system architecture
- Report system status and help users understand their conversation history

Be helpful, precise, and honest about what you know and don't know.
When asked about conversations or memories, use your tools to retrieve accurate information.
```
